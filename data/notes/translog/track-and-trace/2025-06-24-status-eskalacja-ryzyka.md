---
title: Status integracji TransLog i eskalacja ryzyka terminu
project: track-and-trace
date: 2025-06-24
participants:
  - Anna Kowalska (BIAP, PM)
  - Marek Nowak (BIAP, dev)
  - Marcin Dąbrowski (TransLog, operations)
  - Ewa Jankowska (TransLog, zakupy)
decisions:
  - Eskalujemy ryzyko terminu — dwaj podwykonawcy nie dostarczyli dostępu do TMS.
  - Do czasu udostępnienia API śledzimy tylko przewoźników z gotową integracją.
  - Termin pełnego uruchomienia przesuwamy warunkowo o 3 tygodnie.
action_items:
  - Marcin wymusi u dwóch podwykonawców dostęp testowy do TMS do 2025-07-04.
  - Anna przygotuje zaktualizowany harmonogram z terminem warunkowym.
  - Marek uruchomi śledzenie dla przewoźników już zintegrowanych.
open_questions:
  - Czy klient zaakceptuje uruchomienie częściowe zamiast czekać na wszystkich przewoźników?
tags: [translog, track-and-trace, status, eskalacja, ryzyko, termin]
---

Status jest napięty. Integracja bazowa działa, ale dwaj podwykonawcy TransLog nie
udostępnili jeszcze dostępu do swoich systemów TMS — a odpowiadają oni za dużą
część przesyłek. Termin handlowy jest zagrożony z przyczyn po stronie klienta i
jego podwykonawców, nie po stronie integracji.

Zdrowie projektu oceniamy na czerwone, ale z jasną ścieżką wyjścia: uruchamiamy
śledzenie częściowe dla przewoźników już zintegrowanych i przesuwamy pełne
uruchomienie warunkowo o trzy tygodnie.

## Eskalacja

- **Ryzyko:** brak dostępu do TMS u dwóch kluczowych podwykonawców.
- **Skutek:** nie da się śledzić części przesyłek; termin pełnego uruchomienia
  niewykonalny bez tego dostępu.
- **Właściciel po stronie klienta:** Marcin Dąbrowski (operations) — wymusi dostęp
  testowy do 2025-07-04.
- **Plan B:** uruchomienie częściowe, komunikowane odbiorcom jako „śledzenie
  dostępne dla wybranych przewoźników".

Ewa potwierdziła, że rozliczenie za każdy dodatkowy adapter przewoźnika pozostaje
zgodne z wcześniejszą wyceną, więc opóźnienie nie zmienia modelu kosztowego.
