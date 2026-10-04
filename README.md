# 小红书采集工具

YC 出品的小红书笔记采集桌面软件（macOS / Windows），账号授权 + 有效期管控，
采集结果一键导出小绿书压缩包，可直接上传运营后台。

> 私有软件，仅供内部及授权客户使用，禁止传播。

## 功能

- **账号授权登录**：账号密码登录，服务端管控有效期（试用/续期/停用即时生效）
- **三种采集模式**：关键词搜索（数量/排序/类型/时间筛选）｜笔记链接批量｜用户主页全量
- **小绿书导出**：每篇笔记一个文件夹（`标题/1.jpg…文案.txt`）打包 zip，直接上传后台赛道管理
- **无水印原图（可选，默认关）**：图片走 `ci.xiaohongshu.com` 原图直链，实测多为更高分辨率资产
  （如 1080×1440 → 1440×1920）；部分资产该路径不提供服务，失败自动回退默认直链
- **AI 改写**：标题/文案独立提示词、独立调用，改写结果直接进导出包（接口走平台统一网关）
- **多账号池**：扫码或直接粘贴 Cookie 添加账号，采集时轮询分摊请求
- **账号矩阵**：台账 + 健康巡检 + 粉丝/互动增长曲线（每次巡检按天记一条快照，保留 90 天）
- **防风控**：采集间隔+随机抖动、限流特征自动冷却、连续风控自动熔断
- **数据留底**：Excel 保存原始采集数据，媒体文件按需下载

## 使用

1. 安装后打开，输入账号密码登录（账号由管理员在后台开通）
2. 首次使用点「登录小红书」：扫码，或在「Cookie 导入」页粘贴浏览器里登录后的整行 Cookie
3. 选择采集模式与保存内容 → 开始采集；输出目录默认 `~/Documents/XHS采集`

## 构建

```bash
# 依赖：Python 3.10+、Node.js 20+
python3.12 -m venv .venv
.venv/bin/pip install -r desktop/requirements.txt
npm install

.venv/bin/python main.py            # 运行
.venv/bin/python -m desktop.selftest # 自检（不碰真实服务）

# macOS 出包（app + dmg）
BUNDLE_NODE=1 desktop/build_mac.sh

# Windows 出包（在本机执行，或用 GitHub Actions 云构建）
desktop/build_windows.bat
```

推送 `v*` tag 自动触发 GitHub Actions，产出 Windows 安装包（Inno Setup）与 macOS DMG 并附到 Release。

## 目录结构

```
desktop/          # 桌面端应用（PySide6）
├── app.py           # 入口：主题/Node 探测/登录流程
├── login_dialog.py  # 登录框
├── main_window.py   # 主界面（侧边导航 + 采集中心 + 设置）
├── spider_service.py# 采集编排（限速/风控/轮询）
├── xhs_export.py    # 小绿书 zip 导出
├── account_history.py # 账号历史快照 -> 增长曲线数据
├── ai_client.py     # AI 改写（OpenAI Responses 格式）
├── xhs_login_dialog.py # 小红书登录（扫码 / Cookie 导入）
└── README.md        # 详细说明
xhs_utils/        # 小红书签名与请求核心
apis/             # PC 端接口封装
spider/           # 采集基础逻辑
```

## 客户支持

- 登录提示「账号已过期」：联系管理员续期
- 采集失败/疑似限流：等待 10-30 分钟，调大采集间隔或减少数量
- 换小红书账号：侧边栏「扫码添加账号」
- 图集想拿更大分辨率：勾选「无水印原图」（默认关；失败会自动回退，不影响出图）
