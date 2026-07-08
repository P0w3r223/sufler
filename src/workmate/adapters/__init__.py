"""Adaptery — "drzwi" i implementacje portów.

- ``inbound/``  — drzwi, którymi wchodzi zapytanie (Faza 1: ``mcp``;
  Faza 2: ``teams`` — spike M2 echo).
- ``outbound/`` — implementacje portów rdzenia (magazyny danych).
- ``github/`` — zaczątek drzwi Fazy 3 (na razie stub).

Adaptery importują z ``workmate.core``; rdzeń nigdy nie importuje stąd.
"""
