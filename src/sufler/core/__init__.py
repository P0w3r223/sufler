"""Rdzeń Sufler — logika niezależna od interfejsu.

Reguła zależności (nośna konwencja projektu): ``core`` NIGDY nie importuje
z ``sufler.adapters``. Zależność idzie tylko w jedną stronę:
adaptery znają rdzeń, rdzeń nie zna adapterów.
"""
