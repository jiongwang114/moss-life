"""Single-worker production entry point, trusting only this container's host gateway."""
import ipaddress
import os
from pathlib import Path

import uvicorn


def trusted_proxies():
    peers={'127.0.0.1'}
    routes=Path('/proc/net/route')
    if routes.exists():
        for line in routes.read_text().splitlines()[1:]:
            values=line.split()
            if len(values)>3 and values[1]=='00000000' and int(values[3],16)&2:
                peers.add(str(ipaddress.IPv4Address(int(values[2],16).to_bytes(4,'little'))))
    return os.getenv('MOSS_TRUSTED_PROXIES',','.join(sorted(peers)))


if __name__=='__main__':
    uvicorn.run('backend.main:app',host='0.0.0.0',port=8000,workers=1,
                proxy_headers=True,forwarded_allow_ips=trusted_proxies(),limit_concurrency=32)
