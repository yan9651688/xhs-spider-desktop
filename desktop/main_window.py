# encoding: utf-8
"""主窗口：Redwhale 风格（左侧导航 + 卡片式主区）。

侧边栏：采集中心 / 账号矩阵 / 对标监控 / 设置 + 小红书账号区 + 底部用户卡。
采集中心：问候头部、渐变统计卡、六种采集页签、保存选项、进度、结果表 + 日志。
"""
from __future__ import annotations

import os
import threading
import time

from loguru import logger
from PySide6.QtCore import QDate, QDateTime, QMargins, Qt, QThread, QTime, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QIcon, QPainter, QPixmap, QPen
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from desktop import paths, watchlist
from desktop.account_history import METRIC_LABELS, account_options, build_series, growth_summary
from desktop.auth_client import AuthClient, AuthError
from desktop.spider_service import (
    TaskSpec,
    run_collection,
    run_comment_collection,
    run_user_search,
)
from desktop.watch_add_dialog import WatchAddDialog
from desktop.xhs_login_dialog import XhsLoginDialog

# QtCharts 用于账号矩阵的粉丝增长曲线；缺失时该卡片整体隐藏，不影响其它功能
try:
    from PySide6.QtCharts import (
        QChart,
        QChartView,
        QDateTimeAxis,
        QLineSeries,
        QValueAxis,
    )
    HAS_QTCHARTS = True
except Exception:  # pragma: no cover - 仅在裁剪过的 PySide6 上触发
    HAS_QTCHARTS = False

SORT_OPTIONS = [('综合排序', 0), ('最新', 1), ('最多点赞', 2), ('最多评论', 3), ('最多收藏', 4)]
TYPE_OPTIONS = [('不限', 0), ('视频笔记', 1), ('图文笔记', 2)]
TIME_OPTIONS = [('不限', 0), ('一天内', 1), ('一周内', 2), ('半年内', 3)]

TABLE_COLUMNS = ['标题', '类型', '作者', '点赞', '收藏', '评论', '发布时间', '链接']

NAV_HOME, NAV_MATRIX, NAV_WATCH, NAV_SETTINGS = 0, 1, 2, 3

# 账号矩阵
ACCOUNT_COLUMNS = ['账号', '小红书号', '状态', '粉丝', '关注', '获赞与收藏',
                   '作品', 'IP属地', '最近巡检']
PROBE_INTERVAL_MS = 6 * 60 * 60 * 1000        # 自动巡检间隔：6 小时
PROBE_ACCOUNT_GAP_SECONDS = 0.4               # 账号之间的间隔，降风控
RISK_COOLDOWN_SECONDS = 60                    # 某号命中限流后的加长冷却

# 对标监控
WATCH_TREE_COLUMNS = ['对标账号', '粉丝', '作品', 'IP属地', '状态', '最近检查', '待采集']
WATCH_GAP_SECONDS = 0.6                       # 对标号之间的间隔，降风控

_HEALTH_PLACEHOLDER = '—'


def _watch_home_url(user_id: str) -> str:
    return f'https://www.xiaohongshu.com/user/profile/{user_id}'


def _watch_note_url(note: dict) -> str:
    """登记的笔记 -> 可采集的完整 URL（与 spider_service.collect_note_urls 同款拼法）。"""
    note = note or {}
    note_id = str(note.get('note_id') or '')
    if not note_id:
        return ''
    token = str(note.get('xsec_token') or '')
    url = f'https://www.xiaohongshu.com/explore/{note_id}'
    if token:
        url += f'?xsec_token={token}&xsec_source=pc_user'
    return url


def line_icon(kind: str, color: str = '#8a8f98', size: int = 18) -> QIcon:
    """细线风格导航图标（贴近参考图的线性图标）。"""
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QColor

    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    try:
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(QColor(color), 1.6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        painter.setBrush(Qt.NoBrush)
        s = size
        m = s * 0.18
        if kind == 'home':
            painter.drawPolyline([
                QPointF(m, s * 0.48), QPointF(s / 2, m), QPointF(s - m, s * 0.48),
            ])
            painter.drawRect(int(s * 0.28), int(s * 0.48), int(s * 0.44), int(s - m - s * 0.48))
        elif kind == 'grid':
            cell = (s - 2 * m - s * 0.08) / 2
            for row in range(2):
                for col in range(2):
                    painter.drawRoundedRect(
                        int(m + col * (cell + s * 0.08)), int(m + row * (cell + s * 0.08)),
                        int(cell), int(cell), 2, 2,
                    )
        elif kind == 'gear':
            import math
            painter.drawEllipse(QPointF(s / 2, s / 2), s * 0.2, s * 0.2)
            for i in range(6):
                angle = math.pi / 3 * i
                painter.drawLine(
                    QPointF(s / 2 + math.cos(angle) * s * 0.3, s / 2 + math.sin(angle) * s * 0.3),
                    QPointF(s / 2 + math.cos(angle) * s * 0.4, s / 2 + math.sin(angle) * s * 0.4),
                )
        elif kind == 'plus':
            painter.drawLine(QPointF(s / 2, m), QPointF(s / 2, s - m))
            painter.drawLine(QPointF(m, s / 2), QPointF(s - m, s / 2))
        elif kind == 'user':
            painter.drawEllipse(QPointF(s / 2, s * 0.36), s * 0.16, s * 0.16)
            painter.drawArc(int(s * 0.2), int(s * 0.5), int(s * 0.6), int(s * 0.44), 0, 180 * 16)
        elif kind == 'radar':
            import math
            center = QPointF(s / 2, s / 2)
            painter.drawEllipse(center, s * 0.4, s * 0.4)
            painter.drawEllipse(center, s * 0.22, s * 0.22)
            painter.drawLine(center, QPointF(
                s / 2 + math.cos(-math.pi / 4) * s * 0.4,
                s / 2 + math.sin(-math.pi / 4) * s * 0.4,
            ))
            painter.drawPoint(center)
        elif kind == 'folder':
            painter.drawRoundedRect(int(m), int(s * 0.3), int(s - 2 * m), int(s * 0.44), 3, 3)
            painter.drawPolyline([
                QPointF(m, s * 0.3), QPointF(m, s * 0.22),
                QPointF(s * 0.42, s * 0.22), QPointF(s * 0.48, s * 0.3),
            ])
    finally:
        painter.end()
    return QIcon(pixmap)


def gradient_tile(text: str, colors: list, size: int = 34, radius: int = 10,
                  font_size: int = 16) -> QPixmap:
    """渐变圆角方块贴图（logo / 头像）。"""
    from PySide6.QtGui import QColor, QFont, QLinearGradient

    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    try:
        painter.setRenderHint(QPainter.Antialiasing)
        gradient = QLinearGradient(0, 0, size, size)
        for index, color in enumerate(colors):
            gradient.setColorAt(index / (len(colors) - 1), QColor(color))
        painter.setPen(Qt.NoPen)
        painter.setBrush(gradient)
        painter.drawRoundedRect(0, 0, size, size, radius, radius)
        painter.setPen(Qt.white)
        font = QFont()
        font.setBold(True)
        font.setPixelSize(font_size)
        painter.setFont(font)
        painter.drawText(pixmap.rect(), Qt.AlignCenter, text)
    finally:
        painter.end()
    return pixmap


class _LogBridge(QThread):
    """loguru sink -> Qt 信号（工作线程安全转发）。"""
    message = Signal(str)

    def emit_message(self, message):
        self.message.emit(str(message).strip())


class _RestoreAuthWorker(QThread):
    ok = Signal(object, str, str)     # auth, nickname, cookie
    failed = Signal(str)

    def __init__(self, cookies: list, parent=None):
        super().__init__(parent)
        self.cookies = list(cookies)

    def run(self):
        from apis.xhs_pc_apis import XHS_Apis
        from xhs_utils.xhs_pc import XHSPcAuth

        last_error = ''
        for index, item in enumerate(self.cookies):
            cookie = (item or {}).get('cookie', '')
            if not cookie:
                continue
            try:
                auth = XHSPcAuth.from_cookie(cookie)
                nickname = ''
                try:
                    success, _msg, res = XHS_Apis(auth).get_user_me()
                    if success and res:
                        nickname = (res.get('data') or {}).get('nickname') or ''
                except Exception:
                    pass
                self.ok.emit(auth, nickname, cookie)
                return
            except Exception as exc:
                last_error = str(exc)
                logger.warning(f'小红书账号 {index + 1} 会话无效，尝试下一个：{exc}')
        logger.error(f'恢复小红书会话失败：{last_error or "账号池为空"}')
        self.failed.emit(last_error or '账号池为空')


class _CollectWorker(QThread):
    note = Signal(object)
    progress = Signal(int, int, str)
    logline = Signal(str)
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, cookies: list, spec: TaskSpec, parent=None):
        super().__init__(parent)
        self.cookies = cookies
        self.spec = spec
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        emit = self._Emit(self)
        try:
            if self.spec.mode == 'comments':
                summary = run_comment_collection(
                    self.cookies, self.spec, lambda: self._stop, emit)
            elif self.spec.mode == 'usersearch':
                summary = run_user_search(
                    self.cookies, self.spec, lambda: self._stop, emit)
            else:
                summary = run_collection(self.cookies, self.spec,
                                         lambda: self._stop, emit)
            self.done.emit(summary)
        except Exception as exc:
            logger.exception('采集任务异常')
            self.failed.emit(str(exc))

    class _Emit:
        def __init__(self, owner):
            self._owner = owner

        def log(self, text):
            self._owner.logline.emit(str(text))

        def note(self, note_info):
            self._owner.note.emit(note_info)

        def progress(self, done, total, stage):
            self._owner.progress.emit(done, total, stage)


class _CheckWorker(QThread):
    ok = Signal(str)
    failed = Signal(str)

    def __init__(self, server: str, token: str, parent=None):
        super().__init__(parent)
        self.server = server
        self.token = token

    def run(self):
        try:
            data = AuthClient(self.server).check(self.token)
            self.ok.emit(data.get('xhsExpireTime') or '永久')
        except AuthError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:
            self.failed.emit(str(exc))


class _AiTestWorker(QThread):
    ok = Signal(str)
    failed = Signal(str)

    def __init__(self, client, parent=None):
        super().__init__(parent)
        self.client = client

    def run(self):
        try:
            self.ok.emit(self.client.test())
        except Exception as exc:
            self.failed.emit(str(exc))


class _AccountProbeWorker(QThread):
    """逐账号巡检。

    items 是 [{'key','cookie'}] 的**冻结快照**：巡检中用户在侧边栏删账号
    不会让 key 漂移（Cookie 池的删除是按数组下标的，绝不能在下标上做文章）。
    """
    one = Signal(str, object)         # key, 巡检结果
    progress = Signal(int, int)       # done, total
    finished_all = Signal(int, int)   # 正常账号数, 总数

    def __init__(self, items: list, full: bool = True, parent=None):
        super().__init__(parent)
        self.items = [dict(item) for item in items]
        self.full = full
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        from desktop import paths
        from desktop.account_probe import probe_account

        ok_count = 0
        for index, item in enumerate(self.items, start=1):
            if self._stop:
                break
            result = probe_account(item.get('cookie') or '', full=self.full)
            paths.upsert_account(item.get('key') or '', **result)
            if result.get('health') == paths.HEALTH_OK:
                ok_count += 1
            self.one.emit(item.get('key') or '', result)
            self.progress.emit(index, len(self.items))
            if index < len(self.items):
                time.sleep(RISK_COOLDOWN_SECONDS
                           if result.get('health') == paths.HEALTH_LIMITED
                           else PROBE_ACCOUNT_GAP_SECONDS)
        self.finished_all.emit(ok_count, len(self.items))


