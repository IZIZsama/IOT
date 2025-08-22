# main.py

import sys
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QCoreApplication

# gui_app.py から BleApp クラスをインポート
from gui_app import BleApp

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setApplicationDisplayName("BLE App")  # ログ表示用
    window = BleApp()
    window.show()
    sys.exit(app.exec())
