from __future__ import annotations

import asyncio
import ctypes
import json
import os
import platform
import threading
import time
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field, field_validator

from .history import HistoryStore
from .robot import RobotProvider


class AutomationSettings(BaseModel):
    lock_enabled: bool = False
    lock_delay_seconds: int = Field(default=120, ge=5, le=3600)
    dock_on_unlock: bool = True
    min_battery: int = Field(default=30, ge=0, le=100)

    quiet_enabled: bool = False
    quiet_start: str = "22:00"
    quiet_end: str = "07:00"
    quiet_force_suction: bool = True
    quiet_block_lock_automation: bool = True

    @field_validator("quiet_start", "quiet_end")
    @classmethod
    def validate_hhmm(cls, value: str) -> str:
        try:
            h, m = [int(x) for x in value.split(":", 1)]
            if not (0 <= h <= 23 and 0 <= m <= 59):
                raise ValueError
            return f"{h:02d}:{m:02d}"
        except Exception as exc:
            raise ValueError("Czas musi mieć format HH:MM") from exc


class AutomationStore:
    def __init__(self) -> None:
        data_dir = Path(os.getenv("DATA_DIR", Path(__file__).resolve().parents[2] / "data"))
        data_dir.mkdir(parents=True, exist_ok=True)
        self.path = data_dir / "automation.json"
        self._lock = threading.RLock()
        if not self.path.exists():
            self.save(AutomationSettings())

    def load(self) -> AutomationSettings:
        with self._lock:
            try:
                return AutomationSettings.model_validate_json(self.path.read_text(encoding="utf-8"))
            except Exception:
                return AutomationSettings()

    def save(self, settings: AutomationSettings) -> AutomationSettings:
        with self._lock:
            self.path.write_text(
                json.dumps(settings.model_dump(mode="json"), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            return settings


def workstation_locked() -> bool | None:
    """Best-effort local Windows lock detection without pywin32.

    SwitchDesktop is denied while the secure Winlogon desktop is active. This is a
    practical heuristic for Win+L and automatic workstation lock. On non-Windows
    systems the feature is unavailable and returns None.
    """
    if os.name != "nt":
        return None
    try:
        DESKTOP_SWITCHDESKTOP = 0x0100
        user32 = ctypes.windll.user32
        handle = user32.OpenInputDesktop(0, False, DESKTOP_SWITCHDESKTOP)
        if not handle:
            return True
        try:
            can_switch = bool(user32.SwitchDesktop(handle))
            return not can_switch
        finally:
            user32.CloseDesktop(handle)
    except Exception:
        return None


def in_quiet_window(settings: AutomationSettings, now: datetime | None = None) -> bool:
    if not settings.quiet_enabled:
        return False
    now = now or datetime.now()
    current = now.hour * 60 + now.minute
    sh, sm = [int(x) for x in settings.quiet_start.split(":")]
    eh, em = [int(x) for x in settings.quiet_end.split(":")]
    start = sh * 60 + sm
    end = eh * 60 + em
    if start == end:
        return True
    if start < end:
        return start <= current < end
    return current >= start or current < end


class AutomationEngine:
    def __init__(self, robot: RobotProvider, history: HistoryStore, store: AutomationStore) -> None:
        self.robot = robot
        self.history = history
        self.store = store
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._last_state: str | None = None
        self._last_battery: int | None = None
        self._last_locked: bool | None = None
        self._locked_since: float | None = None
        self._lock_start_fired = False
        self._last_quiet_force = 0.0

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(self._run(), name="robomap-automation")
        self.history.log("system", "automation_engine_start", source="system", detail=platform.platform())

    async def stop(self) -> None:
        self._stop.set()
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
        self._task = None

    def presence(self) -> dict:
        return {
            "platform": platform.system(),
            "lock_detection_supported": os.name == "nt",
            "locked": self._last_locked,
            "locked_for_seconds": int(time.monotonic() - self._locked_since) if self._locked_since else 0,
            "lock_start_fired": self._lock_start_fired,
            "quiet_active": in_quiet_window(self.store.load()),
        }

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.history.log("error", "automation_tick", source="system", detail=str(exc))
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                pass

    async def _tick(self) -> None:
        settings = self.store.load()
        locked = workstation_locked()
        await self._handle_presence(locked, settings)

        status = await self.robot.status()
        if status.state != self._last_state:
            self._last_state = status.state
            self.history.log(
                "status",
                "state",
                source="watcher",
                state=status.state,
                battery=status.battery,
                detail=status.detail,
            )
        if status.battery is not None and (
            self._last_battery is None or abs(status.battery - self._last_battery) >= 5
        ):
            self._last_battery = status.battery
            self.history.log(
                "status", "battery", source="watcher", state=status.state, battery=status.battery
            )

        if (
            settings.quiet_enabled
            and settings.quiet_force_suction
            and status.state == "cleaning"
            and in_quiet_window(settings)
            and time.monotonic() - self._last_quiet_force > 45
        ):
            result = await self.robot.set_suction(0)
            self._last_quiet_force = time.monotonic()
            self.history.log(
                "automation",
                "quiet_suction",
                source="night_mode",
                state=status.state,
                battery=status.battery,
                detail=result.detail,
            )

    async def _handle_presence(self, locked: bool | None, settings: AutomationSettings) -> None:
        if locked is None:
            self._last_locked = None
            return
        if self._last_locked is None:
            self._last_locked = locked
            if locked:
                self._locked_since = time.monotonic()
            return

        if locked != self._last_locked:
            self._last_locked = locked
            if locked:
                self._locked_since = time.monotonic()
                self._lock_start_fired = False
                self.history.log("presence", "locked", source="windows")
            else:
                self.history.log("presence", "unlocked", source="windows")
                self._locked_since = None
                if self._lock_start_fired and settings.dock_on_unlock:
                    result = await self.robot.action("dock")
                    self.history.log("automation", "dock_on_unlock", source="windows", detail=result.detail)
                self._lock_start_fired = False

        if not settings.lock_enabled or not locked or self._lock_start_fired or self._locked_since is None:
            return
        if time.monotonic() - self._locked_since < settings.lock_delay_seconds:
            return
        if settings.quiet_block_lock_automation and in_quiet_window(settings):
            return

        status = await self.robot.status()
        if status.state not in {"idle", "paused", "charged", "charging", "docked"}:
            return
        if status.battery is not None and status.battery < settings.min_battery:
            return
        result = await self.robot.action("start")
        self.history.log(
            "automation",
            "start_on_lock",
            source="windows",
            state=status.state,
            battery=status.battery,
            detail=result.detail,
        )
        if result.ok:
            self._lock_start_fired = True
