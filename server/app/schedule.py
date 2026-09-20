import asyncio
import json
from datetime import datetime
from pathlib import Path
from pydantic import BaseModel, Field, field_validator
from typing import Literal
from .automation import in_quiet_window


class ScheduleEntry(BaseModel):
    id: str = Field(min_length=1, max_length=60)
    days: list[int] = Field(min_length=1, max_length=7)
    time: str
    action: Literal['start', 'dock', 'pause'] = 'start'
    enabled: bool = True

    @field_validator('days')
    @classmethod
    def valid_days(cls, days):
        if any(d not in range(7) for d in days):
            raise ValueError('Dni tygodnia: 0–6')
        return sorted(set(days))

    @field_validator('time')
    @classmethod
    def valid_time(cls, value):
        return datetime.strptime(value, '%H:%M').strftime('%H:%M')


class ScheduleDocument(BaseModel):
    entries: list[ScheduleEntry] = Field(default_factory=list, max_length=30)


class Scheduler:
    def __init__(self, robot, automation_store, history, root):
        self.robot, self.automation, self.history = robot, automation_store, history
        self.path = Path(root) / 'schedule.json'
        self.fired_path = Path(root) / 'schedule_fired.json'
        self.fired = json.loads(self.fired_path.read_text()) if self.fired_path.exists() else {}
        self.task = None

    def load(self):
        return ScheduleDocument.model_validate_json(self.path.read_text(encoding='utf-8')) if self.path.exists() else ScheduleDocument()

    def save(self, doc):
        if len({e.id for e in doc.entries}) != len(doc.entries):
            raise ValueError('Identyfikatory harmonogramu muszą być unikalne.')
        tmp = self.path.with_suffix('.tmp')
        tmp.write_text(doc.model_dump_json(indent=2), encoding='utf-8')
        tmp.replace(self.path)
        return doc

    async def tick(self, now=None):
        now = now or datetime.now()
        minute, day = now.strftime('%H:%M'), now.date().isoformat()
        for entry in self.load().entries:
            if not entry.enabled or now.weekday() not in entry.days or entry.time != minute:
                continue
            key = f'{entry.id}:{minute}'
            if self.fired.get(key) == day:
                continue
            self.fired = {k:v for k,v in self.fired.items() if v == day}
            self.fired[key] = day
            tmp = self.fired_path.with_suffix('.tmp')
            tmp.write_text(json.dumps(self.fired))
            tmp.replace(self.fired_path)
            settings = self.automation.load()
            status = await self.robot.status()
            if entry.action == 'start' and (in_quiet_window(settings, now) or status.state not in ('idle', 'paused', 'charged', 'charging', 'docked') or status.battery is None or status.battery < settings.min_battery):
                self.history.log('schedule', 'skipped', source='local', detail='Cisza nocna, stan robota lub bateria blokuje start.')
                continue
            result = await self.robot.action(entry.action)
            self.history.log('schedule', entry.action, source='local', detail=result.detail or str(result.ok))

    async def run(self):
        while True:
            try:
                await self.tick()
            except Exception as exc:
                self.history.log('error', 'schedule', source='local', detail=str(exc))
            await asyncio.sleep(5)

    async def start(self):
        self.task = asyncio.create_task(self.run())

    async def stop(self):
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
