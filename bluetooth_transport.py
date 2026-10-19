"""
Windows RFCOMM transport for Soundcore headphones, via WinRT.

Python's stdlib `socket` on Windows does NOT expose AF_BTH, and pybluez2 has no
prebuilt wheel for Python 3.14. So this uses the Windows Runtime (WinRT)
Bluetooth API through the `winrt-*` packages (pure prebuilt wheels):

    BluetoothDevice.from_bluetooth_address_async(addr)
      -> get_rfcomm_services_async()
      -> StreamSocket.connect_async(host, service_name)
      -> DataWriter / DataReader for raw RFCOMM bytes

The device must already be paired in Windows Bluetooth settings. WinRT connects
to the advertised SerialPort / control RFCOMM service rather than sweeping raw
channel numbers, which is the OS-native equivalent of CoreSound's channel sweep.
"""

import asyncio
import threading
import time
import subprocess
import json
import re

import soundcore_protocol as proto

from winrt.windows.devices.bluetooth import BluetoothDevice
from winrt.windows.devices.bluetooth.rfcomm import RfcommServiceId
from winrt.windows.networking.sockets import StreamSocket
from winrt.windows.storage.streams import DataWriter, DataReader, InputStreamOptions
import winrt.windows.networking  # noqa: F401 - registers HostName type for RFCOMM connect


def _mac_to_int(mac: str) -> int:
    return int(mac.replace(":", "").replace("-", ""), 16)


def list_paired_audio_devices():
    """Return [{'mac','name','connected'}] for paired Bluetooth audio devices."""
    ps = (
        "Get-PnpDevice -Class Bluetooth -ErrorAction SilentlyContinue | "
        "Select-Object FriendlyName,InstanceId,Status | "
        "ConvertTo-Json -Compress -Depth 1"
    )
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, text=True, timeout=20,
        ).stdout.strip()
    except Exception:
        return []
    if not out:
        return []
    try:
        raw = json.loads(out)
    except json.JSONDecodeError:
        return []
    items = raw if isinstance(raw, list) else [raw]

    avrcp_macs = set()
    for d in items:
        iid = d.get("InstanceId", "") or ""
        if "{0000110C-" not in iid and "{0000110E-" not in iid:
            continue
        m = re.search(r"([0-9A-Fa-f]{12})_C00000000$", iid)
        if m:
            avrcp_macs.add(m.group(1).upper())

    by_mac = {}
    for d in items:
        iid = d.get("InstanceId", "") or ""
        m = re.search(r"DEV_([0-9A-Fa-f]{12})", iid)
        if not m:
            continue
        raw_mac = m.group(1).upper()
        if raw_mac not in avrcp_macs:
            continue
        mac = ":".join(raw_mac[i:i + 2] for i in range(0, 12, 2))
        status_ok = d.get("Status") == "OK"
        existing = by_mac.get(mac)
        if not existing or status_ok:
            by_mac[mac] = {"mac": mac, "name": d.get("FriendlyName") or "Unknown Device",
                           "connected": status_ok}
    return list(by_mac.values())


