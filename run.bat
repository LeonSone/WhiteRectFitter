@echo off
cd /d "D:\VSCODE\PYTHON\WhiteRectFitter"
set PATH=%PATH%;D:\OpenCV\Build\bin\Release
build\Release\wrf.exe --play data\boxes512.bin --audio bad_apple.mp3 --sw 1440 --sh 1080 > stdout.txt 2> stderr.txt
echo Exit code: %ERRORLEVEL%
