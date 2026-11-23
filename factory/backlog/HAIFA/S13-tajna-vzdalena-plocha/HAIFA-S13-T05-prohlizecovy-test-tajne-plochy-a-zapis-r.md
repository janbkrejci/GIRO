---
id: HAIFA-S13-T05
title: Prohlížečový test tajné plochy a zápis rozhodnutí
status: todo
workflow: build-test-review
depends_on: [HAIFA-S13-T03, HAIFA-S13-T04]
writes: [aifactory/tests/e2e/test_screen_browser.py, aifactory/tests/tiers.py, docs/decisions.md]
---

## Zadání
## Zadání

Ověř celou tajnou plochu (HAIFA-S13-T01 až T04) v prohlížeči a zapiš rozhodnutí.

Kde:
- `aifactory/tests/e2e/test_screen_browser.py` (nový): vzorem je existující prohlížečový test tajného terminálu z HAIFA-REFINEMENT-T82, tedy spuštění dashboardu přes helpery `dashboard` a `make_repo` z testovacích fixtures. Ten test jen čti, neměň ho.
  - Dashboard se spustí s `GIRO_SCREEN_BACKEND=fake` (`monkeypatch.setenv`), aby test nesahal na skutečnou obrazovku ani vstup a běžel i bez displeje.
  - Test stiskne Shift, Control, Shift, Control a ověří, že se otevře overlay a na canvasu se objeví nenulový obraz (pixel z falešného snímku).
  - Klikne doprostřed obrazu a ověří požadavek `POST /api/screen/input` s `button`, `down` a souřadnicemi přibližně 0,5/0,5 (`page.expect_request`).
  - Stiskne `KeyA` a ověří událost `{type: 'key', code: 'KeyA'}`.
  - Sekvence znovu overlay zavře a pošle `release_all`.
  - Při otevřené ploše Control, Shift, Control, Shift terminál neotevře.
  - Čekání dělá přes `expect`, žádné pevné spánky.
- `aifactory/tests/tiers.py`: zařaď nový soubor mezi browserové (a slow, pokud to odpovídá ostatním browserovým testům).
- `docs/decisions.md`: vedle odstavce **Tajný terminál** přidej odstavec **Tajná plocha**. Popiš:
  - sekvenci,
  - loopback a write guard,
  - SSH tunel jako způsob vzdáleného použití,
  - routy a jejich meze,
  - formát rámců,
  - normalizované souřadnice a `KeyboardEvent.code`,
  - jediného odběratele,
  - `release_all`,
  - backendy (mss + Pillow, SendInput, CGEvent, XTest),
  - nepodporovaný Wayland a oprávnění macOS,
  - falešný backend jen pro testy,
  - že se obraz ani vstup neukládají.

Omezení: příručka zůstává beze změny, funkce je tajná. Produkční kód neměň. Když test odhalí chybu, nahlas ji jako follow-up s přesným popisem.

Mimo rozsah: test se skutečnou obrazovkou a skutečným vstupem.

Hotovo znamená:
- `just pytest tests/e2e/test_screen_browser.py -n0` projde.
- `just lint` a `just typecheck` jsou bez chyb.
- `docs/decisions.md` obsahuje odstavec Tajná plocha, který odpovídá skutečnému chování API a UI.

## Běhy
<!-- doplňuje GIRO při schválení PR -->
