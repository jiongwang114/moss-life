import asyncio
import json
import os
import secrets
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime,timezone,timedelta,date
from pathlib import Path
from weakref import WeakValueDictionary
from fastapi import FastAPI,Request,Depends
from fastapi.responses import JSONResponse,StreamingResponse,FileResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException
from .database import initialize,tx,dump,DATA
from .domain import *
from .auth import password_hash,verify,session,token_hash,authenticate
from .auth import active_user,public_user
from . import accounts
from .providers import Provider
from .services.pipeline import Pipeline
from .services.exporter import run_export,release_export,invalidate_user_exports,cleanup_released_exports
from .services.semantic import SemanticSearch
from .events import bus
from .query import page,date_index
from .schemas import RecordSnapshot,RecordPage,OperationResponse,SubmissionResponse,PendingResponse,ExportTask
from .security import SecurityMiddleware,PublicAssets,problem_response,limit_usage,MAX_INPUT_CHARS,MAX_INPUT_BYTES,MAX_TITLE_CHARS,AI_CALLS_PER_MINUTE,EXPORT_TTL_SECONDS

CATALOG=json.loads((Path(__file__).parent/'catalog.json').read_text(encoding='utf-8'))
pipeline=Pipeline(Provider(CATALOG))
CONFIG=dict(contract_version='1.0',catalog_version=VERSION,limits=dict(max_input_chars=MAX_INPUT_CHARS,max_input_utf8_bytes=MAX_INPUT_BYTES,max_title_chars=MAX_TITLE_CHARS,limits_verified=False,ai_calls_per_minute=AI_CALLS_PER_MINUTE),pagination=dict(default_limit=20,max_limit=100),preview_max_chars=200,session=dict(access_ttl_seconds=3600,refresh_ttl_seconds=2592000),export=dict(temporary_ttl_seconds=EXPORT_TTL_SECONDS,max_active_jobs=1))

async def expire_files():
    while True:
        def clean(c):
            c.execute('DELETE FROM tokens WHERE expires<=?',(time.time(),))
            ids=[]
            for row in c.execute('SELECT * FROM exports WHERE path IS NOT NULL OR frozen IS NOT NULL').fetchall():
                e=json.loads(row['data'])
                deadline=datetime.fromisoformat(e['snapshot_at'])+timedelta(seconds=EXPORT_TTL_SECONDS)
                if e['expires_at']: deadline=min(deadline,datetime.fromisoformat(e['expires_at']))
                if e['status']=='expired' or deadline<=datetime.now(timezone.utc): ids.append(e['id'])
            return ids
        for eid in await tx(clean): await release_export(eid,pipeline)
        await asyncio.sleep(30)

