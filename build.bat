@echo off
rem Builds release\wp2video.zip = wp2video.exe + ffmpeg.exe + LICENSES.txt
rem Usage: build.bat [ffmpeg build folder]   (needs .venv with pyinstaller)
rem Without an argument it uses the most recently installed ffmpeg from "winget install Gyan.FFmpeg".
setlocal
cd /d "%~dp0"
set "FFDIR="
for /d %%P in ("%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg_*") do (
  for /f "delims=" %%B in ('dir /b /ad /od "%%P\ffmpeg-*" 2^>nul') do set "FFDIR=%%P\%%B"
)
if not "%~1"=="" set "FFDIR=%~1"
if not exist "%FFDIR%\bin\ffmpeg.exe" (echo ffmpeg not found. Run: winget install Gyan.FFmpeg, or pass an ffmpeg build folder. & exit /b 1)
echo Using ffmpeg from %FFDIR%

rem python -m, not pyinstaller.exe: the exe launcher hard-codes the venv's path and breaks if the folder moves
.venv\Scripts\python -m PyInstaller --noconfirm --onefile --windowed --name wp2video wp2video.py || exit /b 1

if exist release rmdir /s /q release
mkdir release\wp2video
copy /y dist\wp2video.exe release\wp2video\ >nul || exit /b 1
copy /y "%FFDIR%\bin\ffmpeg.exe" release\wp2video\ >nul || exit /b 1
(echo FFmpeg build in this download:& "%FFDIR%\bin\ffmpeg.exe" -hide_banner -version | findstr /b /c:"ffmpeg version"& echo.) > build\ffversion.txt
copy /y /b LICENSE + NOTICE-ffmpeg.txt + build\ffversion.txt + "%FFDIR%\LICENSE" release\wp2video\LICENSES.txt >nul || exit /b 1
rem zip the folder's contents, not the folder, so Extract All gives wp2video\wp2video.exe
powershell -NoProfile -Command "Compress-Archive -Force release\wp2video\* release\wp2video.zip" || exit /b 1
echo Built release\wp2video.zip
