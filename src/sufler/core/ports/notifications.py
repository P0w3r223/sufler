"""Port proaktywnego powiadamiania Teams (dual-target, ADR 0022) — kierunek WYJŚCIOWY.

``Protocol`` jak pozostałe porty. Notifier rdzenia (``EventNotifier``) zależy tylko od tego
kontraktu, więc pełną logikę testujemy atrapą — bez ``httpx``/MSAL. Adapter (``HttpxTeamsNotifier``)
tłumaczy ``text`` (Markdown) na HTML Teams i wypycha go do celu; rdzeń o HTML/Graph nie wie.

Dwa cele, oba konfigurowalne (decyzja użytkownika): czat 1:1 do osoby oraz post na kanał zespołu.
"""

from __future__ import annotations

from typing import Protocol


class TeamsNotifier(Protocol):
    """Wypchnięcie wiadomości do Teams: czat 1:1, nowy post na kanale lub odpowiedź w wątku."""

    async def send_chat(self, target_user_id: str, text: str) -> None:
        """Wyślij ``text`` (Markdown) jako wiadomość 1:1 do użytkownika o danym AAD id."""
        ...

    async def send_chat_html(self, target_user_id: str, html: str) -> None:
        """Wyślij GOTOWY HTML jako wiadomość 1:1 — z pominięciem renderera Markdown (ADR 0035).

        Osobna metoda, nie flaga w ``send_chat``, bo różni się KONTRAKTEM BEZPIECZEŃSTWA, nie
        formatowaniem. ``send_chat`` renderuje treść niezaufaną (GitHub/Jira/model) i dlatego
        escapuje surowy HTML oraz nie włącza tabel. Tu jest odwrotnie: wołający deklaruje, że
        HTML powstał w rdzeniu, ma sztywny szkielet i każdą wstawioną wartość przepuszczoną przez
        ``html.escape`` (``core/domain/timesheet_message.py``).

        **Nie wolno** podawać tu treści pochodzącej od użytkownika, modelu ani z mostu zdarzeń —
        do tego służy ``send_chat``. Ta metoda istnieje wyłącznie dlatego, że tabela HTML jest
        jedynym czytelnym sposobem pokazania kilkunastu wpisów czasu w wiadomości.
        """
        ...

    async def post_channel(self, team_id: str, channel_id: str, text: str) -> str:
        """Wyślij ``text`` jako NOWY post (root wątku) na kanale; zwróć id utworzonej wiadomości.

        Id roota jest potrzebne do wątkowania (ADR 0024): notifier zapamiętuje je w
        ``ThreadLinkStore`` i kolejne zdarzenia tego samego issue/PR dokłada jako ``reply_channel``.
        """
        ...

    async def reply_channel(self, team_id: str, channel_id: str, root_id: str, text: str) -> None:
        """Wyślij ``text`` jako odpowiedź w istniejącym wątku (``root_id``) na kanale zespołu."""
        ...
