@echo off
rem Builds release\wp2video.zip = wp2video.exe + ffmpeg.exe + LICENSES.txt
rem Usage: build.bat [ffmpeg build folder]   (needs .venv with pyinstaller)
setlocal
cd /d "%~dp0"
set "FFDIR=%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-9.0.2-full_build"
if not "%~1"=="" set "FFDIR=%~1"
if not exist "%FFDIR%\bin\ffmpeg.exe" (echo ffmpeg not found in "%FFDIR%" & exit /b 1)

.venv\Scripts\pyinstaller --noconfirm --onefile --windowed --name wp2video wp2video.py || exit /b 1

if exist release rmdir /s /q release
mkdir release\wp2video
copy /y dist\wp2video.exe release\wp2video\ >nul || exit /b 1
copy /y "%FFDIR%\bin\ffmpeg.exe" release\wp2video\ >nul || exit /b 1
copy /y /b LICENSE + NOTICE-ffmpeg.txt + "%FFDIR%\LICENSE" release\wp2video\LICENSES.txt >nul || exit /b 1
powershell -NoProfile -Command "Compress-Archive -Force release\wp2video release\wp2video.zip" || exit /b 1
echo Built release\wp2video.zip
