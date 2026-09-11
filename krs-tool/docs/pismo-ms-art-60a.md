# Draft letter to the Ministry of Justice on the scope of art. 60a

Date: 2026-09-11
Status: draft — **secondary to `wniosek-ponowne-wykorzystywanie.md`**; send that first
Author: P0w3r223
Related to: `wniosek-ponowne-wykorzystywanie.md`, `adr/0001_zakres_etapu_1_i_granica_offline.md` decision 2, `../../ceidg-tool/docs/research/krs-data-access-and-legal.md`

> **Read this first.** The access-route research finished after this draft was written and changed
> the order of operations. A request for a position has no statutory deadline and no remedy; the
> application for re-use of public sector information has both — 14 days, two months at the outside,
> an appeal to the minister responsible for digital affairs and then an administrative court. That
> application is now the primary document, and this letter is the clarification that follows it.
>
> Question two below — what a private entity is supposed to do under the unfavourable reading — has
> in the meantime been answered by the research rather than by the Ministry: through art. 4b of the
> KRS act and the re-use procedure. Consider rephrasing it as a request to confirm that reading,
> rather than as an open question.

---

The letter itself is in Polish, because its addressee is a Polish authority. Everything outside the
letter — this header and the notes at the end — follows the project convention.

**Fill in before sending:** sender's name and address, company details, date, and the exact
organisational unit. The unit is left blank deliberately: the reconnaissance did not confirm which
department handles this, and guessing an addressee is the same class of error this project avoids
everywhere else.

---

## Treść pisma

