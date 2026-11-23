# HAIFA-S13-T01 — Implementace API snímání obrazovky

Skryté API pro více repozitářů poskytuje dostupnost a binární stream primárního
monitoru. Stream používá `mss` a Pillow, kontrolu `_enabled` tajného terminálu,
jediného odběratele ve stavu aplikace a úklid při shutdownu. Žádný snímek se neukládá.

Pracovní vlákno vlastní celý životní cyklus `mss`, protože jeho prostředky jsou
vázané na vlákno. Kódování JPEG, změna velikosti a hashování probíhají mimo smyčku
událostí. Fronta s kapacitou jednoho rámce omezuje paměť při pomalém příjemci;
shodné surové snímky se nekódují ani nepřenášejí znovu.

Regresní testy v `tests/web/test_web_screen.py` používají syntetický backend
a mock `mss`. Pokrývají loopback, Linux bez X11, Wayland, macOS oprávnění,
geometrii monitoru, pořadí barev, JPEG a délkové hlavičky, validaci parametrů,
výhradní odběr, opětovné připojení, neměnné snímky, příslušnost prostředků
k vláknu a úklid při odpojení i shutdownu aplikace.

Kontroly celého změněného chování, lint a strict mypy provádí samostatný testovací
krok workflow. Diagnostický test
`uv run pytest tests/web/test_web_screen.py::test_stream_frames_exclusion_disconnect_and_reconnect -n0 -q`
prošel (1 test, 18,41 s). Ověřil dva platné JPEG rámce s délkovou hlavičkou,
odmítnutí druhého odběratele, odpojení a opětovné připojení.
Frontend a ovládání vzdálené plochy nejsou součástí tohoto API.

Po rebase jsou `screen_disabled` a `screen_unsupported` zaregistrovány v katalogu
chyb a obě cesty zahrnuty do očekávaného seznamu API rout. Testy odmítnutí
streamu kontrolují také platnost envelope vůči katalogu. Ověření integrace
pokrývají `tests/web/test_web_routes.py`, `tests/web/test_web_app.py`,
`tests/web/test_web_cleanup.py` a `tests/web/test_web_terminal.py`; přímé použití
katalogu dále pokrývají `tests/test_skill.py` a `tests/test_json_envelope.py`.
V navazujícím builder kroku se kontroly neopakovaly; běží v testovacím kroku.
