"""Drzwi Telegram (Faza 2, spike echo) — adapter wejściowy nad tym samym szwem co Teams.

Bot echo potwierdzający odbiór wiadomości, w trybie LONG POLLING (bez webhooka,
bez publicznego endpointu, bez tunelu). Kod SDK (python-telegram-bot) jest
importowany LENIWIE, więc sam import pakietu i testy jednostkowe handlera działają
bez zainstalowanego extra ``telegram``. Reużywa wspólny ``Responder``
(`adapters/inbound/responder.py`) — ten sam interfejs co drzwi Teams.
"""
