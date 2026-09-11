# Draft application for re-use of public sector information — the route that actually exists

Date: 2026-09-11
Status: draft — ready to read; the enumeration of art. 39 sec. 3 must be checked against the statute before sending
Author: P0w3r223
Related to: `pismo-ms-art-60a.md`, `adr/0001_zakres_etapu_1_i_granica_offline.md` decision 2

---

## Why this document exists, and why it outranks the letter

Two independent research passes established that the route we were looking for — ministerial consent
for "network services" — **is closed to a private company**, and closed by a substantive criterion
rather than a formality: the access must be necessary for the applicant's own **public tasks**, the
consent is personal, tied to the tasks named in the application, and cannot be bought, rented or
transferred. The bill's own impact assessment states that it has no effect on micro, small and
medium enterprises, and lists no row for commercial businesses.

But the amendment **added subsections and repealed nothing**. Art. 4b of the KRS act stands, and it
says in as many words that register data are made available for re-use "also through an application
programming interface (API)". Re-use, by statutory definition, means use **for any purpose,
including commercial**.

That gives a second route, and unlike the consent route it has a fully specified procedure with
deadlines and a remedy. This is the difference between "I believe I am allowed to" and "I have it in
writing" — which matters precisely because art. 60a punishes obtaining information **without
entitlement**.

The letter asking the Ministry for a position stays useful, but it is secondary: a request for a
position has no statutory deadline and no appeal. This application has both.

---

## Treść wniosku

> [miejscowość], [data]
>
> **[Nazwa wnioskodawcy]**
> [adres] · [NIP] · [KRS, jeżeli dotyczy]
> [adres do doręczeń elektronicznych]
>
> **Centralna Informacja Krajowego Rejestru Sądowego**
> Ministerstwo Sprawiedliwości
> Al. Ujazdowskie 11, 00-950 Warszawa
>
> **WNIOSEK**
> **o ponowne wykorzystywanie informacji sektora publicznego**
> **w sposób stały i bezpośredni w czasie rzeczywistym**
>
> Na podstawie art. 39 ust. 1 pkt 2 i ust. 2 ustawy z dnia 11 sierpnia 2021 r. o otwartych
> danych i ponownym wykorzystywaniu informacji sektora publicznego, w związku z art. 4b
> ust. 1 i 2 ustawy z dnia 20 sierpnia 1997 r. o Krajowym Rejestrze Sądowym, wnoszę
> o umożliwienie ponownego wykorzystywania informacji z rejestru przedsiębiorców Krajowego
> Rejestru Sądowego w sposób opisany poniżej.
>
> **1. Oznaczenie wnioskodawcy**
> [pełne dane, adres do korespondencji, adres elektroniczny]
>
> **2. Informacje, których dotyczy wniosek**
> Dane i informacje o podmiotach wpisanych do rejestru przedsiębiorców Krajowego Rejestru
> Sądowego, w zakresie odpowiadającym odpisowi aktualnemu i odpisowi pełnemu, obejmującym
> działy 1–6, wraz z nagłówkiem zawierającym stan na dzień, datę i numer ostatniego wpisu, oraz
> dzienny biuletyn zmian.
>
> **3. Cel ponownego wykorzystywania, rodzaj działalności oraz dobra, produkty i usługi**
> Cel: wytworzenie i udostępnianie narzędzia informatycznego, które na podstawie danych jawnych
> sporządza raport o sytuacji podmiotu wpisanego do rejestru przedsiębiorców — sygnalizując
> okoliczności ujawnione w rejestrze, w szczególności wpisy działu 4 i działu 6 — na potrzeby
> weryfikacji kontrahenta.
> Rodzaj działalności: działalność gospodarcza w zakresie oprogramowania i usług informatycznych.
> Produkt: raport analityczny generowany przez to narzędzie. Raport ma charakter informacyjny
> i nie stanowi decyzji ani rekomendacji.
>
> **4. Forma przygotowania i format danych**
> Postać ustrukturyzowana, format JSON, kodowanie UTF-8 — czyli format, w jakim informacje są
> już dziś przygotowywane i udostępniane przez interfejs `api-krs.ms.gov.pl`. Wniosek nie
> zmierza do wytworzenia nowych informacji ani do ich przetwarzania w sposób wykraczający poza
> proste czynności.
>
> **5. Sposób przekazania i okres dostępu**
> Wnoszę o dostęp **stały i bezpośredni w czasie rzeczywistym** za pośrednictwem interfejsu
> programistycznego aplikacji, na okres nieoznaczony, a w razie potrzeby określenia terminu —
> na okres 3 lat z możliwością przedłużenia.
> Dodatkowo wnoszę o umożliwienie **zbiorczego pobrania** danych, zgodnie z częścią 5.2
> załącznika do rozporządzenia wykonawczego Komisji (UE) 2023/138, które dla kategorii
> dotyczącej przedsiębiorstw i ich własności przewiduje udostępnianie „za pośrednictwem API
> i do zbiorczego pobrania", na poziomie poszczególnych przedsiębiorstw.
>
> **6. Uzasadnienie przesłanki z art. 39 ust. 1 pkt 2**
> Informacje, o których mowa w punkcie 2, są udostępniane w systemie teleinformatycznym
> Centralnej Informacji, natomiast **nie zostały określone warunki ponownego wykorzystywania
> ani wysokość opłat, ani też nie poinformowano o braku takich warunków lub opłat** w sposób
> wymagany ustawą. Domniemanie z art. 11 ust. 5 ustawy o otwartych danych dotyczy informacji
> udostępnianych w Biuletynie Informacji Publicznej lub w portalu danych i nie obejmuje
> wprost interfejsu `api-krs.ms.gov.pl`. Wniosek zmierza zatem do usunięcia tej niepewności.
>
> **7. Wnioski dodatkowe**
> a) o wskazanie warunków korzystania z interfejsu programistycznego oraz kryteriów jakości
>    usług w zakresie jego wyników, wydajności i dostępności, a także punktu kontaktowego do
>    spraw interfejsu — zgodnie z art. 3 rozporządzenia (UE) 2023/138;
> b) o wskazanie sposobu obliczenia ewentualnej opłaty, na podstawie art. 21 ustawy o otwartych
>    danych. Zwracam przy tym uwagę, że zgodnie z art. 20 tej ustawy kosztów dostosowania
>    systemu teleinformatycznego nie uwzględnia się w odniesieniu do danych dynamicznych oraz
>    danych o wysokiej wartości udostępnianych za pośrednictwem interfejsu programistycznego.
>
> Proszę o doręczenie rozstrzygnięcia na wskazany adres do doręczeń elektronicznych.
>
> Z wyrazami szacunku,
>
> [podpis kwalifikowany / zaufany / osobisty — art. 4b ust. 2 ustawy o KRS wymaga jednego z nich]

