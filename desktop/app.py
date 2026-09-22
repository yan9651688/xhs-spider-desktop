# encoding: utf-8
"""桌面端应用入口：Node 运行时探测、登录流程与主窗口装配。"""
from __future__ import annotations

import os
import shutil
import sys


def ensure_node_runtime() -> bool:
    """确保签名用的 node 可被找到。

    优先使用打包进来的 node_dist/bin，其次补充常见安装路径
    （Finder 启动的 .app 不继承 shell 的 PATH）。
    """
    candidates = []
    if getattr(sys, 'frozen', False):
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        candidates.append(os.path.join(exe_dir, 'node_dist', 'bin'))
        meipass = getattr(sys, '_MEIPASS', None)
        if meipass:
            candidates.append(os.path.join(meipass, 'node_dist', 'bin'))
    candidates += [
        '/opt/homebrew/bin',
        '/usr/local/bin',
        os.path.expanduser('~/.local/opt/node/bin'),
        os.path.expanduser('~/node/bin'),
    ]

    path = os.environ.get('PATH', '')
    for candidate in candidates:
        if candidate and os.path.isdir(candidate) and candidate not in path.split(os.pathsep):
            path = candidate + os.pathsep + path
    os.environ['PATH'] = path
    return shutil.which('node') is not None