class _WatchProbeWorker(QThread):
    """逐个检查对标账号有没有发新笔记。

    items 是 [{'user_id','target'}] 的**冻结快照**（对齐巡检的防下标漂移思路）。
    cookie 是本机一个可用账号的快照：检查中途客户换了账号也不影响本轮。
    """
    one = Signal(str, object)         # user_id, 检查结果
    progress = Signal(int, int)       # done, total
    finished_all = Signal(int, int)   # 正常数, 总数

    def __init__(self, items: list, cookie: str, parent=None):
        super().__init__(parent)
        self.items = [dict(item) for item in items]
        self.cookie = str(cookie or '')
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        from desktop import watch_probe

        # 会话建立一次、所有对标号共用；账号池全失效时不弹窗打断自动巡检，只记日志
        auth = api = None
        ok_count = 0
        try:
            if self.cookie:
                try:
                    auth, api = watch_probe.build_api(self.cookie)
                except Exception as exc:
                    logger.warning(f'对标检查建立会话失败：{str(exc)[:120]}')
                    api = None
            for index, item in enumerate(self.items, start=1):
                if self._stop:
                    break
                user_id = item.get('user_id') or ''
                if api is None:
                    result = {'health': paths.HEALTH_NETWORK,
                              'health_note': '没有可用的小红书账号，请先扫码登录',
                              'last_checked_at': time.strftime('%Y-%m-%d %H:%M:%S'),
                              'new_notes': [], 'pending': 0, 'scanned_ids': []}
                else:
                    try:
                        result = watch_probe.probe_target(
                            api, user_id, item.get('target') or {})
                    except Exception as exc:
                        logger.exception('对标号检查异常')
                        result = {'health': paths.HEALTH_NETWORK,
                                  'health_note': str(exc)[:200],
                                  'last_checked_at': time.strftime('%Y-%m-%d %H:%M:%S'),
                                  'new_notes': [], 'pending': 0, 'scanned_ids': []}
                try:
                    watch_probe.apply_result(user_id, result)
                except Exception as exc:
                    logger.warning(f'对标检查结果落盘失败（{user_id}）：{str(exc)[:120]}')
                if result.get('health') == paths.HEALTH_OK:
                    ok_count += 1
                self.one.emit(user_id, result)
                self.progress.emit(index, len(self.items))
                if index < len(self.items):
                    time.sleep(RISK_COOLDOWN_SECONDS
                               if result.get('health') == paths.HEALTH_LIMITED
                               else WATCH_GAP_SECONDS)
        finally:
            if auth is not None:
                closer = getattr(auth, 'close', None)
                if callable(closer):
                    try:
                        closer()
                    except Exception:
                        pass
        self.finished_all.emit(ok_count, len(self.items))


