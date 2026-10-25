import sys
import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Qt, QThread, QThreadPool, QTimer, Signal, Slot
from PySide6.QtGui import QColor, QCloseEvent, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from config_loader import Config, MAX_POLL_INTERVAL_SECONDS, MIN_POLL_INTERVAL_SECONDS
from login_helper import (
    create_qr_session,
    login_with_password,
    poll_qr_login,
    validate_authorization,
)
from monitor_service import MonitorService
from regions import DEFAULT_GPU_REGIONS, REGION_CATALOG


CONFIG_PATH = Path(__file__).parent / "config.json"


class AsyncSignals(QObject):
    result = Signal(object)
    error = Signal(str)
    finished = Signal()


class AsyncTask(QRunnable):
    def __init__(self, function):
        super().__init__()
        self.function = function
        self.signals = AsyncSignals()

    @Slot()
    def run(self):
        try:
            result = self.function()
        except Exception as exc:
            self.signals.error.emit(str(exc))
        else:
            self.signals.result.emit(result)
        finally:
            self.signals.finished.emit()


class OrderedCheckList(QListWidget):
    """可勾选、可拖动排序的优先级列表。"""

    def __init__(self, values, selected, parent=None):
        super().__init__(parent)
        self.setDragDropMode(QAbstractItemView.InternalMove)
        self.setDefaultDropAction(Qt.MoveAction)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setToolTip("勾选需要监控的项目；拖动可调整优先级")
        ordered = list(selected) + [value for value in values if value not in selected]
        selected_set = set(selected)
        for value in ordered:
            item = QListWidgetItem(value)
            item.setFlags(
                item.flags()
                | Qt.ItemIsUserCheckable
                | Qt.ItemIsDragEnabled
                | Qt.ItemIsDropEnabled
            )
            item.setCheckState(Qt.Checked if value in selected_set else Qt.Unchecked)
            self.addItem(item)

    def checked_values(self):
        return [
            self.item(index).text()
            for index in range(self.count())
            if self.item(index).checkState() == Qt.Checked
        ]


