# RoboMap Local

Lokalny panel sterowania Xiaomi Robot Vacuum E5 (`xiaomi.vacuum.c108`) dla Windows i przeglądarki. Aktualny kod: 10.7 z rozszerzeniem reguł automatyzacji.

## Funkcje

- Sterowanie miIO/MIoT: sprzątanie, pauza, baza, moc ssania, pilot i gamepad.
- Mapa SVG, kalibracja pozycji, szacowana odometria i szkic przeszkód.
- Historia wersji mapy w SQLite i przywracanie do edytora.
- WebSocket z ponawianiem połączenia i awaryjnym odpytywaniem HTTP.
- Makra, harmonogram, automatyzacje Windows, tryb nocny i własne reguły.
- Historia działania, osiągnięcia, sterowanie głosowe zależne od przeglądarki.
- Jasny interfejs inspirowany Xiaomi Home i launcher Windows WebView2.

## Uruchomienie ze źródeł (Windows, PowerShell)

Wymagany Python 3.12 lub 3.13. Polecenia wykonuj w katalogu repozytorium:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r server/requirements.txt
$env:DATA_DIR = Join-Path $PWD 'data'
.\.venv\Scripts\python.exe start_robomap.py
```

Panel domyślnie działa na `http://127.0.0.1:8787`; jeśli port jest zajęty, launcher Python wybiera kolejny wolny port. Połączenie z własnym robotem skonfiguruj w Ustawieniach. Token nie jest dołączony do repozytorium. Normalne polecenia robota działają lokalnie; pozyskanie tokenu z konta Xiaomi może wymagać dodatkowej autoryzacji i nie jest gwarantowane.

Serwer jest przeznaczony do zaufanej sieci domowej. Nie wystawiaj go bezpośrednio do Internetu: projekt nie ma pełnego mechanizmu logowania użytkowników panelu.

## Okno Windows

`launcher.cs` jest kodem launchera WinForms/WebView2 używanego w lokalnej instalacji. Oczekuje struktury `%LOCALAPPDATA%\RoboMapLocal` z prywatnym środowiskiem Python i bibliotekami WebView2. Repozytorium nie zawiera gotowego EXE, bibliotek Microsoft ani samodzielnego instalatora. Powyższe polecenia uruchamiają wariant WWW i zasobnik systemowy.

## Mapowanie: ograniczenia

E5 nie udostępnia temu sterownikowi dokładnej pozycji XY podczas autonomicznego sprzątania. Mapa demonstracyjna nie jest skanem mieszkania. Odometrię wyznaczamy z poleceń ręcznych; szkic ścian wymaga pomiarów i korekty. Wirtualne ściany i NO-GO na planie nie gwarantują fizycznego ograniczenia ruchu robota. Zgłoszenie zablokowanego zderzaka nie oznacza wykrywania każdego dotknięcia przeszkody.

## Automatyzacje

Reguły mogą reagować na godzinę, niski stan baterii oraz obserwowany czas sprzątania. Nowe reguły są domyślnie wyłączone; każda wykonuje się najwyżej raz dziennie. Wymagają uruchomionego RoboMap. Czas sprzątania w regułach zeruje się po przerwie lub restarcie aplikacji.

## Testy

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
$env:PYTHONPATH = Join-Path $PWD 'server'
.\.venv\Scripts\python.exe -m pytest server/tests -q
```

Testy używają atrap sterownika i katalogów tymczasowych. Nie potwierdzają działania wszystkich poleceń na sprzęcie.

## Dane prywatne

Repozytorium zawiera kod, dokumentację i testy. Pominięto tokeny, hasła, zapisane mapy mieszkania, bazy danych, logi, kopie zapasowe oraz środowisko Python. `.gitignore` wyklucza te pliki z kolejnych zapisów Git.

Projekt niezależny, nie jest oficjalną aplikacją Xiaomi.
