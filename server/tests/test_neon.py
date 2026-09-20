import asyncio
import json
import os
import time
from datetime import datetime
from types import SimpleNamespace
import pytest
from app.neon import Experience, NeonRobot
from app.models import RobotActionResult, RobotStatus
from app.config import ConfigStore, LocalRobotConfig
from app.schedule import Scheduler, ScheduleDocument, ScheduleEntry
from app.automation import AutomationSettings


class FakeRobot:
    def __init__(self):
        self.commands = []
        self.fail = False
        self.state = 'idle'

    async def status(self):
        return RobotStatus(provider='mock', state=self.state, battery=85)

    async def manual(self, direction):
        await asyncio.sleep(.005)
        self.commands.append(direction)
        return RobotActionResult(ok=not self.fail, action=direction, detail='test')

    async def action(self, name):
        return await self.manual(name)

    async def set_suction(self, level):
        return await self.manual(f'power:{level}')

    async def set_no_disturb(self, enabled):
        return await self.manual(f'dnd:{bool(enabled)}')


def test_experience_no_offline_credit_and_no_double_poll(tmp_path):
    xp=Experience(tmp_path)
    for t in range(61):
        xp.observe('cleaning',t,datetime(2026,9,8,23,30))
    assert xp.view()['exp']==10
    assert xp.view()['achievements']==['Nocny Marek']
    xp.observe('cleaning',60)
    xp.observe('cleaning',600)
    assert xp.view()['exp']==10
    xp.observe('offline',601)
    xp.observe('cleaning',602)
    assert xp.view()['exp']==10
    assert Experience(tmp_path).view()['exp']==10


def test_marathon_and_level(tmp_path):
    xp=Experience(tmp_path)
    for t in range(0,6004,3):
        xp.observe('cleaning',t)
    assert 'Maratończyk' in xp.view()['achievements']
    assert xp.view()['level']==2


@pytest.mark.skipif(os.name != 'nt', reason='Windows DPAPI')
def test_dpapi_roundtrip_and_migration(tmp_path,monkeypatch):
    monkeypatch.setenv('DATA_DIR',str(tmp_path))
    token='12ab'*8
    (tmp_path/'robot.json').write_text(json.dumps({'token':token,'robot_ip':'192.168.1.5'}))
    store=ConfigStore()
    assert store.load().token==token
    text=store.path.read_text()
    assert token not in text
    assert 'token_dpapi' in text
    assert store.load().token==token
    assert 'token' not in store.view().model_dump()


@pytest.mark.parametrize('token',['f'*32,'0'*32,'abc','z'*32])
def test_invalid_tokens(tmp_path,monkeypatch,token):
    monkeypatch.setenv('DATA_DIR',str(tmp_path))
    store=ConfigStore()
    with pytest.raises(ValueError):
        store.save(LocalRobotConfig(token=token))


def test_cat_stops_and_releases_writer(tmp_path):
    async def run():
        fake=FakeRobot(); robot=NeonRobot(fake,tmp_path)
        await robot.start_cat()
        await asyncio.sleep(.03)
        assert not (await robot.manual('forward')).ok
        result=await robot.emergency()
        assert result.ok
        assert fake.commands[-1]=='stop'
        count=len(fake.commands)
        await asyncio.sleep(.1)
        assert len(fake.commands)==count
        assert not (await robot.action('start')).ok
        assert robot.halted
    asyncio.run(run())


def test_cat_rejects_failed_prepare(tmp_path):
    async def run():
        fake=FakeRobot();fake.fail=True;robot=NeonRobot(fake,tmp_path)
        with pytest.raises(ValueError):
            await robot.start_cat()
        assert not robot.cat
    asyncio.run(run())


def test_manual_watchdog(tmp_path):
    async def run():
        fake=FakeRobot();robot=NeonRobot(fake,tmp_path)
        await robot.start()
        await robot.manual('forward')
        await asyncio.sleep(1.5)
        assert fake.commands[-1]=='stop'
        assert robot.pose['y'] < 0
        assert abs(robot.pose['y'])<=.13
        await robot.close()
    asyncio.run(run())


def test_cancellation_drains_command_before_stop(tmp_path):
    async def run():
        fake=FakeRobot();robot=NeonRobot(fake,tmp_path)
        task=asyncio.create_task(robot.manual('forward'))
        await asyncio.sleep(.001)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert fake.commands==['forward','stop']
    asyncio.run(run())


def test_schedule_no_duplicate_after_restart(tmp_path):
    async def run():
        fake=FakeRobot();logs=SimpleNamespace(log=lambda *a,**kw:None)
        settings=SimpleNamespace(load=lambda:AutomationSettings())
        scheduler=Scheduler(fake,settings,logs,tmp_path)
        scheduler.save(ScheduleDocument(entries=[ScheduleEntry(id='one',days=[1],time='09:00')]))
        await scheduler.tick(datetime(2026,9,8,9,0))
        await Scheduler(fake,settings,logs,tmp_path).tick(datetime(2026,9,8,9,0))
        assert fake.commands==['start']
    asyncio.run(run())


