"""claude_summary — dzienne zestawienie pracy z historii promptów Claude Code i commitów.

Samodzielny pod-projekt uv (styl heksagonalny jak ``Powiadomienia_teams``): rdzeń
(``core/``) to czysta, testowalna logika bez I/O; adaptery (``adapters/``) czytają
transkrypty Claude Code, wołają ``git`` i (opcjonalnie) Claude API. Narzędzie produkuje
uporządkowane dane per dzień (JSON + Markdown) jako materiał dla większego agenta, który
zapisuje harmonogram/worklog w Jirze.
"""
