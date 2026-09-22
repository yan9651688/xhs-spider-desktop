# encoding: utf-8
"""软件账号登录对话框：居中卡片式（YC 品牌色），对接 xiao 的 /api/xhs/login。"""
from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from desktop import paths
from desktop.auth_client import AuthClient, AuthError


class LoginDialog(QDialog):
    def __init__(self, config: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle('登录 - 小红书采集工具')
        self.setModal(True)
        self.setFixedWidth(400)
        self.session = None
        self.config = dict(config)

        root = QVBoxLayout(self)
        root.setContentsMargins(36, 34, 36, 28)
        root.setSpacing(0)

        # 品牌区：YC 渐变徽标 + 标题
        from desktop.main_window import gradient_tile

        logo = QLabel()
        logo.setPixmap(gradient_tile('YC', ['#ff8a5c', '#6c5ce7', '#4ec9d4'],
                                     size=58, radius=16, font_size=22))
        logo.setAlignment(Qt.AlignCenter)
        root.addWidget(logo)
        root.addSpacing(16)

        title = QLabel('小红书采集工具')
        title.setObjectName('appTitle')
        title.setAlignment(Qt.AlignCenter)
        root.addWidget(title)
        subtitle = QLabel('请使用您的账号登录')
        subtitle.setObjectName('appSubTitle')
        subtitle.setAlignment(Qt.AlignCenter)
        root.addWidget(subtitle)
        root.addSpacing(24)

        # 表单区：通栏输入框，无侧标签
        self.user_edit = QLineEdit()
        self.user_edit.setPlaceholderText('账号')
        self.user_edit.setMinimumHeight(42)
        self.user_edit.setText(config.get('username') or '')
        root.addWidget(self.user_edit)
        root.addSpacing(12)

        self.pwd_edit = QLineEdit()
        self.pwd_edit.setPlaceholderText('密码')
        self.pwd_edit.setEchoMode(QLineEdit.Password)
        self.pwd_edit.setMinimumHeight(42)
        root.addWidget(self.pwd_edit)
        root.addSpacing(20)

        self.login_btn = QPushButton('登 录')
        self.login_btn.setObjectName('primaryBtn')
        self.login_btn.setMinimumHeight(44)
        self.login_btn.setCursor(Qt.PointingHandCursor)
        self.login_btn.setDefault(True)
        self.login_btn.clicked.connect(self.do_login)
        root.addWidget(self.login_btn)
        root.addSpacing(14)

        self.status = QLabel(' ')
        self.status.setWordWrap(True)
        self.status.setAlignment(Qt.AlignCenter)
        self.status.setStyleSheet('color:#c62828; font-size:12px; background:transparent;')
        root.addWidget(self.status)
        root.addStretch(1)

        self.user_edit.setFocus()
        self.pwd_edit.returnPressed.connect(self.do_login)
        self.user_edit.returnPressed.connect(self.pwd_edit.setFocus)

    def showEvent(self, event):
        """每次显示都在屏幕正中弹出。"""
        super().showEvent(event)
        self.adjustSize()
        screen = self.screen() or QApplication.primaryScreen()
        geometry = screen.availableGeometry()
        self.move(geometry.center().x() - self.width() // 2,
                  geometry.center().y() - self.height() // 2 - 20)

    def do_login(self):
        # 服务器对客户不可见：内置线上地址；开发联调可用环境变量 XHS_SERVER 覆盖
        server = os.environ.get('XHS_SERVER') or paths.DEFAULT_SERVER
        username = self.user_edit.text().strip()
        password = self.pwd_edit.text()
        if not username or not password:
            self.status.setText('请输入账号和密码')
            return

        self.login_btn.setEnabled(False)
        self.status.setStyleSheet('color:#9aa0a6; font-size:12px; background:transparent;')
        self.status.setText('正在登录…')
        QApplication.processEvents()
        try:
            session = AuthClient(server).login(username, password)
        except AuthError as exc:
            self.status.setStyleSheet('color:#c62828; font-size:12px; background:transparent;')
            self.status.setText(str(exc))
            self.login_btn.setEnabled(True)
            return
        except Exception as exc:  # 兜底，避免按钮卡死
            self.status.setStyleSheet('color:#c62828; font-size:12px; background:transparent;')
            self.status.setText(f'登录异常：{exc}')
            self.login_btn.setEnabled(True)
            return

        self.config['server'] = server
        self.config['username'] = username
        paths.save_config(self.config)
        paths.save_session(session)
        self.session = session
        self.accept()
