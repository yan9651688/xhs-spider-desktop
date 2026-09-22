# encoding: utf-8
"""主窗口：Redwhale 风格（左侧导航 + 卡片式主区）。

侧边栏：采集中心 / 账号矩阵(预留) / 设置 + 小红书账号区 + 底部用户卡。
采集中心：问候头部、渐变统计卡、三种采集页签、保存选项、进度、结果表 + 日志。
"""
from __future__ import annotations

import os
import threading

from loguru import logger
from PySide6.QtCore import Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QIcon, QPainter, QPixmap, QPen
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
    QVBoxLayout,
    QWidget,
)

from desktop import paths
from desktop.auth_client import AuthClient, AuthError
from desktop.spider_service import TaskSpec, run_collection
from desktop.xhs_login_dialog import XhsLoginDialog

SORT_OPTIONS = [('综合排序', 0), ('最新', 1), ('最多点赞', 2), ('最多评论', 3), ('最多收藏', 4)]
TYPE_OPTIONS = [('不限', 0), ('视频笔记', 1), ('图文笔记', 2)]
TIME_OPTIONS = [('不限', 0), ('一天内', 1), ('一周内', 2), ('半年内', 3)]

TABLE_COLUMNS = ['标题', '类型', '作者', '点赞', '收藏', '评论', '发布时间', '链接']

NAV_HOME, NAV_MATRIX, NAV_SETTINGS = 0, 1, 2


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
    ok = Signal(object, str)     # auth, nickname
    failed = Signal(str)

    def __init__(self, cookie: str, parent=None):
        super().__init__(parent)
        self.cookie = cookie

    def run(self):
        try:
            from apis.xhs_pc_apis import XHS_Apis
            from xhs_utils.xhs_pc import XHSPcAuth

            auth = XHSPcAuth.from_cookie(self.cookie)
            nickname = ''
            try:
                success, _msg, res = XHS_Apis(auth).get_user_me()
                if success and res:
                    nickname = (res.get('data') or {}).get('nickname') or ''
            except Exception:
                pass
            self.ok.emit(auth, nickname)
        except Exception as exc:
            logger.error(f'恢复小红书会话失败：{exc}')
            self.failed.emit(str(exc))


class _CollectWorker(QThread):
    note = Signal(object)
    progress = Signal(int, int, str)
    logline = Signal(str)
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, auth, spec: TaskSpec, parent=None):
        super().__init__(parent)
        self.auth = auth
        self.spec = spec
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        emit = self._Emit(self)
        try:
            summary = run_collection(self.auth, self.spec, lambda: self._stop, emit)
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


