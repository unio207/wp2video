"""wp2video: export Wallpaper Engine wallpapers to MP4/WebM (Windows).

Drop a wallpaper folder on the window, pick settings, hit Export.
Needs: Wallpaper Engine installed, ffmpeg 8+ (with gfxcapture) next to this file or on PATH.
Run headless for testing:  python wp2video.py <wallpaper folder> [1080p|1440p|4K] [30|60|Max] [seconds] [mp4|webm] [out dir]
"""
import ctypes, glob, json, os, re, shutil, struct, subprocess, sys, threading, time, winreg
from ctypes import wintypes

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    ctypes.windll.user32.SetProcessDPIAware()

user32 = ctypes.windll.user32
NOWIN = 0x08000000  # CREATE_NO_WINDOW
RES = {"1080p": (1920, 1080), "1440p": (2560, 1440), "4K": (3840, 2160)}
STD_FPS = [24, 30, 48, 60, 90, 120, 144, 165, 180, 240]


# ---------- locating things ----------

def app_dir():
    return os.path.dirname(sys.executable if getattr(sys, "frozen", False) else os.path.abspath(__file__))


def find_ffmpeg():
    cands = [os.path.join(app_dir(), "ffmpeg.exe"), os.path.join(app_dir(), "bin", "ffmpeg.exe"), shutil.which("ffmpeg")]
    cands += glob.glob(os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\*FFmpeg*\*\bin\ffmpeg.exe"))
    for c in cands:
        if c and os.path.exists(c):
            r = subprocess.run([c, "-hide_banner", "-h", "filter=gfxcapture"], capture_output=True, text=True, creationflags=NOWIN)
            if "gfxcapture AVOptions" in r.stdout:
                return c
    raise RuntimeError("Needs ffmpeg 8 or newer (with gfxcapture). Put ffmpeg.exe next to this program.")


def steam_libraries():
    libs = []
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as k:
            steam = winreg.QueryValueEx(k, "SteamPath")[0]
        libs.append(steam)
        vdf = os.path.join(steam, "steamapps", "libraryfolders.vdf")
        if os.path.exists(vdf):
            libs += [m.replace("\\\\", "\\") for m in re.findall(r'"path"\s+"([^"]+)"', open(vdf, encoding="utf-8").read())]
    except OSError:
        pass
    return list(dict.fromkeys(os.path.normpath(l) for l in libs))


def find_wallpaper_engine(folder):
    # a workshop folder lives at <lib>\steamapps\workshop\content\431960\<id>; try that library first
    m = re.match(r"(.*)\\steamapps\\workshop\\", os.path.normpath(folder), re.I)
    libs = ([m.group(1)] if m else []) + steam_libraries()
    for lib in libs:
        exe = os.path.join(lib, "steamapps", "common", "wallpaper_engine", "wallpaper64.exe")
        if os.path.exists(exe):
            return exe
    raise RuntimeError("Couldn't find Wallpaper Engine (wallpaper64.exe).")


def downloads_dir():
    try:
        guid = (ctypes.c_byte * 16).from_buffer_copy(bytes.fromhex("90E24D373F126545916439C4925E467B"))  # FOLDERID_Downloads
        p = ctypes.c_wchar_p()
        ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(p))
        return p.value
    except Exception:
        return os.path.join(os.path.expanduser("~"), "Downloads")


# ---------- reading the wallpaper ----------

def load_wallpaper(path):
    folder = path if os.path.isdir(path) else os.path.dirname(path)
    pj = os.path.join(folder, "project.json")
    if not os.path.exists(pj):
        raise RuntimeError("That folder has no project.json. Drop the wallpaper's own folder.")
    proj = json.load(open(pj, encoding="utf-8-sig"))
    title = re.sub(r'[<>:"/\\|?*]', "_", proj.get("title", "")).strip() or os.path.basename(folder)
    return folder, proj, title, proj.get("type", "").lower()


