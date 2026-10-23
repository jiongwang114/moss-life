import hashlib
import json
import uuid
from datetime import datetime, timezone
from .database import dump

VERSION = '2026-10-15.v1'
def now(): return datetime.now(timezone.utc).isoformat()
def uid(): return str(uuid.uuid4())
def fingerprint(value): return hashlib.sha256(dump(value).encode()).hexdigest()

class Problem(Exception):
    def __init__(self, code, status=409, record_id=None, operation_id=None, export_id=None, acceptance='unknown', modifiable=False):
        self.code, self.status = code, status
        self.record_id, self.operation_id, self.export_id = record_id, operation_id, export_id
        self.acceptance, self.modifiable = acceptance, modifiable

def info(code): return dict(code=code,message={'UPSTREAM_ERROR':'处理服务暂时不可用','SAVE_FAILED':'结果尚未保存，请重试','WORKER_INTERRUPTED':'处理被中断，请继续'}.get(code,code),retryable=True)
def step(status): return dict(status=status,automatic_attempts=0,manual_attempts=0,active_mode=None,next_retry_at=None,last_error=None)
def new_record(p):
    return dict(id=p['id'],revision=1,title_revision=0,first_sent_at=p['first_sent_at'],sent_utc_offset_minutes=p['sent_utc_offset_minutes'],record_date=p['record_date'],body_format='markdown',body_available=False,body_markdown=None,body_preview=None,title=None,tags=None,catalog_version=VERSION,steps={k:step('queued' if k=='body' else 'blocked') for k in ('body','title','tags')},is_complete=False,last_title_operation=None,available_actions=[],updated_at=now())

def record(c, user, rid):
    if c.execute('SELECT 1 FROM account_deletions WHERE user_id=?',(user,)).fetchone(): raise Problem('AUTH_REQUIRED',401)
    row=c.execute('SELECT * FROM records WHERE id=? AND user_id=?',(rid,user)).fetchone()
    if not row: raise Problem('RECORD_NOT_FOUND',404,rid,acceptance='not_accepted')
    if row['deleted']: raise Problem('RECORD_DELETED',410,rid,acceptance='accepted')
    return json.loads(row['snapshot'])

def save_record(c,r):
    r['revision']+=1; r['updated_at']=now()
    r['is_complete']=all(s['status']=='succeeded' for s in r['steps'].values())
    busy=r['last_title_operation'] and r['last_title_operation']['status'] in ('queued','running')
    r['available_actions']=(['read_body'] if r['body_available'] else [])+(['continue'] if any(s['status']=='awaiting_manual' for s in r['steps'].values()) else [])
    if r['is_complete']: r['available_actions']+= ([] if busy else ['edit_title','regenerate_title'])+['delete_tag','delete_record']
    c.execute('UPDATE records SET snapshot=?,revision=?,complete=?,raw_input=CASE WHEN ? THEN NULL ELSE raw_input END WHERE id=? AND deleted=0',(dump(r),r['revision'],r['is_complete'],r['is_complete'],r['id']))

def operation(c,user,oid):
    row=c.execute('SELECT data FROM operations WHERE id=? AND user_id=?',(oid,user)).fetchone()
    if not row: raise Problem('OPERATION_NOT_FOUND',404,operation_id=oid)
    o=json.loads(row['data']); r=None
    if o['record_id']:
        try: r=record(c,user,o['record_id'])
        except Problem: pass
    return dict(operation=o,record=r)

def replay(c,user,oid,fp):
    row=c.execute('SELECT * FROM operations WHERE id=?',(oid,)).fetchone()
    if not row: return None
    if row['user_id']!=user or row['fingerprint']!=fp: raise Problem('IDEMPOTENCY_MISMATCH',operation_id=oid)
    return operation(c,user,oid)

def new_operation(oid,kind,rid=None):
    return dict(id=oid,kind=kind,status='queued',record_id=rid,target_steps=[],created_at=now(),updated_at=now(),result_record_revision=None,deleted_record_id=None,export_id=None,error=None)
def put_operation(c,o):
    o['updated_at']=now(); c.execute('UPDATE operations SET data=? WHERE id=?',(dump(o),o['id']))
def title_summary(o): return {k:o[k] for k in ('id','kind','status','error')}

def set_tags(c,r,ids):
    known={x['id']:json.loads(x['data']) for x in c.execute('SELECT * FROM tags')}
    if any(t not in known for t in ids): raise ValueError('invalid model tag')
    c.execute('DELETE FROM record_tags WHERE record_id=?',(r['id'],))
    for tid in set(ids):
        c.execute('INSERT OR IGNORE INTO record_tags VALUES(?,?,?)',(r['id'],tid,'direct'))
        parent=known[tid]['parent_id']
        if parent: c.execute('INSERT OR IGNORE INTO record_tags VALUES(?,?,?)',(r['id'],parent,'derived'))
    r['tags']=[x[0] for x in c.execute('SELECT DISTINCT tag_id FROM record_tags WHERE record_id=? ORDER BY tag_id',(r['id'],))]
