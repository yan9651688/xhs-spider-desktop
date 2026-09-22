#!/bin/bash
# macOS 打包脚本：产出 dist/XhsSpider.app 与 dist/XhsSpider.dmg
# 前置：项目根目录已有 .venv 并安装 desktop/requirements.txt，且 npm install 过（node_modules/crypto-js）
# 可选：BUNDLE_NODE=1 时自动执行 bundle_node.sh，把 Node 一起打进包（免用户安装 Node）
set -euo pipefail
cd "$(dirname "$0")/.."

PY=${PY:-.venv/bin/python}
[ -x "$PY" ] || { echo "缺少 .venv，请先：python3.12 -m venv .venv && .venv/bin/pip install -r desktop/requirements.txt"; exit 1; }

[ -f assets/app.icns ] || "$PY" desktop/make_icon.py

if [ "${BUNDLE_NODE:-0}" = "1" ]; then
  ./desktop/bundle_node.sh
  EXTRA=("--add-data" "node_dist:node_dist")
else
  EXTRA=()
fi

"$PY" -m PyInstaller \
  --noconfirm --clean --windowed \
  --name XhsSpider \
  --icon "assets/app.icns" \
  --add-data "xhs_utils/xhs_pc/js:xhs_utils/xhs_pc/js" \
  --add-data "xhs_utils/xhs_core/js:xhs_utils/xhs_core/js" \
  --add-data "node_modules:node_modules" \
  --collect-all curl_cffi \
  "${EXTRA[@]}" \
  main.py

# 生成 DMG 安装包
rm -f dist/XhsSpider.dmg
hdiutil create -ov -volname "XhsSpider" \
  -srcfolder dist/XhsSpider.app \
  dist/XhsSpider.dmg

echo
echo "打包完成："
echo "  dist/XhsSpider.app"
echo "  dist/XhsSpider.dmg   ← 发给客户的就是这个"
[ "${BUNDLE_NODE:-0}" != "1" ] && echo "提示：未内置 Node，目标机器需安装 Node.js 20+（推荐 BUNDLE_NODE=1）"