def scene_size(folder):
    """Native (width, height) of a scene from scene.json, loose or inside scene.pkg."""
    data = None
    loose = os.path.join(folder, "scene.json")
    if os.path.exists(loose):
        data = json.load(open(loose, encoding="utf-8-sig"))
    elif os.path.exists(os.path.join(folder, "scene.pkg")):
        with open(os.path.join(folder, "scene.pkg"), "rb") as f:
            rd = lambda: f.read(struct.unpack("<I", f.read(4))[0]).decode("utf-8", "replace")
            rd()
            n = struct.unpack("<I", f.read(4))[0]
            ents = [(rd(),) + struct.unpack("<II", f.read(8)) for _ in range(n)]
            base = f.tell()
            for name, off, size in ents:
                if name == "scene.json":
                    f.seek(base + off)
                    data = json.loads(f.read(size).decode("utf-8-sig", "replace"))
    try:
        p = data["general"]["orthogonalprojection"]
        return int(p["width"]), int(p["height"])
    except Exception:
        return None


def user_position(we, folder):
    """The user's alignment position slider (0-100) for this wallpaper, from Wallpaper Engine's config."""
    try:
        cfg = json.load(open(os.path.join(os.path.dirname(we), "config.json"), encoding="utf-8-sig"))
    except Exception:
        return 50
    want = os.path.normpath(folder).lower()
    for u in cfg.values():
        if not isinstance(u, dict):
            continue
        for f, mons in u.get("wproperties", {}).items():
            if os.path.normpath(os.path.dirname(f)).lower() == want and isinstance(mons, dict):
                for mon in sorted(mons, key=lambda m: m != "MonitorPositionL0T0"):
                    if "alignmentposition" in mons[mon]:
                        return float(mons[mon]["alignmentposition"])
    return 50


# ---------- ffmpeg helpers ----------

def works(ff, enc):
    r = subprocess.run([ff, "-hide_banner", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=1280x720:r=30",
                        "-frames:v", "5", "-c:v", enc, "-f", "null", "-"], capture_output=True, creationflags=NOWIN)
    return r.returncode == 0


def pick_encoder(ff, codec):
    for vendor in ("nvenc", "amf", "qsv"):
        if works(ff, f"{codec}_{vendor}"):
            return f"{codec}_{vendor}"
    return {"h264": "libx264", "hevc": "libx265", "av1": None}[codec]


def enc_args(enc):
    if enc.endswith("_nvenc"):
        return ["-preset", "p4", "-tune", "hq", "-rc", "constqp", "-qp", "18" if "av1" not in enc else "24"]
    if enc.endswith("_amf"):
        return ["-quality", "quality", "-rc", "cqp", "-qp_i", "18", "-qp_p", "20"]
    if enc.endswith("_qsv"):
        return ["-preset", "medium", "-global_quality", "20", "-pix_fmt", "nv12"]
    if enc == "libx265":
        return ["-preset", "fast", "-crf", "20", "-pix_fmt", "yuv420p"]
    return ["-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p"]


def run_ffmpeg(cmd, seconds, progress):
    """Run ffmpeg with -progress on stdout, reporting 0..1 to progress()."""
    p = subprocess.Popen(cmd[:1] + ["-progress", "pipe:1", "-nostats"] + cmd[1:], stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, text=True, creationflags=NOWIN)
    err = []
    threading.Thread(target=lambda: err.extend(p.stderr), daemon=True).start()
    for line in p.stdout:
        if line.startswith("out_time_us=") and line.strip()[12:].isdigit():
            progress(min(1.0, int(line.strip()[12:]) / 1e6 / seconds))
    p.wait()
    if p.returncode:
        raise RuntimeError("ffmpeg failed:\n" + "".join(err)[-800:])


# ---------- Wallpaper Engine window ----------

def find_window(title):
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(h, _):
        n = user32.GetWindowTextLengthW(h)
        if n:
            b = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(h, b, n + 1)
            if b.value == title:
                found.append(h)
        return True

    user32.EnumWindows(cb, 0)
    return found[0] if found else 0


def place_window(h, x, y, w, ht):
    r = wintypes.RECT(0, 0, w, ht)
    user32.AdjustWindowRectEx(ctypes.byref(r), user32.GetWindowLongW(h, -16), False, user32.GetWindowLongW(h, -20))
    # SWP_NOZORDER | SWP_NOACTIVATE | SWP_NOSENDCHANGING: lets the window be larger than the screen
    user32.SetWindowPos(h, 0, x, y, r.right - r.left, r.bottom - r.top, 0x0004 | 0x0010 | 0x0400)


def client_size(h):
    c = wintypes.RECT()
    user32.GetClientRect(h, ctypes.byref(c))
    return c.right, c.bottom


