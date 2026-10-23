"""Public Android release metadata. Publishing is explicit; never poll GitHub per user."""
import json
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

from .database import DATA
from .domain import Problem

MAX_APK_BYTES = 200 * 1024 * 1024
MAX_MANIFEST_BYTES = 32768


def release_file():
    return Path(os.getenv('MOSS_ANDROID_RELEASE_FILE', str(DATA / 'releases/android.json')))


def package_file(version_code):
    if type(version_code) is not int or not 1 <= version_code <= 2100000000:
        raise Problem('HTTP_404', 404)
    path = release_file().parent / 'apks' / f'{version_code}.apk'
    if not path.is_file():
        raise Problem('HTTP_404', 404)
    return path


def validate_release(value):
    """Keep this contract in sync with AndroidRelease (the client compares versionCode)."""
    if not isinstance(value, dict):
        raise ValueError('Release must be an object')
    required = {'package_name', 'version_name', 'version_code', 'min_sdk',
                'download_url', 'size_bytes', 'sha256', 'notes'}
    if set(value) != required or value['package_name'] != 'com.moss.mobile':
        raise ValueError('Unexpected release fields or package')
    for key, low, high in [('version_code', 1, 2100000000), ('min_sdk', 26, 100),
                           ('size_bytes', 1, MAX_APK_BYTES)]:
        if type(value[key]) is not int or not low <= value[key] <= high:
            raise ValueError('Invalid ' + key)
    if not isinstance(value['version_name'], str) or not re.fullmatch(r'\d{1,5}\.\d{1,5}\.\d{1,5}', value['version_name']):
        raise ValueError('Invalid version name')
    if not isinstance(value['sha256'], str) or not re.fullmatch(r'[0-9a-f]{64}', value['sha256']):
        raise ValueError('Invalid SHA-256')
    url = value['download_url']
    if not isinstance(url, str) or len(url) > 2048 or any(c.isspace() or ord(c) < 32 for c in url):
        raise ValueError('Invalid download URL')
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ValueError('Download must use a plain HTTPS URL')
    if parsed.port not in (None, 443):
        raise ValueError('Download must use HTTPS port 443')
    notes = value['notes']
    if not isinstance(notes, list) or not 1 <= len(notes) <= 20 or any(
        not isinstance(note, str) or not note.strip() or len(note) > 300 for note in notes
    ):
        raise ValueError('Invalid release notes')
    return value


def latest_android_release():
    # Runtime data volume: replacing this file atomically publishes or withdraws a release.
    path = release_file()
    try:
        with path.open('rb') as stream:
            raw = stream.read(MAX_MANIFEST_BYTES + 1)
        if len(raw) > MAX_MANIFEST_BYTES:
            raise ValueError('Manifest too large')
        value = json.loads(raw)
        return {'release': None if value is None else validate_release(value)}
    except FileNotFoundError:
        return {'release': None}
    except (OSError, ValueError, TypeError):
        # Never advertise corrupt metadata as "already up to date".
        raise Problem('APP_UPDATE_UNAVAILABLE', 503)
