from __future__ import annotations

import asyncio
import time
from typing import Any

from .cloud_token import fetch_token_once
from .config import ConfigStore
from .discovery import _valid_token
from .secure_credentials import XiaomiCredentialStore


class CloudAutoTokenEngine:
    """Automatically refreshes the local miIO token from the user's own Xiaomi account.

    It only runs when E5 has already been discovered locally, its token is missing,
    and the user explicitly opted to keep Xiaomi credentials encrypted with Windows DPAPI.
    """

    def __init__(self, config_store: ConfigStore, credentials: XiaomiCredentialStore) -> None:
        self.config_store = config_store
        self.credentials = credentials
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self.last_attempt = 0.0
        self.cooldown_seconds = 300
        self.state: dict[str, Any] = {
            "state": "idle",
            "detail": "AutoToken czeka na wykrycie E5.",
            "last_attempt": 0,
            "credential_saved": self.credentials.status().get("saved", False),
        }

    def public_state(self) -> dict[str, Any]:
        cfg = self.config_store.load()
        cred = self.credentials.status()
        out = dict(self.state)
        out.update({
            "token_ready": _valid_token(cfg.token),
            "robot_ip": cfg.robot_ip,
            "credential_saved": bool(cred.get("saved")),
            "credential_region": cred.get("region", ""),
            "username_hint": cred.get("username_hint", ""),
            "secure_store": "windows-dpapi" if cred.get("available") else "unavailable",
        })
        if _valid_token(cfg.token):
            out["state"] = "ready"
            out["detail"] = "Token miIO jest zapisany. Sterowanie działa lokalnie."
        elif cfg.robot_ip and not cred.get("saved"):
            out["state"] = "authorization-required"
            out["detail"] = "E5 wykryty. Potrzebne jest jednorazowe uwierzytelnienie konta Xiaomi, ponieważ sparowany robot ukrywa token w LAN."
        elif cfg.robot_ip and cred.get("saved") and out.get("state") == "idle":
            out["state"] = "queued"
            out["detail"] = "E5 wykryty. AutoToken pobierze token automatycznie z zapisanego, zaszyfrowanego poświadczenia Xiaomi."
        return out

    async def try_now(self, *, force: bool = False) -> dict[str, Any]:
        async with self._lock:
            cfg = self.config_store.load()
            if _valid_token(cfg.token):
                self.state = {"state": "ready", "detail": "Token miIO jest już zapisany.", "last_attempt": int(time.time())}
                return self.public_state()
            if not cfg.robot_ip:
                self.state = {"state": "waiting-robot", "detail": "Najpierw AutoPair musi znaleźć E5 w sieci lokalnej.", "last_attempt": int(time.time())}
                return self.public_state()
            creds = self.credentials.load()
            if not creds:
                self.state = {"state": "authorization-required", "detail": "E5 został znaleziony, ale nie ma bezpiecznie zapisanej autoryzacji Xiaomi.", "last_attempt": int(time.time())}
                return self.public_state()
            now = time.monotonic()
            if not force and now - self.last_attempt < self.cooldown_seconds:
                return self.public_state()
            self.last_attempt = now
            self.state = {"state": "fetching", "detail": "AutoToken loguje się do Xiaomi i dopasowuje dokładnie xiaomi.vacuum.c108…", "last_attempt": int(time.time())}
            try:
                result = await asyncio.to_thread(
                    fetch_token_once,
                    self.config_store,
                    creds["username"],
                    creds["password"],
                    creds.get("region", "all"),
                )
                self.state = {
                    "state": "ready",
                    "detail": result.detail,
                    "last_attempt": int(time.time()),
                    "region": result.region,
                }
            except Exception as exc:
                self.state = {
                    "state": "error",
                    "detail": str(exc),
                    "last_attempt": int(time.time()),
                }
            return self.public_state()

    async def _run(self) -> None:
        await asyncio.sleep(2.0)
        while True:
            try:
                state = self.public_state()
                if state.get("state") in {"queued", "error"}:
                    await self.try_now(force=False)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.state = {"state": "error", "detail": f"AutoToken background: {type(exc).__name__}: {exc}", "last_attempt": int(time.time())}
            await asyncio.sleep(15)

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="robomap-cloud-autotoken")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
