"""Validate a signed delivery APK and write metadata; never upload or deploy anything."""
import argparse
import importlib.util
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.app_updates import validate_release

spec = importlib.util.spec_from_file_location('moss_publisher', ROOT / 'scripts/publish-release.py')
publisher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publisher)


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('apk', nargs='?')
    parser.add_argument('--download-url', help='Defaults to the MOSS server package endpoint; may override with an HTTPS release asset')
    args = parser.parse_args()
    name, code = publisher.version()
    apk = Path(args.apk).resolve() if args.apk else ROOT / f'交付/MOSS-{name}-安卓兼容版.apk'
    digest = publisher.validate_apk(apk, name, code)
    source = (ROOT / 'moss-prototype/release.js').read_text(encoding='utf-8')
    notes = re.findall(r"^\s*'([^'\n]+)'[,]?\s*$", source, re.M)[:20]
    value = validate_release(dict(package_name='com.moss.mobile', version_name=name,
        version_code=code, min_sdk=26, size_bytes=apk.stat().st_size, sha256=digest,
        download_url=args.download_url or f'https://note.jiongplay.cn/api/v1/app-updates/android/packages/{code}',
        notes=notes))
    output = ROOT / f'交付/MOSS-{name}-android-update.json'
    output.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(dict(manifest=str(output), uploaded=False, server_changed=False,
        next_step='Upload and verify the APK before publishing this manifest on the server'), ensure_ascii=False))


if __name__ == '__main__':
    main()
