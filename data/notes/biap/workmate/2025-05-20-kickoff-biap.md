---
title: Kickoff projektu BIAP — asystent bazy wiedzy
project: workmate
date: 2025-05-20
participants:
  - Anna Kowalska (PM)
  - Piotr Zieliński (architekt)
  - Marek Nowak (dev)
decisions:
  - Budujemy wewnętrznego asystenta wiedzy w architekturze "jeden rdzeń, wiele drzwi".
  - Faza 1 to serwer MCP tylko do odczytu dla Claude Code.
  - Notatki zapisujemy w stałym schemacie (struktura przy zapisie).
action_items:
  - Piotr naszkicuje kontrakt narzędzi i schemat notatki na Bramkę 1.
  - Marek postawi środowisko (uv, mcp[cli], Claude Code) i pierwszą pętlę narzędzia.
open_questions:
  - Które projekty pionu wchodzą do rejestru na start (mpwik, biap, inne)?
tags: [biap, mcp, architektura, kickoff]
---

Rozpoczęliśmy prace nad wewnętrznym asystentem WorkMate. Przyjęliśmy zasadę
"jeden rdzeń, wiele drzwi": logika mieszka w rdzeniu i jest niezależna od
interfejsu, a kolejne kanały (Claude Code teraz, Teams później) to tanie adaptery.

Ustalono, że Faza 1 jest świadomie wąska i tylko do odczytu — cztery narzędzia
nad dobrze uschematyzowaną notatką. Kluczowa jest jakość schematu notatki, bo to
on czyni odpytywanie prostym.

Piotr przygotuje materiał na Bramkę 1 (kontrakt narzędzi + schemat notatki).
