"""Owner-only local CLI: python -m backend.manage invite --uses 1 --days 7."""
import argparse
from .database import initialize,connect
from .accounts import mint_invite

def main():
    parser=argparse.ArgumentParser(description='MOSS 服务端账号管理')
    commands=parser.add_subparsers(dest='command',required=True)
    invite=commands.add_parser('invite',help='生成邀请码（请勿写入源码或公开分享）')
    invite.add_argument('--uses',type=int,default=1)
    invite.add_argument('--days',type=int,default=7)
    args=parser.parse_args()
    initialize()
    with connect() as c:
        print(mint_invite(c,args.uses,args.days))

if __name__=='__main__': main()
