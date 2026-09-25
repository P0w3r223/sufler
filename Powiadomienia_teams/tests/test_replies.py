# 0.2.19 zastąpiło `newest_incoming` (jedna najnowsza wiadomość WSKAZANEJ osoby) przez
# `incoming_after` (WSZYSTKIE wiadomości od najstarszej). Zmiana jest celowa i naprawia realny
# błąd: pracownik piszący w dwóch dymkach był interpretowany tylko z drugiego. Zabrała jednak
# ze sobą dwa zabezpieczenia — tożsamość nadawcy i odporność na wielkość liter — PRZYWRÓCONE
# w fali 2 (`domain/tozsamosc.py`, wymagany parametr `nadawca`).
from powiadomienia_teams.reminders.replies import (
    MEMORY_CAP,
    MEMORY_WINDOW,
    advance_memory,
    history_for_llm,
    incoming_after,
    is_pure_affirmation,
    message_text,
    obcy_nadawcy,
)


def _msg(sender: str, created: str, content: str = "") -> dict:
    return {
        "from": {"user": {"id": sender}},
        "createdDateTime": created,
        "body": {"content": content, "contentType": "html"},
    }


def test_message_text_strips_html():
    assert message_text(_msg("u1", "t", "<p>w piątek <b>10-20</b></p>")) == "w piątek  10-20"


def test_incoming_after_ignores_own_and_old():
    me = "me"
    messages = [
        _msg("me", "2026-07-19T16:00:00Z"),  # nasze — pominięte
        _msg("u1", "2026-07-19T17:00:00Z", "stara"),  # przed watermarkiem
        _msg("u1", "2026-07-19T18:00:00Z", "nowa"),
    ]
    nowe = incoming_after(messages, me, after_iso="2026-07-19T17:30:00Z", nadawca="u1")
    assert [message_text(m) for m in nowe] == ["nowa"]


def test_incoming_after_empty_when_only_own():
    assert incoming_after([_msg("me", "2026-07-19T18:00:00Z")], "me", nadawca="u1") == []


def test_incoming_after_bierze_CALA_porcje_od_najstarszej():
    """Sedno zmiany wobec `newest_incoming`: dwa dymki pod rząd to jedna wypowiedź.

    Wcześniej brana była wyłącznie najnowsza, a watermark przeskakiwał na nią — więc „pon-pt 8-16"
    wysłane chwilę przed „w piątek mnie nie będzie" ginęło bezpowrotnie i bot zapisywał sam urlop.
    """
    messages = [
        _msg("u1", "2026-07-19T18:00:00Z", "pon-pt 8-16"),
        _msg("u1", "2026-07-19T18:00:30Z", "w piątek mnie nie będzie"),
    ]
    nowe = incoming_after(messages, "me", nadawca="u1")
    assert [message_text(m) for m in nowe] == ["pon-pt 8-16", "w piątek mnie nie będzie"]


def test_incoming_after_compares_parsed_time_not_string():
    # '.' (0x2E) < 'Z' (0x5A) leksykograficznie przestawiłby te dwie w tej samej sekundzie
    msgs = [
        _msg("u1", "2026-07-19T18:00:00.500Z", "nowsza"),
        _msg("u1", "2026-07-19T18:00:00Z", "starsza"),
    ]
    nowe = incoming_after(msgs, "me", after_iso="2026-07-19T18:00:00Z", nadawca="u1")
    assert [message_text(m) for m in nowe] == ["nowsza"]


def test_incoming_after_pomija_wiadomosci_systemowe_bez_nadawcy():
    """Graph wstawia do wątku wpisy bez `from` — nie są odpowiedzią i nie mogą ruszyć watermarku."""
    messages = [{"createdDateTime": "2026-07-19T18:00:00Z", "body": {"content": "dołączono"}}]
    assert incoming_after(messages, "me", nadawca="u1") == []


