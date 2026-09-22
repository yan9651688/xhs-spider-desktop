# 小红书采集工具（桌面端）

基于 [Spider_XHS](https://github.com/cv-cat/Spider_XHS) 的 PC 端采集核心（签名/接口全保留），
加了一层**账号登录 + 有效期控制**的桌面软件。登录体系复用 [xiao](https://gitee.com/ybmayun/xiao)
后端（`xhs-auth` 分支新增的 `/api/xhs/*` 接口，与“去AI桌面端”同一套 `sys_user` 账号体系）。

> 仅供学习交流使用，禁止商业化，采集行为请遵守平台规则。

## 功能

- **软件账号门**：启动需登录 xiao 账号；账号带 `xhs_expire_time` 有效期，到期即无法登录/使用
  （每 10 分钟后台复检一次，管理员改库可实时踢下线）
- **小红书扫码登录**：本地生成二维码，App 扫码确认；Cookie 持久化在 `~/.xhs_spider/xhs_cookie.txt`，
  下次启动自动恢复，失效自动要求重扫
- **三种采集模式**：关键词搜索（数量/排序/类型/时间筛选）、笔记链接批量、用户主页全量
- **保存**：图片 / 视频 / Excel（复用原项目 `download_note` / `save_to_xlsx`），输出到自选目录
- **界面**：PySide6；结果表格（双击打开笔记）、实时日志、进度条

## 开发运行

```bash
# Python 3.10+（推荐 3.12）与 Node.js 20+
python3.12 -m venv .venv
.venv/bin/pip install -r desktop/requirements.txt
npm install                # crypto-js，签名 JS 依赖

.venv/bin/python main.py   # 启动桌面端
.venv/bin/python -m desktop.selftest   # 自检（桩服务器+无头UI，不碰真实服务）
```

登录框里填 xiao 部署地址（如 `http://your-server.com`）。

## 打包（macOS）

```bash
desktop/build_mac.sh              # 产出 dist/XhsSpider.app
# 或自带 Node（免目标机安装）：
desktop/bundle_node.sh && BUNDLE_NODE=1 desktop/build_mac.sh
```

未内置 Node 时，目标机器需安装 Node.js 20+（签名算法通过 `node` 子进程执行；
从 Finder 启动的 .app 会自动补 `/opt/homebrew/bin`、`/usr/local/bin` 到 PATH）。

## xiao 后端（服务端）

对应 xiao 仓库 `xhs-auth` 分支（纯新增，不动既有逻辑）：

- `POST /api/xhs/login` `{username, password}` → `{code:0, token, xhsExpireTime}`
  token 7 天有效（内存存储，服务重启需重新登录）
- `GET /api/xhs/check`（header `X-Xhs-Token`）→ 有效期内 `{code:0, xhsExpireTime}`
  过期返回 403，token 失效返回 401
- `POST /api/xhs/logout`
- `sys_user.xhs_expire_time`（datetime）：**NULL=永久**，早于当前时间=到期；
  服务启动时 `XhsSchemaInitializer` 自动补列，手工 SQL 见 `sql/xhs_expire_time.sql`

账号管理（示例）：

```sql
-- 开通到 2026-12-31
UPDATE sys_user SET xhs_expire_time='2026-12-31 23:59:59' WHERE username='xxx';
-- 续期 30 天
UPDATE sys_user SET xhs_expire_time=DATE_ADD(IFNULL(xhs_expire_time,NOW()), INTERVAL 30 DAY) WHERE username='xxx';
-- 永久 / 立即停用
UPDATE sys_user SET xhs_expire_time=NULL WHERE username='xxx';
UPDATE sys_user SET xhs_expire_time=NOW() WHERE username='xxx';
```

## 本地文件

`~/.xhs_spider/`：`config.json`（服务器/用户名/输出目录）、`session.json`（登录 token）、
`xhs_cookie.txt`（小红书 Cookie，注意保密）。

## 目录结构

```
desktop/
├── app.py               # 入口：Node 运行时探测 + 登录流程
├── login_dialog.py      # 软件账号登录（xiao /api/xhs/login）
├── main_window.py       # 主窗口 + 采集工作线程 + 会话复检
├── xhs_login_dialog.py  # 小红书扫码登录（二维码显示在窗口）
├── spider_service.py    # 采集编排（URL收集→逐条抓取→保存）
├── auth_client.py       # xiao 接口客户端
├── paths.py             # ~/.xhs_spider 本地存储
├── selftest.py          # 自检（桩服务器 + 无头 UI）
├── build_mac.sh         # PyInstaller 打包
└── bundle_node.sh       # 可选：内置 Node
```
