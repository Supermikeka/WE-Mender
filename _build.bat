@echo off
chcp 65001 >nul
setlocal

rem 定位 Python：优先项目内 .venv，其次 PATH 里的 python（需已 pip install pyinstaller）
set DIR=%~dp0
set PY=
if exist "%DIR%.venv\Scripts\python.exe" set PY=%DIR%.venv\Scripts\python.exe
if not defined PY for /f "delims=" %%i in ('where python 2^>nul') do if not defined PY set PY=%%i

echo ============================================
echo   打包 WE-Mender 3.0（GUI 单文件 exe）
echo ============================================
echo.

if not defined PY (
    echo [错误] 未找到 Python。请安装 Python 并执行：
    echo   pip install pyinstaller
    pause
    exit /b 1
)

cd /d "%DIR%"

"%PY%" -m PyInstaller --clean --noconfirm WE-Mender.spec

if errorlevel 1 (
    echo.
    echo [失败] 打包出错。
    pause
    exit /b 1
)

echo.
echo ============================================
echo   完成
echo ============================================
echo   产物：%DIR%dist\WE-Mender.exe
echo.
echo   双击运行即可；「排查结果.txt」「backups」会生成在 exe 同目录。
echo   控制台模式仍可用源码运行：python we_mender.py --mode 0
echo.
pause
