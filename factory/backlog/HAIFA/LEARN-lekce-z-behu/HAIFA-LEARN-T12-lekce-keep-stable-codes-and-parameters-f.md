---
id: HAIFA-LEARN-T12
title: "Lekce: Keep stable codes and parameters for every user-visible state, step name and backe…"
status: todo
workflow: build-review
depends_on: []
related: [HAIFA-S01-T54, HAIFA-TODO-T27, HAIFA-TODO-T28, HAIFA-TODO-T29]
writes: [AGENTS.md]
---

## Zadání
Zapiš do `AGENTS.md` pravidlo z lekce `L2fb3a849`, kterou factory našla ve výtkách opakovaných v bězích:

> Keep stable codes and parameters for every user-visible state, step name and backend message and translate them from the EN/CS catalogs at render time; never display raw server text, enum values or strings already translated when they were stored, and add a regression test that switches the locale while the text is shown.

Pravidlo formuluj stručně, zařaď ho tam, kam v souboru patří, a nenech ho odporovat tomu, co tam už je. Jiné soubory neměň.

Hotovo znamená:
- Pravidlo je v `AGENTS.md`.

### Důkazy

Výtky z 4 běhů, ze kterých lekce vznikla:

- repo `haifa`, běh `dc9c566b`, task `HAIFA-S01-T54` (review): Lokalizovat všechny uvedené obrazovky, sdílené komponenty, stavy, validace, tooltipy, aria-label, titulek a html lang.: aifactory/web/src/lib/i18n/catalogs.ts:2 obsahuje jen základ a příručku; App.vue:115 má české titulky; OverviewView.vue:87 českou hlavní akci; aifactory/web/index.html:2 má lang="cs".
- repo `haifa`, běh `dc9c566b`, task `HAIFA-S01-T54` (review): Backendové hlášky lokalizovat kódy/parametry nebo locale požadavku, bez globálního jazyka; zachovat API a technické detaily.: aifactory/src/aifactory/web/app.py:480 splňuje požadavek pro příručku. aifactory/web/src/views/ReposAddView.vue:58 stále přímo zobrazuje errorText; lokalizace hlášek ostatních obrazovek chybí.
- repo `haifa`, běh `dc9c566b`, task `HAIFA-S01-T54` (review): EN ani CS režim nemíchá pevné texty druhého jazyka.: aifactory/web/src/views/GuideView.vue:72 používá EN obsah, zatímco App.vue:121 ponechává český titulek a OverviewView.vue:113 český loading.
- repo `haifa`, běh `dc9c566b`, task `HAIFA-S01-T54` (review): Lokalizovat všechny uvedené obrazovky a sdílené komponenty včetně stavů, validací, přístupnosti, titulku a html lang.: aifactory/web/src/lib/i18n/catalogs.ts:2 obsahuje pouze základ a příručku; App.vue:115 má české titulky; OverviewView.vue:87 českou akci; aifactory/web/index.html:2 má lang="cs".
- repo `haifa`, běh `dc9c566b`, task `HAIFA-S01-T54` (review): Backendové hlášky lokalizovat kódy/parametry nebo locale požadavku bez globálního jazyka; zachovat API a technické detaily.: aifactory/src/aifactory/web/app.py:480 splňuje požadavek pro příručku; aifactory/web/src/views/ReposAddView.vue:58 stále zobrazuje errorText bez lokalizace. Migrace ostatních hlášek chybí.
- repo `haifa`, běh `dc9c566b`, task `HAIFA-S01-T54` (review): EN ani CS režim nemíchá pevné texty druhého jazyka.: aifactory/web/src/views/GuideView.vue:72 poskytuje anglickou příručku, zatímco App.vue:121 ponechává český titulek a OverviewView.vue:113 český loading.
- repo `haifa`, běh `f3181340`, task `HAIFA-TODO-T27` (review): Převést vlastní názvy kroků zobrazované topbarovým readiness čipem do katalogů a odvozovat je reaktivně z kódu kroku. Zachovat původní diagnostický detail serveru.
- repo `haifa`, běh `f3181340`, task `HAIFA-TODO-T27` (review): Převést stavové popisky do EN/CS katalogů.: aifactory/web/src/lib/readiness.ts:13,58 a aifactory/web/src/components/OverviewReadiness.vue:18 — vlastní názvy kroků jako „kontrola počítače“, „stav knihovny“ a „vyhodnocení“ zůstávají české a vstupují do anglického tooltipu a aria-label topbarového čipu.
- repo `haifa`, běh `f3181340`, task `HAIFA-TODO-T27` (review): Zahrnout odpovídající testy.: aifactory/tests/e2e/test_machine_check_browser.py:103–112 očekává české „kontrola počítače“ i pro EN. Chybí regrese vyžadující překlad vlastního názvu kroku při změně locale během probíhající kontroly.
- repo `haifa`, běh `f3181340`, task `HAIFA-TODO-T27` (revise): Názvy kroků readiness kontroly se překládají reaktivně podle kódu kroku; doplněny regrese přepnutí během kontroly a zachování diagnostického detailu.
- repo `haifa`, běh `b38220bb`, task `HAIFA-TODO-T28` (review): V aifactory/web/src/lib/readiness.ts uchovávejte stabilní identitu kroku a překládejte ji při vykreslení. Opravte serverový timeout, request_timeout i chybu pomocného požadavku. Přidejte regrese CS → EN → CS bez nového požadavku do aifactory/web/src/components/OverviewReadiness.test.ts.
- repo `haifa`, běh `b38220bb`, task `HAIFA-TODO-T28` (review): Varování Přehledu poskytujte jako stabilní kódy a parametry nebo lokalizujte explicitním locale požadavku. V RepoCard zobrazte lokalizovaný popis a zachovejte původní diagnostiku bez parsování vět. Doplňte EN/CS regrese do aifactory/web/src/components/overview/RepoCard.test.ts a payload testy do aifactory/tests/web/test_web_overview.py.
- repo `haifa`, běh `b38220bb`, task `HAIFA-TODO-T28` (review): Převést všechny související stavy do EN/CS.: aifactory/web/src/lib/readiness.ts:16,68,150,169,198 — timeoutIssue a auxiliaryIssue zachytí již přeložený step jako string. Po změně jazyka zůstává název kroku v původním jazyce.
- repo `haifa`, běh `b38220bb`, task `HAIFA-TODO-T28` (review): Backendové hlášky lokalizovat pomocí stabilních kódů a parametrů nebo explicitního locale požadavku.: aifactory/src/aifactory/web/overview.py:459,465,684 vytváří anglické věty pro warnings; aifactory/web/src/components/overview/RepoCard.vue:159 je přímo vypisuje v obou jazycích.
- repo `haifa`, běh `b38220bb`, task `HAIFA-TODO-T28` (revise): Doplnil jsem reaktivní překlad uložených timeoutových stavů a strukturovaná backendová varování včetně požadovaných regresí.
- repo `haifa`, běh `ba3c2f36`, task `HAIFA-TODO-T29` (review): Lokalizovat známé stavy závislostí v TaskDetail.vue:506 včetně done u kontejneru a unknown; zachovat číselný postup kontejneru a API enumy. Do aifactory/web/src/components/backlog/TaskDetail.test.ts přidat EN→CS→EN regresi těchto popisků.
- repo `haifa`, běh `ba3c2f36`, task `HAIFA-TODO-T29` (review): Lokalizovat důvody unmet v RunDialog.vue:212, například pomocí existujícího unmetLine. Do aifactory/web/src/components/backlog/RunDialog.test.ts přidat EN/CS scénář blokované závislosti ověřující přeložený důvod, zachování ID a zakázané spuštění.
- repo `haifa`, běh `ba3c2f36`, task `HAIFA-TODO-T29` (review): Doplnit stabilní kód pro UsageError při běhu obou advice managerů a zobrazit lokalizovanou chybu při zachování původní diagnostiky. Do aifactory/tests/web/test_backlog_progress_locale.py přidat UsageError větev pro workflow i task advice a do aifactory/web/src/views/BacklogLocale.test.ts odpovídající EN→CS→EN regresi.
- repo `haifa`, běh `ba3c2f36`, task `HAIFA-TODO-T29` (review): Převést stavy a detaily do EN/CS: aifactory/web/src/components/backlog/TaskDetail.vue:506 vypisuje dep.state přímo. Backend backlog.py:543–546 vrací například ready, blocked, in review nebo done, které zůstávají anglicky i v CS.
- repo `haifa`, běh `ba3c2f36`, task `HAIFA-TODO-T29` (review): Převést workflow advice a související formulářové advice do EN/CS: aifactory/src/aifactory/web/workflow_advice.py:327 vynechává error_code pro UsageError. Validační chyby z workflow/task_advice.py:67–75 proto ImproveButton.vue:114 a WorkflowAdvice.vue:113 zobrazují anglicky i v CS.
- repo `haifa`, běh `ba3c2f36`, task `HAIFA-TODO-T29` (review): Převést spouštěcí dialog do EN/CS: aifactory/web/src/components/backlog/RunDialog.vue:212 stále vypisuje u.reason přímo, například not_done nebo incomplete, místo přeloženého důvodu.
- repo `haifa`, běh `ba3c2f36`, task `HAIFA-TODO-T29` (revise): Doplněny překlady stavů závislostí, důvodů blokace spuštění a validačních chyb advice včetně požadovaných regresí EN→CS→EN.
- repo `haifa`, běh `dc9c566b`, task `HAIFA-S01-T54` (review): Lokalizovat chybové stavy v aifactory/web/src/lib/updates.ts a aifactory/web/src/components/UpdateChip.vue přes stabilní kódy/parametry a reaktivní katalogové popisky. Pokrýt runs_active, neplatnou odpověď, selhání instalace a vypršení čekání na restart. České serverové věty neparsovat; původní diagnostiku zachovat jako technické detaily.
- repo `haifa`, běh `dc9c566b`, task `HAIFA-S01-T54` (review): Doplnit EN/CS regresní testy v aifactory/web/src/lib/updates.test.ts a komponentový scénář v aifactory/web/src/components/UpdateChip.test.ts: česká serverová zpráva runs_active při EN, neplatná odpověď, restart timeout a přepnutí již zobrazené chyby bez nového požadavku. Ověřit tooltip, aria-label a dostupnost diagnostiky. Spustit nové/změněné testovací soubory a kontroly odpovídající opravě.
- repo `haifa`, běh `dc9c566b`, task `HAIFA-S01-T54` (review): Převést všechny uvedené obrazovky a sdílené komponenty včetně chyb, tooltipů, aria-label, titulku a html lang.: Obrazovky používají katalogy a App.vue:113 aktualizuje jazyk i titulky. Zbývá UpdateChip.vue:21, který připojuje nelokalizované state.error do tooltipu a aria-label; lib/updates.ts:25,69 vytváří pevné české chyby.
- repo `haifa`, běh `dc9c566b`, task `HAIFA-S01-T54` (review): Backendové hlášky lokalizovat přes stabilní kódy/parametry nebo locale požadavku, bez globálního jazyka; zachovat API a technické detaily.: Příručka používá locale požadavku v web/app.py:480. Výjimkou jsou aktualizace: web/src/lib/updates.ts:41 ukládá errorText také pro runs_active a UpdateChip.vue:21 jej přímo zobrazuje; updates.test.ts:62–71 výslovně očekává českou serverovou větu.
- repo `haifa`, běh `dc9c566b`, task `HAIFA-S01-T54` (review): Anglický ani český režim nemíchá pevné texty druhého jazyka.: aifactory/web/src/lib/updates.ts:25 obsahuje „Neplatná odpověď kontroly aktualizací.“ a řádek 69 českou instrukci při vypršení čekání. UpdateChip.vue:21–24 je připojí k anglickému popisku bez překladu.
- repo `haifa`, běh `dc9c566b`, task `HAIFA-S01-T54` (review): Ověřit úplnost katalogů a nepřeložené literály, browser EN/CS/persistence/EN, rozepsaný formulář a API chybu v obou jazycích.: i18n/i18n.test.ts ověřuje katalogy a test_locale_browser.py integrační scénáře. Chybí však EN/CS regrese aktualizačního indikátoru pro runs_active, neplatnou odpověď a restart timeout; updates.test.ts:71 stále očekává českou zprávu nezávisle na locale.
- repo `haifa`, běh `dc9c566b`, task `HAIFA-S01-T54` (revise): Opraveny zbývající nelokalizované chyby indikátoru aktualizací a doplněny EN/CS regresní scénáře.

<!-- factory: lesson=L2fb3a849 -->

## Běhy
<!-- doplňuje GIRO při schválení PR -->
