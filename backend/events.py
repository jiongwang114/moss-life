import asyncio
from collections import defaultdict

class Bus:
    def __init__(self): self.listeners=defaultdict(set)
    def subscribe(self,key):
        q=asyncio.Queue(maxsize=1); self.listeners[key].add(q); return q
    def unsubscribe(self,key,q):
        self.listeners[key].discard(q)
        if not self.listeners[key]: self.listeners.pop(key,None)
    def publish(self,key):
        for q in tuple(self.listeners.get(key,())):
            if not q.full(): q.put_nowait(True)
bus=Bus()