---

## What happens after sending

| Etap | Podstawa | Termin |
|---|---|---|
| Wezwanie do uzupełnienia braków, jeżeli będą | art. 39 ust. 5 | 7 dni na uzupełnienie, pod rygorem pozostawienia bez rozpoznania |
| Rozpatrzenie | art. 40 | niezwłocznie, nie później niż 14 dni; przedłużenie z zawiadomieniem, maks. 2 miesiące od złożenia |
| Rozstrzygnięcie | art. 42 | oferta z warunkami i opłatami / informacja o braku możliwości / odmowa decyzją |
| **Uwaga przy trybie ciągłym** | art. 42 | od oferty **nie przysługuje sprzeciw** — zostaje przyjęcie albo rezygnacja |
| Odwołanie od decyzji | art. 43 ust. 1 | organem odwoławczym jest **minister właściwy do spraw informatyzacji**, nie Minister Sprawiedliwości |
| Sąd | art. 43 ust. 3 | skarga do WSA; przekazanie akt 15 dni, rozpoznanie 30 dni |

## Risks worth knowing before sending

- **The standard refusal to a bulk request** is art. 10 sec. 4: no obligation to process information
  in the requested way if that requires "disproportionate action exceeding simple operations". The
  application is drafted to blunt it — point 4 states expressly that we ask for the format the
  register already produces.
- **Art. 6 limits** re-use where statutory secrets, business secrets, third-party database rights or
  the privacy of natural persons apply. For a public register this mostly bites on personal data of
  people in company bodies; the open extract already anonymises them.
- **No one can obtain exclusivity** (art. 9) and comparable applicants must get comparable terms
  (art. 8 sec. 1). Worth remembering if the offer comes back worse than what commercial providers
  evidently work on.

## Two corrections to what this project said earlier

**"A commercial intermediary carries the risk" was imprecise.** There is no licensing regime for
resellers of KRS data in Polish law. Commercial providers are ordinary re-users of the same public
sources; what they sell is aggregation, history, normalisation and an SLA. They carry **operational
and contractual** risk and cannot confer a legal title stronger than the statutory one. The ADR has
been corrected.

**There is already one lawful bulk element, and it is directly useful to us.** Art. 4 sec. 4b of the
KRS act requires the Central Information to publish free of charge, over public networks, a **list of
entities with an entry on bankruptcy or the opening of restructuring** — name, KRS number, tax
number, seat, date of the ruling, case reference and how the proceedings ended. That is a level-one
risk signal available in bulk, without any application. It belongs in
`../../ceidg-tool/docs/research/krs-register-risk-signals.md` as a source we missed.

## Still open

- **No case law was checked** — the court databases were unreachable in both passes. A precedent on
  refusal under art. 10 sec. 4 would change the odds of this route materially. Close this before
  sending.
- Whether Poland published an exemption under art. 14 sec. 5 of directive 2019/1024 covering the
  register — if it did, the high-value-dataset argument weakens for the exemption period.
- Whether a machine interface exists through the European register interconnection system, and on
  what re-use terms.
- Neither pass had a working search engine, so no law-firm commentary, no doctrine and no
  practitioner experience informed either report. Everything above comes from statutory text, the
  bill's explanatory memorandum, the EU regulation and live probes.