class MainWindow(QMainWindow):
    def __init__(self, config: dict, session: dict):
        super().__init__()
        self.config = config
        self.session = session
        self.auth = None
        self.xhs_nickname = ''
        self.collect_worker = None
        self.restore_worker = None
        self._log_sink_id = None

        self.setWindowTitle('小红书采集工具')
        self.resize(1120, 760)
        self.setMinimumSize(1000, 680)
        self._build_ui()

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
        logo_tile.setPixmap(gradient_tile('红', ['#ff8a5c', '#6c5ce7', '#4ec9d4']))
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
        add_nav(NAV_MATRIX, '账号矩阵', 'grid', badge='soon')
        add_nav(NAV_SETTINGS, '设置', 'gear')

        layout.addSpacing(14)
        section = QLabel('账号')
        section.setObjectName('navSection')
        layout.addWidget(section)

        add_row = QHBoxLayout()
        add_btn = QPushButton('添加小红书账号')
        add_btn.setObjectName('navSubItem')
        add_btn.setIcon(line_icon('plus', '#6c5ce7'))
        add_btn.setCursor(Qt.PointingHandCursor)
        add_btn.clicked.connect(self.open_xhs_login)
        add_row.addWidget(add_btn, 1)
        layout.addLayout(add_row)

        self.xhs_item = QPushButton('小红书 · 未登录')
        self.xhs_item.setObjectName('navSubItem')
        self.xhs_item.setIcon(line_icon('user'))
        self.xhs_item.setCursor(Qt.PointingHandCursor)
        self.xhs_item.clicked.connect(self.open_xhs_login)
        layout.addWidget(self.xhs_item)

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
        for w in (self.img_check, self.video_check, self.excel_check,
                  self.zip_check, self.ai_check):
            checks_row.addWidget(w)
        checks_row.addStretch(1)
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
        return page

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

    # ---------- 矩阵占位页 ----------

    def _build_matrix_page(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(22, 18, 22, 16)
        card = QFrame()
        card.setObjectName('card')
        box = QVBoxLayout(card)
        box.setAlignment(Qt.AlignCenter)
        box.setSpacing(10)
        icon = QLabel('🧩')
        icon.setAlignment(Qt.AlignCenter)
        icon.setStyleSheet('font-size:44px; background: transparent;')
        title = QLabel('账号矩阵 · 开发中')
        title.setObjectName('greetTitle')
        title.setAlignment(Qt.AlignCenter)
        desc = QLabel('多账号统一管理、批量采集与发布调度。\n侧边栏入口已预留，规划中敬请期待。')
        desc.setObjectName('mutedLabel')
        desc.setAlignment(Qt.AlignCenter)
        box.addWidget(icon)
        box.addWidget(title)
        box.addWidget(desc)
        outer.addWidget(card, 1)
        return page

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
        self.ai_prompt_edit = QPlainTextEdit(self.config.get('ai_prompt') or '')
        self.ai_prompt_edit.setPlaceholderText(
            '留空使用内置预设提示词（改写标题 20 字内 + 正文 150-250 字 + 话题标签，输出 JSON）'
        )
        self.ai_prompt_edit.setFixedHeight(110)
        ai_form.addRow('提示词', self.ai_prompt_edit)
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

    def save_ai_config(self):
        self.config['ai_base'] = paths.AI_BASE_FIXED
        self.config['ai_key'] = self.ai_key_edit.text().strip()
        self.config['ai_model'] = self.ai_model_edit.text().strip() or paths.AI_MODEL_DEFAULT
        self.config['ai_prompt'] = self.ai_prompt_edit.toPlainText().strip()
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
            self.config.get('ai_model') or '', self.config.get('ai_prompt') or '',
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

    # ---------- 小红书会话 ----------

    def restore_xhs_session(self):
        cookie = paths.load_xhs_cookie()
        if not cookie:
            self.set_xhs_state(False, '')
            return
        self.xhs_label.setText('小红书：检测中…')
        self.restore_worker = _RestoreAuthWorker(cookie, self)
        self.restore_worker.ok.connect(self._on_auth_ready)
        self.restore_worker.failed.connect(self._on_auth_failed)
        self.restore_worker.start()

    def _on_auth_ready(self, auth, nickname: str):
        self.auth = auth
        self.xhs_nickname = nickname
        self.set_xhs_state(True, nickname)
        self.append_log('小红书会话已恢复（Cookie 有效）')

    def _on_auth_failed(self, message: str):
        self.auth = None
        paths.clear_xhs_cookie()
        self.set_xhs_state(False, '')
        self.append_log(f'小红书会话已失效，请重新扫码登录（{message}）')

    def set_xhs_state(self, logged_in: bool, nickname: str):
        if logged_in:
            text = nickname or '已登录'
            self.xhs_label.setText(f'小红书：{text}')
            self.xhs_label.setObjectName('xhsStateOk')
            self.xhs_login_btn.setText('切换账号')
            self.stat_xhs.setText(text)
            self.xhs_item.setText(f'小红书 · {text}')
            self.xhs_item.setIcon(line_icon('user', '#2fbf8f'))
        else:
            self.xhs_label.setText('小红书：未登录')
            self.xhs_label.setObjectName('xhsStateBad')
            self.xhs_login_btn.setText('扫码登录小红书')
            self.stat_xhs.setText('未登录')
            self.xhs_item.setText('小红书 · 未登录')
            self.xhs_item.setIcon(line_icon('user'))
        self._restyle(self.xhs_label)
        self.xhs_label.setStyleSheet('')

    def _restyle(self, widget: QWidget):
        style = widget.style()
        style.unpolish(widget)
        style.polish(widget)

    def forget_xhs_session(self):
        self.auth = None
        paths.clear_xhs_cookie()
        self.set_xhs_state(False, '')
        self.append_log('已清除小红书登录状态')

    def open_xhs_login(self):
        dialog = XhsLoginDialog(self)
        if dialog.exec():
            self.append_log('扫码登录成功，正在恢复会话…')
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
                'prompt': self.config.get('ai_prompt') or '',
            }
        spec = TaskSpec(
            save_images=self.img_check.isChecked(),
            save_videos=self.video_check.isChecked(),
            save_excel=self.excel_check.isChecked(),
            zip_export=self.zip_check.isChecked(),
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
        else:
            spec.mode = 'user'
            spec.user_url = self.user_edit.text().strip()
            if not spec.task_name:
                tail = spec.user_url.split('/')[-1].split('?')[0]
                spec.task_name = f'用户{tail}' if tail else '主页采集'
        return spec

    def _validate_spec(self, spec: TaskSpec) -> str:
        if self.auth is None:
            return '请先扫码登录小红书'
        if spec.mode == 'search' and not spec.query:
            return '请输入搜索关键词'
        if spec.mode == 'user' and not spec.user_url:
            return '请输入用户主页链接'
        if spec.mode == 'urls' and not spec.note_urls:
            return '请至少填写一个笔记链接'
        if not os.path.isdir(spec.output_dir):
            return '输出目录不存在，请重新选择'
        if not (spec.save_images or spec.save_videos or spec.save_excel or spec.zip_export):
            return '请至少选择一种保存内容'
        if spec.ai_cfg and not spec.ai_cfg.get('key'):
            return '已勾选 AI改写，请先在「设置」页配置 API Key'
        return ''

    def start_collection(self):
        spec = self._current_spec()
        error = self._validate_spec(spec)
        if error:
            QMessageBox.warning(self, '无法开始', error)
            return

        self.config['output_dir'] = spec.output_dir
        paths.save_config(self.config)

        self.table.setRowCount(0)
        self.stat_count.setText('0 篇')
        self.progress_bar.setValue(0)
        self.stage_label.setText('')
        self.run_btn.setText('停止')
        self.tabs.setEnabled(False)

        self.collect_worker = _CollectWorker(self.auth, spec, self)
        self.collect_worker.note.connect(self.add_note_row)
        self.collect_worker.progress.connect(self.on_progress)
        self.collect_worker.logline.connect(self.append_log)
        self.collect_worker.done.connect(self.on_done)
        self.collect_worker.failed.connect(self.on_failed)
        self.collect_worker.start()

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
        self.stage_label.setText('完成')
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
            self.output_edit.setText(chosen)
            self.settings_output_edit.setText(chosen)

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
        ):
            if worker is not None and worker.isRunning():
                worker.wait(2500)
        try:
            if self._log_sink_id is not None:
                logger.remove(self._log_sink_id)
                self._log_sink_id = None
        except Exception:
            pass
        super().closeEvent(event)
