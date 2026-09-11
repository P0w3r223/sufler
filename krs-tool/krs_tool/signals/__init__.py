"""Warstwa sygnałów: reguły jako dane, wynik trójwartościowy.

Trzy reguły granic obowiązują w tym pakiecie i mają obserwatorów w `tests/test_granice.py`:

* **nie czyta zegara** — każda data pochodzi z odpisu, więc jedyne zdanie, jakie da się tu
  wypowiedzieć, brzmi „według stanu rejestru na dzień D" (reguła 4);
* **nie ma literału liczbowego innego niż 0 i 1** — sześć miesięcy i piętnaście dni nie mają
  gdzie zamieszkać poza katalogiem, a „31 grudnia" nie da się napisać (reguła 10);
* **nie ma słowa, którym dałoby się kogoś oskarżyć** — leksykon jest zamknięty i skanowany
  (reguła 11).
"""

from __future__ import annotations
