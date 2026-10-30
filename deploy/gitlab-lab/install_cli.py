"""Install an operator-supplied, hash-pinned Grape CLI without package collisions."""
import argparse
import hashlib
from pathlib import Path
import re
import shlex
import subprocess
import venv


def verified_wheel(directory, expected_sha256):
    if not re.fullmatch(r'[a-f0-9]{64}', expected_sha256):
        raise ValueError('Explicit lowercase SHA256 required for the CLI wheel')
    wheels = list(Path(directory).glob('*.whl'))
    if len(wheels) != 1:
        raise ValueError('Supply exactly one reviewed Grape CLI wheel')
    wheel = wheels[0]
    if hashlib.sha256(wheel.read_bytes()).hexdigest() != expected_sha256:
        raise ValueError('Grape CLI wheel SHA256 mismatch')
    return wheel.resolve()


def install(directory, expected_sha256, environment, launcher):
    wheel = verified_wheel(directory, expected_sha256)
    environment, launcher = Path(environment).resolve(), Path(launcher)
    venv.EnvBuilder(with_pip=True).create(environment)
    python = environment / 'bin/python'
    subprocess.run([str(python), '-I', '-m', 'pip', 'install', '--no-cache-dir', str(wheel)], check=True)
    # Both projects use a top-level `src` package. Isolated Python excludes the
    # task's working directory and PYTHONPATH, keeping the CLI's imports local.
    launcher.write_text('#!/bin/sh\nexec ' + shlex.quote(str(python)) + ' -I -m src.cli "$@"\n')
    launcher.chmod(0o755)
    subprocess.run([str(launcher.resolve()), '--version'], check=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', required=True, type=Path)
    parser.add_argument('--sha256', required=True)
    args = parser.parse_args()
    install(args.directory, args.sha256, '/opt/grape-cli', '/usr/local/bin/grape')
