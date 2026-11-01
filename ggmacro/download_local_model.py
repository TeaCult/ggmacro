"""IPv4 fallback for downloading the selected model into the project cache.

Uses the official Ollama registry and verifies every blob's size and SHA256
before publishing a manifest. Useful when Ollama's IPv6 downloads time out.
"""
import hashlib
import argparse
import json
from pathlib import Path
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', default='d1', help='Model name in library (e.g. d1, qwen3.5)')
    parser.add_argument('--tag', default='latest', help='Model tag (e.g. latest, 3b, 2b)')
    args = parser.parse_args()
    model = args.model
    tag = args.tag
    root = Path(__file__).resolve().parents[1] / '.local/models'
    blobs = root / 'blobs'
    blobs.mkdir(parents=True, exist_ok=True)
    registry = f'https://registry.ollama.ai/v2/library/{model}'
    print(f'Fetching manifest for {model}:{tag} from {registry}...', flush=True)
    manifest_bytes = subprocess.check_output([
        'curl', '-4', '--fail', '--silent', '--show-error', '--location',
        '--max-time', '60', registry + '/manifests/' + tag])
    manifest = json.loads(manifest_bytes)
    for item in [manifest['config'], *manifest['layers']]:
        digest = item['digest']
        algorithm, expected = digest.split(':')
        if algorithm != 'sha256' or len(expected) != 64 or any(
                c not in '0123456789abcdef' for c in expected):
            raise ValueError('Invalid registry digest')
        target = blobs / digest.replace(':', '-')
        if not target.exists():
            partial = target.with_suffix('.download')
            size_mb = item['size'] / (1024 * 1024)
            print(f'Downloading {item["mediaType"]} ({size_mb:.1f} MB)...', flush=True)
            subprocess.run([
                'curl', '-4', '--fail', '--silent', '--show-error', '--location',
                '--retry', '2', '--connect-timeout', '15', '--max-time', '900',
                '--output', str(partial), registry + '/blobs/' + digest], check=True)
            source = partial
        else:
            source = target
        checksum = hashlib.sha256()
        with source.open('rb') as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                checksum.update(block)
        if source.stat().st_size != item['size'] or checksum.hexdigest() != expected:
            raise ValueError(f'Blob integrity check failed: {digest}')
        if source != target:
            source.replace(target)
        print('Verified', digest, flush=True)
    destination = root / f'manifests/registry.ollama.ai/library/{model}' / tag
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix('.download')
    temporary.write_bytes(manifest_bytes)
    temporary.replace(destination)
    print(f'Ready: {model}:{tag}', flush=True)



if __name__ == '__main__':
    main()
