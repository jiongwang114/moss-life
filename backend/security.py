"""Bounded requests, private responses and rolling per-user usage limits."""
import asyncio
import json
import os
import time
from pathlib import Path

from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException

from . import database
from .domain import Problem, uid

MAX_INPUT_CHARS = int(os.getenv('MOSS_MAX_INPUT_CHARS', '20000'))
MAX_INPUT_BYTES = MAX_INPUT_CHARS * 4
MAX_TITLE_CHARS = 500
MAX_REQUEST_BYTES = MAX_INPUT_CHARS * 12 + 65536  # Includes JSON Unicode escaping.
AI_CALLS_PER_MINUTE = 36
EXPORT_TTL_SECONDS = 3600
CSP = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-src 'none'; frame-ancestors 'none'; object-src 'none'; base-uri 'none'; form-action 'self'"


def problem_response(e):
    return JSONResponse(status_code=e.status, content={'error': dict(
        code=e.code, message=e.code, retryable=e.status >= 500 or e.status == 429 or e.code in ('TITLE_BUSY', 'AUTH_REQUIRED'),
        request_id=uid(), record_id=e.record_id, operation_id=e.operation_id,
        related_export_id=e.export_id, record_acceptance=e.acceptance, modifiable_rejection=e.modifiable)})


class SecurityMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)

        async def secured_send(message):
            if message['type'] == 'http.response.start':
                headers = list(message.get('headers', []))
                headers += [(b'content-security-policy', CSP.encode()), (b'x-frame-options', b'DENY'),
                            (b'x-content-type-options', b'nosniff'), (b'referrer-policy', b'no-referrer')]
                if scope.get('scheme') == 'https':
                    headers.append((b'strict-transport-security', b'max-age=31536000'))
                if scope['path'].startswith('/api/'):
                    headers = [(k, v) for k, v in headers if k.lower() != b'cache-control']
                    headers.append((b'cache-control', b'no-store'))
                message = {**message, 'headers': headers}
            await send(message)

        if scope['method'] in ('POST', 'PUT', 'PATCH', 'DELETE'):
            length = dict(scope['headers']).get(b'content-length', b'0')
            try:
                too_large = int(length) > MAX_REQUEST_BYTES
            except ValueError:
                too_large = True
            body = bytearray()
            while not too_large:
                part = await receive()
                if part['type'] == 'http.disconnect':
                    return
                body.extend(part.get('body', b''))
                too_large = len(body) > MAX_REQUEST_BYTES
                if not part.get('more_body', False):
                    break
            if too_large:
                return await problem_response(Problem('REQUEST_TOO_LARGE', 413)).__call__(scope, receive, secured_send)
            delivered = False

            async def bounded_receive():
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {'type': 'http.request', 'body': bytes(body), 'more_body': False}
                return await receive()

            return await self.app(scope, bounded_receive, secured_send)
        await self.app(scope, receive, secured_send)


class PublicAssets(StaticFiles):
    """The same manifest controls HTTP and Android assets."""
    def __init__(self, directory):
        self.allowed = frozenset(json.loads((Path(directory) / 'assets.json').read_text(encoding='utf-8')))
        super().__init__(directory=directory, html=True)

    async def get_response(self, path, scope):
        if path not in self.allowed and path != '.':
            raise HTTPException(404)
        return await super().get_response(path, scope)


def reserve_usage(c, user, kind, limit, window=60):
    """Returns seconds until a slot opens; called inside the existing SQLite transaction."""
    stamp = time.time()
    c.execute('DELETE FROM usage_events WHERE at<=?', (stamp-window,))
    row = c.execute('SELECT count(*),min(at) FROM usage_events WHERE user_id=? AND kind=? AND at>?',
                    (user, kind, stamp-window)).fetchone()
    if row[0] >= limit:
        return max(.01, row[1]+window-stamp)
    c.execute('INSERT INTO usage_events VALUES(?,?,?)', (user, kind, stamp))
    return 0


async def limit_usage(user, kind, limit):
    wait = await database.tx(lambda c: reserve_usage(c, user, kind, limit))
    if wait:
        raise Problem('USAGE_RATE_LIMITED', 429)


async def acquire_ai(user):
    # Waiting keeps the current durable task pending; it does not discard the user's input.
    while True:
        wait = await database.tx(lambda c: reserve_usage(c, user, 'ai', AI_CALLS_PER_MINUTE))
        if not wait:
            return
        await asyncio.sleep(wait)
