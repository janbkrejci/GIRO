---
id: HAIFA-LEARN-T09
title: "Lekce: Always include `just lint` and `just typecheck` in the test plan, even for docs-on…"
status: done
workflow: build-review
depends_on: []
related: [HAIFA-S12-T01, HAIFA-TODO-T27]
writes: [.factory/prompts/tester/]
---

## Zadání
Zapiš do promptu role tester (`.factory/prompts/tester/`) pravidlo z lekce `L814bd7c4`, kterou factory našla ve výtkách opakovaných v bězích:

> Always include `just lint` and `just typecheck` in the test plan, even for docs-only or test-only changes, and rerun typecheck after any later Python edit.

Pravidlo formuluj stručně, zařaď ho tam, kam v souboru patří, a nenech ho odporovat tomu, co tam už je. Jiné soubory neměň.

Hotovo znamená:
- Pravidlo je v promptu role tester (`.factory/prompts/tester/`).

### Důkazy

Výtky z 2 běhů, ze kterých lekce vznikla:

- repo `haifa`, běh `091a9d85`, task `HAIFA-S12-T01` (review): Doplňte test plán o povinné just lint a just typecheck a předejte je testerovi/runneru k provedení. Doložte také cílenou kontrolu relativních odkazů, zdrojových cest, úplnosti env/storage inventury a vazeb mapa → scénář → task. Produktové regresní testy ani plná sada nejsou potřeba.
- repo `haifa`, běh `091a9d85`, task `HAIFA-S12-T01` (review): test plan fits the change: test_result.test_plan a quality/04_test_plan/plan.json obsahují prázdné checks a žádné provedené příkazy. Vynechání produktových testů je přiměřené, ale AGENTS.md vyžaduje just lint a just typecheck vždy. Chybí také doložené cílené ověření odkazů, cest a úplnosti mapování požadované zadáním a docs/rebranding/giro.md:383.
- repo `haifa`, běh `091a9d85`, task `HAIFA-S12-T01` (revise): Doplněn konkrétní plán povinných kontrol a cílený ověřovací skript pro tester a runner.
- repo `haifa`, běh `f3181340`, task `HAIFA-TODO-T27` (review): Doplnit just typecheck pro aktuální Python změnu a získat úspěšný frontend typecheck, build aktuálních assetů a vybrané browserové testy včetně obou locale a multi-repo scénáře. Prověřit podmínky opakovaných timeoutů; timeout ani přeskočená kontrola nejsou úspěšným ověřením.
- repo `haifa`, běh `f3181340`, task `HAIFA-TODO-T27` (review): test plan fits the change: Rozsah vybraných frontendových regresí a browserových scénářů je přiměřený. quality/15_readiness-regressions/command.log má exit 1: App.test.ts:243 a :297 překročily limit 5000 ms; 103 testů prošlo. Frontend typecheck skončil exit 124 po 240 s, build exit 124 po 90 s a browserové testy byly přeskočeny. Plán navíc vynechává just typecheck, přestože od předchozího úspěšného výsledku změnil Python browserový test; projekt jej požaduje vždy. Lint prošel.

<!-- factory: lesson=L814bd7c4 -->

## Běhy
<!-- doplňuje GIRO při schválení PR -->
