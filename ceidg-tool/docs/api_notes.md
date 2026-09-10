# CEIDG API v3 — notes from the official integrator documentation

Date: 2026-09-05
Status: accepted
Author: P0w3r223
Related to: docs/reference/HD_CEIDG_API_v3_dokumentacja_v1.0.pdf (v1.0, 2024-10-21, Centrum Informatyki ZETO S.A.)

---

Facts stated by the documentation. Anything the documentation leaves open is
listed at the end and must be settled by the probe (`scripts/ceidg_probe.py`),
with answers recorded in `docs/decisions.md`.

## Environments and auth

| Item | Value |
|---|---|
| Test base URL | `https://test-dane.biznes.gov.pl/api/ceidg/v3` |
| Production base URL | `https://dane.biznes.gov.pl/api/ceidg/v3` |
| Auth | `Authorization: Bearer <JWT>` on every request |
| Transport | HTTPS, TLS 1.2, .NET Core / OpenAPI backend, nginx in front |
| Usage logging | Requests are stored by the provider for 36 months |

## Rate limits (both windows apply at the same time)

| Window | Limit |
|---|---|
| 3 minutes (180 s) | 50 requests |
| 60 minutes | 1000 requests |

After the 3-minute limit is exceeded the API blocks for 180 s **counted from
the last request**, so the client must send nothing during the block, otherwise
the block keeps extending. Recommended constant spacing: 3.6 s between requests.

## Casing

Parameter names **and values** are case-sensitive. Documentation uses lowercase
parameter names; example values are uppercase for `status` and `wojewodztwo`
(`PODLASKIE` in responses). Exact behaviour for lowercase values is a probe question.

## `GET /firmy` — list of firms

List parameters (`nip[]`, `nazwa[]`, …) are passed as repeated query keys:
`nip=1&nip=2`. Parameters: `nip[]`, `regon[]`, `nip_sc[]`, `regon_sc[]`,
`imie[]`, `nazwisko[]`, `nazwa[]`, `ulica[]`, `budynek[]`, `lokal[]`, `miasto[]`,
`wojewodztwo[]`, `powiat[]`, `gmina[]`, `kod[]` (postal code), `pkd[]`
(example `pkd=9312Z`, no dots), `page`, `limit`, `dataod`, `datado`
(business start date range, `YYYY-MM-DD`), `status[]`.

Allowed `status` values: `AKTYWNY`, `WYKRESLONY`, `ZAWIESZONY`,
`OCZEKUJE_NA_ROZPOCZECIE_DZIALANOSCI`, `WYLACZNIE_W_FORMIE_SPOLKI`.

Response shape:

```
{
  "firmy": [ { id, nazwa, adresDzialalnosci{ulica,budynek,lokal,miasto,wojewodztwo,
               powiat,gmina,kraj,kod,terc,simc,ulic,skrytkaPocztowa,
               opisNietypowegoMiejsca,adresat},
               wlasciciel{imie,nazwisko,nip,regon}, dataRozpoczecia, status, link } ],
  "count": <number of entries matching the criteria>,
  "links": { next, prev, self, first, last },
  "properties": { "dc:title", "dc:description", "dc:language", "schema:provider",
                  "schema:datePublished" }
}
```

The documented example shows `links.*` URLs with `limit=25&page=0`, which
suggests page numbering starts at 0 and the default page size is 25. When no
explicit `status` is given the server appends all five statuses to the links.

## `GET /firma` — firm details

Variants: `/firma/{id}`, `/firma?nip=…`, `/firma?regon=…`, `/firma?ids[]=a&ids[]=b`.
Response is `{ "firma": [ … ], "properties": {…} }` — a list even for a single id.

Detail fields (beyond the list fields): `wlasciciel.nipUchylony`,
`wlasciciel.nipUniewazniony`, `adresKorespondencyjny` (same structure as
`adresDzialalnosci`), `adresyDzialalnosciDodatkowe[]`, `obywatelstwa[]{symbol,kraj}`,
`dataZawieszenia`, `dataZakonczenia`, `dataWykreslenia`, `dataWznowienia`,
`numerStatusu`, `telefon`, `email`, `www`, `adresDoreczenElektronicznych`,
`innaFormaKontaktu`, `wspolnoscMajatkowa` (0 no / 1 yes / 2 n.a.),
`wspolnoscMajatkowaDataUstania`, `dataZgonu`, `zarzadSukcesyjnyDataUstanowienia`,
`zarzadSukcesyjnyDataWygasniecia`, `podstawyPrawneWykreslenia[]`, `rokPkd`,
`pkd[]{kod,nazwa}`, `pkdGlowny{kod,nazwa}`, `spolki[]{nip,regon,zawieszenia}`,
`zakazy[]{typ,opis,okres,dataWydania,dataUprawomocnienia}`,
`upadlosc{rodzajInformacji,dataOrzeczenia,imie,nazwisko,nip}`,
`zarzadcaSukcesyjny{…}`, `kwalifikacjeZawodowe[]`, `uprawnienia[]`,
`ograniczenia[]`, `ograniczeniaZdolnosciPrawnej{…}`, `link`.

Note: the documentation lists `pkd.symbol` / `pkdGlowny.symbol` in the field
table but the JSON example uses `kod`. The probe samples decide.

## `GET /raporty` — ready-made reports

Parameters: `dataod`, `datado` (creation date range). Response
`{ "raporty": [ { id, nazwa, raport (download URL), "data-utworzenia", format } ] }`.
Example names: "Złożone wnioski - województwo zachodniopomorskie",
"Zarejestrowane działalności - województwo dolnośląskie"; format `.csv`.
Note the hyphenated key `data-utworzenia`.

## `GET /raport/{id}` — report download

Returns `application/octet-stream;charset=UTF-8`, a ZIP archive
(`content-disposition: attachment; filename="<uuid>.zip"`), ~3 MB in the example.

## `GET /zmiana` — changed entries

Parameters: `dataod`, `datado` (`YYYY-MM-DD` or `YYYY-MM-DD HH:mm:ss`; date-only
expands to 00:00:00 / 23:59:59), `page`, `limit` (example `limit=500`).
Recommended range: at most 5 days. Response
`{ "identyfikatoryWpisow": [ …ids… ], "count", "links", "properties" }`.
Example `links` again use `page=0`.

## Response codes (all endpoints)

| Code | Meaning |
|---|---|
| 200 | OK |
| 204 | No data for the given criteria (empty body) |
| 400 | Malformed request |
| 401 | Unauthorized (bad/expired token or wrong environment) |
| 403 | Forbidden |
| 404 | Resource does not exist |
| 429 | Too many requests |
| 500 | Internal server error |

## Left open by the documentation (probe questions)

- Whether `page` numbering starts at 0 (examples suggest yes) or 1.
- Maximum effective `limit` for `/firmy`.
- Whether an empty result is 204 with empty body or 200 with `firmy: []`.
- Whether `count` is the total number of hits or the page size.
- Whether `/firma?ids[]=…` returns many firms per request and how many ids it accepts.
- Semantics of `nazwa` (substring vs exact, case, Polish diacritics).
- Accepted `pkd` format and value casing for `wojewodztwo` / `status`.
- Contents, cadence, columns, separator and encoding of `/raporty` CSVs.
- Which detail fields are actually populated (`email`, `telefon`, `www`).