def test_is_pure_affirmation_accepts_clean_yes():
    assert is_pure_affirmation("tak")
    assert is_pure_affirmation("Ok!")
    assert is_pure_affirmation("ok, dzięki")
    assert is_pure_affirmation("no dokładnie")


def test_is_pure_affirmation_rejects_correction_even_without_digits():
    # Regresja live: „Ok, ale nie będzie mnie w czwartek" NIE jest czystym potwierdzeniem —
    # zawiera poprawkę (brak cyfr), więc musi trafić do reinterpretacji.
    assert not is_pure_affirmation("Ok, ale nie będzie mnie w czwartek")
    assert not is_pure_affirmation("tak ale w piątek 10-20")
    assert not is_pure_affirmation("ok tylko środa wolne")
    assert not is_pure_affirmation("w piątek mnie nie będzie")


def test_advance_memory_starts_fresh_and_anchors_on_first_message():
    memory, started_at = advance_memory([], "", "2026-07-19T18:00:00Z", "pon-pt 8-16")
    assert memory == ["pon-pt 8-16"]
    assert started_at == "2026-07-19T18:00:00Z"


def test_advance_memory_appends_without_moving_anchor():
    memory, started_at = advance_memory(
        ["pon-pt 8-16"], "2026-07-19T18:00:00Z", "2026-07-19T18:05:00Z", "a w piątek zdalnie"
    )
    assert memory == ["pon-pt 8-16", "a w piątek zdalnie"]
    assert started_at == "2026-07-19T18:00:00Z"  # kotwica NIE przesuwa się przy każdej wiadomości


def test_advance_memory_caps_at_ten_dropping_oldest():
    memory = [f"msg{i}" for i in range(MEMORY_CAP)]
    new_memory, started_at = advance_memory(
        memory, "2026-07-19T18:00:00Z", "2026-07-19T18:05:00Z", "nowa"
    )
    assert len(new_memory) == MEMORY_CAP
    assert new_memory[0] == "msg1"  # najstarsza (msg0) odpadła
    assert new_memory[-1] == "nowa"
    assert started_at == "2026-07-19T18:00:00Z"  # kotwica przycięciem się nie rusza


def test_advance_memory_resets_after_window_expires():
    started = "2026-07-19T18:00:00Z"
    after_window = "2026-07-19T19:01:00Z"  # > 1h po kotwicy
    memory, started_at = advance_memory(["stare"], started, after_window, "nowe od zera")
    assert memory == ["nowe od zera"]
    assert started_at == after_window


def test_advance_memory_is_idempotent_for_same_inputs():
    args = (["a"], "2026-07-19T18:00:00Z", "2026-07-19T18:05:00Z", "b")
    assert advance_memory(*args) == advance_memory(*args)


def test_history_for_llm_returns_copy_of_memory_within_window():
    memory = ["a", "b"]
    history = history_for_llm(memory, "2026-07-19T18:00:00Z", "2026-07-19T18:30:00Z")
    assert history == ["a", "b"]
    assert history is not memory


def test_history_for_llm_empty_when_window_expired():
    history = history_for_llm(["a", "b"], "2026-07-19T18:00:00Z", "2026-07-19T19:01:00Z")
    assert history == []


def test_history_for_llm_empty_when_no_anchor():
    assert history_for_llm([], "", "2026-07-19T18:00:00Z") == []


def test_memory_window_is_one_hour():
    from datetime import timedelta

    assert timedelta(hours=1) == MEMORY_WINDOW


# ─────────────────────────────────────────────────────────────────────────────────────────────
# Dwa zabezpieczenia, których obraz 0.2.19 NIE MIAŁ — przywrócone w fali 2.
#
# Linia repozytorium napisała je 2026-08-18 wraz z opisem incydentu; obraz produkcyjny zbudowano
# 2026-08-20 z drzewa, w którym ich nigdy nie było (rozwidlenie, nie cofnięcie). Stały tu jako
# `xfail(strict=True)`, dopóki kodu nie było; XPASS zapalił CI dokładnie wtedy, gdy naprawa
# weszła, i wymusił zdjęcie markerów. To są dziś zwykłe testy przechodzące.
#
# Zostawiamy je razem z tym opisem, bo zapis wymagania jest wart tyle samo co jego spełnienie:
# kto zobaczy `nadawca` w sygnaturze, ma tu znaleźć powód, dla którego jest WYMAGANY.
# ─────────────────────────────────────────────────────────────────────────────────────────────