class MainWindow(QMainWindow):
    def __init__(self, config: dict, session: dict):
        super().__init__()
        self.config = config
        self.session = session
        self.auth = None
        self.xhs_nickname = ''
        self.collect_worker = None
        self.restore_worker = None
        self._probe_worker = None
        self._probe_running = False
        self._watch_worker = None
        self._watch_running = False
        self._pending_watch = False
        self._watch_filling = False
        self._watch_collect_notes = []
        self._log_sink_id = None

        self.setWindowTitle('小红书采集工具')
        self.resize(1120, 760)
        self.setMinimumSize(1000, 680)
        self._build_ui()
        # 首帧就把台账与对标清单读出来（不写文件），避免切页看到空白
        self.refresh_matrix_page(sync=False)
        self.refresh_watch_page(sync=False)

        self._log_bridge = _LogBridge(self)
        self._log_bridge.message.connect(self.append_log)
        self._log_sink_id = logger.add(
            self._log_bridge.emit_message,
            level='INFO',
            format='{time:HH:mm:ss} | {level: <7} | {message}',
        )

        self._check_timer = QTimer(self)
        self._check_timer.setInterval(10 * 60 * 1000)
        self._check_timer.timeout.connect(self.run_session_check)
        self._check_timer.start()
        QTimer.singleShot(1500, self.run_session_check)

        # 账号巡检：启动后延迟一轮快速体检（错开会话恢复），之后按设置定时
        self._probe_timer = QTimer(self)
        self._probe_timer.setInterval(PROBE_INTERVAL_MS)
        self._probe_timer.timeout.connect(self.run_scheduled_probe)
        if self.config.get('probe_auto', True):
            self._probe_timer.start()
        QTimer.singleShot(4000, self.run_startup_probe)

        self.restore_xhs_session()

    # ---------- UI 骨架 ----------

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_sidebar())
        root.addWidget(self._build_pages(), 1)

    def _build_sidebar(self) -> QWidget:
        side = QWidget()
        side.setObjectName('sidebar')
        side.setFixedWidth(216)
        layout = QVBoxLayout(side)
        layout.setContentsMargins(16, 18, 16, 14)
        layout.setSpacing(4)

        # Logo
        logo_row = QHBoxLayout()
        logo_tile = QLabel()
        logo_tile.setPixmap(gradient_tile('YC', ['#ff8a5c', '#6c5ce7', '#4ec9d4'],
                                          font_size=13))
        logo_tile.setFixedSize(34, 34)
        logo_tile.setAlignment(Qt.AlignCenter)
        logo_text_box = QVBoxLayout()
        logo_text_box.setSpacing(0)
        logo_name = QLabel('小红书采集')
        logo_name.setObjectName('logoName')
        logo_sub = QLabel('Data & Growth')
        logo_sub.setObjectName('logoSub')
        logo_text_box.addWidget(logo_name)
        logo_text_box.addWidget(logo_sub)
        logo_row.addWidget(logo_tile)
        logo_row.addSpacing(8)
        logo_row.addLayout(logo_text_box)
        logo_row.addStretch(1)
        layout.addLayout(logo_row)
        layout.addSpacing(18)

        # 主导航
        self.nav_buttons = {}

        def add_nav(key, text, icon, badge=None):
            btn = QPushButton(text)
            btn.setObjectName('navItem')
            btn.setIcon(line_icon(icon))
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _=False, k=key: self.switch_page(k))
            self.nav_buttons[key] = btn
            if badge:
                row = QHBoxLayout()
                row.setContentsMargins(0, 0, 0, 0)
                row.setSpacing(6)
                row.addWidget(btn, 1)
                badge_label = QLabel(badge)
                badge_label.setObjectName('soonBadge')
                badge_label.setAlignment(Qt.AlignCenter)
                badge_label.setFixedWidth(40)
                row.addWidget(badge_label)
                holder = QWidget()
                holder.setLayout(row)
                layout.addWidget(holder)
            else:
                layout.addWidget(btn)

        add_nav(NAV_HOME, '采集中心', 'home')
        add_nav(NAV_MATRIX, '账号矩阵', 'grid')
        add_nav(NAV_WATCH, '对标监控', 'radar')
        add_nav(NAV_SETTINGS, '设置', 'gear')

        layout.addSpacing(14)
        section = QLabel('小红书账号池')
        section.setObjectName('navSection')
        layout.addWidget(section)

        add_row = QHBoxLayout()
        add_btn = QPushButton('扫码添加账号')
        add_btn.setObjectName('navSubItem')
        add_btn.setIcon(line_icon('plus', '#6c5ce7'))
        add_btn.setCursor(Qt.PointingHandCursor)
        add_btn.clicked.connect(self.open_xhs_login)
        add_row.addWidget(add_btn, 1)
        layout.addLayout(add_row)

        self.xhs_accounts_box = QVBoxLayout()
        self.xhs_accounts_box.setContentsMargins(0, 2, 0, 2)
        self.xhs_accounts_box.setSpacing(2)
        layout.addLayout(self.xhs_accounts_box)
        self.render_xhs_accounts()

        layout.addStretch(1)

        # 底部用户卡
        user_card = QFrame()
        user_card.setObjectName('userCard')
        user_layout = QHBoxLayout(user_card)
        user_layout.setContentsMargins(10, 8, 8, 8)
        avatar = QLabel()
        avatar.setPixmap(gradient_tile(
            (self.session.get('name') or self.session.get('username') or '用')[:1],
            ['#7d6ef0', '#5a4bd0'], size=30, radius=15, font_size=13,
        ))
        avatar.setFixedSize(30, 30)
        avatar.setAlignment(Qt.AlignCenter)
        info = QVBoxLayout()
        info.setSpacing(0)
        self.account_name_label = QLabel(self.session.get('name') or self.session.get('username') or '')
        self.account_name_label.setObjectName('userName')
        self.account_label = QLabel(f"有效期：{self.session.get('xhsExpireTime') or '永久'}")
        self.account_label.setObjectName('userMeta')
        info.addWidget(self.account_name_label)
        info.addWidget(self.account_label)
        logout_btn = QPushButton('退出')
        logout_btn.setObjectName('ghostBtn')
        logout_btn.setCursor(Qt.PointingHandCursor)
        logout_btn.clicked.connect(self.logout)
        user_layout.addWidget(avatar)
        user_layout.addSpacing(8)
        user_layout.addLayout(info, 1)
        user_layout.addWidget(logout_btn)
        layout.addWidget(user_card)

        self.nav_buttons[NAV_HOME].setChecked(True)
        return side

    def _build_pages(self) -> QWidget:
        self.pages = QStackedWidget()
        self.pages.setObjectName('pages')
        self.pages.addWidget(self._build_home_page())       # NAV_HOME
        self.pages.addWidget(self._build_matrix_page())     # NAV_MATRIX
        self.pages.addWidget(self._build_watch_page())      # NAV_WATCH
        self.pages.addWidget(self._build_settings_page())   # NAV_SETTINGS
        return self.pages

    # ---------- 采集中心页 ----------

    def _build_home_page(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(22, 18, 22, 16)
        outer.setSpacing(12)

        # 问候头部
        header = QHBoxLayout()
        greet_box = QVBoxLayout()
        greet_box.setSpacing(1)
        greet = QLabel(f"Hi，{self.session.get('name') or self.session.get('username') or '朋友'}")
        greet.setObjectName('greetTitle')
        greet_sub = QLabel('今天想采集点什么？')
        greet_sub.setObjectName('greetSub')
        greet_box.addWidget(greet)
        greet_box.addWidget(greet_sub)
        header.addLayout(greet_box)
        header.addStretch(1)
        self.xhs_label = QLabel('小红书：未登录')
        self.xhs_label.setObjectName('xhsState')
        header.addWidget(self.xhs_label)
        self.xhs_login_btn = QPushButton('扫码登录小红书')
        self.xhs_login_btn.setObjectName('primaryBtn')
        self.xhs_login_btn.setIcon(line_icon('user', '#ffffff'))
        self.xhs_login_btn.setCursor(Qt.PointingHandCursor)
        self.xhs_login_btn.clicked.connect(self.open_xhs_login)
        header.addWidget(self.xhs_login_btn)
        open_dir_btn = QPushButton('输出目录')
        open_dir_btn.setObjectName('softBtn')
        open_dir_btn.setIcon(line_icon('folder'))
        open_dir_btn.setCursor(Qt.PointingHandCursor)
        open_dir_btn.clicked.connect(self.open_output_dir)
        header.addWidget(open_dir_btn)
        outer.addLayout(header)

        # 渐变统计卡
        cards = QHBoxLayout()
        cards.setSpacing(12)
        self.stat_expire = self._stat_card(cards, 'statCard1', '账号有效期', self.session.get('xhsExpireTime') or '永久')
        self.stat_count = self._stat_card(cards, 'statCard2', '本次已采集', '0 篇')
        self.stat_xhs = self._stat_card(cards, 'statCard3', '小红书账号', '未登录')
        outer.addLayout(cards)

        # 采集页签卡
        collect_card = QFrame()
        collect_card.setObjectName('card')
        card_layout = QVBoxLayout(collect_card)
        card_layout.setContentsMargins(6, 6, 6, 10)
        self.tabs = QTabWidget()
        self.tabs.setObjectName('collectTabs')
        self.tabs.addTab(self._build_search_tab(), '搜索采集')
        self.tabs.addTab(self._build_urls_tab(), '链接采集')
        self.tabs.addTab(self._build_user_tab(), '主页采集')
        self.tabs.addTab(self._build_comments_tab(), '评论采集')
        self.tabs.addTab(self._build_collect_tab(), '收藏采集')
        self.tabs.addTab(self._build_usersearch_tab(), '用户搜索')
        self.tabs.currentChanged.connect(self._sync_option_visibility)
        card_layout.addWidget(self.tabs)
        outer.addWidget(collect_card, 1)

        # 保存选项卡（两行网格，避免拥挤）：第一行输出目录，第二行任务名+保存内容
        options_card = QFrame()
        options_card.setObjectName('card')
        grid = QGridLayout(options_card)
        grid.setContentsMargins(14, 10, 14, 10)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(8)

        out_label = QLabel('输出目录')
        out_label.setObjectName('mutedLabel')
        self.output_edit = QLineEdit(self.config.get('output_dir') or str(paths.DEFAULT_OUTPUT_DIR))
        self.output_edit.setMinimumWidth(180)
        browse_btn = QPushButton('浏览')
        browse_btn.setObjectName('softBtn')
        browse_btn.setFixedWidth(76)
        browse_btn.clicked.connect(self.browse_output_dir)
        open_btn = QPushButton('打开')
        open_btn.setObjectName('softBtn')
        open_btn.setFixedWidth(76)
        open_btn.clicked.connect(self.open_output_dir)
        grid.addWidget(out_label, 0, 0)
        # 编辑结束即落盘（与设置页同一份配置；两处输入框在设置页构建时双向同步）
        self.output_edit.editingFinished.connect(self._persist_output_dir)
        grid.addWidget(self.output_edit, 0, 1)
        grid.addWidget(browse_btn, 0, 2)
        grid.addWidget(open_btn, 0, 3)

        task_label = QLabel('任务名')
        task_label.setObjectName('mutedLabel')
        self.task_edit = QLineEdit()
        self.task_edit.setPlaceholderText('可留空，默认关键词/用户ID')
        grid.addWidget(task_label, 1, 0)
        grid.addWidget(self.task_edit, 1, 1, 1, 3)

        content_label = QLabel('保存内容')
        content_label.setObjectName('mutedLabel')
        checks_row = QHBoxLayout()
        checks_row.setSpacing(18)
        self.img_check = QCheckBox('图片')
        self.img_check.setChecked(True)
        self.video_check = QCheckBox('视频')
        self.video_check.setChecked(True)
        self.excel_check = QCheckBox('Excel')
        self.excel_check.setChecked(True)
        self.zip_check = QCheckBox('打包')
        self.zip_check.setChecked(True)
        self.zip_check.setToolTip('导出小绿书压缩包（图片+文案.txt），可直接上传 xiao 赛道管理')
        self.ai_check = QCheckBox('AI改写')
        self.ai_check.setToolTip('抓取后调用 AI 改写标题与文案（在设置页配置接口）')
        self.no_water_check = QCheckBox('无水印原图')
        self.no_water_check.setToolTip(
            '图片改走 ci.xiaohongshu.com 原图直链：实测分辨率更高（如 1080×1440 → 3072×4096）。\n'
            '默认关闭：属于未公开接口，可能个别图片取不到，失败时会记日志。'
        )
        for w in (self.img_check, self.video_check, self.excel_check,
                  self.zip_check, self.ai_check, self.no_water_check):
            checks_row.addWidget(w)
        checks_row.addStretch(1)
        # 评论采集没有媒体/无打包意义，也不走图文 AI 改写：切页签时隐藏这些选项
        self.media_option_widgets = [self.img_check, self.video_check,
                                     self.zip_check, self.ai_check,
                                     self.no_water_check]
        self.content_label = content_label
        grid.addWidget(content_label, 2, 0)
        grid.addLayout(checks_row, 2, 1, 1, 3)
        grid.setColumnStretch(1, 3)
        grid.setColumnStretch(3, 2)
        outer.addWidget(options_card)

        # 运行行（整行）：开始采集 + 进度 + 限速 + 阶段
        run_card = QFrame()
        run_card.setObjectName('card')
        run_layout = QHBoxLayout(run_card)
        run_layout.setContentsMargins(14, 10, 14, 10)
        run_layout.setSpacing(8)
        self.run_btn = QPushButton('开始采集')
        self.run_btn.setObjectName('primaryBtn')
        self.run_btn.setMinimumHeight(34)
        self.run_btn.setMinimumWidth(104)
        self.run_btn.setCursor(Qt.PointingHandCursor)
        self.run_btn.clicked.connect(self.toggle_run)
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        self.stage_label = QLabel('')
        self.stage_label.setObjectName('mutedLabel')
        delay_label = QLabel('采集间隔')
        delay_label.setObjectName('mutedLabel')
        self.delay_spin = QSpinBox()
        self.delay_spin.setRange(0, 60)
        self.delay_spin.setValue(2)
        self.delay_spin.setSuffix(' 秒')
        self.delay_spin.setToolTip(
            '每篇笔记之间的等待时间，防风控限流。\n0 = 不限速（不推荐，容易触发小红书风控）'
        )
        run_layout.addWidget(self.run_btn)
        run_layout.addSpacing(6)
        run_layout.addWidget(self.progress_bar, 1)
        run_layout.addWidget(delay_label)
        run_layout.addWidget(self.delay_spin)
        run_layout.addWidget(self.stage_label)
        outer.addWidget(run_card)

        # 结果表 + 日志
        bottom = QHBoxLayout()
        bottom.setSpacing(12)
        self.table = QTableWidget(0, len(TABLE_COLUMNS))
        self.table.setObjectName('resultTable')
        self.table.setHorizontalHeaderLabels(TABLE_COLUMNS)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(34)
        self.table.doubleClicked.connect(self.open_current_note)
        bottom.addWidget(self.table, 1)

        log_card = QFrame()
        log_card.setObjectName('card')
        log_card.setFixedWidth(264)
        log_layout = QVBoxLayout(log_card)
        log_layout.setContentsMargins(12, 10, 12, 10)
        log_title = QLabel('运行日志')
        log_title.setObjectName('cardTitle')
        self.log_view = QPlainTextEdit()
        self.log_view.setObjectName('logView')
        self.log_view.setReadOnly(True)
        self.log_view.setPlaceholderText('任务日志将显示在这里')
        log_layout.addWidget(log_title)
        log_layout.addWidget(self.log_view, 1)
        bottom.addWidget(log_card)
        outer.addLayout(bottom, 1)
        self._sync_option_visibility()
        return page

    def _sync_option_visibility(self, index: int = None):
        """评论/用户搜索页只保留「Excel」选项；其余页签显示全部（默认行为不变）。"""
        if index is None:
            index = self.tabs.currentIndex()
        # 页签顺序：0 搜索 1 链接 2 主页 3 评论 4 收藏 5 用户搜索
        excel_only = index in (3, 5)
        for widget in getattr(self, 'media_option_widgets', []):
            widget.setVisible(not excel_only)
        if hasattr(self, 'content_label'):
            self.content_label.setText('保存内容' if not excel_only else '保存内容（仅支持 Excel）')

    def _stat_card(self, parent_layout, object_name: str, label: str, value: str) -> QLabel:
        card = QFrame()
        card.setObjectName(object_name)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(3)
        cap = QLabel(label)
        cap.setObjectName('statLabel')
        val = QLabel(value)
        val.setObjectName('statValue')
        layout.addWidget(cap)
        layout.addWidget(val)
        parent_layout.addWidget(card, 1)
        return val

    def _build_search_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(16, 0, 16, 0)
        layout.addStretch(1)

        row = QHBoxLayout()
        row.setSpacing(8)
        kw_label = QLabel('关键词')
        self.query_edit = QLineEdit()
        self.query_edit.setPlaceholderText('搜索关键词，例如：黄金首饰')
        num_label = QLabel('数量')
        self.num_spin = QSpinBox()
        self.num_spin.setRange(1, 500)
        self.num_spin.setValue(20)
        self.num_spin.setFixedWidth(76)
        sort_label = QLabel('排序')
        self.sort_combo = QComboBox()
        for text, value in SORT_OPTIONS:
            self.sort_combo.addItem(text, value)
        type_label = QLabel('类型')
        self.type_combo = QComboBox()
        for text, value in TYPE_OPTIONS:
            self.type_combo.addItem(text, value)
        time_label = QLabel('时间')
        self.time_combo = QComboBox()
        for text, value in TIME_OPTIONS:
            self.time_combo.addItem(text, value)
        row.addWidget(kw_label)
        row.addWidget(self.query_edit, 1)
        row.addWidget(num_label)
        row.addWidget(self.num_spin)
        row.addWidget(sort_label)
        row.addWidget(self.sort_combo)
        row.addWidget(type_label)
        row.addWidget(self.type_combo)
        row.addWidget(time_label)
        row.addWidget(self.time_combo)
        layout.addLayout(row)
        layout.addStretch(1)
        return tab

    def _build_urls_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(12, 14, 12, 10)
        self.urls_edit = QPlainTextEdit()
        self.urls_edit.setPlaceholderText(
            '每行一个笔记链接，例如：\n'
            'https://www.xiaohongshu.com/explore/xxxxxxxx?xsec_token=...&xsec_source=pc_feed'
        )
        layout.addWidget(self.urls_edit)
        return tab

    def _build_user_tab(self) -> QWidget:
        tab = QWidget()
        form = QFormLayout(tab)
        form.setContentsMargins(12, 14, 12, 10)
        form.setSpacing(10)
        self.user_edit = QLineEdit()
        self.user_edit.setPlaceholderText(
            '用户主页链接，例如：https://www.xiaohongshu.com/user/profile/xxxx?xsec_token=...'
        )
        form.addRow('主页', self.user_edit)
        hint = QLabel('将采集该用户公开可见的全部笔记。')
        hint.setObjectName('mutedLabel')
        form.addRow('', hint)
        return tab

    def _build_comments_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(12, 14, 12, 10)
        layout.setSpacing(8)
        self.comments_edit = QPlainTextEdit()
        self.comments_edit.setPlaceholderText(
            '每行一个笔记链接，例如：\n'
            'https://www.xiaohongshu.com/explore/xxxxxxxx?xsec_token=...&xsec_source=pc_search'
        )
        layout.addWidget(self.comments_edit)
        hint = QLabel('采集每篇笔记的一级评论，结果导出为 Excel（评论内容/评论者/点赞/IP 属地/时间）。')
        hint.setObjectName('mutedLabel')
        layout.addWidget(hint)
        return tab

    def _build_collect_tab(self) -> QWidget:
        tab = QWidget()
        form = QFormLayout(tab)
        form.setContentsMargins(12, 14, 12, 10)
        form.setSpacing(10)
        self.collect_kind_combo = QComboBox()
        self.collect_kind_combo.addItem('收藏的笔记', 'collect')
        self.collect_kind_combo.addItem('赞过的笔记', 'like')
        form.addRow('采集类型', self.collect_kind_combo)
        self.collect_user_edit = QLineEdit()
        self.collect_user_edit.setPlaceholderText(
            '用户主页链接，例如：https://www.xiaohongshu.com/user/profile/xxxx?xsec_token=...'
        )
        form.addRow('主页', self.collect_user_edit)
        hint = QLabel('采集该用户公开的收藏 / 赞过笔记（对方未公开则可能为空）。')
        hint.setObjectName('mutedLabel')
        form.addRow('', hint)
        return tab

    def _build_usersearch_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(16, 0, 16, 0)
        layout.addStretch(1)
        row = QHBoxLayout()
        row.setSpacing(8)
        kw_label = QLabel('关键词')
        self.user_query_edit = QLineEdit()
        self.user_query_edit.setPlaceholderText('搜索用户，例如：黄金首饰 博主')
        num_label = QLabel('数量')
        self.user_num_spin = QSpinBox()
        self.user_num_spin.setRange(1, 200)
        self.user_num_spin.setValue(20)
        self.user_num_spin.setFixedWidth(76)
        row.addWidget(kw_label)
        row.addWidget(self.user_query_edit, 1)
        row.addWidget(num_label)
        row.addWidget(self.user_num_spin)
        layout.addLayout(row)
        hint = QLabel('按关键词搜索账号，结果导出 Excel（粉丝数/作品数/职业认证等）。')
        hint.setObjectName('mutedLabel')
        layout.addWidget(hint)
        layout.addStretch(1)
        return tab

    # ---------- 账号矩阵：资产台账 + 健康巡检 ----------

    def _build_chart_card(self) -> QWidget:
        """粉丝增长曲线卡片：账号 + 指标两个下拉 + 折线图。

        曲线数据全部来自台账 history（每次成功巡检按天写一条快照），
        没有历史时显示占位文案而不是画一条假曲线。
        """
        card = QFrame()
        card.setObjectName('card')
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)

        head = QHBoxLayout()
        head.setSpacing(8)
        title = QLabel('账号增长曲线')
        title.setObjectName('chartTitle')
        head.addWidget(title)
        self.chart_summary = QLabel('')
        self.chart_summary.setObjectName('mutedLabel')
        head.addWidget(self.chart_summary)
        head.addStretch(1)

        self.chart_account_combo = QComboBox()
        self.chart_account_combo.setObjectName('chartSelect')
        self.chart_account_combo.currentIndexChanged.connect(
            lambda _=0: self.refresh_chart())
        head.addWidget(self.chart_account_combo)

        self.chart_metric_combo = QComboBox()
        self.chart_metric_combo.setObjectName('chartSelect')
        for key, text in METRIC_LABELS.items():
            self.chart_metric_combo.addItem(text, key)
        self.chart_metric_combo.currentIndexChanged.connect(
            lambda _=0: self.refresh_chart())
        head.addWidget(self.chart_metric_combo)
        layout.addLayout(head)

        self.chart_view = None
        self.chart_placeholder = QLabel('暂无历史数据，巡检一次后开始累积（每次巡检按天记一条快照）。')
        self.chart_placeholder.setObjectName('mutedLabel')
        self.chart_placeholder.setAlignment(Qt.AlignCenter)
        self.chart_placeholder.setMinimumHeight(180)
        if HAS_QTCHARTS:
            self.chart_view = QChartView(self._new_chart())
            self.chart_view.setObjectName('chartView')
            self.chart_view.setRenderHint(QPainter.Antialiasing)
            self.chart_view.setFixedHeight(210)
            self.chart_view.setStyleSheet('background: transparent; border: none;')
            self.chart_view.setVisible(False)
            layout.addWidget(self.chart_view)
        layout.addWidget(self.chart_placeholder)
        return card

    @staticmethod
    def _new_chart():
        chart = QChart()
        chart.legend().setVisible(False)
        chart.setBackgroundVisible(False)
        chart.setMargins(QMargins(0, 0, 0, 0))
        return chart

    def refresh_chart(self, ledger: dict = None):
        """按当前下拉选择重绘曲线。数据为空时回退占位文案。"""
        if not hasattr(self, 'chart_account_combo'):
            return
        if ledger is None:
            ledger = paths.load_accounts()

        options = account_options(ledger)
        previous = self.chart_account_combo.currentData() or '__all__'
        self.chart_account_combo.blockSignals(True)
        self.chart_account_combo.clear()
        for user_id, label in options:
            self.chart_account_combo.addItem(label, user_id)
        index = self.chart_account_combo.findData(previous)
        self.chart_account_combo.setCurrentIndex(index if index >= 0 else 0)
        self.chart_account_combo.blockSignals(False)

        user_id = self.chart_account_combo.currentData() or '__all__'
        metric = self.chart_metric_combo.currentData() or 'fans'
        series = build_series(ledger, user_id, metric)

        if not HAS_QTCHARTS or self.chart_view is None:
            self.chart_placeholder.setText(
                '当前环境未安装图表组件（PySide6.QtCharts），曲线暂不可用。')
            return

        if len(series) < 2:
            # 只有一天快照画不出趋势：如实说明，不强行连点
            self.chart_view.setVisible(False)
            self.chart_placeholder.setVisible(True)
            if series:
                self.chart_placeholder.setText(
                    f'已记录 1 天数据（{series[0][0]}：{series[0][1]:,}），'
                    '再来一次巡检就能看到增长趋势。')
            else:
                self.chart_placeholder.setText(
                    '暂无历史数据，巡检一次后开始累积（每次巡检按天记一条快照）。')
            self.chart_summary.setText('')
            return

        summary = growth_summary(series)
        label = METRIC_LABELS.get(metric, '')
        delta = summary['delta']
        arrow = '↑' if delta > 0 else ('↓' if delta < 0 else '→')
        self.chart_summary.setText(
            f"{summary['days']} 天 · {label} {summary['first']:,} {arrow} {summary['last']:,}"
            f"（{'+' if delta > 0 else ''}{delta:,}）")

        self._draw_chart(series, label)
        self.chart_placeholder.setVisible(False)
        self.chart_view.setVisible(True)

    def _draw_chart(self, series: list, label: str):
        chart = self.chart_view.chart()
        chart.removeAllSeries()
        for axis in list(chart.axes()):
            chart.removeAxis(axis)

        line = QLineSeries()
        line.setColor(QColor('#6c5ce7'))
        pen = QPen(QColor('#6c5ce7'))
        pen.setWidth(2)
        line.setPen(pen)
        for date, value in series:
            ms = QDateTime(QDate.fromString(date, 'yyyy-MM-dd'),
                           QTime(0, 0)).toMSecsSinceEpoch()
            line.append(float(ms), float(value))
        chart.addSeries(line)

        axis_x = QDateTimeAxis()
        axis_x.setFormat('MM-dd')
        axis_x.setTickCount(min(len(series), 7))
        axis_x.setLabelsColor(QColor('#9aa0a6'))
        chart.addAxis(axis_x, Qt.AlignBottom)
        line.attachAxis(axis_x)

        axis_y = QValueAxis()
        values = [value for _date, value in series]
        low, high = min(values), max(values)
        pad = max(1.0, (high - low) * 0.15)
        axis_y.setRange(max(0.0, low - pad), high + pad)
        axis_y.setLabelFormat('%d')
        axis_y.setLabelsColor(QColor('#9aa0a6'))
        chart.addAxis(axis_y, Qt.AlignLeft)
        line.attachAxis(axis_y)
        chart.setTitle('')

    def _build_matrix_page(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(22, 18, 22, 16)
        outer.setSpacing(12)

        # 标题行
        header = QHBoxLayout()
        title_box = QVBoxLayout()
        title_box.setSpacing(1)
        title = QLabel('账号矩阵')
        title.setObjectName('greetTitle')
        self.matrix_sub = QLabel('正在读取账号台账…')
        self.matrix_sub.setObjectName('greetSub')
        title_box.addWidget(title)
        title_box.addWidget(self.matrix_sub)
        header.addLayout(title_box)
        header.addStretch(1)
        self.matrix_status = QLabel('')
        self.matrix_status.setObjectName('mutedLabel')
        header.addWidget(self.matrix_status)
        self.probe_fast_btn = QPushButton('快速体检')
        self.probe_fast_btn.setObjectName('softBtn')
        self.probe_fast_btn.setCursor(Qt.PointingHandCursor)
        self.probe_fast_btn.setToolTip('只验证登录态，每个账号 1 次请求，最快')
        self.probe_fast_btn.clicked.connect(lambda: self.start_probe(full=False))
        header.addWidget(self.probe_fast_btn)
        self.probe_full_btn = QPushButton('立即巡检')
        self.probe_full_btn.setObjectName('primaryBtn')
        self.probe_full_btn.setCursor(Qt.PointingHandCursor)
        self.probe_full_btn.setToolTip('刷新全部账号的资产数据（粉丝/作品/头像等）')
        self.probe_full_btn.clicked.connect(lambda: self.start_probe(full=True))
        header.addWidget(self.probe_full_btn)
        outer.addLayout(header)

        # 统计卡（objectName 对应 app.py STYLE 里的 statCard4-7）
        cards = QHBoxLayout()
        cards.setSpacing(12)
        self.stat_accounts = self._stat_card(cards, 'statCard4', '账号总数', '0 个')
        self.stat_healthy = self._stat_card(cards, 'statCard5', '健康账号', '0 个')
        self.stat_broken = self._stat_card(cards, 'statCard6', '异常账号', '0 个')
        self.stat_fans = self._stat_card(cards, 'statCard7', '总粉丝数', '—')
        outer.addLayout(cards)

        # 粉丝增长曲线（数据来自台账 history，每 90 天按天快照）
        outer.addWidget(self._build_chart_card())

        # 台账表
        table_card = QFrame()
        table_card.setObjectName('card')
        table_layout = QVBoxLayout(table_card)
        table_layout.setContentsMargins(14, 12, 14, 12)
        table_layout.setSpacing(8)
        self.matrix_table = QTableWidget(0, len(ACCOUNT_COLUMNS))
        self.matrix_table.setObjectName('accountTable')
        self.matrix_table.setHorizontalHeaderLabels(ACCOUNT_COLUMNS)
        header = self.matrix_table.horizontalHeader()
        # 账号列吃剩余宽度，其余列按内容自适应：否则 8 个默认 100px 的列会把
        # Stretch 的账号列挤成一条缝，昵称完全显示不出来
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        for column in range(1, len(ACCOUNT_COLUMNS)):
            header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        header.setMinimumSectionSize(48)
        self.matrix_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.matrix_table.setAlternatingRowColors(True)
        self.matrix_table.setWordWrap(False)
        self.matrix_table.verticalHeader().setVisible(False)
        self.matrix_table.verticalHeader().setDefaultSectionSize(44)
        self.matrix_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.matrix_table.doubleClicked.connect(self.open_current_account)
        table_layout.addWidget(self.matrix_table, 1)
        self.matrix_hint = QLabel('点「快速体检」验证登录态，点「立即巡检」刷新资产数据。')
        self.matrix_hint.setObjectName('mutedLabel')
        table_layout.addWidget(self.matrix_hint)
        outer.addWidget(table_card, 1)
        return page

    # ---------- 对标监控页 ----------

    def _build_watch_page(self) -> QWidget:
        """对标账号订阅监控：定时看竞品有没有发新笔记，只登记不自动下载。"""
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(22, 18, 22, 16)
        outer.setSpacing(12)

        header = QHBoxLayout()
        title_box = QVBoxLayout()
        title_box.setSpacing(1)
        title = QLabel('对标监控')
        title.setObjectName('greetTitle')
        self.watch_sub = QLabel('正在读取对标清单…')
        self.watch_sub.setObjectName('greetSub')
        title_box.addWidget(title)
        title_box.addWidget(self.watch_sub)
        header.addLayout(title_box)
        header.addStretch(1)
        self.watch_status = QLabel('')
        self.watch_status.setObjectName('mutedLabel')
        header.addWidget(self.watch_status)
        self.watch_add_btn = QPushButton('添加对标号')
        self.watch_add_btn.setObjectName('softBtn')
        self.watch_add_btn.setCursor(Qt.PointingHandCursor)
        self.watch_add_btn.setToolTip('粘贴对方的小红书主页链接或 user_id')
        self.watch_add_btn.clicked.connect(self.add_watch_target)
        header.addWidget(self.watch_add_btn)
        self.watch_run_btn = QPushButton('立即检查')
        self.watch_run_btn.setObjectName('primaryBtn')
        self.watch_run_btn.setCursor(Qt.PointingHandCursor)
        self.watch_run_btn.setToolTip('检查全部对标号有没有发新笔记')
        self.watch_run_btn.clicked.connect(self.start_watch)
        header.addWidget(self.watch_run_btn)
        outer.addLayout(header)

        cards = QHBoxLayout()
        cards.setSpacing(12)
        self.stat_watch_total = self._stat_card(cards, 'statCard8', '对标账号', '0 个')
        self.stat_watch_new = self._stat_card(cards, 'statCard9', '待采集新笔记', '0 篇')
        self.stat_watch_checked = self._stat_card(cards, 'statCard10', '最近检查', '—')
        outer.addLayout(cards)

        tree_card = QFrame()
        tree_card.setObjectName('card')
        tree_layout = QVBoxLayout(tree_card)
        tree_layout.setContentsMargins(14, 12, 14, 12)
        tree_layout.setSpacing(8)
        head = QHBoxLayout()
        head.setSpacing(8)
        tree_title = QLabel('对标清单')
        tree_title.setObjectName('chartTitle')
        head.addWidget(tree_title)
        self.watch_empty_label = QLabel('')
        self.watch_empty_label.setObjectName('mutedLabel')
        head.addWidget(self.watch_empty_label)
        head.addStretch(1)
        self.watch_collect_btn = QPushButton('采集选中的新笔记')
        self.watch_collect_btn.setObjectName('softBtn')
        self.watch_collect_btn.setCursor(Qt.PointingHandCursor)
        self.watch_collect_btn.setToolTip('对勾选的待采集笔记跑一次「链接采集」（沿用采集中心的保存选项）')
        self.watch_collect_btn.clicked.connect(self.collect_watch_notes)
        head.addWidget(self.watch_collect_btn)
        self.watch_clear_btn = QPushButton('清空待采集')
        self.watch_clear_btn.setObjectName('softBtn')
        self.watch_clear_btn.setCursor(Qt.PointingHandCursor)
        self.watch_clear_btn.setToolTip('把清单里已登记的新笔记全部标为已处理')
        self.watch_clear_btn.clicked.connect(self.clear_watch_notes)
        head.addWidget(self.watch_clear_btn)
        tree_layout.addLayout(head)

        self.watch_tree = QTreeWidget()
        self.watch_tree.setObjectName('watchTree')
        self.watch_tree.setColumnCount(len(WATCH_TREE_COLUMNS))
        self.watch_tree.setHeaderLabels(WATCH_TREE_COLUMNS)
        self.watch_tree.setAlternatingRowColors(True)
        self.watch_tree.setRootIsDecorated(True)
        self.watch_tree.setUniformRowHeights(True)
        self.watch_tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        for column in range(1, len(WATCH_TREE_COLUMNS)):
            self.watch_tree.header().setSectionResizeMode(column, QHeaderView.ResizeToContents)
        self.watch_tree.header().setMinimumSectionSize(48)
        self.watch_tree.itemChanged.connect(self._on_watch_tree_changed)
        self.watch_tree.itemDoubleClicked.connect(self._on_watch_tree_double_clicked)
        self.watch_tree.itemClicked.connect(self._on_watch_tree_clicked)
        tree_layout.addWidget(self.watch_tree, 1)
        self.watch_hint = QLabel('')
        self.watch_hint.setObjectName('mutedLabel')
        tree_layout.addWidget(self.watch_hint)
        outer.addWidget(tree_card, 1)
        return page

    def refresh_watch_page(self, sync: bool = True):
        """重建对标清单树与统计卡（进入页面 / 检查完 / 清单变动时调用）。

        sync 参数保留给「worker 正在写文件时只读不写」的语义（对齐 refresh_matrix_page）。
        """
        if not hasattr(self, 'watch_tree'):
            return
        data = watchlist.load_watchlist()
        targets = list(data.get('targets', {}).values())

        # 待处理多的排前面，其次按昵称
        def _sort_key(record):
            pending = watchlist.pending_count(record)
            return (-pending, str(record.get('nickname') or ''))
        targets.sort(key=_sort_key)

        self._watch_filling = True
        try:
            self.watch_tree.clear()
            for record in targets:
                self.watch_tree.addTopLevelItem(self._watch_top_item(record))
        finally:
            self._watch_filling = False

        total = len(targets)
        pending = watchlist.total_pending(data)
        checked = [str(r.get('last_checked_at') or '') for r in targets]
        last = max((c for c in checked if c), default='')
        self.stat_watch_total.setText(f'{total} 个')
        self.stat_watch_new.setText(f'{pending} 篇')
        self.stat_watch_checked.setText(last[5:16] if last else '—')
        if not hasattr(self, 'watch_sub'):
            return
        parts = [f'{total} 个对标账号']
        if last:
            parts.append(f'最近检查 {last[5:16]}')
        self.watch_sub.setText(' · '.join(parts))
        if total == 0:
            self.watch_empty_label.setText('')
            self.watch_hint.setText('还没有对标号：点右上角「添加对标号」，粘贴对方主页链接。'
                                    '首次检查只建立基线（不把历史当新笔记）。')
        elif pending:
            self.watch_empty_label.setText('')
            self.watch_hint.setText(
                f'共 {pending} 篇待采集新笔记：勾选后点「采集选中的新笔记」'
                '（用采集中心的保存选项）。笔记链接里的 token 当天有效，建议发现当天就采。'
            )
        else:
            self.watch_empty_label.setText('暂无新笔记')
            self.watch_hint.setText(
                '每 6 小时随账号巡检自动检查一次；也可随时点「立即检查」。'
                '双击笔记行可直接在浏览器打开。'
            )
        self._set_watch_collect_enabled()

    def _watch_top_item(self, record: dict) -> QTreeWidgetItem:
        """一个对标号的顶级行：勾选框 + 头像 + 昵称 + 各列数值。"""
        user_id = str(record.get('user_id') or '')
        item = QTreeWidgetItem()
        item.setData(0, Qt.UserRole, ('target', user_id))
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
        pending = watchlist.pending_count(record)
        nickname = str(record.get('nickname') or f'对标号 {user_id[:8]}')
        item.setText(0, f'{nickname}' + (f'   +{pending}' if pending else ''))
        item.setIcon(0, QIcon(self._account_avatar(record)))
        item.setText(1, str(record.get('fans') or _HEALTH_PLACEHOLDER))
        item.setText(2, self._format_count(record.get('posted')))
        item.setText(3, str(record.get('ip_location') or _HEALTH_PLACEHOLDER))
        item.setToolTip(3, str(record.get('health_note') or ''))
        item.setText(5, self._format_checked_at(record.get('last_checked_at')))
        item.setText(6, f'{pending} 篇' if pending else _HEALTH_PLACEHOLDER)
        for note in record.get('notes') or []:
            item.addChild(self._watch_note_item(user_id, note))
        # 状态列用「文字 + 颜色」放在最后一列之后不便上色，这里改为写进 tooltip 与子项
        health = record.get('health') or paths.HEALTH_UNKNOWN
        item.setText(4, paths.HEALTH_LABELS.get(health, '未知'))
        color = {
            paths.HEALTH_OK: QColor('#2fbf8f'),
            paths.HEALTH_EXPIRED: QColor('#e05a4e'),
            paths.HEALTH_LIMITED: QColor('#ef7d4e'),
            paths.HEALTH_NETWORK: QColor('#f9a03f'),
        }.get(health)
        if color is not None:
            item.setForeground(4, color)
        # 父项勾选态放在子项建好之后设置（Qt 里 setCheckState 只在值真变化时才发
        # itemChanged；先设为 Unchecked 会让「取消勾选」变成无变化的空操作）。
        # 有待采集笔记时默认勾上，客户点一下就能全取消。
        item.setCheckState(0, Qt.Checked if pending else Qt.Unchecked)
        if pending:
            item.setExpanded(True)
        return item

    @staticmethod
    def _watch_note_item(user_id: str, note: dict) -> QTreeWidgetItem:
        """一条待采集的新笔记（子行）：勾选框 + 标题 + 日期 + 点赞。"""
        note = note or {}
        item = QTreeWidgetItem()
        item.setData(0, Qt.UserRole, ('note', user_id, str(note.get('note_id') or '')))
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
        item.setCheckState(0, Qt.Checked)
        title = str(note.get('title') or '无标题')
        kind = '视频' if note.get('type') == 'video' else '图文'
        item.setText(0, f'{title}   [{kind}]')
        item.setText(1, str(note.get('liked') or 0))
        item.setText(5, MainWindow._format_note_time(note.get('time')))
        url = _watch_note_url(note)
        item.setToolTip(0, url or '该笔记没有链接（缺 xsec_token）')
        return item

    @staticmethod
    def _format_note_time(value) -> str:
        """毫秒时间戳 -> MM-DD（当年）或 YYYY-MM-DD。"""
        try:
            stamp = int(value) / 1000
        except (TypeError, ValueError):
            return _HEALTH_PLACEHOLDER
        if stamp <= 0:
            return _HEALTH_PLACEHOLDER
        text = time.strftime('%Y-%m-%d', time.localtime(stamp))
        return text[5:] if text[:4] == time.strftime('%Y') else text

    def _on_watch_tree_changed(self, item: QTreeWidgetItem, column: int):
        """父项勾选递归驱动子项；子项变动回写父项状态。

        重绘树时 setCheckState 也会触发本信号，用 _watch_filling 抑制，
        否则会递归回写、把父项状态改花。
        """
        if getattr(self, '_watch_filling', False) or column != 0:
            return
        self._watch_filling = True
        try:
            state = item.checkState(0)
            if item.childCount():
                for index in range(item.childCount()):
                    item.child(index).setCheckState(0, state)
            else:
                parent = item.parent()
                if parent is not None:
                    states = {parent.child(i).checkState(0)
                              for i in range(parent.childCount())}
                    if states == {Qt.Checked}:
                        parent.setCheckState(0, Qt.Checked)
                    elif states == {Qt.Unchecked}:
                        parent.setCheckState(0, Qt.Unchecked)
                    else:
                        parent.setCheckState(0, Qt.PartiallyChecked)
        finally:
            self._watch_filling = False
        self._set_watch_collect_enabled()

    def _set_watch_collect_enabled(self):
        if not hasattr(self, 'watch_collect_btn'):
            return
        self.watch_collect_btn.setEnabled(bool(self.selected_watch_notes()))

    def selected_watch_notes(self) -> list:
        """当前勾选的新笔记：[{'user_id', 'note_id', 'url'}, ...]。"""
        picked = []
        if not hasattr(self, 'watch_tree'):
            return picked
        for index in range(self.watch_tree.topLevelItemCount()):
            top = self.watch_tree.topLevelItem(index)
            for child_index in range(top.childCount()):
                child = top.child(child_index)
                if child.checkState(0) != Qt.Checked:
                    continue
                payload = child.data(0, Qt.UserRole) or ()
                if len(payload) < 3 or payload[0] != 'note':
                    continue
                url = child.toolTip(0)
                picked.append({'user_id': payload[1], 'note_id': payload[2],
                               'url': url if url.startswith('http') else ''})
        return picked

    def _on_watch_tree_double_clicked(self, item: QTreeWidgetItem, column: int):
        payload = item.data(0, Qt.UserRole) or ()
        if len(payload) >= 3 and payload[0] == 'note':
            url = item.toolTip(0)
            if url.startswith('http'):
                QDesktopServices.openUrl(QUrl(url))
            return
        if len(payload) >= 2 and payload[0] == 'target':
            user_id = str(payload[1] or '')
            if user_id:
                QDesktopServices.openUrl(QUrl(f'https://www.xiaohongshu.com/user/profile/{user_id}'))

    def _on_watch_tree_clicked(self, item: QTreeWidgetItem, column: int):
        # 点「待采集」列时，把该号的新笔记全部勾上（省得一个个点）
        if column != 6 or not item.childCount():
            return
        self._watch_filling = True
        try:
            item.setCheckState(0, Qt.Checked)
            for index in range(item.childCount()):
                item.child(index).setCheckState(0, Qt.Checked)
        finally:
            self._watch_filling = False
        self._set_watch_collect_enabled()

    def add_watch_target(self):
        data = watchlist.load_watchlist()
        existing = list(data.get('targets', {}).keys())
        dialog = WatchAddDialog(existing, self)
        if dialog.exec() != WatchAddDialog.Accepted or not dialog.user_id:
            return
        user_id = dialog.user_id
        watchlist.upsert_target(user_id, health=paths.HEALTH_UNKNOWN,
                                home_url=_watch_home_url(user_id))
        self.append_log(f'已添加对标号 {user_id}，正在建立基线…')
        self.refresh_watch_page()
        self.start_watch(only=user_id)

    def remove_watch_target(self, user_id: str):
        if not user_id:
            return
        record = watchlist.target_of(user_id)
        name = record.get('nickname') or user_id[:8]
        answer = QMessageBox.question(
            self, '移除对标号', f'确定移除「{name}」？已登记的新笔记会一并清掉。',
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            return
        watchlist.remove_targets([user_id])
        self.append_log(f'已移除对标号 {name}')
        self.refresh_watch_page()

    def clear_watch_notes(self):
        data = watchlist.load_watchlist()
        targets = data.get('targets', {})
        total = watchlist.total_pending(data)
        if not total:
            QMessageBox.information(self, '没有待采集', '当前没有待采集的新笔记。')
            return
        answer = QMessageBox.question(
            self, '清空待采集',
            f'把 {total} 篇待采集新笔记全部标为已处理？（笔记本身不会被删除）',
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            return
        for user_id, record in targets.items():
            ids = [n.get('note_id') for n in (record.get('notes') or [])]
            watchlist.drop_notes(user_id, ids)
        self.append_log(f'已清空 {total} 篇待采集新笔记')
        self.refresh_watch_page()

    def collect_watch_notes(self):
        picked = self.selected_watch_notes()
        urls = [p['url'] for p in picked if p.get('url')]
        missing = len(picked) - len(urls)
        if not urls:
            QMessageBox.information(
                self, '没有可采集的笔记',
                '选中的笔记缺少链接（多为登记时没有 xsec_token）。下一轮检查会重新登记。')
            return
        spec = TaskSpec(
            mode='urls',
            note_urls=urls,
            save_images=self.img_check.isChecked(),
            save_videos=self.video_check.isChecked(),
            save_excel=self.excel_check.isChecked() or not (
                self.img_check.isChecked() or self.video_check.isChecked()),
            zip_export=self.zip_check.isChecked(),
            no_watermark=self.no_water_check.isChecked(),
            delay_seconds=float(self.delay_spin.value()),
            task_name=self.task_edit.text().strip() or '对标采集',
            output_dir=self.output_edit.text().strip() or str(paths.DEFAULT_OUTPUT_DIR),
        )
        if missing:
            self.append_log(f'有 {missing} 篇笔记缺链接已跳过（多为登记时没有 xsec_token）')
        self._watch_collect_notes = picked
        if not self.start_collection_with_spec(spec, from_watch=True):
            return
        self.append_log(f'开始采集 {len(urls)} 篇对标新笔记')

    def _after_watch_collection(self):
        """采集完把本次勾选的新笔记从待处理列表移除（seen 保留，防重复登记）。"""
        picked = getattr(self, '_watch_collect_notes', None) or []
        self._watch_collect_notes = []
        if not picked:
            return
        grouped = {}
        for item in picked:
            grouped.setdefault(item['user_id'], []).append(item['note_id'])
        for user_id, note_ids in grouped.items():
            watchlist.drop_notes(user_id, note_ids)
        self.refresh_watch_page()

    # ---------- 对标检查调度 ----------

    def start_watch(self, only: str = '', silent: bool = False):
        """检查对标号。only 非空时只检查那一个（添加后立即建基线用）。"""
        if self._watch_running:
            if not silent:
                self.watch_status.setText('检查进行中…')
            return
        if self._probe_running:
            if not silent:
                QMessageBox.information(self, '稍后再试', '账号巡检正在进行，请等它结束后再检查对标号。')
            return
        if self.collect_worker is not None and self.collect_worker.isRunning():
            if not silent:
                QMessageBox.information(self, '稍后再试', '采集任务正在运行，请等它结束后再检查对标号。')
            return
        items = self.watch_items(only)
        if not items:
            if not silent:
                QMessageBox.information(self, '暂无对标号', '还没有添加对标账号，请点「添加对标号」。')
            return
        cookies = [c.get('cookie') or '' for c in paths.load_xhs_cookies()]
        cookie = next((c for c in cookies if c), '')
        if not cookie and not silent:
            QMessageBox.information(self, '请先登录', '账号池为空，请先在左侧「扫码添加账号」。')
            return
        self._watch_running = True
        self.watch_status.setText(f'检查中 0/{len(items)}…')
        self._set_watch_buttons(False)
        worker = _WatchProbeWorker(items, cookie, self)
        worker.one.connect(self._on_watch_one)
        worker.progress.connect(self._on_watch_progress)
        worker.finished_all.connect(self._on_watch_finished)
        worker.finished.connect(self._on_watch_thread_done)
        worker.start()
        self._watch_worker = worker

    def watch_items(self, only: str = '') -> list:
        """检查目标快照（只含 user_id + 记录），台账此刻的读数为准。"""
        targets = watchlist.load_watchlist().get('targets', {})
        items = []
        for user_id, record in targets.items():
            if only and str(user_id) != str(only):
                continue
            items.append({'user_id': str(user_id), 'target': dict(record)})
        return items

    def _set_watch_buttons(self, enabled: bool):
        for name in ('watch_add_btn', 'watch_run_btn', 'watch_collect_btn',
                     'watch_clear_btn'):
            button = getattr(self, name, None)
            if button is not None:
                button.setEnabled(enabled)
        if enabled:
            self._set_watch_collect_enabled()

    def _on_watch_progress(self, done: int, total: int):
        self.watch_status.setText(f'检查中 {done}/{total}…')

    def _on_watch_one(self, user_id: str, result: dict):
        # worker 正在写清单，这里只重绘界面不再同步文件
        self.refresh_watch_page(sync=False)

    def _on_watch_finished(self, ok_count: int, total: int):
        data = watchlist.load_watchlist()
        pending = watchlist.total_pending(data)
        self.watch_status.setText(
            f'检查完成：{ok_count}/{total} 个正常，{pending} 篇待采集')
        if pending:
            self.append_log(f'对标检查完成：发现 {pending} 篇待采集新笔记')
        self.refresh_watch_page()

    def _on_watch_thread_done(self):
        self._watch_running = False
        self._set_watch_buttons(True)

    def refresh_matrix_page(self, sync: bool = True):
        """重建台账表与统计卡（进入页面 / 巡检完 / 账号池变动时调用）。

        sync=False 用于巡检过程中的高频刷新：此时 worker 正在写台账文件，
        主线程不能再调 sync_accounts_from_pool 去写同一个文件。
        """
        if not hasattr(self, 'matrix_table'):
            return
        if sync:
            paths.sync_accounts_from_pool()
        ledger = paths.load_accounts()
        records = list(ledger['accounts'].values())
        # 需处理的排前面：异常(0) > 未巡检(1) > 正常(2)，同级按昵称
        def _sort_key(record):
            health = record.get('health') or paths.HEALTH_UNKNOWN
            if health in (paths.HEALTH_EXPIRED, paths.HEALTH_LIMITED):
                rank = 0
            elif health == paths.HEALTH_OK:
                rank = 2
            else:
                rank = 1
            return (rank, str(record.get('nickname') or ''))
        records.sort(key=_sort_key)

        table = self.matrix_table
        table.setRowCount(0)
        for record in records:
            row = table.rowCount()
            table.insertRow(row)
            table.setCellWidget(row, 0, self._account_cell(record))
            values = [
                str(record.get('red_id') or _HEALTH_PLACEHOLDER),
                '',  # 状态列用 cellWidget 上色
                self._format_count(record.get('fans')),
                self._format_count(record.get('follows')),
                self._format_count(record.get('interaction')),
                self._format_count(record.get('posted')),
                str(record.get('ip_location') or _HEALTH_PLACEHOLDER),
                self._format_checked_at(record.get('checked_at')),
            ]
            for offset, text in enumerate(values, start=1):
                item = QTableWidgetItem(text)
                item.setData(Qt.UserRole, record.get('key') or '')
                if offset > 1:
                    item.setTextAlignment(Qt.AlignCenter)
                table.setItem(row, offset, item)
            table.setCellWidget(row, 2, self._health_label(record.get('health')))

        self._update_matrix_stats(records)
        self.refresh_chart(ledger)
        self.render_xhs_accounts()

    def _account_cell(self, record: dict) -> QWidget:
        """账号单元格：头像 + 昵称 + 备注（一行放不下就只显示昵称）。"""
        holder = QWidget()
        row = QHBoxLayout(holder)
        row.setContentsMargins(8, 2, 8, 2)
        row.setSpacing(8)
        avatar = QLabel()
        avatar.setFixedSize(28, 28)
        avatar.setAlignment(Qt.AlignCenter)
        avatar.setPixmap(self._account_avatar(record))
        nickname = QLabel(str(record.get('nickname') or '未命名账号'))
        nickname.setObjectName('accountName')
        note = str(record.get('health_note') or '')
        if note:
            nickname.setToolTip(note)
        if str(record.get('red_id') or ''):
            nickname.setToolTip(
                f"小红书号：{record.get('red_id')}" + (f'\n{note}' if note else ''))
        row.addWidget(avatar)
        row.addWidget(nickname, 1)
        return holder

    @staticmethod
    def _account_avatar(record: dict) -> QPixmap:
        """优先用本地缓存头像，缺失/损坏时回退渐变首字。"""
        avatar_file = str(record.get('avatar_file') or '')
        if avatar_file and os.path.exists(avatar_file):
            pixmap = QPixmap(avatar_file)
            if not pixmap.isNull():
                return pixmap.scaled(28, 28, Qt.KeepAspectRatioByExpanding,
                                     Qt.SmoothTransformation)
        name = str(record.get('nickname') or '·')
        return gradient_tile(name[:1], ['#7d6ef0', '#5a4bd0'],
                             size=28, radius=14, font_size=13)

    @staticmethod
    def _health_label(health: str) -> QLabel:
        health = health or paths.HEALTH_UNKNOWN
        label = QLabel(paths.HEALTH_LABELS.get(health, '未知'))
        label.setObjectName({
            paths.HEALTH_OK: 'healthOk',
            paths.HEALTH_EXPIRED: 'healthExpired',
            paths.HEALTH_LIMITED: 'healthLimited',
            paths.HEALTH_NETWORK: 'healthNetwork',
        }.get(health, 'healthUnknown'))
        label.setAlignment(Qt.AlignCenter)
        return label

    @staticmethod
    def _format_count(value) -> str:
        try:
            return f'{int(value):,}'
        except (TypeError, ValueError):
            return _HEALTH_PLACEHOLDER

    @staticmethod
    def _format_checked_at(checked_at: str) -> str:
        text = str(checked_at or '')
        if not text:
            return '未巡检'
        return text[5:16] if len(text) >= 16 else text

    def _update_matrix_stats(self, records: list):
        total = len(records)
        healthy = [r for r in records if r.get('health') == paths.HEALTH_OK]
        broken = [r for r in records
                  if r.get('health') in (paths.HEALTH_EXPIRED, paths.HEALTH_LIMITED)]
        unchecked = [r for r in records
                     if r.get('health') in ('', None, paths.HEALTH_UNKNOWN)]
        self.stat_accounts.setText(f'{total} 个')
        self.stat_healthy.setText(f'{len(healthy)} 个')
        self.stat_broken.setText(f'{len(broken)} 个')
        self.stat_fans.setText(
            f'{sum(int(r.get("fans") or 0) for r in healthy):,}' if healthy else '—')
        if not hasattr(self, 'matrix_sub'):
            return
        last = max((str(r.get('checked_at') or '') for r in records), default='')
        parts = [f'{total} 个账号']
        if last:
            parts.append(f'最近巡检 {last[5:16]}')
        self.matrix_sub.setText(' · '.join(parts))
        # 统计口径只算已巡检账号，避免用偏低数字误导客户
        if unchecked and healthy:
            self.matrix_hint.setText(
                f'总粉丝数只统计已巡检的 {len(healthy)} 个正常账号'
                f'（另有 {len(unchecked)} 个账号未巡检）。'
            )
        elif total == 0:
            self.matrix_hint.setText('还没有小红书账号，先在左侧「扫码添加账号」。')
        else:
            self.matrix_hint.setText('点「快速体检」验证登录态，点「立即巡检」刷新资产数据。')

    def open_current_account(self, index):
        # 第 0 列是 cellWidget（头像+昵称），取不到 item，主键存在第 1 列上
        item = self.matrix_table.item(index.row(), 1)
        key = item.data(Qt.UserRole) if item else ''
        record = paths.load_accounts()['accounts'].get(key or '') or {}
        user_id = str(record.get('user_id') or '')
        if user_id:
            QDesktopServices.openUrl(
                QUrl(f'https://www.xiaohongshu.com/user/profile/{user_id}'))

    # ---------- 巡检调度 ----------

    def run_startup_probe(self):
        """启动后一轮快速体检：客户打开软件就能看到账号还活着没；随后接对标检查。"""
        self.start_probe(full=False, silent=True, then_watch=True)

    def run_scheduled_probe(self):
        self.start_probe(full=False, silent=True, then_watch=True)

    def probe_items(self) -> list:
        """巡检目标快照（key + cookie）；以 Cookie 池为准，台账里已删的不参与。"""
        items = []
        ledger = paths.load_accounts()['accounts']
        for entry in paths.load_xhs_cookies():
            cookie = (entry or {}).get('cookie') or ''
            if not cookie:
                continue
            key = paths.account_key(cookie)
            record = ledger.get(key) or {}
            items.append({'key': key, 'cookie': record.get('cookie') or cookie})
        return items

    def start_probe(self, full: bool = True, silent: bool = False, then_watch: bool = False):
        # 巡检结束后是否接着检查对标号（一轮里串行跑，不并存）
        if then_watch:
            self._pending_watch = True
        if self._probe_running:
            if not silent:
                self.matrix_status.setText('巡检进行中…')
            return
        if self._watch_running:
            self._pending_watch = False
            if not silent:
                QMessageBox.information(self, '稍后再试', '对标检查正在进行，请等它结束后再巡检账号。')
            return
        if self.collect_worker is not None and self.collect_worker.isRunning():
            # 与采集互斥：两套流程同时打接口会把风控风险叠加
            if not silent:
                QMessageBox.information(self, '稍后再试', '采集任务正在运行，请等它结束后再巡检账号。')
            return
        items = self.probe_items()
        if not items:
            self._pending_watch = False
            if not silent:
                QMessageBox.information(self, '暂无账号', '账号池为空，请先在左侧「扫码添加账号」。')
            return
        self._probe_running = True
        self.matrix_status.setText(f'巡检中 0/{len(items)}…')
        self._set_probe_buttons(False)
        worker = _AccountProbeWorker(items, full=full, parent=self)
        worker.one.connect(self._on_probe_one)
        worker.progress.connect(self._on_probe_progress)
        worker.finished_all.connect(self._on_probe_finished)
        worker.finished.connect(self._on_probe_thread_done)
        worker.start()
        self._probe_worker = worker

    def _set_probe_buttons(self, enabled: bool):
        for name in ('probe_fast_btn', 'probe_full_btn'):
            button = getattr(self, name, None)
            if button is not None:
                button.setEnabled(enabled)

    def _on_probe_progress(self, done: int, total: int):
        self.matrix_status.setText(f'巡检中 {done}/{total}…')

    def _on_probe_one(self, key: str, result: dict):
        # worker 正在写台账，这里只重绘界面不再同步文件
        self.refresh_matrix_page(sync=False)

    def _on_probe_finished(self, ok_count: int, total: int):
        self.matrix_status.setText(
            f'巡检完成：{ok_count}/{total} 个账号正常')
        self.refresh_matrix_page()

    def _on_probe_thread_done(self):
        self._probe_running = False
        self._set_probe_buttons(True)
        # 一轮里的第二段：巡检结束再检查对标号（串行，不并存，避免风控叠加）
        if self._pending_watch:
            self._pending_watch = False
            if watchlist.load_watchlist().get('targets'):
                QTimer.singleShot(1500, lambda: self.start_watch(silent=True))

    # ---------- 设置页 ----------

    def _build_settings_page(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(22, 18, 22, 16)
        outer.setSpacing(12)

        # 基础设置卡
        card = QFrame()
        card.setObjectName('card')
        box = QVBoxLayout(card)
        box.setContentsMargins(18, 16, 18, 16)
        title = QLabel('基础')
        title.setObjectName('greetTitle')
        box.addWidget(title)
        form = QFormLayout()
        form.setContentsMargins(0, 10, 0, 0)
        form.setSpacing(12)
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        dir_row = QHBoxLayout()
        self.settings_output_edit = QLineEdit(self.output_edit.text())
        # 设置页与采集页各有一个目录输入框，双向同步：此前设置页这个从没被读过，
        # 客户在设置页改了目录，采集仍用采集页的旧值（只有「浏览」按钮会同时写两边）。
        self.settings_output_edit.textChanged.connect(self.output_edit.setText)
        self.output_edit.textChanged.connect(self.settings_output_edit.setText)
        self.settings_output_edit.editingFinished.connect(self._persist_output_dir)
        dir_row.addWidget(self.settings_output_edit, 1)
        browse_btn = QPushButton('浏览')
        browse_btn.setObjectName('softBtn')
        browse_btn.clicked.connect(self.browse_output_dir)
        open_btn = QPushButton('打开')
        open_btn.setObjectName('softBtn')
        open_btn.clicked.connect(self.open_output_dir)
        dir_row.addWidget(browse_btn)
        dir_row.addWidget(open_btn)
        form.addRow('输出目录', dir_row)
        self.probe_auto_check = QCheckBox('每 6 小时自动体检账号 + 检查对标号')
        self.probe_auto_check.setChecked(bool(self.config.get('probe_auto', True)))
        self.probe_auto_check.setToolTip('关闭后只能手动点「快速体检 / 立即巡检 / 立即检查」')
        self.probe_auto_check.toggled.connect(self.toggle_auto_probe)
        form.addRow('自动巡检', self.probe_auto_check)
        clear_btn = QPushButton('清除小红书登录状态')
        clear_btn.setObjectName('dangerBtn')
        clear_btn.setCursor(Qt.PointingHandCursor)
        clear_btn.clicked.connect(self.forget_xhs_session)
        form.addRow('小红书', clear_btn)
        box.addLayout(form)
        outer.addWidget(card)

        # AI 改写配置卡（接口地址锁死平台网关；模型去模型广场复制）
        ai_card = QFrame()
        ai_card.setObjectName('card')
        ai_box = QVBoxLayout(ai_card)
        ai_box.setContentsMargins(18, 16, 18, 16)
        ai_title = QLabel('AI 改写')
        ai_title.setObjectName('greetTitle')
        ai_box.addWidget(ai_title)
        ai_form = QFormLayout()
        ai_form.setContentsMargins(0, 10, 0, 0)
        ai_form.setSpacing(12)
        ai_form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        ai_form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        ai_base_edit = QLineEdit(paths.AI_BASE_FIXED)
        ai_base_edit.setReadOnly(True)
        ai_form.addRow('接口地址', ai_base_edit)
        self.ai_key_edit = QLineEdit(self.config.get('ai_key') or '')
        self.ai_key_edit.setEchoMode(QLineEdit.Password)
        self.ai_key_edit.setPlaceholderText('sk-…（平台控制台获取）')
        ai_form.addRow('API Key', self.ai_key_edit)
        model_row = QHBoxLayout()
        model_row.setSpacing(8)
        self.ai_model_edit = QLineEdit(self.config.get('ai_model') or paths.AI_MODEL_DEFAULT)
        self.ai_model_edit.setPlaceholderText('粘贴模型名称，如 deepseek-v4.1-flash / gpt-4o-mini')
        pricing_btn = QPushButton('模型广场 ↗')
        pricing_btn.setObjectName('softBtn')
        pricing_btn.setCursor(Qt.PointingHandCursor)
        pricing_btn.setToolTip('打开模型广场，复制模型名称')
        pricing_btn.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(paths.AI_PRICING_URL))
        )
        model_row.addWidget(self.ai_model_edit, 1)
        model_row.addWidget(pricing_btn)
        ai_form.addRow('模型', model_row)
        self.ai_title_prompt_edit = QPlainTextEdit(self.config.get('ai_title_prompt') or '')
        self.ai_title_prompt_edit.setPlaceholderText(
            '留空使用内置预设：小红书爆款标题写手，20字内，有网感，只输出标题本身'
        )
        self.ai_title_prompt_edit.setFixedHeight(76)
        self.ai_content_prompt_edit = QPlainTextEdit(self.config.get('ai_content_prompt') or '')
        self.ai_content_prompt_edit.setPlaceholderText(
            '留空使用内置预设：小红书爆款文案写手，150-250字+话题标签，只输出正文本身'
        )
        self.ai_content_prompt_edit.setFixedHeight(96)
        ai_form.addRow('标题提示词', self.ai_title_prompt_edit)
        ai_form.addRow('文案提示词', self.ai_content_prompt_edit)
        ai_box.addLayout(ai_form)
        ai_btn_row = QHBoxLayout()
        save_btn = QPushButton('保存配置')
        save_btn.setObjectName('primaryBtn')
        save_btn.setCursor(Qt.PointingHandCursor)
        save_btn.clicked.connect(self.save_ai_config)
        self.ai_test_btn = QPushButton('测试连接')
        self.ai_test_btn.setObjectName('softBtn')
        self.ai_test_btn.setCursor(Qt.PointingHandCursor)
        self.ai_test_btn.clicked.connect(self.test_ai_config)
        self.ai_test_label = QLabel('')
        self.ai_test_label.setObjectName('mutedLabel')
        ai_btn_row.addWidget(save_btn)
        ai_btn_row.addWidget(self.ai_test_btn)
        ai_btn_row.addWidget(self.ai_test_label, 1)
        ai_box.addLayout(ai_btn_row)
        outer.addWidget(ai_card)
        outer.addStretch(1)
        return page

    def _persist_output_dir(self):
        """采集页/设置页任一目录框编辑结束就落盘，避免重启丢配置。

        空值不覆盖（客户清空输入框时不该把配置抹成空串）。
        """
        value = self.output_edit.text().strip()
        if value and value != self.config.get('output_dir'):
            self.config['output_dir'] = value
            paths.save_config(self.config)

    def toggle_auto_probe(self, enabled: bool):
        self.config['probe_auto'] = bool(enabled)
        paths.save_config(self.config)
        if enabled:
            self._probe_timer.start()
        else:
            self._probe_timer.stop()
        self.append_log('账号自动巡检已' + ('开启（每 6 小时）' if enabled else '关闭'))

    def save_ai_config(self):
        self.config['ai_base'] = paths.AI_BASE_FIXED
        self.config['ai_key'] = self.ai_key_edit.text().strip()
        self.config['ai_model'] = self.ai_model_edit.text().strip() or paths.AI_MODEL_DEFAULT
        self.config['ai_title_prompt'] = self.ai_title_prompt_edit.toPlainText().strip()
        self.config['ai_content_prompt'] = self.ai_content_prompt_edit.toPlainText().strip()
        paths.save_config(self.config)
        self.ai_test_label.setText('已保存')
        self.append_log('AI 改写配置已保存')

    def test_ai_config(self):
        self.save_ai_config()
        self.ai_test_btn.setEnabled(False)
        self.ai_test_label.setText('测试中…')
        from desktop.ai_client import AIClient
        client = AIClient(
            paths.AI_BASE_FIXED, self.config.get('ai_key') or '',
            self.config.get('ai_model') or '',
            self.config.get('ai_title_prompt') or '',
            self.config.get('ai_content_prompt') or '',
        )
        self._ai_test_worker = _AiTestWorker(client, self)
        self._ai_test_worker.ok.connect(self._on_ai_test_ok)
        self._ai_test_worker.failed.connect(self._on_ai_test_failed)
        self._ai_test_worker.start()

    def _on_ai_test_ok(self, sample: str):
        self.ai_test_btn.setEnabled(True)
        self.ai_test_label.setText(f'连接成功：{sample}…')

    def _on_ai_test_failed(self, message: str):
        self.ai_test_btn.setEnabled(True)
        self.ai_test_label.setText(f'失败：{message}')

    def switch_page(self, key: int):
        for k, btn in self.nav_buttons.items():
            btn.setChecked(k == key)
        self.pages.setCurrentIndex(key)
        if key == NAV_MATRIX:
            # 切进矩阵页时刷新台账（sync=False：不重复写台账文件）
            self.refresh_matrix_page(sync=False)
        elif key == NAV_WATCH:
            self.refresh_watch_page(sync=False)

    # ---------- Cookie 池侧边栏 ----------

    def render_xhs_accounts(self):
        """按 Cookie 池重建侧边栏账号列表；圆点颜色取自账号台账的健康状态。"""
        while self.xhs_accounts_box.count():
            item = self.xhs_accounts_box.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        cookies = paths.load_xhs_cookies()
        ledger = paths.load_accounts()['accounts']
        if not cookies:
            empty = QLabel('暂无账号，请先扫码')
            empty.setObjectName('mutedLabel')
            empty.setContentsMargins(10, 2, 0, 2)
            self.xhs_accounts_box.addWidget(empty)
        for index, item in enumerate(cookies):
            name = item.get('nickname') or f'账号{index + 1}'
            record = ledger.get(paths.account_key(item.get('cookie') or '')) or {}
            health = record.get('health') or paths.HEALTH_UNKNOWN
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(10, 3, 4, 3)
            row_layout.setSpacing(6)
            dot = QLabel()
            dot.setObjectName({
                paths.HEALTH_OK: 'accountDotOk',
                paths.HEALTH_EXPIRED: 'accountDotExpired',
                paths.HEALTH_LIMITED: 'accountDotLimited',
                paths.HEALTH_NETWORK: 'accountDotNetwork',
            }.get(health, 'accountDotUnknown'))
            dot.setAttribute(Qt.WA_StyledBackground, True)
            dot.setFixedSize(7, 7)
            dot.setToolTip(f"状态：{paths.HEALTH_LABELS.get(health, '未知')}")
            name_label = QLabel(name)
            name_label.setObjectName('accountName')
            del_btn = QPushButton('✕')
            del_btn.setObjectName('ghostBtn')
            del_btn.setFixedSize(20, 20)
            del_btn.setCursor(Qt.PointingHandCursor)
            del_btn.setToolTip('从账号池移除')
            del_btn.clicked.connect(lambda _=False, i=index: self.remove_account(i))
            row_layout.addWidget(dot)
            row_layout.addWidget(name_label, 1)
            row_layout.addWidget(del_btn)
            self.xhs_accounts_box.addWidget(row)
        if getattr(self, 'stat_xhs', None):
            self.stat_xhs.setText(f'{len(cookies)} 个' if cookies else '未登录')

    def remove_account(self, index: int):
        cookie = ''
        items = paths.load_xhs_cookies()
        if 0 <= index < len(items):
            cookie = (items[index] or {}).get('cookie') or ''
        paths.remove_xhs_cookie(index)
        if cookie:
            # 客户在侧边栏删掉的号，台账里也一并清掉（含历史快照与头像缓存）
            paths.remove_accounts([paths.account_key(cookie)])
        self.append_log(f'已从账号池移除账号 {index + 1}')
        self.refresh_matrix_page(sync=False)
        self.restore_xhs_session()

    # ---------- 小红书会话 ----------

    def restore_xhs_session(self):
        cookies = paths.load_xhs_cookies()
        self.render_xhs_accounts()
        if not cookies:
            self.auth = None
            self.set_xhs_state(False, '')
            return
        self.xhs_label.setText('小红书：检测中…')
        self.restore_worker = _RestoreAuthWorker(cookies, self)
        self.restore_worker.ok.connect(self._on_auth_ready)
        self.restore_worker.failed.connect(self._on_auth_failed)
        self.restore_worker.start()

    def _on_auth_ready(self, auth, nickname: str, cookie: str):
        self.auth = auth
        self.xhs_nickname = nickname
        if nickname:
            paths.set_xhs_nickname(cookie, nickname)
        # 恢复会话本身也证明了第一个账号可用，写进台账免得显示"未巡检"
        if cookie:
            paths.upsert_account(paths.account_key(cookie),
                                 health=paths.HEALTH_OK, nickname=nickname,
                                 health_note='')
        self.render_xhs_accounts()
        self.refresh_matrix_page(sync=False)
        self.set_xhs_state(True, nickname)
        self.append_log(f'小红书会话已恢复（{nickname or "Cookie 有效"}）')

    def _on_auth_failed(self, message: str):
        self.auth = None
        self.set_xhs_state(False, '')
        self.append_log(f'账号池中没有可用的小红书会话，请重新扫码（{message}）')

    def set_xhs_state(self, logged_in: bool, nickname: str):
        if logged_in:
            text = nickname or '已登录'
            self.xhs_label.setText(f'小红书：{text}')
            self.xhs_label.setObjectName('xhsStateOk')
            self.xhs_login_btn.setText('切换账号')
        else:
            self.xhs_label.setText('小红书：未登录')
            self.xhs_label.setObjectName('xhsStateBad')
            self.xhs_login_btn.setText('登录小红书')
        self._restyle(self.xhs_label)
        self.xhs_label.setStyleSheet('')

    def _restyle(self, widget: QWidget):
        style = widget.style()
        style.unpolish(widget)
        style.polish(widget)

    def forget_xhs_session(self):
        self.auth = None
        paths.clear_xhs_cookie()
        paths.clear_avatars()
        self.set_xhs_state(False, '')
        self.append_log('已清除小红书登录状态')

    def open_xhs_login(self):
        dialog = XhsLoginDialog(self)
        if dialog.exec():
            self.append_log('小红书登录成功，正在恢复会话…')
            self.auth = None
            self.restore_xhs_session()

    # ---------- 采集 ----------

    def toggle_run(self):
        if self.collect_worker is not None and self.collect_worker.isRunning():
            self.collect_worker.stop()
            self.run_btn.setEnabled(False)
            self.run_btn.setText('正在停止…')
            return
        self.start_collection()

    def _current_spec(self) -> TaskSpec:
        index = self.tabs.currentIndex()
        ai_cfg = {}
        if self.ai_check.isChecked():
            ai_cfg = {
                'base': paths.AI_BASE_FIXED,
                'key': self.config.get('ai_key') or '',
                'model': self.config.get('ai_model') or '',
                'title_prompt': self.config.get('ai_title_prompt') or '',
                'content_prompt': self.config.get('ai_content_prompt') or '',
            }
        spec = TaskSpec(
            save_images=self.img_check.isChecked(),
            save_videos=self.video_check.isChecked(),
            save_excel=self.excel_check.isChecked(),
            zip_export=self.zip_check.isChecked(),
            no_watermark=self.no_water_check.isChecked(),
            ai_cfg=ai_cfg,
            delay_seconds=float(self.delay_spin.value()),
            task_name=self.task_edit.text().strip(),
            output_dir=self.output_edit.text().strip() or str(paths.DEFAULT_OUTPUT_DIR),
        )
        if index == 0:
            spec.mode = 'search'
            spec.query = self.query_edit.text().strip()
            spec.require_num = self.num_spin.value()
            spec.sort_type = self.sort_combo.currentData()
            spec.note_type = self.type_combo.currentData()
            spec.note_time = self.time_combo.currentData()
            if not spec.task_name:
                spec.task_name = spec.query
        elif index == 1:
            spec.mode = 'urls'
            spec.note_urls = self.urls_edit.toPlainText().splitlines()
            if not spec.task_name:
                spec.task_name = '链接采集'
        elif index == 2:
            spec.mode = 'user'
            spec.user_url = self.user_edit.text().strip()
            if not spec.task_name:
                tail = spec.user_url.split('/')[-1].split('?')[0]
                spec.task_name = f'用户{tail}' if tail else '主页采集'
        elif index == 3:
            spec.mode = 'comments'
            spec.comment_urls = self.comments_edit.toPlainText().splitlines()
            if not spec.task_name:
                spec.task_name = '评论采集'
        elif index == 4:
            spec.mode = 'collect'
            spec.collect_kind = self.collect_kind_combo.currentData()
            spec.user_url = self.collect_user_edit.text().strip()
            if not spec.task_name:
                tail = spec.user_url.split('/')[-1].split('?')[0]
                prefix = '赞过' if spec.collect_kind == 'like' else '收藏'
                spec.task_name = f'{prefix}{tail}' if tail else f'{prefix}采集'
        elif index == 5:
            spec.mode = 'usersearch'
            spec.user_query = self.user_query_edit.text().strip()
            spec.require_num = self.user_num_spin.value()
            if not spec.task_name:
                spec.task_name = f'用户搜索{spec.user_query}' if spec.user_query else '用户搜索'
        return spec

    def _validate_spec(self, spec: TaskSpec) -> str:
        if not paths.load_xhs_cookies():
            return '请先扫码登录小红书'
        if self.auth is None:
            return '小红书会话检测中，请稍候重试'
        if spec.mode == 'search' and not spec.query:
            return '请输入搜索关键词'
        if spec.mode == 'user' and not spec.user_url:
            return '请输入用户主页链接'
        if spec.mode == 'collect' and not spec.user_url:
            return '请输入用户主页链接'
        if spec.mode == 'usersearch':
            if not spec.user_query:
                return '请输入搜索关键词'
            if not spec.save_excel:
                return '用户搜索仅支持导出 Excel，请勾选「Excel」'
        if spec.mode == 'urls' and not spec.note_urls:
            return '请至少填写一个笔记链接'
        if spec.mode == 'comments':
            if not [u for u in spec.comment_urls if u.strip()]:
                return '请至少填写一个笔记链接'
            if not spec.save_excel:
                return '评论采集仅支持导出 Excel，请勾选「Excel」'
        if not os.path.isdir(spec.output_dir):
            return '输出目录不存在，请重新选择'
        if spec.mode not in ('comments', 'usersearch') and not (
                spec.save_images or spec.save_videos
                or spec.save_excel or spec.zip_export):
            return '请至少选择一种保存内容'
        if spec.ai_cfg and not spec.ai_cfg.get('key'):
            return '已勾选 AI改写，请先在「设置」页配置 API Key'
        return ''

    def start_collection(self, from_watch: bool = False):
        if from_watch:
            return
        spec = self._current_spec()
        error = self._validate_spec(spec)
        if error:
            QMessageBox.warning(self, '无法开始', error)
            return
        self._start_collection_with_spec(spec)

    def start_collection_with_spec(self, spec: TaskSpec, from_watch: bool = False) -> bool:
        """用现成的 spec 启动采集（对标页的「采集选中的新笔记」复用）。

        返回是否真的启动（供调用方决定要不要记日志）。
        """
        error = self._validate_spec(spec)
        if error:
            QMessageBox.warning(self, '无法开始', error)
            return False
        if not self._start_collection_with_spec(spec):
            return False
        return True

    def _start_collection_with_spec(self, spec: TaskSpec) -> bool:
        if self._probe_running:
            # 与巡检互斥：两套流程同时打接口会把风控风险叠加
            QMessageBox.warning(self, '巡检进行中', '账号巡检正在进行，请等它结束后再开始采集。')
            return False
        if self._watch_running:
            QMessageBox.warning(self, '对标检查进行中', '对标检查正在进行，请等它结束后再开始采集。')
            return False

        self.config['output_dir'] = spec.output_dir
        paths.save_config(self.config)

        self.table.setRowCount(0)
        self.stat_count.setText('0 篇')
        self.progress_bar.setValue(0)
        self.stage_label.setText('')
        self.run_btn.setText('停止')
        self.tabs.setEnabled(False)

        self.collect_worker = _CollectWorker(
            [c.get('cookie', '') for c in paths.load_xhs_cookies()], spec, self)
        self.collect_worker.note.connect(self.add_note_row)
        self.collect_worker.progress.connect(self.on_progress)
        self.collect_worker.logline.connect(self.append_log)
        self.collect_worker.done.connect(self.on_done)
        self.collect_worker.failed.connect(self.on_failed)
        self.collect_worker.start()
        return True

    def on_progress(self, done: int, total: int, stage: str):
        self.progress_bar.setMaximum(max(total, 1))
        self.progress_bar.setValue(done)
        self.stage_label.setText(f'{stage} {done}/{total}')

    def add_note_row(self, note: dict):
        row = self.table.rowCount()
        self.table.insertRow(row)
        values = [
            note.get('title') or '无标题',
            note.get('note_type') or '',
            note.get('nickname') or '',
            str(note.get('liked_count') or 0),
            str(note.get('collected_count') or 0),
            str(note.get('comment_count') or 0),
            note.get('upload_time') or '',
            '双击打开',
        ]
        for column, text in enumerate(values):
            item = QTableWidgetItem(text)
            if column == 7:
                item.setForeground(Qt.blue)
            item.setData(Qt.UserRole, note.get('note_url') or '')
            self.table.setItem(row, column, item)
        self.stat_count.setText(f'{row + 1} 篇')

    def open_current_note(self, index):
        item = self.table.item(index.row(), 7)
        url = item.data(Qt.UserRole) if item else ''
        if url:
            QDesktopServices.openUrl(QUrl(url))

    def _finish_run(self):
        self.run_btn.setEnabled(True)
        self.run_btn.setText('开始采集')
        self.tabs.setEnabled(True)

    def on_done(self, summary: dict):
        self._finish_run()
        # 从对标页发起的采集：把本次选中的新笔记标为已处理
        self._after_watch_collection()
        self.stage_label.setText('完成')
        if 'users' in summary:
            self.stat_count.setText(f"{summary['users']} 个")
            self.append_log(
                f"任务完成：搜索到 {summary['users']} 个用户，"
                f"用时 {summary['seconds']} 秒"
            )
            if summary['stopped']:
                QMessageBox.information(
                    self, '已停止', f"任务已停止，共 {summary['users']} 个用户。")
            else:
                QMessageBox.information(
                    self, '搜索完成',
                    f"搜索到 {summary['users']} 个用户\n"
                    f"用时 {summary['seconds']} 秒\n"
                    f"输出目录：{summary['base_dir']}",
                )
            return
        if 'comments' in summary:
            self.stat_count.setText(f"{summary['comments']} 条")
            self.append_log(
                f"任务完成：{summary['got']}/{summary['total']} 篇笔记，"
                f"共 {summary['comments']} 条评论，用时 {summary['seconds']} 秒"
            )
            if summary['stopped']:
                QMessageBox.information(
                    self, '已停止',
                    f"任务已停止，共抓取 {summary['got']} 篇笔记、{summary['comments']} 条评论。")
            else:
                QMessageBox.information(
                    self, '采集完成',
                    f"抓取 {summary['got']}/{summary['total']} 篇笔记\n"
                    f"共 {summary['comments']} 条评论\n"
                    f"用时 {summary['seconds']} 秒\n"
                    f"输出目录：{summary['base_dir']}",
                )
            return
        self.append_log(
            f"任务完成：抓取 {summary['got']}/{summary['total']} 篇，"
            f"用时 {summary['seconds']} 秒，输出目录 {summary['base_dir']}"
        )
        zip_info = f"\n小绿书压缩包：{summary['zip']}" if summary.get('zip') else ''
        if summary['stopped']:
            QMessageBox.information(self, '已停止', f"任务已停止，共抓取 {summary['got']} 篇。")
        else:
            QMessageBox.information(
                self, '采集完成',
                f"抓取 {summary['got']}/{summary['total']} 篇\n"
                f"用时 {summary['seconds']} 秒{zip_info}\n"
                f"输出目录：{summary['base_dir']}",
            )

    def on_failed(self, message: str):
        self._finish_run()
        # 失败时不清账：保留待采集，客户修好问题可以重试
        self._watch_collect_notes = []
        self.stage_label.setText('失败')
        QMessageBox.critical(self, '采集失败', message)

    # ---------- 会话校验 / 登出 ----------

    def run_session_check(self):
        server = self.session.get('server') or self.config.get('server')
        token = self.session.get('token')
        if not server or not token:
            return
        worker = _CheckWorker(server, token, self)
        worker.ok.connect(self._on_check_ok)
        worker.failed.connect(self._on_check_failed)
        worker.start()
        self._check_worker = worker

    def _on_check_ok(self, expire: str):
        self.account_label.setText(f'有效期：{expire}')
        self.stat_expire.setText(expire)

    def _on_check_failed(self, message: str):
        QMessageBox.warning(self, '登录状态失效', message)
        self.logout()

    def logout(self):
        server = self.session.get('server') or self.config.get('server')
        token = self.session.get('token')
        if server and token:
            threading.Thread(
                target=lambda: AuthClient(server).logout(token), daemon=True,
            ).start()
        paths.clear_session()
        self.close()

        from desktop.login_dialog import LoginDialog
        dialog = LoginDialog(paths.load_config())
        if dialog.exec():
            self.new_window = MainWindow(dialog.config, dialog.session)
            self.new_window.show()
        else:
            from PySide6.QtWidgets import QApplication
            QApplication.quit()

    # ---------- 杂项 ----------

    def browse_output_dir(self):
        current = self.output_edit.text() or str(paths.DEFAULT_OUTPUT_DIR)
        chosen = QFileDialog.getExistingDirectory(self, '选择输出目录', current)
        if chosen:
            # 只写采集页输入框：设置页那个与它双向同步（见 _build_settings_page）
            self.output_edit.setText(chosen)
            self._persist_output_dir()

    def open_output_dir(self):
        path = self.output_edit.text().strip() or str(paths.DEFAULT_OUTPUT_DIR)
        os.makedirs(path, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def append_log(self, text: str):
        self.log_view.appendPlainText(text)

    def closeEvent(self, event):
        if self.collect_worker is not None and self.collect_worker.isRunning():
            self.collect_worker.stop()
        for worker in (
            self.collect_worker,
            self.restore_worker,
            getattr(self, '_check_worker', None),
            getattr(self, '_ai_test_worker', None),
            getattr(self, '_probe_worker', None),
            getattr(self, '_watch_worker', None),
        ):
            if worker is None or not worker.isRunning():
                continue
            stopper = getattr(worker, 'stop', None)
            if callable(stopper):
                stopper()
            # 巡检是协作式停止：最坏等当前这个账号探测完（约 2 秒）
            worker.wait(3500)
        try:
            if self._log_sink_id is not None:
                logger.remove(self._log_sink_id)
                self._log_sink_id = None
        except Exception:
            pass
        super().closeEvent(event)
