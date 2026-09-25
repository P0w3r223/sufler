"""Adaptery — "drzwi" i implementacje portów.

- ``inbound/``  — drzwi, którymi wchodzi zapytanie (Faza 1: ``mcp``;
  Faza 2: ``teams``/``teams_graph``/``cli``; Faza 3: ``github`` — polling PAT).
- ``outbound/`` — implementacje portów rdzenia (magazyny danych, klienci HTTP).

Adaptery importują z ``sufler.core``; rdzeń nigdy nie importuje stąd.
"""
