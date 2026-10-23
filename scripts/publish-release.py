"""Publish the signed delivery APK to its versioned GitHub Release."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
REPO = 'jiongwang114/moss-life'
ANDROID = ROOT / '手机端可用落地执行/android-app'


def run(args):
    result = subprocess.run([str(x) for x in args], cwd=ROOT, capture_output=True)
    if result.returncode:
        raise RuntimeError(result.stderr.decode('utf-8', errors='replace').strip())
    return result.stdout.decode('utf-8', errors='replace')


def version():
    gradle = (ANDROID / 'app/build.gradle.kts').read_text(encoding='utf-8')
    name = re.search(r'versionName\s*=\s*"(\d+\.\d+\.\d+)"', gradle).group(1)
    code = int(re.search(r'versionCode\s*=\s*(\d+)', gradle).group(1))
    frontend = (ROOT / 'moss-prototype/release.js').read_text(encoding='utf-8')
    if re.search(r"version:\s*'([^']+)'", frontend).group(1) != name:
        raise RuntimeError('Android and frontend versions differ')
    return name, code


def validate_apk(apk, name, code):
    if apk.parent != (ROOT / '交付').resolve() or apk.suffix != '.apk':
        raise RuntimeError('Publish only a final APK directly inside 交付/')
    tools = ROOT / '手机端可用落地执行/android-sdk/build-tools/35.0.1'
    java = Path(os.environ.get('JAVA_HOME', r'C:\Program Files\Microsoft\jdk-17.0.20.101-hotspot')) / 'bin/java.exe'
    badging = run([tools / 'aapt2.exe', 'dump', 'badging', apk])
    if f"name='com.moss.mobile' versionCode='{code}' versionName='{name}'" not in badging:
        raise RuntimeError('APK package/version differs from source')
    if not re.search(r"(?:minSdkVersion|sdkVersion):'26'", badging) or 'application-debuggable' in badging:
        raise RuntimeError('Expected a non-debuggable Android 8+ release APK')
    for low, high in [('26', '27'), ('28', '36')]:
        run([java, '-jar', tools / 'lib/apksigner.jar', 'verify', '--min-sdk-version', low,
             '--max-sdk-version', high, apk])
    with zipfile.ZipFile(apk) as package:
        bundled = package.read('assets/web/release.js').decode('utf-8')
        if not re.search(r'version:\s*[\x22\x27]' + re.escape(name) + r'[\x22\x27]', bundled):
            raise RuntimeError('Bundled frontend version differs from source')
        for entry in package.namelist():
            if any(x in entry.lower() for x in ['.private', 'node_modules', '.keystore', '.jks', '.env']):
                raise RuntimeError('Private or development artifact inside APK')
    return hashlib.sha256(apk.read_bytes()).hexdigest()


def notes(name, apk, digest):
    source = (ROOT / 'moss-prototype/release.js').read_text(encoding='utf-8')
    entries = re.findall(r"^\s*'([^'\n]+)'[,]?\s*$", source, re.M)
    return '\n'.join([
        f'MOSS {name} · Android', '',
        '在下方 Assets 中下载 APK，打开安装后，使用有效的邀请码注册或登录已有账号。',
        '', '## 版本说明', '', *['- ' + item for item in entries], '',
        '## 安装与使用', '',
        '- 适配目标：Android 8.0 及以上；不同手机及厂商 WebView 仍需实际验证。',
        '- 客户端连接 MOSS 提供的服务，无需自行部署或配置 AI 密钥。',
        '- 安装包已完成本地构建及签名检查；发布不代表所有设备均已通过真机验收。',
        '- 网页版：https://note.jiongplay.cn', '',
        f'安装包：`{apk.name}`', f'SHA-256：`{digest}`', '',
    ])


def api(path, optional=False):
    result = subprocess.run(['gh', 'api', path], cwd=ROOT, capture_output=True)
    if result.returncode:
        if optional and b'(HTTP 404)' in result.stderr:
            return None
        raise RuntimeError(result.stderr.decode('utf-8', errors='replace'))
    return json.loads(result.stdout)


def check_asset(release, apk, digest):
    assets = [item for item in release['assets'] if item['name'] == apk.name]
    if len(assets) != 1:
        raise RuntimeError('Uploaded APK is missing or duplicated')
    asset = assets[0]
    if asset.get('digest') != 'sha256:' + digest or asset['size'] != apk.stat().st_size:
        raise RuntimeError('Remote APK differs from local APK; use a new version, never overwrite')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('apk', nargs='?')
    parser.add_argument('--dry-run', action='store_true', help='Validate locally without contacting GitHub')
    args = parser.parse_args()
    name, code = version()
    apk = Path(args.apk).resolve() if args.apk else ROOT / f'交付/MOSS-{name}-安卓兼容版.apk'
    digest = validate_apk(apk, name, code)
    tag = 'v' + name
    body = notes(name, apk, digest)
    if args.dry_run:
        print(json.dumps(dict(tag=tag, apk=apk.name, sha256=digest, bytes=apk.stat().st_size,
                              remote_changed=False), ensure_ascii=False))
        return
    # Publish only from committed, pushed source. Never auto-stage local/private files.
    run(['git', 'diff', '--exit-code'])
    run(['git', 'diff', '--cached', '--exit-code'])
    head = run(['git', 'rev-parse', 'HEAD']).strip()
    if api(f'repos/{REPO}/git/ref/heads/main')['object']['sha'] != head:
        raise RuntimeError('Commit and push reviewed source to main before publishing its APK')
    release = api(f'repos/{REPO}/releases/tags/{tag}', optional=True)
    if release is not None:
        ref = api(f'repos/{REPO}/git/ref/tags/{tag}')
        target = ref['object']
        if target['type'] == 'tag':
            target = api(f'repos/{REPO}/git/tags/{target["sha"]}')['object']
        if target['sha'] != head:
            raise RuntimeError('This version is already tied to another commit; increment the version')
        if release['draft'] or release['prerelease']:
            raise RuntimeError('An unfinished release exists; inspect it before retrying')
        check_asset(release, apk, digest)
        print(release['html_url'])
        return
    with tempfile.TemporaryDirectory(prefix='moss-release-') as temp:
        note_file = Path(temp) / 'notes.md'
        note_file.write_text(body, encoding='utf-8')
        # gh uploads assets to a draft before publishing the completed release.
        run(['gh', 'release', 'create', tag, apk, '--repo', REPO, '--target', head,
             '--title', f'MOSS {name}', '--notes-file', note_file])
    release = api(f'repos/{REPO}/releases/tags/{tag}')
    check_asset(release, apk, digest)
    print(release['html_url'])


if __name__ == '__main__':
    main()