> [miejscowość], [data]
>
> **[Nazwa wnioskodawcy]**
> [adres]
> [NIP / KRS, jeżeli dotyczy]
> [adres do doręczeń elektronicznych]
>
> **Ministerstwo Sprawiedliwości**
> [właściwa komórka organizacyjna]
> Al. Ujazdowskie 11
> 00-950 Warszawa
>
> **Dotyczy: prośby o zajęcie stanowiska w sprawie zakresu przedmiotowego art. 60a ustawy
> o Krajowym Rejestrze Sądowym w odniesieniu do ogólnodostępnego interfejsu
> `api-krs.ms.gov.pl`**
>
> Szanowni Państwo,
>
> zwracam się z prośbą o zajęcie stanowiska w sprawie, która ma bezpośrednie znaczenie dla
> zgodności z prawem planowanego przeze mnie przedsięwzięcia, a której nie udało mi się
> rozstrzygnąć ani na podstawie tekstu ustawy, ani dostępnych materiałów legislacyjnych.
>
> **Stan faktyczny.** Zamierzam wytworzyć narzędzie informatyczne, które na podstawie danych
> jawnych z Krajowego Rejestru Sądowego sporządza raport o sytuacji podmiotu wpisanego do
> rejestru przedsiębiorców. Narzędzie miałoby pobierać odpisy w postaci ustrukturyzowanej za
> pośrednictwem interfejsu `api-krs.ms.gov.pl`, udostępnionego przez Ministerstwo
> Sprawiedliwości i figurującego w portalu `dane.gov.pl` jako zbiór danych otwartych na
> licencji CC0 1.0, z adnotacją, że korzystanie z niego nie wymaga tokenu i nie podlega
> limitom pobrań. Jestem podmiotem prywatnym, prowadzącym działalność gospodarczą; nie jestem
> podmiotem publicznym ani nie realizuję zadań publicznych na podstawie odrębnych przepisów.
>
> **Wątpliwość.** Ustawa z dnia 26 września 2025 r. o zmianie ustawy o Krajowym Rejestrze
> Sądowym (Dz.U. 2025 poz. 1556), obowiązująca od 29 listopada 2025 r., dodała art. 60a
> w brzmieniu: „Kto bez uprawnienia uzyskuje z Rejestru informację za pośrednictwem usług
> sieciowych, podlega grzywnie, karze ograniczenia wolności albo pozbawienia wolności do lat
> 2". Ustawa nie zawiera definicji legalnej pojęcia „usługi sieciowe" i nie przewiduje
> delegacji do wydania aktu wykonawczego, który mógłby je doprecyzować.
>
> **Pytanie pierwsze — zasadnicze.** Czy korzystanie z ogólnodostępnego interfejsu
> `api-krs.ms.gov.pl` przez podmiot prywatny, w celu ponownego wykorzystywania informacji
> sektora publicznego, stanowi „uzyskiwanie z Rejestru informacji za pośrednictwem usług
> sieciowych" w rozumieniu art. 60a — a jeżeli tak, to czy odbywa się „bez uprawnienia"?
>
> Za odpowiedzią przeczącą przemawiają, moim zdaniem, następujące okoliczności:
>
> 1. Ustawa nowelizująca **nie zmieniła art. 4b ustawy o KRS**, zgodnie z którym dane
>    zgromadzone w Rejestrze udostępnia się w celu ponownego wykorzystywania „także za
>    pośrednictwem interfejsu programistycznego aplikacji (API)", z zachowaniem przepisów
>    ustawy z dnia 11 sierpnia 2021 r. o otwartych danych i ponownym wykorzystywaniu
>    informacji sektora publicznego. Uprawnienie do korzystania z tego kanału zdaje się więc
>    wynikać wprost z ustawy, co wyłączałoby znamię „bez uprawnienia".
> 2. Ustawodawca posługuje się **dwoma różnymi zwrotami**: „interfejs programistyczny aplikacji
>    (API)" w art. 4b ust. 2 oraz „usługi sieciowe" w art. 4 ust. 4c i następnych oraz
>    w art. 60a. Zwrot drugi został wprowadzony dla kanału reglamentowanego, dostępnego po
>    decyzji Ministra Sprawiedliwości.
> 3. Interfejs `api-krs.ms.gov.pl` jest nadal publicznie dostępny i odpowiada bez żadnego
>    uwierzytelnienia, a odpowiadające mu metadane w portalu danych otwartych nadal deklarują
>    licencję CC0 i brak limitów.
> 4. Krąg wnioskodawców uprawnionych do ubiegania się o zgodę z art. 4 ust. 4d jest zamknięty
>    i nie obejmuje podmiotów prywatnych nierealizujących zadań publicznych. Przyjęcie
>    wykładni przeciwnej prowadziłoby do wniosku, że podmiot taki nie ma **żadnej** drogi
>    legalnego dostępu maszynowego do danych, które ustawa nakazuje udostępniać bezpłatnie
>    i powszechnie.
>
> **Pytanie drugie — warunkowe.** Jeżeli Ministerstwo stoi na stanowisku, że art. 60a obejmuje
> również ogólnodostępny interfejs, uprzejmie proszę o wskazanie, w jakim trybie podmiot
> prywatny miałby uzyskać wymagane uprawnienie, oraz czy i w jaki sposób zamierzają Państwo
> poinformować o tym dotychczasowych użytkowników interfejsu.
>
> **Pytanie trzecie — o dokumenty finansowe.** Rozporządzenie wykonawcze Komisji (UE) 2023/138
> ustanawiające wykaz zbiorów danych o wysokiej wartości wymienia w kategorii dotyczącej spółek
> i własności spółek między innymi **sprawozdania finansowe i sprawozdania z działalności**,
> przewidując ich udostępnianie bezpłatnie, za pośrednictwem interfejsu programistycznego
> aplikacji oraz w formie pobrania zbiorczego, na licencji CC BY 4.0 lub mniej restrykcyjnej.
> Dokumenty finansowe z Repozytorium Dokumentów Finansowych są dziś udostępniane wyłącznie
> przez przeglądarkę internetową, a w portalu `dane.gov.pl` nie figuruje odpowiadający im
> zbiór. Uprzejmie proszę o informację, czy i w jakim terminie planowane jest udostępnienie
> tych dokumentów w sposób przewidziany powołanym rozporządzeniem.
>
> Zaznaczam, że do czasu otrzymania odpowiedzi **wstrzymałem się z jakimkolwiek automatycznym
> odpytywaniem interfejsu**, a budowane narzędzie działa wyłącznie na plikach zapisywanych
> ręcznie. Proszę potraktować to jako wyraz dobrej wiary, a nie jako przyznanie, że dotychczasowe
> korzystanie z interfejsu było nieuprawnione.
>
> Będę wdzięczny za odpowiedź w formie pisemnej albo na wskazany adres do doręczeń
> elektronicznych.
>
> Z wyrazami szacunku,
>
> [podpis]

---

## Notes for the sender

**Why this framing.** The letter asks for a position, not for a formal interpretation, because no
procedure for binding interpretation of this provision was found. It also deliberately asks the
second question — what a private entity is supposed to do under the unfavourable reading — because
the answer either opens a route or documents that none exists. Both outcomes are useful; only
silence is not.

**Why mention the standstill.** Stating that automated querying is on hold is worth more than it
costs: it is true, it demonstrates good faith, and the sentence that follows it prevents the
statement from being read as an admission. Do not remove one without the other.

**What the answer changes on our side.** A favourable position flips one configuration value and the
network adapter goes live. An unfavourable one leaves the product on operator-supplied files, or
moves it to a commercial intermediary who carries the risk. Either way the code already exists —
that is what ADR-0001 decision 3 and the port in `odpis/zrodlo.py` bought.

**Still to confirm before sending:** the correct organisational unit, and whether the request should
be lodged through a specific channel rather than as an ordinary letter. That is the subject of the
access-route research running alongside this draft.
