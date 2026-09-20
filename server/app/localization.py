from __future__ import annotations

import copy
import json
import math
import threading
from datetime import datetime, timezone
from pathlib import Path


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_heading(value: float) -> float:
    """Normalize radians to [-pi, pi). Heading 0 points to the top of the map."""
    value = float(value)
    return (value + math.pi) % (math.tau) - math.pi


class LocalizationStore:
    """Persistent local pose estimate for E5.

    Xiaomi E5 does not expose a trustworthy XY pose through the public MIoT surface used
    by RoboMap. This store therefore keeps only RoboMap's own calibrated/dead-reckoned
    estimate and labels its confidence/source explicitly.
    """

    def __init__(self, root: Path) -> None:
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / "localization.json"
        self._lock = threading.RLock()
        self._state = self._defaults()
        self._load()

    @staticmethod
    def _defaults() -> dict:
        return {
            "version": 1,
            "pose": {"x": 0.0, "y": 0.0, "heading": 0.0},
            "calibrated": False,
            "source": "uncalibrated",
            "confidence": 0.0,
            "updated_at": _iso_now(),
            "linear_speed_mps": 0.10,
            "turn_rate_dps": 90.0,
            "trail": [],
        }

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            state = self._defaults()
            state.update({k: v for k, v in raw.items() if k in state})
            pose = raw.get("pose") or {}
            state["pose"] = {
                "x": float(pose.get("x", 0.0)),
                "y": float(pose.get("y", 0.0)),
                "heading": normalize_heading(float(pose.get("heading", 0.0))),
            }
            state["confidence"] = max(0.0, min(1.0, float(state.get("confidence", 0.0))))
            state["linear_speed_mps"] = max(0.03, min(0.50, float(state.get("linear_speed_mps", 0.10))))
            state["turn_rate_dps"] = max(20.0, min(360.0, float(state.get("turn_rate_dps", 90.0))))
            state["trail"] = [
                {"x": float(p.get("x", 0.0)), "y": float(p.get("y", 0.0))}
                for p in (state.get("trail") or [])[-1000:]
                if isinstance(p, dict)
            ]
            self._state = state
        except Exception:
            # A corrupt localization estimate must never break robot control.
            self._state = self._defaults()

    def _save_locked(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._state, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def view(self) -> dict:
        with self._lock:
            return copy.deepcopy(self._state)

    def update_pose(
        self,
        *,
        x: float,
        y: float,
        heading: float,
        source: str,
        confidence: float,
        calibrated: bool | None = None,
        append_trail: bool = True,
        persist: bool = True,
    ) -> dict:
        with self._lock:
            self._state["pose"] = {
                "x": round(float(x), 5),
                "y": round(float(y), 5),
                "heading": round(normalize_heading(heading), 7),
            }
            if calibrated is not None:
                self._state["calibrated"] = bool(calibrated)
            self._state["source"] = str(source)[:64]
            self._state["confidence"] = max(0.0, min(1.0, float(confidence)))
            self._state["updated_at"] = _iso_now()
            if append_trail and self._state["calibrated"]:
                self._append_trail_locked(float(x), float(y))
            if persist:
                self._save_locked()
            return copy.deepcopy(self._state)

    def save(self) -> None:
        with self._lock:
            self._save_locked()

    def _append_trail_locked(self, x: float, y: float) -> None:
        trail = self._state.setdefault("trail", [])
        if trail:
            last = trail[-1]
            if math.hypot(float(last["x"]) - x, float(last["y"]) - y) < 0.025:
                return
        trail.append({"x": round(x, 4), "y": round(y, 4)})
        del trail[:-1000]

    def clear_trail(self) -> dict:
        with self._lock:
            self._state["trail"] = []
            pose = self._state.get("pose") or {}
            if self._state.get("calibrated"):
                self._append_trail_locked(float(pose.get("x", 0.0)), float(pose.get("y", 0.0)))
            self._state["updated_at"] = _iso_now()
            self._save_locked()
            return copy.deepcopy(self._state)

    def set_motion_config(self, *, linear_speed_mps: float, turn_rate_dps: float) -> dict:
        linear = float(linear_speed_mps)
        turn = float(turn_rate_dps)
        if not 0.03 <= linear <= 0.50:
            raise ValueError("Prędkość liniowa musi być w zakresie 3–50 cm/s.")
        if not 20 <= turn <= 360:
            raise ValueError("Prędkość obrotu musi być w zakresie 20–360°/s.")
        with self._lock:
            self._state["linear_speed_mps"] = linear
            self._state["turn_rate_dps"] = turn
            self._state["updated_at"] = _iso_now()
            self._save_locked()
            return copy.deepcopy(self._state)

    def degrade(self, amount: float, *, source: str | None = None) -> dict:
        with self._lock:
            self._state["confidence"] = max(0.0, float(self._state.get("confidence", 0.0)) - max(0.0, float(amount)))
            if source:
                self._state["source"] = source
            self._state["updated_at"] = _iso_now()
            self._save_locked()
            return copy.deepcopy(self._state)
