from __future__ import annotations

import asyncio
import socket
import threading
import time
from dataclasses import dataclass, asdict
from ipaddress import ip_address, ip_interface, ip_network
from typing import Any

from .config import ConfigStore

TARGET_MODEL = "xiaomi.vacuum.c108"
MIIO_PORT = 54321
HELLO = bytes.fromhex("21310020ffffffffffffffffffffffffffffffffffffffffffffffffffffffff")


@dataclass
class Candidate:
    ip: str
    device_id: str = ""
    model: str = ""
    token: str = ""
    token_source: str = ""
    source: str = "handshake"
    verified: bool = False

    @property
    def token_available(self) -> bool:
        return _valid_token(self.token)

    def public(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("token", None)
        data["token_available"] = self.token_available
        data["exact_model"] = self.model == TARGET_MODEL
        return data


def _valid_token(token: str | None) -> bool:
    if not token or len(token) != 32:
        return False
    t = token.lower()
    try:
        int(t, 16)
    except ValueError:
        return False
    return t not in {"0" * 32, "f" * 32}


def _private_ipv4_interfaces() -> list[tuple[str, int]]:
    result: list[tuple[str, int]] = []
    try:
        import ifaddr
        for adapter in ifaddr.get_adapters():
            for item in adapter.ips:
                if not isinstance(item.ip, str) or item.network_prefix is None:
                    continue
                try:
                    addr = ip_address(item.ip)
                    prefix = int(item.network_prefix)
                except (ValueError, TypeError):
                    continue
                if addr.version != 4 or addr.is_loopback or not addr.is_private:
                    continue
                result.append((item.ip, max(0, min(32, prefix))))
    except Exception:
        pass
    return result


def _broadcast_targets() -> list[str]:
    targets = {"255.255.255.255"}
    for host, prefix in _private_ipv4_interfaces():
        try:
            iface = ip_interface(f"{host}/{prefix}")
            targets.add(str(iface.network.broadcast_address))
        except ValueError:
            continue
    return sorted(targets)


def _local_unicast_targets(max_hosts_per_adapter: int = 254) -> list[str]:
    targets: set[str] = set()
    own = {host for host, _ in _private_ipv4_interfaces()}
    for host, prefix in _private_ipv4_interfaces():
        try:
            # Research fallback stays inside the current local /24.
            scan_prefix = max(prefix, 24)
            net = ip_network(f"{host}/{scan_prefix}", strict=False)
            count = 0
            for addr in net.hosts():
                value = str(addr)
                if value in own:
                    continue
                targets.add(value)
                count += 1
                if count >= max_hosts_per_adapter:
                    break
        except ValueError:
            continue
    return sorted(targets)


def _parse_hello_response(data: bytes, host: str, source: str) -> Candidate | None:
    if len(data) < 32 or data[:2] != b"\x21\x31":
        return None
    declared = int.from_bytes(data[2:4], "big", signed=False)
    if declared < 32 or declared > len(data):
        return None
    try:
        addr = ip_address(host)
        if addr.version != 4 or not addr.is_private:
            return None
    except ValueError:
        return None
    token = data[16:32].hex()
    return Candidate(
        ip=host,
        device_id=data[8:12].hex(),
        token=token,
        token_source=(f"miio-{source}" if _valid_token(token) else ""),
        source=source,
    )


def _probe_hello(targets: list[str], timeout: float, source: str, repeat: int = 2) -> dict[str, Candidate]:
    found: dict[str, Candidate] = {}
    if not targets:
        return found
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.settimeout(0.10)
        for target in targets:
            for _ in range(repeat):
                try:
                    sock.sendto(HELLO, (target, MIIO_PORT))
                except OSError:
                    break
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                data, addr = sock.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                break
            item = _parse_hello_response(data, addr[0], source)
            if item is not None:
                old = found.get(item.ip)
                if old is None or (item.token_available and not old.token_available):
                    found[item.ip] = item
    finally:
        sock.close()
    return found


def _broadcast_handshake(timeout: float = 3.0) -> tuple[dict[str, Candidate], bool]:
    found = _probe_hello(_broadcast_targets(), min(timeout, 1.6), "broadcast", repeat=3)
    used_unicast = False
    if not found:
        used_unicast = True
        found = _probe_hello(_local_unicast_targets(), min(timeout, 2.0), "unicast-lan", repeat=1)
    return found, used_unicast


def _direct_token_capture(ip: str, rounds: int = 5) -> Candidate | None:
    """Try to capture a token that the device voluntarily exposes in a local miIO hello.

    Some provisioned Xiaomi devices answer with only ffffffffff... instead of the real
    token. In that case there is nothing to derive from the LAN handshake, so we keep
    watching on later AutoPair cycles rather than guessing/bruteforcing the secret.
    """
    best: Candidate | None = None
    for index in range(max(1, rounds)):
        batch = _probe_hello([ip], timeout=0.45, source="direct-autotoken", repeat=2)
        item = batch.get(ip)
        if item is not None:
            best = item
            if item.token_available:
                return item
        if index + 1 < rounds:
            time.sleep(0.18)
    return best


def _decode_prop(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="ignore").strip()
    return str(value or "").strip()


def _discover_mdns(timeout: float = 2.0) -> dict[str, Candidate]:
    try:
        import zeroconf
    except Exception:
        return {}
    found: dict[str, Candidate] = {}
    lock = threading.Lock()

    class Listener(zeroconf.ServiceListener):
        def add_service(self, zc, type_: str, name: str) -> None:
            try:
                info = zc.get_service_info(type_, name, timeout=900)
                if info is None or not info.addresses:
                    return
                host = str(ip_address(info.addresses[0]))
                addr = ip_address(host)
                if addr.version != 4 or not addr.is_private:
                    return
                props = {_decode_prop(k).lower(): _decode_prop(v) for k, v in (info.properties or {}).items()}
                model = ""
                for key in ("model", "modelname", "miio_model", "miio.model"):
                    if props.get(key):
                        model = props[key]
                        break
                if not model and TARGET_MODEL in name:
                    model = TARGET_MODEL
                token = ""
                for key in ("token", "miio_token", "miio.token", "local_token"):
                    if _valid_token(props.get(key)):
                        token = props[key]
                        break
                with lock:
                    found[host] = Candidate(
                        ip=host,
                        model=model,
                        token=token,
                        token_source=("mdns-txt" if token else ""),
                        source="mdns",
                    )
            except Exception:
                return

        def update_service(self, zc, type_: str, name: str) -> None:
            self.add_service(zc, type_, name)

        def remove_service(self, zc, type_: str, name: str) -> None:
            return

    zc = zeroconf.Zeroconf()
    browser = zeroconf.ServiceBrowser(zc, "_miio._udp.local.", Listener())
    try:
        time.sleep(timeout)
    finally:
        try:
            browser.cancel()
        except Exception:
            pass
        zc.close()
    return found


def _merge_candidates(handshake: dict[str, Candidate], mdns: dict[str, Candidate]) -> list[Candidate]:
    merged: dict[str, Candidate] = dict(handshake)
    for host, item in mdns.items():
        if host in merged:
            current = merged[host]
            current.model = item.model or current.model
            if item.token_available and not current.token_available:
                current.token = item.token
                current.token_source = item.token_source
            current.source = f"{current.source}+mdns"
        else:
            merged[host] = item
    return sorted(merged.values(), key=lambda item: tuple(int(x) for x in item.ip.split(".")))


def _verify_candidate(candidate: Candidate, token: str) -> tuple[bool, str]:
    if not _valid_token(token):
        return False, candidate.model
    try:
        from miio import Device
        dev = Device(ip=candidate.ip, token=token, timeout=2, lazy_discover=True)
        info = dev.info()
        model = str(getattr(info, "model", "") or candidate.model or "")
        return model == TARGET_MODEL, model
    except Exception:
        return candidate.model == TARGET_MODEL, candidate.model


def _capture_selected_token(selected: Candidate, stored_token: str = "") -> tuple[str, str, bool]:
    if _valid_token(stored_token):
        return stored_token, "stored", False
    if selected.token_available:
        return selected.token, selected.token_source or "handshake", False

    # AUTO-TOKEN research patch: direct local hello burst to the already selected E5.
    captured = _direct_token_capture(selected.ip, rounds=5)
    if captured is not None:
        if captured.device_id and not selected.device_id:
            selected.device_id = captured.device_id
        if captured.token_available:
            selected.token = captured.token
            selected.token_source = captured.token_source or "direct-autotoken"
            return captured.token, selected.token_source, True
    return "", "", True


def discover_once(config_store: ConfigStore, timeout: float = 3.0, auto_save: bool = True) -> dict[str, Any]:
    started = time.monotonic()
    cfg = config_store.load()
    handshake, used_unicast = _broadcast_handshake(timeout=timeout)
    mdns = _discover_mdns(timeout=min(timeout, 2.0))
    candidates = _merge_candidates(handshake, mdns)

    selected: Candidate | None = None
    selected_token = ""
    token_source = ""
    reason = ""
    token_capture_attempted = False

    if cfg.device_id:
        selected = next((c for c in candidates if c.device_id == cfg.device_id), None)
        if selected is not None:
            selected.model = selected.model or cfg.model
            reason = "stored-device-id"

    exact = [c for c in candidates if c.model == TARGET_MODEL]
    if selected is None and len(exact) == 1:
        selected = exact[0]
        reason = "exact-e5-mdns"

    if selected is None and _valid_token(cfg.token):
        for candidate in candidates:
            ok, model = _verify_candidate(candidate, cfg.token)
            candidate.model = model or candidate.model
            candidate.verified = ok
            if ok:
                selected = candidate
                selected_token = cfg.token
                token_source = "stored"
                reason = "stored-token-verified"
                break

    if selected is None:
        for candidate in candidates:
            if not candidate.token_available:
                continue
            ok, model = _verify_candidate(candidate, candidate.token)
            candidate.model = model or candidate.model
            candidate.verified = ok
            if ok:
                selected = candidate
                selected_token = candidate.token
                token_source = candidate.token_source or "handshake"
                reason = "handshake-token-verified"
                break

    if selected is None and len(candidates) == 1 and not cfg.device_id and not cfg.robot_ip:
        selected = candidates[0]
        selected.model = selected.model or TARGET_MODEL
        reason = "research-single-lan-candidate"

    # If we found the robot but still do not have a token, automatically perform
    # a direct local capture burst. This does not guess or brute-force anything;
    # it only stores a token if the E5 actually exposes it in the LAN response.
    if selected is not None and not selected_token:
        selected_token, token_source, token_capture_attempted = _capture_selected_token(selected, cfg.token)
        if selected_token and reason not in {"stored-token-verified", "handshake-token-verified"}:
            reason = f"{reason}+autotoken" if reason else "autotoken"

    token_saved = False
    auto_added = False
    if selected is not None and auto_save:
        current = config_store.load()
        token_to_save = selected_token or (selected.token if selected.token_available else "")
        update: dict[str, Any] = {
            "robot_ip": selected.ip,
            "device_id": selected.device_id or current.device_id,
            "model": selected.model or current.model,
            "last_discovery_reason": reason,
        }
        if token_to_save:
            update["token"] = token_to_save
            token_saved = token_to_save != current.token
        config_store.save(current.model_copy(update=update))
        auto_added = True

    current_after = config_store.load() if auto_save else cfg
    selected_public = selected.public() if selected else None
    token_ready = _valid_token(current_after.token) or bool(selected_token)
    exact_selected = bool(selected and selected.model == TARGET_MODEL)

    if selected is None and not candidates:
        detail = "AutoPair LAN: nie znaleziono urządzenia miIO w tej sieci. Sprawdź, czy E5 i komputer są w tej samej sieci Wi‑Fi/LAN."
    elif selected is None and candidates:
        detail = "Wykryto urządzenia miIO, ale żadne nie zostało jednoznacznie rozpoznane jako E5."
    elif exact_selected and token_ready:
        src = token_source or (selected.token_source if selected else "") or "lokalnie"
        detail = f"AutoPair + AutoToken: E5 dodany automatycznie ({selected.ip}); token zapisany automatycznie [{src}]."
    elif exact_selected:
        detail = (
            f"AutoPair działa: E5 wykryty ({selected.ip}). Ten już sparowany robot nie ujawnił tokenu w handshake LAN. "
            "Dalsze czekanie nie wyliczy sekretu — użyj jednorazowego AutoToken z własnego konta Xiaomi w panelu poniżej albo wpisz token ręcznie."
        )
    else:
        detail = f"AutoPair LAN: odnaleziono zapamiętany robot pod adresem {selected.ip}."

    return {
        "found": bool(candidates),
        "auto_added": auto_added,
        "selected": selected_public,
        "token_saved": token_saved,
        "token_ready": token_ready,
        "token_source": token_source or (selected.token_source if selected else ""),
        "token_autocapture": True,
        "token_capture_attempted": token_capture_attempted,
        "token_hidden": bool(exact_selected and not token_ready and token_capture_attempted),
        "exact_model": exact_selected,
        "reason": reason,
        "research_patch": True,
        "lan_only": True,
        "used_unicast_fallback": used_unicast,
        "elapsed_ms": int((time.monotonic() - started) * 1000),
        "candidates": [c.public() for c in candidates],
        "detail": detail,
    }


class AutoDiscoveryEngine:
    def __init__(self, config_store: ConfigStore, interval_seconds: int = 20) -> None:
        self.config_store = config_store
        self.interval_seconds = interval_seconds
        self._task: asyncio.Task | None = None
        self._scan_lock = asyncio.Lock()
        self.last_result: dict[str, Any] | None = None

    async def scan(self, *, auto_save: bool = True) -> dict[str, Any]:
        async with self._scan_lock:
            self.last_result = await asyncio.to_thread(discover_once, self.config_store, 3.0, auto_save)
            return self.last_result

    async def _run(self) -> None:
        await asyncio.sleep(0.8)
        first_pass = True
        while True:
            wait_seconds = self.interval_seconds
            try:
                cfg = self.config_store.load()
                # If IP is known but token is still missing, keep AutoToken watching
                # more frequently until a valid token becomes available.
                should_scan = cfg.auto_discovery or (first_pass and not cfg.robot_ip) or (cfg.robot_ip and not _valid_token(cfg.token))
                if should_scan:
                    result = await self.scan(auto_save=True)
                    if not result.get("token_ready"):
                        wait_seconds = min(self.interval_seconds, 10)
                first_pass = False
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.last_result = {
                    "found": False,
                    "auto_added": False,
                    "selected": None,
                    "token_saved": False,
                    "token_ready": bool(self.config_store.load().token),
                    "token_source": "",
                    "token_autocapture": True,
                    "token_capture_attempted": False,
                    "exact_model": False,
                    "reason": "scan-error",
                    "research_patch": True,
                    "lan_only": True,
                    "candidates": [],
                    "detail": f"AutoPair/AutoToken: błąd skanowania: {type(exc).__name__}: {exc}",
                }
            await asyncio.sleep(wait_seconds)

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="robomap-research-autopair-autotoken")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