def test_kazda_wiadomosc_trafia_do_DOKLADNIE_JEDNEJ_z_dwoch_klasyfikacji():
    """Szew między `incoming_after` a `obcy_nadawcy`: żadnej szczeliny, żadnego nakładania.

    Obie funkcje klasyfikują nadawcę niezależnie, a `_process_pending` pyta o drugą dopiero wtedy,
    gdy pierwsza nic nie zwróciła. Dziś reguła jest ta sama, więc szczeliny nie ma — ale to dwie
    kopie jednego rozstrzygnięcia i nic nie pilnowało ich zgodności. Zmiana w jednej (np. lista
    dopuszczonych kont drugiego bota) po cichu otwierałaby lukę, którą ta para miała zamknąć:
    wiadomość nieprzypisana do nikogo znika bez śladu, a wpis wygasa jako „pracownik milczy".
    """
    guid = "AAAA1111-BBBB-2222-CCCC-333344445555"
    wiadomosci = [
        _msg(guid.upper(), "2026-07-19T18:00:00Z", "bot"),  # bot, inna wielkość liter
        _msg("u1", "2026-07-19T18:01:00Z", "pracownik"),
        _msg("U1", "2026-07-19T18:02:00Z", "pracownik, inaczej zapisany"),
        _msg("obcy", "2026-07-19T18:03:00Z", "intruz"),
        {"createdDateTime": "2026-07-19T18:04:00Z", "body": {"content": "systemowa"}},
        _msg("u1", "2026-07-19T17:00:00Z", "sprzed watermarku"),
    ]
    po = "2026-07-19T17:30:00Z"

    moje = incoming_after(wiadomosci, guid, po, nadawca="u1")
    cudze = obcy_nadawcy(wiadomosci, guid, po, nadawca="u1")

    tresci_moich = {message_text(m) for m in moje}
    assert tresci_moich == {"pracownik", "pracownik, inaczej zapisany"}
    assert cudze == ["obcy"]
    # Rozłączność: nikt nie jest jednocześnie adresatem i obcym.
    nadawcy_moich = {m["from"]["user"]["id"] for m in moje}
    assert not (nadawcy_moich & set(cudze))


def test_obcy_nadawcy_zbiera_wszystkich_bez_powtorzen():
    """Raport dla operatora: kto pisze w tym wątku poza rozmową — każdy RAZ.

    Jeden natręt piszący pięć razy nie ma dawać pięciu pozycji w alercie; dwie różne osoby mają
    dać dwie. Zwracamy postać SUROWĄ, bo operator ma zobaczyć dokładnie to, co przyszło z Graph,
    nawet jeśli porównywaliśmy znormalizowane.
    """
    messages = [
        _msg("Obcy-A", "2026-07-19T18:00:00Z", "raz"),
        _msg("obcy-a", "2026-07-19T18:01:00Z", "dwa"),  # ten sam, inna wielkość liter
        _msg("obcy-B", "2026-07-19T18:02:00Z", "trzy"),
        _msg("u1", "2026-07-19T18:03:00Z", "to ja"),  # adresat — nie jest obcy
        _msg("me", "2026-07-19T18:04:00Z", "bot"),  # bot — nie jest obcy
    ]
    assert obcy_nadawcy(messages, "me", nadawca="u1") == ["Obcy-A", "obcy-B"]


def test_obcy_nadawcy_pomija_wiadomosci_systemowe():
    """Wpis bez `from` (Graph wstawia je przy zmianach w wątku) nie jest obcym nadawcą.

    Gdyby był, KAŻDY wątek, w którym ktoś kiedyś zmienił nazwę, wisiałby w `UNKNOWN` na zawsze
    i nigdy nie dałoby się go domknąć.
    """
    messages = [{"createdDateTime": "2026-07-19T18:00:00Z", "body": {"content": "dołączono"}}]
    assert obcy_nadawcy(messages, "me", nadawca="u1") == []


