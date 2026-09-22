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


STYLE = """
QWidget { font-size: 13px; }
QLineEdit, QPlainTextEdit, QSpinBox, QComboBox { padding: 4px 6px; }
QPushButton { padding: 6px 14px; }
QPushButton:disabled { color: #999; }
QGroupBox { font-weight: 600; margin-top: 10px; }
QTabWidget::pane { border: 1px solid #ddd; border-radius: 4px; }
QProgressBar { border: 1px solid #ccc; border-radius: 4px; text-align: center; padding: 2px; }
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
    app.setStyleSheet(STYLE)

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
