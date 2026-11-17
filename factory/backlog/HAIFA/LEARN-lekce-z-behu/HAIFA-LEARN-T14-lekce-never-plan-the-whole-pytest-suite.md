---
id: HAIFA-LEARN-T14
title: "Lekce: Never plan the whole pytest suite (`just test`, `just check`) in one factory test…"
status: todo
workflow: build-review
depends_on: []
related: [HAIFA-REFINEMENT-T09, HAIFA-REFINEMENT-T10, HAIFA-S10-T03, HAIFA-S10-T04, HAIFA-REFINEMENT-T69, HAIFA-REFINEMENT-T75]
writes: [.factory/prompts/tester/]
---

## Zadání
Zapiš do promptu role tester (`.factory/prompts/tester/`) pravidlo z lekce `Ld90d08d0`, kterou factory našla ve výtkách opakovaných v bězích:

> Never plan the whole pytest suite (`just test`, `just check`) in one factory test step: it needs far more than the shared 600 s budget, so select scoped tests of the changed modules and their callers from the start.

Pravidlo formuluj stručně, zařaď ho tam, kam v souboru patří, a nenech ho odporovat tomu, co tam už je. Jiné soubory neměň.

Hotovo znamená:
- Pravidlo je v promptu role tester (`.factory/prompts/tester/`).

### Důkazy

Výtky z 7 běhů, ze kterých lekce vznikla:

- repo `haifa`, běh `41006510`, task `HAIFA-REFINEMENT-T09` (test_triage): [plan] The change edits the text of the builder seed prompt and one test file. No Python source or frontend code changed. All tests in tests/library that install, update, reseed and compare the seed run, plus the config tests that read the seed roster (308 tests). These are the only tests that read the builder prompt text or its version. Static checks run on the one changed Python file. The whole suite (2553 pytest items plus vitest) cannot fit in the test step's shared 600s limit, so it is left to the operator's whole-suite run.
- repo `haifa`, běh `4edbeb85`, task `HAIFA-REFINEMENT-T10` (test_triage): [plan] All checks share one 600s limit. `just test` runs vitest (about 100s) and then 2486 pytest tests, which reached only 34% before the limit, so typecheck and lint never started. That check cannot pass on any code within this budget. The new plan keeps the frontend gate and catches leftover imports of the deleted package by collecting the whole suite. Pytest runs on the tests of every changed module and its direct callers. typecheck and lint are unchanged.
- repo `haifa`, běh `d6911784`, task `HAIFA-REFINEMENT-T09` (test_triage): [plan] `just test` cannot finish inside the workflow's shared 600s limit on any code. Vitest took about 90s, and pytest reached only 34% of 2486 tests before it was killed, so typecheck and lint never started. The change is one markdown seed prompt and test_library_seed.py. The tests that read the seed are tests/library plus 5 files outside it (config seed_tester and cli, check machine, harness check cli, web library), so these 386 tests cover every consumer of the changed prompt. The frontend is untouched.
- repo `haifa`, běh `53703a25`, task `HAIFA-S10-T03` (test_triage): [plan] The change touches shared configuration read across the whole codebase, so coverage stays full. The previous single pytest check was killed by the ~449 s per-check cap at 35% (exit 124) and never printed which tests failed. The chunks below cover exactly the same test files once each, and `just e2e` runs tests/e2e serially the way the repo and CI do.
- repo `haifa`, běh `53703a25`, task `HAIFA-S10-T03` (test_triage): [plan] Every check shares one 600 s budget, and the full suite needs about 1300 s. In the last run, library took 225 s, web was cut off, and run, validation and e2e never started. A full plan cannot finish on any code. These checks cover the tests of every changed module and their direct callers in an estimated 500 s, including the test_library_seed test that found the real fault.
- repo `haifa`, běh `53b4adeb`, task `HAIFA-S10-T04` (test_triage): [plan] The plan was at fault, not the code. The whole pytest suite hit the shared 600 s limit at 56% with every test passing so far, and e2e never started. Lint, typecheck and web-test passed. The scoped selection is about 550 tests, roughly 190 s at the rate observed, which fits the budget. It still covers every changed module and its direct callers, including the loader, the inheritance code, the trace DB record of the library commit, the CLI, the check rules, the web API and the move action.
- repo `haifa`, běh `53b4adeb`, task `HAIFA-S10-T04` (test_triage): [plan] Triage: the plan was at fault. The whole suite timed out at 72% after 473 s with no failure or error. It needs about 650 s on this 4-CPU machine, so together with the roughly 150 s of static checks it cannot fit the shared 600 s limit on any code. This selection keeps every caller the reviewer's B1 finding names (tests/run, tests/review, tests/workflow) and every changed module. At the observed 3.7 tests/s it runs in about 340 s, and the whole step in about 530 s.
- repo `haifa`, běh `535e4acc`, task `HAIFA-REFINEMENT-T69` (test_triage): [plan] Both failed checks timed out without reporting code failures. Preserve full just typecheck with a larger allowance and replace the timed-out 643-case run package with complete task-run and retry files covering both production calls to the changed wrapper. Retain all three successful checks unchanged. Remaining run-package tests stay with CI; timeouts total 900 seconds.
- repo `haifa`, běh `d9d82e0b`, task `HAIFA-REFINEMENT-T75` (test_triage): [plan] The full failed log shows timeout exit 124 without a failing assertion. Two complementary shards preserve every frontend test. The retained frontend build performs the frozen dependency install and Vue typecheck used by web-test. All other checks remain unchanged; timeouts total 870 seconds.

<!-- factory: lesson=Ld90d08d0 -->

## Běhy
<!-- doplňuje GIRO při schválení PR -->
