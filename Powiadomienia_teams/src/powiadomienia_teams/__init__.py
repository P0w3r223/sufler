"""Powiadomienia_teams — cotygodniowy asystent uzupełniania zmian w Microsoft Shifts.

Patrz PLAN.md w katalogu projektu. Warstwy:
- domain/   — niemutowalny model grafiku (bez I/O),
- reminders/— czysta logika wykrywania luk i budowy propozycji,
- scheduler/— wyznaczanie kolejnego terminu (niedziela 16:00),
- graph/    — warstwa Microsoft Graph (Etap 2+),
- agent/    — interpretacja odpowiedzi NL (Etap 4).
"""
