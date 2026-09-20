from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
import os
import asyncio
from ipaddress import ip_address

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .automation import AutomationEngine, AutomationSettings, AutomationStore
from .config import ConfigStore, ConnectionUpdate, ConnectionView
from .discovery import AutoDiscoveryEngine
from .cloud_token import fetch_token_once
from .cloud_autotoken import CloudAutoTokenEngine
from .secure_credentials import XiaomiCredentialStore
from .history import HistoryStore
from .macros import MacroDefinition, MacroManager, MacroRecordingState
from .models import MapDocument, RoomScanPayload, RobotActionResult, RobotStatus
from .robot import build_provider
from .storage import MapStore
from .neon import NeonRobot
from .schedule import Scheduler, ScheduleDocument
from .rules import RulesEngine, RulesDocument


store = MapStore()
config_store = ConfigStore()
history = HistoryStore()
automation_store = AutomationStore()
robot = NeonRobot(build_provider(config_store), map_store=store)
macro_manager = MacroManager(robot, history)
automation_engine = AutomationEngine(robot, history, automation_store)
auto_discovery = AutoDiscoveryEngine(config_store)
xiaomi_credentials = XiaomiCredentialStore()
cloud_autotoken = CloudAutoTokenEngine(config_store, xiaomi_credentials)
scheduler = Scheduler(robot, automation_store, history, config_store.path.parent)


rules_engine = RulesEngine(robot, automation_store, history, config_store.path.parent)

@asynccontextmanager
async def lifespan(_: FastAPI):
    await robot.start()
    await scheduler.start()
    await rules_engine.start()
    await automation_engine.start()
    await auto_discovery.start()
    await cloud_autotoken.start()
    try:
        yield
    finally:
        robot.halted = True
        await rules_engine.stop()
        await scheduler.stop()
        await macro_manager.stop_playback()
        await cloud_autotoken.stop()
        await auto_discovery.stop()
        await automation_engine.stop()
        await robot.close()


app = FastAPI(title="RoboMap Local", version="10.7.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[],
    allow_methods=["*"],
    allow_headers=["*"],
)




@app.middleware('http')
async def same_origin(request: Request, call_next):
    from urllib.parse import urlsplit
    from fastapi.responses import JSONResponse
    origin = request.headers.get('origin')
    if origin and urlsplit(origin).netloc != request.headers.get('host'):
        return JSONResponse({'detail': 'Niedozwolone źródło żądania.'}, status_code=403)
    return await call_next(request)

class CloudTokenBootstrapRequest(BaseModel):
    username: str = Field(min_length=1, max_length=160)
    password: str = Field(min_length=1, max_length=256)
    region: str = Field(default="all", max_length=8)
    remember: bool = False


def _request_is_loopback(request: Request) -> bool:
    host = request.client.host if request.client else ""
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return host.lower() in {"localhost"}


@app.post("/api/connection/autotoken/xiaomi")
async def cloud_token_bootstrap(payload: CloudTokenBootstrapRequest, request: Request) -> dict:
    # Xiaomi credentials must never traverse the ordinary LAN HTTP interface.
    # This setup endpoint is intentionally usable only from Launcher/localhost.
    if not _request_is_loopback(request):
        raise HTTPException(status_code=403, detail="Jednorazowy AutoToken z konta Xiaomi działa tylko na tym komputerze (127.0.0.1 / Launcher).")
    try:
        result = await asyncio.to_thread(
            fetch_token_once, config_store, payload.username, payload.password, payload.region
        )
        if payload.remember:
            await asyncio.to_thread(xiaomi_credentials.save, payload.username, payload.password, payload.region)
        else:
            await asyncio.to_thread(xiaomi_credentials.clear)
        cloud_autotoken.state = {
            "state": "ready",
            "detail": result.detail,
            "last_attempt": 0,
            "region": result.region,
        }
    except Exception as exc:
        history.log("security", "autotoken_bootstrap_failed", source="localhost", detail=str(exc)[:240])
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    history.log("settings", "autotoken_bootstrap_ok", source="localhost", detail=f"region={result.region}; ip={result.ip}; remember={payload.remember}")
    return {
        "ok": result.ok,
        "region": result.region,
        "ip": result.ip,
        "model": result.model,
        "name": result.name,
        "detail": result.detail,
        "token_set": config_store.view().token_set,
        "credential_saved": xiaomi_credentials.status().get("saved", False),
    }


