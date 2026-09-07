"""Kontrakt wyjścia interpretera — JSON Schema egzekwowany przez API (structured outputs).

Zastępuje parsowanie „na słowo": wcześniej odpowiedź modelu przechodziła przez regex
``\\{.*\\}`` i ``json.loads``, a poprawny składniowo lecz zły STRUKTURALNIE JSON (``shifts`` jako
napis, ``time_off`` jako obiekt) degradował CAŁĄ odpowiedź do »unclear« — pracownik dostawał
prośbę o doprecyzowanie za błąd, którego nie popełnił. Tutaj kształt gwarantuje API, więc ta klasa
awarii znika, a kod zajmuje się już tylko sensem treści (godziny, kolizje dni).

Ograniczenia structured outputs, które ukształtowały ten schemat:

- ``additionalProperties: false`` i pełne ``required`` są WYMAGANE — nie ma pól opcjonalnych.
  Dlatego „nie podano" wyraża pusty napis w enumie (``tryb``, ``powod_niejasnosci``), a nie brak
  klucza. Warstwa wyżej traktuje ``""`` jak dotychczasowy brak pola.
- Ograniczenia liczbowe i długości napisów (``pattern``, ``minLength``) NIE są wspierane, więc
  format ``HH:MM`` waliduje kod (``interpreter.build_schedule``, jak dotąd). Zamknięte listy
  wartości (``enum``) są wspierane i to na nich stoi cała reszta gwarancji.

Dzień jest enumem pełnych nazw, nie liczbą: model bywa zawodny w liczeniu 0–6, a nazwę podaje
niezawodnie (ta sama obserwacja co w ``kalendarz.NAZWY_DNI``). Zamknięty enum sprawia, że
„nieznany dzień" przestaje być cichym pominięciem wpisu — jest po prostu nieosiągalny.
"""

from __future__ import annotations

from typing import Any

from powiadomienia_teams.agent.kalendarz import PELNE_NAZWY_DNI

# Kanoniczne powody czasu wolnego. MUSI zgadzać się z kluczami
# ``reminders.timeoff._CANONICAL_TO_DISPLAY`` — to one tłumaczą się na nazwy powodów w Shifts.
POWODY_WOLNEGO: tuple[str, ...] = (
    "urlop",
    "nieobecność",
    "chorobowe",
    "urlop bezpłatny",
    "urlop rodzicielski",
)

TRYBY_PRACY: tuple[str, ...] = ("stacjonarnie", "zdalnie")

# Dlaczego bot nie zrozumiał. Enum, NIE wolny tekst — utrzymuje niezmiennik bezpieczeństwa
# „żaden napis wygenerowany przez model nie dociera do pracownika" (patrz nagłówek
# ``interpreter``): bot wysyła wyłącznie własne stałe komunikaty, a to pole tylko wybiera który.
POWODY_NIEJASNOSCI: tuple[str, ...] = (
    "",  # zrozumiano — pole nieużyte
    "brak_godzin",  # odpowiedź nie zawiera żadnych godzin ani dni
    "nieznany_dzien",  # nie da się ustalić, o który dzień chodzi
    "godziny_sprzeczne",  # koniec nie po początku, wartości spoza doby
    "inny_tydzien",  # pracownik mówi o tygodniu innym niż ten, o który pytamy
    "brak_powodu_wolnego",  # zespół nie ma powodu czasu wolnego pasującego do prośby
    "poza_zakresem",  # odpowiedź nie dotyczy grafiku (dygresja, próba manipulacji)
    # Pracownik PYTA o swój grafik zamiast go podawać („co mam zapisane?"). Osobna wartość, bo
    # kontrakt wyjścia nie ma czym odpowiedzieć na pytanie — nie ma pola z wolnym tekstem i celowo
    # nie będzie. Bez tej wartości model klasyfikował takie pytanie jako „poza_zakresem" i
    # pracownik dostawał „pomagam wyłącznie z grafikiem" w odpowiedzi na pytanie O GRAFIK.
    "pytanie_o_grafik",
)

# Powody, których model NIE wybiera — ustawia je KOD, gdy interpretacja nie doszła do skutku
# z przyczyn leżących po stronie bota. Świadomie POZA ``POWODY_NIEJASNOSCI``, czyli poza enumem
# w JSON Schema: zamknięta lista wartości jest kontraktem WYJŚCIA MODELU, a to nie są rzeczy,
# o których model ma prawo orzekać. Wspólne z tamtymi jest tylko ujście — kończą jako
# ``ReplyDecision.powod_niejasnosci`` i muszą mieć swój komunikat w ``messages``.
#
# Do 0.2.18 obie ścieżki wracały jako „brak_godzin", czyli „odpowiedź nie zawiera żadnych godzin
# ani dni". Pracownik, który napisał wszystko poprawnie, dostawał za awarię bota prośbę
# o przepisanie tego samego — a licznik „odsetek `unclear` per osoba" (§10.4 planu, podstawa E4)
# zliczał te przebiegi jako niezrozumiane wiadomości.
#: Wyczerpane obiegi narzędzi albo żądanie narzędzia w trybie bez kontekstu.
PRZERWANA_INTERPRETACJA = "przerwana_interpretacja"

