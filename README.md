# wp2video

<em>Turn a Wallpaper Engine wallpaper into an MP4 or WebM file, on Windows.</em>

<p align="center">
  <img src="docs/images/export.gif" width="562" alt="The wp2video window, titled Wallpaper to Video: the example wallpaper folder ISS Earth view is dropped in, length is set to 10 seconds and the save folder to Videos, Export is clicked, the status line steps through Loading wallpaper, Testing your PC's frame rate and Recording 1920x1080 at 60 fps, the progress bar fills, and it ends on Done. Saved to: ISS Earth view (1920x1080 60fps).mp4">
  <br>
  <sub>A real export of the example wallpaper in <code>examples/iss-earth</code>, recorded from the app (31 s, played at 1.5x). A script clicked through the GUI; the export itself is unmodified.</sub>
</p>

<p align="center">
  <img src="docs/images/output.gif" width="640" alt="The exported video: a Soyuz spacecraft docked to the space station drifts over a cloud-covered Earth against black space, with a small ISS HDEV CAMERA label and a running UTC clock in the bottom-left corner">
  <br>
  <sub>The file that export produced, 5 s of it as a 640 px GIF (the MP4 is 1920x1080 at 60 fps). The clock is drawn live by the wallpaper's page, so it shows Wallpaper Engine rendering it, not a copied video.</sub>
</p>

wp2video opens the wallpaper in Wallpaper Engine's own renderer, in a window placed off-screen, and records that window with ffmpeg. Scene and web wallpapers keep their animation, effects and particles, because Wallpaper Engine is what draws them.

## Get it

You need **Windows 10 or 11** and **Wallpaper Engine** (from Steam).

