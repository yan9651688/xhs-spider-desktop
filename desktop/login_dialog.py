# encoding: utf-8
"""软件账号登录对话框：对接 xiao 的 /api/xhs/login。"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from desktop import paths
from desktop.auth_client import AuthClient, AuthError


class LoginDialog(QDialog):
    def __init__(self, config: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle('登录 - 小红书采集工具')
        self.setModal(True)
        self.setMinimumWidth(440)
        self.session = None
        self.config = dict(config)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 20)
        layout.setSpacing(14)

        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        title = QLabel('小红书采集工具')
        title.setObjectName('appTitle')
        title.setAlignment(Qt.AlignCenter)
        subtitle = QLabel('账号登录')
        subtitle.setObjectName('appSubTitle')
        subtitle.setAlignment(Qt.AlignCenter)
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        layout.addLayout(title_box)
        layout.addSpacing(6)

        form = QFormLayout()
        self.user_edit = QLineEdit(config.get('username') or '')
        self.user_edit.setFocus()
        self.pwd_edit = QLineEdit()
        self.pwd_edit.setEchoMode(QLineEdit.Password)
        form.addRow('账号', self.user_edit)
        form.addRow('密码', self.pwd_edit)
        layout.addLayout(form)

        self.status = QLabel('')
        self.status.setWordWrap(True)
        self.status.setStyleSheet('color:#c62828;')
        layout.addWidget(self.status)

        self.login_btn = QPushButton('登 录')
        self.login_btn.setObjectName('primaryBtn')
        self.login_btn.setMinimumHeight(36)
        self.login_btn.setDefault(True)
        self.login_btn.clicked.connect(self.do_login)
        layout.addWidget(self.login_btn)

        self.pwd_edit.returnPressed.connect(self.do_login)

    def do_login(self):
        # 服务器对客户不可见：内置线上地址；开发联调可用环境变量 XHS_SERVER 覆盖
        import os
        server = os.environ.get('XHS_SERVER') or paths.DEFAULT_SERVER
        username = self.user_edit.text().strip()
        password = self.pwd_edit.text()
        if not username or not password:
            self.status.setText('请输入账号和密码')
            return

        self.login_btn.setEnabled(False)
        self.status.setStyleSheet('color:#666;')
        self.status.setText('正在登录…')
        QApplication_processEvents()
        try:
            session = AuthClient(server).login(username, password)
        except AuthError as exc:
            self.status.setStyleSheet('color:#c62828;')
            self.status.setText(str(exc))
            self.login_btn.setEnabled(True)
            return
        except Exception as exc:  # 兜底，避免按钮卡死
            self.status.setStyleSheet('color:#c62828;')
            self.status.setText(f'登录异常：{exc}')
            self.login_btn.setEnabled(True)
            return

        self.config['server'] = server
        self.config['username'] = username
        paths.save_config(self.config)
        paths.save_session(session)
        self.session = session
        self.accept()


def QApplication_processEvents():
    from PySide6.QtWidgets import QApplication
    QApplication.processEvents()
