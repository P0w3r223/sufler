"""Port WYJŚCIOWEGO załącznika Graph — wspólny prymityw „wyślij plik na Teams" (ADR 0026).

Do dziś każde drzwi mówią wyłącznie tekstem: ``TeamsNotifier`` (ADR 0022) i ``reply_on_thread``
(ADR 0024) emitują HTML/tekst, załączniki są ściśle WEJŚCIOWE (ADR 0016 — agent czyta to, co
wgra użytkownik). Ten port dokłada BRAKUJĄCĄ zdolność: wgranie bajtów na dysk kanału w SharePoint
i zreferowanie ich w wiadomości. To jeden PRYMITYW współdzielony przez trzech przyszłych
konsumentów — odpowiedź plikiem w wątku (ADR 0026), push pliku do użytkownika (ADR 0027) oraz
dostawę arkusza worklog załącznikiem (ADR 0035/0038) — dlatego stoi osobno, a nie w którymś z nich.

``Protocol`` jak pozostałe porty (dowolna zgodna implementacja bez dziedziczenia). Metody są
SYNCHRONICZNE, bo narzędzia agenta biegną synchronicznie (dispatch w puli wątków, ADR 0026): adapter
używa ``httpx.Client``, nie ``AsyncClient`` — jak zapis GitHub Gate-4 (``GithubWritePort``).
Rdzeń o Graph/HTML nie wie; testuje się go strukturalną atrapą tego kontraktu.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class UploadedFile:
    """Referencja pliku wgranego na dysk kanału — wszystko, czego trzeba, by go załączyć.

    Wynik ``upload_channel_file`` i WEJŚCIE ``post_reply_with_attachment``: rozbicie na dwa kroki
    (wgraj → załącz) jest celowe — ten sam upload obsłuży odpowiedź w wątku ORAZ (później) push
    1:1, bez ponownego wysyłania bajtów. ``attachment_id`` to GUID (z ``eTag`` driveItem) WALIDOWANY
    już przy wgraniu — Teams wiąże nim wpis ``<attachment id=…>`` w treści z pozycją w tablicy
    ``attachments``. Ekstrakcja przy wgraniu (nie przy załączaniu) sprawia, że referencja jest z
    definicji gotowa: niżej nie da się już zbudować martwego załącznika z pustym id/URL-em.
    """

    item_id: str
    name: str
    web_url: str
    attachment_id: str


class TeamsFileSender(Protocol):
    """Wyjściowy załącznik Graph: wgraj plik na dysk kanału i zrefereuj go w odpowiedzi w wątku.

    Rozdzielone na dwa kroki, bo referencja (``UploadedFile``) jest współdzielona między celami
    dostawy: dziś odpowiedź w wątku, w kolejnych ADR-ach push 1:1 do nadawcy — wgranie robi się raz.
    """

    def upload_channel_file(
        self,
        team_id: str,
        channel_id: str,
        filename: str,
        content: bytes,
        content_type: str,
    ) -> UploadedFile:
        """Wgraj ``content`` jako ``filename`` na dysk (SharePoint) kanału; zwróć referencję.

        Nadpisuje plik o tej samej nazwie w folderze kanału (upload po ŚCIEŻCE jest idempotentny —
        powtórka nie tworzy duplikatu), więc wołający dba o unikalność nazwy, jeśli jej potrzebuje.
        Wymaga zakresu zapisu Graph ``Files.ReadWrite.All`` (nadany 2026-07-27) na tokenie drzwi.
        """
        ...

    def post_reply_with_attachment(
        self,
        team_id: str,
        channel_id: str,
        root_id: str,
        html: str,
        attachment: UploadedFile,
    ) -> None:
        """Odpowiedz w istniejącym wątku (``root_id``) wiadomością z załączonym ``attachment``.

        ``html`` to GOTOWY, zaufany HTML składany w rdzeniu (jak ``send_chat_html``, ADR 0035) — nie
        treść niezaufana. Gdy root wątku zniknął (Graph 404), adapter podnosi ``ThreadRootGone``,
        żeby wołający mógł zdegradować, zamiast wywracać strumień.
        """
        ...
