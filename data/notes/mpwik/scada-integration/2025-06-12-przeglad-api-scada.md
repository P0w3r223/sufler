---
title: Przegląd kontraktu API i dostępu do SCADA
project: scada-integration
date: 2025-06-12
participants:
  - Anna Kowalska (PM)
  - Marek Nowak (dev)
  - Katarzyna Wójcik (bezpieczeństwo)
  - przedstawiciel MPWiK (IT)
decisions:
  - Zatwierdzono kontrakt API v1 — trzy endpointy odczytowe (pomiary, stany, alarmy).
  - Klucze API trzymamy w menedżerze sekretów, nie w repozytorium.
  - Środowisko testowe MPWiK będzie zasilone danymi zanonimizowanymi.
action_items:
  - Marek wdroży walidację wejścia na granicy API do 2025-06-20.
  - Katarzyna przygotuje checklistę przeglądu bezpieczeństwa przed produkcją.
open_questions:
  - Czy alarmy krytyczne mają być udostępniane w czasie zbliżonym do rzeczywistego?
tags: [api, scada, bezpieczenstwo, sekrety]
---

Domknęliśmy kontrakt API pierwszej wersji. Zgodnie z ustaleniami z kickoffu
endpointy są wąskie i typowane — świadomie rezygnujemy z generycznego "czytaj
dowolne dane" na rzecz trzech konkretnych zasobów.

Rozstrzygnięto kwestię sekretów: klucze API idą do menedżera sekretów, a nie do
folderu indeksowanego przez usługę. Katarzyna podniosła ryzyko traktowania
treści alarmów jako poleceń — ustalono, że pobrane dane traktujemy wyłącznie
jak dane.

Środowisko testowe MPWiK zostanie zasilone danymi zanonimizowanymi, co odblokowuje
prace bez dostępu do produkcji.
