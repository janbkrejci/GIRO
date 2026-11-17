---
id: HAIFA-LEARN-T11
title: "Lekce: Set each check's timeout from its measured runtime under load with a clear margin…"
status: done
workflow: build-review
depends_on: []
related: [HAIFA-S01-T49, HAIFA-S12-T01, HAIFA-TODO-T27]
writes: [aifactory/src/aifactory/seed/agents/tester/]
---

## Zadání
Zapiš do promptu role tester v semínku knihovny (`aifactory/src/aifactory/seed/agents/tester/`) pravidlo z lekce `L39b5d526`, kterou factory našla ve výtkách opakovaných v bězích:

> Set each check's timeout from its measured runtime under load with a clear margin and split slow suites into batches; a timed-out check (exit 124) is no verdict, so re-plan until every check finishes.

Pravidlo formuluj stručně, zařaď ho tam, kam v souboru patří, a nenech ho odporovat tomu, co tam už je. Jiné soubory neměň.
Po merge se změna do knihovny dostane příkazem `factory library seed` nebo novou verzí GIRO.

Hotovo znamená:
- Pravidlo je v promptu role tester v semínku knihovny (`aifactory/src/aifactory/seed/agents/tester/`).

### Důkazy

Výtky z 4 běhů, ze kterých lekce vznikla:

- repo `haifa`, běh `c56c620d`, task `HAIFA-S01-T49` (review): Dokončit úspěšný běh just pytest tests/e2e/test_f3_browser.py -n0. Vyšetřit timeout a nastavit přiměřený omezený časový rozpočet; při opakování chyb HAIFA_HOME odstranit jejich příčinu bez oslabení ochrany nebo assertions.
- repo `haifa`, běh `c56c620d`, task `HAIFA-S01-T49` (review): Dokončit požadované scoped ověření včetně browserového testu dialogu.: quality/07_* logy potvrzují úspěšný lint, typecheck, web-test, web-build, guide kontroly a přímé browserové volající. quality/10_backlog-and-review-browser/command.log však končí exit 124 po 190.011 s. Předchozí úplný běh skončil exit 1 kvůli třem chybám ochrany HAIFA_HOME při teardownu.
- repo `haifa`, běh `c56c620d`, task `HAIFA-S01-T49` (revise): Prověřen poslední timeout; implementace nevyžaduje změnu, zbývající browserové ověření potřebuje delší limit a podrobnější diagnostiku.
- repo `haifa`, běh `091a9d85`, task `HAIFA-S12-T01` (review): Dokončete just typecheck v samostatném test kroku s odpovídajícím timeoutem a doložte exit 0. Současný timeout není úspěch ani důkaz typové chyby. Úspěšný lint neopakujte bez relevantní změny.
- repo `haifa`, běh `091a9d85`, task `HAIFA-S12-T01` (review): Ověření inventury proti zdrojům, úplnosti mapování a odkazů.: quality/08_contract-document-verification/command.log — exit 1 na dvou zkrácených cestách z docs/rebranding/giro.md:64–65. Oba soubory existují pod aifactory/web/src/, který resolver nezahrnuje. quality/08_typecheck/command.log — exit 124 po 180 s. Lint prošel.
- repo `haifa`, běh `f3181340`, task `HAIFA-TODO-T27` (review): Dokončit frontend typecheck a vybraný multi-repo browser test s úspěšným návratovým kódem. Upravit rezervu timeoutů nebo rozdělit browserovou sadu.
- repo `haifa`, běh `f3181340`, task `HAIFA-TODO-T27` (review): test plan fits the change: Výběr testů přiměřeně pokrývá změny a přímé konzumenty: 46 frontendových souborů a 1051 testů prošlo; lint a Python typecheck prošly. quality/11_frontend-typecheck/command.log:2 hlásí exit 124 po 30 s; quality/11_shell-browser-tests/command.log:2 hlásí exit 124 po 200 s před dokončením multi-repo testu. Plán navíc přijímá nepřeložený název kroku. Release workflow v .github/workflows/ci.yml chybějící lokální ověření nenahrazuje.
- repo `haifa`, běh `f3181340`, task `HAIFA-TODO-T27` (review): Dokončit ověření App.test.ts:243 a :297 s úspěšným výsledkem. Zjistit příčinu timeoutů a podle zjištění opravit čekání, běhové podmínky nebo odůvodněně upravit testovací limit.
- repo `omnibus-a2ui`, běh `7aa4f1ef`, task `MIG-S07-T07` (review): B2: Get a green run of the scoped batches on the final code. No code change is needed unless a test fails. Rerun tests-modules-page, tests-meta-callers and tests-wire with timeouts that fit the load (about 450 s each, under the 600 s cap), or put meta-editor-module, translations-modules-page and nav-order in their own first batch. The run must include nav-order.test.ts.
- repo `omnibus-a2ui`, běh `7aa4f1ef`, task `MIG-S07-T07` (review): Testy meta editoru, stránky Modules, pořadí navigace a překladů procházejí (na finálním kódu): quality/12_tests-*/command.log: 0 failures, but all three batches stopped with rc 124. tests-modules-page stopped at translations-modules-page, so nav-order.test.ts, which covers the changed component, never ran. Single tests took up to 31 s under load.
- repo `omnibus-a2ui`, běh `7aa4f1ef`, task `MIG-S07-T07` (review): test plan fits the change: The scope is right: the same three batches, justified by find -newer. The 170-200 s timeouts left no margin over the 50-80 s idle runtime under load, so the plan produced no verdict.

<!-- factory: lesson=L39b5d526 -->

## Běhy
<!-- doplňuje GIRO při schválení PR -->
