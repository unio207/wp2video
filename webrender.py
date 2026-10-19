"""Render a Wallpaper Engine *web* wallpaper to video without Wallpaper Engine, on a virtual clock.

Headless Edge (or Chrome) loads the wallpaper's page. A script injected before the page runs replaces the
page's clock (performance.now, Date, timers, requestAnimationFrame), so every output frame is rendered at an
exact time: <video> elements and CSS animations are seeked to it, then the frame is screenshotted and piped
to ffmpeg. The result has exactly fps * seconds frames, at any resolution, whatever the PC's speed.

Talks to the browser over the DevTools protocol with a small built-in WebSocket client: no extra packages.
Run:  python webrender.py <wallpaper folder> [1080p|1440p|4K] [fps] [seconds] [mp4|webm] [out dir] [browsers]
"""
import base64, json, os, pathlib, shutil, socket, struct, subprocess, sys, tempfile, threading, time, urllib.request

import wp2video as w

BROWSERS = [r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe", r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe",
            r"%ProgramFiles%\Google\Chrome\Application\chrome.exe", r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
            r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"]

# Injected before any page script. Virtual time starts at 0 ms and only moves when step(t) is called.
CLOCK_JS = r"""
(() => {
  const realRAF = window.requestAnimationFrame.bind(window), realTimeout = window.setTimeout.bind(window);
  const RealDate = Date, epoch = __EPOCH__;  // shared by every browser in a render, so Date agrees across the joins
  let now = 0, nextId = 1;
  const timers = new Map(), rafs = new Map(), audio = [];
  performance.now = () => now;
  let seed = 0x2f6b9d1;  // seeded Math.random: every browser, and every run, sees the same numbers
  Math.random = () => { seed = (seed + 0x6d2b79f5) | 0; let x = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    x = (x + Math.imul(x ^ (x >>> 7), 61 | x)) ^ x; return ((x ^ (x >>> 14)) >>> 0) / 4294967296; };
  window.Date = class extends RealDate {
    constructor(...a) { a.length ? super(...a) : super(epoch + now); }
    static now() { return epoch + now; }
  };
  const call = (fn, args) => { try { typeof fn === "function" ? fn(...args) : (0, eval)(fn); } catch (e) { console.error(e); } };
  window.setTimeout = (fn, ms = 0, ...args) => { const id = nextId++; timers.set(id, { at: now + Math.max(0, +ms || 0), fn, args }); return id; };
  window.setInterval = (fn, ms = 0, ...args) => { const ev = Math.max(1, +ms || 0), id = nextId++; timers.set(id, { at: now + ev, fn, args, ev }); return id; };
  window.clearTimeout = window.clearInterval = (id) => timers.delete(id);
  window.requestAnimationFrame = (fn) => { const id = nextId++; rafs.set(id, fn); return id; };
  window.cancelAnimationFrame = (id) => rafs.delete(id);
  // Wallpaper Engine's page APIs: silent audio, no media info
  window.wallpaperRegisterAudioListener = (fn) => audio.push(fn);
  window.wallpaperRegisterMediaStatusListener = window.wallpaperRegisterMediaPropertiesListener =
    window.wallpaperRegisterMediaThumbnailListener = window.wallpaperRegisterMediaPlaybackListener =
    window.wallpaperRegisterMediaTimelineListener = () => {};
  window.__wp2v = {
    async step(t) {
      for (;;) {  // due timers in time order, intervals included
        let id = null, tm = null;
        for (const [i, x] of timers) if (x.at <= t && (!tm || x.at < tm.at)) { id = i; tm = x; }
        if (!tm) break;
        now = tm.at;
        if (tm.ev) tm.at += tm.ev; else timers.delete(id);
        call(tm.fn, tm.args);
      }
      now = t;
      const silence = new Array(128).fill(0);
      for (const fn of audio) call(fn, [silence]);
      const cbs = [...rafs.values()]; rafs.clear();
      for (const fn of cbs) call(fn, [t]);
      for (const a of document.getAnimations()) { a.pause(); a.currentTime = t; }
      await Promise.all([...document.querySelectorAll("video")].map((v) => {
        v.pause();
        if (!(v.duration > 0)) return;
        const s = v.loop ? (t / 1000) % v.duration : Math.min(t / 1000, v.duration);
        if (Math.abs(v.currentTime - s) < 1e-4) return;
        return new Promise((r) => { v.addEventListener("seeked", r, { once: true }); v.currentTime = s; });
      }));
      await new Promise((r) => realRAF(() => realRAF(r)));  // let the browser paint this state
    },
    // project.json defaults, the way Wallpaper Engine hands them to the page
    applyProperties(props) {
      const l = window.wallpaperPropertyListener;
      if (l && l.applyUserProperties) call(l.applyUserProperties.bind(l), [props]);
      if (l && l.applyGeneralProperties) call(l.applyGeneralProperties.bind(l), [{ fps: 60 }]);
    },
    async ready() {
      await Promise.all([...document.querySelectorAll("video")].map((v) =>
        v.readyState >= 1 ? 0 : new Promise((r) => { v.addEventListener("loadedmetadata", r, { once: true }); realTimeout(r, 5000); })));
      if (document.fonts) await document.fonts.ready;
    },
  };
})();
"""


class WebSocket:
    """Just enough RFC 6455 for one local DevTools connection: text frames out, text frames in."""

    def __init__(self, url):
        host, _, path = url[len("ws://"):].partition("/")
        h, _, p = host.partition(":")
        self.s = socket.create_connection((h, int(p)))
        key = base64.b64encode(os.urandom(16)).decode()
        self.s.sendall(f"GET /{path} HTTP/1.1\r\nHost: {host}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                       f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n".encode())
        head = b""
        while b"\r\n\r\n" not in head:
            head += self.s.recv(4096)
        head, self.buf = head.split(b"\r\n\r\n", 1)
        if b" 101 " not in head.split(b"\r\n")[0]:
            raise RuntimeError("DevTools refused the WebSocket: " + head.split(b"\r\n")[0].decode())

    def _read(self, n):
        while len(self.buf) < n:
            chunk = self.s.recv(1 << 20)
            if not chunk:
                raise RuntimeError("Browser closed the DevTools connection.")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def send(self, text):
        data = text.encode()
        n = len(data)
        head = bytes([0x81]) + (bytes([0x80 | n]) if n < 126 else bytes([0xFE]) + struct.pack(">H", n) if n < 65536
                                else bytes([0xFF]) + struct.pack(">Q", n))
        mask = os.urandom(4)
        masked = (int.from_bytes(data, "big") ^ int.from_bytes((mask * (n // 4 + 1))[:n], "big")).to_bytes(n, "big") if n else b""
        self.s.sendall(head + mask + masked)

    def recv(self):
        parts = []
        while True:
            b0, b1 = self._read(2)
            n = b1 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._read(8))[0]
            payload = self._read(n)
            op = b0 & 0x0F
            if op == 0x9:  # ping
                self.s.sendall(bytes([0x8A, 0x80 | len(payload)]) + b"\0\0\0\0" + payload)
                continue
            if op == 0x8:
                raise RuntimeError("Browser closed the DevTools connection.")
            parts.append(payload)
            if b0 & 0x80:
                return b"".join(parts).decode()


class Browser:
    def __init__(self, W, H):
        exe = next((p for p in map(os.path.expandvars, BROWSERS) if os.path.exists(p)), None)
        if not exe:
            raise RuntimeError("Needs Microsoft Edge or Google Chrome.")
        self.profile = tempfile.mkdtemp(prefix="wp2video-")
        self.p = subprocess.Popen([exe, "--headless=new", "--remote-debugging-port=0", f"--user-data-dir={self.profile}",
                                   f"--window-size={W},{H}", "--hide-scrollbars", "--mute-audio", "--no-first-run",
                                   "--no-default-browser-check", "--autoplay-policy=no-user-gesture-required",
                                   "--allow-file-access-from-files", "--disable-background-timer-throttling",
                                   "--disable-renderer-backgrounding", "--disable-backgrounding-occluded-windows",
                                   "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=w.NOWIN)
        port_file = os.path.join(self.profile, "DevToolsActivePort")
        for _ in range(100):
            if os.path.exists(port_file) and open(port_file).read().count("\n") >= 1:
                break
            time.sleep(0.1)
        else:
            self.close()
            raise RuntimeError("The browser didn't start its DevTools port.")
        port = open(port_file).read().split("\n")[0].strip()
        pages = [t for t in json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list")) if t["type"] == "page"]
        self.ws = WebSocket(pages[0]["webSocketDebuggerUrl"])
        self.n = 0
        self.call("Emulation.setDeviceMetricsOverride", width=W, height=H, deviceScaleFactor=1, mobile=False)

    def call(self, method, **params):
        self.n += 1
        self.ws.send(json.dumps({"id": self.n, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == self.n:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error'].get('message')}")
                return msg.get("result", {})

    def js(self, expr):
        r = self.call("Runtime.evaluate", expression=expr, awaitPromise=True, returnByValue=True)
        if "exceptionDetails" in r:
            raise RuntimeError("Page script failed: " + r["exceptionDetails"].get("text", ""))
        return r.get("result", {}).get("value")

    def close(self):
        try:
            self.p.kill()
            self.p.wait(5)
        except Exception:
            pass
        shutil.rmtree(getattr(self, "profile", ""), ignore_errors=True)


def default_properties(proj):
    """The {name: {value}} properties Wallpaper Engine would pass on first load, from project.json."""
    props = (proj.get("general") or {}).get("properties") or {}
    return {k: {"value": v.get("value")} for k, v in props.items() if isinstance(v, dict) and "value" in v}


def open_page(b, folder, proj, epoch):
    """Load the wallpaper's page on the virtual clock, with its videos, fonts and default properties ready."""
    b.call("Page.enable")
    b.call("Page.addScriptToEvaluateOnNewDocument", source=CLOCK_JS.replace("__EPOCH__", str(epoch)))
    b.call("Page.navigate", url=pathlib.Path(folder, proj.get("file", "index.html")).resolve().as_uri())
    for _ in range(200):
        if b.js("document.readyState") == "complete":
            break
        time.sleep(0.05)
    b.js("window.__wp2v.ready()")
    b.js(f"window.__wp2v.applyProperties({json.dumps(default_properties(proj))})")


def render_still(path, res, out_dir, status, at=2.0):
    """One lossless PNG of the page `at` seconds in. The clock steps there at 60 fps, so pages that animate
    their way in look the way they would on screen."""
    folder, proj, title, kind = w.load_wallpaper(path)
    if kind != "web":
        raise RuntimeError(f"Only web wallpapers render this way, not {kind or 'unknown'}.")
    W, H = w.RES[res]
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, f"{title} ({W}x{H}).png")
    k = 2
    while os.path.exists(out):
        out = os.path.join(out_dir, f"{title} ({W}x{H}) ({k}).png")
        k += 1
    status("Starting the browser...")
    b = Browser(W, H)
    try:
        open_page(b, folder, proj, int(time.time() * 1000))
        status(f"Saving a full-size still ({W}x{H})...")
        for i in range(round(at * 60) + 1):
            b.js(f"window.__wp2v.step({i * 1000 / 60})")
        with open(out, "wb") as f:
            f.write(base64.b64decode(b.call("Page.captureScreenshot", format="png")["data"]))
    finally:
        b.close()
    return out


def render_range(folder, proj, W, H, fps, start, end, ff, enc, seg, tick, epoch):
    """Frames start..end-1 into seg. The page's clock is first run from 0 to start without screenshots, so a
    page that builds up state (particles, simulations) reaches frame `start` the same way a single pass would."""
    b = Browser(W, H)
    enc_p = None
    try:
        open_page(b, folder, proj, epoch)
        for i in range(start):
            b.js(f"window.__wp2v.step({i * 1000 / fps})")
        enc_p = subprocess.Popen([ff, "-y", "-hide_banner", "-v", "error", "-f", "image2pipe", "-c:v", "mjpeg",
                                  "-framerate", str(fps), "-i", "-"] + enc + ["-an", seg],
                                 stdin=subprocess.PIPE, creationflags=w.NOWIN)
        for i in range(start, end):
            b.js(f"window.__wp2v.step({i * 1000 / fps})")
            shot = b.call("Page.captureScreenshot", format="jpeg", quality=95, optimizeForSpeed=True)
            enc_p.stdin.write(base64.b64decode(shot["data"]))
            tick()
        enc_p.stdin.close()
        if enc_p.wait():
            raise RuntimeError("ffmpeg failed to encode the frames.")
    finally:
        b.close()
        if enc_p and enc_p.poll() is None:
            enc_p.kill()


def render_web(path, res, fps, seconds, fmt, out_dir, status, progress, workers=None, seamless=False):
    ff = w.find_ffmpeg()
    folder, proj, title, kind = w.load_wallpaper(path)
    if kind != "web":
        raise RuntimeError(f"Only web wallpapers render this way, not {kind or 'unknown'}.")
    W, H = w.RES[res]
    seconds = float(seconds)
    fps, total = int(fps), round((seconds + (w.seamless_fade(seconds) if seamless else 0)) * int(fps))
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, f"{title} ({W}x{H} {fps}fps).{fmt}")
    k = 2
    while os.path.exists(out):
        out = os.path.join(out_dir, f"{title} ({W}x{H} {fps}fps) ({k}).{fmt}")
        k += 1

    codec = w.capture_encoder(ff, fmt, W, H, fps)
    enc, mux = w.output_args(codec, fmt)

    # Frames don't depend on how long each takes, so the clip is split across browsers and joined losslessly.
    # The screenshot is the slow part (~47 ms a frame at 1080p), and it runs per browser.
    n = workers or (2 if total >= 120 else 1)  # measured at 1080p: 1 browser 46.7 s, 2 31.7 s, 4 34.6 s, 6 43.3 s
    bounds = [total * j // n for j in range(n + 1)]
    tmp = tempfile.mkdtemp(prefix="wp2video-")
    segs = [os.path.join(tmp, f"{j}.mkv") for j in range(n)]
    done, lock, errors = [0], threading.Lock(), []
    epoch = int(time.time() * 1000)  # the page's Date at frame 0

    def tick():
        with lock:
            done[0] += 1
            progress(done[0] / total)

    def run(j):
        try:
            render_range(folder, proj, W, H, fps, bounds[j], bounds[j + 1], ff, enc, segs[j], tick, epoch)
        except Exception as ex:
            errors.append(ex)

    status(f"Rendering {W}x{H} at {fps} fps: {total} frames in {n} browser{'s' if n > 1 else ''}...")
    try:
        threads = [threading.Thread(target=run, args=(j,), daemon=True) for j in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        if errors:
            raise errors[0]
        status("Saving...")
        listing = os.path.join(tmp, "segments.txt")
        with open(listing, "w", encoding="utf-8") as f:
            f.writelines(f"file '{s}'\n" for s in segs)
        joined = os.path.join(tmp, "joined.mkv") if seamless else out
        subprocess.run([ff, "-y", "-hide_banner", "-v", "error", "-f", "concat", "-safe", "0", "-i", listing,
                        "-c", "copy"] + ([] if seamless else mux) + [joined], check=True, creationflags=w.NOWIN)
        if seamless:
            status("Making it loop seamlessly...")
            w.make_seamless(ff, joined, out, seconds, codec, fmt, progress)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return out


if __name__ == "__main__":
    if sys.stdout:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    a = sys.argv[1:] + [None] * 7
    t0 = time.time()
    out = render_web(a[0], a[1] or "1080p", a[2] or 60, a[3] or 10, a[4] or "mp4", a[5] or w.downloads_dir(),
                     status=print, progress=lambda f: None, workers=int(a[6]) if a[6] else None)
    print(f"saved: {out} ({time.time() - t0:.1f} s)")