@asynccontextmanager
async def lifespan(app):
    app.state.user_locks=WeakValueDictionary()
    app.state.password_slots=asyncio.Semaphore(2)
    app.state.dummy_password=password_hash(secrets.token_urlsafe(32))
    await asyncio.to_thread(initialize)
    # Exclusive process lock: in-memory bus and executor intentionally run as one worker.
    lock=(DATA/'worker.lock').open('a+b')
    try:
        try:
            if os.name=='nt':
                import msvcrt
                lock.seek(0); lock.write(b'0'); lock.flush(); lock.seek(0); msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            raise RuntimeError('Use one Uvicorn worker per MOSS_DATA_DIR')
        app.state.referral_key=await asyncio.to_thread(accounts.load_referral_key)
        def seed(c):
            # Seed once only: a deleted demo account must not reappear on restart.
            if not c.execute("SELECT 1 FROM settings WHERE key='accounts_seeded'").fetchone():
                initial_password=os.getenv('DEMO_PASSWORD','DEMO_PASSWORD')
                if os.getenv('MOSS_ENV')=='production' and (initial_password in ('DEMO_PASSWORD','moss') or len(initial_password)<10):
                    raise RuntimeError('Set a non-default DEMO_PASSWORD of at least 10 characters before initializing production')
                c.execute('INSERT OR IGNORE INTO users VALUES(?,?,?)',('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa','demo-user',password_hash(initial_password)))
                c.execute("INSERT INTO settings VALUES('accounts_seeded','1')")
            c.executemany('INSERT OR IGNORE INTO tags VALUES(?,?)',[(t['id'],dump(t)) for t in CATALOG])
            c.execute('INSERT OR IGNORE INTO settings VALUES(?,?)',('cursor_secret',secrets.token_hex(32)))
            return c.execute("SELECT value FROM settings WHERE key='cursor_secret'").fetchone()[0]
        app.state.secret=bytes.fromhex(await tx(seed))
        def recover(c):
            active=[]; occupied=set()
            for row in c.execute('SELECT * FROM records WHERE complete=0 AND deleted=0').fetchall():
                r=json.loads(row['snapshot']); changed=False
                for key,s in r['steps'].items():
                    if s['status']=='running' and not c.execute('SELECT 1 FROM candidates WHERE record_id=? AND step=?',(r['id'],key)).fetchone():
                        terminal=s['active_mode']=='manual' or s['automatic_attempts']>=3
                        s.update(status='awaiting_manual' if terminal else 'retry_wait',active_mode=None if terminal else 'automatic',last_error=info('WORKER_INTERRUPTED'),next_retry_at=None if terminal else now()); changed=True
                if changed: save_record(c,r)
            for row in c.execute('SELECT * FROM operations').fetchall():
                o=json.loads(row['data'])
                if o['status'] in ('queued','running'):
                    if o['kind']=='continue': active.append((row['user_id'],o)); occupied.add(o['record_id'])
                    elif o['kind']=='title_regenerate':
                        o.update(status='failed',error=info('WORKER_INTERRUPTED')); put_operation(c,o)
                        try:
                            r=record(c,row['user_id'],o['record_id']); r['last_title_operation']=title_summary(o); save_record(c,r)
                        except Problem: pass
            pending=[(r['user_id'],r['id']) for r in c.execute('SELECT * FROM records WHERE complete=0 AND deleted=0') if r['id'] not in occupied]
            exports=[r['id'] for r in c.execute('SELECT * FROM exports') if json.loads(r['data'])['status'] in ('queued','preparing')]
            return active,pending,exports
        active,pending,exports=await tx(recover)
        for user,o in active: pipeline.spawn(o['id'],pipeline.continue_op(user,o['id']))
        for user,rid in pending: pipeline.spawn(rid,pipeline.run(user,rid))
        for eid in exports: pipeline.spawn(eid,run_export(eid,{t['id']:t for t in CATALOG}))
        app.state.search=SemanticSearch(CATALOG)
        await app.state.search.start()
        app.state.accounts=accounts.AccountDeletion(pipeline,app.state.search,app.state.password_slots)
        account_cleaner=asyncio.create_task(app.state.accounts.recover())
        cleaner=asyncio.create_task(expire_files())
        try: yield
        finally:
            cleaner.cancel(); account_cleaner.cancel(); await asyncio.gather(cleaner,account_cleaner,return_exceptions=True); await pipeline.close(); await app.state.search.close()
    finally:
        lock.close()


app=FastAPI(title='Moss contract 1.0',lifespan=lifespan,docs_url=None,redoc_url=None,openapi_url=None)
app.add_middleware(SecurityMiddleware)

@app.get('/healthz',include_in_schema=False)
async def health():
    await tx(lambda c:c.execute('SELECT 1').fetchone())
    return {'status':'ok'}

@app.exception_handler(Problem)
async def problem_handler(request,e):
    return problem_response(e)
@app.exception_handler(RequestValidationError)
async def validation_handler(request,e): return await problem_handler(request,Problem('INPUT_INVALID',422))
@app.exception_handler(HTTPException)
async def http_handler(request,e): return await problem_handler(request,Problem('HTTP_'+str(e.status_code),e.status_code))
@app.exception_handler(Exception)
async def unexpected(request,e):
    import logging
    logging.getLogger(__name__).exception('Request failed')
    return await problem_handler(request,Problem('SERVICE_UNAVAILABLE',503))
async def user(request:Request):
    header=request.headers.get('authorization')
    identifier=await tx(lambda c:authenticate(c,header))
    lock=app.state.user_locks.setdefault(identifier,asyncio.Lock())
    async with lock:
        await tx(lambda c:authenticate(c,header))
        yield identifier
async def payload(request):
    try:
        p=await request.json()
        if not isinstance(p,dict): raise ValueError()
        return p
    except Exception: raise Problem('INPUT_INVALID',422)
def fields(p,keys):
    if set(p)!=set(keys): raise Problem('INPUT_INVALID',422)
