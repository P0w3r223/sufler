---
title: Status prac i przegląd ryzyk
project: scada-integration
date: 2025-06-26
participants:
  - Anna Kowalska (PM)
  - Marek Nowak (dev)
  - przedstawiciel MPWiK (IT)
decisions:
  - Utrzymujemy termin wdrożenia testowego na koniec lipca 2025.
  - Alarmy krytyczne na razie poza zakresem MVP — trafiają do backlogu.
action_items:
  - Marek dokończy integrację endpointu pomiarów i pokrycie testami.
  - Anna zaktualizuje rejestr ryzyk i prześle go stronie MPWiK.
open_questions:
  - Kto po stronie MPWiK odpowiada za rotację kluczy API po wdrożeniu?
tags: [status, ryzyka, wdrozenie, testy]
---

Integracja endpointu pomiarowego jest na ukończeniu i objęta testami. Zdrowie
projektu oceniamy jako dobre, choć pojawiło się ryzyko po stronie utrzymania —
brak jasnego właściciela rotacji kluczy API po stronie MPWiK.

Zdecydowano, że obsługa alarmów krytycznych w czasie rzeczywistym zostaje
przesunięta poza MVP, aby nie zagrozić terminowi wdrożenia testowego.

Następny przegląd po pierwszym wdrożeniu na środowisku testowym.
