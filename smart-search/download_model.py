"""Download a pinned public model once; never sends journal data over the network."""
import argparse
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import urllib.request

FILES = ['config.json', 'tokenizer.json', 'tokenizer_config.json',
         'special_tokens_map.json', 'onnx/model_quantized.onnx']


def download(directory, profile=None):
    config = json.loads(Path(__file__).with_name('model-profiles.json').read_text(encoding='utf-8'))
    selected = config['profiles'][profile or config['default']]
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    def fetch(name):
        target = directory / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            temporary = target.with_suffix(target.suffix + '.part')
            request = urllib.request.Request(
                f'https://huggingface.co/{selected["model"]}/resolve/{selected["revision"]}/{name}',
                headers={'User-Agent': 'Moss model setup'})
            with urllib.request.urlopen(request, timeout=120) as response, temporary.open('wb') as output:
                expected = response.headers.get('Content-Length')
                while chunk := response.read(1024 * 1024):
                    output.write(chunk)
            if expected and temporary.stat().st_size != int(expected):
                raise RuntimeError('Incomplete model download')
            temporary.replace(target)
        with target.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        return dict(file=name, bytes=target.stat().st_size, sha256=digest)
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        manifest = list(pool.map(fetch, FILES))
    (directory / 'moss-model-manifest.json').write_text(
        json.dumps(dict(model=selected['model'], revision=selected['revision'], files=manifest), indent=2), encoding='utf-8')
    print(f'Model ready: {len(manifest)} files, {sum(x["bytes"] for x in manifest)} bytes')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--directory', default=os.getenv('MOSS_SEARCH_MODEL_DIR', '.smart-env/models/bge-small-zh-v1.5'))
    parser.add_argument('--profile', choices=['multilingual', 'bge-zh'])
    options=parser.parse_args()
    download(options.directory, options.profile)