def valid_uuid(value):
    try:
        if str(uuid.UUID(value))!=value: raise ValueError()
    except Exception: raise Problem('INPUT_INVALID',422)

@app.post('/api/v1/auth/login')
async def login(request:Request):
    p=await payload(request); fields(p,['username','password'])
    if not all(isinstance(p[k],str) for k in p): raise Problem('AUTH_INVALID_CREDENTIALS',401)
    if len(p['username'])>40 or len(p['password'])>128: raise Problem('AUTH_INVALID_CREDENTIALS',401)
    await accounts.throttle(request,'login',p['username'])
    row=await tx(lambda c:c.execute('SELECT * FROM users WHERE username=? AND NOT EXISTS(SELECT 1 FROM account_deletions WHERE user_id=users.id)',(p['username'],)).fetchone())
    async with app.state.password_slots:
        valid=await asyncio.to_thread(verify,p['password'],row['password'] if row else app.state.dummy_password)
    if not row or not valid: raise Problem('AUTH_INVALID_CREDENTIALS',401)
    def create(c):
        current=active_user(c,row['id'])
        if current['password']!=row['password']: raise Problem('AUTH_INVALID_CREDENTIALS',401)
        return session(c,current)
    return await tx(create)
@app.post('/api/v1/auth/refresh')
async def refresh(request:Request):
    p=await payload(request); fields(p,['refresh_token'])
    if not isinstance(p['refresh_token'],str): raise Problem('AUTH_REQUIRED',401)
    if len(p['refresh_token'])>128: raise Problem('AUTH_REQUIRED',401)
    await accounts.throttle(request,'refresh',token_hash(p['refresh_token']))
    def rotate(c):
        row=c.execute("SELECT users.* FROM tokens JOIN users ON users.id=tokens.user_id WHERE hash=? AND kind='refresh' AND expires>?",(token_hash(p['refresh_token']),time.time())).fetchone()
        if not row: raise Problem('AUTH_REQUIRED',401)
        group=c.execute('SELECT session_id FROM token_sessions WHERE token_hash=?',(token_hash(p['refresh_token']),)).fetchone()
        if not group:
            # Legacy token pairs have no safe session linkage; retire their old access tokens.
            c.execute("DELETE FROM tokens WHERE user_id=? AND kind='access' AND hash NOT IN (SELECT token_hash FROM token_sessions)",(row['id'],))
        else:
            c.execute('DELETE FROM tokens WHERE hash IN (SELECT token_hash FROM token_sessions WHERE session_id=?)',(group[0],))
        c.execute('DELETE FROM tokens WHERE hash=?',(token_hash(p['refresh_token']),)); return session(c,row,group[0] if group else None)
    return await tx(rotate)

@app.post('/api/v1/auth/register',status_code=201)
async def register(request:Request):
    p=await payload(request); fields(p,['username','password','nickname','invite_code'])
    return {'user':await accounts.register(request,p)}

@app.get('/api/v1/account')
async def profile(u=Depends(user,scope="function")):
    return await tx(lambda c:public_user(c,active_user(c,u)))

@app.patch('/api/v1/account/nickname')
async def nickname(request:Request,u=Depends(user,scope="function")):
    p=await payload(request); fields(p,['nickname']); name=accounts.validate_nickname(p['nickname'])
    def update(c):
        row=active_user(c,u)
        c.execute('INSERT INTO user_profiles VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET nickname=excluded.nickname',(u,name))
        return public_user(c,row)
    return await tx(update)

@app.get('/api/v1/account/referral')
async def get_referral(request:Request,u=Depends(user,scope="function")):
    await accounts.throttle(request,'referral-read',u)
    return await tx(lambda c:accounts.referral(c,u,app.state.referral_key))

@app.post('/api/v1/account/referral')
async def create_referral(request:Request,u=Depends(user,scope="function")):
    p=await payload(request); fields(p,[])
    await accounts.throttle(request,'referral-create',u)
    return await tx(lambda c:accounts.referral(c,u,app.state.referral_key,create=True))

