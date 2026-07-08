---
title: Kickoff integracji z systemami MPWiK
project: scada-integration
date: 2025-05-14
participants:
  - Anna Kowalska (PM)
  - Marek Nowak (dev)
  - przedstawiciel MPWiK (IT)
decisions:
  - Zakres MVP obejmuje odczyt danych z systemu SCADA i udostępnienie ich przez API.
  - Uwierzytelnianie po stronie MPWiK oparte o klucze API per usługa.
  - Dane osobowe nie wchodzą w zakres integracji na tym etapie.
action_items:
  - Marek przygotuje szkic kontraktu API do końca tygodnia.
  - Anna umówi warsztat bezpieczeństwa z działem IT MPWiK.
open_questions:
  - Czy MPWiK udostępni środowisko testowe, czy pracujemy na danych zanonimizowanych?
  - Jaki jest docelowy SLA dla odpytań o dane pomiarowe?
tags: [integracja, scada, api, kickoff]
---

Spotkanie otwierające współpracę przy integracji z infrastrukturą MPWiK.
Ustaliliśmy, że pierwszy etap skupia się wyłącznie na odczycie danych
pomiarowych ze SCADA — bez zapisu i bez danych osobowych.

Strona MPWiK zwróciła uwagę na wymóg pełnej rozdzielności środowisk testowego
i produkcyjnego. Zespół zaproponował kontrakt API oparty o wąskie, typowane
endpointy zamiast generycznego dostępu do bazy.

Kolejny przegląd zaplanowano za cztery tygodnie, po przygotowaniu szkicu API
i warsztacie bezpieczeństwa.
