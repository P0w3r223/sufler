"""Adaptery — "drzwi" i implementacje portów.

- ``inbound/``  — drzwi, którymi wchodzi zapytanie (Faza 1: ``mcp``;
  Faza 2: ``teams``/``teams_graph``/``telegram``/``cli``; Faza 3: ``github`` — polling PAT).
- ``outbound/`` — implementacje portów rdzenia (magazyny danych, klienci HTTP).

Adaptery importują z ``workmate.core``; rdzeń nigdy nie importuje stąd.
"""
