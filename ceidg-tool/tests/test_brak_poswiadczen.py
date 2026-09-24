"""Paczka nie niesie żadnego poświadczenia — i to jest twierdzenie z obserwatorem.

Skan z 2026-09-24 przeszedł całą bazę obiektów po **wartości** obu sekretów tej maszyny i po
obu **kształtach** z `SECRET_PATTERNS`: zero trafień po wartości, 23 po kształcie i wszystkie 23
w nazwanych atrapach. Pomiar po wartości jest jednorazowy z natury — wartości w repozytorium nie
ma, o to przecież chodzi — więc powtarzalna jest tutaj wyłącznie połowa po kształcie.

**Skanowane są cztery zbiory, i to jest cała treść tego pliku.** Pierwsza wersja czytała sam
katalog roboczy, co przegląd testów obalił w sposób nie do obrony: publikuje się **historię**,
nie drzewo. Token wpisany, zacommitowany i skasowany następnym commitem zostawiał suitę zieloną
przy blobie leżącym w historii — a odruchową reakcją na czerwony test jest właśnie skasowanie
pliku. Przy kluczu Anthropic dałoby się to odkręcić obrotem klucza; token CEIDG niesie w treści
PESEL, więc obrót nie wyjmuje PESEL-u z opublikowanego bloba. Stąd:

1. bloby osiągalne z `HEAD` — to, co poleci przy `git push`;
2. bloby z indeksu — treść zatwierdzona do commita, która bywa inna niż plik na dysku;
3. pliki śledzone, czytane z dysku — zmiana jeszcze nieprzygotowana;
4. pliki nieśledzone i nieignorowane — czyli dokładnie to, co dobrałoby `git add -A`.

Zbiór czwarty istnieje dlatego, że scenariusz z docstringu („ktoś wkleja token do nowego pliku")
rozgrywa się **przed** `git add`, a bramkę uruchamia się przed commitem. Sama lista śledzonych
plików nie widziała go aż do chwili, w której było za późno.

**Płytki klon jest zgłaszany jako awaria, nie pomijany.** `actions/checkout` domyślnie bierze
`fetch-depth: 1`, więc połowa po historii widziałaby jeden commit i milczała — najgorszy możliwy
tryb awarii strażnika, bo zielony. Obserwator siedzi tu, w teście, a nie w pliku workflow: plik
workflow tego repozytorium odpala się dziś wyłącznie dla gałęzi `ceidg-tool`, której **już nie
ma**, a po przeniesieniu pracy do monorepo zadanie poprowadzą **ich** workflow. Gwarancja oparta
na cudzej konfiguracji nie jest gwarancją.

**Zakres jest podkatalogiem pakietu, wyliczanym z `git rev-parse --show-prefix`.** W układzie
samodzielnym prefiks jest pusty i skan obejmuje całe repozytorium. Po przeniesieniu do
`ceidg-tool/` na `Main` ta jedna linijka trzyma obie połowy w tym samym zakresie — bez niej
przeprowadzka robi dwie przeciwne rzeczy naraz: `ls-files` **zawęża** się do podkatalogu, a
`rev-list --objects HEAD` chodzi po **całym** repozytorium niezależnie od katalogu roboczego,
więc skan zacząłby odpowiadać za historię cudzego zespołu i czerwieniłby ich bramkę scalania na
atrapach z `claude_summary/`. Zmierzone na `origin/Main` i `origin/Dev`: po dwa takie odciski.

Czego ten plik nie złapie, powiedziane wprost, żeby nikt nie wziął zieleni za więcej, niż znaczy:

- sekretu **nieprzezroczystego**, niepasującego do żadnego kształtu z `SECRET_PATTERNS`;
- JWT-a rozbitego na dwa literały napisowe — a rozbić go trzeba, bo nie mieści się w limicie stu
  kolumn, którego pilnuje ruff;
- prawdziwego klucza wklejonego w miejsce, gdzie akurat stoi atrapa, jeżeli podmieni się przy tym
  wartość atrapy na tę z listy niżej;
- `config.DEFAULT_ENV_FILE` jest ścieżką **względną wobec katalogu roboczego**, więc operator
  uruchamiający narzędzie z korzenia monorepo czyta `.env` leżący poza tym zakresem.
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ceidg_tool import cli, config
from ceidg_tool.config import ENV_ANTHROPIC_KEY, ENV_TOKEN, SECRET_PATTERNS
from ceidg_tool.errors import ConfigError
from tests.support import zarejestrowane_polecenia

KORZEN = Path(__file__).resolve().parents[1]

# Atrapy rozpoznawane po **wartości**, nie po ścieżce. Lista po ścieżkach licencjonowała cały
# plik — prawdziwy klucz dopisany do `tests/test_jsonout.py` przechodził. Po wartości nie
# przechodzi, a przy okazji ten plik nie musi być wyjątkiem dla samego siebie: trzyma sha256
# dopasowania, więc sam żadnego kształtu sekretu nie niesie.
ATRAPY: dict[str, str] = {
    # `sk-ant-…-nieprawidlowy-klucz-do-testu` — scenariusz B2 przebiegu fazy 4.
    "1f7eea21a22f8c91ebfe29dd5500259da2d17a3ac76168982c2ab85895bdcb4d": "docs/test-runs-phase4.md",
    # JWT z podpisem `c2lnbmF0dXJlX3Rlc3Rvd2E`.
    "66fbfe077b14dc0c8b1291d2a333714d7dc540c6fd387399928ed1acca3859e6": "tests/test_ui_render.py",
    # Okaz z jwt.io — ten sam w `test_console.py` i `test_jsonout.py`.
    "dd24bb34887806e1cc4b6443591bae319b027e1e0524ebd77c02d04f399b3c6a": "tests/test_console.py",
}

# Polecenia działające **bez żadnego poświadczenia**. Reguła, którą stosuje kod, brzmi: token
# jest potrzebny wszędzie poza tą dwójką i poza `--demo`. `token zapisz|usun` jest tu dlatego,
# że poświadczenie *przyjmuje*, a nie *zużywa*; `szukaj-pkd` czyta dwa pliki YAML.
BEZ_POSWIADCZENIA: frozenset[str] = frozenset({"szukaj-pkd", "token"})

# Najkrótsze wywołanie, które dochodzi do rozstrzygania poświadczeń. Bez argumentu wymaganego
# przez polecenie typer kończy kodem 2 na użyciu i test mierzyłby wtedy parser, nie bramkę.
WYWOLANIA: dict[str, list[str]] = {
    "aktualizuj": ["aktualizuj"],
    "eksportuj": ["eksportuj"],
    "kreator": ["kreator"],
    "pobierz": ["pobierz", "-w", "wielkopolskie"],
    "raporty": ["raporty"],
    "runy": ["runy"],
    "sprawdz-nip": ["sprawdz-nip", "9995237548"],
    "sprawdz-token": ["sprawdz-token"],
    "szukaj-pkd": ["szukaj-pkd", "fryzjer"],
    "token": ["token", "usun"],
    "wyczysc": ["wyczysc"],
    "wznow": ["wznow"],
}


def _git(*argumenty: str, wymagane: bool = True) -> subprocess.CompletedProcess[str]:
    """Wywołanie gita. `wymagane=False` tam, gdzie **kod powrotu jest odpowiedzią**.

    Wszędzie indziej porażka gita musi być porażką testu: `check=False` na `rev-list` zamieniał
    awarię w pusty zbiór, czyli w zieleń — ten sam kształt defektu, przed którym stoi ten plik.
    """
    wynik = subprocess.run(
        ["git", *argumenty],
        cwd=KORZEN,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if wymagane and wynik.returncode != 0:
        pytest.fail(f"`git {' '.join(argumenty)}` zawiodło: {wynik.stderr.strip()}")
    return wynik


def _cat_file(tryb: str, szy: list[str]) -> bytes:
    """Karmi `git cat-file` przez `input=`, nie przez ręczny zapis na potok.

    Zapisanie kilkuset kilobajtów na stdin procesu, którego wyjścia jeszcze się nie czyta,
    zapycha bufor potoku i zakleszcza oba procesy — zdarzyło się przy pierwszym przebiegu tego
    skanu. `subprocess.run` czyta i pisze równolegle, więc problem znika razem z ręczną obsługą.
    """
    if not szy:
        return b""
    return subprocess.run(
        ["git", "cat-file", tryb],
        cwd=KORZEN,
        input=("\n".join(szy)).encode(),
        capture_output=True,
        check=True,
    ).stdout


def _rozbierz_strumien(strumien: bytes, oczekiwane: int) -> dict[str, bytes]:
    """Rozkłada wyjście `git cat-file --batch` na {sha: treść}.

    Twardo, nie po cichu. Wersja przerywająca na pierwszym nagłówku nie do rozebrania gubiła
    **resztę** strumienia: dowiązanie do podmodułu daje `<sha> missing`, więc sekret stojący
    za nim w kolejce nie był w ogóle czytany, a test świecił na zielono.
    """
    zawartosci: dict[str, bytes] = {}
    i = 0
    while i < len(strumien):
        koniec = strumien.find(b"\n", i)
        if koniec == -1:
            break
        czesci = strumien[i:koniec].split()
        if len(czesci) != 3:
            pytest.fail(f"Nagłówek `cat-file` nie do rozebrania: {strumien[i:koniec]!r}")
        sha, rozmiar = czesci[0].decode(), int(czesci[2])
        zawartosci[sha] = strumien[koniec + 1 : koniec + 1 + rozmiar]
        i = koniec + 1 + rozmiar + 1

    assert len(zawartosci) == oczekiwane, (
        f"`cat-file` zwrócił {len(zawartosci)} obiektów zamiast {oczekiwane} — skan przeczytał "
        "mniej, niż o ile prosił, więc jego cisza nic nie znaczy."
    )
    return zawartosci


def _dopasowania(tresc: bytes) -> set[str]:
    """Zwraca sha256 znalezionych kształtów sekretu. Nigdy samej wartości.

    Bajty zerowe lecą przed dopasowaniem, bo Windows PowerShell 5.1 pisze `>` i `Out-File`
    w UTF-16LE — plik z wklejonym tokenem wyglądałby wtedy jak `e\\x00y\\x00J\\x00…` i nie
    pasowałby do niczego. Wzorce są czysto ASCII, więc usunięcie zer nic poza tym nie zmienia.
    """
    tekst = tresc.replace(b"\x00", b"").decode("utf-8", "replace")
    return {
        hashlib.sha256(m.group(0).encode()).hexdigest()
        for wzor in SECRET_PATTERNS
        for m in wzor.finditer(tekst)
    }


@pytest.fixture(scope="module")
def korpus() -> dict[str, set[str]]:
    """{gdzie: odciski dopasowań} dla wszystkich czterech zbiorów. Zmierzone ~0,5 s."""
    if _git("rev-parse", "--git-dir", wymagane=False).returncode != 0:
        pytest.skip("Katalog nie jest repozytorium gita — nie ma czego pilnować.")
    if _git("rev-parse", "--is-shallow-repository").stdout.strip() == "true":
        pytest.fail(
            "Płytki klon: historia jest niewidoczna, więc ten skan nie dowodzi niczego. "
            "W CI użyj `actions/checkout` z `fetch-depth: 0`."
        )

    prefiks = _git("rev-parse", "--show-prefix").stdout.strip()
    znalezione: dict[str, set[str]] = {}

    def dodaj(gdzie: str, tresc: bytes) -> None:
        if odciski := _dopasowania(tresc):
            znalezione.setdefault(gdzie, set()).update(odciski)

    # 1. historia. `--full-history`, bo domyślne upraszczanie potrafi pominąć wersję pliku
    # wniesioną gałęzią boczną i scaloną — czyli dokładnie tę, którą ktoś „posprzątał".
    argumenty = ["rev-list", "--objects", "--full-history", "HEAD"]
    if prefiks:
        argumenty += ["--", prefiks]
    pary = [w.split(" ", 1) for w in _git(*argumenty).stdout.splitlines() if w]
    sciezki = {p[0]: (p[1] if len(p) > 1 else p[0]) for p in pary}
    typy = _cat_file("--batch-check=%(objectname) %(objecttype)", [p[0] for p in pary])
    bloby = [w.split()[0] for w in typy.decode().splitlines() if w.endswith(" blob")]
    for sha, tresc in _rozbierz_strumien(_cat_file("--batch", bloby), len(bloby)).items():
        dodaj(f"historia: {sciezki.get(sha, sha)}", tresc)

    # 2. indeks — treść przygotowana do commita bywa inna niż plik na dysku. Tryb 160000 to
    # dowiązanie do podmodułu: obiektu nie ma w tej bazie, `cat-file` odpowiada `missing`.
    wpisy = [w.split(maxsplit=3) for w in _git("ls-files", "--stage").stdout.splitlines() if w]
    w_indeksie = {w[1]: w[3] for w in wpisy if len(w) == 4 and w[0] != "160000"}
    szy = list(w_indeksie)
    for sha, tresc in _rozbierz_strumien(_cat_file("--batch", szy), len(szy)).items():
        dodaj(f"indeks: {w_indeksie.get(sha, sha)}", tresc)

    # 3 i 4. dysk: śledzone (zmiana jeszcze nieprzygotowana) oraz nieśledzone i nieignorowane,
    # czyli dokładnie zbiór, który dobrałby `git add -A`. `-z`, bo bez tego `core.quotePath`
    # wypisuje `żółw.txt` w postaci z ucieczkami ósemkowymi, `is_file()` jest wtedy fałszem
    # i plik **wypada ze skanu po cichu** — a polski operator nazywa pliki po polsku.
    na_dysku = _git("ls-files", "-z", "--cached", "--others", "--exclude-standard")
    for sciezka in {w for w in na_dysku.stdout.split("\0") if w}:
        plik = KORZEN / sciezka
        if not plik.is_file():
            continue
        try:
            dodaj(f"dysk: {sciezka}", plik.read_bytes())
        except OSError:  # pragma: no cover - plik zniknął w trakcie przebiegu
            continue

    return znalezione


# ----------------------------------------------------------------------------- git


def test_env_nie_jest_sledzony() -> None:
    """Śledzenie jest gorsze niż odignorowanie i dlatego sprawdzane pierwsze.

    Kolejność nie jest kosmetyczna: `git check-ignore` zwraca 1 dla pliku **już śledzonego**,
    więc test pytający najpierw o ignorowanie krzyczałby „przestał być ignorowany" w sytuacji,
    w której prawdziwym zdaniem jest „jest w repozytorium".

    Druga asercja jest szersza od pierwszej i o to chodzi: `ls-files -ci` wypisuje **każdy**
    śledzony plik pasujący do reguły ignorowania, więc obejmuje też `.env.local` wepchnięty
    przez `git add -f`, o którym lista nazw nigdy by się nie dowiedziała.
    """
    assert ".env" not in _git("ls-files").stdout.splitlines(), (
        "`.env` jest śledzony przez git. To jest wyciek, nie usterka — zdejmij go z indeksu "
        "i potraktuj oba poświadczenia jako ujawnione."
    )

    wepchniete = _git("ls-files", "-ci", "--exclude-standard").stdout.split()
    assert not wepchniete, f"Śledzone mimo reguły ignorowania (wymuszone `add -f`?): {wepchniete}"


def test_env_i_jego_warianty_pozostaja_ignorowane() -> None:
    """Jeden wiersz `.gitignore` dźwiga całą resztę — a wariantów nazwy jest więcej niż jeden.

    `.env.local`, `.env.prod`, `.env.bak` po kopii zapasowej: każdy z nich `git add -A`
    dobierze, a żaden nie jest `.env`. Stąd wzorzec `.env.*` z jawnym wyjątkiem na wzorzec.

    `--no-index` przy sprawdzaniu wyjątku nie jest ozdobą. Bez niego `check-ignore` zwraca 1
    dla **każdego pliku śledzonego**, cokolwiek mówią reguły, więc asercja o `.env.example`
    była zielona także po skasowaniu `!.env.example` z `.gitignore` — zmierzone.
    """
    for nazwa in (".env", ".env.local", ".env.prod", ".env.bak"):
        assert _git("check-ignore", "-q", nazwa, wymagane=False).returncode == 0, (
            f"`{nazwa}` nie jest ignorowany — przywróć wpis w `.gitignore` przed czymkolwiek innym."
        )

    wyjatek = _git("check-ignore", "-q", "--no-index", ".env.example", wymagane=False)
    assert wyjatek.returncode != 0, (
        "`.env.example` wpadł pod wzorzec `.env.*` i przestanie być śledzony — a to on niesie "
        "instrukcję, jak wstawić własne poświadczenie."
    )


def test_nigdzie_w_repozytorium_nie_ma_ksztaltu_sekretu(korpus: dict[str, set[str]]) -> None:
    """Kształt, nie wartość — bo wartości w repozytorium nie ma i test nie ma jej skąd wziąć.

    Wzorce biorę z `config.SECRET_PATTERNS`, żeby dopisanie trzeciego kształtu rozszerzało ten
    skan samo. Komunikat mówi o ujawnieniu, a nie o obrocie klucza: token CEIDG niesie PESEL,
    więc nowy token nie cofa publikacji starego.
    """
    obce = {
        gdzie: sorted(odciski - set(ATRAPY))
        for gdzie, odciski in korpus.items()
        if odciski - set(ATRAPY)
    }

    assert not obce, (
        "Kształt sekretu poza listą atrap: "
        + "; ".join(f"{gdzie} ({len(o)})" for gdzie, o in sorted(obce.items()))
        + ". Jeśli to nowa atrapa, dopisz jej sha256 do ATRAPY **w tym samym commicie**. "
        "Jeśli nie — poświadczenie jest ujawnione: nowy token nie wyjmie starego z historii."
    )


def test_lista_atrap_opisuje_stan_faktyczny(korpus: dict[str, set[str]]) -> None:
    """Zwolnienie wystawione **z góry**, na wartość, której repozytorium nigdy nie miało.

    To jest jedyna mutacja, którą ten test umie złapać, i warto ją nazwać dokładnie, bo dwie
    sąsiednie wyglądają podobnie, a nie są. Wpis na nieobecną wartość przepuściłby ją przy
    pierwszym pojawieniu się — czyli wpuszczałby dokładnie jeden sekret, wskazany zawczasu.
    Zmierzone: dopisanie wpisu o wartości spoza repozytorium czerwieni ten test.

    Czego **nie** złapie i dlaczego nie da się inaczej: usunięcia atrapy z drzewa roboczego.
    Skan obejmuje historię, a wartość raz zacommitowana zostaje w niej na zawsze, więc wpis
    opisujący ją pozostaje potrzebny, choćby plik dawno zniknął. Pierwsza wersja tego testu
    obiecywała w docstringu właśnie tamto i sprawdzała coś trzeciego — czy wypisana ścieżka
    jest nadal śledzona — a usunięcie atrapy z `tests/test_console.py` zostawiało ją zieloną.
    """
    zywe = set().union(*korpus.values()) if korpus else set()
    martwe = {odcisk: gdzie for odcisk, gdzie in ATRAPY.items() if odcisk not in zywe}

    assert not martwe, (
        f"ATRAPY wymienia wartości, których w repozytorium już nie ma: {sorted(martwe.values())}. "
        "Skreśl wpis, zamiast zostawiać zwolnienie na wartość, o której nikt już nie pamięta."
    )


# ----------------------------------------------------------------------------- wzorzec i komunikat


def test_env_example_jest_wzorcem_a_nie_schowkiem() -> None:
    """Plik ma **nazwać** oba poświadczenia i nie podać żadnego.

    Pusta wartość jest tu treścią: ktoś, kto wpisze swój token do `.env.example` zamiast do
    `.env`, wypycha go przy pierwszym commicie — `.env.example` nie jest ignorowany.

    Sprawdzane jest **każde** wystąpienie klucza, nie ostatnie. Słownik zbudowany z par gubi
    wcześniejsze przypisanie, więc `CEIDG_TOKEN=<prawdziwy>` nad pustym przechodziłoby — a to
    wygląda dokładnie jak plik, w którym ktoś „zostawił sobie na chwilę" swoją wartość.
    """
    wiersze = (KORZEN / ".env.example").read_text(encoding="utf-8").splitlines()
    przypisania = [w.split("=", 1) for w in wiersze if "=" in w and not w.lstrip().startswith("#")]

    for klucz in (ENV_TOKEN, ENV_ANTHROPIC_KEY):
        wystapienia = [wartosc for nazwa, wartosc in przypisania if nazwa.strip() == klucz]
        assert wystapienia, f"`.env.example` nie wymienia {klucz}."
        assert all(w.strip() == "" for w in wystapienia), (
            f"`.env.example` niesie wartość dla {klucz}."
        )


def test_komunikat_o_braku_tokenu_mowi_co_dziala_bez_niego() -> None:
    """Czytelnik tego zdania właśnie sklonował repozytorium i nie ma poświadczenia.

    Sam adres usługi zostawiał go przy zadaniu na Profil Zaufany, czyli na kilka dni — a dwie
    ścieżki bez tokenu istnieją i są w tym momencie całą odpowiedzią na „czy to w ogóle działa".
    """
    from ceidg_tool.config import load_settings

    with pytest.raises(ConfigError) as zlapany:
        load_settings(environ={}, env_file=None, use_keyring=False)

    komunikat = str(zlapany.value)
    assert "szukaj-pkd" in komunikat
    assert "--demo" in komunikat


# ----------------------------------------------------------------------------- lista bez tokenu


def test_wywolania_obejmuja_wszystkie_polecenia() -> None:
    """Nowe polecenie ma zatrzymać się tutaj, zanim trafi do zdania w README.

    Tabela wypisana ręcznie jest tyle warta, ile jej kompletność — a cztery dokumenty mówiły
    o „poleceniach sięgających do rejestru" listą, która gubiła `eksportuj`, `runy`, `wyczysc`
    i `kreator` i dokładała `sprawdz-token`, który do rejestru nie sięga wcale.
    """
    assert set(WYWOLANIA) == zarejestrowane_polecenia()


@pytest.mark.parametrize("polecenie", sorted(WYWOLANIA))
def test_poswiadczenie_jest_potrzebne_wszedzie_poza_wyliczona_dwojka(
    polecenie: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reguła publikowana w README, `.env.example`, `SKILL.md` i `CLAUDE.md` — zmierzona.

    Bez tego cztery kopie jednego zdania rozjeżdżały się po cichu z kodem: pomyłka nie była
    w zdaniu trudnym, tylko w takim, którego nikt nie miał jak sprawdzić. Odpowiedzią nie jest
    ostrożniejsze pisanie, tylko obserwator.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "read_token_from_keyring", lambda *_: None)
    monkeypatch.setenv("TERM", "dumb")
    monkeypatch.delenv("FORCE_COLOR", raising=False)

    wynik = CliRunner().invoke(
        cli.app, WYWOLANIA[polecenie], env={"CEIDG_TOKEN": "", "ANTHROPIC_API_KEY": ""}
    )

    if polecenie in BEZ_POSWIADCZENIA:
        assert "Brak tokenu" not in wynik.output, f"`{polecenie}` zaczęło wymagać poświadczenia."
    else:
        assert wynik.exit_code == 3, f"`{polecenie}` skończyło kodem {wynik.exit_code}"
        assert "Brak tokenu" in wynik.output
