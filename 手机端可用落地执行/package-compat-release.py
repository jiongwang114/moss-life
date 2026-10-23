"""Package the compatibility release with the existing update identity only."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import zipfile
import io
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
TASK = ROOT / '手机端可用落地执行'
PRIVATE = ROOT / '.private/android-signing'
GRADLE = (TASK / 'android-app/app/build.gradle.kts').read_text(encoding='utf-8')
VERSION = re.search(r'versionName\s*=\s*"(\d+\.\d+\.\d+)"', GRADLE).group(1)
VERSION_CODE = int(re.search(r'versionCode\s*=\s*(\d+)', GRADLE).group(1))
OUTPUT = ROOT / f'交付/MOSS-{VERSION}-安卓兼容版.apk'


def previous_release():
    current = tuple(map(int, VERSION.split('.')))
    candidates = []
    for apk in (ROOT / '交付').glob('MOSS-*.apk'):
        match = re.match(r'MOSS-(\d+\.\d+\.\d+)-', apk.name)
        if match:
            number = tuple(map(int, match.group(1).split('.')))
            if number < current:
                candidates.append((number, apk))
    if not candidates:
        raise RuntimeError('Keep the previous signed release APK in 交付/ to verify update identity')
    return max(candidates)[1]


BASELINE = previous_release()
JAVA = Path(os.environ.get('JAVA_HOME', r'C:\Program Files\Microsoft\jdk-17.0.20.101-hotspot')) / 'bin/java.exe'
TOOLS = TASK / 'android-sdk/build-tools/35.0.1'
SOURCE = TASK / 'android-app/app/build/outputs/apk/release/app-release-unsigned.apk'
LOGS = ROOT / '.test-runs' / ('compat-' + VERSION)


def run(args, env=None):
    process_env = dict(env if env is not None else os.environ)
    process_env['PYTHONIOENCODING'] = 'utf-8'
    result = subprocess.run([str(x) for x in args], env=process_env, capture_output=True)
    if result.returncode:
        # Commands can contain signing flags; never print secret environment values.
        raise RuntimeError(result.stderr.decode(errors='replace')[:1500])
    return result.stdout.decode('utf-8', errors='replace')


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--publish', action='store_true', help='Also publish reviewed, committed and pushed source to GitHub')
    args = parser.parse_args()
    LOGS.mkdir(parents=True, exist_ok=True)
    for item in [SOURCE, BASELINE, PRIVATE/'signing.private.json', PRIVATE/'legacy-debug.keystore', PRIVATE/'moss-release.jks', PRIVATE/'moss.lineage']:
        assert item.is_file(), f'Required existing artifact missing: {item.name}'
    prior_badging = run([TOOLS/'aapt2.exe', 'dump', 'badging', BASELINE])
    prior_code = int(re.search(r"versionCode='(\d+)'", prior_badging).group(1))
    if VERSION_CODE <= prior_code:
        raise RuntimeError('Increase versionCode above the previous release before packaging')
    env = dict(os.environ)
    env['MOSS_COMPAT_SIGNING_PASSWORD'] = json.loads((PRIVATE/'signing.private.json').read_text(encoding='utf-8'))['password']
    signer = [JAVA, '-jar', TOOLS/'lib/apksigner.jar']
    old = ['--ks', PRIVATE/'legacy-debug.keystore', '--ks-key-alias', 'androiddebugkey', '--ks-pass', 'pass:android', '--key-pass', 'pass:android']
    new = ['--ks', PRIVATE/'moss-release.jks', '--ks-key-alias', 'moss', '--ks-pass', 'env:MOSS_COMPAT_SIGNING_PASSWORD', '--key-pass', 'env:MOSS_COMPAT_SIGNING_PASSWORD']
    run(signer+['sign', '--out', OUTPUT, '--lineage', PRIVATE/'moss.lineage', '--rotation-min-sdk-version', '28', '--debuggable-apk-permitted', 'false']+old+['--next-signer']+new+[SOURCE],env)
    signatures = []
    for low, high in [('26','27'),('28','36')]:
        text = run(signer+['verify', '--verbose', '--print-certs', '--min-sdk-version', low, '--max-sdk-version', high, OUTPUT],env)
        original = run(signer+['verify', '--print-certs', '--min-sdk-version', low, '--max-sdk-version', high, BASELINE],env)
        digests = lambda value: re.findall(r'Signer #\d+ certificate SHA-256 digest: ([0-9a-f]+)', value)
        assert digests(text) and digests(text)==digests(original), 'Existing APK update certificate changed'
        (LOGS/f'signature-{low}-{high}.txt').write_text(text,encoding='utf-8')
        signatures.append(f'{low}-{high}')
    badging = run([TOOLS/'aapt2.exe','dump','badging',OUTPUT])
    assert f"name='com.moss.mobile' versionCode='{VERSION_CODE}' versionName='{VERSION}'" in badging
    assert "minSdkVersion:'26'" in badging
    assert 'application-debuggable' not in badging
    (LOGS/'release-badging.txt').write_text(badging,encoding='utf-8')
    manifest = run([TOOLS/'aapt2.exe','dump','xmltree',OUTPUT,'--file','AndroidManifest.xml'])
    assert re.search(r'usesCleartextTraffic[^\r\n]*=false',manifest)
    assert re.search(r'allowBackup[^\r\n]*=false',manifest)
    assert not re.search(r'debuggable[^\r\n]*=true',manifest)
    (LOGS/'release-manifest.txt').write_text(manifest,encoding='utf-8')
    compiled = TASK/'android-app/app/build/compat-frontend'
    with zipfile.ZipFile(OUTPUT) as package, zipfile.ZipFile(BASELINE) as original:
        assets = json.loads(package.read('assets/web/assets.json'))
        assert all(package.read('assets/web/'+name)==(compiled/name).read_bytes() for name in assets)
        assert 'boot.js' in assets and 'port-host.js' in assets and 'compat.js' in assets
        assert not any('.private' in name or 'node_modules' in name or '.keystore' in name or '.jks' in name for name in package.namelist())
        # Build aapt resource paths can change; compare the icon source files in
        # the new APK to the baseline icon bytes regardless of resource name.
        def pixels(data):
            image = Image.open(io.BytesIO(data)).convert('RGBA')
            return image.size, hashlib.sha256(image.tobytes()).hexdigest()
        icon_sources = [pixels(p.read_bytes()) for p in (TASK/'android-app/app/src/main/res').rglob('*.png')]
        packed_png = {pixels(package.read(name)) for name in package.namelist() if name.endswith('.png')}
        baseline_png = {pixels(original.read(name)) for name in original.namelist() if name.endswith('.png')}
        # aapt can normalize invisible RGB values in round PNGs. Compare the
        # packaged pixels against the installed-release baseline instead.
        assert icon_sources and packed_png==baseline_png, 'Packaged launcher artwork changed'
    report = dict(apk=str(OUTPUT.relative_to(ROOT)), version=VERSION, version_code=VERSION_CODE, min_android='8.0', min_sdk=26,
                  bytes=OUTPUT.stat().st_size, sha256=hashlib.sha256(OUTPUT.read_bytes()).hexdigest(),
                  signature_verified_api_ranges=signatures, same_update_signers_as_previous=True,
                  bundled_compat_assets_match_build=True, launcher_artwork_unchanged=True,
                  device_tested=False, server_deployed=False)
    (ROOT/f'交付/MOSS-{VERSION}-安装包校验.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))
    print(run([sys.executable, ROOT/'scripts/prepare-app-update.py', OUTPUT]).strip())
    # Packaging is a local action; online publication is explicit.
    if args.publish:
        print(run([sys.executable, ROOT/'scripts/publish-release.py', OUTPUT]).strip())


if __name__=='__main__':
    main()
