import threading
import urllib.request
import webbrowser
import pystray
from PIL import Image, ImageDraw


def create_tray(port, shutdown):
    url = f'http://127.0.0.1:{port}'
    icon_image = Image.new('RGBA', (64, 64), '#050505')
    draw = ImageDraw.Draw(icon_image)
    draw.ellipse((5, 5, 59, 59), outline='#00f3ff', width=4)
    draw.rounded_rectangle((19, 19, 45, 45), radius=8, fill='#00f3ff')

    def command(action):
        def on_click(icon, item):
            def run():
                try:
                    request = urllib.request.Request(url + '/api/robot/' + action, data=b'{}',
                                                     headers={'Content-Type': 'application/json'})
                    with urllib.request.urlopen(request, timeout=30) as response:
                        response.read()
                except Exception:
                    icon.notify('Nie potwierdzono komendy. Sprawdź panel RoboMap.', 'RoboMap')
            threading.Thread(target=run, daemon=True).start()
        return on_click

    def quit_app(icon, item):
        # Shutdown lifespan sends STOP and drains background tasks before exit.
        shutdown()

    icon = pystray.Icon('RoboMap', icon_image, 'RoboMap NEON / lokalne centrum sterowania', pystray.Menu(
        pystray.MenuItem('▶ Rozpocznij sprzątanie', command('start')),
        pystray.MenuItem('⏸ Zatrzymanie', command('pause')),
        pystray.MenuItem('⌂ Powrót do bazy', command('dock')),
        pystray.MenuItem('⚙ Otwórz w przeglądarce', lambda: webbrowser.open(url), default=True),
        pystray.MenuItem('❌ Zamknij serwer', quit_app),
    ))
    threading.Thread(target=icon.run, daemon=True).start()
    return icon