@app.post('/api/v1/account/password')
async def change_password(request:Request,u=Depends(user,scope="function")):
    p=await payload(request); fields(p,['current_password','new_password'])
    await accounts.throttle(request,'sensitive',u)
    expected=await accounts.check_password(u,p['current_password'],app.state.password_slots)
    async with app.state.password_slots:
        hashed=await asyncio.to_thread(password_hash,accounts.validate_password(p['new_password']))
    def update(c):
        if active_user(c,u)['password']!=expected: raise Problem('AUTH_REQUIRED',401)
        c.execute('UPDATE users SET password=? WHERE id=?',(hashed,u)); accounts.revoke_all(c,u)
    await tx(update)
    return {'status':'reauthentication_required'}

@app.post('/api/v1/auth/logout')
async def logout(request:Request,u=Depends(user,scope="function")):
    p=await payload(request); fields(p,['refresh_token'])
    await tx(lambda c:accounts.revoke_session(c,u,request.headers['authorization'][7:],p['refresh_token']))
    return {'status':'logged_out'}

@app.post('/api/v1/auth/logout-all')
async def logout_all(u=Depends(user,scope="function")):
    await tx(lambda c:accounts.revoke_all(c,u))
    return {'status':'logged_out'}

@app.delete('/api/v1/account')
async def delete_account(request:Request,u=Depends(user,scope="function")):
    p=await payload(request); fields(p,['current_password','confirmation'])
    if p['confirmation']!='注销': raise Problem('CONFIRMATION_REQUIRED',422)
    await accounts.throttle(request,'sensitive',u)
    done=await app.state.accounts.begin(u,p['current_password'])
    return JSONResponse({'status':'deleted' if done else 'deleting'},status_code=200 if done else 202)
@app.get('/api/v1/config')
async def config(u=Depends(user,scope="function")): return CONFIG
@app.get('/api/v1/tags')
async def tags(u=Depends(user,scope="function")): return dict(catalog_version=VERSION,items=CATALOG)

@app.get('/api/v1/search/status')
async def search_status(u=Depends(user,scope="function")): return await app.state.search.status(u)

@app.post('/api/v1/search')
async def semantic_search(request:Request,u=Depends(user,scope="function")):
    p=await payload(request)
    if set(p)-{'query','tag_ids','limit'} or not isinstance(p.get('query'),str): raise Problem('INPUT_INVALID',422)
    text=p['query'].strip(); tags=p.get('tag_ids',[]); limit=p.get('limit',5)
    if not text or len(text)>1000 or type(limit) is not int or not 1<=limit<=20: raise Problem('INPUT_INVALID',422)
    if not isinstance(tags,list) or any(not isinstance(t,str) or t not in {x['id'] for x in CATALOG} for t in tags): raise Problem('INPUT_INVALID',422)
    await limit_usage(u,'search',60)
    return await app.state.search.search(u,text,sorted(set(tags)),limit)

@app.post('/api/v1/records')
async def submit(request:Request,u=Depends(user,scope="function")):
    p=await payload(request); rid=p.get('id'); valid_uuid(rid)
    def create(c):
        active_user(c,u)
        existing=c.execute('SELECT * FROM records WHERE id=?',(rid,)).fetchone(); fp=fingerprint(p)
        if existing:
            if existing['user_id']!=u: raise Problem('IDEMPOTENCY_MISMATCH',record_id=rid)
            if existing['deleted']: raise Problem('RECORD_DELETED',410,rid,acceptance='accepted')
            if existing['fingerprint']!=fp: raise Problem('IDEMPOTENCY_MISMATCH',record_id=rid,acceptance='accepted')
            return dict(record=record(c,u,rid),replayed=True)
        try:
            fields(p,['id','raw_input','first_sent_at','sent_utc_offset_minutes','record_date'])
            if not isinstance(p['raw_input'],str) or type(p['sent_utc_offset_minutes']) is not int: raise ValueError()
            stamp=datetime.fromisoformat(p['first_sent_at']); offset=p['sent_utc_offset_minutes']
            if stamp.tzinfo is None or not -840<=offset<=840 or date.fromisoformat(p['record_date']).isoformat()!=p['record_date']: raise ValueError()
            if stamp.astimezone(timezone(timedelta(minutes=offset))).date().isoformat()!=p['record_date']: raise ValueError()
        except Exception: raise Problem('INPUT_INVALID',422,rid,acceptance='not_accepted',modifiable=True)
        if not p['raw_input'].strip(): raise Problem('EMPTY_INPUT',422,rid,acceptance='not_accepted',modifiable=True)
        if len(p['raw_input'])>MAX_INPUT_CHARS or len(p['raw_input'].encode('utf-8'))>MAX_INPUT_BYTES: raise Problem('INPUT_TOO_LARGE',422,rid,acceptance='not_accepted',modifiable=True)
        if c.execute('SELECT 1 FROM records WHERE user_id=? AND deleted=0 AND complete=0',(u,)).fetchone(): raise Problem('PENDING_RECORD_EXISTS',record_id=rid,acceptance='not_accepted')
        r=new_record(p)
        c.execute('INSERT INTO records VALUES(?,?,?,?,?,0,0,?,?,?)',(rid,u,fp,dump(r),p['raw_input'],p['record_date'],stamp.timestamp(),1))
        return dict(record=r,replayed=False)
    result=await tx(create)
    if not result['replayed']: pipeline.spawn(rid,pipeline.run(u,rid))
    return JSONResponse(SubmissionResponse.model_validate(result).model_dump(),status_code=200 if result['replayed'] else 202)
