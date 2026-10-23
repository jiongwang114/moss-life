import hashlib
import hmac
import secrets
import time
from datetime import datetime,timezone
from .domain import Problem,uid

def password_hash(password,salt=None):
    salt=salt or secrets.token_hex(16)
    return salt+':'+hashlib.scrypt(password.encode(),salt=salt.encode(),n=16384,r=8,p=1).hex()
def verify(password,stored): return hmac.compare_digest(password_hash(password,stored.split(':')[0]),stored)
def token_hash(token): return hashlib.sha256(token.encode()).hexdigest()
def public_user(c,user):
    profile=c.execute('SELECT nickname FROM user_profiles WHERE user_id=?',(user['id'],)).fetchone()
    return dict(id=user['id'],username=user['username'],nickname=profile[0] if profile else user['username'])

def active_user(c,identifier):
    row=c.execute('SELECT * FROM users WHERE id=? AND NOT EXISTS(SELECT 1 FROM account_deletions WHERE user_id=users.id)',(identifier,)).fetchone()
    if not row: raise Problem('AUTH_REQUIRED',401)
    return row

def session(c,user,session_id=None):
    active_user(c,user['id'])
    session_id=session_id or uid()
    result={'token_type':'Bearer','user':public_user(c,user)}
    for kind,ttl in [('access',3600),('refresh',2592000)]:
        token=secrets.token_urlsafe(48); expires=time.time()+ttl
        c.execute('INSERT INTO tokens VALUES(?,?,?,?)',(token_hash(token),user['id'],kind,expires))
        c.execute('INSERT INTO token_sessions VALUES(?,?)',(token_hash(token),session_id))
        result[kind+'_token']=token; result[kind+'_expires_at']=datetime.fromtimestamp(expires,timezone.utc).isoformat()
    return result
def authenticate(c,header):
    if not header or not header.startswith('Bearer '): raise Problem('AUTH_REQUIRED',401)
    row=c.execute("SELECT user_id FROM tokens WHERE hash=? AND kind='access' AND expires>? AND NOT EXISTS(SELECT 1 FROM account_deletions WHERE account_deletions.user_id=tokens.user_id)",(token_hash(header[7:]),time.time())).fetchone()
    if not row: raise Problem('AUTH_REQUIRED',401)
    return row[0]
