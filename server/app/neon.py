"""Local telemetry, conservative dead reckoning, timed RC and RPG."""
from __future__ import annotations

import asyncio
import json
import math
import os
import random
import time
from datetime import datetime
from pathlib import Path

from .localization import LocalizationStore, normalize_heading
from .models import RobotActionResult, RobotPose, RobotStatus
from .robot import RobotProvider


class Experience:
    ranks = ['Początkujący Miotłowy', 'Zwiadowca Okruszków', 'Łowca Kłaczków',
             'Strażnik Podłogi', 'Pogromca Pyłu', 'Rycerz Porządku', 'Mistrz Szczotki',
             'Cyberczyściciel', 'Legenda Salonu', 'Terminator Kurzu']

    def __init__(self, root):
        self.path = root / 'experience.json'
        self.data = {'seconds': 0.0, 'achievements': []}
        if self.path.exists():
            self.data = json.loads(self.path.read_text(encoding='utf-8'))
        self.last = None
        self.cleaning = False
        self.session = 0.0

    def observe(self, state, now=None, wall=None):
        now = time.monotonic() if now is None else now
        wall = wall or datetime.now()
        dt = now - self.last if self.last is not None else 0
        if state == 'cleaning' and self.cleaning and 0 <= dt <= 15:
            self.data['seconds'] += dt
            self.session += dt
            if wall.hour >= 23:
                self.unlock('Nocny Marek')
            if self.session > 3600:
                self.unlock('Maratończyk')
        else:
            self.session = 0.0
        self.last, self.cleaning = now, state == 'cleaning'
        self.save()

    def unlock(self, name):
        if name not in self.data['achievements']:
            self.data['achievements'].append(name)

    def save(self):
        tmp = self.path.with_suffix('.tmp')
        tmp.write_text(json.dumps(self.data, ensure_ascii=False), encoding='utf-8')
        tmp.replace(self.path)

    def view(self):
        exp = int(self.data['seconds'] / 6)
        level = min(10, exp // 1000 + 1)
        return {**self.data, 'exp': exp, 'level': level, 'rank': self.ranks[level - 1],
                'progress': (exp % 1000) / 1000 if level < 10 else 1,
                'next_exp': level * 1000 if level < 10 else None}


class NeonRobot(RobotProvider):
    """Single-writer robot controller plus RoboMap-local pose estimation.

    E5 does not expose a usable absolute XY position through the public MIoT surface used
    here. RoboMap therefore anchors its pose to the dock/manual calibration and integrates
    only movements it actually commands. The API always marks the result as estimated.
    """

    def __init__(self, base, root=None, map_store=None):
        self.base = base
        self.map_store = map_store
        root = Path(root or os.getenv('DATA_DIR', Path(__file__).resolve().parents[2] / 'data'))
        root.mkdir(parents=True, exist_ok=True)
        self.xp = Experience(root)
        self.localization = LocalizationStore(root)
        from .mapping import MappingSurvey
        self.survey = MappingSurvey(root)
        loc = self.localization.view()
        self.pose = dict(loc['pose'])
        self.pose_source = loc['source']
        self.pose_confidence = float(loc['confidence'])
        self.pose_calibrated = bool(loc['calibrated'])
        self.pose_updated_at = loc['updated_at']
        self.lock = asyncio.Lock()
        self.status_lock = asyncio.Lock()
        self.cached = None
        self.cached_at = 0.0
        self.status_failures = 0
        self.cat = None
        self.cat_until = 0.0
        self.cat_error = ''
        self.cat_stop = asyncio.Event()
        self.halted = False
        self.direction = 'stop'
        self.lease = 0.0
        self.lease_seconds = 0.72
        self.pose_at = time.monotonic()
        self.dots = []
        self.last_fault = None
        self.monitor = None
        self.watchdog = None
        self.silent_task = None
        self.rc_clean_active = False
        self.rc_clean_started_by_robomap = False
        self.rc_dnd_before = None
        self._last_autonomous_degrade = time.monotonic()
        self._last_pose_persist = 0.0

    def _motion_config(self) -> tuple[float, float]:
        state = self.localization.view()
        return float(state['linear_speed_mps']), math.radians(float(state['turn_rate_dps']))

    def _persist_pose(self, *, source=None, confidence=None, calibrated=None, append_trail=True, force=False):
        now = time.monotonic()
        persist = bool(force or now - self._last_pose_persist >= 1.0)
        state = self.localization.update_pose(
            x=self.pose['x'], y=self.pose['y'], heading=self.pose['heading'],
            source=source or self.pose_source,
            confidence=self.pose_confidence if confidence is None else confidence,
            calibrated=self.pose_calibrated if calibrated is None else calibrated,
            append_trail=append_trail,
            persist=persist,
        )
        if persist:
            self._last_pose_persist = now
        self.pose = dict(state['pose'])
        self.pose_source = state['source']
        self.pose_confidence = float(state['confidence'])
        self.pose_calibrated = bool(state['calibrated'])
        self.pose_updated_at = state['updated_at']

    def integrate(self):
        now = time.monotonic()
        dt = max(0.0, min(now, self.lease) - self.pose_at)
        self.pose_at = now
        if dt <= 0 or self.direction == 'stop':
            return
        linear_speed, turn_rate = self._motion_config()
        changed = False
        confidence_loss = 0.0
        if self.direction in ('left', 'right'):
            delta = dt * turn_rate * (1 if self.direction == 'right' else -1)
            self.pose['heading'] = normalize_heading(self.pose['heading'] + delta)
            confidence_loss = abs(delta) / math.pi * 0.025
            changed = True
        elif self.direction in ('forward', 'back'):
            distance = dt * linear_speed * (1 if self.direction == 'forward' else -1)
            self.pose['x'] += math.sin(self.pose['heading']) * distance
            self.pose['y'] -= math.cos(self.pose['heading']) * distance
            confidence_loss = abs(distance) * 0.035
            changed = True
        if changed:
            if not self.pose_calibrated:
                # Movement without a map anchor is still useful to the experimental radar,
                # but must not be presented as a location on the home map.
                self.pose_source = 'uncalibrated-odometry'
                self.pose_confidence = 0.0
            else:
                self.pose_source = 'rc-odometry'
                self.pose_confidence = max(0.10, self.pose_confidence - confidence_loss)
            self._persist_pose(source=self.pose_source, confidence=self.pose_confidence, append_trail=self.pose_calibrated)

    def _dock(self):
        if not self.map_store:
            return None
        try:
            return self.map_store.load().dock
        except Exception:
            return None

    def calibrate_to_dock(self):
        dock = self._dock()
        if dock is None:
            raise ValueError('Najpierw ustaw stację dokującą na mapie.')
        self.integrate()
        self.pose = {'x': float(dock.x), 'y': float(dock.y), 'heading': normalize_heading(float(dock.rotation or 0.0))}
        self.pose_at = time.monotonic()
        self.pose_source = 'dock'
        self.pose_confidence = 1.0
        self.pose_calibrated = True
        self._persist_pose(source='dock', confidence=1.0, calibrated=True, append_trail=False, force=True)
        self.localization.clear_trail()
        return self.localization_view(note='Pozycja zsynchronizowana ze stacją dokującą.')

    def set_map_pose(self, x: float, y: float, heading: float):
        self.integrate()
        self.pose = {'x': float(x), 'y': float(y), 'heading': normalize_heading(float(heading))}
        self.pose_at = time.monotonic()
        self.pose_source = 'manual-calibration'
        self.pose_confidence = 0.95
        self.pose_calibrated = True
        self._persist_pose(source='manual-calibration', confidence=.95, calibrated=True, append_trail=False, force=True)
        self.localization.clear_trail()
        return self.localization_view(note='Ręczna kalibracja pozycji zapisana.')

    def clear_localization_trail(self):
        self.integrate()
        self.localization.clear_trail()
        return self.localization_view(note='Ślad pozycji wyczyszczony.')

    def set_localization_config(self, linear_speed_mps: float, turn_rate_dps: float):
        self.localization.set_motion_config(linear_speed_mps=linear_speed_mps, turn_rate_dps=turn_rate_dps)
        return self.localization_view(note='Kalibracja odometrii zapisana.')

    def _degrade_for_autonomous_motion(self, state: str):
        """Autonomous E5 motion is not observable as XY; mark last-known pose accordingly."""
        if not self.pose_calibrated or self.direction != 'stop' or self.rc_clean_active:
            return
        now = time.monotonic()
        if state not in ('cleaning', 'returning'):
            self._last_autonomous_degrade = now
            return
        elapsed = max(0.0, now - self._last_autonomous_degrade)
        if elapsed < 2.0:
            return
        self._last_autonomous_degrade = now
        loss = min(.12, elapsed * .018)
        self.pose_confidence = max(.05, self.pose_confidence - loss)
        self.pose_source = 'autonomous-last-known'
        self._persist_pose(source=self.pose_source, confidence=self.pose_confidence, append_trail=False)

    def _sync_localization_from_status(self, status: RobotStatus):
        self.integrate()
        if status.state in ('charging', 'charged', 'docked') and self._dock() is not None and self.direction == 'stop':
            dock = self._dock()
            dx = self.pose['x'] - float(dock.x)
            dy = self.pose['y'] - float(dock.y)
            if (not self.pose_calibrated or math.hypot(dx, dy) > .015 or self.pose_source != 'dock'):
                self.pose = {'x': float(dock.x), 'y': float(dock.y), 'heading': normalize_heading(float(dock.rotation or 0.0))}
                self.pose_source = 'dock'
                self.pose_confidence = 1.0
                self.pose_calibrated = True
                self.pose_at = time.monotonic()
                self._persist_pose(source='dock', confidence=1.0, calibrated=True, append_trail=True, force=True)
        else:
            self._degrade_for_autonomous_motion(status.state)

    def _pose_note(self, state: str | None = None) -> str:
        if not self.pose_calibrated:
            return 'Pozycja nie jest skalibrowana. Ustaw bazę i użyj „Robot w bazie” albo wskaż pozycję na mapie.'
        if self.pose_source == 'dock':
            return 'Pozycja potwierdzona przez stan ładowania i położenie bazy na mapie.'
        if self.pose_source == 'manual-calibration':
            return 'Pozycja ustawiona ręcznie na mapie.'
        if self.pose_source == 'rc-odometry':
            return 'Pozycja szacowana z komend jazdy RoboMap; może narastać błąd odometrii.'
        if self.pose_source == 'autonomous-last-known' or state in ('cleaning', 'returning'):
            return 'E5 nie przekazuje XY podczas autonomicznego ruchu. Znacznik pokazuje ostatnią znaną pozycję, nie dokładną lokalizację.'
        return 'Pozycja lokalna RoboMap.'

    def localization_view(self, note: str | None = None) -> dict:
        self.integrate()
        state = self.localization.view()
        state['pose'] = dict(self.pose)
        state['source'] = self.pose_source
        state['confidence'] = round(float(self.pose_confidence), 3)
        state['calibrated'] = bool(self.pose_calibrated)
        state['moving'] = self.direction != 'stop'
        state['direction'] = self.direction
        state['approximate'] = self.pose_source not in ('dock',)
        state['note'] = note or self._pose_note(getattr(self.cached, 'state', None))
        state['trail'] = state.get('trail', [])[-700:]
        return state

    async def status(self):
        async with self.status_lock:
            interval = min(20, 2 ** min(self.status_failures + 1, 5))
            if self.cached is None or time.monotonic() - self.cached_at > interval:
                self.cached = await self.base.status()
                self.status_failures = min(5, self.status_failures + 1) if self.cached.state == 'offline' else 0
                self.cached_at = time.monotonic()
            self._sync_localization_from_status(self.cached)
            if not self.pose_calibrated:
                return self.cached.model_copy(update={
                    'pose': None,
                    'supports_pose': False,
                    'pose_source': self.pose_source,
                    'pose_confidence': self.pose_confidence,
                    'pose_updated_at': self.pose_updated_at,
                    'pose_note': self._pose_note(self.cached.state),
                })
            return self.cached.model_copy(update={
                'pose': RobotPose(**self.pose),
                'supports_pose': True,
                'pose_source': self.pose_source,
                'pose_confidence': round(self.pose_confidence, 3),
                'pose_updated_at': self.pose_updated_at,
                'pose_note': self._pose_note(self.cached.state),
            })

    async def action(self, name):
        if name in ('stop', 'pause', 'dock'):
            await self.stop_cat()
            if self.rc_clean_active or self.rc_dnd_before is not None:
                await self._restore_rc_silent_mode()
            self.rc_clean_active = False
            self.rc_clean_started_by_robomap = False
        async with self.lock:
            if self.cat and not self.cat.done():
                return RobotActionResult(ok=False, action=name, detail='Najpierw zatrzymaj Cat Mode.')
            if self.halted and name not in ('stop', 'pause'):
                return RobotActionResult(ok=False, action=name, detail='STOP aktywny. Odblokuj sterowanie w panelu.')
            self.integrate()
            if self.direction != 'stop':
                await self.base.manual('stop')
                self.direction = 'stop'
            result = await self._write(self.base.action(name))
            if result.ok and name in ('start', 'resume', 'dock') and self.pose_calibrated:
                self.pose_source = 'autonomous-last-known'
                self.pose_confidence = min(self.pose_confidence, .55 if name == 'dock' else .45)
                self._persist_pose(source=self.pose_source, confidence=self.pose_confidence, append_trail=False)
            return result

    async def _write(self, coroutine):
        pending = asyncio.create_task(coroutine)
        try:
            return await asyncio.shield(pending)
        except asyncio.CancelledError:
            await pending
            await self.base.manual('stop')
            raise

    async def manual(self, direction):
        async with self.lock:
            if direction != 'stop' and (self.halted or (self.cat and not self.cat.done())):
                return RobotActionResult(ok=False, action=direction, detail='Sterowanie zablokowane lub aktywny Cat Mode.')
            return await self._manual(direction)

    async def _manual(self, direction):
        self.integrate()
        pending = asyncio.create_task(self.base.manual(direction))
        try:
            result = await asyncio.shield(pending)
        except asyncio.CancelledError:
            await pending
            await self.base.manual('stop')
            self.direction = 'stop'
            raise
        if result.ok:
            self.direction = direction
            self.pose_at = time.monotonic()
            self.lease = self.pose_at + self.lease_seconds
            if direction != 'stop' and self.pose_calibrated:
                self.pose_source = 'rc-odometry'
        return result

    async def keepalive(self, direction: str) -> RobotActionResult:
        if direction not in ('left', 'right', 'forward', 'back'):
            return RobotActionResult(ok=False, action='manual:keepalive', detail='Nieznany kierunek heartbeat.')
        if self.halted or self.direction != direction:
            return RobotActionResult(ok=False, action='manual:keepalive', detail='Heartbeat nie pasuje do aktywnego kierunku.')
        self.lease = time.monotonic() + self.lease_seconds
        return RobotActionResult(ok=True, action='manual:keepalive', detail='RC lease odnowiony lokalnie.')

    async def set_suction(self, level):
        async with self.lock:
            if self.cat and not self.cat.done():
                return RobotActionResult(ok=False, action='suction', detail='Cat Mode utrzymuje moc Cicha.')
            return await self._write(self.base.set_suction(level))

    async def set_no_disturb(self, enabled=True):
        async with self.lock:
            return await self._write(self.base.set_no_disturb(bool(enabled)))

    async def _enter_rc_silent_mode(self):
        if self.rc_dnd_before is None:
            if hasattr(self.base, 'get_no_disturb_config'):
                try:
                    self.rc_dnd_before = await self.base.get_no_disturb_config()
                except Exception:
                    self.rc_dnd_before = {"enabled": None, "period": None}
            else:
                self.rc_dnd_before = {"enabled": None, "period": None}
        if hasattr(self.base, 'set_no_disturb_window'):
            result = await self.base.set_no_disturb_window(True, '00:00:00-23:59:59')
        else:
            result = await self.base.set_no_disturb(True)
        if result.ok:
            await asyncio.sleep(.20)
        return result

    async def _restore_rc_silent_mode(self):
        previous = self.rc_dnd_before
        self.rc_dnd_before = None
        if not previous or previous.get('enabled') is None or not previous.get('period'):
            return None
        try:
            if hasattr(self.base, 'set_no_disturb_window'):
                return await self.base.set_no_disturb_window(bool(previous['enabled']), str(previous['period']))
            return None
        except Exception:
            return None

    async def begin_rc_clean(self):
        async with self.lock:
            if self.halted or (self.cat and not self.cat.done()):
                return RobotActionResult(ok=False, action='rc-begin', detail='Sterowanie zablokowane lub aktywny Cat Mode.')
            quiet = await self._enter_rc_silent_mode()
            status = await self.base.status()
            if status.state in ('offline', 'unconfigured', 'awaiting-token', 'error'):
                return RobotActionResult(ok=False, action='rc-begin', detail=status.detail or f'Stan robota: {status.state}')
            if self.rc_clean_active:
                return RobotActionResult(ok=True, action='rc-begin', detail='RC Clean już aktywny.')
            if status.state == 'cleaning':
                self.rc_clean_active = True
                self.rc_clean_started_by_robomap = False
                return RobotActionResult(ok=True, action='rc-begin', detail='Szczotki już pracują; przejmuję sterowanie gałką.')
            started = await self.base.action('start')
            if not started.ok:
                return RobotActionResult(ok=False, action='rc-begin', detail=started.detail or 'E5 odrzucił uruchomienie szczotek.')
            self.rc_clean_active = True
            self.rc_clean_started_by_robomap = True
            detail = 'RC Clean: szczotki i ssanie uruchomione automatycznie.'
            if not quiet.ok:
                detail += ' Nie potwierdzono trybu cichego.'
            return RobotActionResult(ok=True, action='rc-begin', detail=detail)

    async def end_rc_clean(self):
        async with self.lock:
            manual = await self._manual('stop')
            pause = None
            if self.rc_clean_active and self.rc_clean_started_by_robomap:
                pause = await self.base.action('pause')
            self.rc_clean_active = False
            self.rc_clean_started_by_robomap = False
            restored = await self._restore_rc_silent_mode()
            ok = manual.ok and (pause is None or pause.ok)
            detail = 'RC zakończony; koła zatrzymane' + (' i szczotki w pauzie.' if pause is not None else '.')
            if restored is not None and not restored.ok:
                detail += ' Nie udało się przywrócić wcześniejszego DND.'
            return RobotActionResult(ok=ok, action='rc-end', detail=detail)

    async def test_connection(self):
        return await self.base.test_connection()

    async def start_cat(self):
        async with self.lock:
            if self.cat and not self.cat.done():
                return {'ok': True, 'active': True}
            status = await self.status()
            if self.halted or status.state not in ('idle', 'paused', 'charged', 'charging', 'docked'):
                raise ValueError('Robot musi być gotowy lub w pauzie. Sprawdź połączenie i STOP.')
            if status.battery is None or status.battery < 20:
                raise ValueError('Cat Mode wymaga odczytu baterii co najmniej 20%.')
            for result in (await self.base.action('pause'), await self.base.set_suction(0)):
                if not result.ok:
                    raise ValueError(result.detail or 'Robot odrzucił przygotowanie Cat Mode.')
            self.cat_stop = asyncio.Event()
            self.cat_error = ''
            self.cat_until = time.monotonic() + 60
            self.cat = asyncio.create_task(self._cat_loop())
            return {'ok': True, 'active': True}

    async def _cat_loop(self):
        try:
            while not self.cat_stop.is_set() and time.monotonic() < self.cat_until:
                async with self.lock:
                    if self.cat_stop.is_set() or self.halted:
                        break
                    result = await self._manual(random.choice(['left', 'right', 'forward']))
                    if not result.ok:
                        raise ValueError(result.detail)
                await self._cat_wait(random.uniform(.25, .65))
                async with self.lock:
                    result = await self._manual('stop')
                    if not result.ok:
                        raise ValueError(result.detail)
                await self._cat_wait(random.uniform(1, 2.5))
        except Exception as exc:
            self.cat_error = str(exc)
        finally:
            async with self.lock:
                result = await self._manual('stop')
                if not result.ok:
                    self.cat_error = result.detail or 'Nie potwierdzono STOP.'

    async def _cat_wait(self, seconds):
        try:
            await asyncio.wait_for(self.cat_stop.wait(), seconds)
        except asyncio.TimeoutError:
            pass

    async def stop_cat(self):
        self.cat_stop.set()
        if self.cat and not self.cat.done() and self.cat is not asyncio.current_task():
            await self.cat

    async def emergency(self):
        self.halted = True
        await self.stop_cat()
        async with self.lock:
            manual = await self._manual('stop')
            normal = await self.base.action('stop')
        await self._restore_rc_silent_mode()
        self.rc_clean_active = False
        self.rc_clean_started_by_robomap = False
        return RobotActionResult(ok=manual.ok or normal.ok, action='emergency-stop',
                                 detail='STOP wysłany; sterowanie zablokowane.' if manual.ok or normal.ok else 'Brak potwierdzenia STOP z robota.')

    def view(self):
        loc = self.localization_view()
        return {'rpg': self.xp.view(), 'cat': {'active': bool(self.cat and not self.cat.done()),
                'remaining': max(0, int(self.cat_until - time.monotonic())), 'error': self.cat_error},
                'halted': self.halted, 'radar': {'pose': loc['pose'], 'dots': self.dots,
                'approximate': True, 'bumper_supported': bool(self.cached and self.cached.bumper_supported)},
                'localization': loc}

    async def start(self):
        self.monitor = asyncio.create_task(self._monitor())
        self.watchdog = asyncio.create_task(self._watchdog())
        self.silent_task = None

    async def close(self):
        await self.stop_cat()
        if self.direction != 'stop':
            await self.manual('stop')
        await self._restore_rc_silent_mode()
        self._persist_pose(source=self.pose_source, confidence=self.pose_confidence, append_trail=False, force=True)
        for task in (self.monitor, self.watchdog, self.silent_task):
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self.xp.save()

    async def _ensure_silent_signals(self):
        while True:
            await asyncio.sleep(2.0)
            try:
                result = await self.set_no_disturb(True)
                if result.ok:
                    return
            except Exception:
                pass
            await asyncio.sleep(8.0)

    async def _watchdog(self):
        while True:
            await asyncio.sleep(.08)
            async with self.lock:
                if self.direction != 'stop' and time.monotonic() >= self.lease:
                    await self._manual('stop')

    async def _monitor(self):
        while True:
            try:
                status = await self.status()
                self.xp.observe(status.state)
                if status.state in ('error', 'offline', 'awaiting-token', 'unconfigured'):
                    self.cat_stop.set()
                fault = status.error_code if status.bumper_hit else None
                if fault is not None and fault != self.last_fault:
                    self.integrate()
                    self.dots.append({**self.pose, 'time': time.time(), 'code': fault})
                    self.dots = self.dots[-500:]
                    self.survey.contact(self.localization_view())
                self.last_fault = fault
            except Exception:
                self.xp.observe('offline')
                self.cat_stop.set()
            await asyncio.sleep(3)
