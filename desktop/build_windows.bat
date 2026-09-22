@echo off
REM ============================================================
REM  Windows 一键打包脚本（在 Windows 10/11 机器上执行）
REM  前置：1) Python 3.10-3.12（安装时勾选 Add to PATH）
REM        2) Node.js 20+（https://nodejs.org）
REM        3) 本目录为项目根目录，已执行 npm install
REM  产物：dist\XhsSpider-win64.zip（解压即用，自带 Node）
REM ============================================================
setlocal
cd /d "%~dp0.."

if not exist .venv (
  python -m venv .venv || goto :err
)
.venv\Scripts\python -m pip install -q --upgrade pip || goto :err
.venv\Scripts\pip install -q -r desktop\requirements.txt pyinstaller || goto :err

if not exist node_modules (
  npm install --no-fund --no-audit || goto :err
)

REM 自带 Node 运行时，客户机无需安装
if not exist node_dist\bin mkdir node_dist\bin
copy /y "%ProgramFiles%\nodejs\node.exe" node_dist\bin\ >nul 2>&1
if not exist node_dist\bin\node.exe (
  for /f %%i in ('where node') do copy /y "%%i" node_dist\bin\ >nul 2>&1 && goto :node_ok
)
:node_ok
if not exist node_dist\bin\node.exe (
  echo [警告] 未找到 node.exe，打包继续，但客户机需自行安装 Node.js 20+
)

if not exist assets\app.ico .venv\Scripts\python desktop\make_icon.py

.venv\Scripts\pyinstaller --noconfirm --clean --windowed ^
  --name XhsSpider ^
  --icon "assets\app.ico" ^
  --add-data "xhs_utils\xhs_pc\js;xhs_utils\xhs_pc\js" ^
  --add-data "xhs_utils\xhs_core\js;xhs_utils\xhs_core\js" ^
  --add-data "node_modules;node_modules" ^
  --add-data "node_dist;node_dist" ^
  --collect-all curl_cffi ^
  main.py || goto :err

powershell -NoProfile -Command "Compress-Archive -Path 'dist\XhsSpider\*' -DestinationPath 'dist\XhsSpider-win64.zip' -Force" || goto :err

echo.
echo 打包完成：dist\XhsSpider-win64.zip   ← 发给客户的就是这个
goto :eof

:err
echo [错误] 打包失败，请检查上方日志
exit /b 1
