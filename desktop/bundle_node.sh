#!/bin/bash
# 把本机 Node 运行时复制到 node_dist/，配合 BUNDLE_NODE=1 的 build_mac.sh 使用，
# 使 .app 完全自包含（无需目标机器安装 Node.js）。仅 macOS（arm64/x64 跟随本机）。
set -euo pipefail
cd "$(dirname "$0")/.."

NODE_BIN=$(command -v node) || { echo "本机未安装 node"; exit 1; }
NODE_ROOT=$(cd "$(dirname "$NODE_BIN")/.." && pwd)

rm -rf node_dist
mkdir -p node_dist/bin
cp "$NODE_BIN" node_dist/bin/node
# node 可执行文件依赖同级资源（macOS 上通常自带，独立可执行；如有 dylib 一并复制）
find "$NODE_ROOT" -maxdepth 1 -name '*.dylib' -exec cp {} node_dist/bin/ \; 2>/dev/null || true

node_dist/bin/node -v && echo "已生成 node_dist/（记得 .gitignore 已忽略它）"
