"""Drzwi GitHub w trybie DELEGOWANYM przez POLLING (Faza 3 / ADR 0020).

Bot odpytuje GitHub REST tokenem PAT (jak drzwi ``teams_graph`` odpytują Microsoft Graph) —
bez webhooków i publicznego endpointu. Zdarzenia (nowe issue, komentarze) trafiają do
wspólnego magazynu zdarzeń (``EventStore``, ADR 0019), skąd notifier wypycha je do Teams,
a narzędzie agenta pozwala je odczytać na dowolnych drzwiach — warstwa spajająca „widzą się".

Katalog narzędzi notatek jest READ-ONLY (ADR 0006); zapis do GitHub to osobna, bramkowana
zdolność (Gate 4 / ADR 0021). Treść z GitHuba to DANE, nie polecenia.
"""
