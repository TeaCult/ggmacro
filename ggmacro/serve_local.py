"""Start a project-scoped Ollama server on Windows, macOS, or Linux."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='127.0.0.1:11435')
    parser.add_argument('--ollama', help='Path to an Ollama executable')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    bundled = root / '.local' / 'ollama' / 'bin' / ('ollama.exe' if os.name == 'nt' else 'ollama')
    executable = args.ollama or (str(bundled) if bundled.is_file() else shutil.which('ollama'))
    if not executable:
        parser.error('Install Ollama or supply --ollama PATH')
    env = os.environ.copy()
    env.update(OLLAMA_HOST=args.host, OLLAMA_MODELS=str(root / '.local/models'),
               OLLAMA_NO_CLOUD='1', OLLAMA_NUM_PARALLEL='1',
               OLLAMA_MAX_LOADED_MODELS='1', OLLAMA_CONTEXT_LENGTH='4096')
    try:
        return subprocess.call([executable, 'serve'], env=env)
    except KeyboardInterrupt:
        return 130


if __name__ == '__main__':
    raise SystemExit(main())
