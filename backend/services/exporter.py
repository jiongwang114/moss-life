import asyncio
import hashlib
import json
import re
import zipfile
from datetime import datetime,timezone,timedelta
from urllib.parse import quote
import yaml
from ..database import DATA,tx,dump
from ..domain import info
from ..events import bus


async def disk(fn,*args,**kwargs):
    # A cancelled coroutine must wait for its current file write before closing/unlinking.
    work=asyncio.create_task(asyncio.to_thread(fn,*args,**kwargs))
    try: return await asyncio.shield(work)
    except asyncio.CancelledError:
        while not work.done():
            try: await asyncio.shield(work)
            except asyncio.CancelledError: pass
        work.result()
        raise


def invalidate_export(c,eid):
    row=c.execute('SELECT data FROM exports WHERE id=?',(eid,)).fetchone()
    if not row: return
    task=json.loads(row[0])
    task.update(status='expired',file_name=None,file_bytes=None,sha256=None,expires_at=None,
                excluded_incomplete_records=[],error=None,revision=task['revision']+1)
    # A path marker survives crashes and failed unlinks, so the cleaner can retry.
    c.execute('UPDATE exports SET data=?,frozen=NULL,path=? WHERE id=?',(dump(task),str(DATA/(eid+'.zip')),eid))


def invalidate_user_exports(c,user):
    for row in c.execute('SELECT id FROM exports WHERE user_id=? AND (path IS NOT NULL OR frozen IS NOT NULL)',(user,)).fetchall():
        invalidate_export(c,row[0])


async def release_export(eid,pipeline):
    await tx(lambda c:invalidate_export(c,eid))
    worker=pipeline.tasks.get(eid)
    if worker:
        worker.cancel()
        await asyncio.gather(worker,return_exceptions=True)
    try:
        for suffix in ('.zip','.part'): await disk((DATA/(eid+suffix)).unlink,missing_ok=True)
    except OSError:
        return False
    await tx(lambda c:c.execute('UPDATE exports SET path=NULL WHERE id=?',(eid,)))
    bus.publish(eid)
    return True


async def cleanup_released_exports(user,pipeline):
    rows=await tx(lambda c:c.execute("SELECT id FROM exports WHERE user_id=? AND path IS NOT NULL AND json_extract(data,'$.status')='expired'",(user,)).fetchall())
    for row in rows:
        await release_export(row[0],pipeline)

def build_note(r,catalog):
    names=lambda ids:[catalog[t]['name'] for t in ids]
    meta={k:r[k] for k in ('id','title','first_sent_at','sent_utc_offset_minutes','record_date','body_format','catalog_version')}
    meta.update(tags=names(r['tags']),tag_ids=r['tags'])
    for dimension,field in [('scene','scene_tags'),('topic','topic_tags'),('record_type','record_type_tags')]: meta[field]=names([t for t in r['tags'] if catalog[t]['dimension']==dimension])
    return '---\n'+yaml.safe_dump(meta,allow_unicode=True,sort_keys=False)+'---\n\n'+r['body_markdown']
def note_path(r):
    safe=re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f-\x9f]','_',r['title'])[:60].rstrip('. ') or 'record'
    date=datetime.fromisoformat(r['first_sent_at']).astimezone(timezone(timedelta(minutes=r['sent_utc_offset_minutes'])))
    return f"{r['record_date'].replace('-','/')}/{date:%H%M%S}-{safe}-{r['id']}.md"

async def run_export(eid,catalog):
    row=await tx(lambda c:c.execute('SELECT * FROM exports WHERE id=?',(eid,)).fetchone())
    if not row or json.loads(row['data'])['status'] not in ('queued','preparing'): return
    task=json.loads(row['data']); rows=json.loads(row['frozen']); path=DATA/(eid+'.zip'); partial=DATA/(eid+'.part')
    async def update():
        task['revision']+=1
        def save(c):
            row=c.execute('SELECT data FROM exports WHERE id=?',(eid,)).fetchone()
            if not row or json.loads(row[0])['status'] not in ('queued','preparing'): return False
            c.execute('UPDATE exports SET data=? WHERE id=?',(dump(task),eid))
            if task['status'] in ('ready','failed'): c.execute('UPDATE exports SET path=?,frozen=NULL WHERE id=?',(str(path) if task['status']=='ready' else None,eid))
            return True
        if not await tx(save): raise asyncio.CancelledError()
        bus.publish(eid)
    try:
        task['status']='preparing'; await update()
        index=f"# 导出索引\n\n快照时间：{task['snapshot_at']}\n\n导出 {len(rows)} 条。目录按首次发送原日期组织，以下按日期和时间倒序。\n\n未包含的未完成记录：\n"
        for x in task['excluded_incomplete_records']: index+=f"- {x['id']} · {x['first_sent_at']} · {x['title'] or '未完成记录'}\n"
        if not task['excluded_incomplete_records']: index+='无\n'
        index+='\n目录规则：YYYY/MM/DD/HHmmss-安全标题-完整UUID.md\n\n'
        archive=zipfile.ZipFile(partial,'w',zipfile.ZIP_DEFLATED)
        try:
            for r in rows:
                name=note_path(r)
                await disk(archive.writestr,name,build_note(r,catalog).encode('utf-8'))
                label=re.sub(r'[\[\]\\\r\n]',' ',r['title'])
                index+=f"- [{r['record_date']} · {label}]({quote(name,safe='/')})\n"
                task['processed_records']+=1; await update()
            await disk(archive.writestr,'INDEX.md',index.encode('utf-8'))
        finally: await disk(archive.close)
        await disk(partial.replace,path)
        def digest():
            with path.open('rb') as f: return hashlib.file_digest(f,'sha256').hexdigest()
        task.update(status='ready',file_name='records-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'.zip',file_bytes=path.stat().st_size,sha256=await disk(digest))
        await update()
    except asyncio.CancelledError:
        for file in (partial,path): await disk(file.unlink,missing_ok=True)
        raise
    except Exception:
        partial.unlink(missing_ok=True); path.unlink(missing_ok=True)
        task.update(status='failed',error=info('EXPORT_FAILED'),file_name=None,file_bytes=None,sha256=None,expires_at=None)
        await update()
