# RoboMap NEON 10

Własna wersja oparta na RoboMap Local 9.6. Aktualizacja zachowuje istniejące mapy, makra, ustawienia, historię i autoryzację.

## Uruchomienie

Uruchom `INSTALUJ_ROBOMAP_NEON_v10.bat`. Instalator sprawdza środowisko, przygotowuje biblioteki, kopiuje poprzedni kod i dane do katalogu `backups` aplikacji, aktualizuje program i tworzy skrót na pulpicie. Codziennie używaj skrótu RoboMap Local. Zamknięcie okna pozostawia program w zasobniku Windows. Ponowne otwarcie skrótu przywraca okno. Menu ikony obok zegara pozwala sterować robotem, otworzyć przeglądarkę i zakończyć serwer.

## Co dodano

- Neonowy pulpit, animowany model SVG E5, wskaźnik baterii, tryb alarmowy i opcjonalne dźwięki przycisków.
- Polskie komendy głosowe: start/sprzątaj/odkurzaj, baza/wracaj, pauza, stop. Potwierdzenie mową następuje po poprawnej odpowiedzi API. Negacja blokuje start. Mikrofon działa po kliknięciu. Lokalna mowa wymaga obsługi i polskiego pakietu w przeglądarce. Opcja usługi przeglądarki może wysyłać nagranie do dostawcy mowy; domyślnie jest wyłączona. HTTP w LAN i WebView2 mogą ograniczać dostępność mikrofonu.
- Cat Mode: krótkie losowe ruchy, przerwy, moc Cicha, limit 60 sekund, wymagane co najmniej 20% baterii. Tryb przerywa się po błędzie, utracie łączności lub STOP. Moc Cicha nie oznacza wyłączenia wentylatora; E5 udostępnia trzy poziomy mocy. Po trybie pozostaje moc Cicha, którą można zmienić przyciskiem.
- RPG: 10 EXP za minutę zaobserwowanego sprzątania, 10 rang co 1000 EXP, Nocny Marek po 23:00 i Maratończyk po ponad godzinie ciągłego sprzątania. Historia EXP zaczyna się od instalacji v10; nie dolicza czasu wyłączonego hosta ani przerw w telemetrii.
- Radar Canvas: orientacyjna pozycja na podstawie czasu komend, 10 cm/s i 90°/s jako założenia, czerwone punkty dla zgłoszeń zablokowanego zderzaka. Brak gwarancji wykrywania zwykłych dotknięć mebli. Reset ustawia lokalne (0,0). Po ponownym uruchomieniu radar zaczyna od nowa. To nie jest SLAM, mapa LiDAR ani mechanizm NO-GO.
- Harmonogram tygodniowy z ochroną przed wielokrotnym wykonaniem po restarcie. Pomija zaplanowany start podczas ciszy nocnej, niedostępności robota lub niskiej baterii. Nie nadrabia pominiętych godzin, kiedy host był wyłączony.
- Profile: Normalny = Standard, bez Win+L i reguł nocnych; Nocny = Cicha i reguły nocne, bez Win+L; Nie ma mnie = Mocna i Win+L, bez reguł nocnych. Profil nie uruchamia sprzątania natychmiast.
- Watchdog pilota: po ok. 1,2 s bez odnawiania sterowania wysyła STOP. Globalny awaryjny STOP blokuje następne komendy ruchu do jawnego odblokowania. Rzeczywisty czas dotarcia STOP zależy od połączenia i czasu odpowiedzi robota.
- Token miIO jest szyfrowany Windows DPAPI. Starszy jawny token migruje przy pierwszym odczycie. Kopia przed aktualizacją zachowuje dawny format — chroń ją tak jak poprzednie dane.

## Pierwsze połączenie

AutoPair wykrywa E5 w LAN. Jeśli urządzenie ujawni prawidłowy token, aplikacja zapisuje go automatycznie. Jeśli sparowany robot ukrywa token, otwórz Ustawienia na komputerze hostującym i jednorazowo zaloguj się do własnego konta Xiaomi. Konto może wymagać dodatkowej weryfikacji, której biblioteka nie obsługuje. Zapamiętywanie konta do przyszłego odzyskiwania tokenu jest opcjonalne i domyślnie wyłączone. Zapamiętane dane konta i token są chronione DPAPI bieżącego użytkownika Windows. Nie można wyliczyć sekretu z adresu IP ani Device ID.

