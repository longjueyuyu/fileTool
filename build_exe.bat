@echo off
chcp 65001 >nul
echo 正在安装打包依赖...
pip install -r requirements-build.txt -q
pip install -r requirements.txt -q
echo.
echo 正在打包（PyInstaller）...
pyinstaller main.spec --noconfirm --clean
if errorlevel 1 (
    echo 打包失败
    exit /b 1
)
echo.
echo 打包完成。输出: dist\局域网文件共享.exe
echo 将 dist 目录下的「局域网文件共享.exe」发给客户即可（单文件，无需其他依赖）。
pause
