# Cotygodniowe karty czasu → arkusz WorklogPRO + wiadomość na Teams

Jak uruchomić drzwi `workmate-worklogi` z [ADR 0035](../adr/0035-weekly-per-person-worklogpro-sheets-and-teams-dm.md):
w piątek program bierze godziny za **tydzień zamknięty** (poprzedni poniedziałek–niedziela),
generuje każdej osobie arkusz importu WorklogPRO i wysyła jej prywatną wiadomość z zestawieniem.

> **Tryb bojowy nie wystartuje**, dopóki nie potwierdzisz schematu importu (§1) i nie ustawisz
> `WORKMATE_WORKLOGI_HEADERS_CONFIRMED=true`. Nagłówki w kodzie to HIPOTEZA z dokumentacji
> producenta, a dopasowanie kolumn idzie po nazwie — jedna literówka unieważnia każdy plik.
> Przebieg PRÓBNY działa bez tego i to on generuje arkusz do porównania.

---

## 1. Bramka: potwierdzenie schematu WorklogPRO

W Jirze: **Apps → WorklogPRO → Import worklogs**

1. Pobierz szablon (jest generowany pod atrybuty Waszej instancji) i porównaj nagłówki z
   `WORKLOGPRO_HEADERS` w `src/workmate/core/domain/timesheet_sheet.py`.
2. Zaimportuj ręcznie 2–3 wiersze — czy przechodzi?
3. Zaimportuj **te same** wiersze drugi raz — czy powstają duplikaty? (Dokumentacja milczy;
   zakładamy, że tak.)
4. **Czy import działa z konta bez uprawnień admina?** Jeśli nie, model „plik per osoba" trzeba
   zamienić na jeden zbiorczy arkusz dla admina — to inna aplikacja.

Rozjazd nagłówków poprawia się w dwóch miejscach: stała `WORKLOGPRO_HEADERS` w module i test
`test_headers_are_not_changed_by_accident` (to DETEKTOR ZMIANY, nie bramka poprawności — nie wie,
jak wyglądają prawdziwe nagłówki, pilnuje tylko, żeby nikt nie zmienił naszych mimochodem).

Gdy wszystkie cztery punkty przejdą, zapisz to w konfiguracji:

```dotenv
WORKMATE_WORKLOGI_HEADERS_CONFIRMED=true
```

Bez tego `WORKMATE_WORKLOGI_DRY_RUN=false` kończy się twardym błędem startu — świadomie, żeby
pierwszy przebieg bojowy nie rozesłał kilkunastu bezużytecznych plików.

## 2. Konfiguracja

```dotenv
WORKMATE_WORKLOGI_ENABLED=true
# Katalog arkuszy — MUSI leżeć poza data/ i poza repo (dane osobowe, nie baza wiedzy).
# Obie kontrole są egzekwowane przy starcie; ścieżka w drzewie roboczym = twardy błąd.
WORKMATE_WORKLOGI_OUTPUT_DIR=D:/worklogi
WORKMATE_WORKLOGI_IDENTITIES=D:/worklogi/identities.yaml
WORKMATE_WORKLOGI_TEAM_ID=9c76036b-...
WORKMATE_WORKLOGI_HOURS_PATH=D:/worklogi/hours.json
# Pilotaż: zacznij od siebie.
WORKMATE_WORKLOGI_ONLY_SOURCE_IDS=EMP-017
```

Tożsamość Graph reużywa `WORKMATE_TEAMS_PUSH_CLIENT_ID`/`_TENANT_ID` i wspólny cache MSAL — jedno
logowanie. Pełna lista pokręteł: [`reference/config.md`](../reference/config.md).

## 3. Mapa tożsamości

Graph da AAD user-id i nazwę, ale **nie konto Jiry** (bywa nim prywatny adres spoza tenanta).
Dlatego mapowanie jest jawne:

```yaml
EMP-042:
  aad_user_id: 712020-...          # z Graph / scripts/lista_czlonkow.py
  jira_user: mikolaj@example.org        # e-mail albo accountId Atlassiana
  display_name: Mikołaj Anonimowicz # opcjonalnie, nadpisuje nazwę z Graph
```

**Fail-closed:** nieznany `source_id`, brak `jira_user` albo osoba spoza zespołu = brak pliku i brak
wiadomości, z wpisem w raporcie przebiegu. Nigdy nie zgadujemy po nazwisku — zły `jira_user`
zaimportuje czyjeś godziny na cudze konto Jiry, a tego (ADR 0034) nie da się cofnąć narzędziem.

## 4. Źródło godzin (na razie atrapa)

Docelowy system nie jest ustalony, więc czytamy plik JSON:

```json
[
  {"source_id": "EMP-042", "day": "2026-07-15", "issue_key": "WT-12", "hours": 3.5},
  {"source_id": "EMP-017", "day": "2026-07-16", "issue_key": "WT-14", "minutes": 90,
   "comment": "przegląd kodu"}
]
```

Podmiana na realne źródło to jedna klasa spełniająca port `HoursSource` i jedna linia w wiringu
drzwi — rdzeń się nie zmieni.

## 5. Uruchomienie

```bash
uv sync --extra worklogi
uv run workmate-worklogi --login    # jednorazowo (także po dodaniu TeamMember.Read.All)
uv run workmate-worklogi --once     # jeden przebieg
uv run workmate-worklogi            # pętla: czeka na kolejne piątki
```

**Domyślnie tryb PRÓBNY** (`WORKMATE_WORKLOGI_DRY_RUN=true`): arkusze powstają, wiadomości nie
wychodzą, stan się nie zapisuje. Obejrzyj pliki, zanim ustawisz `false`.

Blokada jednej instancji jest automatyczna — dwa procesy wysłałyby ludziom po dwie wiadomości.

## 6. Co dostaje pracownik

Prywatna wiadomość na Teams z tabelą (dzień, zgłoszenie, czas), sumą tygodnia, ścieżką do pliku
i przypomnieniem, że **ponowny import tego samego arkusza zdubluje wpisy**. Czas w tabeli jest
w tej samej notacji co w arkuszu (`2h 30m`), więc oba dokumenty da się porównać wiersz po wierszu,
a suma zgadza się co do minuty. Nazwa pliku niesie identyfikator osoby i etykietę tygodnia
(`worklog_mikolaj-anonimowicz_emp-042_2026-w29.xlsx`) — identyfikator jest tam, bo dwie osoby
o tej samej nazwie nadpisywały sobie arkusze.

## 7. Znane ograniczenia

| Ograniczenie | Skutek | Status |
|---|---|---|
| Brak załącznika w Teams | Ścieżka jako tekst, plik na udziale | Czeka na `Files.ReadWrite.All` (ADR 0026/0027) |
| Ponowny import dubluje wpisy | Człowiek importuje sam | Mitygacja: etykieta tygodnia + ostrzeżenie |
| Schemat niepotwierdzony | Zły nagłówek = plik nie do importu | Bramka §1 |
| Import może być admin-only | Model „plik per osoba" upada | Do sprawdzenia w §1 |
| Raport dotyczy tygodnia zamkniętego | Zestawienie sprzed 3–12 dni | Świadomy wybór: okno bieżące gubiło weekend BEZPOWROTNIE |
