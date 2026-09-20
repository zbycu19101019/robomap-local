from __future__ import annotations

import base64
import ctypes
import json
import os
import threading
from ctypes import wintypes
from pathlib import Path
from typing import Any


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _protect(data: bytes) -> str:
    if os.name != "nt":
        raise RuntimeError("Bezpieczny magazyn AutoToken jest dostępny tylko w Windows.")
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    buf = ctypes.create_string_buffer(data)
    in_blob = _DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)))
    out_blob = _DATA_BLOB()
    if not crypt32.CryptProtectData(ctypes.byref(in_blob), "RoboMap Xiaomi AutoToken", None, None, None, 0, ctypes.byref(out_blob)):
        raise ctypes.WinError()
    try:
        protected = ctypes.string_at(out_blob.pbData, out_blob.cbData)
        return base64.b64encode(protected).decode("ascii")
    finally:
        kernel32.LocalFree(out_blob.pbData)


def _unprotect(value: str) -> bytes:
    if os.name != "nt":
        raise RuntimeError("Bezpieczny magazyn AutoToken jest dostępny tylko w Windows.")
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    raw = base64.b64decode(value.encode("ascii"))
    buf = ctypes.create_string_buffer(raw)
    in_blob = _DATA_BLOB(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)))
    out_blob = _DATA_BLOB()
    if not crypt32.CryptUnprotectData(ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob)):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)


class XiaomiCredentialStore:
    """Stores Xiaomi credentials encrypted with Windows DPAPI for the current user.

    Nothing is returned through the public API except whether a credential exists.
    The encrypted blob is usable only by the same Windows user account on this PC.
    """

    def __init__(self) -> None:
        data_dir = Path(os.getenv("DATA_DIR", Path(__file__).resolve().parents[2] / "data"))
        data_dir.mkdir(parents=True, exist_ok=True)
        self.path = data_dir / "xiaomi_autotoken.dat"
        self._lock = threading.RLock()

    def save(self, username: str, password: str, region: str) -> None:
        payload = json.dumps({
            "username": username.strip(),
            "password": password,
            "region": (region or "all").strip().lower(),
        }, ensure_ascii=False).encode("utf-8")
        protected = _protect(payload)
        with self._lock:
            self.path.write_text(protected, encoding="ascii")

    def load(self) -> dict[str, str] | None:
        with self._lock:
            if not self.path.exists():
                return None
            try:
                payload = json.loads(_unprotect(self.path.read_text(encoding="ascii")).decode("utf-8"))
                username = str(payload.get("username") or "").strip()
                password = str(payload.get("password") or "")
                region = str(payload.get("region") or "all").strip().lower()
                if not username or not password:
                    return None
                return {"username": username, "password": password, "region": region}
            except Exception:
                return None

    def clear(self) -> None:
        with self._lock:
            try:
                self.path.unlink(missing_ok=True)
            except TypeError:
                if self.path.exists():
                    self.path.unlink()

    def status(self) -> dict[str, Any]:
        data = self.load()
        return {
            "available": os.name == "nt",
            "saved": bool(data),
            "region": data.get("region", "") if data else "",
            "username_hint": self._mask_username(data.get("username", "")) if data else "",
        }

    @staticmethod
    def _mask_username(value: str) -> str:
        value = value.strip()
        if not value:
            return ""
        if "@" in value:
            left, right = value.split("@", 1)
            return (left[:2] + "•••@" + right) if left else ("•••@" + right)
        if len(value) <= 4:
            return "••••"
        return value[:2] + "•••" + value[-2:]
