"""Testy czystych helperów klienta Graph (bez sieci).

``HttpxGraphChannelClient`` to cienki adapter na ``httpx`` (I/O testowane osobno/smoke),
ale kodowanie share id dla pobrania pliku z SharePoint to czysta, łatwa do pomyłki logika.
"""
from __future__ import annotations

import base64

from workmate.adapters.inbound.teams_graph.graph import _encode_share_id


def test_encode_share_id_uses_u_prefix_urlsafe_base64_without_padding():
    url = "https://contoso.sharepoint.com/sites/Team/Shared Documents/General/raport.pdf"

    share_id = _encode_share_id(url)

    assert share_id.startswith("u!")
    body = share_id[2:]
    assert "=" not in body  # dopełnienie usunięte (wymóg Graph)
    assert "+" not in body and "/" not in body  # alfabet urlsafe
    # Dekodowalne z powrotem do oryginalnego URL-a (po uzupełnieniu paddingu).
    padded = body + "=" * (-len(body) % 4)
    assert base64.urlsafe_b64decode(padded).decode("utf-8") == url
