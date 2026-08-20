"""Porty magazynów danych (interfejsy strukturalne).

Użycie ``Protocol`` zamiast klas bazowych oznacza, że dowolna klasa o zgodnych
sygnaturach jest akceptowana — bez dziedziczenia. Ułatwia to testy (proste
atrapy w pamięci) i podmianę implementacji.
"""

from __future__ import annotations

from typing import Protocol

from workmate.core.domain.models import Note, Project, ProjectStatusRecord


class NotesRepository(Protocol):
    """Dostęp *tylko do odczytu* do notatek ze spotkań."""

    def all(self) -> list[Note]:
        """Zwróć wszystkie notatki (Faza 1: mały zbiór, ładowany w całości)."""
        ...

    def get(self, note_id: str) -> Note | None:
        """Zwróć notatkę po identyfikatorze albo ``None``, gdy nie istnieje."""
        ...


class NotesWriter(Protocol):
    """Dostęp *do zapisu* notatek (Bramka 2, ADR 0006).

    Świadomie osobny od ``NotesRepository`` — dzięki temu odczyt pozostaje jawnie
    tylko-do-odczytu, a możliwość zapisu jest wstrzykiwana tylko tym drzwiom,
    które mają na nią pozwolenie (profil uprawnień per drzwi).
    """

    def exists(self, note_id: str) -> bool:
        """Czy notatka o danym identyfikatorze już istnieje (kontrola kolizji)?"""
        ...

    def write(self, note: Note) -> None:
        """Zapisz notatkę atomowo pod ścieżką z ``note.id`` — CREATE-ONLY.

        Kolizja to błąd, nigdy ciche nadpisanie: na tej własności stoi idempotencja notatek
        ze spotkania i wątku (ADR 0043/0048), więc ``write`` zostaje nietknięte przez ADR 0065.
        Mutacja ma WŁASNE czasowniki niżej.
        """
        ...

    def overwrite_body(self, note_id: str, body: str, *, expected_sha256: str) -> None:
        """Podmień TREŚĆ istniejącej notatki (ADR 0065) — nagłówek pliku zostaje NIETKNIĘTY.

        Bierze ``note_id`` i samą treść, a nie całą notatkę, bo tyle właśnie umie i tyle ma
        umieć. Poprzednia redakcja przyjmowała ``Note`` i składała plik od nowa przez
        ``render_note`` — czyli deklarowała „metadane zostają nietknięte", a w rzeczywistości
        przepisywała nagłówek z modelu przy KAŻDEJ edycji. Zmierzone: ginął komentarz YAML,
        ginęły pola spoza schematu (``status:``, ``source_url:``), dochodziły puste pola
        schematu, zmieniało się wcięcie list. Parametr, którego wołający nie może użyć, jest
        gorszy niż jego brak — stąd inna sygnatura, nie tylko inne ciało.

        Osobny rozmyślnie: gdyby ``write`` dostał flagę „nadpisuj", każdy dotychczasowy wołający
        (trzy ścieżki ``save_*``, seed korpusu, atrapy w testach) niósłby domyślnie zdolność,
        której nie potrzebuje — a jedna pomyłka cicho zamieniłaby zapis idempotentny w niszczący.
        Zapis jest atomowy (nowy plik + podmiana), nigdy w miejscu.

        ``expected_sha256`` to skrót pliku z chwili ODCZYTU. Między odczytem a zapisem leży
        wywołanie sędziego — sekundy — a drzwi obsługują tury równolegle. Bez tej kontroli dwie
        równoległe zmiany tej samej notatki nadpisywały się wzajemnie i wersja pośrednia ginęła
        BEZ MIGAWKI, czyli w jedynym stanie, którego ta warstwa ma nie dopuszczać.

        Treść niosącą WŁASNY nagłówek odrzuca (``odrzuc_wlasny_frontmatter``) — pisarz jest
        ostatnią bramą przed dyskiem, więc reguła stoi tu, a nie przy jednym wołającym.
        """
        ...

    def digest(self, note_id: str) -> str:
        """Skrót pliku notatki — znacznik wersji do optymistycznej kontroli współbieżności.

        Wołający bierze go PRZED długą operacją (ocena zmiany) i podaje przy zapisie; różnica
        znaczy „ktoś zmienił notatkę w międzyczasie" i zapis ma się wtedy nie odbyć.
        """
        ...

    def content_with_digest(self, note_id: str) -> tuple[str, str]:
        """Zwróć ``(treść pliku, skrót)`` z JEDNEGO odczytu bajtów; ``("", "")``, gdy się nie da.

        Istnieje dla migawki i to jest cała jego treść. Migawka ma być KOPIĄ pliku, a nie
        renderem z modelu: ``NoteMetadata`` ignoruje pola spoza schematu, więc `status:` czy
        `source_url:` dopisane ręcznie znikały z kopii bez śladu — przy ``delete``, gdzie migawka
        jest jedynym odzyskiem między nocnymi kopiami, znaczyło to utratę nieodwracalną.

        Jeden odczyt, nie dwa, bo skrót i materiał kopii MUSZĄ pochodzić z tej samej chwili.
        Przy dwóch odczytach między nimi mieści się równoległa tura: skrót opisywałby wtedy inną
        wersję pliku niż ta, którą zabezpiecza kopia. Tego domknięcia nie dało się zrobić bez
        nowego czasownika portu — stąd ten czasownik.

        ``("", "")`` znaczy „nie ma czego zabezpieczyć" (notatki brak albo bajty nie są poprawnym
        UTF-8) i jest dla wołającego ODMOWĄ, nie zachętą do pracy na domyśle. Błąd I/O jest
        głośny jak w ``digest`` — cisza w tym miejscu byłaby zgodą na mutację bez kopii.
        """
        ...

    def delete(self, note_id: str, *, expected_sha256: str) -> None:
        """Usuń POJEDYNCZĄ notatkę (ADR 0065). Nigdy katalog, nigdy wzorzec, nigdy rekurencyjnie.

        Wołający ma obowiązek zapisać migawkę PRZED wywołaniem — port jej nie robi, bo to
        decyzja polityki, a nie systemu plików.

        ``expected_sha256`` niesie tę samą kontrolę wersji, co ``overwrite``, i z tego samego
        powodu: usunięcie też dzieli odczyt od zapisu oceną sędziego (a przy werdykcie „confirm" —
        całą turą), a drzwi obsługują tury równolegle. Różnica skrótu znaczy „ktoś zmienił notatkę
        po tym, jak ją przeczytaliśmy" i usunięcie ma się wtedy NIE odbyć: migawka zabezpiecza
        wersję sprzed zmiany, więc bez tej kontroli wersja pośrednia ginęłaby bez kopii.
        """
        ...


class ProjectsRepository(Protocol):
    """Dostęp *tylko do odczytu* do rejestru projektów pionu."""

    def all(self) -> list[Project]:
        """Zwróć wszystkie projekty z rejestru."""
        ...

    def get(self, key: str) -> Project | None:
        """Zwróć projekt po kluczu albo ``None``."""
        ...

    def status_record(self, key: str) -> ProjectStatusRecord | None:
        """Zwróć zadeklarowany status projektu albo ``None``."""
        ...