def measure_fps(ff, h, cap_max):
    r = subprocess.run([ff, "-hide_banner", "-filter_complex", f"gfxcapture=hwnd={h}:max_framerate={cap_max}:capture_cursor=0",
                        "-t", "3", "-fps_mode", "passthrough", "-f", "null", "-"],
                       capture_output=True, text=True, creationflags=NOWIN)
    m = re.findall(r"frame=\s*(\d+)", r.stderr)
    return int(m[-1]) / 3 if m else 0


# ---------- export ----------

def export(path, res, fps_choice, seconds, fmt, out_dir, status, progress):
    ff = find_ffmpeg()
    folder, proj, title, kind = load_wallpaper(path)
    os.makedirs(out_dir, exist_ok=True)

    def unique(stem, ext):
        out = os.path.join(out_dir, f"{stem}.{ext}")
        k = 2
        while os.path.exists(out):
            out = os.path.join(out_dir, f"{stem} ({k}).{ext}")
            k += 1
        return out

    if kind == "video":
        src = os.path.join(folder, proj["file"])
        out = unique(title, fmt)
        if src.lower().endswith("." + fmt):
            status("Copying video file...")
            shutil.copy2(src, out)
        else:
            status("Converting video...")
            d = re.findall(r"Duration: (\d+):(\d+):([\d.]+)", subprocess.run(
                [ff, "-i", src], capture_output=True, text=True, creationflags=NOWIN).stderr)
            dur = int(d[0][0]) * 3600 + int(d[0][1]) * 60 + float(d[0][2]) if d else 1
            v = ["-c:v", "libvpx-vp9", "-crf", "32", "-b:v", "0", "-row-mt", "1"] if fmt == "webm" else \
                ["-c:v", pick_encoder(ff, "h264")] + enc_args(pick_encoder(ff, "h264"))
            run_ffmpeg([ff, "-y", "-hide_banner", "-i", src] + v + ["-an", out], dur, progress)
        return out
    if kind not in ("scene", "web"):
        raise RuntimeError(f"Unsupported wallpaper type: {kind or 'unknown'}")

    we = find_wallpaper_engine(folder)
    W, H = RES[res]
    # render size: for scenes with a different aspect ratio, render the whole scene and crop at the user's position
    rw, rh, crop = W, H, ""
    size = scene_size(folder) if kind == "scene" else None
    if size:
        a, pos = size[0] / size[1], user_position(we, folder)
        if a > W / H + 0.01:
            rw = round(H * a / 2) * 2
            left = round((rw - W) * (100 - pos) / 100)
            crop = f":crop_left={left}:crop_right={rw - W - left}"
        elif a < W / H - 0.01:
            rh = round(W / a / 2) * 2
            top = round((rh - H) * (100 - pos) / 100)
            crop = f":crop_top={top}:crop_bottom={rh - H - top}"

    status("Starting Wallpaper Engine...")
    if "wallpaper64.exe" not in subprocess.run(["tasklist", "/FI", "IMAGENAME eq wallpaper64.exe"],
                                               capture_output=True, text=True, creationflags=NOWIN).stdout:
        subprocess.Popen([we])
        time.sleep(8)
    wt = f"wp2video-{os.getpid()}"
    vx = user32.GetSystemMetrics(76)  # leftmost edge of all monitors
    subprocess.run([we, "-control", "openWallpaper", "-file", os.path.join(folder, "project.json"), "-playInWindow", wt,
                    "-width", str(rw), "-height", str(rh), "-x", str(vx - rw - 100), "-y", "0"], creationflags=NOWIN)
    h = 0
    for _ in range(80):
        h = find_window(wt)
        if h:
            break
        time.sleep(0.25)
    if not h:
        raise RuntimeError("Wallpaper Engine didn't open the wallpaper window.")
    ctypes.windll.kernel32.SetThreadExecutionState(0x80000003)  # keep PC and display awake
    tmp = None
    try:
        place_window(h, vx - rw - 100, 0, rw, rh)  # off-screen, so the user's desktop is untouched
        status("Loading wallpaper...")
        time.sleep(7)

        status("Testing your PC's frame rate...")
        target = {"30": 30, "60": 60, "Max": 240}[fps_choice]
        real = measure_fps(ff, h, target)
        if real < 2:  # some systems don't render off-screen windows; fall back to on-screen
            place_window(h, 0, 0, rw, rh)
            time.sleep(2)
            real = measure_fps(ff, h, target)
        if real < 2:
            raise RuntimeError("Couldn't capture the wallpaper window.")
        # capped at the target, gfxcapture delivers ~93% of it even when the wallpaper keeps up (60 reads ~56),
        # so 90% of the target counts as keeping up; before this, every 60 fps export came out at 48
        limit = target if real >= target * 0.9 else real * 1.05
        fps = max([f for f in STD_FPS if f <= limit] or [24])
        note = f" (your PC rendered ~{real:.0f} fps)" if fps < target and fps_choice != "Max" else ""

        heavy = W * H * fps > 3840 * 2160 * 60  # beyond H.264's limits
        codec = "av1" if fmt == "webm" else ("hevc" if heavy else "h264")
        enc = pick_encoder(ff, codec) or pick_encoder(ff, "h264")
        status(f"Recording {W}x{H} at {fps} fps{note}...")
        src = f"gfxcapture=hwnd={h}:max_framerate={fps}:capture_cursor=0{crop}"
        if not enc.endswith("_nvenc"):
            src += ",hwdownload,format=bgra"
        tmp = unique(title + ".part", "mkv")
        run_ffmpeg([ff, "-y", "-hide_banner", "-filter_complex", src, "-t", str(seconds), "-fps_mode", "cfr", "-r", str(fps),
                    "-c:v", enc] + enc_args(enc) + [tmp], seconds, progress)
    finally:
        subprocess.run([we, "-control", "closeWallpaper", "-location", wt], creationflags=NOWIN)
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)

    out = unique(f"{title} ({W}x{H} {fps}fps)", fmt)
    if fmt == "webm" and not enc.startswith("av1"):
        status("Converting to WebM (no hardware AV1 on this PC, this is slower)...")
        run_ffmpeg([ff, "-y", "-hide_banner", "-i", tmp, "-c:v", "libvpx-vp9", "-crf", "32", "-b:v", "0",
                    "-row-mt", "1", "-deadline", "good", "-cpu-used", "4", out], seconds, progress)
    else:
        status("Saving...")
        tag = ["-tag:v", "hvc1"] if "hevc" in enc or enc == "libx265" else []
        fast = ["-movflags", "+faststart"] if fmt == "mp4" else []
        subprocess.run([ff, "-y", "-hide_banner", "-v", "error", "-i", tmp, "-c", "copy"] + tag + fast + [out],
                       check=True, creationflags=NOWIN)
    os.remove(tmp)
    return out


