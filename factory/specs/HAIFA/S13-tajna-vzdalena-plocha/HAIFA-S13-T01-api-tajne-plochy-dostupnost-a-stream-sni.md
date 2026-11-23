# HAIFA-S13-T01 — API tajné plochy

Backend je dostupný pouze v aplikaci pro více repozitářů. Používá stejnou
kontrolu loopback serveru a klienta jako tajný terminál prostřednictvím `_enabled`.
Nezapisuje obrazová data na disk.

`GET /api/screen` vrací standardní envelope s daty
`{enabled, supported, reason, width, height}`. Rozměry jsou fyzické pixely primárního
monitoru `mss.monitors[1]`; interní geometrie obsahuje také `left` a `top`.
Nepodporovaný backend vrací nulové rozměry a stabilní důvod.

`GET /api/screen/stream` přenáší rámce ve formátu čtyřbajtová délka JPEG v síťovém
pořadí bajtů a samotný JPEG. Parametry jsou celá čísla: `fps` 1–30 (výchozí 15),
`max_width` 320–3840 (1280), `quality` 20–95 (60). Neplatná hodnota vrací HTTP 400
`usage_error`. Obrázek se nezvětšuje a zachovává poměr stran.

Stream vyžaduje loopback (403 `screen_disabled`), podporu snímání
(501 `screen_unsupported`, důvod v `data.reason`) a volné místo jediného odběratele
(409 `busy`). Rezervace odběratele vzniká před prvním asynchronním čekáním.
Odpojení i ukončení aplikace ukončí pracovní běh a zavřou snímací backend.

Jeden pracovní běh přes `anyio.to_thread` vlastní vytvoření, čtení i zavření `mss`.
Pillow převádí surové BGRA/BGRX pixely na RGB a kóduje JPEG v témže vlákně.
SHA-256 surového snímku brání opakovanému posílání stejného obsahu. Fronta drží
nejvýše jeden rámec; při pomalém příjemci jej nahrazuje nejnovějším.

Linux bez `DISPLAY` hlásí `no_display`, případně `wayland_unsupported`, pokud má
`WAYLAND_DISPLAY`. macOS při selhání snímání vrací `permission_denied` s instrukcí
k povolení Screen Recording v System Settings > Privacy & Security. Ostatní
selhání snímání hlásí `capture_failed`.

`GIRO_SCREEN_BACKEND=fake` zapíná deterministický syntetický backend pro testy,
nezávislý na přítomnosti monitoru. Rozhraní nepřijímá vstup myši, klávesnice,
zvuku ani schránky.
