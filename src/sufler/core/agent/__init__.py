"""Runtime agenta rdzenia (Faza 2, kamień M1 / ADR 0008).

Model, który dostaje zapytanie w języku naturalnym, w ograniczonej pętli decyduje
które z narzędzi Fazy 1 wywołać i składa odpowiedź. Zależy tylko od portu
``LLMClient`` i katalogu narzędzi — bez importów SDK, testowalny na atrapie LLM.
"""