@app.get("/api/connection/autotoken/status")
async def cloud_autotoken_status(request: Request) -> dict:
    if not _request_is_loopback(request):
        # No sensitive fields are returned, but keep the automatic-token control local.
        return {"state": "remote-view", "token_ready": config_store.view().token_set, "credential_saved": False, "detail": "AutoToken konta Xiaomi jest zarządzany tylko na tym komputerze."}
    return cloud_autotoken.public_state()


@app.post("/api/connection/autotoken/run")
async def cloud_autotoken_run(request: Request) -> dict:
    if not _request_is_loopback(request):
        raise HTTPException(status_code=403, detail="AutoToken konta Xiaomi działa tylko na tym komputerze.")
    result = await cloud_autotoken.try_now(force=True)
    if result.get("state") == "error":
        raise HTTPException(status_code=502, detail=result.get("detail") or "AutoToken nie powiódł się")
    return result


@app.delete("/api/connection/autotoken/credentials")
async def cloud_autotoken_forget(request: Request) -> dict:
    if not _request_is_loopback(request):
        raise HTTPException(status_code=403, detail="Zarządzanie autoryzacją Xiaomi działa tylko na tym komputerze.")
    await asyncio.to_thread(xiaomi_credentials.clear)
    cloud_autotoken.state = {"state": "idle", "detail": "Usunięto zapisaną autoryzację Xiaomi z tego komputera.", "last_attempt": 0}
    history.log("security", "autotoken_credentials_cleared", source="localhost")
    return cloud_autotoken.public_state()

class MacroStartRequest(BaseModel):
    name: str = Field(default="Nowe makro", max_length=80)


class PoseCalibrationRequest(BaseModel):
    x: float = Field(ge=-100, le=100)
    y: float = Field(ge=-100, le=100)
    heading: float = Field(default=0.0, ge=-20, le=20)


class LocalizationConfigRequest(BaseModel):
    linear_speed_cm_s: float = Field(default=10.0, ge=3, le=50)
    turn_rate_deg_s: float = Field(default=90.0, ge=20, le=360)


@app.get("/api/health")
async def health() -> dict:
    return {"ok": True, "service": "robomap-local", "mode": "local-only", "version": "10.7.0", "autopair": True, "autotoken": "auto+dpapi", "localization": "dock+rc-odometry"}




def _lan_ip() -> str:
    try:
        import ifaddr
        for adapter in ifaddr.get_adapters():
            for item in adapter.ips:
                if not isinstance(item.ip, str):
                    continue
                try:
                    addr = ip_address(item.ip)
                except ValueError:
                    continue
                if addr.version == 4 and addr.is_private and not addr.is_loopback:
                    return item.ip
    except Exception:
        pass
    return "127.0.0.1"


@app.get("/api/network")
async def network_info() -> dict:
    port = int(os.getenv("ROBOMAP_PORT", "8787"))
    lan = _lan_ip()
    return {
        "local_url": f"http://127.0.0.1:{port}",
        "lan_url": f"http://{lan}:{port}",
        "lan_ip": lan,
        "port": port,
    }

@app.get("/api/connection", response_model=ConnectionView)
async def get_connection() -> ConnectionView:
    return config_store.view()


@app.put("/api/connection", response_model=ConnectionView)
async def put_connection(update: ConnectionUpdate) -> ConnectionView:
    try:
        config_store.update(update)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    history.log("settings", "connection_update", source="web", detail=f"IP={update.robot_ip}")
    return config_store.view()


@app.post("/api/connection/test", response_model=RobotActionResult)
async def test_connection() -> RobotActionResult:
    result = await robot.test_connection()
    history.log("command", "connection_test", source="web", detail=result.detail)
    if not result.ok:
        raise HTTPException(status_code=502, detail=result.detail or "Robot nie odpowiada")
    return result


