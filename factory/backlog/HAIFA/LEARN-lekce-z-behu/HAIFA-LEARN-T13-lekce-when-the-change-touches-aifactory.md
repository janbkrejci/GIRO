---
id: HAIFA-LEARN-T13
title: "Lekce: When the change touches `aifactory/web/`, put the full `just web-test` in the test…"
status: done
workflow: build-review
depends_on: []
related: [HAIFA-S01-T50, HAIFA-S01-T53, HAIFA-S01-T54, HAIFA-S12-T04, HAIFA-S12-T03]
writes: [.factory/prompts/tester/]
---

## Zadání
Zapiš do promptu role tester (`.factory/prompts/tester/`) pravidlo z lekce `L1278e91e`, kterou factory našla ve výtkách opakovaných v bězích:

> When the change touches `aifactory/web/`, put the full `just web-test` in the test plan and run it on the final code; never defer it to CI, the builder or a later step.

Pravidlo formuluj stručně, zařaď ho tam, kam v souboru patří, a nenech ho odporovat tomu, co tam už je. Jiné soubory neměň.

Hotovo znamená:
- Pravidlo je v promptu role tester (`.factory/prompts/tester/`).

### Důkazy

Výtky z 5 běhů, ze kterých lekce vznikla:

- repo `haifa`, běh `b0000405`, task `HAIFA-S01-T50` (review): Doplnit do testovacího plánu just web-test a doložit jeho úspěšné provedení na aktuálním kódu. Opakování backendových, browser a build kontrol není potřeba.
- repo `haifa`, běh `b0000405`, task `HAIFA-S01-T50` (review): Spustit předepsané kontroly včetně just web-test, just web-build, lint, typecheck a změněných testů.: Logy quality/05_*, quality/08_* a quality/11_* dokládají úspěšný build, browser testy, scoped frontendové testy, backend, lint a typecheck. Spuštění just web-test chybí; context_handoff/test-plan.md úplnou frontendovou kontrolu výslovně odkládá.
- repo `haifa`, běh `b0000405`, task `HAIFA-S01-T50` (review): test plan fits the change: Poslední plán vhodně ověřuje opravu testu a navazuje na dřívější zelené kontroly. Celkové ověření však vynechává just web-test, které AGENTS.md i zadání vyžadují při změně aifactory/web/. Výjimka pro factory běh se týká just check a just e2e.
- repo `haifa`, běh `b0000405`, task `HAIFA-S01-T50` (revise): Doplněno předání testerovi o povinné just web-test; implementace podle review nevyžaduje další změny.
- repo `haifa`, běh `ba2169ae`, task `HAIFA-S01-T53` (review): Tester musí spustit požadovaný just web-test na aktuálním kódu a doložit úspěšný výsledek. Aktuální plán jej výslovně odkládá. Při úspěchu není potřeba měnit implementaci; celý just check ani just e2e nevyžaduji.
- repo `haifa`, běh `ba2169ae`, task `HAIFA-S01-T53` (review): Spustit změněné testy, just lint, just typecheck, just web-test, just web-build a příslušné browser testy bez živých modelů a služeb.: quality/11_scoped-web-tests/command.log potvrzuje exit 0 a 422 testů v 19 souborech. Logy 05_lint, 05_typecheck, 08_web-build a 08_formatting-browser-tests potvrzují exit 0; browser testy mají 3 passed a stubované API. context_handoff/test-plan.md výslovně odkládá celý just web-test; jeho provedení není doloženo.
- repo `haifa`, běh `ba2169ae`, task `HAIFA-S01-T53` (revise): Implementace nevyžaduje změny; předávací testovací plán nyní výslovně požaduje zbývající just web-test.
- repo `haifa`, běh `dc9c566b`, task `HAIFA-S01-T54` (review): Dokončit odložené just web-test a předat log výsledku.
- repo `haifa`, běh `dc9c566b`, task `HAIFA-S01-T54` (review): test plan fits the change: Logy potvrzují 22 Python testů, 32 frontendových testů, build, lint, mypy a browser scénář s exit 0. Výběr odpovídá změně příručky a přímým volajícím. Požadované just web-test však zůstává podle context_handoff/test-plan.md odložené bez konečného výsledku či přípustného zdůvodnění vypuštění; CI tuto kontrolu nenahrazuje.
- repo `haifa`, běh `dc9c566b`, task `HAIFA-S01-T54` (revise): Zachováno schválené rozdělení migrace: díl příručky je ověřen, celé T54 zůstává blokované; závěrečné just web-test musí provést testovací krok.
- repo `haifa`, běh `295c5c8d`, task `HAIFA-S12-T04` (review): Tester/runner musí na finálním opraveném kódu provést just web-test včetně celé frontendové sady a potřebné cílené backendové regrese; doložit výsledky před publikací PR.
- repo `haifa`, běh `295c5c8d`, task `HAIFA-S12-T04` (review): test plan fits the change: quality/08_test_plan/plan.json vybírá pouze pět Vitest souborů; log potvrzuje 104 testů. Požadovaný just web-test s celou frontendovou sadou neproběhl ani v quality/05. Převzetí nezměněného úspěšného web-build a nezasažených backendových kontrol je odůvodněné; lint/typecheck a oba browser jazyky prošly. Chybí úplná frontendová sada a regrese obou datových chyb.
- repo `haifa`, běh `3d9b959a`, task `HAIFA-S12-T03` (review): Nech testovací krok dokončit odložené just web-test a ověřit nové regresní testy.
- repo `haifa`, běh `3d9b959a`, task `HAIFA-S12-T03` (review): Proběhlo požadované frontendové, cílené, browserové a vizuální ověření.: quality/08 logy potvrzují cílené funkční kontroly; quality/11 potvrzuje lint/typecheck. test-plan.md výslovně odkládá just web-test a uvádí nedoloženou vizuální inspekci.
- repo `haifa`, běh `3d9b959a`, task `HAIFA-S12-T03` (review): test plan fits the change: Poslední formátovací opravu přiměřeně pokrývá lint/typecheck. Celkovému plánu chybí povinné just web-test a regrese porovnávající dokumentovaný docs_root se skutečným nastavením.

<!-- factory: lesson=L1278e91e -->

## Běhy
<!-- doplňuje GIRO při schválení PR -->
