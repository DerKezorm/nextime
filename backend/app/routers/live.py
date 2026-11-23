"""``GET /api/live``: the stream an open page listens to (Server-Sent Events). It carries only "changed": the page then
asks for its calendars and events again (TIME-20 AC-4). A comment every 20 seconds keeps proxies from closing it; a
session that ended closes it, and the page falls back to asking now and then until it can listen again."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from ..db import SessionLocal
from ..deps import Account
from ..security import SESSION_COOKIE, session_account
from ..services import live

router = APIRouter(tags=["live"])

#: Seconds between two keep-alive comments; each also checks that the session still holds.
BEAT = 20


def _still_signed_in(cookie: str | None, account_id: int) -> bool:
    with SessionLocal() as db:
        found = session_account(db, cookie)
        return found is not None and found.id == account_id


@router.get("/api/live", summary="Changes of the own calendars as they happen (Server-Sent Events)")
async def stream(request: Request, account: Account) -> StreamingResponse:
    cookie = request.cookies.get(SESSION_COOKIE)
    entry = live.subscribe(account.id)

    async def events() -> AsyncIterator[bytes]:
        try:
            yield b"retry: 5000\n: listening\n\n"
            while True:
                try:
                    message = await asyncio.wait_for(entry[1].get(), BEAT)
                except TimeoutError:
                    if await request.is_disconnected():
                        return
                    if not await asyncio.to_thread(_still_signed_in, cookie, account.id):
                        return
                    yield b": beat\n\n"
                    continue
                yield f"event: {message}\ndata: {{}}\n\n".encode()
        finally:
            live.unsubscribe(account.id, entry)

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})