Sterowanie po uzyskaniu tokenu działa w LAN. Adres komputera jest w Ustawieniach, port zwykle 8787. Aplikacja nie otwiera portów routera ani nie zmienia zapory. Panel LAN jest przeznaczony dla zaufanej sieci domowej i nie ma logowania użytkowników. Żądania z obcych źródeł przeglądarkowych są blokowane.

## Zachowane funkcje i granice

Mapa, strefy NO-GO, wirtualne ściany, zapis mapy, import geometrii RoomPlan przez istniejący endpoint, makra, D-pad, gamepad, Win+L, tryb nocny, historia SQLite i statystyki pozostają dostępne. Mapa nie steruje autonomiczną nawigacją E5. Nie dodano natywnej aplikacji RoomPlan na iPhone ani wiarygodnej lokalizacji E5 na mapie mieszkania. Harmonogram i dotychczasowe reguły są gotowe; dowolny wizualny edytor reguł warunkowych pozostaje poza tą wersją.

## Weryfikacja

Testy jednostkowe korzystają z fałszywego sterownika — nie poruszają rzeczywistym robotem. Obejmują ochronę sekretu, niepoprawne tokeny, naliczanie EXP, awaryjny STOP, utratę heartbeat, anulowanie polecenia i harmonogram. Testy UI wykonano na podglądzie desktop i 412 px. Launcher skompilowano jako x64. Pełny test fizycznych ruchów i logowania Xiaomi wymaga działającego tokenu i konta właściciela.

Specyfikacja E5: https://miot-spec.org/miot-spec-v2/instance?type=urn:miot-spec-v2:device:vacuum:0000A006:xiaomi-c108:1

Web Speech API: https://developer.mozilla.org/en-US/docs/Web/API/SpeechRecognition/processLocally


## NEON 10.1 — RC/Gamepad Reliability Patch

Sterowanie padem zostało przebudowane pod odporność na utratę pakietów i wolne odpowiedzi miIO:

- kolejka komend nie rośnie bez końca — transport zachowuje tylko STOP i najnowszy kierunek,
- heartbeat 220 ms odnawia wyłącznie lokalny lease serwera i nie spamuje robota powtarzanymi zapisami MIoT,
- watchdog zatrzymuje robota po ok. 0,72 s bez heartbeat,
- ręczne komendy MIoT mają krótki timeout i nie blokują STOP przez standardowe 5 s,
- gałka ma hysteresis/dead-zone 0,48/0,32 i 45 ms stabilizacji zmiany kierunku,
- RoboMap wiąże się z jednym konkretnym gamepadem zamiast skakać między urządzeniami,
- zgubiona komenda jest ponawiana tylko dla aktualnego kierunku; stare kierunki nie są odtwarzane,
- utrata fokusu, ukrycie strony i odłączenie pada powodują STOP, a serwerowy watchdog jest dodatkową warstwą bezpieczeństwa.


## NEON 10.2 — RC Clean / Silent / Reverse

- Sterowanie gamepadem odbywa się lewą gałką: góra = przód, dół = **jazda do tyłu**, lewo/prawo = obrót. D-pad działa równolegle.
- E5 rozdziela komendę kierunku (MIoT service 13 / property 1) od uruchomienia sprzątania. Dlatego sam kierunek porusza kołami, ale nie musi uruchamiać szczotek. NEON 10.2 przy pierwszym wychyleniu gałki rozpoczyna jedną sesję `RC Clean`: najpierw włącza tryb DND, następnie `start-sweep`, a dopiero potem wysyła kierunek. Nie trzeba naciskać X/Cross.
- Puszczenie gałki wysyła tylko `direction=stop`, więc szczotki pozostają gotowe podczas krótkich postojów. Wyłączenie gamepada, odłączenie kontrolera, utrata fokusu/ukrycie strony albo zamknięcie aplikacji kończy sesję RC; jeśli RoboMap sam uruchomił sprzątanie, wysyła pauzę.
- Sygnały robota są automatycznie wyciszane przez właściwość E5 `no-disturb` (service 10 / property 1). Firmware może nadal emitować krytyczne ostrzeżenia bezpieczeństwa/błędów — aplikacja ich nie omija.
- `back` jest wysyłane jako oficjalna wartość `3` właściwości `remote-control.direction-key`, zgodnie ze specyfikacją `xiaomi.vacuum.c108`.

