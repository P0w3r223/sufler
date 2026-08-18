"""Hierarchia wyjątków rdzenia.

Adaptery i warstwa aplikacji podnoszą podklasy ``WorkMateError`` z konkretnym
kontekstem. Granica (adapter MCP) łapie ``RepositoryError`` i zamienia go na
czytelny komunikat dla klienta, zamiast wywracać cały serwer — to realizacja
zasady "fail fast na błędnych danych, degraduj łagodnie na granicy".
"""

from __future__ import annotations


class WorkMateError(Exception):
    """Bazowy wyjątek wszystkich błędów domenowych WorkMate."""


class RepositoryError(WorkMateError):
    """Błąd odczytu/parsowania danych z magazynu (notatki, rejestr projektów)."""


class WriteError(WorkMateError):
    """Nie udało się zapisać notatki: nieznany projekt, pusty slug albo problem I/O.

    To błąd *oczekiwany* (najczęściej złe wejście wołającego). Granica MCP łapie
    go i zwraca ``{"error": ...}`` — analogicznie do ``RepositoryError`` przy
    odczycie. Wyjątki nieznane nadal wypływają jako defekt kodu.
    """


class NoteExistsError(WriteError):
    """Notatka o tym samym id już istnieje — zapis create-only odrzucony (kolizja).

    Podklasa ``WriteError`` (istniejące ``except WriteError`` dalej łapią), ale WYRÓŻNIONA, by
    idempotentna ścieżka notatki ze spotkania (ADR 0043) odróżniła „już złożona" (wyścig zapisu:
    pre-check przeszedł, równoległe zadanie zapisało pierwsze) od realnej porażki zapisu i
    zaraportowała to jako pominięcie, nie błąd.
    """


class InvalidRequestError(WorkMateError):
    """Prośba wołającego jest niepoprawna (zły zakres, wartość spoza limitu) — bez mutacji.

    Istnieje, bo ``WriteError`` niósł dotąd DWA znaczenia: „zapis się nie udał" i „wejście jest
    złe". Na czystej ścieżce ODCZYTU to drugie jest myleniem czytelnika dokładnie tam, gdzie
    szuka dowodu, że zapisu nie ma. Granica traktuje go tak samo — koperta łapie
    ``WorkMateError`` i zwraca ``{"error": ...}``.
    """


class NoteAuthorizationError(WorkMateError):
    """Nadawca nie ma prawa złożyć notatki ze spotkania (B2 / ADR 0042).

    Podnoszony PRZED jakimkolwiek pobraniem transkryptu, gdy tożsamość nadawcy (AAD id)
    nie rozwiązuje się do rozpoznanego członka pionu (fail-closed). To błąd *oczekiwany*
    (autoryzacja), nie defekt — drzwi degradują go do czytelnej odmowy, nie do tracebacku.
    Osobny od ``WriteError``: „nie wolno" to inny komunikat niż „zapis się nie udał".
    """


class LLMError(WorkMateError):
    """Błąd komunikacji z modelem (runtime agenta, Faza 2 / ADR 0008).

    Sieć, limit żądań, uwierzytelnianie albo niespodziewany kształt odpowiedzi
    Claude API. Podnoszony wyłącznie w adapterze outbound (``AnthropicLLMClient``),
    żeby wokabularz SDK nie wchodził do rdzenia; drzwi degradują łagodnie.
    """


class JiraReadError(WorkMateError):
    """Odczyt z Jiry (ADR 0054, "moje zadania") się nie udał: auth, throttling, timeout, sieć.

    Adapter tłumaczy tu błąd transportu (401/403/429/timeout), żeby narzędzie zwróciło czytelny
    komunikat zamiast surowego ``httpx.HTTPError``. Osobny od ``WriteError`` — to ścieżka ODCZYTU,
    nic nie mutuje; osobny od ``RepositoryError`` — źródło jest zewnętrznym API, nie magazynem
    lokalnym.
    """


class ScheduleReadError(WorkMateError):
    """Odczyt grafiku Shifts (ADR 0059) się nie udał: brak/wygaśnięcie cichego tokenu, brak zgody
    (Schedule.Read.All), throttling, timeout albo sieć.

    Adapter tłumaczy tu błąd cichego uwierzytelnienia (cudzy cache MSAL powiadomienia-teams) i
    transportu Graph, żeby narzędzie zwróciło czytelny komunikat zamiast surowego wyjątku. Osobny od
    ``JiraReadError`` (inne źródło i inna podpowiedź naprawcza) — degraduje łagodnie na granicy.
    """


class ThreadRootGone(WorkMateError):
    """Root wątku na kanale Teams już nie istnieje (odpowiedź na usunięty post, ADR 0024).

    Adapter ``TeamsNotifier`` podnosi to, gdy ``reply_channel`` dostaje 404 (człowiek/retencja
    usunęły root). Notifier łapie i degraduje: tworzy NOWY root i przełącza link — dzięki temu
    usunięty wątek nie blokuje na trwałe całego strumienia zdarzeń (zamiast wywracać kursor).
    """


class ExecManagerError(WorkMateError):
    """Menedżer wykonawców (ADR infra 0012) nie zdołał zapewnić wykonawcy scope'a.

    Podnoszony przez ``ExecManagerService`` i klienta gniazda kontrolnego, gdy: scope nie przechodzi
    ścisłej walidacji (próba wstrzyknięcia montażu), silnik kontenerów odmawia startu, albo
    wykonawca nie wystawił gniazda w oknie gotowości. Osobny od ``RepositoryError``/``WriteError`` —
    to nie jest ani magazyn, ani zapis, tylko cykl życia kontenera. Klient ``Bash`` degraduje go do
    ``CommandResult`` z niezerowym kodem (jak każdą inną niedostępność wykonawcy, ADR 0057), więc
    tura agenta się nie wywraca — model poprawia się w następnej.
    """
