from __future__ import annotations

import asyncio
import math
import time
from abc import ABC, abstractmethod

from .config import ConfigStore
from .models import RobotActionResult, RobotPose, RobotStatus


class RobotProvider(ABC):
    @abstractmethod
    async def status(self) -> RobotStatus: ...

    @abstractmethod
    async def action(self, name: str) -> RobotActionResult: ...

    async def manual(self, direction: str) -> RobotActionResult:
        return RobotActionResult(ok=False, action=f"manual:{direction}", detail="Sterowanie ręczne niedostępne")

    async def set_suction(self, level: int) -> RobotActionResult:
        return RobotActionResult(ok=False, action="suction", detail="Zmiana mocy niedostępna")

    async def set_no_disturb(self, enabled: bool) -> RobotActionResult:
        return RobotActionResult(ok=False, action="no-disturb", detail="Tryb cichy niedostępny")

    async def get_no_disturb_config(self) -> dict:
        return {"enabled": None, "period": None}

    async def set_no_disturb_window(self, enabled: bool, period: str) -> RobotActionResult:
        return await self.set_no_disturb(enabled)

    async def test_connection(self) -> RobotActionResult:
        status = await self.status()
        return RobotActionResult(ok=status.state not in {"offline", "unconfigured"}, action="test", detail=status.detail)


class MockRobotProvider(RobotProvider):
    def __init__(self) -> None:
        self.state = "docked"
        self.battery = 87
        self.started_at: float | None = None

    async def status(self) -> RobotStatus:
        pose = RobotPose(x=0.45, y=3.7, heading=0)
        if self.state == "cleaning" and self.started_at is not None:
            t = time.monotonic() - self.started_at
            pose = RobotPose(
                x=2.8 + math.cos(t / 3.0) * 1.55,
                y=2.05 + math.sin(t / 2.2) * 1.2,
                heading=(t * 0.65) % (math.pi * 2),
            )
        return RobotStatus(
            provider="mock",
            state=self.state,
            battery=self.battery,
            pose=pose,
            supports_pose=True,
            supports_software_no_go=False,
            detail="Tryb demonstracyjny — nie steruje prawdziwym robotem.",
        )

    async def action(self, name: str) -> RobotActionResult:
        if name in {"start", "resume"}:
            self.state = "cleaning"
            self.started_at = time.monotonic()
        elif name == "pause":
            self.state = "paused"
        elif name == "stop":
            self.state = "idle"
            self.started_at = None
        elif name == "dock":
            self.state = "returning"
            self.started_at = None
        else:
            return RobotActionResult(ok=False, action=name, detail="Nieznana akcja")
        return RobotActionResult(ok=True, action=name)


