"""Testy portu LLM — załączniki multimodalne (ADR 0016) i budżet materiałów tury (ADR 0064).

Czyste dataklasy i helpery serializacji (``Attachment``, ``attachment_to_row``/
``attachment_from_row``, ``UserText.attachments``) oraz ``AttachmentQueue`` — cały kontrakt
między narzędziem ``File`` a runtime'em. Bez SDK, bez sieci: sprawdzamy neutralną formę
(NIE bloki Anthropic), round-trip do wiersza ``blocks_json`` i arytmetykę budżetu.
"""

from __future__ import annotations

from sufler.core.ports.llm import (
    Attachment,
    AttachmentQueue,
    UserText,
    attachment_from_row,
    attachment_to_row,
)


def test_user_text_defaults_to_no_attachments_for_backward_compat():
    """``attachments`` jest addytywne (domyślnie puste) — stare wywołania bez zmian."""
    assert UserText("pytanie").attachments == ()


def test_user_text_carries_attachments_tuple():
    att = Attachment("image", "image/png", "zrzut.png", data_base64="QUJD")
    entry = UserText("opis", (att,))

    assert entry.text == "opis"
    assert entry.attachments == (att,)


def test_attachment_to_row_emits_neutral_dict_with_all_fields():
    """Wiersz to forma NEUTRALNA (nie blok Anthropic) — jedno źródło kształtu, 5 pól."""
    att = Attachment("document", "application/pdf", "umowa.pdf", data_base64="QkFTRTY0")

    row = attachment_to_row(att)

    assert row == {
        "kind": "document",
        "media_type": "application/pdf",
        "name": "umowa.pdf",
        "data_base64": "QkFTRTY0",
        "text": "",
    }


def test_attachment_round_trips_through_row_for_image():
    att = Attachment("image", "image/jpeg", "foto.jpg", data_base64="/9j/PQ==")

    assert attachment_from_row(attachment_to_row(att)) == att


def test_attachment_round_trips_through_row_for_extracted_docx_text():
    """.docx po ekstrakcji nosi tekst (nie base64) — round-trip zachowuje treść."""
    att = Attachment("text", "text/plain", "notatka.docx", text="Akapit\nWiersz | tabeli")

    assert attachment_from_row(attachment_to_row(att)) == att


def test_attachment_from_row_defaults_missing_keys_to_empty_strings():
    """Wiersz legacy/niepełny → puste pola (odczyt degraduje, nie rzuca)."""
    att = attachment_from_row({"kind": "image", "media_type": "image/png"})

    assert att == Attachment("image", "image/png", "", data_base64="", text="")


def test_attachment_from_row_ignores_unknown_keys():
    """Wiersz z przyszłego/obcego schematu nie może wywrócić odczytu ani przeciec do modelu."""
    row = {"kind": "text", "name": "x.docx", "text": "abc", "spurious": "ignore-me"}

    att = attachment_from_row(row)

    assert att == Attachment("text", "", "x.docx", text="abc")


# --- AttachmentQueue: kontrakt budżetu materiałów tury (ADR 0064) ---------------
# Kolejka jest CAŁYM kontraktem między narzędziem ``File`` (tylko dokłada) a runtime'em
# (tylko zabiera). Miała dotąd wyłącznie pokrycie pośrednie — przez narzędzie i runtime —
# więc jej własna arytmetyka budżetu nie była nigdzie zapisana wprost.


def test_queue_starts_with_the_whole_budget_and_nothing_pending():
    kolejka = AttachmentQueue(budget_bytes=1000)

    assert kolejka.remaining_bytes() == 1000
    assert kolejka.drain() == ()


def test_offer_within_budget_accepts_and_charges_the_size():
    kolejka = AttachmentQueue(budget_bytes=1000)
    att = Attachment("document", "application/pdf", "umowa.pdf", data_base64="QQ==")

    assert kolejka.offer(att, 400) is True
    assert kolejka.remaining_bytes() == 600
    assert kolejka.drain() == (att,)


def test_offer_of_exactly_the_remaining_budget_is_accepted():
    """Granica INKLUZYWNA: odcinamy dopiero powyżej — plik równy resztce jeszcze się mieści."""
    kolejka = AttachmentQueue(budget_bytes=100)

    assert kolejka.offer(Attachment("image", "image/png", "a.png"), 100) is True
    assert kolejka.remaining_bytes() == 0


def test_offer_one_byte_over_the_budget_is_refused_without_charging_it():
    """Odmowa nie może „zjeść" budżetu — inaczej jedna za duża prośba blokowałaby kolejne."""
    kolejka = AttachmentQueue(budget_bytes=100)

    assert kolejka.offer(Attachment("image", "image/png", "a.png"), 101) is False
    assert kolejka.remaining_bytes() == 100
    assert kolejka.drain() == ()


def test_a_refused_offer_leaves_room_for_a_smaller_one():
    """Sens zwracania ``False`` zamiast wyjątku: model ma spróbować czegoś mniejszego."""
    kolejka = AttachmentQueue(budget_bytes=100)
    maly = Attachment("image", "image/png", "maly.png")

    kolejka.offer(Attachment("document", "application/pdf", "duzy.pdf"), 500)

    assert kolejka.offer(maly, 40) is True
    assert kolejka.drain() == (maly,)


def test_drain_empties_the_queue_but_not_the_budget():
    """Budżet dotyczy CAŁEJ tury, a nie jednej rundy wywołań — zabranie plików go nie odnawia.

    Gdyby ``drain`` zerował licznik, model wysycałby żądanie, pobierając po jednym pliku na
    rundę: pętla narzędzi ma ich osiem.
    """
    kolejka = AttachmentQueue(budget_bytes=100)
    kolejka.offer(Attachment("image", "image/png", "a.png"), 60)

    assert len(kolejka.drain()) == 1
    assert kolejka.drain() == ()
    assert kolejka.remaining_bytes() == 40


def test_queue_preserves_the_order_in_which_files_were_offered():
    kolejka = AttachmentQueue(budget_bytes=1000)
    pierwszy = Attachment("image", "image/png", "1.png")
    drugi = Attachment("document", "application/pdf", "2.pdf")

    kolejka.offer(pierwszy, 10)
    kolejka.offer(drugi, 10)

    assert kolejka.drain() == (pierwszy, drugi)


def test_a_zero_budget_refuses_everything_that_costs_anything():
    """Drzwi POMNIEJSZAJĄ budżet o to, co same wstawiły — wyczerpany do zera jest realny."""
    kolejka = AttachmentQueue(budget_bytes=0)

    assert kolejka.offer(Attachment("image", "image/png", "a.png"), 1) is False
    assert kolejka.offer(Attachment("text", "text/plain", "a.txt"), 0) is True