There is no published release yet, so for now get `wp2video.zip` by [building it](#build-from-source). It's 100 MB, or 240 MB unzipped, almost all of it `ffmpeg.exe`. Then:

1. Right-click `wp2video.zip`, choose **Extract All...**, then click **Extract**.
2. Open the extracted `wp2video` folder and double-click **wp2video.exe**.
   If Windows says *"Windows protected your PC"*, click **More info**, then **Run anyway**. This appears because the exe isn't code-signed.

Keep `wp2video.exe` and `ffmpeg.exe` in the same folder.

## Make a video

1. Drag a wallpaper folder onto the window, or click the box to choose one.
   Wallpaper folders are in `Steam\steamapps\workshop\content\431960\<id>\`.
   In Wallpaper Engine, right-click a wallpaper and choose **Open in Explorer** to find one.
   To try it without picking one, use `examples\iss-earth` from this repo.
2. Pick resolution (1080p, 1440p, 4K), frame rate (30, 60, or Max: as high as the wallpaper renders, up to 240 and up to Wallpaper Engine's own FPS limit in its Performance settings), length (10, 30, 60 s) and format (mp4, webm).
3. Click **Export**. When it's done, click the file name to show it in Explorer.

Videos are saved to your **Downloads** folder unless you pick another one.

Limits: the file has no audio. Video wallpapers skip the recording: the source file is copied, or converted if the format differs, so they ignore the resolution, frame rate and length settings, and WebM conversion of them is always VP9 on the CPU. Application wallpapers aren't supported.

Without the GUI, the same export runs from a terminal:

```console
> python wp2video.py examples\iss-earth 1080p 60 10 mp4 out
Starting Wallpaper Engine...
Loading wallpaper...
Testing your PC's frame rate...
Recording 1920x1080 at 60 fps...
Saving...
saved: out\ISS Earth view (1920x1080 60fps).mp4
```

The arguments are `<folder> [1080p|1440p|4K] [30|60|Max] [seconds] [mp4|webm] [out dir]`. This run took 21 s end to end on an RTX 4060 with NVENC. 10 s of that is fixed waiting: 7 s for the wallpaper to load and 3 s to measure its frame rate (the `time.sleep(7)` and `measure_fps` calls in `export()`). If Wallpaper Engine isn't already running, starting it adds another 8 s.

## The example wallpaper

`examples/iss-earth` is a small web wallpaper made for this repo: `project.json`, an `index.html` that loops `earth.webm` (14 s, 1280x720, 1 MB) and draws a clock on top. The footage is from NASA's [Earth Views from the International Space Station](https://images.nasa.gov/details/Earth%20Views%20from%20the%20International%20Space%20Station) (HDEV camera), trimmed to 1:48-2:02 and re-encoded. NASA material is public domain in the US; the NASA name and insignia aren't used here.

Wallpaper Engine opens it straight from the repo folder, so it doesn't have to be in the Workshop folder.

## How it works

All of it is in `wp2video.py`.

- **Rendering.** It runs `wallpaper64.exe -control openWallpaper -playInWindow`, then moves that window just left of the leftmost monitor, so your desktop and current wallpaper stay as they are. If a system doesn't render off-screen windows (the capture measures under 2 fps), it moves the window on-screen and tries again. In that case the window covers the top-left of your main screen while it records.
- **Capture.** ffmpeg's `gfxcapture` filter (Windows Graphics Capture, ffmpeg 8.0+) records the window by handle, without the cursor. The PC is kept awake while it records.
- **Frame rate.** It captures for 3 s first, capped at your pick, and counts the frames. Reaching 90% of the pick counts as keeping up, because the capture limiter delivers less than its cap even when the wallpaper keeps pace: capped at 60, both test wallpapers read 55 to 57 fps, and capped at 30, 28. Below 90%, the output uses the highest standard rate (24, 30, 48, 60, 90 ... 240) at or below 1.05 × the measured rate. A constant rate the PC can't keep up would fill the file with duplicated frames, so it rounds down instead. The floor is 24 fps, so a PC rendering below about 23 fps still gets duplicated frames. With Max, a 4K Workshop scene measured about 80 fps here and was saved at 60, and the example measured 175 and was saved at 180. Wallpaper Engine's limit on this PC is 180.
- **Aspect ratio.** A scene whose native size differs from the output (read from `scene.json`, loose or inside `scene.pkg`) is rendered whole at its own aspect ratio, then cropped. The crop follows the alignment slider you set for that wallpaper in Wallpaper Engine (read from its `config.json`), so the video frames the wallpaper the way your desktop does.
- **Encoding.** It probes NVENC, AMF and Quick Sync in that order and uses the first that works, falling back to x264 or x265 on the CPU. When the pixel rate goes past 4K at 60 fps (4K above 60, or 1440p at 144 and up, both only reachable with Max) it switches to HEVC, because that is past what H.264 encoders handle (the `heavy` check in `export()`). WebM is AV1 when the GPU can encode it; otherwise it's re-encoded to VP9 afterwards, which is slower.
- **Drag and drop** uses the Win32 `WM_DROPFILES` message directly, so the GUI is plain tkinter with no extra packages.

## Build from source

Needs Python 3.8 or newer (built here with 3.12) and ffmpeg 8.0 or newer. Run these in the repo folder:

```console
winget install Gyan.FFmpeg
python -m venv .venv
.venv\Scripts\pip install pyinstaller
build.bat
```

This makes `release\wp2video.zip`, with `wp2video.exe`, `ffmpeg.exe` and `LICENSES.txt` at its top level. `build.bat` uses the most recently installed ffmpeg from winget and writes its version into `LICENSES.txt`. To use a different ffmpeg, pass its folder (the one that contains `bin\`): `build.bat C:\path\to\ffmpeg-build`. It runs PyInstaller as `python -m PyInstaller` rather than `pyinstaller.exe`, because pip's exe launchers hard-code the venv's path and stop working if the folder is moved.

To run without building, use `python wp2video.py` with `ffmpeg.exe` next to it or on PATH. There are no automated tests. The checks are manual exports like the one above.

## License

wp2video is MIT licensed (see `LICENSE`). The bundled `ffmpeg.exe` is an unmodified FFmpeg build under GPLv3. It runs as a separate program and is not covered by the MIT license; `LICENSES.txt` in the zip has the full text and source links. The example footage is NASA's and public domain (see [The example wallpaper](#the-example-wallpaper)).