async def _run_autodiscover() -> dict:
    result = await auto_discovery.scan(auto_save=True)
    selected = result.get("selected") or {}
    # If the paired E5 hides its token locally, immediately escalate to the
    # user's pre-authorized Xiaomi AutoToken source (if one was saved).
    if result.get("exact_model") and not result.get("token_ready"):
        token_state = await cloud_autotoken.try_now(force=True)
        result["cloud_autotoken"] = token_state
        result["token_ready"] = bool(token_state.get("token_ready"))
        if result["token_ready"]:
            result["token_saved"] = True
            result["token_source"] = "xiaomi-account-auto"
            result["detail"] = "AutoPair znalazł E5, a AutoToken automatycznie pobrał i zapisał jego token z autoryzowanego konta Xiaomi."
        elif token_state.get("state") == "authorization-required":
            result["detail"] = "E5 wykryty. Sparowany robot ukrywa token w LAN; potrzebne jest jednorazowe połączenie własnego konta Xiaomi. Potem RoboMap zasysa token automatycznie."
    history.log(
        "network",
        "autodiscover",
        source="local",
        detail=f"{result.get('detail', '')} IP={selected.get('ip', '')}",
    )
    return result


@app.post("/api/connection/autodiscover")
async def connection_autodiscover_post() -> dict:
    # Zachowane dla zgodności ze starszym frontendem.
    return await _run_autodiscover()


def _autodiscover_state_payload() -> dict:
    return auto_discovery.last_result or {
        "found": False,
        "auto_added": False,
        "selected": None,
        "token_saved": False,
        "token_ready": config_store.view().token_set,
        "exact_model": False,
        "reason": "not-run-yet",
        "candidates": [],
        "research_patch": True,
        "lan_only": True,
        "detail": "Badawcza łatka AutoPair jeszcze nie wykonała skanu LAN.",
    }


@app.get("/api/connection/autodiscover")
async def connection_autodiscover_state(run: bool = False) -> dict:
    if run:
        return await _run_autodiscover()
    return _autodiscover_state_payload()


@app.get("/api/autopair")
async def autopair_state_alias() -> dict:
    return _autodiscover_state_payload()


@app.get("/api/autopair/run")
async def autopair_run_alias() -> dict:
    return await _run_autodiscover()


@app.post("/api/autopair/run")
async def autopair_run_alias_post() -> dict:
    return await _run_autodiscover()


@app.get("/api/discovery")
async def discovery_state_alias() -> dict:
    return _autodiscover_state_payload()


@app.get("/api/discovery/run")
async def discovery_run_alias() -> dict:
    return await _run_autodiscover()


@app.get("/api/map", response_model=MapDocument)
async def get_map() -> MapDocument:
    return store.load()


@app.put("/api/map", response_model=MapDocument)
async def put_map(doc: MapDocument) -> MapDocument:
    try:
        saved = store.save(doc)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    history.log("map", "save", source="web", detail=f"revision={saved.revision}")
    return saved


@app.post("/api/maps/roomplan", response_model=MapDocument)
async def import_roomplan(scan: RoomScanPayload) -> MapDocument:
    current = store.load()
    imported = MapDocument(
        revision=current.revision,
        name=scan.name or current.name,
        source=scan.source,
        walls=scan.walls,
        doors=scan.doors,
        windows=scan.windows,
        openings=scan.openings,
        furniture=scan.furniture,
        no_go_zones=current.no_go_zones,
        virtual_walls=current.virtual_walls,
        dock=current.dock,
    )
    try:
        saved = store.save(imported)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    history.log("map", "roomplan_import", source="iphone", detail=scan.device_name or scan.source)
    return saved


@app.get('/api/rules')
async def get_rules():
    return rules_engine.load()

@app.put('/api/rules')
async def put_rules(doc: RulesDocument):
    try:
        return rules_engine.save(doc)
    except ValueError as exc:
        raise HTTPException(409, str(exc))

@app.get("/api/localization")
async def localization_state() -> dict:
    # Purely local/in-memory. No MIoT packet is sent, so the UI may poll this quickly.
    return robot.localization_view()


@app.get('/api/mapping')
async def mapping_state():
    return robot.survey.view()


@app.get('/api/maps/history')
async def map_history():
    return store.versions()


@app.get('/api/maps/history/{revision}')
async def historical_map(revision: int):
    try:
        return store.historical(revision)
    except KeyError:
        raise HTTPException(404, 'Nie znaleziono wersji mapy.')


