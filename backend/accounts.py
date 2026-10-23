"""Account operations and authenticated, single-use referral invitations."""
import asyncio
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time

from . import database
from .auth import active_user, password_hash, public_user, token_hash, verify
from .domain import Problem, dump, uid
from datetime import datetime, timezone


def load_referral_key():
    """Keep the recovery key outside SQLite; back it up with the private data volume."""
    path=database.DATA/'referral.key'
    if not path.exists():
        with database.connect() as c:
            if c.execute('SELECT 1 FROM account_referrals LIMIT 1').fetchone():
                raise RuntimeError('Restore the private referral key together with the database')
    try:
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(fd,'wb') as f:
            f.write(secrets.token_bytes(32)); f.flush(); os.fsync(f.fileno())
    key=path.read_bytes()
    if len(key)!=32: raise RuntimeError('Invalid private referral key')
    return key


def referral_code(key,user,nonce):
    value=hmac.new(key,f'moss-referral-v1:{user}:{nonce}'.encode(),hashlib.sha256).digest()
    return base64.urlsafe_b64encode(value).decode().rstrip('=')


def referral(c,user,key,create=False):
    active_user(c,user)
    stamp=time.time()
    row=c.execute('SELECT r.*,i.remaining,i.expires FROM account_referrals r '
                  'JOIN invites i ON i.hash=r.invite_hash WHERE r.user_id=?',(user,)).fetchone()
    if create and (not row or row['remaining']<=0 or row['expires']<=stamp):
        if row: c.execute('DELETE FROM invites WHERE hash=?',(row['invite_hash'],))
        nonce=secrets.token_hex(24)
        code=referral_code(key,user,nonce)
        hashed=token_hash(code)
        c.execute('INSERT INTO invites VALUES(?,1,?)',(hashed,stamp+7*86400))
        c.execute('INSERT INTO account_referrals VALUES(?,?,?,?)',(user,hashed,nonce,stamp))
        row=c.execute('SELECT r.*,i.remaining,i.expires FROM account_referrals r '
                      'JOIN invites i ON i.hash=r.invite_hash WHERE r.user_id=?',(user,)).fetchone()
    if not row: return dict(status='not_generated',code=None,created_at=None,expires_at=None)
    status='used' if row['remaining']<=0 else 'expired' if row['expires']<=stamp else 'active'
    code=referral_code(key,user,row['nonce']) if status=='active' else None
    if code and not hmac.compare_digest(token_hash(code),row['invite_hash']):
        raise Problem('REFERRAL_UNAVAILABLE',503)
    return dict(status=status,code=code,
                created_at=datetime.fromtimestamp(row['created'],timezone.utc).isoformat(),
                expires_at=datetime.fromtimestamp(row['expires'],timezone.utc).isoformat())


def revoke_referral(c,user):
    c.execute('DELETE FROM invites WHERE hash IN (SELECT invite_hash FROM account_referrals WHERE user_id=?)',(user,))


def validate_password(value):
    if not isinstance(value,str) or not 10 <= len(value) <= 128 or len(value.encode()) > 512:
        raise Problem('PASSWORD_INVALID',422)
    return value


def validate_nickname(value):
    if not isinstance(value,str) or not 1 <= len(value.strip()) <= 40 or any(ord(c)<32 for c in value):
        raise Problem('NICKNAME_INVALID',422)
    return value.strip()


def mint_invite(c,uses=1,days=7):
    if not 1 <= uses <= 1000 or not 1 <= days <= 365: raise ValueError('Invalid invitation limits')
    code=secrets.token_urlsafe(24)
    c.execute('INSERT INTO invites VALUES(?,?,?)',(token_hash(code),uses,time.time()+days*86400))
    return code


async def throttle(request,action,username=''):
    # Uvicorn normalizes client.host only for the explicitly trusted proxy.
    peer=request.client.host if request.client else 'unknown'
    keys=[(hashlib.sha256(f'{action}:peer:{peer}'.encode()).hexdigest(),120 if action in ('login','refresh') else 30)]
    if username: keys.append((hashlib.sha256(f'{action}:account:{username}'.encode()).hexdigest(),12 if action=='refresh' else 10 if action=='login' else 6))
    def attempt(c):
        stamp=time.time()
        c.execute('DELETE FROM auth_attempts WHERE started<?',(stamp-600,))
        for key,limit in keys:
            row=c.execute('SELECT * FROM auth_attempts WHERE key=?',(key,)).fetchone()
            if row and row['started']>stamp-60 and row['count']>=limit: return False
        for key,_ in keys:
            c.execute('INSERT INTO auth_attempts VALUES(?,?,1) ON CONFLICT(key) DO UPDATE SET '
                      'count=CASE WHEN started<? THEN 1 ELSE count+1 END,started=CASE WHEN started<? THEN excluded.started ELSE started END',
                      (key,stamp,stamp-60,stamp-60))
        return True
    if not await database.tx(attempt): raise Problem('AUTH_RATE_LIMITED',429)


