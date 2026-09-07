"""Pamięć grafiku dla kroku 1.5 — jednostkowo, bez nasłuchu (ADR 0009)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from powiadomienia_teams.domain.models import DaneTygodnia
from powiadomienia_teams.runtime.pamiec_grafiku import PamiecSamouzupelnien

UTC = timezone.utc
T0 = datetime(2026, 9, 7, 10, 0, tzinfo=UTC)


def _dane() -> DaneTygodnia:
    return DaneTygodnia(
        poniedzialek=datetime(2026, 9, 14, tzinfo=UTC), zmiany=(), wolne=(), wolne_dni={}
    )


def test_swiezy_wpis_nie_powoduje_pobrania():
    pamiec = PamiecSamouzupelnien()
    pamiec.przyjmij({"2026-09-14": _dane()}, teraz=T0)
    wolania: list[set[str]] = []

    wynik = pamiec.dla_samouzupelnienia(
        {"2026-09-14"},
        teraz=T0 + timedelta(hours=1),
        pobierz=lambda ws: (wolania.append(ws), {})[1],
    )

    assert wolania == []
    assert wynik["2026-09-14"] is not None


def test_po_ttl_wpis_jest_pobierany_ponownie():
    pamiec = PamiecSamouzupelnien(ttl_s=3600)
    pamiec.przyjmij({"2026-09-14": _dane()}, teraz=T0)
    wolania: list[set[str]] = []

    pamiec.dla_samouzupelnienia(
        {"2026-09-14"},
        teraz=T0 + timedelta(hours=2),
        pobierz=lambda ws: (wolania.append(ws), {w: _dane() for w in ws})[1],
    )

    assert wolania == [{"2026-09-14"}]


def test_odswiez_omija_swiezy_wpis():
    """Reguła, dzięki której bezpieczeństwo nie zależy od TTL — wpis po terminie czyta na nowo."""
    pamiec = PamiecSamouzupelnien()
    pamiec.przyjmij({"2026-09-14": _dane()}, teraz=T0)
    wolania: list[set[str]] = []

    pamiec.dla_samouzupelnienia(
        {"2026-09-14"},
        teraz=T0,
        pobierz=lambda ws: (wolania.append(ws), {w: _dane() for w in ws})[1],
        odswiez={"2026-09-14"},
    )

    assert wolania == [{"2026-09-14"}]


def test_porazka_nie_jest_zapamietywana():
    """Zapamiętana porażka na sześć godzin to wyłączony krok 1.5 na sześć godzin."""
    pamiec = PamiecSamouzupelnien()
    wolania: list[set[str]] = []

    def pobierz(ws: set[str]) -> dict[str, DaneTygodnia | None]:
        wolania.append(ws)
        return {w: None for w in ws}

    assert pamiec.dla_samouzupelnienia({"2026-09-14"}, teraz=T0, pobierz=pobierz) == {
        "2026-09-14": None
    }
    pamiec.dla_samouzupelnienia({"2026-09-14"}, teraz=T0 + timedelta(minutes=1), pobierz=pobierz)

    assert len(wolania) == 2, "porażka została zapamiętana"


def test_pobiera_wylacznie_brakujace_tygodnie():
    """Mieszanka świeżego i nieznanego: pobranie obejmuje tylko ten drugi."""
    pamiec = PamiecSamouzupelnien()
    pamiec.przyjmij({"2026-09-14": _dane()}, teraz=T0)
    wolania: list[set[str]] = []

    wynik = pamiec.dla_samouzupelnienia(
        {"2026-09-14", "2026-09-21"},
        teraz=T0,
        pobierz=lambda ws: (wolania.append(ws), {w: _dane() for w in ws})[1],
    )

    assert wolania == [{"2026-09-21"}]
    assert set(wynik) == {"2026-09-14", "2026-09-21"}


def test_pamiec_nie_umie_udawac_snapshotu():
    """Brak `dla_tygodnia`/`dla_tygodni` jest własnością KONSTRUKCYJNĄ, nie przeoczeniem."""
    pamiec = PamiecSamouzupelnien()
    assert not hasattr(pamiec, "dla_tygodnia")
    assert not hasattr(pamiec, "dla_tygodni")
