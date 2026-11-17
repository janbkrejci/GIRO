---
id: HAIFA-S01-T56
title: Přepínač jazyka všude jako SVG ikona
status: todo
workflow: build-test-review
depends_on: [HAIFA-S01-T54]
writes: [aifactory/web/src/App.vue, aifactory/web/src/App.test.ts, aifactory/web/src/components/LanguageSelect.vue, aifactory/web/src/components/LanguageSelect.test.ts, aifactory/web/src/components/assistant/AssistantPanel.vue, aifactory/web/src/components/assistant/AssistantPanel.test.ts, aifactory/web/src/components/ui/SelectMenu.vue, aifactory/web/src/components/ui/SelectMenu.test.ts, aifactory/tests/e2e/test_locale_browser.py, aifactory/src/aifactory/web/static/, aifactory/tests/test_giro_home.py]
---

## Zadání
Ve sdílené komponentě aifactory/web/src/components/LanguageSelect.vue nahraď viditelný název aktuálního jazyka jedinou SVG ikonou Languages z již používaného lucide-vue-next. Ikonovou podobu používej všude, tedy v topbaru v App.vue i v panelu průvodce v components/assistant/AssistantPanel.vue. Nepřidávej volitelnou textovou variantu. Zachovej nabídku English / Čeština po otevření, označení vybraného jazyka i dosavadní ukládání volby. Ikonový ovladač má velikostí a vzhledem odpovídat sousedním ikonám topbaru, mít lokalizovaný přístupný název a tooltip.

Zachovej SelectMenu.vue jako společný dropdown včetně ovládání klávesnicí, focusu a ARIA atributů. Pokud potřebuješ upravit jeho trigger, přidej pouze minimální možnost vlastního obsahu s dosavadním zobrazením jako výchozím pro ostatní selecty. Zachovej data-test="language-select". Neměň překlady, výchozí jazyk ani lokalizační službu.

Uprav související očekávání v App.test.ts, která nyní vyžadují viditelný text Čeština. V LanguageSelect.test.ts ověř SVG, absenci viditelného názvu jazyka v zavřeném ovladači, přístupný název a výběr jazyka; při změně společného SelectMenu doplň odpovídající ověření do SelectMenu.test.ts. V AssistantPanel.test.ts ověř ikonovou podobu přepínače i zachované přepínání jazyka. V tests/e2e/test_locale_browser.py doplň kontrolu SVG a absence viditelného názvu jazyka v zavřeném přepínači v topbaru i panelu průvodce. Zachovej jeho scénáře výběru klávesnicí a persistence. Přegeneruj distribuovaný frontend pomocí just web-build. Dokumentace ani backend tuto změnu nepotřebují.

Hotovo znamená:
- Zavřený přepínač jazyka v topbaru i panelu průvodce zobrazuje SVG ikonu bez viditelného názvu jazyka a má lokalizovaný přístupný název i tooltip.
- Nabídka obsahuje English a Čeština; výběr funguje myší i klávesnicí, synchronizuje oba výskyty a přetrvá po obnovení stránky.
- Ostatní dropdowny zachovávají dosavadní vzhled a ovládání.
- Browserový test ověří oba výskyty při šířkách 1366 i 390 px bez přetékání stránky.
- Projdou just lint, just typecheck, just web-test, just web-build a just pytest tests/e2e/test_locale_browser.py -n0; každý upravený testový soubor je spuštěn.

## Běhy
<!-- doplňuje GIRO při schválení PR -->