@app.get('/api/v1/records/pending',response_model=PendingResponse)
async def pending(u=Depends(user,scope="function")):
    def read(c):
        row=c.execute('SELECT snapshot FROM records WHERE user_id=? AND complete=0 AND deleted=0',(u,)).fetchone()
        return dict(record=json.loads(row[0]) if row else None)
    return await tx(read)
@app.get('/api/v1/records/date-index')
async def index(request:Request,u=Depends(user,scope="function")): return await tx(lambda c:date_index(c,u,request.query_params))
@app.get('/api/v1/records',response_model=RecordPage)
async def listing(request:Request,u=Depends(user,scope="function")): return await tx(lambda c:page(c,u,request.query_params,app.state.secret))
@app.get('/api/v1/records/{rid}',response_model=RecordSnapshot)
async def get_record(rid:str,u=Depends(user,scope="function")): return await tx(lambda c:record(c,u,rid))
@app.get('/api/v1/operations/{oid}',response_model=OperationResponse)
async def get_operation(oid:str,u=Depends(user,scope="function")): return await tx(lambda c:operation(c,u,oid))

async def mutation(request,u,rid,kind,tag_id=None):
    p=await payload(request); oid=p.get('operation_id'); valid_uuid(oid)
    fp=fingerprint([kind,rid,tag_id,p])
    def write(c):
        old=replay(c,u,oid,fp)
        if old: return old,True
        fields(p,['operation_id']+(['expected_title_revision'] if kind in ('title_edit','title_regenerate') else [])+(['title'] if kind=='title_edit' else []))
        r=record(c,u,rid); o=new_operation(oid,kind,rid)
        def reject(code,status=409): raise Problem(code,status,rid,oid,acceptance='accepted')
        if kind=='continue':
            o['target_steps']=[k for k,s in r['steps'].items() if s['status']=='awaiting_manual']
            if not o['target_steps']: reject('NO_CONTINUABLE_STEP')
            for k in o['target_steps']: r['steps'][k].update(status='queued',active_mode='manual')
        else:
            if not r['is_complete']: reject('RECORD_INCOMPLETE')
            if kind in ('title_edit','title_regenerate'):
                if r['last_title_operation'] and r['last_title_operation']['status'] in ('queued','running'): reject('TITLE_BUSY')
                if type(p['expected_title_revision']) is not int or p['expected_title_revision']!=r['title_revision']: reject('TITLE_VERSION_MISMATCH')
                if kind=='title_edit':
                    if not isinstance(p['title'],str) or not p['title'].strip(): reject('EMPTY_TITLE',422)
                    if len(p['title'])>MAX_TITLE_CHARS: reject('TITLE_TOO_LARGE',422)
                    r['title']=p['title']; r['title_revision']+=1; o['status']='succeeded'
                r['last_title_operation']=title_summary(o)
            elif kind=='tag_delete':
                if tag_id not in r['tags']: reject('TAG_NOT_FOUND',404)
                t=next(t for t in CATALOG if t['id']==tag_id)
                c.execute('DELETE FROM record_tags WHERE record_id=? AND tag_id=?',(rid,tag_id))
                if t['parent_id']:
                    parent=t['parent_id']; children=[x['id'] for x in CATALOG if x['parent_id']==parent]
                    remaining=c.execute('SELECT tag_id FROM record_tags WHERE record_id=?',(rid,)).fetchall()
                    if not any(x[0] in children for x in remaining): c.execute("DELETE FROM record_tags WHERE record_id=? AND tag_id=? AND source='derived'",(rid,parent))
                else:
                    for child in [x['id'] for x in CATALOG if x['parent_id']==tag_id]: c.execute('DELETE FROM record_tags WHERE record_id=? AND tag_id=?',(rid,child))
                r['tags']=[x[0] for x in c.execute('SELECT DISTINCT tag_id FROM record_tags WHERE record_id=? ORDER BY tag_id',(rid,))]; o['status']='succeeded'
            elif kind=='record_delete':
                c.execute('UPDATE records SET deleted=1,snapshot=NULL,raw_input=NULL,revision=revision+1 WHERE id=?',(rid,))
                c.execute('DELETE FROM candidates WHERE record_id=?',(rid,)); c.execute('DELETE FROM record_tags WHERE record_id=?',(rid,))
                invalidate_user_exports(c,u)
                o.update(status='succeeded',deleted_record_id=rid,result_record_revision=r['revision']+1)
        if kind!='record_delete':
            save_record(c,r)
            if o['status']=='succeeded': o['result_record_revision']=r['revision']
        c.execute('INSERT INTO operations VALUES(?,?,?,?)',(oid,u,fp,dump(o)))
        c.execute('INSERT INTO audit(user_id,record_id,kind,at) VALUES(?,?,?,?)',(u,rid,kind,now()))
        return operation(c,u,oid),False
    result,replayed=await tx(write); bus.publish(rid)
    if kind=='record_delete': await cleanup_released_exports(u,pipeline)
    if not replayed:
        if kind=='continue': pipeline.spawn(oid,pipeline.continue_op(u,oid))
        if kind=='title_regenerate': pipeline.spawn(oid,pipeline.regenerate(u,oid))
    return JSONResponse(OperationResponse.model_validate(result).model_dump(),status_code=200 if replayed or kind not in ('continue','title_regenerate') else 202)