## NEON 10.3 — Silent RC / mniej piknięć

- Poprzednie wersje włączały tylko `no-disturb=true`. W E5 tryb DND ma jednak także osobne okno czasu (`service 10 / property 2`). Domyślnie jest to 22:00–08:00, więc w dzień samo ustawienie flagi DND nie musi wyciszać komunikatów.
- Przy rozpoczęciu sesji RC RoboMap odczytuje dotychczasową konfigurację DND, tymczasowo ustawia `00:00:00-23:59:59` + `no-disturb=true`, a po zakończeniu RC przywraca wcześniejsze ustawienia. Dzięki temu harmonogram DND użytkownika nie jest trwale nadpisywany.
- Gamepad dostał 140 ms „neutral grace” przy przechodzeniu gałką przez środek i 90 ms stabilizacji zmiany kierunku. Ogranicza to niepotrzebne `STOP -> kierunek` podczas szybkiego przechodzenia między sektorami, a więc także liczbę zapisów `remote-control.direction-key`.
- Nadal obowiązuje watchdog i awaryjny STOP. Jeżeli firmware E5 generuje buzzer bezpośrednio dla każdego zapisu `direction-key`, publiczna specyfikacja modelu nie udostępnia osobnej regulacji głośności/alarmu; wtedy całkowite programowe wyciszenie wymagałoby znalezienia prywatnego polecenia firmware i nie jest gwarantowane przez standard MIoT.


## NEON 10.4 — Live Map / pozycja robota

- Mapa mieszkania ma osobne narzędzia do rysowania **ścian domu**, **drzwi**, wirtualnych ścian, stref NO-GO, bazy i ręcznej pozycji robota. Geometria może też zostać zaimportowana z pliku JSON zgodnego ze schematem RoomPlan/RoboMap (`walls`, `doors`, `windows`, `openings`, `furniture`). To nie jest bezpośredni importer dowolnego natywnego pliku USDZ/CapturedRoom z iOS — eksport musi dostarczyć te pola w JSON.
- Dodano trwały lokalny moduł `localization.json`. Pozycja ma współrzędne X/Y, kierunek, źródło i wskaźnik pewności. Ślad jest zachowywany między uruchomieniami.
- Najlepszym punktem odniesienia jest baza. Po ustawieniu bazy na mapie użyj **„Robot jest w bazie”**. Gdy telemetria potwierdza `charging`, `charged` lub `docked`, RoboMap ponownie przyciąga pozycję do zapisanej bazy i ustawia pewność 100%.
- Podczas sterowania D-padem, gamepadem, makrem lub inną komendą ręczną pozycja jest aktualizowana na żywo z lokalnej odometrii czasu poleceń. Interfejs odpytuje tylko lokalny endpoint co ok. 320 ms, więc nie zwiększa ruchu MIoT do odkurzacza.
- Domyślne założenia ruchu to 10 cm/s i 90°/s. Można je kalibrować w panelu mapy, aby znacznik lepiej odpowiadał faktycznemu E5. Można też wybrać **📍 Robot** i kliknąć/przeciągnąć na mapie, aby skorygować pozycję i kierunek.
- E5 `xiaomi.vacuum.c108` nie wystawia przez używaną publiczną powierzchnię MIoT absolutnej pozycji XY. Dlatego podczas autonomicznego sprzątania/powrotu RoboMap nie udaje GPS/SLAM: pokazuje **ostatnią znaną pozycję** i stopniowo obniża pewność. Dokładna globalna pozycja wraca po ponownym zadokowaniu albo ręcznej kalibracji.
- Strefy NO-GO na tej mapie pozostają warstwą RoboMap. Bez niezależnego, wiarygodnego pomiaru XY nie można obiecać ich dokładnego egzekwowania podczas autonomicznego sprzątania E5.

### Pierwsza kalibracja mapy

1. Zaimportuj lub narysuj geometrię domu.
2. Umieść ikonę **⌂ Baza** dokładnie tam, gdzie naprawdę stoi stacja.
3. Zapisz mapę.
4. Gdy fizyczny E5 stoi na stacji, kliknij **„Robot jest w bazie”**.
5. Od tej chwili ręczne ruchy RoboMap przesuwają znacznik i zapisują ślad. Jeśli po kilku metrach widać dryf, popraw cm/s lub °/s albo użyj narzędzia **📍 Robot**.