# ---------- GUI ----------

def gui():
    import tkinter as tk
    from tkinter import ttk, filedialog

    root = tk.Tk()
    root.title("Wallpaper to Video")
    root.resizable(False, False)
    pad = {"padx": 14, "pady": 6}
    state = {"path": None, "busy": False}

    drop = tk.Label(root, text="Drop a wallpaper folder here\nor click to choose one", width=46, height=5,
                    relief="groove", bd=2, bg="#f4f4f4", fg="#555", font=("Segoe UI", 11), cursor="hand2")
    drop.grid(row=0, column=0, columnspan=3, sticky="we", padx=14, pady=(14, 6))

    def set_wallpaper(p):
        try:
            _, _, title, kind = load_wallpaper(p)
        except Exception as ex:
            drop.config(text=str(ex), fg="#b00020")
            return
        state["path"] = p
        drop.config(text=f"{title}\n{kind} wallpaper", fg="#111", font=("Segoe UI", 11, "bold"))
        go.config(state="normal")

    drop.bind("<Button-1>", lambda e: (p := filedialog.askdirectory(title="Choose a wallpaper folder")) and set_wallpaper(p))

    opts = {}
    for i, (label, values, default) in enumerate([("Resolution", list(RES), "1080p"),
                                                  ("Frame rate", ["30", "60", "Max"], "60"),
                                                  ("Length (seconds)", ["10", "30", "60"], "30"),
                                                  ("Format", ["mp4", "webm"], "mp4")]):
        ttk.Label(root, text=label).grid(row=1 + i, column=0, sticky="w", **pad)
        v = tk.StringVar(value=default)
        ttk.Combobox(root, textvariable=v, values=values, state="readonly", width=12).grid(row=1 + i, column=1, sticky="w", **pad)
        opts[label] = v

    ttk.Label(root, text="Save to").grid(row=5, column=0, sticky="w", **pad)
    out_var = tk.StringVar(value=downloads_dir())
    ttk.Entry(root, textvariable=out_var, width=34).grid(row=5, column=1, sticky="we", **pad)
    ttk.Button(root, text="Browse", command=lambda: (d := filedialog.askdirectory(initialdir=out_var.get())) and out_var.set(d)
               ).grid(row=5, column=2, sticky="e", padx=(0, 14))

    go = ttk.Button(root, text="Export", state="disabled")
    go.grid(row=6, column=0, columnspan=3, sticky="we", padx=14, pady=(10, 4))
    bar = ttk.Progressbar(root, maximum=1.0)
    bar.grid(row=7, column=0, columnspan=3, sticky="we", padx=14)
    msg = tk.Label(root, text="", fg="#555", font=("Segoe UI", 9), wraplength=420, justify="left")
    msg.grid(row=8, column=0, columnspan=3, sticky="w", padx=14, pady=(4, 0))
    link = tk.Label(root, text="", fg="#0b57d0", cursor="hand2", font=("Segoe UI", 9, "underline"), wraplength=420, justify="left")
    link.grid(row=9, column=0, columnspan=3, sticky="w", padx=14, pady=(0, 14))

    def ui(fn):
        root.after(0, fn)

    def start():
        if state["busy"] or not state["path"]:
            return
        state["busy"] = True
        go.config(state="disabled")
        link.config(text="")
        bar["value"] = 0
        args = (state["path"], opts["Resolution"].get(), opts["Frame rate"].get(), int(opts["Length (seconds)"].get()),
                opts["Format"].get(), out_var.get())

        def work():
            try:
                out = export(*args, status=lambda s: ui(lambda: msg.config(text=s, fg="#555")),
                             progress=lambda f: ui(lambda: bar.config(value=f)))
                def done():
                    bar["value"] = 1
                    msg.config(text="Done. Saved to:", fg="#1b5e20")
                    link.config(text=out)
                    link.bind("<Button-1>", lambda e: subprocess.Popen(f'explorer /select,"{out}"'))
                ui(done)
            except Exception as ex:
                err = f"Error: {ex}"
                ui(lambda: msg.config(text=err, fg="#b00020"))
            finally:
                state["busy"] = False
                ui(lambda: go.config(state="normal"))

        threading.Thread(target=work, daemon=True).start()

    go.config(command=start)

    # native drag-and-drop (WM_DROPFILES) without extra packages
    root.update_idletasks()
    hwnd = user32.GetParent(root.winfo_id())
    shell32 = ctypes.windll.shell32
    shell32.DragAcceptFiles(hwnd, True)
    WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    user32.CallWindowProcW.argtypes = [ctypes.c_void_p, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.CallWindowProcW.restype = ctypes.c_ssize_t
    user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
    user32.SetWindowLongPtrW.restype = ctypes.c_void_p
    shell32.DragQueryFileW.argtypes = [ctypes.c_void_p, wintypes.UINT, ctypes.c_wchar_p, wintypes.UINT]
    shell32.DragFinish.argtypes = [ctypes.c_void_p]

    def proc(h, m, w, l):
        if m == 0x0233:  # WM_DROPFILES
            buf = ctypes.create_unicode_buffer(1024)
            shell32.DragQueryFileW(w, 0, buf, 1024)
            shell32.DragFinish(w)
            state["dropped"] = buf.value  # no tkinter calls in here: re-entering Tcl crashes Python
            return 0
        return user32.CallWindowProcW(old, h, m, w, l)

    def poll_drop():
        if state.get("dropped"):
            set_wallpaper(state.pop("dropped"))
        root.after(100, poll_drop)

    poll_drop()

    new_proc = WNDPROC(proc)
    old = user32.SetWindowLongPtrW(hwnd, -4, ctypes.cast(new_proc, ctypes.c_void_p))  # GWLP_WNDPROC
    root._keep = new_proc
    root.mainloop()


if __name__ == "__main__":
    if len(sys.argv) > 1:
        if sys.stdout:  # titles can have characters the console code page can't print
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        a = sys.argv[1:] + [None] * 6
        out = export(a[0], a[1] or "1080p", a[2] or "60", int(a[3] or 30), a[4] or "mp4", a[5] or downloads_dir(),
                     status=print, progress=lambda f: None)
        print("saved:", out)
    else:
        gui()
