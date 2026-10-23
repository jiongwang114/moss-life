import base64
import hashlib
import hmac
import json
from datetime import date
from .database import dump
from .domain import Problem,now

def filters(q,c):
    view=q.get('view','time'); tags=sorted(set(filter(None,q.get('tag_ids','').split(','))))
    if view not in ('time','category') or (view=='time' and 'tag_ids' in q): raise Problem('INPUT_INVALID',422)
    if view=='category' and not tags: raise Problem('FILTER_REQUIRED',422)
    valid={r[0] for r in c.execute('SELECT id FROM tags')}
    if any(t not in valid for t in tags): raise Problem('INPUT_INVALID',422)
    return view,tags
def where(user,view,tags):
    sql='user_id=? AND deleted=0'; args=[user]
    if view=='category': sql+=' AND complete=1'
    for t in tags:
        sql+=' AND EXISTS(SELECT 1 FROM record_tags rt WHERE rt.record_id=records.id AND rt.tag_id=?)'; args.append(t)
    return sql,args
def page(c,user,q,secret):
    view,tags=filters(q,c); day=q.get('record_date'); sql,args=where(user,view,tags)
    try:
        limit=int(q.get('limit',20))
        if not 1<=limit<=100: raise ValueError()
        if day: date.fromisoformat(day)
    except ValueError: raise Problem('INPUT_INVALID',422)
    binding=[user,view,tags,day]
    if day: sql+=' AND record_date=?'; args.append(day)
    if q.get('cursor'):
        try:
            raw=base64.urlsafe_b64decode(q['cursor']); payload,signature=raw[:-32],raw[-32:]
            if not hmac.compare_digest(hmac.digest(secret,payload,'sha256'),signature): raise ValueError()
            data=json.loads(payload); boundary=data['boundary']
            if len(boundary)!=3: raise ValueError()
        except Exception: raise Problem('CURSOR_INVALID',400)
        if data['binding']!=binding: raise Problem('CURSOR_FILTER_MISMATCH',400)
        sql+=' AND (record_date,sent_epoch,id)<(?,?,?)'; args+=boundary
    rows=c.execute('SELECT * FROM records WHERE '+sql+' ORDER BY record_date DESC,sent_epoch DESC,id DESC LIMIT ?',args+[limit+1]).fetchall()
    more=len(rows)>limit; rows=rows[:limit]; cursor=None
    if more:
        r=rows[-1]; payload=dump(dict(binding=binding,boundary=[r['record_date'],r['sent_epoch'],r['id']])).encode()
        cursor=base64.urlsafe_b64encode(payload+hmac.digest(secret,payload,'sha256')).decode()
    items=[]
    for row in rows:
        r=json.loads(row['snapshot']); r.pop('body_markdown'); items.append(r)
    return dict(view=view,tag_ids=tags,record_date=day,items=items,next_cursor=cursor,has_more=more)
def date_index(c,user,q):
    view,tags=filters(q,c); sql,args=where(user,view,tags); years={}
    for row in c.execute('SELECT record_date,count(*) n FROM records WHERE '+sql+' GROUP BY record_date ORDER BY record_date DESC',args):
        day=row['record_date']; y,m=map(int,day.split('-')[:2]); count=row['n']
        year=years.setdefault(y,dict(year=y,count=0,months={})); year['count']+=count
        month=year['months'].setdefault(m,dict(month=m,count=0,days=[])); month['count']+=count; month['days'].append(dict(record_date=day,count=count))
    for year in years.values(): year['months']=list(year['months'].values())
    return dict(view=view,tag_ids=tags,index_generated_at=now(),years=list(years.values()))
