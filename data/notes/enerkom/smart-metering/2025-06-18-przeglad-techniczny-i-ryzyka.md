---
title: Przegląd techniczny PoC i rejestr ryzyk EnerKom
project: smart-metering
date: 2025-06-18
participants:
  - Anna Kowalska (BIAP, PM)
  - Piotr Zieliński (BIAP, architekt)
  - Marek Nowak (BIAP, dev)
  - Renata Szymańska (EnerKom, dział pomiarów)
decisions:
  - Utrzymujemy odbiór M2 na koniec tygodnia 6, mimo luk w danych z koncentratora.
  - Braki w serii 15-minutowej oznaczamy flagą jakości zamiast interpolować wartości.
action_items:
  - Marek dokończy walidację kompletności serii i raport braków.
  - Renata sprawdzi okno serwisowe koncentratora, w którym gubione są odczyty.
open_questions:
  - Kto po stronie EnerKom akceptuje raport jakości danych przy odbiorze M2?
tags: [enerkom, smart-metering, architektura, ryzyka, jakosc-danych]
---

Przegląd potwierdził, że odczyt i normalizacja działają, ale ujawnił ryzyko po
stronie źródła: koncentrator gubi część odczytów w oknie serwisowym, więc seria
15-minutowa bywa niekompletna.

Zdecydowaliśmy nie interpolować brakujących wartości — zamiast tego oznaczamy je
flagą jakości, żeby nie wprowadzać danych, których nie ma. To świadomy wybór:
lepiej pokazać lukę niż zmyśloną liczbę na potrzeby bilansowania.

## Rejestr ryzyk (wyciąg)

| Ryzyko | Wpływ | Prawdopodobieństwo | Reakcja |
| --- | --- | --- | --- |
| Braki odczytów w oknie serwisowym | Średni | Wysokie | Flaga jakości i raport braków |
| Zmiana formatu plików przez dostawcę | Wysoki | Niskie | Walidacja schematu na wejściu, fail fast |
| Brak właściciela akceptacji jakości | Średni | Średnie | Ustalić osobę odbioru M2 |

Zdrowie PoC oceniamy na żółte: technicznie jesteśmy na czas, ale odbiór zależy od
zaakceptowania raportu jakości danych po stronie EnerKom.
