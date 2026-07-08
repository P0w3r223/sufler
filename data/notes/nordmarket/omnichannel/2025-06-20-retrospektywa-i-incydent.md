---
title: Retrospektywa pilotażu NordMarket i zgłoszenie incydentu
project: omnichannel
date: 2025-06-20
participants:
  - Anna Kowalska (BIAP, PM)
  - Marek Nowak (BIAP, dev)
  - Beata Krawczyk (NordMarket, e-commerce)
  - Łukasz Adamski (NordMarket, sieć sklepów)
decisions:
  - Pilotaż uznajemy za udany — synchronizacja schodzi poniżej 5 minut w godzinach pracy.
  - Przyczyną incydentu z 2025-06-14 była kolejka blokowana przez wsadowy import nocny.
  - Import nocny rozbijamy na mniejsze paczki, żeby nie blokował kolejki bieżącej.
action_items:
  - Marek rozdzieli kolejkę importu wsadowego od strumienia zmian bieżących.
  - Beata zbierze opinie kasjerów z 3 sklepów pilotażowych.
  - Anna zaktualizuje status projektu w rejestrze.
open_questions:
  - Czy przy rozszerzeniu na 20 sklepów utrzymamy 5 minut bez rozbudowy infrastruktury?
tags: [nordmarket, omnichannel, retrospektywa, incydent, pilotaz]
---

Retrospektywa po pierwszym miesiącu pilotażu. Ogólny ton pozytywny — w godzinach
pracy sklepów stany schodzą poniżej progu 5 minut z SLA, a liczba reklamacji
„towar niedostępny" spadła.

Omówiliśmy też jeden incydent.

## Zgłoszenie incydentu INC-2025-06-14

- **Kiedy:** 2025-06-14, sobota, 11:20–12:05 (45 minut).
- **Objaw:** synchronizacja stanów opóźniona do około 25 minut w godzinach szczytu.
- **Wpływ:** jeden sklep pilotażowy sprzedał 3 sztuki produktu pokazanego online
  jako dostępny, którego fizycznie już nie było.
- **Przyczyna źródłowa:** nocny import wsadowy nie zakończył się do rana i wciąż
  trzymał wspólną kolejkę, blokując strumień zmian bieżących.
- **Działanie doraźne:** ręczne wznowienie kolejki bieżącej.
- **Działanie trwałe:** rozdzielenie kolejki importu wsadowego od strumienia zmian
  bieżących (action item Marka).

Łukasz podkreślił, że kasjerzy chwalą sobie brak rozjazdu stanów, ale pytają o
podgląd dostępności w innych sklepach — to potencjalny wątek na etap 2.