def _ensure_check_icon() -> str:
    """生成紫色对勾指示图（QSS 的 image:url 需要真实文件路径）。"""
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QColor, QPainter, QPen, QPixmap

    path = os.path.join(os.path.expanduser('~'), '.xhs_spider', 'check.png')
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        pixmap = QPixmap(16, 16)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        try:
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor('#6c5ce7'))
            painter.drawRoundedRect(0, 0, 16, 16, 5, 5)
            painter.setPen(QPen(QColor('#ffffff'), 2.0, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            painter.setBrush(Qt.NoBrush)
            painter.drawPolyline([QPointF(3.5, 8.5), QPointF(6.8, 11.8), QPointF(12.5, 4.5)])
        finally:
            painter.end()
        pixmap.save(path, 'PNG')
    except Exception:
        return ''
    return path


STYLE = """
/* ============ 全局 ============ */
QWidget {
    font-family: -apple-system, 'PingFang SC', 'Microsoft YaHei', 'Helvetica Neue', sans-serif;
    font-size: 13px;
    color: #2d3436;
    background-color: #eef0f4;
}
QMainWindow, QDialog { background-color: #eef0f4; }

/* ============ 输入控件 ============ */
QLineEdit, QPlainTextEdit, QSpinBox, QComboBox {
    background-color: #ffffff;
    border: 1px solid #e4e7ee;
    border-radius: 8px;
    padding: 7px 10px;
    selection-background-color: #6c5ce7;
    selection-color: #ffffff;
}
QLineEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QComboBox:focus {
    border: 1px solid #6c5ce7;
}
QLineEdit:read-only { color: #9aa0a6; background-color: #f7f8fa; }
QComboBox::drop-down { border: none; width: 22px; }
QComboBox QAbstractItemView {
    background-color: #ffffff;
    border: 1px solid #edeff3;
    border-radius: 8px;
    selection-background-color: #eeecfb;
    selection-color: #5b4bd0;
    outline: none;
}
QSpinBox::up-button, QSpinBox::down-button { width: 18px; border: none; background: transparent; }

/* ============ 按钮 ============ */
QPushButton {
    background-color: #ffffff;
    border: 1px solid #e4e7ee;
    border-radius: 8px;
    padding: 7px 14px;
    color: #2d3436;
}
QPushButton:hover { border-color: #6c5ce7; color: #5b4bd0; }
QPushButton:pressed { background-color: #f4f2fd; }
QPushButton:disabled { color: #b9bec7; border-color: #eef0f3; }
QPushButton#primaryBtn {
    background-color: #6c5ce7;
    border: none;
    color: #ffffff;
    font-weight: 600;
}
QPushButton#primaryBtn:hover { background-color: #7d6ef0; color: #ffffff; }
QPushButton#primaryBtn:pressed { background-color: #5a4bd0; }
QPushButton#primaryBtn:disabled { background-color: #c7c0f2; color: #ffffff; }
QPushButton#softBtn {
    background-color: #f4f5f9;
    border: none;
    color: #555b66;
}
QPushButton#softBtn:hover { background-color: #eceef5; color: #5b4bd0; }
QPushButton#dangerBtn {
    background-color: #fff0ee;
    border: none;
    color: #e05a4e;
}
QPushButton#dangerBtn:hover { background-color: #ffe4e1; }
QPushButton#ghostBtn {
    background: transparent;
    border: none;
    color: #9aa0a6;
    padding: 4px 8px;
}
QPushButton#ghostBtn:hover { color: #e05a4e; }

/* ============ 侧边栏 ============ */
QWidget#sidebar { background-color: #ffffff; border-right: 1px solid #edeff3; }
QWidget#sidebar QLabel, QWidget#sidebar QPushButton { background: transparent; }
QLabel#logoTile {
    background-color: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 #ff8a5c, stop:0.55 #6c5ce7, stop:1 #4ec9d4);
    border-radius: 10px;
    color: #ffffff;
    font-size: 16px;
    font-weight: 700;
}
QLabel#logoName { font-size: 15px; font-weight: 700; color: #2d3436; }
QLabel#logoSub { font-size: 10px; color: #9aa0a6; }
QPushButton#navItem {
    border: none;
    border-radius: 9px;
    padding: 9px 12px;
    text-align: left;
    color: #6b7180;
    font-weight: 500;
}
QPushButton#navItem:hover { background-color: #f5f4fc; color: #5b4bd0; }
QPushButton#navItem:checked {
    background-color: #eeecfb;
    color: #5b4bd0;
    font-weight: 600;
}
QPushButton#navSubItem {
    border: none;
    border-radius: 8px;
    padding: 7px 10px;
    text-align: left;
    color: #6b7180;
    font-size: 12px;
}
QPushButton#navSubItem:hover { background-color: #f5f4fc; color: #5b4bd0; }
QLabel#accountDot { background-color: #2fbf8f; border-radius: 3px; }
QLabel#accountName { font-size: 12px; color: #555b66; }
QLabel#navSection {
    color: #9aa0a6;
    font-size: 11px;
    font-weight: 700;
    padding: 2px 4px;
    letter-spacing: 1px;
}
QLabel#soonBadge {
    background-color: #eeecfb;
    color: #6c5ce7;
    font-size: 10px;
    font-weight: 700;
    border-radius: 9px;
    padding: 2px 0px;
}
QFrame#userCard {
    background-color: #f7f7fc;
    border: 1px solid #eef0f6;
    border-radius: 12px;
}
QFrame#userCard QLabel { background: transparent; }
QLabel#avatar {
    background-color: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #7d6ef0, stop:1 #5a4bd0);
    border-radius: 15px;
    color: #ffffff;
    font-weight: 700;
    font-size: 13px;
}
QLabel#userName { font-size: 12px; font-weight: 600; color: #2d3436; }
QLabel#userMeta { font-size: 10px; color: #9aa0a6; }

/* ============ 主区卡片 ============ */
QFrame#card {
    background-color: #ffffff;
    border: 1px solid #edeff3;
    border-radius: 14px;
}
QFrame#card QLabel { background: transparent; }
QLabel#greetTitle { font-size: 19px; font-weight: 700; color: #22262b; }
QLabel#greetSub { font-size: 12px; color: #9aa0a6; }
QLabel#cardTitle { font-size: 13px; font-weight: 700; color: #2d3436; }
QLabel#mutedLabel { color: #9aa0a6; font-size: 12px; }
QLabel#xhsState { color: #e05a4e; font-size: 12px; font-weight: 600; }
QLabel#xhsStateOk { color: #2fbf8f; font-size: 12px; font-weight: 600; }
QLabel#xhsStateBad { color: #e05a4e; font-size: 12px; font-weight: 600; }

/* ============ 渐变统计卡 ============ */
QFrame#statCard1, QFrame#statCard2, QFrame#statCard3 {
    border: none;
    border-radius: 14px;
}
QFrame#statCard1 {
    background-color: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 #8a7bf5, stop:0.6 #6c5ce7, stop:1 #f9a03f);
}
QFrame#statCard2 {
    background-color: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 #7b6cf0, stop:0.75 #5f4fd8, stop:1 #7d6ef0);
}
QFrame#statCard3 {
    background-color: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 #6c5ce7, stop:0.6 #5a4bd0, stop:1 #4ec9d4);
}
QFrame#statCard1 QLabel, QFrame#statCard2 QLabel, QFrame#statCard3 QLabel {
    color: #ffffff;
    background: transparent;
}
QLabel#statLabel { font-size: 11px; color: rgba(255,255,255,0.78); }
QLabel#statValue { font-size: 19px; font-weight: 700; }

/* ============ 页签 ============ */
QTabWidget#collectTabs::pane {
    border: none;
    border-top: 1px solid #f0f1f5;
    border-radius: 0;
    background: transparent;
}
QTabWidget#collectTabs > QWidget { background: transparent; }
QTabBar { background: transparent; }
QTabBar::tab {
    padding: 10px 22px;
    background: transparent;
    color: #8a8f98;
    border-bottom: 2px solid transparent;
    margin-right: 4px;
    font-weight: 500;
}
QTabBar::tab:hover { color: #5b4bd0; }
QTabBar::tab:selected {
    color: #5b4bd0;
    border-bottom: 2px solid #6c5ce7;
    font-weight: 700;
}

/* ============ 表格 ============ */
QTableWidget#resultTable {
    background-color: #ffffff;
    border: 1px solid #edeff3;
    border-radius: 14px;
    gridline-color: #f4f5f8;
    alternate-background-color: #fafbfd;
}
QTableWidget#resultTable::item { padding: 6px 6px; }
QTableWidget#resultTable::item:selected { background-color: #eeecfb; color: #2d3436; }
QHeaderView::section {
    background-color: #ffffff;
    border: none;
    border-bottom: 1px solid #f0f1f5;
    padding: 9px 8px;
    color: #9aa0a6;
    font-weight: 600;
    font-size: 12px;
}

/* ============ 日志 / 进度 ============ */
QPlainTextEdit#logView {
    background-color: #fafbfd;
    border: 1px solid #f0f1f5;
    border-radius: 10px;
    font-family: 'Menlo', 'SF Mono', 'Consolas', monospace;
    font-size: 11px;
    color: #6b7180;
}
QProgressBar {
    border: none;
    border-radius: 4px;
    background-color: #edeff3;
    text-align: center;
    color: #8a8f98;
    font-size: 11px;
    min-height: 12px;
}
QProgressBar::chunk { background-color: #6c5ce7; border-radius: 4px; }

/* ============ 复选框 ============ */
QCheckBox { spacing: 6px; color: #555b66; }
QCheckBox::indicator {
    width: 16px; height: 16px;
    border: 1px solid #d5d9e2;
    border-radius: 5px;
    background-color: #ffffff;
}
QCheckBox::indicator:checked {
    background-color: #6c5ce7;
    border-color: #6c5ce7;
    image: url(__CHECK_PNG__);
}
QCheckBox::indicator:hover { border-color: #6c5ce7; }

/* ============ 滚动条 ============ */
QScrollBar:vertical { background: transparent; width: 9px; margin: 2px; }
QScrollBar::handle:vertical { background: #dcdfe6; border-radius: 4px; min-height: 30px; }
QScrollBar::handle:vertical:hover { background: #6c5ce7; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; }
QScrollBar:horizontal { background: transparent; height: 9px; margin: 2px; }
QScrollBar::handle:horizontal { background: #dcdfe6; border-radius: 4px; min-width: 30px; }
QScrollBar::handle:horizontal:hover { background: #6c5ce7; }

/* ============ 对话框（登录 / 扫码） ============ */
QLabel#appTitle { font-size: 20px; font-weight: 700; color: #22262b; }
QLabel#appSubTitle { font-size: 12px; color: #9aa0a6; }
QLabel#qrCard {
    background-color: #ffffff;
    border: 1px solid #edeff3;
    border-radius: 14px;
}
"""


def main(argv=None) -> int:
    argv = list(sys.argv[1:]) if argv is None else argv

    from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

    from desktop import paths
    from desktop.auth_client import AuthClient, AuthError
    from desktop.login_dialog import LoginDialog
    from desktop.main_window import MainWindow

    paths.ensure_app_dir()
    node_ok = ensure_node_runtime()

    app = QApplication(argv)
    app.setApplicationName('小红书采集工具')
    app.setOrganizationName('xhs-spider')
    check_png = _ensure_check_icon()
    app.setStyleSheet(STYLE.replace('__CHECK_PNG__', check_png.replace('\\', '/')))

    if not node_ok:
        QMessageBox.warning(
            None, '缺少 Node.js',
            '未检测到 Node.js（签名模块依赖它）。\n\n'
            '请安装 Node.js 20+ 后重试：\n'
            '  macOS:  brew install node\n'
            '  Windows: https://nodejs.org/ 下载安装\n\n'
            '缺少 Node 时扫码登录与采集将不可用。',
        )

    config = paths.load_config()
    session = paths.load_session()

    window = None
    if session and session.get('token') and config.get('server'):
        try:
            AuthClient(config['server']).check(session['token'])
            window = MainWindow(config, session)
            window.show()
        except AuthError:
            paths.clear_session()
            session = None
        except Exception:
            session = None

    if window is None:
        dialog = LoginDialog(config)
        if dialog.exec() != QDialog.Accepted:
            return 0
        config, session = dialog.config, dialog.session
        window = MainWindow(config, session)
        window.show()

    return app.exec()
