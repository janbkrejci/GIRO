---
id: HAIFA-REFINEMENT-T120
title: Z Přehledu na projektový Kanban a z karty přímo na běh
status: done
depends_on: []
related: [HAIFA-REFINEMENT-T115]
writes: [aifactory/web/src/, aifactory/tests/e2e/test_multi_repo_browser.py, aifactory/src/aifactory/web/static/]
---

## Zadání
Přehled dnes odkazuje na projektový Backlog, který otevře Strom (HAIFA-REFINEMENT-T115). Klik na název nebo kód projektu na Přehledu má nově otevřít přímo Kanban s filtrem na daný projekt v daném repozitáři, a to i když měl uživatel předtím otevřený Strom. Odkaz i přepínače zůstanou přístupné z klávesnice. Přímé otevření běžného Backlogu dál respektuje uložený režim.

Klik na tělo karty běžícího nebo selhaného tasku v Kanbanu má otevřít detail konkrétního běžícího nebo posledního relevantního selhaného běhu v témže repozitáři. Rozhodni podle aktuálního stavu karty a dat o běhu, včetně situace, kdy má task starší selhané běhy a právě běží nový. Karta bez dostupného ID odpovídajícího běhu a ostatní karty dál odkazují na detail tasku. Zachovej existující ovládací prvky karet a drag-and-drop.

Doplň regresní testy pro výchozí Kanban a filtr projektu při přechodu z Přehledu, přímé odkazy na běžící i selhaný běh a fallback na task. Aktualizuj scénář kliknutí na projekt v existujícím browser testu. Projdi přímé volající a otestuj všechny editované soubory podle AGENTS.md. Spusť just lint, just typecheck, celý just web-test, relevantní browser test a just web-build pro distribuovaný frontend.

## Běhy
<!-- doplňuje GIRO při schválení PR -->
- 2026-11-20 · workflow build-test-review · PR https://github.com/janbkrejci/GIRO/pull/20 · náklady $0.00