@app.post('/api/v1/records/{rid}/continue')
async def continue_record(rid:str,request:Request,u=Depends(user,scope="function")): return await mutation(request,u,rid,'continue')
@app.patch('/api/v1/records/{rid}/title')
async def edit(rid:str,request:Request,u=Depends(user,scope="function")): return await mutation(request,u,rid,'title_edit')
@app.post('/api/v1/records/{rid}/title/regenerate')
async def regenerate(rid:str,request:Request,u=Depends(user,scope="function")): return await mutation(request,u,rid,'title_regenerate')
@app.delete('/api/v1/records/{rid}/tags/{tid}')
async def delete_tag(rid:str,tid:str,request:Request,u=Depends(user,scope="function")): return await mutation(request,u,rid,'tag_delete',tid)
@app.delete('/api/v1/records/{rid}')
async def delete_record(rid:str,request:Request,u=Depends(user,scope="function")): return await mutation(request,u,rid,'record_delete')

def export_read(c,u,eid):
    row=c.execute('SELECT * FROM exports WHERE id=? AND user_id=?',(eid,u)).fetchone()
    if not row: raise Problem('EXPORT_NOT_FOUND',404,export_id=eid)
    e=json.loads(row['data'])
    if e['status']=='expired' or e['expires_at'] and datetime.fromisoformat(e['expires_at'])<=datetime.now(timezone.utc): raise Problem('EXPORT_EXPIRED',410,export_id=eid)
    return e
