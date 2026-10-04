# encoding: utf-8
"""小红书登录对话框：扫码登录 / Cookie 导入两种方式，都复用 XHSPcAuth。"""
from __future__ import annotations

import io

import qrcode
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from desktop import paths

# 正在运行的登录线程注册表，防止对话框关闭后被 GC 中断
_ACTIVE_THREADS = set()

# Cookie 导入时必填的两个字段：a1 参与签名，web_session 是登录态本身
_REQUIRED_COOKIE_FIELDS = ('a1', 'web_session')


def normalize_cookie_text(text: str) -> str:
    """把粘贴内容整理成 `k=v; k=v` 形式。

    容忍客户直接粘贴浏览器请求头里的整行 `Cookie: a1=...; web_session=...`，
    以及换行/制表符分隔的多行格式。
    """
    text = str(text or '').strip()
    if not text:
        return ''
    for prefix in ('cookie:', 'Cookie:'):
        if text.startswith(prefix):
            text = text[len(prefix):]
            break
    parts = []
    for chunk in text.replace('\t', '\n').replace('\r', '\n').split('\n'):
        parts.extend(chunk.split(';'))
    return '; '.join(p.strip() for p in parts if p.strip())


def parse_cookie_map(cookie: str) -> dict:
    result = {}
    for part in str(cookie or '').split(';'):
        key, sep, value = part.strip().partition('=')
        if sep and key.strip():
            result[key.strip()] = value
    return result


def missing_cookie_fields(cookie: str) -> list:
    """返回缺失的必填字段名（空列表 = 通过）。"""
    cookie_map = parse_cookie_map(cookie)
    return [name for name in _REQUIRED_COOKIE_FIELDS if not cookie_map.get(name)]


def is_valid_cookie(cookie: str) -> bool:
    return not missing_cookie_fields(cookie)


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
            # exc 已由 qrcode_login 填好具体原因（如"二维码已过期…"），直接展示
            self.failed.emit(str(exc))
            return
        cookie = ''
        try:
            cookie = auth.cookies
            if cookie:
                paths.add_xhs_cookie(cookie)
        finally:
            try:
                auth.close()
            except Exception:
                pass
        if cookie:
            self.succeeded.emit(cookie)
        else:
            self.failed.emit('扫码登录未返回有效 Cookie')


class CookieLoginThread(QThread):
    """导入 Cookie：先本地校验字段，再联网 bootstrap 确认登录态真的有效。"""

    succeeded = Signal(str, str)     # cookie, nickname
    failed = Signal(str)

    def __init__(self, cookie: str, parent=None):
        super().__init__(parent)
        self.cookie = cookie

    def run(self):
        try:
            from apis.xhs_pc_apis import XHS_Apis
            from xhs_utils.xhs_pc import XHSPcAuth
        except Exception as exc:
            self.failed.emit(f'加载登录模块失败：{exc}')
            return
        nickname = ''
        try:
            auth = XHSPcAuth.from_cookie(self.cookie)
            try:
                success, _msg, res = XHS_Apis(auth).get_user_me()
                if success and res:
                    data = res.get('data') or {}
                    nickname = data.get('nickname') or ''
                    # guest=True 表示 web_session 已失效，接口却仍可能返回 200
                    if data.get('guest') is True:
                        self.failed.emit('该 Cookie 已失效（未登录状态），请重新登录小红书后重新复制')
                        return
            except Exception:
                pass
            finally:
                try:
                    auth.close()
                except Exception:
                    pass
        except ValueError as exc:
            self.failed.emit(f'Cookie 无效：{exc}')
            return
        except Exception as exc:
            self.failed.emit(f'Cookie 校验失败：{str(exc)[:120]}')
            return
        paths.add_xhs_cookie(self.cookie, nickname)
        self.succeeded.emit(self.cookie, nickname)


class XhsLoginDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('小红书登录')
        self.setModal(True)
        self.setMinimumWidth(400)
        self.thread = None
        self.cookie_thread = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 14)
        layout.setSpacing(12)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_qr_tab(), '扫码登录')
        self.tabs.addTab(self._build_cookie_tab(), 'Cookie 导入')
        layout.addWidget(self.tabs)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.cancel_btn = QPushButton('取消')
        self.cancel_btn.clicked.connect(self.reject)
        buttons.addWidget(self.cancel_btn)
        layout.addLayout(buttons)

    # ---------- 扫码登录页 ----------

    def _build_qr_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(4, 12, 4, 4)
        layout.setSpacing(10)

        self.qr_label = QLabel('点击下方按钮生成二维码')
        self.qr_label.setObjectName('qrCard')
        self.qr_label.setAttribute(Qt.WA_StyledBackground, True)
        self.qr_label.setAlignment(Qt.AlignCenter)
        self.qr_label.setMinimumSize(320, 320)
        layout.addWidget(self.qr_label, 0, Qt.AlignHCenter)

        self.hint = QLabel('用小红书 App 首页左上角「扫一扫」扫码，并在手机上确认登录。')
        self.hint.setWordWrap(True)
        self.hint.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.hint)

        self.status = QLabel('')
        self.status.setWordWrap(True)
        self.status.setAlignment(Qt.AlignCenter)
        self.status.setStyleSheet('color:#666;')
        layout.addWidget(self.status)

        self.start_btn = QPushButton('开始扫码 / 刷新二维码')
        self.start_btn.setObjectName('primaryBtn')
        self.start_btn.setCursor(Qt.PointingHandCursor)
        self.start_btn.clicked.connect(self.start)
        layout.addWidget(self.start_btn)
        layout.addStretch(1)
        return tab

    # ---------- Cookie 导入页 ----------

    def _build_cookie_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(4, 12, 4, 4)
        layout.setSpacing(10)

        tip = QLabel(
            '在浏览器登录小红书后，按 F12 打开开发者工具 → Network，'
            '任意挑一个请求，复制请求头里的整行 Cookie 粘贴到下面。\n'
            '必须包含 a1 与 web_session 两个字段。'
        )
        tip.setWordWrap(True)
        tip.setObjectName('mutedLabel')
        layout.addWidget(tip)

        self.cookie_edit = QPlainTextEdit()
        self.cookie_edit.setPlaceholderText(
            'a1=xxxx; web_session=xxxx; ...（可整行粘贴，含 "Cookie:" 前缀也能识别）'
        )
        self.cookie_edit.setMinimumHeight(150)
        layout.addWidget(self.cookie_edit, 1)

        self.cookie_status = QLabel('')
        self.cookie_status.setWordWrap(True)
        self.cookie_status.setStyleSheet('color:#666;')
        layout.addWidget(self.cookie_status)

        self.cookie_btn = QPushButton('导入并验证')
        self.cookie_btn.setObjectName('primaryBtn')
        self.cookie_btn.setCursor(Qt.PointingHandCursor)
        self.cookie_btn.clicked.connect(self.start_cookie_login)
        layout.addWidget(self.cookie_btn)
        return tab

    # ---------- 扫码 ----------

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

    # ---------- Cookie 导入 ----------

    def start_cookie_login(self):
        if self.cookie_thread is not None and self.cookie_thread.isRunning():
            return
        cookie = normalize_cookie_text(self.cookie_edit.toPlainText())
        if not cookie:
            self._on_cookie_failed('请先粘贴小红书 Cookie')
            return
        missing = missing_cookie_fields(cookie)
        if missing:
            self._on_cookie_failed(
                f'Cookie 缺少必要字段：{"、".join(missing)}。'
                '请确认复制的是登录后的完整 Cookie（web_session 是 HttpOnly，'
                '必须从开发者工具 Network 请求头里复制，document.cookie 取不到）。'
            )
            return
        self.cookie_edit.setPlainText(cookie)
        self.cookie_status.setText('正在验证 Cookie 并获取账号信息…')
        self.cookie_status.setStyleSheet('color:#6b7180;')
        self.cookie_btn.setEnabled(False)
        self.cookie_thread = CookieLoginThread(cookie, self)
        _ACTIVE_THREADS.add(self.cookie_thread)
        self.cookie_thread.finished.connect(
            lambda t=self.cookie_thread: _ACTIVE_THREADS.discard(t))
        self.cookie_thread.succeeded.connect(self._on_cookie_success)
        self.cookie_thread.failed.connect(self._on_cookie_failed)
        self.cookie_thread.start()

    def _on_cookie_success(self, cookie: str, nickname: str):
        self.accept()

    def _on_cookie_failed(self, message: str):
        self.cookie_status.setText(message)
        self.cookie_status.setStyleSheet('color:#c62828;')
        self.cookie_btn.setEnabled(True)

    # ---------- 收尾 ----------

    def _on_success(self, cookie: str):
        self.accept()

    def _on_failed(self, message: str):
        self.status.setText(message)
        self.status.setStyleSheet('color:#c62828;')
        self.start_btn.setEnabled(True)

    def closeEvent(self, event):
        # 线程已注册到 _ACTIVE_THREADS，允许其在后台自然结束
        super().closeEvent(event)
