from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .config import ConfigStore
from .discovery import TARGET_MODEL, _valid_token

ALLOWED_REGIONS = ("de", "cn", "i2", "ru", "sg", "us")


@dataclass
class CloudTokenResult:
    ok: bool
    region: str = ""
    ip: str = ""
    model: str = ""
    name: str = ""
    detail: str = ""


def _text(value: Any) -> str:
    return str(value or "").strip()


def _select_target(devices: list[dict[str, Any]], known_ip: str) -> dict[str, Any] | None:
    matches = [d for d in devices if _text(d.get("model")) == TARGET_MODEL and _valid_token(_text(d.get("token")))]
    if not matches:
        return None
    if known_ip:
        by_ip = [d for d in matches if _text(d.get("localip")) == known_ip]
        if len(by_ip) == 1:
            return by_ip[0]
    if len(matches) == 1:
        return matches[0]
    return None


def fetch_token_once(config_store: ConfigStore, username: str, password: str, region: str = "de") -> CloudTokenResult:
    """One-time bootstrap of the local miIO token from the user's own Xiaomi account.

    Credentials live only in this function call and are never persisted by RoboMap.
    After the device token is saved, normal RoboMap operation remains LAN-only.
    """
    username = username.strip()
    password = password or ""
    region = (region or "de").strip().lower()
    if not username or not password:
        raise ValueError("Podaj login i hasło do własnego konta Xiaomi.")
    if region != "all" and region not in ALLOWED_REGIONS:
        raise ValueError("Nieobsługiwany region Xiaomi Home.")

    try:
        from micloud import MiCloud
    except Exception as exc:
        raise RuntimeError("Brak biblioteki micloud. Uruchom ponownie instalator RoboMap.") from exc

    cloud = MiCloud(username, password)
    try:
        if not cloud.login():
            raise RuntimeError("Logowanie Xiaomi nie powiodło się. Sprawdź dane logowania lub weryfikację konta/2FA.")

        cfg = config_store.load()
        regions = list(ALLOWED_REGIONS) if region == "all" else [region]
        all_model_matches: list[tuple[str, dict[str, Any]]] = []
        for current_region in regions:
            try:
                devices = cloud.get_devices(country=current_region) or []
            except Exception:
                continue
            for device in devices:
                if _text(device.get("model")) == TARGET_MODEL and _valid_token(_text(device.get("token"))):
                    all_model_matches.append((current_region, device))

        if not all_model_matches:
            raise RuntimeError(
                "Na tym koncie/regionie nie znaleziono Xiaomi Robot Vacuum E5 z dostępnym tokenem. "
                "Jeśli region w Xiaomi Home jest inny, wybierz „Wszystkie regiony”."
            )

        selected_region = ""
        selected: dict[str, Any] | None = None
        if cfg.robot_ip:
            by_ip = [(r, d) for r, d in all_model_matches if _text(d.get("localip")) == cfg.robot_ip]
            if len(by_ip) == 1:
                selected_region, selected = by_ip[0]
        if selected is None and len(all_model_matches) == 1:
            selected_region, selected = all_model_matches[0]
        if selected is None:
            names = [f"{_text(d.get('name')) or TARGET_MODEL} ({_text(d.get('localip')) or 'bez IP'}, {r})" for r, d in all_model_matches[:5]]
            raise RuntimeError("Znaleziono kilka E5. Nie wybieram automatycznie niewłaściwego robota: " + "; ".join(names))

        token = _text(selected.get("token")).lower()
        if not _valid_token(token):
            raise RuntimeError("Xiaomi zwróciło nieprawidłowy token urządzenia.")

        ip = _text(selected.get("localip")) or cfg.robot_ip
        name = _text(selected.get("name")) or "Xiaomi Robot Vacuum E5"
        current = config_store.load()
        config_store.save(current.model_copy(update={
            "robot_ip": ip,
            "token": token,
            "model": TARGET_MODEL,
            "last_discovery_reason": f"xiaomi-account-bootstrap:{selected_region}",
        }))
        return CloudTokenResult(
            ok=True,
            region=selected_region,
            ip=ip,
            model=TARGET_MODEL,
            name=name,
            detail=(
                f"Token E5 został pobrany jednorazowo z Twojego konta Xiaomi ({selected_region}) i zapisany lokalnie. "
                "Hasło nie zostało zapisane. Dalsze sterowanie RoboMap działa lokalnie po LAN/Wi‑Fi."
            ),
        )
    finally:
        # Best effort: release session/cookies kept by the third-party cloud helper.
        try:
            session = getattr(cloud, "session", None)
            if session is not None:
                session.close()
        except Exception:
            pass
        password = ""
