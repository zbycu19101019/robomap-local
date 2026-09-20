import asyncio
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Literal
from pydantic import BaseModel, Field, field_validator, model_validator
from .automation import in_quiet_window


class Rule(BaseModel):
    id: str = Field(min_length=1,max_length=60)
    name: str = Field(min_length=1,max_length=80)
    enabled: bool = False
    trigger: Literal['time','battery_below','cleaning_minutes'] = 'time'
    time: str = '09:00'
    threshold: int = Field(default=20,ge=1,le=240)
    days: list[int] = Field(default_factory=lambda:list(range(7)),min_length=1,max_length=7)
    min_battery: int = Field(default=30,ge=0,le=100)
    respect_quiet: bool = True
    action: Literal['start','pause','dock','quiet','standard','strong'] = 'start'

    @field_validator('time')
    @classmethod
    def valid_time(cls,v):
        return datetime.strptime(v,'%H:%M').strftime('%H:%M')

    @field_validator('days')
    @classmethod
    def valid_days(cls,v):
        if any(d not in range(7) for d in v):raise ValueError('Dni muszą być w zakresie 0–6')
        return sorted(set(v))

    @model_validator(mode='after')
    def sensible(self):
        if self.trigger=='battery_below' and self.threshold>100:raise ValueError('Bateria: najwyżej 100%')
        if self.trigger=='battery_below' and self.action=='start':raise ValueError('Niska bateria nie może uruchamiać sprzątania')
        return self


class RulesDocument(BaseModel):
    revision: int = 0
    rules: list[Rule] = Field(default_factory=list,max_length=40)

    @model_validator(mode='after')
    def unique(self):
        if len({r.id for r in self.rules})!=len(self.rules):raise ValueError('Powtórzone identyfikatory reguł')
        return self


class RulesEngine:
    def __init__(self,robot,automation,history,root):
        self.robot,self.automation,self.history=robot,automation,history
        self.path=Path(root)/'rules.json'
        self.fired_path=Path(root)/'rules-fired.json'
        self.task=None
        self.clean_since=None
        self.last_observed=None

    def load(self):
        return RulesDocument.model_validate_json(self.path.read_text(encoding='utf-8')) if self.path.exists() else RulesDocument()

    @staticmethod
    def write(path,text):
        tmp=path.with_suffix('.tmp');tmp.write_text(text,encoding='utf-8');tmp.replace(path)

    def save(self,doc):
        current=self.load()
        if current.revision!=doc.revision:raise ValueError('Reguły zmieniono w innym panelu. Odśwież listę.')
        doc=doc.model_copy(update={'revision':doc.revision+1})
        self.write(self.path,doc.model_dump_json(indent=2))
        return doc

    async def tick(self,now=None,clock=None):
        now=now or datetime.now();clock=time.monotonic() if clock is None else clock
        doc=self.load()
        if not any(r.enabled for r in doc.rules):
            self.clean_since=None;self.last_observed=None;return
        status=await self.robot.status()
        if status.state=='cleaning':
            if self.clean_since is None or self.last_observed is None or clock-self.last_observed>30:self.clean_since=clock
        else:self.clean_since=None
        self.last_observed=clock
        fired=json.loads(self.fired_path.read_text()) if self.fired_path.exists() else {}
        day=now.date().isoformat()
        fired={k:v for k,v in fired.items() if v==day}
        settings=self.automation.load()
        for rule in doc.rules:
            if not rule.enabled or now.weekday() not in rule.days or fired.get(rule.id)==day:continue
            matches=(rule.trigger=='time' and now.strftime('%H:%M')==rule.time
                or rule.trigger=='battery_below' and status.state=='cleaning' and status.battery is not None and status.battery<rule.threshold
                or rule.trigger=='cleaning_minutes' and self.clean_since is not None and clock-self.clean_since>=rule.threshold*60)
            if not matches:continue
            reason=None
            if status.state in ('offline','error','awaiting-token','unconfigured'):reason='Robot niedostępny'
            elif getattr(self.robot,'halted',False):reason='Aktywny STOP awaryjny'
            elif rule.action=='start' and (status.state not in ('idle','paused','charging','charged','docked') or status.battery is None or status.battery<max(rule.min_battery,settings.min_battery)):reason='Stan lub bateria blokuje start'
            elif rule.respect_quiet and rule.action in ('start','standard','strong') and in_quiet_window(settings,now):reason='Cisza nocna'
            # Mark before execution: never replay the same rule after restart.
            fired[rule.id]=day;self.write(self.fired_path,json.dumps(fired))
            if reason:
                self.history.log('rules','skipped',source=rule.name,detail=reason);continue
            if rule.action in ('quiet','standard','strong'):
                result=await self.robot.set_suction({'quiet':0,'standard':1,'strong':2}[rule.action])
            else:result=await self.robot.action(rule.action)
            self.history.log('rules',rule.action,source=rule.name,detail=result.detail or str(result.ok))
            break # Avoid several conflicting actions in one tick.

    async def run(self):
        while True:
            try:await self.tick()
            except Exception as exc:self.history.log('error','rules',source='local',detail=str(exc))
            await asyncio.sleep(5)

    async def start(self):self.task=asyncio.create_task(self.run())
    async def stop(self):
        if self.task:
            self.task.cancel()
            try:await self.task
            except asyncio.CancelledError:pass
