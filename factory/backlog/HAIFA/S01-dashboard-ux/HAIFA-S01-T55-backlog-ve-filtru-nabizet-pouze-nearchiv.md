---
id: HAIFA-S01-T55
title: "Backlog: ve filtru nabízet pouze nearchivované projekty"
status: done
archived: true
workflow: build-test-review
depends_on: [HAIFA-REFINEMENT-T114]
writes: [aifactory/web/src/lib/backlog.ts, aifactory/web/src/lib/backlog.test.ts, aifactory/web/src/views/BacklogView.vue, aifactory/web/src/views/BacklogView.test.ts, aifactory/tests/e2e/test_f3_browser.py, aifactory/src/aifactory/web/static/]
---

## Zadání
Upravit nabídku filtru projektu na stránce Backlog tak, aby ve stromu i kanbanu obsahovala pouze nearchivované projekty a volbu „vše“. Použít existující příznak archived kořenového kontejneru, nikoli stav dokončení projektu nebo archivaci jednotlivých tasků. Přepínač „Zobrazit archivované projekty“ nadále umožní zobrazit archivované projekty ve stromu, ale nerozšíří nabídku filtru.

Zdroj nabídky je v aifactory/web/src/lib/backlog.ts ve funkci projectOptions: projekty skládá z items i steps. Zajistit, aby projekt označený v items jako archivovaný nebyl znovu přidán ze steps. Zachovat dosavadní fallback názvů, řazení a podporu backlogu s méně než třemi úrovněmi.

V aifactory/web/src/views/BacklogView.vue je seznam projects sdílen také s formulářem nového stepu. Oddělit nabídku filtru podle potřeby tak, aby tato oprava neměnila chování formuláře. Archivovaný projekt uložený ve filtru nebo předaný odkazem /backlog/project/<id> se vyhodnotí jako „vše“. Totéž platí po archivaci právě vybraného projektu a obnovení dat. Po obnovení projektu z archivu se projekt opět nabídne.

Doplnit regresní testy v aifactory/web/src/lib/backlog.test.ts a aifactory/web/src/views/BacklogView.test.ts. Rozšířit existující browserový scénář test_project_archive_from_tree v aifactory/tests/e2e/test_f3_browser.py o kontrolu nabídky při archivaci a obnovení projektu. Před úpravou dohledat přímé volající projectOptions a použití project-filter v aifactory/web/src/ a celém aifactory/tests/. Neměnit API, formát backlogu ani pravidla archivace.

Hotovo znamená:
- Filtr ve stromu i kanbanu nabízí pouze nearchivované projekty a „vše“, i při zapnutém zobrazení archivovaných projektů.
- Test ověřuje, že steps nevrátí archivovaný projekt do nabídky a nearchivovaný dokončený projekt zůstane dostupný.
- Testy pokrývají uložený filtr archivovaného projektu, přímý odkaz na něj a archivaci právě vybraného projektu; výsledkem je účinný filtr „vše“.
- Obnovený projekt se znovu objeví v nabídce a chování formuláře nového stepu zůstane zachováno.
- Projdou just lint, just typecheck, just web-test a just pytest tests/e2e/test_f3_browser.py -n0; spustí se každý upravený testový soubor.
- Proběhne just web-build a aktualizuje distribuovaný statický frontend.

## Běhy
<!-- doplňuje GIRO při schválení PR -->
- 2026-11-17 · workflow build-test-review · PR https://github.com/janbkrejci/GIRO/pull/8 · náklady $0.00
