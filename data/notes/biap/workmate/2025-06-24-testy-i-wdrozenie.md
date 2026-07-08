---
title: Testy, dane realistyczne i plan wdrożenia HTTP
project: workmate
date: 2025-06-24
participants:
  - Anna Kowalska (PM)
  - Piotr Zieliński (architekt)
  - Marek Nowak (dev)
decisions:
  - Dodajemy podstawowe testy jednostkowe serwisów i loaderów notatek.
  - Przełączenie transportu na streamable-http planujemy na tydzień 4 (Bramka 3).
  - Uwierzytelnianie per osoba i uprawnienia minimalne ustalimy na Bramce 3.
action_items:
  - Marek napisze testy dla search_notes i get_project_status.
  - Anna przygotuje krótkie README onboardingowe dla zespołu.
open_questions:
  - Czy hosting stawiamy na serwerze firmowym od razu, czy najpierw stdio u kilku devów?
tags: [testy, wdrozenie, http, bramka-3, onboarding]
---

Skupiliśmy się na jakości i przygotowaniu do wyjścia poza lokalne stdio. Zespół
zgodził się, że przed wdrożeniem HTTP potrzebujemy podstawowego pokrycia testami
— zwłaszcza logiki wyszukiwania i syntezy statusu projektu.

Ustalono ścieżkę na tydzień 4: przełączenie na transport streamable-http,
wdrożenie na serwerze firmowym oraz uwierzytelnianie per osoba z uprawnieniami
minimalnymi (do domknięcia na Bramce 3).

Jako plan awaryjny (deskopowanie) rozważamy pozostawienie serwera na stdio u kilku
developerów, jeśli tydzień 4 się ześlizgnie — to wciąż działający dowód wartości.
