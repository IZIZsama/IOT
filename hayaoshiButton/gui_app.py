import sys
import re
import threading
import requests
import socketio
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QTextEdit, QListWidget, QListWidgetItem, QLineEdit, QLabel,
    QGroupBox, QFormLayout
)
from PySide6.QtCore import QCoreApplication, QThread, Slot, Qt, QMetaObject
from PySide6.QtGui import QColor
from typing import List, Dict, Any

from ble_worker import BleWorker
from constants import ESP32_SERVICE_UUID, ESP32_CHAR_UUID_NOTIFY, MAX_ALLOWED_DEVICES


class BleApp(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("BLE デバイス接続管理 (Python PySide6)")
        self.setGeometry(100, 100, 950, 700)

        self.main_layout = QVBoxLayout(self)

        # --- BLE設定 ---
        self.settings_group = QGroupBox("BLE 設定")
        self.settings_layout = QFormLayout(self.settings_group)
        self.main_layout.addWidget(self.settings_group)

        self.allowed_name_input = QLineEdit()
        self.allowed_name_input.setPlaceholderText("例: MySensorDevice")
        self.set_allowed_name_button = QPushButton("スキャンフィルター名を設定")
        self.set_allowed_name_button.clicked.connect(self._set_allowed_device_name)
        h_layout_allowed_name = QHBoxLayout()
        h_layout_allowed_name.addWidget(self.allowed_name_input)
        h_layout_allowed_name.addWidget(self.set_allowed_name_button)
        self.settings_layout.addRow("スキャン時の許可デバイス名 (任意):", h_layout_allowed_name)
        self.settings_layout.labelForField(h_layout_allowed_name).setToolTip(
            "スキャン結果をこの名前でフィルタリングします。空の場合は接続対象の名前でフィルタリングを試みます。"
        )

        self.target_name_inputs = []
        target_names_h_layout = QHBoxLayout()
        for i in range(MAX_ALLOWED_DEVICES):
            input_box = QLineEdit()
            input_box.setPlaceholderText(f"デバイス名 {i + 1}")
            self.target_name_inputs.append(input_box)
            target_names_h_layout.addWidget(input_box)

        self.set_target_names_button = QPushButton("接続したいデバイス名を設定")
        self.set_target_names_button.clicked.connect(self._set_target_device_names)
        target_names_h_layout.addWidget(self.set_target_names_button)
        self.settings_layout.addRow(f"接続対象デバイス名 (最大{MAX_ALLOWED_DEVICES}つ):", target_names_h_layout)
        self.settings_layout.labelForField(target_names_h_layout).setToolTip(
            f"接続できるのは、ここで設定された名前を持つデバイスのみで、合計{MAX_ALLOWED_DEVICES}台までです。"
        )

        # --- スキャン・接続中デバイス表示 ---
        self.control_layout = QHBoxLayout()
        self.main_layout.addLayout(self.control_layout)

        self.scan_group = QGroupBox("スキャン結果")
        self.scan_layout = QVBoxLayout(self.scan_group)
        self.scan_button = QPushButton("BLEデバイスをスキャン")
        self.scan_button.clicked.connect(self._start_ble_scan)
        self.scan_layout.addWidget(self.scan_button)
        self.device_list_widget = QListWidget()
        self.device_list_widget.itemDoubleClicked.connect(self._connect_selected_device)
        self.scan_layout.addWidget(self.device_list_widget)
        self.control_layout.addWidget(self.scan_group)
        self.scanned_devices_map = {}

        self.connected_group = QGroupBox("接続中のターゲットデバイス")
        self.connected_layout = QVBoxLayout(self.connected_group)
        self.connected_count_label = QLabel(f"接続中: 0 / {MAX_ALLOWED_DEVICES} 台")
        self.connected_layout.addWidget(self.connected_count_label)
        self.connected_devices_list = QListWidget()
        self.connected_devices_list.itemDoubleClicked.connect(self._disconnect_selected_connected_device)
        self.connected_layout.addWidget(self.connected_devices_list)
        self.control_layout.addWidget(self.connected_group)

        # --- 通知速度表示 ---
        self.notification_rate_group = QGroupBox("通知速度 (Hz / ms 遅延)")
        self.notification_rate_layout = QVBoxLayout(self.notification_rate_group)
        self.notification_rate_list = QListWidget()
        self.notification_rate_layout.addWidget(self.notification_rate_list)
        self.main_layout.addWidget(self.notification_rate_group)
        self._device_rates: Dict[str, Dict[str, Any]] = {}

        # --- ログ出力 ---
        self.log_group = QGroupBox("ログ出力")
        self.log_layout = QVBoxLayout(self.log_group)
        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)
        self.log_layout.addWidget(self.log_output)
        self.main_layout.addWidget(self.log_group)

        # --- 早押しゲーム管理エリア ---
        self.early_press_group = QGroupBox("早押しゲーム管理")
        self.early_press_layout = QVBoxLayout(self.early_press_group)
        self.status_label = QLabel("ゲーム状態: 停止中")
        self.early_press_layout.addWidget(self.status_label)

        self.start_game_button = QPushButton("ゲーム開始")
        self.stop_game_button = QPushButton("ゲーム停止")
        self.early_press_layout.addWidget(self.start_game_button)
        self.early_press_layout.addWidget(self.stop_game_button)

        self.order_list_widget = QListWidget()
        self.early_press_layout.addWidget(self.order_list_widget)

        self.main_layout.addWidget(self.early_press_group)

        self.start_game_button.clicked.connect(self.start_early_press_game)
        self.stop_game_button.clicked.connect(self.stop_early_press_game)

        # --- BleWorkerスレッド起動 ---
        self.ble_thread = QThread()
        self.ble_worker = BleWorker()
        self.ble_worker.moveToThread(self.ble_thread)

        self.ble_worker.scan_finished.connect(self._on_scan_finished)
        self.ble_worker.device_scanned.connect(self._on_device_scanned)
        self.ble_worker.connected.connect(self._on_connected)
        self.ble_worker.disconnected.connect(self._on_disconnected)
        self.ble_worker.error_occurred.connect(self._on_error_occurred)
        self.ble_worker.notification_rate_updated.connect(self._on_notification_rate_updated)
        self.ble_worker.services_discovered.connect(self._on_services_discovered)
        self.ble_worker.characteristics_discovered.connect(self._on_characteristics_discovered)
        self.ble_worker.early_press_order_updated.connect(self._update_early_press_order_display)
        self.ble_worker.early_press_winner.connect(self._on_early_press_winner)

        self.ble_thread.start()
        QCoreApplication.instance().aboutToQuit.connect(self._cleanup_ble_worker)

        self._update_connected_devices_display()

        # --- Socket.IOクライアント ---
        self.sio = socketio.Client()
        self.sio.on('connect', self._on_socket_connect)
        self.sio.on('disconnect', self._on_socket_disconnect)
        self.sio.on('early_press_order_updated', self._on_early_press_order_updated)
        self.sio.on('early_press_winner', self._on_early_press_winner)

        self.sio_thread = threading.Thread(target=self._start_socketio_client)
        self.sio_thread.daemon = True
        self.sio_thread.start()

        self.fetch_current_order()

    # --- BLE設定関連メソッド ---
    @Slot()
    def _set_allowed_device_name(self):
        name = self.allowed_name_input.text().strip()
        self.ble_worker.set_allowed_device_name(name)
        self._log_message(f"スキャンフィルター名を設定: '{name if name else 'なし'}'")

    @Slot()
    def _set_target_device_names(self):
        names = [box.text().strip() for box in self.target_name_inputs]
        try:
            self.ble_worker.set_target_device_names(names)
            self._log_message(f"接続対象デバイス名を設定: {self.ble_worker.target_device_names}")
        except ValueError as e:
            self._on_error_occurred(str(e))

    @Slot()
    def _start_ble_scan(self):
        self._log_message("BLEスキャンを開始します...")
        self.device_list_widget.clear()
        self.scanned_devices_map.clear()
        self.scan_button.setEnabled(False)
        self.ble_worker.start_scan()

    @Slot(dict)
    def _on_device_scanned(self, device_info: dict):
        self._log_message(f"検出: {device_info['name']} ({device_info['address']}) RSSI: {device_info['rssi']}")

    @Slot(list)
    def _on_scan_finished(self, devices: List[dict]):
        self._log_message(f"スキャンが完了しました。許可されたデバイス {len(devices)} 台を検出。")
        for device in devices:
            item = QListWidgetItem(f"{device['name']} ({device['address']}) - RSSI: {device['rssi']}")
            self.device_list_widget.addItem(item)
            self.scanned_devices_map[device['address']] = device
        self.scan_button.setEnabled(True)

    @Slot(QListWidgetItem)
    def _connect_selected_device(self, item: QListWidgetItem):
        text = item.text()
        match = re.search(r'\((\w{2}(?::\w{2}){5})\)', text)
        if match:
            address = match.group(1)
            self._log_message(f"デバイス {address} への接続を試行中...")
            self.ble_worker.connect_device(address)
        else:
            self._log_message("エラー: アドレス抽出失敗。", is_error=True)

    @Slot(str, str)
    def _on_connected(self, address: str, name: str):
        self._log_message(f"デバイス {name} ({address}) に正常に接続しました。")
        self._update_connected_devices_display()
        self._log_message(f"サービス発見開始: {name} ({address})")
        self.ble_worker.discover_services(address)

    @Slot(str)
    def _on_disconnected(self, address: str):
        self._log_message(f"デバイス {address} から切断しました。")
        self._update_connected_devices_display()
        if address in self._device_rates:
            del self._device_rates[address]
            self._update_notification_rate_display()

    @Slot(str)
    def _on_error_occurred(self, error: str):
        self._log_message(f"エラー: {error}", is_error=True)

    def _update_connected_devices_display(self):
        self.connected_devices_list.clear()
        connected = self.ble_worker.get_connected_targets()
        count = len(connected)
        self.connected_count_label.setText(f"接続中: {count} / {MAX_ALLOWED_DEVICES} 台")

        for addr, name in connected.items():
            item = QListWidgetItem(f"{name} ({addr})")
            item.setData(Qt.UserRole, addr)
            self.connected_devices_list.addItem(item)

        try:
            self.connected_devices_list.itemDoubleClicked.disconnect(self._disconnect_selected_connected_device)
        except TypeError:
            pass
        self.connected_devices_list.itemDoubleClicked.connect(self._disconnect_selected_connected_device)

    @Slot(QListWidgetItem)
    def _disconnect_selected_connected_device(self, item: QListWidgetItem):
        addr = item.data(Qt.UserRole)
        if addr:
            self._log_message(f"デバイス {addr} からの切断を試行中...")
            self.ble_worker.disconnect_device(addr)
        else:
            self._log_message("エラー: アドレス抽出失敗。", is_error=True)

    @Slot(str, list)
    def _on_services_discovered(self, address: str, services: List[dict]):
        self._log_message(f"デバイス {address} のサービスを発見しました ({len(services)} 個)。")
        found = False
        for s in services:
            if s['uuid'].lower() == ESP32_SERVICE_UUID.lower():
                self._log_message(f"ターゲットサービス '{s['uuid']}' 検出。キャラクタリスティック発見を開始。")
                self.ble_worker.discover_characteristics(address, s['uuid'])
                found = True
                break
        if not found:
            self._log_message(f"警告: デバイス {address} でターゲットサービスが見つかりませんでした。", is_error=True)

    @Slot(str, str, list)
    def _on_characteristics_discovered(self, address: str, service_uuid: str, characteristics: List[dict]):
        self._log_message(f"サービス {service_uuid} のキャラクタリスティックを発見しました ({len(characteristics)} 個)。")
        found_notify = False
        for c in characteristics:
            self._log_message(f"キャラクタリスティック: {c['uuid']}, プロパティ: {', '.join(c['properties'])}")
            if c['uuid'].lower() == ESP32_CHAR_UUID_NOTIFY.lower() and "notify" in c['properties']:
                self._log_message(f"通知対応キャラクタリスティック '{c['uuid']}' を検出。通知開始。")
                self.ble_worker.start_notify(address, c['uuid'])
                found_notify = True
                break
        if not found_notify:
            self._log_message(f"警告: 通知対応キャラクタリスティックが見つかりませんでした。", is_error=True)

    @Slot(dict)
    def _on_notification_rate_updated(self, rate_info: Dict[str, Any]):
        addr = rate_info["address"]
        self._device_rates[addr] = rate_info
        self._update_notification_rate_display()

    def _update_notification_rate_display(self):
        self.notification_rate_list.clear()
        devices = sorted(
            self._device_rates.values(),
            key=lambda x: (x["rate_hz"] if x["rate_hz"] != float('inf') else -1.0,
                           x["delay_ms"] if x["delay_ms"] != 0 else float('inf')),
            reverse=True,
        )
        for d in devices:
            rate_text = f"{d['rate_hz']:.2f} Hz" if d["rate_hz"] != float('inf') else "∞ Hz"
            delay_text = f"{d['delay_ms']:.2f} ms" if d["delay_ms"] != float('inf') else "0 ms"
            item_text = f"[{d['address'][-5:]}]: {rate_text} ({delay_text} 遅延)"
            self.notification_rate_list.addItem(item_text)

    def _log_message(self, message: str, is_error: bool = False):
        cursor = self.log_output.textCursor()
        cursor.movePosition(cursor.End)
        if is_error:
            original_format = cursor.charFormat()
            error_format = original_format
            error_format.setForeground(QColor("red"))
            cursor.setCharFormat(error_format)
            cursor.insertText(f"[{QCoreApplication.instance().applicationDisplayName()}] {message}\n")
            cursor.setCharFormat(original_format)
        else:
            cursor.insertText(f"[{QCoreApplication.instance().applicationDisplayName()}] {message}\n")
        self.log_output.setTextCursor(cursor)
        self.log_output.ensureCursorVisible()

    def _cleanup_ble_worker(self):
        self._log_message("アプリケーション終了中。BLEワーカーをクリーンアップします...")
        self.ble_worker.cleanup()
        self.ble_thread.quit()
        self.ble_thread.wait()

    # 早押しゲーム関連

    def _start_socketio_client(self):
        try:
            self.sio.connect('http://localhost:5000')
            self.sio.wait()
        except Exception as e:
            self._log_message(f"Socket.IO接続エラー: {e}", is_error=True)

    def _on_socket_connect(self):
        self._log_message("Socket.IOに接続しました。")

    def _on_socket_disconnect(self):
        self._log_message("Socket.IOから切断されました。")

    def _on_early_press_order_updated(self, order):
        QMetaObject.invokeMethod(
            self,
            "_update_early_press_order_display",
            Qt.QueuedConnection,
            args=[order]
        )

    def _on_early_press_winner(self, winner):
        winner_name = winner.get("name", "不明")
        winner_addr = winner.get("address", "不明")
        button_id = winner.get("button_id", "不明")
        self._log_message(f"勝者決定！ {winner_name} ({winner_addr}) ボタンID: {button_id}")

    @Slot(list)
    def _update_early_press_order_display(self, order):
        self.order_list_widget.clear()
        for item in order:
            text = f"{item['order']}位: {item['name']} (ボタンID: {item['button_id']})"
            self.order_list_widget.addItem(text)

    def start_early_press_game(self):
        self.status_label.setText("ゲーム状態: 開始中")
        try:
            resp = requests.post('http://localhost:5000/early_press/start')
            if resp.ok:
                self._log_message("早押しゲーム開始リクエスト成功。")
            else:
                self._log_message(f"早押しゲーム開始リクエスト失敗: {resp.status_code}", is_error=True)
        except Exception as e:
            self._log_message(f"早押しゲーム開始リクエスト例外: {e}", is_error=True)

    def stop_early_press_game(self):
        self.status_label.setText("ゲーム状態: 停止中")
        try:
            resp = requests.post('http://localhost:5000/early_press/stop')
            if resp.ok:
                self._log_message("早押しゲーム停止リクエスト成功。")
            else:
                self._log_message(f"早押しゲーム停止リクエスト失敗: {resp.status_code}", is_error=True)
        except Exception as e:
            self._log_message(f"早押しゲーム停止リクエスト例外: {e}", is_error=True)

    def fetch_current_order(self):
        try:
            resp = requests.get('http://localhost:5000/early_press/current_order')
            if resp.ok:
                order = resp.json().get('order', [])
                self._update_early_press_order_display(order)
            else:
                self._log_message(f"順位取得失敗: {resp.status_code}", is_error=True)
        except Exception as e:
            self._log_message(f"順位取得例外: {e}", is_error=True)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setApplicationDisplayName("BLE App")
    window = BleApp()
    window.show()
    sys.exit(app.exec())
