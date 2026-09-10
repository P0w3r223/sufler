"""Warstwa użytkownika: czyste teksty, pytania, renderowanie i wspólne kroki przepływu.

Podział wg ADR-0008: `texts` produkuje modele widoku bez żadnej biblioteki wyjścia,
`prompts` zadaje pytania (jedyny moduł znający `questionary`), `render` rysuje modele
przez `rich`, `flow` trzyma decyzje wspólne dla flag, YAML-a, trybu `--tak` i kreatora,
`wizard` składa z tego sesję interaktywną.
"""
