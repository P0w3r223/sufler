---
title: Schemat notatki i kontrakt narzędzi (Bramka 1)
project: workmate
date: 2025-06-10
participants:
  - Anna Kowalska (PM)
  - Piotr Zieliński (architekt)
  - Marek Nowak (dev)
  - Katarzyna Wójcik (bezpieczeństwo)
decisions:
  - Zatwierdzono schemat notatki - pola - projekt, data, uczestnicy, decyzje, action items, otwarte pytania.
  - Zatwierdzono nazwy narzędzi - search_notes, get_note, list_projects, get_project_status.
  - Notatki jako pliki Markdown z frontmatter YAML; wyszukiwanie po metadanych.
action_items:
  - Marek zaimplementuje loader notatek i walidację schematu.
  - Piotr udokumentuje schemat notatki w docs/reference.
open_questions:
  - Czy action items wymagają struktury (właściciel, termin), czy wystarczy tekst?
tags: [bramka-1, schemat, kontrakt-narzedzi, notatki]
---

To była kluczowa sesja decyzyjna — Bramka 1. Zablokowaliśmy kontrakt narzędzi
oraz stały schemat notatki. Po tym kroku reszta prac to już rozbudowa, a nie
zmiana fundamentów.

Zdecydowano o formacie Markdown z frontmatter YAML: czytelnym dla człowieka i
łatwym do walidacji. Na start action items pozostają zwykłym tekstem; ich
strukturyzacja (właściciel, termin) trafia do backlogu jako możliwe rozszerzenie.

Wyszukiwanie w Fazie 1 działa po metadanych i treści — bez RAG-a, który
zaplanowano dopiero na później.