@app.websocket('/ws/live')
async def live_socket(ws: WebSocket):
    from urllib.parse import urlsplit
    origin = ws.headers.get('origin')
    if origin and urlsplit(origin).netloc != ws.headers.get('host'):
        await ws.close(code=1008)
        return
    await ws.accept()
    try:
        while True:
            # Existing monitor owns hardware polling; panels only receive cached data.
            status = robot.cached
            await asyncio.wait_for(ws.send_json({'type':'live',
                'robot':status.model_dump(mode='json') if status else None,
                'localization':robot.localization_view()}), timeout=3)
            await asyncio.sleep(.4)
    except (WebSocketDisconnect, RuntimeError, OSError, asyncio.TimeoutError):
        pass


@app.post('/api/mapping/{action}')
async def mapping_action(action: str):
    if action == 'prepare':
        await macro_manager.stop_playback()
        result = await robot.action('pause')
        if not result.ok:
            raise HTTPException(502, result.detail or 'Robot nie potwierdził pauzy.')
        for attempt in range(6):
            robot.cached = None
            status = await robot.status()
            if status.state in ('idle', 'paused', 'charging', 'charged', 'docked'):
                return {'detail': 'Robot zatrzymany. Wskaż jego rzeczywistą pozycję i kierunek na mapie.'}
            await asyncio.sleep(.5)
        raise HTTPException(409, 'Robot nie potwierdził zatrzymania. Poczekaj i ponów przygotowanie mapowania.')
    elif action == 'start':
        status = await robot.status()
        if status.state in ('cleaning', 'returning') and not robot.rc_clean_active:
            raise HTTPException(409, 'Robot porusza się samodzielnie. Najpierw wybierz „Przygotuj mapowanie”.')
        loc = robot.localization_view()
        if not loc['calibrated'] or loc['confidence'] < .35 or loc['source'] == 'autonomous-last-known':
            raise HTTPException(409, 'Najpierw skalibruj aktualną pozycję robota na mapie.')
        robot.survey.active = True
    elif action == 'pause':
        robot.survey.active = False
    elif action == 'contact':
        if not robot.survey.active:
            raise HTTPException(409, 'Włącz zapis pomiarów.')
        if not robot.survey.contact(robot.localization_view(), 'user-contact'):
            raise HTTPException(409, 'Punkt już istnieje lub pozycja wymaga kalibracji.')
    else:
        raise HTTPException(404, 'Nieznana czynność mapowania.')
    return robot.survey.view()


@app.post("/api/localization/dock")
async def localization_dock() -> dict:
    if robot.direction != "stop":
        raise HTTPException(status_code=409, detail="Najpierw zatrzymaj ruch robota.")
    try:
        result = robot.calibrate_to_dock()
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    history.log("map", "localization_dock", source="web", detail=f"x={result['pose']['x']:.2f}; y={result['pose']['y']:.2f}")
    return result


@app.post("/api/localization/pose")
async def localization_pose(payload: PoseCalibrationRequest) -> dict:
    status = await robot.status()
    if status.state not in ('idle', 'paused', 'charging', 'charged', 'docked'):
        raise HTTPException(409, 'Kalibracja wymaga zatrzymanego robota. Wybierz „Przygotuj mapowanie”, a potem wskaż pozycję.')
    if robot.direction != "stop":
        raise HTTPException(status_code=409, detail="Najpierw zatrzymaj ruch robota.")
    result = robot.set_map_pose(payload.x, payload.y, payload.heading)
    history.log("map", "localization_manual", source="web", detail=f"x={payload.x:.2f}; y={payload.y:.2f}; heading={payload.heading:.2f}")
    return result


@app.post("/api/localization/trail/clear")
async def localization_clear_trail() -> dict:
    result = robot.clear_localization_trail()
    history.log("map", "localization_trail_clear", source="web")
    return result


@app.put("/api/localization/config")
async def localization_config(payload: LocalizationConfigRequest) -> dict:
    try:
        result = robot.set_localization_config(payload.linear_speed_cm_s / 100.0, payload.turn_rate_deg_s)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    history.log("map", "localization_config", source="web", detail=f"v={payload.linear_speed_cm_s:.1f}cm/s; turn={payload.turn_rate_deg_s:.1f}deg/s")
    return result


