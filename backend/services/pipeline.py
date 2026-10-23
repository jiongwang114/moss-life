import asyncio
import json
import os
import sqlite3
from datetime import datetime,timezone,timedelta
from ..database import tx,dump
from ..domain import record,save_record,put_operation,title_summary,info,set_tags,Problem,now
from ..events import bus
from ..providers import preview

class Pipeline:
    def __init__(self,provider): self.provider=provider; self.tasks={}; self.save_calls={}
    def spawn(self,key,coro):
        if key in self.tasks: coro.close(); return
        task=asyncio.create_task(coro); self.tasks[key]=task
        def done(t):
            self.tasks.pop(key,None)
            if not t.cancelled() and t.exception():
                import logging
                logging.getLogger(__name__).error('Worker failed',exc_info=t.exception())
        task.add_done_callback(done)
    async def close(self):
        for t in tuple(self.tasks.values()): t.cancel()
        await asyncio.gather(*tuple(self.tasks.values()),return_exceptions=True)
    async def change(self,user,rid,fn):
        def write(c):
            r=record(c,user,rid); value=fn(c,r); save_record(c,r); return value
        result=await tx(write); bus.publish(rid); return result
    async def step(self,user,rid,key,manual=False):
        # Retain output in memory even when the candidate-staging transaction fails.
        retained=None
        while True:
            snapshot=await tx(lambda c:record(c,user,rid))
            deadline=snapshot['steps'][key]['next_retry_at']
            if deadline:
                await asyncio.sleep(max(0,(datetime.fromisoformat(deadline)-datetime.now(timezone.utc)).total_seconds()))
            def begin(c,r):
                s=r['steps'][key]
                if s['status']=='succeeded': return None
                # A durable running attempt after restart has already consumed its budget.
                resume=s['status'] in ('running','save_pending')
                if not resume: s['manual_attempts' if manual else 'automatic_attempts']+=1
                s.update(status='running',active_mode='manual' if manual else 'automatic',next_retry_at=None)
                row=c.execute('SELECT value FROM candidates WHERE record_id=? AND step=?',(rid,key)).fetchone()
                raw=c.execute('SELECT raw_input FROM records WHERE id=?',(rid,)).fetchone()[0]
                return (json.loads(row[0]) if row else None,raw if key=='body' else r['body_markdown'])
            try: work=await self.change(user,rid,begin)
            except Problem: return False
            if work is None: return True
            candidate,body=work; candidate=candidate if candidate is not None else retained; generated=False
            try:
                if candidate is None: candidate=await self.provider.generate(key,body,rid,user)
                retained=candidate
                generated=True
                def stage(c,r):
                    c.execute('INSERT OR REPLACE INTO candidates VALUES(?,?,?)',(rid,key,dump(candidate)))
                    r['steps'][key]['status']='save_pending'
                await self.change(user,rid,stage)
                def commit(c,r):
                    fault_key=(rid,key); self.save_calls[fault_key]=self.save_calls.get(fault_key,0)+1
                    if self.save_calls[fault_key]<=int(json.loads(os.getenv('SAVE_FAIL_FIRST','{}')).get(key,0)): raise sqlite3.OperationalError('injected save failure')
                    if key=='body': r.update(body_markdown=candidate,body_preview=preview(candidate),body_available=True)
                    elif key=='title': r['title']=candidate; r['title_revision']+=1
                    else: set_tags(c,r,candidate)
                    r['steps'][key].update(status='succeeded',active_mode=None,next_retry_at=None,last_error=None)
                    c.execute('DELETE FROM candidates WHERE record_id=? AND step=?',(rid,key))
                await self.change(user,rid,commit)
                return True
            except Problem: return False
            except Exception:
                def fail(c,r):
                    s=r['steps'][key]; terminal=manual or s['automatic_attempts']>=3
                    s.update(status='awaiting_manual' if terminal else ('save_pending' if generated else 'retry_wait'),active_mode=None if terminal else 'automatic',last_error=info('SAVE_FAILED' if generated else 'UPSTREAM_ERROR'),next_retry_at=None if terminal else (datetime.now(timezone.utc)+timedelta(seconds=float(os.getenv('RETRY_DELAY_SECONDS','1')))).isoformat())
                    return terminal
                # A busy database is retried without spending another model attempt.
                while True:
                    try: terminal=await self.change(user,rid,fail); break
                    except sqlite3.OperationalError: await asyncio.sleep(.2)
                    except Problem: return False
                if terminal: return False
                await asyncio.sleep(float(os.getenv('RETRY_DELAY_SECONDS','1')))
                # save_pending carries a completed failed attempt; next save consumes next attempt.
                await self.change(user,rid,lambda c,r:r['steps'][key].update(status='queued'))
    async def successors(self,user,rid):
        def unlock(c,r):
            if r['steps']['body']['status']!='succeeded': return []
            keys=[]
            for k in ('title','tags'):
                if r['steps'][k]['status']=='blocked': r['steps'][k]['status']='queued'
                if r['steps'][k]['status'] not in ('succeeded','awaiting_manual'): keys.append(k)
            return keys
        try: keys=await self.change(user,rid,unlock)
        except Problem: return
        await asyncio.gather(*(self.step(user,rid,k) for k in keys))
    async def run(self,user,rid):
        r=await tx(lambda c:record(c,user,rid))
        if r['steps']['body']['status']=='awaiting_manual': return
        if r['steps']['body']['status']=='succeeded' or await self.step(user,rid,'body'): await self.successors(user,rid)
    async def continue_op(self,user,oid):
        def start(c):
            o=json.loads(c.execute('SELECT data FROM operations WHERE id=?',(oid,)).fetchone()[0]); o['status']='running'; put_operation(c,o); return o
        o=await tx(start); rid=o['record_id']
        async def target(k):
            try: r=await tx(lambda c:record(c,user,rid))
            except Problem: return False
            if r['steps'][k]['status']=='awaiting_manual': return False
            return await self.step(user,rid,k,True)
        results=await asyncio.gather(*(target(k) for k in o['target_steps']))
        def finish(c):
            o['status']='succeeded' if all(results) else 'failed'; o['error']=None if all(results) else info('UPSTREAM_ERROR')
            try: o['result_record_revision']=record(c,user,rid)['revision'] if all(results) else None
            except Problem: pass
            put_operation(c,o)
        await tx(finish); bus.publish(rid)
        if 'body' in o['target_steps'] and results[0]: await self.successors(user,rid)
    async def regenerate(self,user,oid):
        def start(c):
            o=json.loads(c.execute('SELECT data FROM operations WHERE id=?',(oid,)).fetchone()[0]); r=record(c,user,o['record_id'])
            o['status']='running'; put_operation(c,o); r['last_title_operation']=title_summary(o); save_record(c,r); return o,r
        try: o,r=await tx(start)
        except Problem: return
        bus.publish(r['id'])
        try: title=await self.provider.generate('title',r['body_markdown'],oid,user); error=None
        except Exception: title=None; error=info('UPSTREAM_ERROR')
        def finish(c):
            row=c.execute('SELECT deleted FROM records WHERE id=?',(r['id'],)).fetchone()
            if row['deleted']: o.update(status='failed',error=info('RECORD_DELETED')); put_operation(c,o); return
            current=record(c,user,r['id'])
            if current['title_revision']!=r['title_revision'] or current['last_title_operation']['id']!=oid: o.update(status='failed',error=info('TITLE_VERSION_MISMATCH'))
            elif error: o.update(status='failed',error=error)
            else: current['title']=title; current['title_revision']+=1; o['status']='succeeded'
            current['last_title_operation']=title_summary(o); save_record(c,current)
            if o['status']=='succeeded': o['result_record_revision']=current['revision']
            put_operation(c,o)
        await tx(finish); bus.publish(r['id'])
