#!/bin/bash
# 把一份**自包含**的 Node 运行时放进 node_dist/，配合 BUNDLE_NODE=1 的 build_mac.sh 使用，
# 使 .app 完全自包含（目标机器无需安装 Node.js）。仅 macOS（arch 跟随本机）。
#
# 注意：不能直接拷 Homebrew 的 node。它是动态链接 /opt/homebrew/opt/* 下一堆 dylib
# （libnode、libuv、ada-url、simdjson…）的，拷过去在别的机器上必然 "Abort trap: 6"。
# CI 里那行 `cp $(which node)` 能成立，只因为 actions/setup-node 装的是官方独立二进制。
# 所以这里：本机 node 自包含就直接复用，否则下载 nodejs.org 的官方独立包。
set -euo pipefail
cd "$(dirname "$0")/.."

NODE_VERSION=${NODE_VERSION:-v20.20.2}
CACHE_DIR=${NODE_CACHE_DIR:-.node_cache}

case "$(uname -m)" in
  arm64) NODE_ARCH=arm64 ;;
  x86_64) NODE_ARCH=x64 ;;
  *) echo "不支持的架构: $(uname -m)"; exit 1 ;;
esac

rm -rf node_dist
mkdir -p node_dist/bin

copy_local_node() {
  local bin root
  bin=$(command -v node) || return 1
  root=$(cd "$(dirname "$bin")/.." && pwd)
  cp "$bin" node_dist/bin/node
  # 官方包里 node 自带全部依赖；Homebrew 那种把同级 dylib 一并带上也仍会因绝对路径失败，
  # 这里只是尽量复制，真正的判定交给调用方的可执行性检查。
  find "$root" -maxdepth 1 -name '*.dylib' -exec cp {} node_dist/bin/ \; 2>/dev/null || true
}

fetch_official_node() {
  local pkg="node-${NODE_VERSION}-darwin-${NODE_ARCH}"
  local tarball="$CACHE_DIR/${pkg}.tar.gz"
  mkdir -p "$CACHE_DIR"
  if [ ! -f "$tarball" ]; then
    echo "下载官方 Node ${NODE_VERSION} (darwin-${NODE_ARCH}) ..."
    curl -fL --retry 3 --connect-timeout 20 -o "$tarball.part" \
      "https://nodejs.org/dist/${NODE_VERSION}/${pkg}.tar.gz"
    mv "$tarball.part" "$tarball"
  fi
  tar -xzf "$tarball" -C "$CACHE_DIR" "${pkg}/bin/node"
  # 目标可能已存在且是只读的（Homebrew 的 node 装出来是 555），必须先删再拷
  rm -f node_dist/bin/node
  cp "$CACHE_DIR/${pkg}/bin/node" node_dist/bin/node
  chmod 755 node_dist/bin/node
}

# 本机 node 能用就不下载（CI 等环境省一次网络往返）；跑不起来就回退官方包。
if copy_local_node && node_dist/bin/node -v >/dev/null 2>&1; then
  echo "复用本机 Node：$(node_dist/bin/node -v)"
else
  echo "本机 Node 不是自包含的（Homebrew 动态链接版），改用官方独立二进制"
  fetch_official_node
fi

node_dist/bin/node -v \
  || { echo "!! node_dist/bin/node 无法运行，打包中止"; exit 1; }
echo "已生成 node_dist/（已 gitignore）"