def test_obcy_nadawcy_milczy_gdy_wszystko_w_porzadku():
    """Sonda w drugą stronę: zwykła rozmowa NIE może zapalać alertu."""
    messages = [
        _msg("u1", "2026-07-19T18:00:00Z", "pon-pt 8-16"),
        _msg("me", "2026-07-19T18:01:00Z", "potwierdzam?"),
    ]
    assert obcy_nadawcy(messages, "me", nadawca="u1") == []


def test_incoming_after_odrzuca_nadawce_spoza_pendingu():
    """Nadawcą MUSI być ta osoba, o której grafik pytamy — nie „ktokolwiek poza botem".

    Warunek „nie bot" wygląda równoważnie tylko dopóki czat jest 1:1, a tworzy go
    `graph.client.ensure_chat` jako `oneOnOne`. Gdy wątek stanie się grupowy (ktoś dodany,
    migracja czatu), cudza treść staje się „odpowiedzią pracownika": idzie do modelu, przesuwa
    watermark i może skończyć ZAPISEM W GRAFIKU tej osoby, a jej prawdziwa odpowiedź — starsza od
    przesuniętego watermarku — znika na zawsze.

    Wiadomości systemowe są dziś odsiewane (brak `from` → pominięte), więc realne ryzyko zawęża się
    do wątku, który przestał być 1:1. To jest założenie, nie gwarancja typu.
    """
    messages = [_msg("obcy", "2026-07-19T18:00:00Z", "w piątek 10-20")]
    assert incoming_after(messages, "me", nadawca="u1") == []
    # …i wołający MUSI się o tym dowiedzieć, bo pusta lista znaczy u niego „pracownik milczy",
    # a to jedyna przesłanka wygaszenia. Stąd osobna funkcja, nie sam odsiew.
    assert obcy_nadawcy(messages, "me", nadawca="u1") == ["obcy"]


def test_incoming_after_rozpoznaje_wlasna_wiadomosc_niezaleznie_od_wielkosci_liter():
    """GUID-y z Graph bywają w różnej wielkości liter, a `sender == me_id` porównuje znak w znak.

    Skutek rozjazdu jest gorszy niż zignorowanie wiadomości: bot bierze WŁASNY komunikat za
    odpowiedź pracownika, podaje go modelowi do interpretacji i przesuwa na nim watermark.
    """
    guid = "AAAA1111-BBBB-2222-CCCC-333344445555"
    messages = [_msg(guid.lower(), "2026-07-19T18:00:00Z", "nasza")]
    assert incoming_after(messages, guid, nadawca="u1") == []
    # To NASZA wiadomość, nie cudza — nie wolno jej zgłaszać jako obcego nadawcy.
    assert obcy_nadawcy(messages, guid, nadawca="u1") == []


def test_jest_uprzejmoscia_odroznia_grzecznosc_od_nowej_sprawy():
    from powiadomienia_teams.reminders.replies import jest_uprzejmoscia

    for tekst in ("dzięki", "Dziękuję bardzo!", "👍", "🙂🙂", "miłego weekendu", ""):
        assert jest_uprzejmoscia(tekst, z_potwierdzeniami=False), tekst
    # „ok" po wygaśnięciu to ZGODA na propozycję (tak uczy przypomnienie), po odmowie — grzeczność.
    assert not jest_uprzejmoscia("ok", z_potwierdzeniami=False)
    assert jest_uprzejmoscia("ok, dzięki", z_potwierdzeniami=True)
    for tekst in ("jednak pon-pt 8-16", "dzięki, w piątek 10-18", "ok zapisz", "8-16"):
        assert not jest_uprzejmoscia(tekst, z_potwierdzeniami=True), tekst