@app.post('/api/v1/exports')
async def create_export(request:Request,u=Depends(user,scope="function")):
    p=await payload(request); oid=p.get('operation_id'); valid_uuid(oid); fp=fingerprint(['export_create',p])
    def create(c):
        active_user(c,u)
        old=replay(c,u,oid,fp)
        if old: return export_read(c,u,old['operation']['export_id']),True
        fields(p,['operation_id','format','scope'])
        if p['format']!='zip_markdown' or p['scope']!='all_completed': raise Problem('INPUT_INVALID',422)
        for row in c.execute('SELECT data FROM exports WHERE user_id=?',(u,)):
            e=json.loads(row[0])
            if e['status'] in ('queued','preparing'): raise Problem('EXPORT_BUSY',export_id=e['id'],operation_id=oid)
        rows=[json.loads(row[0]) for row in c.execute('SELECT snapshot FROM records WHERE user_id=? AND deleted=0 ORDER BY record_date DESC,sent_epoch DESC,id DESC',(u,))]
        completed=[r for r in rows if r['is_complete']]
        if not completed: raise Problem('EXPORT_EMPTY',422,operation_id=oid)
        from .security import reserve_usage
        if reserve_usage(c,u,'export',6): raise Problem('USAGE_RATE_LIMITED',429)
        excluded=[]
        for r in rows:
            if not r['is_complete']:
                statuses=[s['status'] for s in r['steps'].values()]
                excluded.append(dict(id=r['id'],first_sent_at=r['first_sent_at'],title=r['title'],status='awaiting_manual' if 'awaiting_manual' in statuses and not set(statuses)&{'queued','running','retry_wait','save_pending'} else 'processing'))
        eid=uid(); e=dict(id=eid,operation_id=oid,revision=1,format='zip_markdown',scope='all_completed',status='preparing',snapshot_at=now(),total_records=len(completed),processed_records=0,excluded_incomplete_records=excluded,file_name=None,file_bytes=None,sha256=None,expires_at=(datetime.now(timezone.utc)+timedelta(seconds=EXPORT_TTL_SECONDS)).isoformat(),error=None)
        c.execute('INSERT INTO exports VALUES(?,?,?,?,NULL)',(eid,u,dump(e),dump(completed)))
        o=new_operation(oid,'export_create'); o.update(status='succeeded',export_id=eid)
        c.execute('INSERT INTO operations VALUES(?,?,?,?)',(oid,u,fp,dump(o))); return e,False
    e,replayed=await tx(create)
    if not replayed: pipeline.spawn(e['id'],run_export(e['id'],{t['id']:t for t in CATALOG}))
    return JSONResponse(ExportTask.model_validate(e).model_dump(),status_code=200 if replayed else 202)
@app.get('/api/v1/exports/{eid}',response_model=ExportTask)
async def get_export(eid:str,u=Depends(user,scope="function")): return await tx(lambda c:export_read(c,u,eid))
@app.get('/api/v1/exports/{eid}/download')
async def download(eid:str,u=Depends(user,scope="function")):
    e=await tx(lambda c:export_read(c,u,eid))
    if e['status']!='ready': raise Problem('EXPORT_NOT_READY',export_id=eid)
    return FileResponse(DATA/(eid+'.zip'),media_type='application/zip',filename=e['file_name'])

@app.delete('/api/v1/exports/{eid}',status_code=204)
async def discard_export(eid:str,u=Depends(user,scope="function")):
    valid_uuid(eid)
    row=await tx(lambda c:c.execute('SELECT user_id FROM exports WHERE id=?',(eid,)).fetchone())
    if not row or row['user_id']!=u: raise Problem('EXPORT_NOT_FOUND',404,export_id=eid)
    if not await release_export(eid,pipeline): raise Problem('EXPORT_CLEANUP_PENDING',503,export_id=eid)

async def stream(key,u,header,export=False):
    q=bus.subscribe(key)
    def read(c):
        authenticate(c,header)
        return export_read(c,u,key) if export else record(c,u,key)
    try: initial=await tx(read)
    except BaseException: bus.unsubscribe(key,q); raise
    async def events():
        revision=0; snapshot=initial
        try:
            while True:
                if snapshot['revision']>revision:
                    revision=snapshot['revision']
                    yield f"id: {key}:{revision}\nevent: {'export' if export else 'record'}_snapshot\ndata: {dump(snapshot)}\n\n"
                try: await asyncio.wait_for(q.get(),15)
                except asyncio.TimeoutError: yield ': heartbeat\n\n'
                try: snapshot=await tx(read)
                except Problem: return
        finally: bus.unsubscribe(key,q)
    return StreamingResponse(events(),media_type='text/event-stream',headers={'Cache-Control':'no-cache','X-Accel-Buffering':'no'})
@app.get('/api/v1/records/{rid}/events')
async def record_events(rid:str,request:Request,u=Depends(user,scope="function")): return await stream(rid,u,request.headers.get('authorization'))
@app.get('/api/v1/exports/{eid}/events')
async def export_events(eid:str,request:Request,u=Depends(user,scope="function")): return await stream(eid,u,request.headers.get('authorization'),True)

app.mount('/',PublicAssets(directory=Path(__file__).resolve().parents[1]/'moss-prototype'),name='ui')
