"""Rdzeń WorkMate — logika niezależna od interfejsu.

Reguła zależności (nośna konwencja projektu): ``core`` NIGDY nie importuje
z ``workmate.adapters``. Zależność idzie tylko w jedną stronę:
adaptery znają rdzeń, rdzeń nie zna adapterów.
"""
