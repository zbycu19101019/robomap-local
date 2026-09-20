from __future__ import annotations

import asyncio
import json
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field

from .history import HistoryStore
from .models import RobotActionResult
from .robot import RobotProvider


class MacroStep(BaseModel):
    at_ms: int = Field(ge=0, le=120_000)
    command: str


class MacroDefinition(BaseModel):
    id: str
    name: str
    created_at: str
    duration_ms: int = Field(ge=0, le=120_000)
    steps: list[MacroStep]


class MacroRecordingState(BaseModel):
    recording: bool = False
    name: str | None = None
    elapsed_ms: int = 0
    step_count: int = 0


class MacroStore:
    def __init__(self) -> None:
        data_dir = Path(os.getenv("DATA_DIR", Path(__file__).resolve().parents[2] / "data"))
        data_dir.mkdir(parents=True, exist_ok=True)
        self.path = data_dir / "macros.json"
        self._lock = threading.RLock()
        if not self.path.exists():
            self.path.write_text("[]", encoding="utf-8")

    def list(self) -> list[MacroDefinition]:
        with self._lock:
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                return [MacroDefinition.model_validate(item) for item in raw]
            except Exception:
                return []

    def save_all(self, items: list[MacroDefinition]) -> None:
        with self._lock:
            tmp = self.path.with_suffix('.tmp')
            tmp.write_text(
                json.dumps([m.model_dump(mode="json") for m in items], ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            tmp.replace(self.path)

    def add(self, item: MacroDefinition) -> MacroDefinition:
        items = self.list()
        items.append(item)
        self.save_all(items)
        return item

    def delete(self, macro_id: str) -> bool:
        items = self.list()
        filtered = [m for m in items if m.id != macro_id]
        changed = len(filtered) != len(items)
        if changed:
            self.save_all(filtered)
        return changed

    def get(self, macro_id: str) -> MacroDefinition | None:
        return next((m for m in self.list() if m.id == macro_id), None)


class MacroManager:
    MAX_DURATION_MS = 60_000
    MAX_STEPS = 400
    ALLOWED = {"forward", "back", "left", "right", "stop"}

    def __init__(self, robot: RobotProvider, history: HistoryStore) -> None:
        self.robot = robot
        self.history = history
        self.store = MacroStore()
        self._recording_name: str | None = None
        self._recording_started: float | None = None
        self._steps: list[MacroStep] = []
        self._play_task: asyncio.Task | None = None
        self._playing_id: str | None = None

    def recording_state(self) -> MacroRecordingState:
        elapsed = 0
        if self._recording_started is not None:
            elapsed = int((time.monotonic() - self._recording_started) * 1000)
        return MacroRecordingState(
            recording=self._recording_started is not None,
            name=self._recording_name,
            elapsed_ms=max(0, elapsed),
            step_count=len(self._steps),
        )

    def start_recording(self, name: str) -> MacroRecordingState:
        name = (name or "Nowe makro").strip()[:80] or "Nowe makro"
        self._recording_name = name
        self._recording_started = time.monotonic()
        self._steps = []
        self.history.log("macro", "record_start", source="web", detail=name)
        return self.recording_state()

    def capture_manual(self, command: str) -> None:
        if self._recording_started is None or command not in self.ALLOWED:
            return
        at_ms = int((time.monotonic() - self._recording_started) * 1000)
        if at_ms > self.MAX_DURATION_MS or len(self._steps) >= self.MAX_STEPS:
            return
        # Avoid duplicate consecutive network repeats from held gamepad/button.
        if self._steps and self._steps[-1].command == command:
            return
        self._steps.append(MacroStep(at_ms=at_ms, command=command))

    async def stop_recording(self) -> MacroDefinition | None:
        if self._recording_started is None:
            return None
        duration_ms = min(int((time.monotonic() - self._recording_started) * 1000), self.MAX_DURATION_MS)
        try:
            await self.robot.manual("stop")
        except Exception:
            pass
        if not self._steps or self._steps[-1].command != "stop":
            self._steps.append(MacroStep(at_ms=duration_ms, command="stop"))
        item = MacroDefinition(
            id=uuid.uuid4().hex[:12],
            name=self._recording_name or "Makro",
            created_at=datetime.now(timezone.utc).isoformat(),
            duration_ms=duration_ms,
            steps=self._steps[: self.MAX_STEPS],
        )
        self._recording_name = None
        self._recording_started = None
        self._steps = []
        if len(item.steps) <= 1:
            self.history.log("macro", "record_discarded", source="web", detail="Brak ruchów")
            return None
        self.store.add(item)
        self.history.log("macro", "record_saved", source="web", detail=item.name)
        return item

    async def cancel_recording(self) -> None:
        self._recording_name = None
        self._recording_started = None
        self._steps = []
        await self.robot.manual("stop")
        self.history.log("macro", "record_cancel", source="web")

    def list(self) -> list[MacroDefinition]:
        return self.store.list()

    async def play(self, macro_id: str) -> RobotActionResult:
        macro = self.store.get(macro_id)
        if macro is None:
            return RobotActionResult(ok=False, action="macro:play", detail="Nie znaleziono makra")
        status = await self.robot.status()
        if status.state in {"offline", "unconfigured", "awaiting-token", "error"}:
            return RobotActionResult(ok=False, action="macro:play", detail="Robot jest offline lub nieskonfigurowany")
        await self.stop_playback()
        self._playing_id = macro.id
        self._play_task = asyncio.create_task(self._run_macro(macro), name=f"robomap-macro-{macro.id}")
        self.history.log("macro", "play_start", source="web", detail=macro.name)
        return RobotActionResult(ok=True, action="macro:play", detail=f"Uruchomiono: {macro.name}")

    async def _run_macro(self, macro: MacroDefinition) -> None:
        started = time.monotonic()
        try:
            for step in macro.steps:
                target = started + (step.at_ms / 1000.0)
                delay = target - time.monotonic()
                while delay > 0:
                    await asyncio.sleep(min(delay, .4))
                    if self.robot.direction != 'stop':
                        result = await self.robot.keepalive(self.robot.direction)
                        if not result.ok:
                            raise RuntimeError(result.detail)
                    delay = target - time.monotonic()
                result = await self.robot.manual(step.command)
                self.history.log(
                    "command",
                    f"manual:{step.command}",
                    source=f"macro:{macro.name}",
                    detail=result.detail,
                )
                if not result.ok:
                    raise RuntimeError(result.detail or "Robot odrzucił komendę makra")
            self.history.log("macro", "play_done", source="system", detail=macro.name)
        except asyncio.CancelledError:
            self.history.log("macro", "play_cancel", source="system", detail=macro.name)
            raise
        except Exception as exc:
            self.history.log("error", "macro", source="system", detail=str(exc))
        finally:
            try:
                await self.robot.manual("stop")
            except Exception:
                pass
            self._playing_id = None
            self._play_task = None

    async def stop_playback(self) -> None:
        task = self._play_task
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
        self._play_task = None
        self._playing_id = None
        try:
            await self.robot.manual("stop")
        except Exception:
            pass

    def playback_state(self) -> dict:
        return {"playing": self._play_task is not None and not self._play_task.done(), "macro_id": self._playing_id}
