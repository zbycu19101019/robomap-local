import asyncio
import importlib
from fastapi.testclient import TestClient
from app.storage import MapStore
from app.models import RobotStatus
from app.neon import NeonRobot


def test_map_versions_survive_and_restore_as_new_revision(tmp_path,monkeypatch):
    monkeypatch.setenv('DATA_DIR',str(tmp_path))
    store=MapStore();doc=store.load();doc.name='Nowa';store.save(doc)
    assert [v['revision'] for v in store.versions()]==[1,0]
    old=store.historical(0);old.revision=store.load().revision
    assert store.save(old).revision==2
    assert store.historical(1).name=='Nowa'


def test_websocket_origin_and_cached_status(tmp_path,monkeypatch):
    monkeypatch.setenv('DATA_DIR',str(tmp_path))
    main=importlib.import_module('app.main')
    monkeypatch.setattr(main.robot,'cached',RobotStatus(provider='mock',state='paused',battery=80))
    client=TestClient(main.app)
    with client.websocket_connect('/ws/live',headers={'origin':'http://testserver'}) as ws:
        message=ws.receive_json()
        assert message['robot']['battery']==80
        assert 'localization' in message
    import pytest
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect('/ws/live',headers={'origin':'https://foreign.example'}): pass


def test_offline_poll_backoff_does_not_block_pause(tmp_path):
    from app.models import RobotActionResult
    class Offline:
        reads=0
        actions=[]
        async def status(self):
            self.reads+=1
            return RobotStatus(provider='mock',state='offline')
        async def action(self,name):
            self.actions.append(name)
            return RobotActionResult(ok=True,action=name)
    async def run():
        base=Offline();robot=NeonRobot(base,root=tmp_path)
        await robot.status();await robot.status()
        assert base.reads==1
        await robot.action('pause')
        assert base.actions==['pause']
    asyncio.run(run())
