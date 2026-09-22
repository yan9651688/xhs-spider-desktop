# encoding: utf-8
"""小红书扫码登录对话框：复用 XHSPcAuth.from_qrcode_login，二维码显示在窗口内。"""
from __future__ import annotations

import io

import qrcode
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from desktop import paths

# 正在运行的登录线程注册表，防止对话框关闭后被 GC 中断
_ACTIVE_THREADS = set()


class QrLoginThread(QThread):
    qr_ready = Signal(str)
    succeeded = Signal(str)
    failed = Signal(str)

    def run(self):
        try:
            from xhs_utils.xhs_pc import XHSPcAuth

            auth = XHSPcAuth.from_qrcode_login(
                show_in_terminal=False,
                qr_callback=self.qr_ready.emit,
            )
        except Exception as exc:  # 二维码过期/网络失败/初始化失败
            self.failed.emit(f'扫码登录失败：{exc}')
            return
        cookie = ''
        try:
            cookie = auth.cookies
            if cookie:
                paths.save_xhs_cookie(cookie)
        finally:
            try:
                auth.close()
            except Exception:
                pass
        if cookie:
            self.succeeded.emit(cookie)
        else:
            self.failed.emit('扫码登录未返回有效 Cookie')


class XhsLoginDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('小红书扫码登录')
        self.setModal(True)
        self.setMinimumWidth(380)
        self.thread = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 16)
        layout.setSpacing(12)
        self.qr_label = QLabel('点击下方按钮生成二维码')
        self.qr_label.setObjectName('qrCard')
        self.qr_label.setAttribute(Qt.WA_StyledBackground, True)
        self.qr_label.setAlignment(Qt.AlignCenter)
        self.qr_label.setMinimumSize(320, 320)
        layout.addWidget(self.qr_label)

        self.hint = QLabel('用小红书 App 首页左上角「扫一扫」扫码，并在手机上确认登录。')
        self.hint.setWordWrap(True)
        self.hint.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.hint)

        self.status = QLabel('')
        self.status.setWordWrap(True)
        self.status.setAlignment(Qt.AlignCenter)
        self.status.setStyleSheet('color:#666;')
        layout.addWidget(self.status)

        buttons = QHBoxLayout()
        self.start_btn = QPushButton('开始扫码 / 刷新二维码')
        self.start_btn.setObjectName('primaryBtn')
        self.start_btn.clicked.connect(self.start)
        self.cancel_btn = QPushButton('取消')
        self.cancel_btn.clicked.connect(self.reject)
        buttons.addWidget(self.start_btn)
        buttons.addWidget(self.cancel_btn)
        layout.addLayout(buttons)

    def start(self):
        if self.thread is not None and self.thread.isRunning():
            return
        self.status.setText('正在初始化设备并获取二维码…')
        self.start_btn.setEnabled(False)
        self.thread = QrLoginThread(self)
        _ACTIVE_THREADS.add(self.thread)
        self.thread.finished.connect(lambda t=self.thread: _ACTIVE_THREADS.discard(t))
        self.thread.qr_ready.connect(self.show_qr)
        self.thread.succeeded.connect(self._on_success)
        self.thread.failed.connect(self._on_failed)
        self.thread.start()

    def show_qr(self, url: str):
        try:
            img = qrcode.make(url)
            buf = io.BytesIO()
            img.save(buf, 'PNG')
            buf.seek(0)
            pixmap = QPixmap()
            pixmap.loadFromData(buf.read())
            self.qr_label.setPixmap(
                pixmap.scaled(
                    300, 300,
                    Qt.KeepAspectRatio, Qt.SmoothTransformation,
                )
            )
            self.status.setText('请使用小红书 App 扫码，扫码后请在手机上确认')
            self.status.setStyleSheet('color:#6b7180;')
        except Exception as exc:
            self._on_failed(f'二维码渲染失败：{exc}')

    def _on_success(self, cookie: str):
        self.accept()

    def _on_failed(self, message: str):
        self.status.setText(message)
        self.status.setStyleSheet('color:#c62828;')
        self.start_btn.setEnabled(True)

    def closeEvent(self, event):
        # 线程已注册到 _ACTIVE_THREADS，允许其在后台自然结束
        super().closeEvent(event)
