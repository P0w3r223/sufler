---
title: Kickoff Omnichannel NordMarket i transkrypt ustaleń
project: omnichannel
date: 2025-03-25
participants:
  - Anna Kowalska (BIAP, PM)
  - Marek Nowak (BIAP, dev)
  - Beata Krawczyk (NordMarket, e-commerce)
  - Łukasz Adamski (NordMarket, sieć sklepów)
decisions:
  - Pierwszy cel to jedna prawda o stanie magazynowym, widoczna online i w sklepach.
  - Pilotaż uruchamiamy w 3 sklepach i sklepie internetowym, nie w całej sieci.
  - Asystent obsługi klienta wchodzi dopiero po ustabilizowaniu synchronizacji stanów.
action_items:
  - Marek zbada API systemu magazynowego i kasowego NordMarket.
  - Beata udostępni dane sprzedaży z ostatniego kwartału do kalibracji.
  - Anna przygotuje harmonogram pilotażu dla 3 sklepów.
open_questions:
  - Jak często kasy raportują sprzedaż — na bieżąco czy wsadowo na koniec dnia?
tags: [nordmarket, omnichannel, retail, kickoff, transkrypt, magazyn]
---

Fragment transkryptu spotkania otwierającego, spisany na potrzeby zespołu.

[00:04] Beata Krawczyk (NordMarket): Największy ból to rozjazd stanów. Klient widzi
w sklepie internetowym „dostępny", a w sklepie stacjonarnym produktu nie ma. Zwroty
i reklamacje idą w górę.

[00:06] Łukasz Adamski (NordMarket): Z drugiej strony w sklepie mamy towar, którego
online nie pokazujemy, bo synchronizacja idzie raz dziennie, w nocy.

[00:08] Marek Nowak (BIAP): Czyli problem to opóźnienie i brak jednego źródła prawdy
o stanie. Kasy raportują sprzedaż na bieżąco czy wsadowo?

[00:09] Łukasz Adamski (NordMarket): Wsadowo, na koniec dnia. To pewnie sedno sprawy.

[00:11] Anna Kowalska (BIAP): Zacznijmy wąsko — trzy sklepy plus online, jedna
prawda o stanie, aktualizacja w minutach, nie w dobie. Chatbota obsługi klienta
odłóżmy, aż stany będą się zgadzać.

[00:12] Beata Krawczyk (NordMarket): Zgoda. Bez zaufania do stanów chatbot i tak
będzie mówił klientom nieprawdę.

Wniosek: rdzeń problemu to wsadowa synchronizacja raz na dobę. MVP to skrócenie
pętli aktualizacji stanów w pilotażu na 3 sklepy i sklep internetowy. Asystent
klienta później.
