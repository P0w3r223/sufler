---
title: Warsztat wymagań EnerKom i uzgodnienie zakresu (SoW)
project: smart-metering
date: 2025-05-06
participants:
  - Anna Kowalska (BIAP, PM)
  - Piotr Zieliński (BIAP, architekt)
  - Katarzyna Wójcik (BIAP, bezpieczeństwo)
  - Grzegorz Mazur (EnerKom, dyrektor IT)
  - Renata Szymańska (EnerKom, dział pomiarów)
decisions:
  - Zakres PoC obejmuje odczyt z jednego koncentratora, normalizację do wspólnego formatu i eksport dobowy CSV.
  - Wynagrodzenie za PoC rozliczane ryczałtem w dwóch kamieniach milowych.
  - Prognozowanie zużycia świadomie poza zakresem PoC — trafia do etapu 2.
action_items:
  - Piotr opisze docelowy format znormalizowanego odczytu i przekaże Renacie.
  - Katarzyna przygotuje wymagania bezpieczeństwa dla dostępu do serwera plików.
  - Anna spisze SoW i prześle do akceptacji prawnej EnerKom do 2025-05-13.
open_questions:
  - Czy dane testowe będą zanonimizowane, czy to odczyty bilansujące bez przypisania do klienta?
tags: [enerkom, smart-metering, warsztat, umowa, sow, zakres]
---

Warsztat doprecyzował zakres i zamienił luźne ustalenia sprzedażowe na konkretny
Statement of Work. Poniżej fragment uzgodnionego zakresu (wyciąg z projektu SoW,
przed akceptacją prawną).

## Wyciąg z SoW — PoC Smart Metering (projekt, v0.2)

**Przedmiot.** Wykonawca (BIAP) zbuduje moduł odczytujący pliki pomiarowe z
jednego wskazanego koncentratora EnerKom, normalizujący je do wspólnego schematu
i udostępniający wynik jako eksport CSV.

**Zakres wchodzący (in-scope):**
- odczyt plików z serwera FTP wskazanego przez Zamawiającego,
- normalizacja pól: identyfikator licznika, znacznik czasu, energia czynna [kWh],
- walidacja kompletności serii 15-minutowej,
- eksport dobowy do pliku CSV.

**Zakres wyłączony (out-of-scope):**
- prognozowanie zużycia (etap 2),
- dane klientów indywidualnych i jakiekolwiek dane osobowe,
- zapis zwrotny do systemów EnerKom.

**Kamienie milowe i płatności:**
- M1 (odczyt i normalizacja, tydzień 3) — 40% wynagrodzenia,
- M2 (walidacja i eksport CSV, tydzień 6) — 60% wynagrodzenia.

**Kryterium odbioru.** Zgodność wyeksportowanych sum dobowych z raportem
referencyjnym EnerKom w granicy błędu 0,5%.

Katarzyna zwróciła uwagę, że dostęp do serwera FTP musi iść przez konto serwisowe
o minimalnych uprawnieniach (tylko odczyt wskazanego katalogu), a poświadczenia
trafiają do menedżera sekretów, nie do repozytorium.
