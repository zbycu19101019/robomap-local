from __future__ import annotations

import os
import socket
import sys
import threading
import time
import traceback
import webbrowser
from pathlib import Path


def local_ip() -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except Exception:
        try:
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return "127.0.0.1"
    finally:
        sock.close()


def port_is_free(port: int) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("0.0.0.0", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def find_free_port(start: int = 8787, end: int = 8797) -> int:
    preferred = os.getenv("ROBOMAP_PORT")
    if preferred:
        try:
            p = int(preferred)
            if 1 <= p <= 65535 and port_is_free(p):
                return p
        except ValueError:
            pass
    for port in range(start, end + 1):
        if port_is_free(port):
            return port
    raise RuntimeError(f"Porty {start}-{end} sa zajete.")


def wait_and_open(port: int) -> None:
    if os.getenv("ROBOMAP_NO_BROWSER") == "1":
        return
    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.4):
                webbrowser.open(f"http://127.0.0.1:{port}")
                return
        except OSError:
            time.sleep(0.25)


def log_dir() -> Path:
    root = Path(os.getenv("LOCALAPPDATA", Path.home())) / "RoboMapLocal" / "logs"
    root.mkdir(parents=True, exist_ok=True)
    return root


def write_error(exc: BaseException) -> None:
    try:
        path = log_dir() / "backend_error.log"
        path.write_text(
            "RoboMap Local - blad uruchomienia\n\n"
            + "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
            encoding="utf-8",
        )
        print(f"Szczegoly zapisano w: {path}", flush=True)
    except Exception:
        pass


def main() -> None:
    try:
        import uvicorn
    except Exception as exc:
        raise RuntimeError("Nie mozna zaladowac Uvicorn. Uruchom instalator RoboMap ponownie.") from exc

    app_root = Path(__file__).resolve().parent
    server_dir = app_root / "server"
    if not server_dir.exists():
        raise RuntimeError(f"Brak katalogu serwera: {server_dir}")

    data_dir = Path(os.getenv("DATA_DIR", Path(os.getenv("LOCALAPPDATA", Path.home())) / "RoboMapLocal" / "data"))
    data_dir.mkdir(parents=True, exist_ok=True)
    os.environ["DATA_DIR"] = str(data_dir)

    os.chdir(server_dir)
    sys.path.insert(0, str(server_dir))

    try:
        from app.main import app
    except Exception as exc:
        raise RuntimeError("Nie mozna zaladowac backendu RoboMap.") from exc

    port = find_free_port()
    ip = local_ip()
    print("RoboMap Local", flush=True)
    print("-----------------------------------------------", flush=True)
    print(f"Ten komputer: http://127.0.0.1:{port}", flush=True)
    print(f"Telefon/laptop w tej samej sieci: http://{ip}:{port}", flush=True)
    print("-----------------------------------------------", flush=True)

    threading.Thread(target=wait_and_open, args=(port,), daemon=True).start()
    os.environ['ROBOMAP_PORT'] = str(port)
    config = uvicorn.Config(app, host='0.0.0.0', port=port, log_level='info', access_log=False)
    server = uvicorn.Server(config)
    from tray import create_tray
    tray = create_tray(port, lambda: setattr(server, 'should_exit', True))
    try:
        server.run()
    finally:
        tray.stop()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except BaseException as exc:
        traceback.print_exception(type(exc), exc, exc.__traceback__)
        write_error(exc)
        sys.exit(1)
