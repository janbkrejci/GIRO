---
id: HAIFA-REFINEMENT-T119
title: Přechodné síťové chyby při git fetch nesmí zastavit approve ani auto-continue řetěz
status: todo
depends_on: []
---

## Zadání
Úkol od Clauda z NEO_HEF, zadal Jan. Implementaci provede Jan na jiném stroji.

Pozorování z ostrého NEO_HEF (GIRO 1.0.33, Windows, Azure DevOps), 2026-11-20: DNS k `asolcz.visualstudio.com` občas na desítky sekund vypadne. `factory task approve` (a approve uvnitř auto-continue řetězu po úspěšném runu) po jediném neúspěšném `git fetch origin develop` skončí s `fetch_failed: ... Could not resolve host`, řetěz končí `stop: not_merged` a další task se nespustí. Stalo se to dnes třikrát (ROB-TODO-T150, ROB-TODO-T153, ROB-S20-T002). Ruční `factory task approve` o minutu později pokaždé prošel. Totéž se stalo i v `task retry` (fetch při startu).

Požadavek:
- Přechodné síťové chyby gitu i providera (DNS resolve, connection reset, timeout, HTTP 5xx) opakovat s backoffem, například 5 pokusů během asi 2–3 minut. Teprve po vyčerpání pokusů vrátit `fetch_failed` při fetchi.
- Platí pro approve, retry, run a merge v řetězu, včetně approve uvnitř auto-continue po úspěšném runu.
- Trvalé chyby (auth, not found) neopakovat.
- Do logu a envelope zapsat počet pokusů.

Hotovo znamená:
- Testy simulují přechodnou i trvalou chybu fetch a providera bez živých služeb. Ověří úspěch po přechodné chybě, vyčerpání limitu opakování a okamžité ukončení bez opakování při trvalé chybě.
- Testy ověří backoff a počet pokusů v logu a envelope.
- Řetěz po přechodném výpadku pokračuje dalším taskem.

## Běhy
<!-- doplňuje GIRO při schválení PR -->
