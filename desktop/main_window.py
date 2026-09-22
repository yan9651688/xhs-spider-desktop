# encoding: utf-8
"""主窗口：账号/有效期状态、小红书扫码、三种采集模式、日志与结果表。"""
from __future__ import annotations

import os
import threading

from loguru import logger
from PySide6.QtCore import Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
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


class MainWindow(QMainWindow):
    def __init__(self, config: dict, session: dict):
        super().__init__()
        self.config = config
        self.session = session
        self.auth = None
        self.xhs_nickname = ''
        self.collect_worker = None
        self.restore_worker = None

        self.setWindowTitle('小红书采集工具')
        self.resize(980, 720)
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

    # ---------- UI ----------

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        root.addWidget(self._build_header())

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_search_tab(), '搜索采集')
        self.tabs.addTab(self._build_urls_tab(), '链接采集')
        self.tabs.addTab(self._build_user_tab(), '主页采集')
        root.addWidget(self.tabs, 1)

        root.addWidget(self._build_options_box())
        root.addWidget(self._build_run_row())

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumHeight(150)
        self.log_view.setPlaceholderText('运行日志')
        root.addWidget(self.log_view)

        self.table = QTableWidget(0, len(TABLE_COLUMNS))
        self.table.setHorizontalHeaderLabels(TABLE_COLUMNS)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setWordWrap(False)
        self.table.doubleClicked.connect(self.open_current_note)
        root.addWidget(self.table, 2)

    def _build_header(self) -> QWidget:
        box = QWidget()
        layout = QHBoxLayout(box)
        layout.setContentsMargins(0, 4, 0, 4)

        expire = self.session.get('xhsExpireTime') or '永久'
        name = self.session.get('name') or self.session.get('username') or ''
        self.account_label = QLabel(f'账号：{name}    有效期至：{expire}')
        layout.addWidget(self.account_label)
        layout.addStretch(1)

        self.xhs_label = QLabel('小红书：检测中…')
        layout.addWidget(self.xhs_label)
        self.xhs_login_btn = QPushButton('扫码登录小红书')
        self.xhs_login_btn.clicked.connect(self.open_xhs_login)
        layout.addWidget(self.xhs_login_btn)
        open_dir_btn = QPushButton('打开输出目录')
        open_dir_btn.clicked.connect(self.open_output_dir)
        layout.addWidget(open_dir_btn)
        logout_btn = QPushButton('退出登录')
        logout_btn.clicked.connect(self.logout)
        layout.addWidget(logout_btn)
        return box

    def _build_search_tab(self) -> QWidget:
        tab = QWidget()
        form = QFormLayout(tab)
        self.query_edit = QLineEdit()
        self.query_edit.setPlaceholderText('搜索关键词，例如：黄金首饰')
        form.addRow('关键词', self.query_edit)
        self.num_spin = QSpinBox()
        self.num_spin.setRange(1, 500)
        self.num_spin.setValue(20)
        form.addRow('数量', self.num_spin)
        self.sort_combo = QComboBox()
        for text, value in SORT_OPTIONS:
            self.sort_combo.addItem(text, value)
        form.addRow('排序', self.sort_combo)
        self.type_combo = QComboBox()
        for text, value in TYPE_OPTIONS:
            self.type_combo.addItem(text, value)
        form.addRow('类型', self.type_combo)
        self.time_combo = QComboBox()
        for text, value in TIME_OPTIONS:
            self.time_combo.addItem(text, value)
        form.addRow('时间', self.time_combo)
        return tab

    def _build_urls_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
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
        self.user_edit = QLineEdit()
        self.user_edit.setPlaceholderText(
            '用户主页链接，例如：https://www.xiaohongshu.com/user/profile/xxxx?xsec_token=...'
        )
        form.addRow('主页', self.user_edit)
        hint = QLabel('将采集该用户公开可见的全部笔记。')
        hint.setStyleSheet('color:#666;')
        form.addRow('', hint)
        return tab

    def _build_options_box(self) -> QWidget:
        box = QGroupBox('保存选项')
        layout = QFormLayout(box)

        dir_row = QHBoxLayout()
        self.output_edit = QLineEdit(self.config.get('output_dir') or str(paths.DEFAULT_OUTPUT_DIR))
        browse_btn = QPushButton('浏览…')
        browse_btn.clicked.connect(self.browse_output_dir)
        dir_row.addWidget(self.output_edit, 1)
        dir_row.addWidget(browse_btn)
        layout.addRow('输出目录', dir_row)

        self.task_edit = QLineEdit()
        self.task_edit.setPlaceholderText('留空则自动使用：关键词/用户ID + 时间')
        layout.addRow('任务名', self.task_edit)

        check_row = QHBoxLayout()
        self.img_check = QCheckBox('保存图片')
        self.img_check.setChecked(True)
        self.video_check = QCheckBox('保存视频')
        self.video_check.setChecked(True)
        self.excel_check = QCheckBox('导出 Excel')
        self.excel_check.setChecked(True)
        check_row.addWidget(self.img_check)
        check_row.addWidget(self.video_check)
        check_row.addWidget(self.excel_check)
        check_row.addStretch(1)
        layout.addRow('内容', check_row)
        return box

    def _build_run_row(self) -> QWidget:
        box = QWidget()
        layout = QHBoxLayout(box)
        self.run_btn = QPushButton('开始采集')
        self.run_btn.setMinimumHeight(34)
        self.run_btn.clicked.connect(self.toggle_run)
        layout.addWidget(self.run_btn)
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar, 1)
        self.stage_label = QLabel('')
        layout.addWidget(self.stage_label)
        return box

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
            self.xhs_label.setStyleSheet('color:#2e7d32;')
            self.xhs_login_btn.setText('切换小红书账号')
        else:
            self.xhs_label.setText('小红书：未登录')
            self.xhs_label.setStyleSheet('color:#c62828;')
            self.xhs_login_btn.setText('扫码登录小红书')

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
        spec = TaskSpec(
            save_images=self.img_check.isChecked(),
            save_videos=self.video_check.isChecked(),
            save_excel=self.excel_check.isChecked(),
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
        if not (spec.save_images or spec.save_videos or spec.save_excel):
            return '请至少选择一种保存内容'
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
        if summary['stopped']:
            QMessageBox.information(self, '已停止', f"任务已停止，共抓取 {summary['got']} 篇。")
        else:
            QMessageBox.information(
                self, '采集完成',
                f"抓取 {summary['got']}/{summary['total']} 篇\n"
                f"用时 {summary['seconds']} 秒\n"
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
        self.account_label.setText(
            f"账号：{self.session.get('name') or self.session.get('username')}    有效期至：{expire}"
        )

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

    def open_output_dir(self):
        path = self.output_edit.text().strip() or str(paths.DEFAULT_OUTPUT_DIR)
        os.makedirs(path, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def append_log(self, text: str):
        self.log_view.appendPlainText(text)

    def closeEvent(self, event):
        if self.collect_worker is not None and self.collect_worker.isRunning():
            self.collect_worker.stop()
            self.collect_worker.wait(3000)
        try:
            if self._log_sink_id is not None:
                logger.remove(self._log_sink_id)
                self._log_sink_id = None
        except Exception:
            pass
        super().closeEvent(event)
