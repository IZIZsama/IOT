import sys
import re
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QTextEdit, QListWidget, QListWidgetItem, QLineEdit, QLabel,
    QGroupBox, QFormLayout
)
from PySide6.QtCore import QCoreApplication, QThread, Signal, Slot
from PySide6.QtGui import QColor, QPalette
from typing import List, Dict, Optional, Any

# 上記の修正された BleWorker クラスの定義をここに貼り付けてください

class BleApp(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("BLE デバイス接続管理 (Python PySide6)")
        self.setGeometry(100, 100, 800, 700) # ウィンドウサイズを調整

        # GUIコンポーネントとレイアウトのセットアップ
        self.main_layout = QVBoxLayout(self)

        # --- 設定エリア ---
        self.settings_group = QGroupBox("BLE 設定")
        self.settings_layout = QFormLayout(self.settings_group)
        self.main_layout.addWidget(self.settings_group)

        # スキャンフィルター名入力
        self.allowed_name_input = QLineEdit()
        self.allowed_name_input.setPlaceholderText("例: MySensorDevice")
        self.set_allowed_name_button = QPushButton("スキャンフィルター名を設定")
        self.set_allowed_name_button.clicked.connect(self._set_allowed_device_name)
        
        h_layout_allowed_name = QHBoxLayout()
        h_layout_allowed_name.addWidget(self.allowed_name_input)
        h_layout_allowed_name.addWidget(self.set_allowed_name_button)
        self.settings_layout.addRow("スキャン時の許可デバイス名 (任意):", h_layout_allowed_name)
        self.settings_layout.labelForField(h_layout_allowed_name).setToolTip("スキャン結果をこの名前でフィルタリングします。空の場合は接続対象の名前でフィルタリングを試みます。")


        # 接続したいデバイス名 (最大4つ) 入力ボックス
        self.target_name_inputs = []
        target_names_h_layout = QHBoxLayout()
        for i in range(4):
            input_box = QLineEdit()
            input_box.setPlaceholderText(f"デバイス名 {i + 1}")
            self.target_name_inputs.append(input_box)
            target_names_h_layout.addWidget(input_box)
        
        self.set_target_names_button = QPushButton("接続したいデバイス名を設定")
        self.set_target_names_button.clicked.connect(self._set_target_device_names)
        target_names_h_layout.addWidget(self.set_target_names_button)
        self.settings_layout.addRow("接続対象デバイス名 (最大4つ):", target_names_h_layout)
        self.settings_layout.labelForField(target_names_h_layout).setToolTip("接続できるのは、ここで設定された名前を持つデバイスのみで、合計4台までです。")

        # --- スキャンと接続中のデバイス表示エリア ---
        self.control_layout = QHBoxLayout()
        self.main_layout.addLayout(self.control_layout)

        # スキャン結果
        self.scan_group = QGroupBox("スキャン結果")
        self.scan_layout = QVBoxLayout(self.scan_group)
        self.scan_button = QPushButton("BLEデバイスをスキャン")
        self.scan_button.clicked.connect(self._start_ble_scan)
        self.scan_layout.addWidget(self.scan_button)
        self.device_list_widget = QListWidget()
        self.device_list_widget.itemDoubleClicked.connect(self._connect_selected_device)
        self.scan_layout.addWidget(self.device_list_widget)
        self.control_layout.addWidget(self.scan_group)
        self.scanned_devices_map = {} # アドレスとデバイス情報のマップ

        # 接続中のターゲットデバイス
        self.connected_group = QGroupBox("接続中のターゲットデバイス")
        self.connected_layout = QVBoxLayout(self.connected_group)
        self.connected_count_label = QLabel("接続中: 0 / 4 台")
        self.connected_layout.addWidget(self.connected_count_label)
        self.connected_devices_list = QListWidget()
        self.connected_layout.addWidget(self.connected_devices_list)
        self.control_layout.addWidget(self.connected_group)

        # --- ログ出力エリア ---
        self.log_group = QGroupBox("ログ出力")
        self.log_layout = QVBoxLayout(self.log_group)
        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)
        self.log_layout.addWidget(self.log_output)
        self.main_layout.addWidget(self.log_group)


        # --- BleWorker のセットアップ ---
        self.ble_thread = QThread()
        self.ble_worker = BleWorker()
        self.ble_worker.moveToThread(self.ble_thread)

        # BleWorker からのシグナルを受け取る
        self.ble_worker.scan_finished.connect(self._on_scan_finished)
        self.ble_worker.device_scanned.connect(self._on_device_scanned)
        self.ble_worker.connected.connect(self._on_connected)
        self.ble_worker.disconnected.connect(self._on_disconnected)
        self.ble_worker.error_occurred.connect(self._on_error_occurred)
        # 他のシグナルも必要に応じて接続

        self.ble_thread.start()
        
        # アプリケーション終了時のクリーンアップ
        QCoreApplication.instance().aboutToQuit.connect(self._cleanup_ble_worker)

        # 初回接続状態の表示更新
        self._update_connected_devices_display()

    @Slot()
    def _set_allowed_device_name(self):
        name = self.allowed_name_input.text().strip()
        self.ble_worker.set_allowed_device_name(name)
        self._log_message(f"スキャンフィルター名を設定: '{name if name else 'なし'}'")

    @Slot()
    def _set_target_device_names(self):
        names = [input_box.text().strip() for input_box in self.target_name_inputs]
        try:
            self.ble_worker.set_target_device_names(names)
            self._log_message(f"接続対象デバイス名を設定: {BleWorker.TARGET_DEVICE_NAMES}")
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
    def _on_device_scanned(self, device_info: Dict):
        # 個々のデバイスが発見されたときにログに表示
        self._log_message(f"検出: {device_info['name']} ({device_info['address']}) RSSI: {device_info['rssi']}")
        # device_list_widgetにはscan_finishedでまとめて追加するので、ここでは追加しない

    @Slot(list)
    def _on_scan_finished(self, devices: List[Dict]):
        self._log_message(f"スキャンが完了しました。許可されたデバイス {len(devices)} 台を検出。")
        for device in devices:
            item = QListWidgetItem(f"{device['name']} ({device['address']}) - RSSI: {device['rssi']}")
            self.device_list_widget.addItem(item)
            self.scanned_devices_map[device['address']] = device 
        self.scan_button.setEnabled(True)

    @Slot(QListWidgetItem)
    def _connect_selected_device(self, item: QListWidgetItem):
        selected_text = item.text()
        match = re.search(r'\((\w{2}(?::\w{2}){5})\)', selected_text)
        if match:
            address = match.group(1)
            self._log_message(f"デバイス {address} への接続を試行中...")
            self.ble_worker.connect_device(address)
        else:
            self._log_message("エラー: 選択されたアイテムからアドレスを抽出できませんでした。", is_error=True)

    @Slot(str, str)
    def _on_connected(self, address: str, name: str):
        self._log_message(f"デバイス {name} ({address}) に正常に接続しました。")
        self._update_connected_devices_display() # 接続状態を更新

    @Slot(str)
    def _on_disconnected(self, address: str):
        self._log_message(f"デバイス {address} から切断しました。")
        self._update_connected_devices_display() # 接続状態を更新

    @Slot(str)
    def _on_error_occurred(self, error_message: str):
        self._log_message(f"エラー: {error_message}", is_error=True)

    def _update_connected_devices_display(self):
        """接続中のデバイスリストとカウントをGUIに反映します。"""
        self.connected_devices_list.clear()
        connected_targets = self.ble_worker.get_connected_targets()
        count = len(connected_targets)
        self.connected_count_label.setText(f"接続中: {count} / 4 台")

        for address, name in connected_targets.items():
            item = QListWidgetItem(f"{name} ({address})")
            # 切断ボタンをアイテムに追加する方法は少し複雑になるため、
            # 簡単にするためにアイテムをダブルクリックして切断するようにします
            # または、各アイテムの横にボタンを配置するためにカスタム делеゲートを使用します
            self.connected_devices_list.addItem(item)
            # 切断機能の紐付け (ダブルクリックで切断)
            item.setData(0, address) # データとしてアドレスを保存
        
        self.connected_devices_list.itemDoubleClicked.connect(self._disconnect_selected_connected_device)


    @Slot(QListWidgetItem)
    def _disconnect_selected_connected_device(self, item: QListWidgetItem):
        address = item.data(0) # 保存したアドレスを取得
        if address:
            self._log_message(f"デバイス {address} からの切断を試行中...")
            self.ble_worker.disconnect_device(address)
        else:
            self._log_message("エラー: 接続中のアイテムからアドレスを抽出できませんでした。", is_error=True)


    def _log_message(self, message: str, is_error: bool = False):
        """ログ出力エリアにメッセージを追加します。"""
        cursor = self.log_output.textCursor()
        cursor.movePosition(cursor.End)
        
        if is_error:
            original_format = cursor.charFormat()
            error_format = original_format
            error_format.setForeground(QColor("red"))
            cursor.setCharFormat(error_format)
            cursor.insertText(f"[{QCoreApplication.instance().applicationDisplayName()}] {message}\n")
            cursor.setCharFormat(original_format) # フォーマットを元に戻す
        else:
            cursor.insertText(f"[{QCoreApplication.instance().applicationDisplayName()}] {message}\n")
        
        self.log_output.setTextCursor(cursor)
        self.log_output.ensureCursorVisible() # 最新行までスクロール

    def _cleanup_ble_worker(self):
        self._log_message("アプリケーション終了中。BLEワーカーをクリーンアップします...")
        self.ble_worker.cleanup()
        self.ble_thread.quit()
        self.ble_thread.wait()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setApplicationDisplayName("BLE App") # ログ表示用
    window = BleApp()
    window.show()
    sys.exit(app.exec())