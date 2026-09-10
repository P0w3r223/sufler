"""Protokół zdarzeń postępu — `client` i `store` nie znają `rich` ani konsoli."""

from __future__ import annotations

from typing import Protocol


class Events(Protocol):
    """Odbiorca zdarzeń; implementacja konsolowa powstaje w fazie 3."""

    def on_request(self, endpoint: str, status: int, elapsed_s: float) -> None: ...

    def on_wait(self, seconds: float, reason: str, resume_at_epoch: float) -> None: ...

    def on_page(self, page_index: int, records: int, total: int | None) -> None: ...

    def on_details(self, done: int, total: int) -> None: ...

    def on_export(self, done: int, total: int) -> None: ...

    def on_download(self, done_bytes: int, total_bytes: int | None) -> None: ...

    def on_model(self, elapsed_s: float, tokens: int) -> None: ...

    def on_message(self, text: str) -> None: ...

    def close(self) -> None:
        """Kończy żywy pasek postępu.

        Należy do protokołu, bo pasek musi zgasnąć **zanim** cokolwiek innego trafi na ekran.
        Dopóki `close()` wywoływał tylko `finally` w `cli.py`, w kreatorze pasek żył do końca
        sesji i nadpisywał podsumowanie, pytanie o zapis i menu — program czekał na odpowiedź,
        której nie było widać, i wyglądał na zawieszony."""
        ...


class NullEvents:
    """Odbiorca, który nic nie robi — domyślny w testach i w bibliotece."""

    def on_request(self, endpoint: str, status: int, elapsed_s: float) -> None:
        return None

    def on_wait(self, seconds: float, reason: str, resume_at_epoch: float) -> None:
        return None

    def on_page(self, page_index: int, records: int, total: int | None) -> None:
        return None

    def on_details(self, done: int, total: int) -> None:
        return None

    def on_export(self, done: int, total: int) -> None:
        return None

    def on_download(self, done_bytes: int, total_bytes: int | None) -> None:
        return None

    def on_model(self, elapsed_s: float, tokens: int) -> None:
        return None

    def on_message(self, text: str) -> None:
        return None

    def close(self) -> None:
        return None