@app.get("/api/robot/status", response_model=RobotStatus)
async def robot_status() -> RobotStatus:
    return await robot.status()


@app.post("/api/robot/emergency-stop", response_model=RobotActionResult)
async def emergency_stop() -> RobotActionResult:
    robot.halted = True
    await macro_manager.stop_playback()
    result = await robot.emergency()
    ok = result.ok
    detail = result.detail
    history.log("command", "emergency_stop", source="web", detail=detail)
    if not ok:
        raise HTTPException(status_code=502, detail=detail)
    return RobotActionResult(ok=True, action="emergency-stop", detail="Wysłano STOP awaryjny.")


@app.post("/api/robot/{action}", response_model=RobotActionResult)
async def robot_action(action: str) -> RobotActionResult:
    if action not in {"start", "pause", "resume", "stop", "dock"}:
        raise HTTPException(status_code=404, detail="Nieznana akcja")
    result = await robot.action(action)
    history.log("command", action, source="web", detail=result.detail)
    if not result.ok:
        raise HTTPException(status_code=502, detail=result.detail or "Błąd robota")
    return result




@app.post("/api/robot/manual/keepalive/{direction}", response_model=RobotActionResult)
async def robot_manual_keepalive(direction: str) -> RobotActionResult:
    # Fast local heartbeat: do NOT write to the robot and do NOT spam history/SQLite.
    result = await robot.keepalive(direction)
    if not result.ok:
        raise HTTPException(status_code=409, detail=result.detail or "Heartbeat odrzucony")
    return result

@app.post("/api/robot/manual/{direction}", response_model=RobotActionResult)
async def robot_manual(direction: str) -> RobotActionResult:
    if direction not in {"left", "right", "forward", "back", "stop"}:
        raise HTTPException(status_code=404, detail="Nieznany kierunek")
    result = await robot.manual(direction)
    if result.ok:
        macro_manager.capture_manual(direction)
    history.log("command", f"manual:{direction}", source="web", detail=result.detail)
    if not result.ok:
        raise HTTPException(status_code=502, detail=result.detail or "Błąd sterowania ręcznego")
    return result




@app.post("/api/robot/rc/begin", response_model=RobotActionResult)
async def robot_rc_begin() -> RobotActionResult:
    result = await robot.begin_rc_clean()
    history.log("command", "rc:begin", source="gamepad", detail=result.detail)
    if not result.ok:
        raise HTTPException(status_code=502, detail=result.detail or "Nie udało się uruchomić RC Clean")
    return result


@app.post("/api/robot/rc/end", response_model=RobotActionResult)
async def robot_rc_end() -> RobotActionResult:
    result = await robot.end_rc_clean()
    history.log("command", "rc:end", source="gamepad", detail=result.detail)
    if not result.ok:
        raise HTTPException(status_code=502, detail=result.detail or "Nie udało się zakończyć RC Clean")
    return result


@app.post("/api/robot/silent/{enabled}", response_model=RobotActionResult)
async def robot_silent(enabled: bool) -> RobotActionResult:
    result = await robot.set_no_disturb(enabled)
    history.log("settings", f"robot_silent:{enabled}", source="web", detail=result.detail)
    if not result.ok:
        raise HTTPException(status_code=502, detail=result.detail or "Nie udało się zmienić sygnałów robota")
    return result

@app.post("/api/robot/suction/{level}", response_model=RobotActionResult)
async def robot_suction(level: int) -> RobotActionResult:
    result = await robot.set_suction(level)
    history.log("command", f"suction:{level}", source="web", detail=result.detail)
    if not result.ok:
        raise HTTPException(status_code=502, detail=result.detail or "Błąd zmiany mocy")
    return result


@app.get("/api/macros", response_model=list[MacroDefinition])
async def list_macros() -> list[MacroDefinition]:
    return macro_manager.list()


@app.get("/api/macros/state")
async def macro_state() -> dict:
    return {
        "recording": macro_manager.recording_state().model_dump(mode="json"),
        "playback": macro_manager.playback_state(),
    }


@app.post("/api/macros/record/start", response_model=MacroRecordingState)
async def macro_record_start(req: MacroStartRequest) -> MacroRecordingState:
    return macro_manager.start_recording(req.name)


