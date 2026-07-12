"""Drzwi Teams w trybie DELEGOWANYM (ADR 0015) — polling kanału przez Microsoft Graph.

Bot działa jako ZALOGOWANY UŻYTKOWNIK (device-code MSAL), bez publicznego endpointu i
bez rejestracji bota — operacyjnie jak drzwi Telegram (long polling). Odpowiada runtime
agenta rdzenia nad tym samym katalogiem narzędzi (READ-ONLY, ADR 0006), reużywając szwu
``Responder``. Importy ``msal``/``httpx`` są leniwe; logika decyzyjna (``selection``) i
handler są wolne od I/O i testowalne atrapami.
"""