async def register(request,p):
    username=p.get('username')
    if not isinstance(username,str) or not re.fullmatch(r'[a-z0-9][a-z0-9_-]{2,39}',username):
        raise Problem('USERNAME_INVALID',422)
    await throttle(request,'register',username)
    password=validate_password(p.get('password'))
    nickname=validate_nickname(p.get('nickname',username))
    code=p.get('invite_code')
    if not isinstance(code,str) or not 1 <= len(code) <= 128: raise Problem('INVITE_INVALID',422)
    async with request.app.state.password_slots:
        hashed=await asyncio.to_thread(password_hash,password)
    def create(c):
        invite=c.execute('SELECT * FROM invites WHERE hash=? AND remaining>0 AND expires>?',(token_hash(code.strip()),time.time())).fetchone()
        if not invite: raise Problem('INVITE_INVALID',422)
        if c.execute('SELECT 1 FROM users WHERE username=?',(username,)).fetchone(): raise Problem('USERNAME_TAKEN',409)
        identifier=uid()
        c.execute('INSERT INTO users VALUES(?,?,?)',(identifier,username,hashed))
        c.execute('INSERT INTO user_profiles VALUES(?,?)',(identifier,nickname))
        c.execute('UPDATE invites SET remaining=remaining-1 WHERE hash=?',(invite['hash'],))
        return public_user(c,active_user(c,identifier))
    return await database.tx(create)


async def check_password(user,password,slots):
    if not isinstance(password,str) or len(password)>128: raise Problem('AUTH_INVALID_CREDENTIALS',401)
    row=await database.tx(lambda c:active_user(c,user))
    async with slots:
        valid=await asyncio.to_thread(verify,password,row['password'])
    if not valid: raise Problem('AUTH_INVALID_CREDENTIALS',401)
    return row['password']


def revoke_all(c,user):
    c.execute('DELETE FROM token_sessions WHERE token_hash IN (SELECT hash FROM tokens WHERE user_id=?)',(user,))
    c.execute('DELETE FROM tokens WHERE user_id=?',(user,))


def revoke_session(c,user,access,refresh):
    access_hash=token_hash(access)
    group=c.execute('SELECT session_id FROM token_sessions WHERE token_hash=?',(access_hash,)).fetchone()
    if group:
        hashes=[r[0] for r in c.execute('SELECT token_hash FROM token_sessions WHERE session_id=?',(group[0],))]
        for hashed in hashes:
            c.execute('DELETE FROM token_sessions WHERE token_hash=?',(hashed,))
            c.execute('DELETE FROM tokens WHERE hash=? AND user_id=?',(hashed,user))
    else:
        # Sessions created before the grouping migration can still be logged out.
        for value in (access,refresh):
            if isinstance(value,str): c.execute('DELETE FROM tokens WHERE hash=? AND user_id=?',(token_hash(value),user))


class AccountDeletion:
    """Persist a cleanup job before revocation; retry after failures or server restart."""
    def __init__(self,pipeline,search,password_slots):
        self.pipeline,self.search=pipeline,search
        self.password_slots=password_slots
        self.lock=asyncio.Lock()

    async def begin(self,user,password):
        expected=await check_password(user,password,self.password_slots)
        def begin(c):
            if active_user(c,user)['password']!=expected: raise Problem('AUTH_REQUIRED',401)
            rids=[r[0] for r in c.execute('SELECT id FROM records WHERE user_id=?',(user,))]
            eids=[r[0] for r in c.execute('SELECT id FROM exports WHERE user_id=?',(user,))]
            oids=[r[0] for r in c.execute('SELECT id FROM operations WHERE user_id=?',(user,))]
            c.execute('INSERT INTO account_deletions VALUES(?,?)',(user,dump(dict(records=rids,exports=eids,operations=oids))))
            revoke_referral(c,user)
            revoke_all(c,user)
            c.execute('UPDATE records SET deleted=1,snapshot=NULL,raw_input=NULL,revision=revision+1 WHERE user_id=?',(user,))
        await database.tx(begin)
        return await self.finish(user)

    async def finish(self,user):
        async with self.lock:
            row=await database.tx(lambda c:c.execute('SELECT data FROM account_deletions WHERE user_id=?',(user,)).fetchone())
            if not row: return True
            data=json.loads(row[0])
            tasks=[self.pipeline.tasks[k] for k in data['records']+data['exports']+data['operations'] if k in self.pipeline.tasks]
            for task in tasks: task.cancel()
            if tasks: await asyncio.gather(*tasks,return_exceptions=True)
            try:
                # A retry must keep the account inaccessible until every store is cleaned.
                await self.search.purge_user(user,data['records'])
                for eid in data['exports']:
                    for suffix in ('.zip','.part'):
                        await asyncio.to_thread((database.DATA/(eid+suffix)).unlink,missing_ok=True)
                def purge(c):
                    revoke_referral(c,user)
                    for table in ('candidates','record_tags','search_jobs'):
                        c.execute(f'DELETE FROM {table} WHERE record_id IN (SELECT id FROM records WHERE user_id=?)',(user,))
                    for table in ('operations','audit','exports','records','user_profiles'):
                        c.execute(f'DELETE FROM {table} WHERE user_id=?',(user,))
                    revoke_all(c,user)
                    c.execute('DELETE FROM users WHERE id=?',(user,))
                    c.execute('DELETE FROM account_deletions WHERE user_id=?',(user,))
                await database.tx(purge)
                return True
            except asyncio.CancelledError: raise
            except Exception:
                return False

    async def recover(self):
        while True:
            users=await database.tx(lambda c:[r[0] for r in c.execute('SELECT user_id FROM account_deletions')])
            for user in users: await self.finish(user)
            await asyncio.sleep(5)
