# ble_worker.py

import asyncio
import time
from bleak import BleakScanner, BleakClient
from PySide6.QtCore import QObject, Signal, Slot
from typing import List, Dict, Optional, Any
from collections import deque

from constants import (
    MAX_ALLOWED_DEVICES,
    RATE_BUFFER_SIZE,
    ESP32_SERVICE_UUID,
    ESP32_CHAR_UUID_NOTIFY,
    ESP32_CHAR_UUID_RAISE_FLAG,
)

class BleWorker(QObject):
    # BLE関連のシグナル
    scan_finished = Signal(list)
    device_scanned = Signal(dict)
    connected = Signal(str, str)
    disconnected = Signal(str)
    services_discovered = Signal(str, list)
    characteristics_discovered = Signal(str, str, list)
    characteristic_read = Signal(str, str, list)
    characteristic_write_ack = Signal(str, str)
    notification_received = Signal(str, str, list)
    notification_rate_updated = Signal(dict)
    error_occurred = Signal(str)

    # 早押しゲーム関連のシグナル
    early_press_order_updated = Signal(list)
    early_press_winner = Signal(dict)

    def __init__(self):
        super().__init__()
        self._loop = None
        self._clients: Dict[str, BleakClient] = {}
        self._connected_target_addresses: Dict[str, str] = {}
        self._notification_metrics: Dict[str, Dict[str, Any]] = {}

        self.allowed_device_name: Optional[str] = None
        self.target_device_names: List[str] = []

        # 早押しゲーム用状態
        self._button_press_log: List[Dict[str, Any]] = []
        self._is_game_active: bool = False
        self._winner_address: Optional[str] = None

    def _ensure_event_loop(self):
        if self._loop is None:
            try:
                self._loop = asyncio.get_event_loop()
            except RuntimeError:
                self._loop = asyncio.new_event_loop()
                asyncio.set_event_loop(self._loop)
        return self._loop

    # スキャン開始
    @Slot()
    def start_scan(self):
        loop = self._ensure_event_loop()
        try:
            future = asyncio.run_coroutine_threadsafe(self._perform_scan(), loop)
            future.result()
        except Exception as e:
            self.error_occurred.emit(f"Scan error: {e}")

    async def _perform_scan(self):
        devices = await BleakScanner.discover(timeout=5.0)
        allowed_devices = []
        for device in devices:
            if self.allowed_device_name:
                is_allowed = device.name == self.allowed_device_name
            elif self.target_device_names:
                is_allowed = device.name in self.target_device_names
            else:
                is_allowed = True

            if is_allowed:
                info = {
                    "address": device.address,
                    "name": device.name or "Unknown",
                    "rssi": device.rssi,
                }
                self.device_scanned.emit(info)
                allowed_devices.append(info)
        self.scan_finished.emit(allowed_devices)

    # デバイス名設定
    def set_allowed_device_name(self, name: str):
        self.allowed_device_name = name if name else None

    def set_target_device_names(self, names: List[str]):
        if len(names) > MAX_ALLOWED_DEVICES:
            raise ValueError(f"Target device names list cannot exceed {MAX_ALLOWED_DEVICES} items.")
        self.target_device_names = [n.strip() for n in names if n.strip()]

    def get_connected_targets(self) -> Dict[str, str]:
        return self._connected_target_addresses

    # デバイス接続
    @Slot(str)
    def connect_device(self, address: str):
        loop = self._ensure_event_loop()
        if len(self._connected_target_addresses) >= MAX_ALLOWED_DEVICES:
            self.error_occurred.emit(f"最大接続台数に達しました。")
            return
        if address in self._clients and self._clients[address].is_connected:
            if address in self._connected_target_addresses:
                self.connected.emit(address, self._connected_target_addresses[address])
                return
            else:
                self.error_occurred.emit(f"デバイス {address} は既に接続済みですが対象外です。")
                return
        try:
            future = asyncio.run_coroutine_threadsafe(self._perform_connect(address), loop)
            future.result()
        except Exception as e:
            self.error_occurred.emit(f"接続エラー: {e}")

    async def _perform_connect(self, address: str):
        client = BleakClient(address)
        await client.connect()
        connected_name = None
        try:
            services = await client.get_services()
            # デバイス名取得（サービスのdevice.nameなど環境依存）
            connected_name = client._backend.client.device.name if hasattr(client._backend.client.device, 'name') else "Unknown"
        except Exception:
            connected_name = "Unknown"

        is_target = False
        if self.allowed_device_name and connected_name == self.allowed_device_name:
            is_target = True
        elif self.target_device_names and connected_name in self.target_device_names:
            is_target = True

        if not is_target:
            await client.disconnect()
            raise Exception(f"接続したデバイス名 {connected_name} は許可されていません。")

        self._clients[address] = client
        self._connected_target_addresses[address] = connected_name
        self.connected.emit(address, connected_name)

    # デバイス切断
    @Slot(str)
    def disconnect_device(self, address: str):
        loop = self._ensure_event_loop()
        if address not in self._clients:
            self.error_occurred.emit(f"デバイス {address} は接続されていません。")
            return
        try:
            future = asyncio.run_coroutine_threadsafe(self._perform_disconnect(address), loop)
            future.result()
            self.disconnected.emit(address)
        except Exception as e:
            self.error_occurred.emit(f"切断エラー: {e}")

    async def _perform_disconnect(self, address: str):
        client = self._clients[address]
        await client.disconnect()
        del self._clients[address]
        if address in self._connected_target_addresses:
            del self._connected_target_addresses[address]
        if address in self._notification_metrics:
            del self._notification_metrics[address]

    # サービス発見
    @Slot(str)
    def discover_services(self, address: str):
        loop = self._ensure_event_loop()
        if address not in self._clients:
            self.error_occurred.emit(f"デバイス {address} は接続されていません。")
            return
        try:
            future = asyncio.run_coroutine_threadsafe(self._perform_discover_services(address), loop)
            future.result()
        except Exception as e:
            self.error_occurred.emit(f"サービス発見エラー: {e}")

    async def _perform_discover_services(self, address: str):
        client = self._clients[address]
        services = await client.get_services()
        services_info = []
        for service in services:
            services_info.append({
                "uuid": str(service.uuid),
                "description": service.description,
            })
        self.services_discovered.emit(address, services_info)

    # キャラクタリスティック発見
    @Slot(str, str)
    def discover_characteristics(self, address: str, service_uuid: str):
        loop = self._ensure_event_loop()
        if address not in self._clients:
            self.error_occurred.emit(f"デバイス {address} は接続されていません。")
            return
        try:
            future = asyncio.run_coroutine_threadsafe(
                self._perform_discover_characteristics(address, service_uuid), loop
            )
            future.result()
        except Exception as e:
            self.error_occurred.emit(f"キャラクタリスティック発見エラー: {e}")

    async def _perform_discover_characteristics(self, address: str, service_uuid: str):
        client = self._clients[address]
        services = await client.get_services()
        characteristics_info = []
        for service in services:
            if str(service.uuid).lower() == service_uuid.lower():
                for char in service.characteristics:
                    characteristics_info.append({
                        "uuid": str(char.uuid),
                        "description": char.description,
                        "properties": [p.name for p in char.properties],
                    })
                break
        self.characteristics_discovered.emit(address, service_uuid, characteristics_info)

    # キャラクタリスティック読み込み
    @Slot(str, str)
    def read_characteristic(self, address: str, char_uuid: str):
        loop = self._ensure_event_loop()
        if address not in self._clients:
            self.error_occurred.emit(f"デバイス {address} は接続されていません。")
            return
        try:
            future = asyncio.run_coroutine_threadsafe(self._perform_read_characteristic(address, char_uuid), loop)
            future.result()
        except Exception as e:
            self.error_occurred.emit(f"読み込みエラー: {e}")

    async def _perform_read_characteristic(self, address: str, char_uuid: str):
        client = self._clients[address]
        value = await client.read_gatt_char(char_uuid)
        self.characteristic_read.emit(address, char_uuid, list(value))

    # キャラクタリスティック書き込み
    @Slot(str, str, list)
    def write_characteristic(self, address: str, char_uuid: str, value_list: List[int]):
        loop = self._ensure_event_loop()
        if address not in self._clients:
            self.error_occurred.emit(f"デバイス {address} は接続されていません。")
            return
        try:
            future = asyncio.run_coroutine_threadsafe(
                self._perform_write_characteristic(address, char_uuid, bytes(value_list)), loop
            )
            future.result()
        except Exception as e:
            self.error_occurred.emit(f"書き込みエラー: {e}")

    async def _perform_write_characteristic(self, address: str, char_uuid: str, value: bytes):
        client = self._clients[address]
        await client.write_gatt_char(char_uuid, value)
        self.characteristic_write_ack.emit(address, char_uuid)

    # 通知開始
    @Slot(str, str)
    def start_notify(self, address: str, char_uuid: str):
        loop = self._ensure_event_loop()
        if address not in self._clients:
            self.error_occurred.emit(f"デバイス {address} は接続されていません。")
            return
        try:
            future = asyncio.run_coroutine_threadsafe(
                self._perform_start_notify(address, char_uuid), loop
            )
            future.result()
        except Exception as e:
            self.error_occurred.emit(f"通知開始エラー: {e}")

    async def _perform_start_notify(self, address: str, char_uuid: str):
        client = self._clients[address]

        if address not in self._notification_metrics:
            self._notification_metrics[address] = {
                "last_timestamp": time.monotonic(),
                "timestamps": deque(maxlen=RATE_BUFFER_SIZE),
                "current_rate": 0.0,
                "current_delay": 0.0,
                "notify_char_uuid": char_uuid
            }
        else:
            metrics = self._notification_metrics[address]
            metrics["last_timestamp"] = time.monotonic()
            metrics["timestamps"].clear()
            metrics["current_rate"] = 0.0
            metrics["current_delay"] = 0.0
            metrics["notify_char_uuid"] = char_uuid

        async def notification_handler(sender: int, data: bytearray):
            current_time = time.monotonic()
            metrics = self._notification_metrics.get(address)
            if metrics:
                metrics["timestamps"].append(current_time)
                if len(metrics["timestamps"]) >= 2:
                    instant_delay = metrics["timestamps"][-1] - metrics["timestamps"][-2]
                    if len(metrics["timestamps"]) == RATE_BUFFER_SIZE:
                        total_time = metrics["timestamps"][-1] - metrics["timestamps"][0]
                        if total_time > 0:
                            metrics["current_rate"] = (RATE_BUFFER_SIZE - 1) / total_time
                            metrics["current_delay"] = total_time / (RATE_BUFFER_SIZE - 1) * 1000
                        else:
                            metrics["current_rate"] = float('inf')
                            metrics["current_delay"] = 0.0
                    else:
                        if instant_delay > 0:
                            metrics["current_rate"] = 1.0 / instant_delay
                            metrics["current_delay"] = instant_delay * 1000
                        else:
                            metrics["current_rate"] = float('inf')
                            metrics["current_delay"] = 0.0

                self.notification_rate_updated.emit({
                    "address": address,
                    "char_uuid": char_uuid,
                    "rate_hz": metrics["current_rate"],
                    "delay_ms": metrics["current_delay"]
                })

                # 早押しボタン通知処理
                if len(data) >= 1:
                    button_id = int.from_bytes(data[:1], 'little')
                    await self.record_button_press(address, button_id, current_time)

                self.notification_received.emit(address, char_uuid, list(data))

        await client.start_notify(char_uuid, notification_handler)

    # 通知停止
    @Slot(str, str)
    def stop_notify(self, address: str, char_uuid: str):
        loop = self._ensure_event_loop()
        if address not in self._clients:
            self.error_occurred.emit(f"デバイス {address} は接続されていません。")
            return
        try:
            future = asyncio.run_coroutine_threadsafe(
                self._perform_stop_notify(address, char_uuid), loop
            )
            future.result()
        except Exception as e:
            self.error_occurred.emit(f"通知停止エラー: {e}")

    async def _perform_stop_notify(self, address: str, char_uuid: str):
        client = self._clients[address]
        await client.stop_notify(char_uuid)
        if address in self._notification_metrics:
            del self._notification_metrics[address]

    # クリーンアップ
    @Slot()
    def cleanup(self):
        loop = self._ensure_event_loop()
        try:
            tasks = [client.disconnect() for client in self._clients.values() if client.is_connected]
            if tasks:
                future = asyncio.run_coroutine_threadsafe(asyncio.gather(*tasks, return_exceptions=True), loop)
                future.result()
            self._clients.clear()
            self._connected_target_addresses.clear()
            self._notification_metrics.clear()
            self._button_press_log.clear()
            self._is_game_active = False
            self._winner_address = None
        except Exception as e:
            self.error_occurred.emit(f"クリーンアップエラー: {e}")
