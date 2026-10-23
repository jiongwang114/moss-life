"""Owner-only local CLI: python -m backend.manage invite --uses 1 --days 7."""
import argparse
import json
import hashlib
import os
import tempfile
from pathlib import Path
from .database import initialize,connect
from .accounts import mint_invite
from .app_updates import validate_release, MAX_MANIFEST_BYTES, release_file
from .database import DATA


def publish_android(source=None, apk=None):
    """Local operator action, no HTTP upload endpoint and no account database mutation."""
    value = None
    if source is not None:
        with Path(source).open('rb') as stream:
            raw = stream.read(MAX_MANIFEST_BYTES + 1)
        if len(raw) > MAX_MANIFEST_BYTES:
            raise ValueError('Manifest too large')
        value = validate_release(json.loads(raw))
    target = release_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    if apk is not None:
        if value is None:
            raise ValueError('Cannot upload an APK without release metadata')
        packages = target.parent / 'apks'
        packages.mkdir(exist_ok=True)
        final = packages / f"{value['version_code']}.apk"
        staged = None
        try:
            digest = hashlib.sha256(); size = 0
            with Path(apk).open('rb') as incoming, tempfile.NamedTemporaryFile(dir=packages, prefix='.apk-', delete=False) as out:
                staged = Path(out.name)
                while chunk := incoming.read(65536):
                    size += len(chunk)
                    if size > value['size_bytes']:
                        raise ValueError('APK size differs from release metadata')
                    digest.update(chunk); out.write(chunk)
                out.flush(); os.fsync(out.fileno())
            if size != value['size_bytes'] or digest.hexdigest() != value['sha256']:
                raise ValueError('APK checksum differs from release metadata')
            if final.exists():
                with final.open('rb') as old:
                    if hashlib.file_digest(old, 'sha256').hexdigest() != value['sha256']:
                        raise ValueError('Version already has a different APK; increase version_code')
            else:
                staged.chmod(0o644)
                os.replace(staged, final)
        finally:
            if staged is not None:
                staged.unlink(missing_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=target.parent,
                                         prefix='.android-', delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return value

def main():
    parser=argparse.ArgumentParser(description='MOSS 服务端账号管理')
    commands=parser.add_subparsers(dest='command',required=True)
    invite=commands.add_parser('invite',help='生成邀请码（请勿写入源码或公开分享）')
    invite.add_argument('--uses',type=int,default=1)
    invite.add_argument('--days',type=int,default=7)
    publish=commands.add_parser('publish-android',help='原子发布已上传安装包的版本信息')
    publish.add_argument('manifest',help='经过校验的 Android 更新 JSON 文件')
    publish.add_argument('--apk',help='同时校验并发布到现有服务器的安装包（同版本不可覆盖）')
    commands.add_parser('withdraw-android',help='撤回更新提示，不影响现有应用及用户数据')
    args=parser.parse_args()
    if args.command in ('publish-android','withdraw-android'):
        value=publish_android(args.manifest if args.command=='publish-android' else None,
                              args.apk if args.command=='publish-android' else None)
        print('Android update: '+(value['version_name'] if value else 'withdrawn'))
        return
    initialize()
    with connect() as c:
        print(mint_invite(c,args.uses,args.days))

if __name__=='__main__': main()
