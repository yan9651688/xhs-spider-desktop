#!/bin/bash
# macOS 打包脚本：产出 dist/XhsSpider.app（onedir）
# 前置：项目根目录已有 .venv 并安装 desktop/requirements.txt，且 npm install 过（node_modules/crypto-js）
# 可选：设 BUNDLE_NODE=1 并先执行 desktop/bundle_node.sh，把 Node 一起打进 .app（免用户安装 Node）
set -euo pipefail
cd "$(dirname "$0")/.."

PY=${PY:-.venv/bin/python}
[ -x "$PY" ] || { echo "缺少 .venv，请先：python3.12 -m venv .venv && .venv/bin/pip install -r desktop/requirements.txt"; exit 1; }

EXTRA=()
if [ "${BUNDLE_NODE:-0}" = "1" ] && [ -d node_dist ]; then
  EXTRA+=("--add-data" "node_dist:node_dist")
fi

"$PY" -m PyInstaller \
  --noconfirm --clean --windowed \
  --name XhsSpider \
  --add-data "xhs_utils/xhs_pc/js:xhs_utils/xhs_pc/js" \
  --add-data "xhs_utils/xhs_core/js:xhs_utils/xhs_core/js" \
  --add-data "node_modules:node_modules" \
  --collect-all curl_cffi \
  "${EXTRA[@]}" \
  main.py

echo
echo "打包完成：dist/XhsSpider.app"
[ "${BUNDLE_NODE:-0}" != "1" ] && echo "提示：未内置 Node，目标机器需安装 Node.js 20+（或 BUNDLE_NODE=1 重新打包）"