class LocalMiotRobotProvider(RobotProvider):
    """Bezpośrednie sterowanie Xiaomi E5 przez lokalny protokół MIoT/miIO.

    Nie używa Home Assistanta ani zewnętrznego backendu. Komputer i robot muszą
    widzieć się w tej samej sieci IP, a aplikacja potrzebuje lokalnego tokenu miIO.
    """

    STATUS_NAMES = {
        1: "idle",
        2: "cleaning",
        3: "paused",
        4: "error",
        5: "charging",
        6: "returning",
        7: "charged",
    }
    ACTIONS = {
        "start": (2, 1),
        "stop": (2, 2),
        "pause": (2, 3),
        "resume": (2, 4),
        "dock": (2, 8),  # stop-and-gocharge
    }
    DIRECTIONS = {
        "left": 0,
        "right": 1,
        "forward": 2,
        "back": 3,
        "stop": 4,
    }

    def __init__(self, config_store: ConfigStore) -> None:
        self.config_store = config_store

    @staticmethod
    def _value(reply):
        if isinstance(reply, list) and reply:
            first = reply[0]
            if isinstance(first, dict):
                if first.get("code", 0) != 0:
                    raise RuntimeError(f"MIoT code {first.get('code')}")
                return first.get("value")
            return first
        if isinstance(reply, dict):
            if reply.get("code", 0) != 0:
                raise RuntimeError(f"MIoT code {reply.get('code')}")
            return reply.get("value")
        return reply

    def _device(self, timeout_override: float | None = None):
        cfg = self.config_store.load()
        if not cfg.robot_ip or not cfg.token:
            raise RuntimeError("E5 nie jest jeszcze w pełni skonfigurowany. AutoPair/AutoToken powinien uzupełnić IP i token automatycznie.")
        try:
            from miio.miot_device import MiotDevice
        except Exception as exc:
            raise RuntimeError("Brak biblioteki python-miio. Uruchom install_windows.bat lub pip install -r requirements.txt") from exc
        return MiotDevice(
            ip=cfg.robot_ip,
            token=cfg.token,
            model=cfg.model,
            timeout=timeout_override if timeout_override is not None else cfg.timeout_seconds,
            lazy_discover=True,
        )

    def _read_status_sync(self) -> RobotStatus:
        dev = self._device()
        status_raw = self._value(dev.get_property_by(2, 1))
        battery_raw = self._value(dev.get_property_by(3, 1))
        fault = None
        try:
            fault = int(self._value(dev.get_property_by(2, 2)))
        except Exception:
            pass
        faults = {1: 'Zablokowane lewe koło', 2: 'Zablokowane prawe koło',
                  3: 'Zablokowany zderzak', 4: 'Zablokowana szczotka boczna',
                  5: 'Czujnik spadku', 6: 'Koło w powietrzu', 7: 'Błąd wentylatora',
                  8: 'Niska bateria', 9: 'Błąd ładowania', 10: 'Robot uwięziony',
                  11: 'Błąd lewego koła', 12: 'Błąd prawego koła',
                  13: 'Błąd szczotki bocznej', 14: 'Błąd baterii', 15: 'Nachylenie podłoża'}
        try:
            state_code = int(status_raw)
        except (TypeError, ValueError):
            state_code = -1
        try:
            battery = int(battery_raw) if battery_raw is not None else None
        except (TypeError, ValueError):
            battery = None
        return RobotStatus(
            provider="local_miot",
            state='error' if fault else self.STATUS_NAMES.get(state_code, f"status-{status_raw}"),
            error_code=fault, error_name=faults.get(fault, f'Błąd urządzenia {fault}' if fault else None),
            bumper_hit=fault == 3, bumper_supported=fault is not None,
            battery=battery,
            pose=None,
            supports_pose=False,
            supports_software_no_go=False,
            detail="Połączenie bezpośrednie z E5 przez lokalne Wi-Fi/LAN (miIO/MIoT).",
        )

    async def status(self) -> RobotStatus:
        cfg = self.config_store.load()
        if not cfg.robot_ip:
            return RobotStatus(
                provider="local_miot",
                state="unconfigured",
                supports_pose=False,
                supports_software_no_go=False,
                detail="RoboMap nie znalazł jeszcze E5 w sieci lokalnej.",
            )
        if not cfg.token:
            return RobotStatus(
                provider="local_miot",
                state="awaiting-token",
                supports_pose=False,
                supports_software_no_go=False,
                detail=f"E5 wykryty pod {cfg.robot_ip}. Sparowany robot ukrywa token w LAN; RoboMap przechodzi do automatycznego AutoToken. Jeśli nie ma jeszcze autoryzacji Xiaomi, otwórz Ustawienia i połącz konto jeden raz.",
            )
        try:
            return await asyncio.to_thread(self._read_status_sync)
        except Exception as exc:
            return RobotStatus(
                provider="local_miot",
                state="offline",
                supports_pose=False,
                supports_software_no_go=False,
                detail=f"Brak odpowiedzi z robota: {exc}",
            )

    def _action_sync(self, name: str) -> RobotActionResult:
        spec = self.ACTIONS.get(name)
        if spec is None:
            return RobotActionResult(ok=False, action=name, detail="Nieznana akcja")
        dev = self._device()
        siid, aiid = spec
        reply = dev.call_action_by(siid, aiid, [])
        self._value(reply)
        return RobotActionResult(ok=True, action=name, detail=str(reply))

    async def action(self, name: str) -> RobotActionResult:
        try:
            return await asyncio.to_thread(self._action_sync, name)
        except Exception as exc:
            return RobotActionResult(ok=False, action=name, detail=str(exc))

    def _manual_sync(self, direction: str) -> RobotActionResult:
        if direction not in self.DIRECTIONS:
            return RobotActionResult(ok=False, action=f"manual:{direction}", detail="Nieznany kierunek")
        # Manual steering must fail fast. A lost UDP response must never block a later STOP
        # for the normal 5 s status/action timeout. Each command gets a fresh MiotDevice.
        dev = self._device(timeout_override=1.0)
        reply = dev.set_property_by(13, 1, self.DIRECTIONS[direction])
        self._value(reply)
        return RobotActionResult(ok=True, action=f"manual:{direction}", detail=str(reply))

    async def manual(self, direction: str) -> RobotActionResult:
        try:
            # Hard upper bound for RC traffic. Even if python-miio waits longer internally,
            # RoboMap releases the controller so a STOP/watchdog can proceed.
            return await asyncio.wait_for(asyncio.to_thread(self._manual_sync, direction), timeout=1.35)
        except asyncio.TimeoutError:
            return RobotActionResult(ok=False, action=f"manual:{direction}", detail="Timeout sterowania ręcznego (1.35 s)")
        except Exception as exc:
            return RobotActionResult(ok=False, action=f"manual:{direction}", detail=str(exc))

    def _suction_sync(self, level: int) -> RobotActionResult:
        if level not in {0, 1, 2}:
            return RobotActionResult(ok=False, action="suction", detail="Moc musi mieć wartość 0, 1 lub 2")
        dev = self._device()
        reply = dev.set_property_by(2, 5, level)
        self._value(reply)
        return RobotActionResult(ok=True, action="suction", detail=str(reply))

    async def set_suction(self, level: int) -> RobotActionResult:
        try:
            return await asyncio.to_thread(self._suction_sync, level)
        except Exception as exc:
            return RobotActionResult(ok=False, action="suction", detail=str(exc))

    def _no_disturb_sync(self, enabled: bool) -> RobotActionResult:
        # Xiaomi Robot Vacuum E5: service 10 (no-disturb), property 1 (no-disturb).
        # The boolean only enables DND; the actual mute is active inside property 10/2
        # (enable-time-period). For RC we therefore use set_no_disturb_window().
        dev = self._device(timeout_override=1.5)
        reply = dev.set_property_by(10, 1, bool(enabled))
        self._value(reply)
        return RobotActionResult(ok=True, action="no-disturb", detail=f"no-disturb={bool(enabled)}")

    async def set_no_disturb(self, enabled: bool) -> RobotActionResult:
        try:
            return await asyncio.wait_for(asyncio.to_thread(self._no_disturb_sync, enabled), timeout=1.9)
        except asyncio.TimeoutError:
            return RobotActionResult(ok=False, action="no-disturb", detail="Timeout ustawiania trybu cichego")
        except Exception as exc:
            return RobotActionResult(ok=False, action="no-disturb", detail=str(exc))

    def _get_no_disturb_config_sync(self) -> dict:
        dev = self._device(timeout_override=1.5)
        enabled = self._value(dev.get_property_by(10, 1))
        period = self._value(dev.get_property_by(10, 2))
        return {"enabled": bool(enabled), "period": str(period or "")}

    async def get_no_disturb_config(self) -> dict:
        try:
            return await asyncio.wait_for(asyncio.to_thread(self._get_no_disturb_config_sync), timeout=2.5)
        except Exception:
            # If the firmware refuses reading the period, do not block RC.
            return {"enabled": None, "period": None}

    def _set_no_disturb_window_sync(self, enabled: bool, period: str) -> RobotActionResult:
        dev = self._device(timeout_override=1.5)
        payload = [
            {"did": "robomap-dnd-period", "siid": 10, "piid": 2, "value": str(period)},
            {"did": "robomap-dnd-enable", "siid": 10, "piid": 1, "value": bool(enabled)},
        ]
        reply = dev.send("set_properties", payload)
        # Validate every MIoT result; set_properties normally returns one result per item.
        if isinstance(reply, list):
            for item in reply:
                self._value(item)
        else:
            self._value(reply)
        return RobotActionResult(ok=True, action="no-disturb-window", detail=f"DND {period}, enabled={bool(enabled)}")

    async def set_no_disturb_window(self, enabled: bool, period: str) -> RobotActionResult:
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(self._set_no_disturb_window_sync, enabled, period), timeout=2.2
            )
        except asyncio.TimeoutError:
            return RobotActionResult(ok=False, action="no-disturb-window", detail="Timeout ustawiania okna DND")
        except Exception as exc:
            return RobotActionResult(ok=False, action="no-disturb-window", detail=str(exc))

    def _test_sync(self) -> RobotActionResult:
        dev = self._device()
        info = dev.info()
        model = getattr(info, "model", None)
        detail = f"Robot odpowiada lokalnie{f' — {model}' if model else ''}."
        return RobotActionResult(ok=True, action="test", detail=detail)

    async def test_connection(self) -> RobotActionResult:
        try:
            return await asyncio.to_thread(self._test_sync)
        except Exception as exc:
            return RobotActionResult(ok=False, action="test", detail=str(exc))


class RobotManager(RobotProvider):
    def __init__(self, config_store: ConfigStore) -> None:
        self.local = LocalMiotRobotProvider(config_store)

    async def status(self) -> RobotStatus:
        return await self.local.status()

    async def action(self, name: str) -> RobotActionResult:
        return await self.local.action(name)

    async def manual(self, direction: str) -> RobotActionResult:
        return await self.local.manual(direction)

    async def set_suction(self, level: int) -> RobotActionResult:
        return await self.local.set_suction(level)

    async def set_no_disturb(self, enabled: bool) -> RobotActionResult:
        return await self.local.set_no_disturb(enabled)

    async def get_no_disturb_config(self) -> dict:
        return await self.local.get_no_disturb_config()

    async def set_no_disturb_window(self, enabled: bool, period: str) -> RobotActionResult:
        return await self.local.set_no_disturb_window(enabled, period)

    async def test_connection(self) -> RobotActionResult:
        return await self.local.test_connection()


def build_provider(config_store: ConfigStore) -> RobotProvider:
    return RobotManager(config_store)
