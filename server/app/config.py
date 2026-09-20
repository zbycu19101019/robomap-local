from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from pydantic import BaseModel, Field
from .secure_credentials import _protect, _unprotect


class LocalRobotConfig(BaseModel):
    robot_ip: str = ""
    token: str = ""
    model: str = "xiaomi.vacuum.c108"
    device_id: str = ""
    auto_discovery: bool = True
    last_discovery_reason: str = ""
    timeout_seconds: int = Field(default=5, ge=2, le=20)


class ConnectionView(BaseModel):
    mode: str = "local_miot"
    robot_ip: str = ""
    model: str = "xiaomi.vacuum.c108"
    device_id: str = ""
    token_set: bool = False
    auto_discovery: bool = True
    last_discovery_reason: str = ""
    detail: str = "Sterowanie lokalne po Wi-Fi/LAN. Badawcza łatka AutoPair może automatycznie odnaleźć E5 wyłącznie w bieżącej sieci."


class ConnectionUpdate(BaseModel):
    robot_ip: str = ""
    token: str | None = None
    auto_discovery: bool | None = None


class ConfigStore:
    def __init__(self) -> None:
        data_dir = Path(os.getenv("DATA_DIR", Path(__file__).resolve().parents[2] / "data"))
        data_dir.mkdir(parents=True, exist_ok=True)
        self.path = data_dir / "robot.json"
        self._lock = threading.RLock()
        if not self.path.exists():
            self.save(LocalRobotConfig())

    def load(self) -> LocalRobotConfig:
        with self._lock:
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                encrypted = data.pop('token_dpapi', '')
                if encrypted:
                    data['token'] = _unprotect(encrypted).decode('ascii')
                result = LocalRobotConfig.model_validate(data)
                if result.token and not encrypted and os.name == 'nt':
                    self.save(result)
                return result
            except Exception as exc:
                raise RuntimeError('Nie można odczytać konfiguracji robota. Przywróć kopię lub użyj właściwego konta Windows.') from exc

    def save(self, config: LocalRobotConfig) -> LocalRobotConfig:
        with self._lock:
            data = config.model_dump(mode="json")
            if config.token:
                if len(config.token) != 32 or any(c not in '0123456789abcdefABCDEF' for c in config.token) or config.token.lower() in ('f'*32, '0'*32):
                    raise ValueError('Token musi zawierać 32 poprawne znaki HEX.')
                data['token_dpapi'] = _protect(config.token.encode('ascii'))
                data['token'] = ''
            tmp = self.path.with_suffix('.tmp')
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
            tmp.replace(self.path)
            return config

    def update(self, update: ConnectionUpdate) -> LocalRobotConfig:
        current = self.load()
        token = current.token if update.token is None or not update.token.strip() else update.token.strip()
        changes = {
            "robot_ip": update.robot_ip.strip(),
            "token": token,
        }
        if update.auto_discovery is not None:
            changes["auto_discovery"] = bool(update.auto_discovery)
        return self.save(current.model_copy(update=changes))

    def view(self) -> ConnectionView:
        cfg = self.load()
        return ConnectionView(
            robot_ip=cfg.robot_ip,
            model=cfg.model,
            device_id=cfg.device_id,
            token_set=bool(cfg.token),
            auto_discovery=cfg.auto_discovery,
            last_discovery_reason=cfg.last_discovery_reason,
        )
