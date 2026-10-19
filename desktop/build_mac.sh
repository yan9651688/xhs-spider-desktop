#!/bin/bash
# macOS 打包脚本：产出 dist/XhsSpider.app 与 dist/XhsSpider.dmg
# 前置：项目根目录已有 .venv 并安装 desktop/requirements.txt，且 npm install 过（node_modules/crypto-js）
# 可选：BUNDLE_NODE=1 时自动执行 bundle_node.sh，把 Node 一起打进包（免用户安装 Node）
#
# 打 Intel 包（Apple Silicon 上交叉构建）：
#   PY=.venv-x64/bin/python TARGET_ARCH=x86_64 BUNDLE_NODE=1 ./desktop/build_mac.sh
#   .venv-x64 必须是 x86_64 解释器建的 venv（Rosetta 下运行），产物名带 -x86_64 后缀。
set -euo pipefail
cd "$(dirname "$0")/.."

PY=${PY:-.venv/bin/python}
TARGET_ARCH=${TARGET_ARCH:-}
[ -x "$PY" ] || { echo "缺少 .venv，请先：python3.12 -m venv .venv && .venv/bin/pip install -r desktop/requirements.txt"; exit 1; }

# PyInstaller 只会用当前解释器的架构出包：用 arm64 解释器配 --target-arch x86_64
# 会退化成源码编译 bootloader，而本机 libxcrun.dylib 没有 x86_64 切片，必失败。
PY_ARCH=$("$PY" -c 'import platform; print(platform.machine())')
if [ -n "$TARGET_ARCH" ] && [ "$PY_ARCH" != "$TARGET_ARCH" ]; then
  echo "解释器架构 ($PY_ARCH) 与 TARGET_ARCH ($TARGET_ARCH) 不一致。"
  echo "Intel 包需要用 x86_64 解释器：PY=.venv-x64/bin/python TARGET_ARCH=x86_64 $0"
  exit 1
fi

[ -f assets/app.icns ] || "$PY" desktop/make_icon.py

if [ "${BUNDLE_NODE:-0}" = "1" ]; then
  # 交叉构建时不能靠 uname -m 猜：外层是 arm64 shell 也必须打进 x64 的 node
  NODE_ARCH=$PY_ARCH ./desktop/bundle_node.sh
  EXTRA=("--add-data" "node_dist:node_dist")
else
  EXTRA=()
fi

# 交叉构建（解释器架构 != 本机架构）时产物落到独立目录，避免覆盖另一架构的 dist/XhsSpider.app
# 注意：PyInstaller 会在 --workpath 下再建一层 <name> 子目录，所以这里给的是父目录。
if [ "$PY_ARCH" != "$(uname -m)" ]; then
  DISTPATH=${DISTPATH:-dist-$PY_ARCH}
  WORKPATH=${WORKPATH:-build/$PY_ARCH}
else
  DISTPATH=${DISTPATH:-dist}
  WORKPATH=${WORKPATH:-build}
fi

# Rosetta 进程里 /usr/bin/{lipo,install_name_tool,otool} 都是 xcrun shim，要 dlopen 只有 arm64 切片的
# libxcrun，必然报 "unable to load libxcrun"。PyInstaller 处理 Mach-O 时三者都会调，所以各包一层原生调用。
if [ "$PY_ARCH" != "$(uname -m)" ] && [ "$(uname -m)" = "arm64" ]; then
  SHIM_DIR=$(mktemp -d)
  trap 'rm -rf "$SHIM_DIR"' EXIT
  for tool in lipo install_name_tool otool; do
    printf '#!/bin/sh\nexec arch -arm64 /usr/bin/%s "$@"\n' "$tool" > "$SHIM_DIR/$tool"
    chmod +x "$SHIM_DIR/$tool"
  done
  export PATH="$SHIM_DIR:$PATH"
fi

"$PY" -m PyInstaller \
  --noconfirm --clean --windowed \
  --name XhsSpider \
  --icon "assets/app.icns" \
  --distpath "$DISTPATH" \
  --workpath "$WORKPATH" \
  --add-data "xhs_utils/xhs_pc/js:xhs_utils/xhs_pc/js" \
  --add-data "xhs_utils/xhs_core/js:xhs_utils/xhs_core/js" \
  --add-data "node_modules:node_modules" \
  --collect-all curl_cffi \
  ${EXTRA[@]+"${EXTRA[@]}"} \
  main.py

# 生成 DMG 安装包
SUFFIX=""
[ "$PY_ARCH" = "x86_64" ] && SUFFIX="-x86_64"
rm -f "dist/XhsSpider${SUFFIX}.dmg"
hdiutil create -ov -volname "XhsSpider" \
  -srcfolder "$DISTPATH/XhsSpider.app" \
  "dist/XhsSpider${SUFFIX}.dmg"

echo
echo "打包完成（架构 ${PY_ARCH}）："
echo "  $DISTPATH/XhsSpider.app"
echo "  dist/XhsSpider${SUFFIX}.dmg   ← 发给客户的就是这个"
if [ "${BUNDLE_NODE:-0}" != "1" ]; then
  echo "提示：未内置 Node，目标机器需安装 Node.js 20+（推荐 BUNDLE_NODE=1）"
fi