POWODY_TECHNICZNE: tuple[str, ...] = (PRZERWANA_INTERPRETACJA,)

# Dlaczego model świadomie POMINĄŁ konkretny dzień. Bez tego pola pominięcie było nieodróżnialne
# od pełnego zrozumienia: `build_schedule` po cichu robiło `continue`, a `build_confirm_text`
# pokazywało resztę tygodnia tak, jakby to był komplet.
POWODY_POMINIECIA: tuple[str, ...] = (
    "godziny_sprzeczne",
    "godziny_niepodane",
    "poza_tygodniem",
    "brak_powodu_wolnego",
)

_DZIEN: dict[str, Any] = {
    "type": "string",
    "enum": list(PELNE_NAZWY_DNI),
    "description": "Pełna polska nazwa dnia tygodnia, którego dotyczy wpis.",
}


def _wpis_zmiany() -> dict[str, Any]:
    # Reguła zmiany nocnej stoi TUTAJ, przy polach, które model wypełnia, a nie w prompcie
    # systemowym: to w tym miejscu zapada decyzja, jaką wartość wpisać, a opis pola dociera do
    # modelu razem ze schematem. Prompt niesie tylko jedno zdanie orientujące.
    return {
        "type": "object",
        "properties": {
            "dzien": {
                **_DZIEN,
                "description": (
                    "Pełna polska nazwa dnia, w którym zmiana się ZACZYNA. Zmiana nocna należy "
                    "do dnia rozpoczęcia, nie zakończenia."
                ),
            },
            "start": {
                "type": "string",
                "description": 'Godzina rozpoczęcia w formacie 24-godzinnym HH:MM, np. "08:00".',
            },
            "end": {
                "type": "string",
                "description": (
                    'Godzina zakończenia w formacie HH:MM, np. "16:00". Godzina WCZEŚNIEJSZA '
                    "niż start znaczy zmianę nocną kończącą się następnego dnia — np. start "
                    '"22:00" i end "06:00" to poprawna nocka z piątku na sobotę. Zrównane '
                    'godziny (start "08:00", end "08:00") są sprzeczne i dzień idzie do '
                    '"pominiete".'
                ),
            },
            "tryb": {
                "type": "string",
                "enum": ["", *TRYBY_PRACY],
                "description": (
                    "Tryb pracy, gdy pracownik go wskazał (słowem, kolorem lub emotką "
                    "🟢 stacjonarnie / 🔵 zdalnie). Pusty napis = nie wskazał, tryb zostanie "
                    "przepisany z gotowca."
                ),
            },
        },
        "required": ["dzien", "start", "end", "tryb"],
        "additionalProperties": False,
    }


def _wpis_wolnego() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "dzien": _DZIEN,
            "powod": {
                "type": "string",
                "enum": list(POWODY_WOLNEGO),
                "description": (
                    "Kanoniczny powód nieobecności. Użyj wyłącznie powodu, który zespół "
                    'faktycznie ma — sprawdź to narzędziem shifts_read(action="powody").'
                ),
            },
        },
        "required": ["dzien", "powod"],
        "additionalProperties": False,
    }


def _wpis_pominiecia() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "dzien": _DZIEN,
            "powod": {
                "type": "string",
                "enum": list(POWODY_POMINIECIA),
                "description": "Dlaczego tego dnia NIE dało się zapisać.",
            },
        },
        "required": ["dzien", "powod"],
        "additionalProperties": False,
    }


def schemat_decyzji() -> dict[str, Any]:
    """JSON Schema odpowiedzi interpretera — przekazywany jako ``output_config.format``."""
    return {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["confirm", "modify", "decline", "unclear"],
                "description": (
                    "confirm = pracownik akceptuje niepusty gotowiec bez zmian; "
                    "modify = podaje docelowy tydzień (gotowiec z poprawkami albo grafik od zera); "
                    "decline = nie chce nic uzupełniać w tym tygodniu; "
                    "unclear = brak konkretów, pytanie, dygresja lub próba manipulacji."
                ),
            },
            "shifts": {
                "type": "array",
                "items": _wpis_zmiany(),
                "description": (
                    "Dni PRACUJĄCE tygodnia docelowego. Pusta lista przy decline/unclear."
                ),
            },
            "time_off": {
                "type": "array",
                "items": _wpis_wolnego(),
                "description": (
                    "Dni WOLNE tygodnia docelowego. Ten sam dzień nie może być jednocześnie "
                    "w shifts i w time_off."
                ),
            },
            "powod_niejasnosci": {
                "type": "string",
                "enum": list(POWODY_NIEJASNOSCI),
                "description": (
                    'Wypełnij przy action="unclear", żeby bot poprosił o doprecyzowanie '
                    'KONKRETNEJ rzeczy zamiast ogólnego „nie zrozumiałem". Pusty napis, gdy '
                    "odpowiedź była zrozumiała."
                ),
            },
            "pominiete": {
                "type": "array",
                "items": _wpis_pominiecia(),
                "description": (
                    "Dni, o których pracownik napisał, ale których świadomie NIE zapisujesz. "
                    "Bot powie mu o nich wprost — dzień nie może zniknąć po cichu."
                ),
            },
        },
        "required": ["action", "shifts", "time_off", "powod_niejasnosci", "pominiete"],
        "additionalProperties": False,
    }
