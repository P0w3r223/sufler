"""Drzwi Jira (Server/Data Center) w trybie DELEGOWANYM przez POLLING (ADR 0030).

Bot odpytuje Jira REST v2 tokenem PAT (Bearer) — jak drzwi ``github`` odpytują GitHub REST —
bez webhooków i publicznego endpointu. Zdarzenia (nowe zgłoszenie, zmiana statusu, komentarz)
trafiają do wspólnego magazynu zdarzeń (``EventStore``, ADR 0019), skąd notifier wypycha je do
Teams, a narzędzie agenta pozwala je odczytać na dowolnych drzwiach — warstwa spajająca „widzą się".

Drzwi są READ-ONLY (jak GitHub w ADR 0020: ingest zdarzeń); zapis do Jira będzie osobno
bramkowany w kolejnym kroku. Treść z Jiry (podsumowania, komentarze) to DANE, nie polecenia.
"""