@app.post("/api/macros/record/stop")
async def macro_record_stop() -> dict:
    item = await macro_manager.stop_recording()
    return {"saved": item is not None, "macro": item.model_dump(mode="json") if item else None}


@app.post("/api/macros/record/cancel")
async def macro_record_cancel() -> dict:
    await macro_manager.cancel_recording()
    return {"ok": True}


@app.post("/api/macros/{macro_id}/play", response_model=RobotActionResult)
async def macro_play(macro_id: str) -> RobotActionResult:
    if robot.halted or (robot.cat and not robot.cat.done()):
        raise HTTPException(status_code=409, detail='Zatrzymaj Cat Mode lub odblokuj STOP.')
    result = await macro_manager.play(macro_id)
    if not result.ok:
        raise HTTPException(status_code=404, detail=result.detail or "Nie udało się uruchomić makra")
    return result


@app.post("/api/macros/playback/stop")
async def macro_playback_stop() -> dict:
    await macro_manager.stop_playback()
    history.log("macro", "play_stop", source="web")
    return {"ok": True}


@app.delete("/api/macros/{macro_id}")
async def macro_delete(macro_id: str) -> dict:
    if not macro_manager.store.delete(macro_id):
        raise HTTPException(status_code=404, detail="Nie znaleziono makra")
    history.log("macro", "delete", source="web", detail=macro_id)
    return {"ok": True}


@app.get("/api/automations", response_model=AutomationSettings)
async def get_automations() -> AutomationSettings:
    return automation_store.load()


@app.put("/api/automations", response_model=AutomationSettings)
async def put_automations(settings: AutomationSettings) -> AutomationSettings:
    saved = automation_store.save(settings)
    history.log("settings", "automations_update", source="web")
    return saved


@app.get("/api/system/presence")
async def get_presence() -> dict:
    return automation_engine.presence()


@app.get("/api/history")
async def get_history(limit: int = 100) -> dict:
    return {"events": history.recent(limit)}


@app.get("/api/stats")
async def get_stats(days: int = 30) -> dict:
    return history.stats(days)


@app.get('/api/schedule')
async def schedule_get():
    return scheduler.load()

@app.put('/api/schedule')
async def schedule_put(doc: ScheduleDocument):
    try:
        return scheduler.save(doc)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

@app.post('/api/profiles/{name}')
async def profile_set(name: str):
    presets = {'normal': (1, False, False), 'night': (0, True, False), 'away': (2, False, True)}
    if name not in presets:
        raise HTTPException(status_code=404, detail='Nieznany profil')
    power, quiet, lock = presets[name]
    result = await robot.set_suction(power)
    if not result.ok:
        raise HTTPException(status_code=502, detail=result.detail)
    settings = automation_store.load().model_copy(update={'quiet_enabled': quiet, 'lock_enabled': lock})
    automation_store.save(settings)
    history.log('profile', name, source='web')
    return {'ok': True, 'detail': 'Profil zastosowany.'}

@app.get('/api/neon')
async def neon_state():
    return robot.view()

@app.post('/api/cat/start')
async def cat_start():
    if macro_manager.recording_state().recording:
        raise HTTPException(status_code=409, detail='Zakończ nagrywanie makra.')
    await macro_manager.stop_playback()
    try:
        return await robot.start_cat()
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

@app.post('/api/cat/stop')
async def cat_stop():
    await robot.stop_cat()
    result = await robot.manual('stop')
    if not result.ok:
        raise HTTPException(status_code=502, detail=result.detail)
    return {'ok': True}

@app.post('/api/control/unlock')
async def unlock_control():
    robot.halted = False
    return {'ok': True}

@app.post('/api/radar/reset')
async def reset_radar():
    if robot.direction != 'stop' or (robot.cat and not robot.cat.done()):
        raise HTTPException(status_code=409, detail='Najpierw zatrzymaj ruch.')
    # Radar reset no longer destroys the calibrated home-map position.
    robot.dots = []
    return {'ok': True, 'detail': 'Zdarzenia radaru wyczyszczone. Pozycja na mapie została zachowana.'}

static_dir = Path(__file__).resolve().parents[1] / "static"
app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
