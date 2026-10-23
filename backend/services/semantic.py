"""Incremental private search cache. Source records remain authoritative in moss.sqlite3."""
import asyncio
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import time
import uuid

from .. import database
from ..domain import Problem, dump

ROOT = Path(__file__).resolve().parents[2]


def model_profile():
    config=json.loads((ROOT/'smart-search'/'model-profiles.json').read_text(encoding='utf-8'))
    name=os.getenv('MOSS_SEARCH_PROFILE',config['default'])
    return name,config['profiles'][name]


class SearchUnavailable(Exception):
    pass


class NodeWorker:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.process = None
        self.downloader = None
        self.lock = asyncio.Lock()

    async def call(self, op, **arguments):
        async with self.lock:
            if not self.process or self.process.returncode is not None:
                node = os.getenv('MOSS_NODE_BINARY') or shutil.which('node')
                if not node:
                    raise SearchUnavailable('NODE_NOT_INSTALLED')
                env = dict(os.environ)
                env['MOSS_SEARCH_CACHE'] = str(self.directory / 'models')
                env['MOSS_SEARCH_DIRECTORY'] = str(self.directory)
                profile_name,profile=model_profile()
                local_model = self.directory / 'models' / profile['directory']
                if not env.get('MOSS_SEARCH_MODEL_DIR') and not local_model.joinpath('moss-model-manifest.json').exists():
                    self.downloader = await asyncio.create_subprocess_exec(
                        sys.executable, str(ROOT / 'smart-search' / 'download_model.py'),
                        '--directory', str(local_model), '--profile',profile_name, stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL)
                    try:
                        await asyncio.wait_for(self.downloader.wait(), 600)
                        if self.downloader.returncode != 0:
                            raise SearchUnavailable('MODEL_DOWNLOAD_FAILED')
                    except BaseException:
                        await self.close()
                        raise
                if local_model.joinpath('moss-model-manifest.json').exists():
                    env.setdefault('MOSS_SEARCH_MODEL_DIR', str(local_model))
                try:
                    self.process = await asyncio.create_subprocess_exec(
                        node, str(ROOT / 'smart-search' / 'worker.mjs'), cwd=ROOT,
                        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                        # Upstream can log arbitrary model internals. No journal content in logs.
                        stderr=asyncio.subprocess.DEVNULL, env=env,
                        limit=64 * 1024 * 1024)
                except OSError as exc:
                    raise SearchUnavailable('WORKER_START_FAILED') from exc
            identifier = uuid.uuid4().hex
            try:
                self.process.stdin.write((dump(dict(id=identifier, op=op, **arguments)) + '\n').encode())
                await self.process.stdin.drain()
                raw = await asyncio.wait_for(self.process.stdout.readline(), float(os.getenv('MOSS_SEARCH_TIMEOUT', '180')))
                response = json.loads(raw)
                if response.get('id') != identifier:
                    raise ValueError('Unexpected worker response')
                if response.get('error'):
                    raise SearchUnavailable(response['error'])
                return response['result']
            except asyncio.CancelledError:
                await self.close()
                raise
            except SearchUnavailable:
                raise
            except Exception as exc:
                await self.close()
                raise SearchUnavailable('WORKER_INTERRUPTED') from exc

    async def close(self):
        if self.downloader and self.downloader.returncode is None:
            self.downloader.kill()
            await self.downloader.wait()
        self.downloader = None
        if self.process and self.process.returncode is None:
            self.process.kill()
            await self.process.wait()
        self.process = None


