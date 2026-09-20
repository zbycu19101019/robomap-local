import importlib
from fastapi.testclient import TestClient
from app.models import RobotStatus, RobotActionResult


class Offline:
    async def status(self):
        return RobotStatus(provider='mock', state='awaiting-token')
    async def manual(self,*args):
        return RobotActionResult(ok=False,action='test',detail='Brak tokenu')
    action=manual
    set_suction=manual


def test_api_routes_origin_and_unconfigured_cat(tmp_path, monkeypatch):
    monkeypatch.setenv('DATA_DIR', str(tmp_path))
    main=importlib.import_module('app.main')
    monkeypatch.setattr(main.robot, 'base', Offline())
    # No TestClient lifespan: no discovery, account traffic or hardware.
    client=TestClient(main.app)
    for path in ['/api/health','/api/neon','/api/schedule','/api/map','/api/macros','/api/history','/api/automations','/api/connection','/api/localization']:
        assert client.get(path).status_code==200, path
    assert client.post('/api/cat/start').status_code==409
    assert client.put('/api/connection',json={'token':'invalid'}).status_code==422
    assert client.post('/api/control/unlock', headers={'Origin':'https://foreign.example'}).status_code==403
    assert client.post('/api/control/unlock', headers={'Origin':'http://testserver'}).status_code==200
    assert client.put('/api/schedule',json={'entries':[{'id':'a','days':[9],'time':'09:00'}]}).status_code==422
    assert client.get('/api/health').json()['version']=='10.7.0'