class LoginDialog(QDialog):
    token_saved = Signal(str)

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config
        self.qr_uuid = None
        self.qr_poll_in_flight = False
        self.tasks = set()
        self.pool = QThreadPool.globalInstance()
        self.setWindowTitle("登录 AutoDL")
        self.setMinimumWidth(440)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_qr_tab(), "微信扫码")
        self.tabs.addTab(self._build_token_tab(), "粘贴令牌")
        self.tabs.addTab(self._build_password_tab(), "账号密码")

        close_button = QPushButton("关闭")
        close_button.clicked.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(self.tabs)
        layout.addWidget(close_button, alignment=Qt.AlignRight)

        self.poll_timer = QTimer(self)
        self.poll_timer.setInterval(2000)
        self.poll_timer.timeout.connect(self._poll_qr)
        QTimer.singleShot(0, self.refresh_qr)

    def _build_qr_tab(self):
        page = QWidget()
        self.qr_label = QLabel("正在获取二维码……")
        self.qr_label.setAlignment(Qt.AlignCenter)
        self.qr_label.setMinimumSize(280, 280)
        self.qr_status = QLabel("请稍候")
        self.qr_status.setAlignment(Qt.AlignCenter)
        refresh = QPushButton("刷新二维码")
        refresh.clicked.connect(self.refresh_qr)
        layout = QVBoxLayout(page)
        layout.addWidget(self.qr_label)
        layout.addWidget(self.qr_status)
        layout.addWidget(refresh, alignment=Qt.AlignCenter)
        return page

    def _build_token_tab(self):
        page = QWidget()
        self.token_edit = QLineEdit()
        self.token_edit.setEchoMode(QLineEdit.Password)
        self.token_edit.setPlaceholderText("粘贴 Authorization")
        show = QCheckBox("显示")
        show.toggled.connect(
            lambda checked: self.token_edit.setEchoMode(
                QLineEdit.Normal if checked else QLineEdit.Password
            )
        )
        self.token_status = QLabel("令牌只会保存到本地 config.json")
        validate_button = QPushButton("验证并保存")
        validate_button.clicked.connect(self._validate_token)
        row = QHBoxLayout()
        row.addWidget(self.token_edit)
        row.addWidget(show)
        layout = QVBoxLayout(page)
        layout.addLayout(row)
        layout.addWidget(self.token_status)
        layout.addWidget(validate_button, alignment=Qt.AlignRight)
        return page

    def _build_password_tab(self):
        page = QWidget()
        self.phone_edit = QLineEdit()
        self.phone_edit.setPlaceholderText("中国大陆手机号")
        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.Password)
        self.password_edit.setPlaceholderText("密码不会保存")
        self.password_status = QLabel("登录成功后仅保存 Authorization")
        login_button = QPushButton("登录并保存")
        login_button.clicked.connect(self._password_login)
        form = QFormLayout(page)
        form.addRow("手机号", self.phone_edit)
        form.addRow("密码", self.password_edit)
        form.addRow("", self.password_status)
        form.addRow("", login_button)
        return page

    def _run_async(self, function, on_result, on_error):
        task = AsyncTask(function)
        self.tasks.add(task)
        task.signals.result.connect(on_result)
        task.signals.error.connect(on_error)
        task.signals.finished.connect(lambda: self.tasks.discard(task))
        self.pool.start(task)

    @Slot()
    def refresh_qr(self):
        self.poll_timer.stop()
        self.qr_uuid = None
        self.qr_label.setText("正在获取二维码……")
        self.qr_status.setText("正在连接 AutoDL")

        def loaded(result):
            pixmap = QPixmap()
            if not pixmap.loadFromData(result["image"]):
                self.qr_status.setText("二维码图片格式无法识别，请刷新重试")
                return
            self.qr_label.setPixmap(
                pixmap.scaled(260, 260, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            )
            self.qr_uuid = result["uuid"]
            self.qr_status.setText("请使用微信扫描并确认登录")
            self.poll_timer.start()

        self._run_async(
            create_qr_session,
            loaded,
            lambda message: self.qr_status.setText(f"获取失败：{message}"),
        )

    @Slot()
    def _poll_qr(self):
        if not self.qr_uuid or self.qr_poll_in_flight:
            return
        self.qr_poll_in_flight = True
        uuid = self.qr_uuid

        def completed(result):
            self.qr_poll_in_flight = False
            status = result.get("status")
            if status == "success":
                self.poll_timer.stop()
                self._save_token(result["token"])
            elif status == "expired":
                self.poll_timer.stop()
                self.qr_status.setText("二维码已过期，请点击刷新")
            elif status == "error":
                self.qr_status.setText(f"查询失败：{result.get('message', '未知错误')}")
            else:
                self.qr_status.setText(result.get("message", "等待扫码确认"))

        def failed(message):
            self.qr_poll_in_flight = False
            self.qr_status.setText(f"查询失败：{message}")

        self._run_async(lambda: poll_qr_login(uuid), completed, failed)

    @Slot()
    def _validate_token(self):
        token = self.token_edit.text().strip()
        if not token:
            self.token_status.setText("请输入 Authorization")
            return
        self.token_status.setText("正在验证……")

        def valid(_result):
            self._save_token(token)

        self._run_async(
            lambda: validate_authorization(token),
            valid,
            lambda message: self.token_status.setText(f"验证失败：{message}"),
        )

    @Slot()
    def _password_login(self):
        phone = self.phone_edit.text().strip()
        password = self.password_edit.text()
        self.password_edit.clear()
        if not phone or not password:
            self.password_status.setText("手机号和密码不能为空")
            return
        self.password_status.setText("正在登录……")
        self._run_async(
            lambda: login_with_password(phone, password),
            self._save_token,
            lambda message: self.password_status.setText(f"登录失败：{message}"),
        )

    def _save_token(self, token):
        try:
            self.config.update_authorization(token)
        except (OSError, TypeError, ValueError) as exc:
            QMessageBox.critical(self, "保存失败", str(exc))
            return
        self.token_edit.clear()
        self.token_saved.emit(token)
        QMessageBox.information(self, "登录成功", "Authorization 已安全保存到本地配置。")
        self.accept()

    def reject(self):
        self.poll_timer.stop()
        super().reject()

    def accept(self):
        self.poll_timer.stop()
        super().accept()


class MonitorWorker(QObject):
    event = Signal(dict)
    completed = Signal(int)

    def __init__(self, config):
        super().__init__()
        self.stop_event = threading.Event()
        self.service = MonitorService(
            config,
            stop_event=self.stop_event,
            event_callback=self.event.emit,
        )

    @Slot()
    def run(self):
        self.completed.emit(self.service.run())

    @Slot()
    def stop(self):
        self.service.stop()


class MainWindow(QMainWindow):
    def __init__(self, config_path=CONFIG_PATH):
        super().__init__()
        self.config_path = Path(config_path)
        self.config = self._load_config()
        self.worker = None
        self.worker_thread = None
        self.close_pending = False
        self.row_map = {}
        self.countdown = 0

        self.setWindowTitle("AutoDL GPU Monitor")
        self.resize(1180, 780)
        self.setMinimumSize(980, 680)
        self._build_ui()
        self._load_form()

        self.countdown_timer = QTimer(self)
        self.countdown_timer.setInterval(1000)
        self.countdown_timer.timeout.connect(self._tick_countdown)

    def _load_config(self):
        try:
            return Config(self.config_path)
        except FileNotFoundError:
            return Config.create_default(self.config_path)
        except Exception as exc:
            QMessageBox.warning(
                None,
                "配置读取失败",
                f"将加载默认配置，原文件不会被覆盖，保存前请检查。\n\n{exc}",
            )
            return Config.create_default(self.config_path)

    def _build_ui(self):
        central = QWidget()
        root = QVBoxLayout(central)
        root.addLayout(self._build_auth_bar())

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self._build_settings_panel())
        splitter.addWidget(self._build_dashboard())
        splitter.setSizes([360, 800])
        root.addWidget(splitter, 1)

        buttons = QHBoxLayout()
        buttons.addStretch()
        self.start_button = QPushButton("开始监控")
        self.start_button.clicked.connect(self.start_monitoring)
        self.stop_button = QPushButton("停止")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.stop_monitoring)
        buttons.addWidget(self.start_button)
        buttons.addWidget(self.stop_button)
        root.addLayout(buttons)
        self.setCentralWidget(central)

    def _build_auth_bar(self):
        layout = QHBoxLayout()
        title = QLabel("AutoDL GPU Monitor")
        title.setStyleSheet("font-size: 20px; font-weight: 600;")
        self.auth_status = QLabel()
        self.auth_edit = QLineEdit()
        self.auth_edit.setEchoMode(QLineEdit.Password)
        self.auth_edit.setReadOnly(True)
        self.auth_edit.setPlaceholderText("Authorization")
        self.auth_edit.setMaximumWidth(280)
        self.auth_show = QCheckBox("显示")
        self.auth_show.toggled.connect(
            lambda checked: self.auth_edit.setEchoMode(
                QLineEdit.Normal if checked else QLineEdit.Password
            )
        )
        self.login_button = QPushButton("登录/切换账号")
        self.login_button.clicked.connect(self.open_login)
        self.clear_button = QPushButton("清除令牌")
        self.clear_button.clicked.connect(self.clear_token)
        layout.addWidget(title)
        layout.addStretch()
        layout.addWidget(self.auth_status)
        layout.addWidget(self.auth_edit)
        layout.addWidget(self.auth_show)
        layout.addWidget(self.login_button)
        layout.addWidget(self.clear_button)
        return layout

    def _build_settings_panel(self):
        self.settings_group = QGroupBox("运行配置")
        layout = QVBoxLayout(self.settings_group)

        layout.addWidget(QLabel("GPU 型号（勾选并拖动排序）"))
        self.models_list = OrderedCheckList(DEFAULT_GPU_REGIONS, [])
        layout.addWidget(self.models_list, 2)
        layout.addWidget(QLabel("区域（勾选并拖动排序）"))
        self.regions_list = OrderedCheckList(REGION_CATALOG, [])
        layout.addWidget(self.regions_list, 1)

        form = QFormLayout()
        self.gpu_num = QSpinBox()
        self.gpu_num.setRange(1, 8)
        self.interval = QSpinBox()
        self.interval.setRange(MIN_POLL_INTERVAL_SECONDS, MAX_POLL_INTERVAL_SECONDS)
        self.interval.setSuffix(" 秒")
        self.mode = QComboBox()
        self.mode.addItem("仅监控", "monitor_only")
        self.mode.addItem("监控并购买", "monitor_and_buy")
        self.image_edit = QLineEdit()
        self.private_image_edit = QLineEdit()
        self.sendkey_edit = QLineEdit()
        self.sendkey_edit.setEchoMode(QLineEdit.Password)
        self.sendkey_show = QCheckBox("显示 SendKey")
        self.sendkey_show.toggled.connect(
            lambda checked: self.sendkey_edit.setEchoMode(
                QLineEdit.Normal if checked else QLineEdit.Password
            )
        )
        form.addRow("GPU 数量", self.gpu_num)
        form.addRow("轮询间隔", self.interval)
        form.addRow("运行模式", self.mode)
        form.addRow("公共镜像", self.image_edit)
        form.addRow("私有镜像 UUID", self.private_image_edit)
        form.addRow("Server酱 SendKey", self.sendkey_edit)
        form.addRow("", self.sendkey_show)
        layout.addLayout(form)

        self.save_button = QPushButton("保存配置")
        self.save_button.clicked.connect(self.save_config)
        layout.addWidget(self.save_button)
        return self.settings_group

    def _build_dashboard(self):
        panel = QWidget()
        layout = QVBoxLayout(panel)
        status_row = QHBoxLayout()
        self.state_label = QLabel("当前状态：未启动")
        self.last_query_label = QLabel("最近查询：—")
        self.next_query_label = QLabel("下次查询：—")
        status_row.addWidget(self.state_label)
        status_row.addStretch()
        status_row.addWidget(self.last_query_label)
        status_row.addWidget(self.next_query_label)
        layout.addLayout(status_row)

        self.inventory = QTableWidget(0, 6)
        self.inventory.setHorizontalHeaderLabels(
            ["GPU 型号", "区域", "空闲", "总数", "状态", "更新时间"]
        )
        self.inventory.horizontalHeader().setStretchLastSection(True)
        self.inventory.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.inventory.setSelectionBehavior(QAbstractItemView.SelectRows)
        layout.addWidget(self.inventory, 3)

        layout.addWidget(QLabel("运行日志"))
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.document().setMaximumBlockCount(1000)
        layout.addWidget(self.log_view, 2)
        return panel

    def _load_form(self):
        self.auth_edit.setText(self.config.Authorization)
        self._update_auth_status()
        self._replace_check_list(
            self.models_list, DEFAULT_GPU_REGIONS, self.config.gpu_models
        )
        self._replace_check_list(
            self.regions_list, REGION_CATALOG, self.config.gpu_regions
        )
        self.gpu_num.setValue(self.config.gpu_num)
        self.interval.setValue(self.config.poll_interval_seconds)
        self.mode.setCurrentIndex(max(0, self.mode.findData(self.config.mode)))
        self.image_edit.setText(self.config.image)
        self.private_image_edit.setText(self.config.private_image_uuid)
        self.sendkey_edit.setText(self.config.sendkey)

    @staticmethod
    def _replace_check_list(widget, values, selected):
        widget.clear()
        ordered = list(selected) + [value for value in values if value not in selected]
        selected_set = set(selected)
        for value in ordered:
            item = QListWidgetItem(value)
            item.setFlags(
                item.flags()
                | Qt.ItemIsUserCheckable
                | Qt.ItemIsDragEnabled
                | Qt.ItemIsDropEnabled
            )
            item.setCheckState(Qt.Checked if value in selected_set else Qt.Unchecked)
            widget.addItem(item)

    def _form_data(self):
        data = self.config.to_dict()
        data.update(
            {
                "authorization": self.auth_edit.text().strip(),
                "mode": self.mode.currentData(),
                "gpu": {
                    "models": self.models_list.checked_values(),
                    "num": self.gpu_num.value(),
                    "regions": self.regions_list.checked_values(),
                },
                "monitor": {"interval_seconds": self.interval.value()},
                "image": {
                    "private_image_uuid": self.private_image_edit.text().strip(),
                    "autodl_image": self.image_edit.text().strip(),
                },
                "notification": {"sendkey": self.sendkey_edit.text().strip()},
            }
        )
        return data

    @Slot()
    def save_config(self, show_message=True):
        try:
            self.config = Config.validate_and_save(
                self._form_data(), self.config_path
            )
        except (OSError, TypeError, ValueError) as exc:
            QMessageBox.warning(self, "配置无效", str(exc))
            return False
        self._update_auth_status()
        if show_message:
            QMessageBox.information(self, "保存成功", "配置已保存到本地 config.json。")
        return True

    @Slot()
    def open_login(self):
        dialog = LoginDialog(self.config, self)
        dialog.token_saved.connect(self._token_saved)
        dialog.exec()

    @Slot(str)
    def _token_saved(self, token):
        self.auth_edit.setText(token)
        self._update_auth_status()

    @Slot()
    def clear_token(self):
        answer = QMessageBox.question(
            self,
            "清除 Authorization",
            "确定清除本地保存的 Authorization 吗？",
        )
        if answer != QMessageBox.Yes:
            return
        try:
            self.config.clear_authorization()
        except OSError as exc:
            QMessageBox.critical(self, "清除失败", str(exc))
            return
        self.auth_edit.clear()
        self._update_auth_status()

    def _update_auth_status(self):
        logged_in = bool(self.auth_edit.text().strip())
        self.auth_status.setText("登录状态：已配置" if logged_in else "登录状态：未登录")
        self.auth_status.setStyleSheet(
            "color: #198754;" if logged_in else "color: #c0392b;"
        )
        if self.worker is None:
            self.start_button.setEnabled(logged_in)

    @Slot()
    def start_monitoring(self):
        if not self.save_config(show_message=False):
            return
        if not self.config.has_authorization():
            QMessageBox.warning(self, "尚未登录", "请先登录或粘贴有效 Authorization。")
            return
        if self.config.mode == "monitor_and_buy":
            summary = (
                f"GPU 数量：{self.config.gpu_num}\n"
                f"型号优先级：{' → '.join(self.config.gpu_models)}\n"
                f"区域优先级：{' → '.join(self.config.gpu_regions)}\n"
                f"镜像：{self.config.private_image_uuid or self.config.image or '默认镜像'}\n\n"
                "确认后，发现库存将立即自动创建付费实例。"
            )
            answer = QMessageBox.warning(
                self,
                "确认启用自动购买",
                summary,
                QMessageBox.Yes | QMessageBox.Cancel,
                QMessageBox.Cancel,
            )
            if answer != QMessageBox.Yes:
                return

        self.log_view.clear()
        self._initialize_inventory()
        self._set_running(True)
        self.worker_thread = QThread(self)
        self.worker = MonitorWorker(self.config)
        self.worker.moveToThread(self.worker_thread)
        self.worker_thread.started.connect(self.worker.run)
        self.worker.event.connect(self._handle_event)
        self.worker.completed.connect(self._worker_completed)
        self.worker.completed.connect(self.worker_thread.quit)
        self.worker_thread.finished.connect(self._thread_finished)
        self.worker_thread.start()

    @Slot()
    def stop_monitoring(self):
        if self.worker is None:
            return
        self.state_label.setText("当前状态：正在安全停止……")
        self.stop_button.setEnabled(False)
        self.worker.stop()

    def _set_running(self, running):
        self.settings_group.setEnabled(not running)
        self.auth_edit.setEnabled(not running)
        self.auth_show.setEnabled(not running)
        self.login_button.setEnabled(not running)
        self.clear_button.setEnabled(not running)
        self.save_button.setEnabled(not running)
        self.start_button.setEnabled(not running and bool(self.auth_edit.text().strip()))
        self.stop_button.setEnabled(running)

    def _initialize_inventory(self):
        self.inventory.setRowCount(0)
        self.row_map.clear()
        for model in self.config.gpu_models:
            for region in self.config.gpu_regions:
                row = self.inventory.rowCount()
                self.inventory.insertRow(row)
                self.row_map[(model, region)] = row
                values = [model, region, "—", "—", "等待查询", "—"]
                for column, value in enumerate(values):
                    self.inventory.setItem(row, column, QTableWidgetItem(value))

    @Slot(dict)
    def _handle_event(self, event):
        event_type = event.get("type")
        if event_type == "log":
            self._append_log(event.get("message", ""), event.get("level", "info"))
        elif event_type == "state":
            self.state_label.setText(f"当前状态：{event.get('message', '运行中')}")
            if event.get("state") == "waiting":
                self.countdown = int(event.get("seconds", 0))
                self.countdown_timer.start()
            else:
                self.countdown_timer.stop()
                self.next_query_label.setText("下次查询：—")
        elif event_type == "watch":
            self._handle_watch_event(event.get("event", {}))
        elif event_type == "inventory":
            self.last_query_label.setText(f"最近查询：{event.get('queried_at', '—')}")
            for candidate in event.get("candidates", []):
                self._update_inventory_candidate(candidate, event.get("queried_at", "—"))
        elif event_type == "purchase_success":
            result = event["result"]
            QMessageBox.information(
                self,
                "购买成功",
                f"型号：{result['gpu_info']['gpu']}\n"
                f"区域：{result['region_name']}\n"
                f"机器 ID：{result['machine_id']}\n"
                f"价格：{result['price']} 元/小时",
            )
        elif event_type == "order_uncertain":
            QMessageBox.critical(
                self,
                "订单结果不确定",
                "创建订单时连接中断或响应异常。程序已停止，请立即到 AutoDL 控制台核对实例列表，避免重复购买。",
            )

    def _handle_watch_event(self, event):
        event_type = event.get("type")
        region = event.get("region")
        if event_type == "region_query_started":
            for model in self.config.gpu_models:
                self._set_row_status(model, region, "查询中", QColor("#d39e00"))
        elif event_type == "region_error":
            for model in self.config.gpu_models:
                self._set_row_status(model, region, "查询失败", QColor("#c0392b"))
            self._append_log(
                f"区域 {region} 查询失败：{event.get('message', '未知错误')}",
                "error",
            )
        elif event_type == "region_result":
            inventory = event.get("inventory", {})
            now = time.strftime("%H:%M:%S")
            for model in self.config.gpu_models:
                details = inventory.get(model)
                if details is None:
                    self._set_row_values(model, region, "—", "—", "未返回", now)
                else:
                    idle = details.get("idle_gpu_num", 0)
                    total = details.get("total_gpu_num", "—")
                    status = "有货" if int(idle) >= self.config.gpu_num else "无货"
                    self._set_row_values(model, region, idle, total, status, now)

    def _update_inventory_candidate(self, candidate, queried_at):
        idle = candidate.get("idle_gpu_num", 0)
        status = "有货" if int(idle) >= self.config.gpu_num else "无货"
        self._set_row_values(
            candidate["gpu"],
            candidate["region"],
            idle,
            candidate.get("total_gpu_num", "—"),
            status,
            queried_at.split(" ")[-1],
        )

    def _set_row_values(self, model, region, idle, total, status, updated):
        row = self.row_map.get((model, region))
        if row is None:
            return
        for column, value in enumerate([model, region, idle, total, status, updated]):
            item = self.inventory.item(row, column)
            if item is None:
                item = QTableWidgetItem()
                self.inventory.setItem(row, column, item)
            item.setText(str(value))
        color = QColor("#198754") if status == "有货" else QColor("#555555")
        if status in {"查询失败", "未返回"}:
            color = QColor("#c0392b")
        self.inventory.item(row, 4).setForeground(color)

    def _set_row_status(self, model, region, status, color):
        row = self.row_map.get((model, region))
        if row is None:
            return
        item = self.inventory.item(row, 4)
        if item is None:
            item = QTableWidgetItem()
            self.inventory.setItem(row, 4, item)
        item.setText(status)
        item.setForeground(color)

    def _append_log(self, message, level="info"):
        labels = {
            "info": "信息",
            "success": "成功",
            "warning": "警告",
            "error": "错误",
        }
        timestamp = time.strftime("%H:%M:%S")
        self.log_view.appendPlainText(
            f"[{timestamp}] [{labels.get(level, '信息')}] {message}"
        )

    @Slot()
    def _tick_countdown(self):
        self.countdown = max(0, self.countdown - 1)
        self.next_query_label.setText(f"下次查询：{self.countdown} 秒后")
        if self.countdown == 0:
            self.countdown_timer.stop()

    @Slot(int)
    def _worker_completed(self, code):
        self.countdown_timer.stop()
        if code == 0:
            self.state_label.setText("当前状态：已停止")
        else:
            self.state_label.setText("当前状态：异常停止")

    @Slot()
    def _thread_finished(self):
        if self.worker is not None:
            self.worker.deleteLater()
        if self.worker_thread is not None:
            self.worker_thread.deleteLater()
        self.worker = None
        self.worker_thread = None
        self._set_running(False)
        if self.close_pending:
            self.close_pending = False
            QTimer.singleShot(0, self.close)

    def closeEvent(self, event: QCloseEvent):
        if self.worker is not None:
            self.close_pending = True
            self.stop_monitoring()
            event.ignore()
            return
        event.accept()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("AutoDL GPU Monitor")
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
