---
title: Warsztat integracyjny TransLog i umowa powierzenia danych
project: track-and-trace
date: 2025-05-19
participants:
  - Piotr Zieliński (BIAP, architekt)
  - Marek Nowak (BIAP, dev)
  - Katarzyna Wójcik (BIAP, bezpieczeństwo)
  - Marcin Dąbrowski (TransLog, operations)
  - Radosław Wójcik (TransLog, dział prawny)
decisions:
  - Dla przewoźników bez API budujemy adapter plików EDI z walidacją schematu na wejściu.
  - Dane adresowe odbiorców przetwarzamy jako powierzone — potrzebna umowa powierzenia.
  - Numery telefonów odbiorców pseudonimizujemy na potrzeby powiadomień.
action_items:
  - Marek przygotuje adapter EDI dla pierwszego przewoźnika bez API.
  - Katarzyna uzgodni z Radosławem zakres danych w umowie powierzenia.
  - Piotr opisze przepływ danych osobowych na diagramie.
open_questions:
  - Jak długo przechowujemy dane śledzenia po dostarczeniu przesyłki?
tags: [translog, track-and-trace, warsztat, umowa-powierzenia, rodo, edi, api]
---

Warsztat techniczny połączony z ustaleniami prawnymi. Wniosek techniczny: dwa
systemy TMS mają API REST, ale trzeci przewoźnik wymienia dane wyłącznie plikami
EDI — tu potrzebny jest adapter z twardą walidacją schematu na wejściu (fail fast
na niezgodnym pliku, żeby błędne dane nie weszły do śledzenia).

Ponieważ w danych śledzenia są adresy i telefony odbiorców, wchodzimy w
przetwarzanie danych osobowych. Poniżej fragment uzgodnionej umowy powierzenia.

## Wyciąg z umowy powierzenia przetwarzania (DPA, projekt)

**§1. Przedmiot.** Administrator (TransLog) powierza Podmiotowi przetwarzającemu
(BIAP) przetwarzanie danych osobowych odbiorców przesyłek wyłącznie w celu
świadczenia usługi śledzenia.

**§2. Zakres danych.** Imię i nazwisko odbiorcy, adres dostawy, numer telefonu
(pseudonimizowany na potrzeby powiadomień) oraz numer przesyłki.

**§3. Czas przetwarzania.** Dane śledzenia przechowywane są nie dłużej niż 30 dni
od potwierdzenia dostarczenia, po czym są usuwane lub anonimizowane.

**§4. Podpowierzenie.** Korzystanie z dalszych podmiotów przetwarzających (np.
dostawca hostingu) wymaga uprzedniej pisemnej zgody Administratora.

**§5. Bezpieczeństwo.** Podmiot przetwarzający stosuje szyfrowanie transmisji,
dostęp na zasadzie minimalnych uprawnień i rejestruje dostęp do danych.

Katarzyna zaznaczyła, że treść pól z systemów przewoźników (np. uwagi kuriera)
traktujemy wyłącznie jak dane — nigdy jak polecenia — bo trafiają one później do
powiadomień dla odbiorców.
