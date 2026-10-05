@echo off
REM ============================================================
REM  Windows 一键打包脚本（在 Windows 10/11 机器上执行）
REM  前置：1) Python 3.10-3.12（安装时勾选 Add to PATH）
REM        2) Node.js 20+（https://nodejs.org）
REM        3) 本目录为项目根目录
REM  产物：dist\XhsSpider-win64.zip（解压即用，自带 Node）
REM
REM  国内网络：默认走阿里云 PyPI 镜像 + npmmirror，免得直连被墙把安装卡死。
REM  要换源就预先设环境变量：set PIP_INDEX=...  set NPM_REGISTRY=...
REM ============================================================
setlocal
cd /d "%~dp0.."

if not defined PIP_INDEX set "PIP_INDEX=https://mirrors.aliyun.com/pypi/simple"
if not defined NPM_REGISTRY set "NPM_REGISTRY=https://registry.npmmirror.com"

if not exist .venv (
  echo [1/5] 创建虚拟环境
  python -m venv .venv || goto :err
)
echo [1/5] 安装 Python 依赖（源：%PIP_INDEX%）
.venv\Scripts\python -m pip install -q -i "%PIP_INDEX%" --upgrade pip || goto :err
.venv\Scripts\pip install -q -i "%PIP_INDEX%" -r desktop\requirements.txt pyinstaller || goto :err

echo [2/5] 安装前端依赖（源：%NPM_REGISTRY%）
if not exist node_modules (
  npm install --no-fund --no-audit --registry=%NPM_REGISTRY% || goto :err
)

echo [3/5] 自带 Node 运行时（客户机无需安装）
REM Windows 官方 node.exe 是单文件自包含的，直接拷就行。
REM 注意 macOS 那边不能这么干：Homebrew 的 node 动态链接一堆 dylib，必须另下官方独立包。
if not exist node_dist\bin mkdir node_dist\bin
if not exist node_dist\bin\node.exe if exist "%ProgramFiles%\nodejs\node.exe" copy /y "%ProgramFiles%\nodejs\node.exe" node_dist\bin\ >nul

REM "delims=" 不能省：node 常装在 "C:\Program Files\nodejs"，
REM for /f 默认按空格切，%%i 只会拿到 "C:\Program"，拷贝必然失败。
if not exist node_dist\bin\node.exe for /f "delims=" %%i in ('where node 2^>nul') do (
  if not exist node_dist\bin\node.exe copy /y "%%i" node_dist\bin\ >nul 2>&1
)

REM 拷完必须验一次能不能跑：跑不起来的 node 比没有更坑，到客户机上才炸。
if exist node_dist\bin\node.exe (
  node_dist\bin\node.exe -v
  if errorlevel 1 (
    echo [警告] node_dist\bin\node.exe 无法运行，已丢弃
    del /q node_dist\bin\node.exe
  )
)
if not exist node_dist\bin\node.exe (
  echo [警告] 未找到可用的 node.exe，打包继续，但客户机需自行安装 Node.js 20+
)

if not exist assets\app.ico .venv\Scripts\python desktop\make_icon.py

echo [4/5] PyInstaller 打包
.venv\Scripts\pyinstaller --noconfirm --clean --windowed ^
  --name XhsSpider ^
  --icon "assets\app.ico" ^
  --add-data "xhs_utils\xhs_pc\js;xhs_utils\xhs_pc\js" ^
  --add-data "xhs_utils\xhs_core\js;xhs_utils\xhs_core\js" ^
  --add-data "node_modules;node_modules" ^
  --add-data "node_dist;node_dist" ^
  --collect-all curl_cffi ^
  main.py || goto :err

echo [5/5] 生成 zip
powershell -NoProfile -Command "Compress-Archive -Path 'dist\XhsSpider\*' -DestinationPath 'dist\XhsSpider-win64.zip' -Force" || goto :err
if exist dist\XhsSpider-win64.zip goto :zip_ok
echo [错误] zip 未生成
goto :err
:zip_ok

echo.
echo 打包完成：dist\XhsSpider-win64.zip   ← 发给客户的就是这个
goto :eof

:err
echo [错误] 打包失败，请检查上方日志
exit /b 1
