"""Testy deterministycznego parsera mówców (``core/domain/transcript.py``, ADR 0047).

Parser jest jedynym źródłem nazwisk uczestników — kotwiczy notatkę anty-halucynacyjnie.
Testujemy oba formaty (dwukropek z ``vtt_to_text`` i Nazwa+czas z surowego eksportu Teams),
odsiewanie artefaktów oraz UCZCIWĄ degenerację (≤1 etykieta / rzadkie tury → generyk, nie
zmyślona liczba/rola). Funkcje czyste — bez I/O, bez Claude.
"""

from __future__ import annotations

from workmate.core.domain.transcript import (
    _NONE,
    _UNRECOGNIZED,
    parse_speaker_roster,
)


def test_colon_format_two_speakers_is_diarized():
    transcript = (
        "Anna Kowalska: Dzień dobry wszystkim.\n"
        "Jan Nowak: Cześć.\n"
        "Anna Kowalska: Zaczynajmy spotkanie.\n"
    )
    roster = parse_speaker_roster(transcript)

    assert roster.diarized is True
    assert roster.speakers == ("Anna Kowalska", "Jan Nowak")
    # Diaryzacja kompletna → sami rozpoznani, bez generyka.
    assert roster.participants() == ["Anna Kowalska", "Jan Nowak"]
    assert roster.allowed_names() == ("Anna Kowalska", "Jan Nowak")


def test_name_time_single_speaker_degenerates_honestly():
    # Realny kształt surowego eksportu Teams: jedna etykieta mówcy na całe spotkanie,
    # reszta tur sklejona (bez granic diaryzacji) — dokładnie plik z .docx.
    transcript = "Mikołaj Anonimowicz   0:03 " + ("dużo sklejonego tekstu wielu rozmówców. " * 40)
    roster = parse_speaker_roster(transcript)

    assert roster.speakers == ("Mikołaj Anonimowicz",)
    assert roster.diarized is False
    # NIE zgadujemy liczby ani ról — jeden uczciwy generyk obok znanej etykiety.
    assert roster.participants() == ["Mikołaj Anonimowicz", _UNRECOGNIZED]


def test_name_time_label_glued_to_text_is_recovered():
    # Realny .docx: timestamp SKLEJONY z pierwszym słowem tury ("0:03Firmach"). Etykieta
    # organizatora musi zostać odzyskana mimo braku spacji/granicy po czasie.
    transcript = "Mikołaj Anonimowicz   0:03Firmach to było oglądanie i tak dalej i tak dalej."
    roster = parse_speaker_roster(transcript)

    assert roster.speakers == ("Mikołaj Anonimowicz",)
    assert roster.diarized is False
    assert roster.participants() == ["Mikołaj Anonimowicz", _UNRECOGNIZED]


def test_name_time_hms_timestamp_is_recognized():
    # Format Nazwa H:MM:SS (dłuższe spotkania, godzina w znaczniku) — jawnie wspierany przez
    # regex ``(?::\d{2})?``, ale bez tego testu nieodróżnialny od regresu do samego M:SS.
    transcript = (
        "Anna Kowalska   1:02:03 pierwsza tura po godzinie.\n"
        "Jan Nowak   1:05:10 druga tura.\n"
        "Anna Kowalska   1:07:00 trzecia tura.\n"
    )
    roster = parse_speaker_roster(transcript)

    assert roster.speakers == ("Anna Kowalska", "Jan Nowak")
    assert roster.diarized is True


def test_number_longer_than_timestamp_is_not_a_speaker():
    # "12:345" NIE jest znacznikiem czasu (za długie) — lookahead (?!\d) odrzuca fałszywe trafienie.
    transcript = "Raport 12:345 pozycji w tabeli.\nDruga linia."
    roster = parse_speaker_roster(transcript)

    assert roster.speakers == ()


def test_no_labels_yields_none_generic():
    transcript = "To jest zwykły tekst bez żadnych etykiet mówców.\nDruga linia treści."
    roster = parse_speaker_roster(transcript)

    assert roster.speakers == ()
    assert roster.diarized is False
    assert roster.participants() == [_NONE]
    assert roster.allowed_names() == ()


def test_single_word_colon_artifact_is_filtered_out():
    # "Tak:" na początku wypowiedzi NIE jest mówcą (jednoczłonowe, jednorazowe) — odsiane.
    transcript = "Tak: zróbmy to.\nAnna Kowalska: dobrze.\nAnna Kowalska: potwierdzam.\n"
    roster = parse_speaker_roster(transcript)

    assert roster.speakers == ("Anna Kowalska",)  # bez "Tak"


def test_single_word_name_kept_when_repeated():
    # Jednoczłonowa etykieta powtórzona ≥2× to realny mówca (imię) — zatrzymana.
    transcript = "Anna: cześć.\nMarek: hej.\nAnna: ok.\nMarek: no dobrze.\n"
    roster = parse_speaker_roster(transcript)

    assert set(roster.speakers) == {"Anna", "Marek"}
    assert roster.diarized is True


def test_sparse_turns_over_long_text_not_diarized():
    # Dwie etykiety, ale monologi tak długie, że tury są zbyt rzadkie na kompletną diaryzację —
    # uczciwie dokładamy generyk (mogli być nienazwani rozmówcy).
    transcript = "Anna Kowalska: " + ("a" * 2500) + "\nJan Nowak: " + ("b" * 2500) + "\n"
    roster = parse_speaker_roster(transcript)

    assert roster.speakers == ("Anna Kowalska", "Jan Nowak")
    assert roster.diarized is False
    assert roster.participants()[-1] == _UNRECOGNIZED


def test_teams_system_lines_without_marker_are_ignored():
    # "Nazwa rozpoczęto transkrypcję" nie ma dwukropka ani czasu → to nie tura mówcy.
    transcript = (
        "Mikołaj Anonimowicz rozpoczęto transkrypcję\n"
        "Anna Kowalska: pierwsza wypowiedź.\n"
        "Jan Nowak: druga wypowiedź.\n"
        "Mikołaj Anonimowicz zatrzymano transkrypcję\n"
    )
    roster = parse_speaker_roster(transcript)

    assert roster.speakers == ("Anna Kowalska", "Jan Nowak")


def test_speaker_label_whitespace_is_normalized():
    transcript = "Anna    Kowalska: raz.\nAnna Kowalska: dwa.\n"
    roster = parse_speaker_roster(transcript)

    assert roster.speakers == ("Anna Kowalska",)
