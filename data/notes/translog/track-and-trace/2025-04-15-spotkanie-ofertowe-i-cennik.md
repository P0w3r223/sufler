---
title: Spotkanie ofertowe TransLog i wstępna wycena
project: track-and-trace
date: 2025-04-15
participants:
  - Tomasz Lewandowski (BIAP, sprzedaż)
  - Anna Kowalska (BIAP, PM)
  - Marcin Dąbrowski (TransLog, operations)
  - Ewa Jankowska (TransLog, zakupy)
decisions:
  - Model rozliczenia to stały koszt integracji plus abonament miesięczny za śledzenie.
  - Wycena wstępna oparta o 2 systemy TMS przewoźników; każdy kolejny wyceniany osobno.
action_items:
  - Tomasz prześle ofertę z cennikiem i założeniami do 2025-04-18.
  - Marcin wskaże dwóch przewoźników pilotażowych i ich systemy TMS.
  - Ewa potwierdzi budżet i tryb akceptacji zakupowej.
open_questions:
  - Czy przewoźnicy podwykonawcy mają API, czy śledzenie odbywa się przez pliki EDI?
tags: [translog, track-and-trace, sprzedaz, oferta, cennik, tms]
---

Spotkanie ofertowe u operatora logistycznego TransLog. Klient chce jednego widoku
śledzenia przesyłek, mimo że korzysta z wielu przewoźników podwykonawców z różnymi
systemami TMS.

Poniżej wyciąg z przedstawionej wstępnej wyceny (założenia, nie oferta wiążąca).

## Wyciąg z wyceny wstępnej

| Pozycja | Zakres | Wycena |
| --- | --- | --- |
| Integracja bazowa | Platforma śledzenia i 2 systemy TMS przewoźników | koszt jednorazowy, ryczałt |
| Każdy kolejny przewoźnik | Adapter do jednego dodatkowego TMS lub EDI | koszt jednorazowy per przewoźnik |
| Abonament miesięczny | Utrzymanie, hosting, wsparcie w SLA | opłata miesięczna wg liczby śledzonych przesyłek |

**Założenia wyceny:**
- dwa systemy TMS w cenie bazowej mają dostępne API REST,
- śledzenie w czasie zbliżonym do rzeczywistego (aktualizacja statusu do 10 minut),
- brak migracji danych historycznych w zakresie bazowym.

Marcin zwrócił uwagę, że część mniejszych podwykonawców nie ma API i wymienia dane
plikami EDI — to najpoważniejsze założenie do potwierdzenia, bo wpływa na koszt
każdego kolejnego adaptera. Ewa poprosiła o rozbicie ceny na integrację i abonament,
żeby zmieścić się w trybie akceptacji zakupowej.