class SemanticSearch:
    def __init__(self, catalog, worker=None, enabled=None):
        self.catalog = {tag['id']: tag for tag in catalog}
        self.enabled = os.getenv('MOSS_SEARCH_ENABLED', '0') == '1' if enabled is None else enabled
        self.include_tags = os.getenv('MOSS_SEARCH_INCLUDE_TAGS', '1') == '1'
        self.directory = database.DATA / '.smart-env'
        self.worker = worker or NodeWorker(self.directory)
        self.profile_name,self.profile=model_profile()
        # A change of model, input policy or segmentation invalidates only this derived cache.
        self.signature = hashlib.sha256(dump(dict(
            schema=2, model=self.profile,
            include_tags=self.include_tags, chunk_policy='tokens256-overlap40-v1',
        )).encode()).hexdigest()
        self.task = None
        self.wake = asyncio.Event()
        self.phase = 'starting' if self.enabled else 'disabled'
        self.error = None
        self.last_index_ms = None
        self.scope = os.getenv('MOSS_SEARCH_SCOPE', 'category')
        self.inventory_checked = False
        self.initialized = False
        self.maintenance = asyncio.Lock()

    @contextmanager
    def cache(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.directory / 'metadata.sqlite3', timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA busy_timeout=10000')
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def initialize(self):
        with self.cache() as c:
            c.execute('PRAGMA journal_mode=WAL')
            c.executescript('''
              CREATE TABLE IF NOT EXISTS documents(
                id TEXT PRIMARY KEY, user_id TEXT NOT NULL, fingerprint TEXT NOT NULL,
                signature TEXT NOT NULL, revision INTEGER NOT NULL, indexed_at REAL NOT NULL);
              CREATE TABLE IF NOT EXISTS chunks(
                id TEXT PRIMARY KEY, record_id TEXT NOT NULL, user_id TEXT NOT NULL,
                ordinal INTEGER NOT NULL, snippet TEXT NOT NULL);
              CREATE INDEX IF NOT EXISTS search_user ON chunks(user_id,record_id);
            ''')
        manifest = dict(format='moss-smart-search-v1', upstream_compatible=False,
                        model=self.profile['model'], signature=self.signature)
        (self.directory / 'manifest.json').write_text(dump(manifest), encoding='utf-8')

    def tag_paths(self, identifiers):
        paths = []
        for identifier in identifiers or []:
            chain = []
            current = self.catalog.get(identifier)
            while current:
                chain.append(current['name'])
                current = self.catalog.get(current['parent_id'])
            if chain:
                paths.append(' / '.join(reversed(chain)))
        return sorted(set(paths))

    def document(self, row):
        snapshot = json.loads(row['snapshot'])
        return dict(title=snapshot['title'], body=snapshot['body_markdown'],
                    tags=self.tag_paths(snapshot['tags']), include_tags=self.include_tags)

    def fingerprint(self, row):
        document = self.document(row)
        if not self.include_tags:
            document['tags'] = []
        return hashlib.sha256(dump(document).encode()).hexdigest()

    async def start(self):
        if not self.enabled:
            return
        self.task = asyncio.create_task(self.bootstrap())

    async def bootstrap(self):
        while not self.initialized:
            try:
                await self.prepare_cache()
                self.initialized = True
                self.error = None
            except asyncio.CancelledError:
                raise
            except Exception:
                self.error = 'SEARCH_STORAGE_UNAVAILABLE'
                self.phase = 'error'
                await asyncio.sleep(5)
        await self.run()

    async def prepare_cache(self):
        await asyncio.to_thread(self.initialize)
        # Reconcile after restart, including changes committed while the feature was disabled.
        def reconcile(c):
            with self.cache() as cache:
                known = {r['id']: r for r in cache.execute('SELECT * FROM documents')}
            present = set()
            for row in c.execute('SELECT * FROM records'):
                present.add(row['id'])
                old = known.get(row['id'])
                needs_update = row['deleted'] or (row['complete'] and (
                    not old or old['signature'] != self.signature or old['fingerprint'] != self.fingerprint(row)))
                if needs_update:
                    c.execute('INSERT INTO search_jobs(record_id,revision) VALUES(?,?) '
                              'ON CONFLICT(record_id) DO UPDATE SET revision=excluded.revision,retry_at=0,attempts=0,error=NULL',
                              (row['id'], row['revision']))
            with self.cache() as cache:
                for identifier in known.keys() - present:
                    cache.execute('DELETE FROM chunks WHERE record_id=?', (identifier,))
                    cache.execute('DELETE FROM documents WHERE id=?', (identifier,))
        await database.tx(reconcile)

    async def close(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        await self.worker.close()

    async def run(self):
        while True:
            job = None
            try:
                if not self.inventory_checked:
                    self.phase = 'loading_model'
                    inventory = await self.worker.call('inventory')
                    def repair(c):
                        with self.cache() as cache:
                            known = cache.execute('SELECT * FROM documents').fetchall()
                        for document in known:
                            actual = inventory.get(document['id'])
                            with self.cache() as cache:
                                expected_chunks = cache.execute('SELECT count(*) FROM chunks WHERE record_id=?', (document['id'],)).fetchone()[0]
                            if not actual or not actual['valid'] or actual['chunks'] != expected_chunks or actual['fingerprint'] != document['fingerprint'] or actual['signature'] != self.signature:
                                with self.cache() as cache:
                                    cache.execute('DELETE FROM documents WHERE id=?', (document['id'],))
                                    cache.execute('DELETE FROM chunks WHERE record_id=?', (document['id'],))
                                row = c.execute('SELECT revision FROM records WHERE id=?', (document['id'],)).fetchone()
                                if row:
                                    c.execute('INSERT OR REPLACE INTO search_jobs(record_id,revision) VALUES(?,?)', (document['id'], row['revision']))
                        for identifier in inventory:
                            if not c.execute('SELECT 1 FROM records WHERE id=? AND deleted=0 AND complete=1', (identifier,)).fetchone():
                                row = c.execute('SELECT revision FROM records WHERE id=?', (identifier,)).fetchone()
                                c.execute('INSERT OR REPLACE INTO search_jobs(record_id,revision) VALUES(?,?)', (identifier, row['revision'] if row else 0))
                    await database.tx(repair)
                    self.inventory_checked = True
                    self.phase = 'ready'
                job = await database.tx(lambda c: c.execute(
                    'SELECT * FROM search_jobs WHERE retry_at<=? ORDER BY retry_at,record_id LIMIT 1',
                    (time.time(),)).fetchone())
                if job:
                    started = time.monotonic()
                    await self.process(dict(job))
                    self.last_index_ms = round((time.monotonic() - started) * 1000)
                    self.error = None
                    self.phase = 'ready'
                    continue
                self.wake.clear()
                try:
                    await asyncio.wait_for(self.wake.wait(), 1)
                except asyncio.TimeoutError:
                    pass
            except asyncio.CancelledError:
                raise
            except Exception:
                self.error = 'SEARCH_PROCESSING_FAILED'
                self.phase = 'error'
                if job:
                    await database.tx(lambda c: c.execute(
                        'UPDATE search_jobs SET attempts=attempts+1,retry_at=?,error=? WHERE record_id=? AND revision=?',
                        (time.time() + min(60, 2 ** min(job['attempts'] + 1, 6)), self.error, job['record_id'], job['revision'])))
                else:
                    self.inventory_checked = False
                await asyncio.sleep(1)

    async def process(self, job):
        async with self.maintenance:
            await self._process(job)

    async def purge_user(self, user, identifiers):
        async with self.maintenance:
            # Load the engine only if there is a running worker or a persisted engine.
            if identifiers and (self.enabled or (self.directory / 'engine').exists()):
                for identifier in identifiers:
                    await self.worker.call('remove', record_id=identifier)
            if (self.directory / 'metadata.sqlite3').exists():
                def clean():
                    with self.cache() as cache:
                        cache.execute('DELETE FROM chunks WHERE user_id=?',(user,))
                        cache.execute('DELETE FROM documents WHERE user_id=?',(user,))
                await asyncio.to_thread(clean)

    async def _process(self, job):
        row = await database.tx(lambda c: c.execute('SELECT * FROM records WHERE id=?', (job['record_id'],)).fetchone())
        row = dict(row) if row else None
        eligible = row and not row['deleted'] and row['complete']
        fingerprint = self.fingerprint(row) if eligible else None
        def already_indexed():
            with self.cache() as c:
                old = c.execute('SELECT fingerprint,signature FROM documents WHERE id=?', (job['record_id'],)).fetchone()
                return bool(old and old['fingerprint'] == fingerprint and old['signature'] == self.signature)
        unchanged = eligible and await asyncio.to_thread(already_indexed)
        chunks = None
        if eligible and not unchanged:
            self.phase = 'indexing'
            chunks = await self.worker.call('upsert', record_id=row['id'], user_id=row['user_id'],
                                            fingerprint=fingerprint, signature=self.signature, **self.document(row))
            if not chunks:
                raise SearchUnavailable('EMPTY_VECTORS')
        elif not eligible:
            await self.worker.call('remove', record_id=job['record_id'])

        def commit(c):
            current = c.execute('SELECT * FROM records WHERE id=?', (job['record_id'],)).fetchone()
            # Do not install a stale result after edits/deletion during model inference.
            if row and (not current or current['revision'] != row['revision']):
                return
            with self.cache() as cache:
                if not eligible:
                    cache.execute('DELETE FROM chunks WHERE record_id=?', (job['record_id'],))
                    cache.execute('DELETE FROM documents WHERE id=?', (job['record_id'],))
                elif not unchanged:
                    cache.execute('DELETE FROM chunks WHERE record_id=?', (row['id'],))
                    cache.executemany('INSERT INTO chunks VALUES(?,?,?,?,?)', [
                        (chunk['id'], row['id'], row['user_id'], index, chunk['snippet'])
                        for index, chunk in enumerate(chunks)])
                    cache.execute('INSERT OR REPLACE INTO documents VALUES(?,?,?,?,?,?)',
                                  (row['id'], row['user_id'], fingerprint, self.signature, row['revision'], time.time()))
            c.execute('DELETE FROM search_jobs WHERE record_id=? AND revision=?', (job['record_id'], job['revision']))
        await database.tx(commit)

    async def status(self, user):
        if not self.enabled:
            return dict(enabled=False, state='disabled', indexed=0, total=0, pending=0, failed=0, scope=self.scope)
        if not self.initialized:
            total=await database.tx(lambda c:c.execute('SELECT count(*) FROM records WHERE user_id=? AND complete=1 AND deleted=0',(user,)).fetchone()[0])
            return dict(enabled=True,state='error' if self.error else 'starting',indexed=0,total=total,pending=total,failed=0,scope=self.scope)
        def read(c):
            rows = c.execute('SELECT * FROM records WHERE user_id=? AND complete=1 AND deleted=0', (user,)).fetchall()
            with self.cache() as cache:
                known = {r['id']: r for r in cache.execute('SELECT * FROM documents WHERE user_id=?', (user,))}
            indexed = sum(1 for row in rows if row['id'] in known and known[row['id']]['signature'] == self.signature
                          and known[row['id']]['fingerprint'] == self.fingerprint(row))
            failed = c.execute('SELECT count(*) FROM search_jobs j JOIN records r ON r.id=j.record_id '
                               'WHERE r.user_id=? AND r.deleted=0 AND r.complete=1 AND j.error IS NOT NULL', (user,)).fetchone()[0]
            return dict(enabled=True, state='error' if failed or self.error else self.phase,
                        indexed=indexed, total=len(rows), pending=len(rows)-indexed, failed=failed,
                        last_index_ms=self.last_index_ms, scope=self.scope)
        return await database.tx(read)

    async def search(self, user, text, tag_ids, limit):
        if not self.enabled:
            raise Problem('SEARCH_DISABLED', 503)
        if not self.initialized:
            raise Problem('SEARCH_UNAVAILABLE', 503)
        started = time.monotonic()
        try:
            # Read authoritative metadata before candidate selection; no model/network calls under transaction.
            def candidates(c):
                from ..query import where
                sql, args = where(user, 'category' if tag_ids else 'time', tag_ids)
                rows = c.execute('SELECT * FROM records WHERE ' + sql + ' AND complete=1', args).fetchall()
                valid = {r['id']: (dict(r), self.fingerprint(r)) for r in rows}
                allowed = {}
                with self.cache() as cache:
                    for row in cache.execute('SELECT * FROM documents WHERE user_id=?', (user,)):
                        source = valid.get(row['id'])
                        if source and source[1] == row['fingerprint'] and row['signature'] == self.signature:
                            allowed[row['id']] = row['fingerprint']
                return allowed
            allowed = await database.tx(candidates)
            if allowed:
                ranked = await self.worker.call('lookup', query=text, user_id=user, allowed=allowed,
                                                signature=self.signature, limit=limit)
            else:
                ranked = []
            def results(c):
                items, seen = [], set()
                for match in ranked:
                    entry = match
                    if entry['record_id'] in seen:
                        continue
                    row = c.execute('SELECT * FROM records WHERE id=? AND user_id=? AND deleted=0 AND complete=1', (entry['record_id'], user)).fetchone()
                    if not row:
                        continue
                    # Recheck after ranking: deletion, tag edits and title edits may have raced the query.
                    if entry['fingerprint'] != self.fingerprint(row) or allowed.get(row['id']) != entry['fingerprint']:
                        continue
                    snapshot = json.loads(row['snapshot'])
                    if not set(tag_ids).issubset(snapshot['tags'] or []):
                        continue
                    seen.add(row['id'])
                    snapshot.pop('body_markdown', None)
                    items.append(dict(record=snapshot, snippet=entry['snippet'], score=match['score']))
                    if len(items) == limit:
                        break
                return items
            items = await database.tx(results)
            return dict(items=items, status=await self.status(user), elapsed_ms=round((time.monotonic()-started)*1000))
        except SearchUnavailable as exc:
            if str(exc) == 'QUERY_TOO_LONG':
                raise Problem('SEARCH_QUERY_TOO_LONG', 422) from exc
            raise Problem('SEARCH_UNAVAILABLE', 503) from exc
