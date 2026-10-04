# encoding: utf-8
"""添加对标账号的小对话框：粘贴对方主页链接或 user_id。

项目里没有 QInputDialog 那种原生灰框的使用先例（登录类都用自绘 QDialog），
这里沿用 login_dialog / xhs_login_dialog 的卡片式风格。
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QLabel,
    QLineEdit,
    QPushButton,
    QHBoxLayout,
    QVBoxLayout,
)

from desktop import watchlist


class WatchAddDialog(QDialog):
    """只负责收集输入 + 本地格式校验；真正的资料拉取交给 worker。"""

    def __init__(self, existing_ids=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle('添加对标账号')
        self.setModal(True)
        self.setFixedWidth(420)
        self.user_id = ''
        self._existing = {str(i) for i in (existing_ids or [])}

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 18)
        root.setSpacing(0)

        title = QLabel('添加对标账号')
        title.setObjectName('greetTitle')
        root.addWidget(title)
        root.addSpacing(4)
        hint = QLabel('粘贴对方的小红书主页链接，或对方 user_id')
        hint.setObjectName('mutedLabel')
        hint.setWordWrap(True)
        root.addWidget(hint)
        root.addSpacing(14)

        self.input_edit = QLineEdit()
        self.input_edit.setPlaceholderText(
            'https://www.xiaohongshu.com/user/profile/xxxxxxxx')
        self.input_edit.setMinimumHeight(40)
        self.input_edit.returnPressed.connect(self.do_accept)
        root.addWidget(self.input_edit)
        root.addSpacing(8)

        self.status = QLabel(' ')
        self.status.setWordWrap(True)
        self.status.setObjectName('mutedLabel')
        root.addWidget(self.status)
        root.addSpacing(10)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel_btn = QPushButton('取消')
        cancel_btn.setObjectName('softBtn')
        cancel_btn.setCursor(Qt.PointingHandCursor)
        cancel_btn.clicked.connect(self.reject)
        buttons.addWidget(cancel_btn)
        self.ok_btn = QPushButton('添加')
        self.ok_btn.setObjectName('primaryBtn')
        self.ok_btn.setCursor(Qt.PointingHandCursor)
        self.ok_btn.setDefault(True)
        self.ok_btn.clicked.connect(self.do_accept)
        buttons.addWidget(self.ok_btn)
        root.addLayout(buttons)

        self.input_edit.setFocus()
        if existing_ids:
            hint.setText(f'粘贴对方的小红书主页链接，或对方 user_id（已添加 {len(existing_ids)} 个）')

    def do_accept(self):
        text = self.input_edit.text()
        user_id = watchlist.parse_target(text)
        if not user_id:
            self.status.setText('识别不出 user_id：请粘贴主页链接（形如 …/user/profile/xxxx），或直接填 24 位 user_id')
            return
        if user_id in self._existing:
            self.status.setText('这个账号已经在清单里了')
            return
        self.user_id = user_id
        self.accept()
