import socketio
import threading
import requests
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel, QPushButton, QListWidget
from PySide6.QtCore import Signal, QMetaObject, Qt

class EarlyPressManager(QWidget):
    order_updated = Signal(list)  # 順位リスト更新通知用シグナル

    def __init__(self):
        super().__init__()
        self.setWindowTitle("早押しゲーム管理")

        self.layout = QVBoxLayout(self)
        self.status_label = QLabel("ゲーム状態: 停止中")
        self.layout.addWidget(self.status_label)

        self.start_button = QPushButton("ゲーム開始")
        self.stop_button = QPushButton("ゲーム停止")
        self.layout.addWidget(self.start_button)
        self.layout.addWidget(self.stop_button)

        self.order_list = QListWidget()
        self.layout.addWidget(self.order_list)

        self.start_button.clicked.connect(self.start_game)
        self.stop_button.clicked.connect(self.stop_game)

        self.order_updated.connect(self.update_order_display)

        # Socket.IOクライアント初期化（別スレッドで起動）
        self.sio = socketio.Client()

        self.sio.on('early_press_order_updated', self.on_order_updated)
        self.sio.on('early_press_winner', self.on_winner)
        self.sio.on('connect', lambda: print('Socket.IO connected'))
        self.sio.on('disconnect', lambda: print('Socket.IO disconnected'))

        self.thread = threading.Thread(target=self.start_socketio)
        self.thread.daemon = True
        self.thread.start()

        # 起動時に現在の順位をAPIから取得
        self.fetch_current_order()

    def start_socketio(self):
        try:
            self.sio.connect('http://localhost:5000')
            self.sio.wait()
        except Exception as e:
            print(f"Socket.IO接続エラー: {e}")

    def on_order_updated(self, order):
        # GUIスレッドで安全に処理するためシグナル発行
        QMetaObject.invokeMethod(self, "order_updated", Qt.QueuedConnection, args=[order])

    def update_order_display(self, order):
        self.order_list.clear()
        for item in order:
            text = f"{item['order']}位: {item['name']} (ボタンID: {item['button_id']})"
            self.order_list.addItem(text)

    def on_winner(self, winner):
        name = winner.get("name", "不明")
        address = winner.get("address", "不明")
        button_id = winner.get("button_id", "不明")
        print(f"勝者決定！ {name} ({address}) ボタンID: {button_id}")

    def start_game(self):
        self.status_label.setText("ゲーム状態: 開始中")
        try:
            resp = requests.post('http://localhost:5000/early_press/start')
            if resp.ok:
                print("ゲーム開始リクエスト成功")
            else:
                print(f"ゲーム開始リクエスト失敗: {resp.status_code}")
        except Exception as e:
            print(f"ゲーム開始リクエスト例外: {e}")

    def stop_game(self):
        self.status_label.setText("ゲーム状態: 停止中")
        try:
            resp = requests.post('http://localhost:5000/early_press/stop')
            if resp.ok:
                print("ゲーム停止リクエスト成功")
            else:
                print(f"ゲーム停止リクエスト失敗: {resp.status_code}")
        except Exception as e:
            print(f"ゲーム停止リクエスト例外: {e}")

    def fetch_current_order(self):
        try:
            resp = requests.get('http://localhost:5000/early_press/current_order')
            if resp.ok:
                order = resp.json().get('order', [])
                self.update_order_display(order)
            else:
                print(f"順位取得失敗: {resp.status_code}")
        except Exception as e:
            print(f"順位取得例外: {e}")