def test_schedule_quiet_hours(tmp_path):
    async def run():
        fake=FakeRobot();logs=SimpleNamespace(log=lambda *a,**kw:None)
        settings=SimpleNamespace(load=lambda:AutomationSettings(quiet_enabled=True))
        scheduler=Scheduler(fake,settings,logs,tmp_path)
        scheduler.save(ScheduleDocument(entries=[ScheduleEntry(id='one',days=[1],time='23:00')]))
        await scheduler.tick(datetime(2026,9,8,23,0))
        assert not fake.commands
    asyncio.run(run())


def test_miot_rejected_response():
    from app.robot import LocalMiotRobotProvider
    with pytest.raises(RuntimeError):
        LocalMiotRobotProvider._value([{'code':-1}])


def test_manual_keepalive_extends_lease_without_robot_spam(tmp_path):
    async def run():
        fake = FakeRobot(); robot = NeonRobot(fake, tmp_path)
        await robot.start()
        assert (await robot.manual('forward')).ok
        # Heartbeats extend only local lease; they must not resend 'forward' to MIoT.
        for _ in range(4):
            await asyncio.sleep(.18)
            assert (await robot.keepalive('forward')).ok
        assert fake.commands.count('forward') == 1
        assert fake.commands[-1] == 'forward'
        await asyncio.sleep(.82)
        assert fake.commands[-1] == 'stop'
        await robot.close()
    asyncio.run(run())


def test_keepalive_rejects_stale_direction(tmp_path):
    async def run():
        fake = FakeRobot(); robot = NeonRobot(fake, tmp_path)
        await robot.manual('left')
        result = await robot.keepalive('right')
        assert not result.ok
    asyncio.run(run())


def test_local_manual_has_hard_timeout(tmp_path, monkeypatch):
    from app.robot import LocalMiotRobotProvider
    monkeypatch.setenv('DATA_DIR', str(tmp_path))
    store = ConfigStore()
    provider = LocalMiotRobotProvider(store)
    def slow(_direction):
        time.sleep(2.0)
        return RobotActionResult(ok=True, action='late')
    monkeypatch.setattr(provider, '_manual_sync', slow)
    async def run():
        started = time.monotonic()
        result = await provider.manual('forward')
        elapsed = time.monotonic() - started
        assert not result.ok
        assert 'Timeout' in result.detail
        assert elapsed < 1.7
    asyncio.run(run())


def test_rc_clean_starts_brushes_once_and_pauses_on_end(tmp_path):
    async def run():
        fake = FakeRobot(); robot = NeonRobot(fake, tmp_path)
        result = await robot.begin_rc_clean()
        assert result.ok
        assert fake.commands[:2] == ['dnd:True', 'start']
        await robot.manual('forward')
        await robot.manual('stop')
        await robot.manual('back')
        assert fake.commands.count('start') == 1
        assert 'back' in fake.commands
        ended = await robot.end_rc_clean()
        assert ended.ok
        assert fake.commands[-2:] == ['stop', 'pause']
    asyncio.run(run())



def test_home_map_pose_calibrates_to_dock_and_moves(tmp_path):
    from app.models import Dock, MapDocument
    class MapStub:
        def load(self):
            return MapDocument(name='Dom', dock=Dock(x=2.0, y=3.0, rotation=0.0))
    async def run():
        fake = FakeRobot(); fake.state = 'charging'
        robot = NeonRobot(fake, tmp_path, map_store=MapStub())
        status = await robot.status()
        assert status.supports_pose
        assert status.pose.x == pytest.approx(2.0)
        assert status.pose.y == pytest.approx(3.0)
        assert status.pose_source == 'dock'
        assert status.pose_confidence == pytest.approx(1.0)
        fake.state = 'idle'
        await robot.manual('forward')
        await asyncio.sleep(.22)
        await robot.keepalive('forward')
        await asyncio.sleep(.12)
        await robot.manual('stop')
        loc = robot.localization_view()
        assert loc['pose']['y'] < 3.0
        assert loc['source'] == 'rc-odometry'
        assert loc['confidence'] < 1.0
        assert len(loc['trail']) >= 2
    asyncio.run(run())


def test_manual_map_calibration_persists(tmp_path):
    robot = NeonRobot(FakeRobot(), tmp_path)
    loc = robot.set_map_pose(1.25, 2.5, 0.75)
    assert loc['calibrated']
    assert loc['source'] == 'manual-calibration'
    restored = NeonRobot(FakeRobot(), tmp_path).localization_view()
    assert restored['pose']['x'] == pytest.approx(1.25)
    assert restored['pose']['y'] == pytest.approx(2.5)
    assert restored['pose']['heading'] == pytest.approx(0.75, abs=1e-5)


def test_autonomous_cleaning_is_marked_last_known(tmp_path):
    async def run():
        fake = FakeRobot(); robot = NeonRobot(fake, tmp_path)
        robot.set_map_pose(1.0, 1.0, 0.0)
        robot._last_autonomous_degrade = time.monotonic() - 10
        fake.state = 'cleaning'
        status = await robot.status()
        assert status.pose_source == 'autonomous-last-known'
        assert status.pose_confidence < .95
        assert 'ostatnią znaną' in status.pose_note.lower()
    asyncio.run(run())
