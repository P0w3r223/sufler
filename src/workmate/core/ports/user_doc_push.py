"""Port WYJŚCIOWEGO dokumentu (pliku) 1:1 do użytkownika (ADR 0027, wariant PLIKOWY).

Wariant obrazowy (``user_push.py``) osadza obraz INLINE przez ``hostedContents`` czatu — bez dysku,
więc bez zakresu ``Files.*``. Dokument (md/txt/pdf/docx) tak się nie da: plik musi trafić na dysk
(OneDrive „głosu" bota) i zostać ZREFEROWANY w wiadomości jako załącznik typu ``reference`` —
mechanizm inny niż inline-obraz i inny niż ``TeamsFileSender.upload_channel_file`` (dysk KANAŁU).
Dlatego stoi jako osobny port, a nie metoda na tamtych.

Dostawa 1:1 wymaga zakresów CZATU (``Chat.Create``/``ChatMessage.Send``) ORAZ zapisu
``Files.ReadWrite.All`` (wgranie na OneDrive) — szerzej niż obraz. Bramkę i scope'y trzyma
``config/teams_graph.py`` (``enable_user_doc_push``, osobno od obrazowej
``enable_user_file_push``).

``Protocol`` jak pozostałe porty. Metoda jest SYNCHRONICZNA, bo narzędzia agenta biegną
synchronicznie (dispatch w puli wątków) — adapter używa ``httpx.Client``, jak ``TeamsFileSender``
(ADR 0026) i ``UserImageSender`` (ADR 0027, obraz). Rdzeń o Graph/OneDrive nie wie; testuje się go
strukturalną atrapą tego kontraktu. Renderowanie treści → bajtów robi osobny ``DocumentRenderer``
(``core/ports/document.py``); ten port dostaje już GOTOWE bajty + typ MIME + nazwę.
"""

from __future__ import annotations

from typing import Protocol


class UserDocSender(Protocol):
    """Wyjściowy dokument Graph: wgraj plik i wyślij go jako ZAŁĄCZNIK w wiadomości 1:1."""

    def send_document_to_user(
        self,
        target_user_id: str,
        filename: str,
        content: bytes,
        content_type: str,
        *,
        caption_html: str = "",
    ) -> None:
        """Wyślij ``content`` (bajty ``filename``, typ ``content_type``) do ``target_user_id``.

        Plik ląduje na dysku OneDrive „głosu" bota, zostaje udostępniony odbiorcy i zreferowany jako
        załącznik w czacie 1:1 (idempotentne utworzenie/znalezienie czatu). Wymaga zakresów czatu
        (``Chat.Create``/``ChatMessage.Send``) ORAZ ``Files.ReadWrite.All`` na tokenie drzwi.

        ``caption_html`` — opcjonalna, ZAUFANA treść HTML wiadomości niosącej załącznik (np. bogata
        karta czasu z ``render_timesheet_message``). Pusta = adapter daje własny minimalny podpis.
        Kontrakt jak ``send_chat_html``: podana treść idzie do Teams BEZ escapowania, więc KAŻDY jej
        bajt musi pochodzić od wołającego z rdzenia (wartości zewnętrzne już zescapowane) — nigdy od
        modelu ani z niezaufanego źródła.
        """
        ...
