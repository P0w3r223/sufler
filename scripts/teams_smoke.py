"""Smoke round-tripu drzwi Teams (Etap A) — odtwarza to, co Bot Framework Emulator.

Weryfikuje transport BEZ Azure i BEZ GUI Emulatora:
1. stawia lokalny „sink" connectora (serviceUrl), do którego bot odsyła odpowiedź,
2. POST-uje aktywność ``message`` na ``/api/messages`` bota (jak Emulator),
3. czeka, aż bot odeśle odpowiedź na sink, i weryfikuje treść echa.

Wymaga: działającego bota anonimowego (``scripts/run-teams-anon.ps1``) oraz extra
``teams`` (``uv sync --extra teams``). Uruchomienie:

    uv run --no-sync python scripts/teams_smoke.py [BOT_PORT]
"""

from __future__ import annotations

import asyncio
import sys

from aiohttp import ClientSession, web

BOT_PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 3978
BOT_URL = f"http://localhost:{BOT_PORT}/api/messages"
SINK_HOST = "127.0.0.1"
SINK_PORT = 39785
NOTE_TEXT = "notatka ze spotkania z mpwik"
EXPECTED = f"Odebrałem notatkę: {NOTE_TEXT}"

captured: dict[str, object] = {}
reply_event = asyncio.Event()


async def sink_handler(request: web.Request) -> web.Response:
    """Sink connectora: przyjmuje POST bota (dowolna ścieżka /v3/...), łapie ``text``."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    captured["path"] = request.path
    captured["body"] = body
    if isinstance(body, dict) and body.get("text") is not None:
        captured["text"] = body["text"]
        reply_event.set()
    return web.json_response({"id": "sink-1"}, status=200)


async def main() -> int:
    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", sink_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, SINK_HOST, SINK_PORT).start()
    print(f"[sink] connector-sink nasłuchuje na http://{SINK_HOST}:{SINK_PORT}")

    activity = {
        "type": "message",
        "id": "1",
        "timestamp": "2026-07-08T09:00:00.000Z",
        "channelId": "emulator",
        "serviceUrl": f"http://{SINK_HOST}:{SINK_PORT}",
        "from": {"id": "user1", "name": "Tester"},
        "conversation": {"id": "conv1"},
        "recipient": {"id": "bot1", "name": "Sufler"},
        "text": NOTE_TEXT,
        "locale": "pl-PL",
    }

    async with ClientSession() as session:
        try:
            async with session.post(BOT_URL, json=activity) as resp:
                status = resp.status
                body_text = await resp.text()
        except Exception as exc:
            print(f"[bot] BŁĄD połączenia z {BOT_URL}: {exc}")
            await runner.cleanup()
            return 2

    print(f"[bot] POST /api/messages -> HTTP {status} {body_text!r}")
    if status == 401:
        print("[WYNIK] 401 — bot działa w trybie UWIERZYTELNIONYM (nie anonimowym).")
        await runner.cleanup()
        return 3
    if status >= 400:
        print(f"[WYNIK] nieoczekiwany status {status}.")
        await runner.cleanup()
        return 6

    try:
        await asyncio.wait_for(reply_event.wait(), timeout=10)
    except TimeoutError:
        print("[WYNIK] TIMEOUT — bot nie odesłał odpowiedzi na serviceUrl w 10 s.")
        print(f"[sink] ostatni odebrany body: {captured.get('body')}")
        await runner.cleanup()
        return 4

    reply = captured.get("text")
    print(f"[sink] odebrano na {captured.get('path')!r}: text={reply!r}")
    print(f"[oczekiwano]           text={EXPECTED!r}")
    ok = reply == EXPECTED
    print("[WYNIK] ROUND-TRIP OK" if ok else "[WYNIK] NIEZGODNOSC TRESCI")
    await runner.cleanup()
    return 0 if ok else 5


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
