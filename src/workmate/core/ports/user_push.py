"""Port WYJŚCIOWEGO obrazu 1:1 do użytkownika (ADR 0027, wariant obrazowy) — kierunek WYJŚCIOWY.

Lustro ADR 0016 (obrazy WEJŚCIOWE — agent czyta to, co wgra użytkownik): tu agent ODSYŁA obraz
osobie, z którą właśnie rozmawia. Obraz idzie INLINE przez ``hostedContents`` czatu 1:1 — bez dysku
SharePoint, więc bez zakresu ``Files.*`` (odrębnie od ``TeamsFileSender``/ADR 0026, który wgrywa
pliki na dysk kanału). Dostawa 1:1 wymaga jednak zakresów CZATU (``Chat.Create``/
``ChatMessage.Send``) — skonsentowanych już przez admina (``Powiadomienia_teams``), lecz
nieobecnych w tokenie pollera kanału bez włączenia bramki (patrz ``config.py``).

``Protocol`` jak pozostałe porty. Metoda jest SYNCHRONICZNA, bo narzędzia agenta biegną
synchronicznie (dispatch w puli wątków) — adapter używa ``httpx.Client``, nie ``AsyncClient``, jak
``TeamsFileSender`` (ADR 0026). Rdzeń o Graph/HTML nie wie; testuje się go atrapą tego kontraktu.
"""

from __future__ import annotations

from typing import Protocol

# Format obrazu → typ MIME dla ``hostedContents``. Wąska allowlista: tylko rastrowe formaty,
# które Teams renderuje inline (bez SVG — wektor niesie skrypt/zewnętrzne referencje).
IMAGE_CONTENT_TYPES: dict[str, str] = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
}

# Sygnatury bajtowe (magic bytes) formatów z allowlisty — do WERYFIKACJI, że bajty faktycznie są
# tym, co deklaruje model (boundary validation). Klucz KANONICZNY (jpeg → jpg). Bez tego model
# mógłby zadeklarować ``png`` i przesłać dowolne bajty z ``contentType: image/png``.
_IMAGE_MAGIC: dict[str, tuple[bytes, ...]] = {
    "png": (b"\x89PNG\r\n\x1a\n",),
    "jpg": (b"\xff\xd8\xff",),
    "gif": (b"GIF87a", b"GIF89a"),
}


def sniff_image_format(content: bytes) -> str | None:
    """Rozpoznaj format z magic bytes; kanoniczny klucz (``png``/``jpg``/``gif``) lub ``None``."""
    for fmt, prefixes in _IMAGE_MAGIC.items():
        if content.startswith(prefixes):
            return fmt
    return None


class UserImageSender(Protocol):
    """Wyjściowy obraz Graph: wyślij obraz INLINE w wiadomości 1:1 do użytkownika o danym AAD id."""

    def send_image_to_user(self, target_user_id: str, content: bytes, content_type: str) -> None:
        """Wyślij ``content`` (bajty obrazu) jako obraz inline w czacie 1:1 z ``target_user_id``.

        ``content_type`` to typ MIME z ``IMAGE_CONTENT_TYPES``. Obraz osadzamy przez
        ``hostedContents`` (bez dysku SharePoint), więc nie wymaga zakresu ``Files.*``. Idzie na
        czat 1:1 z odbiorcą (idempotentne utworzenie/znalezienie czatu), więc wymaga zakresów czatu
        na tokenie drzwi (``Chat.Create``/``ChatMessage.Send``).
        """
        ...
