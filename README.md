# wp2video

Turn a Wallpaper Engine wallpaper into a video file (MP4 or WebM).

## Download and run

1. Go to the [latest release](https://github.com/unio207/wp2video/releases/latest) and click **wp2video.zip** to download it.
2. Right-click the downloaded zip, choose **Extract All...**, then click **Extract**.
3. Open the extracted folder and double-click **wp2video.exe**.
   - If Windows says *"Windows protected your PC"*, click **More info**, then **Run anyway**.
     (This appears because the app isn't code-signed.)

Keep `wp2video.exe` and `ffmpeg.exe` in the same folder.

You need **Windows 10 or 11** and **Wallpaper Engine** (from Steam). Nothing else to install.

## Make a video

1. Drag a wallpaper folder onto the window, or click the box to choose one.
   Wallpaper folders are in `Steam\steamapps\workshop\content\431960\`.
   Tip: in Wallpaper Engine, right-click a wallpaper and choose **Open in Explorer**.
2. Pick resolution, frame rate, length and format.
3. Click **Export**. When it's done, click the file name to open it.

Videos are saved to your **Downloads** folder unless you pick another one.

## Build from source

```
python -m venv .venv
.venv\Scripts\pip install pyinstaller
build.bat
```

This creates `release\wp2video.zip`. `build.bat` uses the ffmpeg folder from
`winget install Gyan.FFmpeg`, or pass another ffmpeg build folder as its argument.
Without building, run `python wp2video.py` with `ffmpeg.exe` (8.0 or newer) next to it or on PATH.

## License

wp2video is MIT licensed (see `LICENSE`). The bundled `ffmpeg.exe` is FFmpeg under
GPLv3; see `LICENSES.txt` in the download for details and source links.
