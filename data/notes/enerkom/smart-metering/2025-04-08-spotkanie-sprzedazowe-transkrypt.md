---
title: Spotkanie sprzedażowe EnerKom — rozpoznanie potrzeb (transkrypt)
project: smart-metering
date: 2025-04-08
participants:
  - Tomasz Lewandowski (BIAP, sprzedaż)
  - Anna Kowalska (BIAP, PM)
  - Grzegorz Mazur (EnerKom, dyrektor IT)
  - Renata Szymańska (EnerKom, dział pomiarów)
decisions:
  - Rozpoczynamy od bezpłatnego PoC odczytu z jednego koncentratora, bez zobowiązania produkcyjnego.
  - Zakres pilotażu ograniczamy do liczników bilansujących, bez danych klientów indywidualnych.
action_items:
  - Tomasz prześle wstępną ofertę i szkic zakresu PoC do 2025-04-15.
  - Renata wskaże koncentrator testowy i format plików odczytowych.
  - Anna zaproponuje termin warsztatu wymagań.
open_questions:
  - Czy odczyty da się pobierać przez API, czy tylko jako pliki z serwera FTP?
  - Jaka jest częstotliwość raportowania liczników — co 15 minut czy co godzinę?
tags: [enerkom, smart-metering, sprzedaz, transkrypt, poc]
---

Fragment transkryptu rozmowy sprzedażowej, spisany z nagrania na potrzeby zespołu.

[00:02] Tomasz Lewandowski (BIAP): Dziękujemy za spotkanie. Chcielibyśmy najpierw
zrozumieć, jak dziś wygląda u Państwa odczyt danych z liczników, zanim cokolwiek
zaproponujemy.

[00:03] Grzegorz Mazur (EnerKom): Mamy kilkanaście tysięcy liczników i sieć
koncentratorów. Dane spływają do systemu pomiarowego, ale raportowanie robimy
ręcznie w arkuszu. To zajmuje jeden dzień w tygodniu.

[00:05] Renata Szymańska (EnerKom): Problem jest taki, że koncentratory oddają
pliki w różnych formatach, zależnie od dostawcy. Nie mamy jednego API.

[00:06] Anna Kowalska (BIAP): Czyli realny cel to jedno miejsce, w którym te
odczyty są znormalizowane i dostępne od razu, bez ręcznej obróbki?

[00:07] Grzegorz Mazur (EnerKom): Dokładnie. A docelowo prognoza zużycia, żeby
lepiej planować bilansowanie.

[00:09] Tomasz Lewandowski (BIAP): Proponuję zacząć od małego PoC — jeden
koncentrator, odczyt i normalizacja. Bez ruszania danych klientów indywidualnych,
żeby nie wchodzić od razu w RODO.

[00:10] Renata Szymańska (EnerKom): To brzmi rozsądnie. Wskażę koncentrator, na
którym możemy testować bez ryzyka.

Wniosek: potrzeba jest realna (ręczne raportowanie to jeden dzień w tygodniu), a
próg wejścia niski — PoC na jednym koncentratorze. Prognozowanie zużycia to faza
druga, nie MVP.