class SoundcoreConnection:
    """A live RFCOMM session to one Soundcore device, driven on a private asyncio loop."""

    def __init__(self, on_battery=None, on_mode=None, on_device_info=None, log=None):
        self.mac = None
        self.on_battery = on_battery
        self.on_mode = on_mode
        self.on_device_info = on_device_info
        self._log = log or (lambda *a: None)

        self.battery = None
        self.mode = None
        self.device_info = None

        self._socket = None
        self._writer = None
        self._reader = None
        self._rx = bytearray()
        self._last_frame_at = 0.0
        self._io_lock = None  # created on the loop in connect()

        self._loop = asyncio.new_event_loop()
        self._loop_thread = threading.Thread(target=self._run_loop, daemon=True)
        self._loop_thread.start()
        self._reader_task = None
        self._connected = False

    # -- loop plumbing ------------------------------------------------------
    def _run_loop(self):
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def _call(self, coro, timeout=20):
        fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return fut.result(timeout=timeout)

    # -- connect ------------------------------------------------------------
    def connect(self, mac: str):
        self.mac = mac
        self._call(self._connect_async(mac), timeout=25)
        self._connected = True
        self._reader_task = asyncio.run_coroutine_threadsafe(self._read_loop(), self._loop)
        # Kick off an info query so battery/mode populate.
        time.sleep(0.3)
        try:
            self.request_info()
        except Exception:
            pass
        return True

    async def _connect_async(self, mac: str):
        self._io_lock = asyncio.Lock()
        addr = _mac_to_int(mac)
        device = await BluetoothDevice.from_bluetooth_address_async(addr)
        if device is None:
            raise ConnectionError(
                "Windows could not resolve the paired device. Make sure the Q20i is "
                "paired in Bluetooth settings and powered on."
            )
        result = await device.get_rfcomm_services_async()
        services = list(result.services) if result and result.services else []
        if not services:
            raise ConnectionError(
                "No RFCOMM services exposed by the device. Toggle it off/on, or turn off "
                "multipoint / disconnect it from your phone, then retry."
            )

        # The Q20i (and most Soundcore headphones) expose control on a
        # VENDOR-specific RFCOMM service, not SerialPort/Handsfree. Its UUID
        # ends with the model code (e.g. ...-...3004 for the A3004 Q20i).
        # Prefer vendor UUIDs first, then SerialPort, then the rest.
        serial_uuid = str(RfcommServiceId.serial_port.uuid).lower()
        standard = {
            "00001101-0000-1000-8000-00805f9b34fb",  # SerialPort
            "0000111e-0000-1000-8000-00805f9b34fb",  # Handsfree
            "0000111f-0000-1000-8000-00805f9b34fb",  # Handsfree AG
            "0000110a-0000-1000-8000-00805f9b34fb",  # A2DP source
            "0000110b-0000-1000-8000-00805f9b34fb",  # A2DP sink
            "0000110c-0000-1000-8000-00805f9b34fb",  # AVRCP target
            "0000110e-0000-1000-8000-00805f9b34fb",  # AVRCP control
        }

        def rank(s):
            u = str(s.service_id.uuid).lower()
            if u not in standard:
                return 0          # vendor service -> try first
            if u == serial_uuid:
                return 1
            return 2
        services.sort(key=rank)

        last_err = None
        for svc in services:
            sock = StreamSocket()
            try:
                await sock.connect_async(svc.connection_host_name, svc.connection_service_name)
            except Exception as e:  # noqa: BLE001 - WinRT raises OSError-ish
                last_err = e
                try:
                    sock.close()
                except Exception:
                    pass
                continue
            self._socket = sock
            self._writer = DataWriter(sock.output_stream)
            self._reader = DataReader(sock.input_stream)
            self._reader.input_stream_options = InputStreamOptions.PARTIAL
            # Probe: does it speak the Soundcore protocol?
            if await self._probe_async():
                self._log("Connected via WinRT RFCOMM "
                          f"({svc.connection_service_name})")
                return
            # wrong service -> close and try next
            try:
                sock.close()
            except Exception:
                pass
            self._socket = None
        if last_err:
            raise ConnectionError(f"RFCOMM connect failed: {last_err}")
        raise ConnectionError("Connected but no Soundcore control response on any service.")

    async def _probe_async(self, attempts=5, wait=0.5):
        start = time.time()
        for _ in range(attempts):
            try:
                await self._write_async(proto.build_device_info_query())
            except Exception:
                return False
            # read whatever is available in a short window
            end = time.time() + wait
            while time.time() < end:
                await self._pump_once()
                if self._last_frame_at > start:
                    return True
                await asyncio.sleep(0.05)
        return False

    # -- io -----------------------------------------------------------------
    async def _write_async(self, data: bytes):
        if not self._writer:
            raise ConnectionError("not connected")
        async with self._io_lock:
            self._writer.write_bytes(bytes(data))
            await self._writer.store_async()

    async def _pump_once(self, want=256):
        """Load available bytes (PARTIAL: returns as soon as >=1 byte arrives)."""
        if not self._reader:
            return 0
        async with self._io_lock:
            try:
                count = await self._reader.load_async(want)
            except Exception:
                return 0
            if not count:
                return 0
            buf = bytearray(count)
            self._reader.read_bytes(buf)
        chunk = bytes(buf)
        self._rx.extend(chunk)
        frames, remaining = proto.parse_soundcore_frames(bytes(self._rx))
        self._rx = bytearray(remaining)
        for cat, type_, payload in frames:
            self._handle_frame(cat, type_, payload)
        return count

    async def _read_loop(self):
        # PARTIAL load_async returns on the first available byte, so this never
        # blocks forever and leaves room for writes to take the io lock.
        while self._connected and self._socket:
            n = await self._pump_once()
            if n == 0:
                await asyncio.sleep(0.05)

    def _handle_frame(self, cat, type_, payload):
        self._last_frame_at = time.time()
        if cat == 0x01 and type_ == 0x7F:
            asyncio.run_coroutine_threadsafe(
                self._write_async(proto.build_device_info_query()), self._loop)
        elif cat == 0x01 and type_ == 0x01:
            info = proto.extract_device_info(payload)
            if info and not self.device_info:
                self.device_info = info
                if self.on_device_info:
                    self.on_device_info(info)
            batt = proto.extract_battery(payload)
            if not batt:
                batt = proto.extract_single_battery(payload)
            if batt:
                self.battery = batt
                if self.on_battery:
                    self.on_battery(batt)
            m = proto.extract_mode_from_info(payload)
            if m:
                merged = dict(self.mode or {})
                merged.update(m)
                self.mode = merged
                if self.on_mode:
                    self.on_mode(self.mode)
        elif cat == 0x06 and type_ == 0x01:
            mode = proto.extract_mode(payload)
            if mode:
                self.mode = mode
                if self.on_mode:
                    self.on_mode(mode)

    # -- public control API -------------------------------------------------
    def _send(self, data: bytes):
        self._call(self._write_async(data), timeout=10)

    def request_info(self):
        self._send(proto.build_device_info_query())

    def set_mode(self, mode, anc_scene="level5", transparency_scene="vocal", wind=False):
        self._send(proto.build_mode_command(mode, anc_scene, transparency_scene, wind))

    def set_eq_preset(self, preset_id):
        self._send(proto.build_eq_command(preset_id))

    def set_custom_eq(self, bands):
        self._send(proto.build_custom_eq_command(bands))

    def set_dolby(self):
        self._send(proto.build_dolby_command())

    def disconnect(self):
        self._connected = False
        try:
            if self._socket:
                self._socket.close()
        except Exception:
            pass
        self._socket = None
        self._writer = None
        self._reader = None
        try:
            self._loop.call_soon_threadsafe(self._loop.stop)
        except Exception:
            pass


def _awaitable(winrt_async_op):  # retained for reference; no longer used
    raise NotImplementedError


if __name__ == "__main__":
    print("Paired Bluetooth audio devices:")
    for d in list_paired_audio_devices():
        flag = "  [connected]" if d["connected"] else ""
        print(f"  {d['mac']}  {d['name']}{flag}")
