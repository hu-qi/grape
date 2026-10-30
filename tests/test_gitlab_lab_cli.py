"""Verify the lab's CLI artifact before installing it into an isolated Python."""
import hashlib
import importlib.util
import os
from pathlib import Path
import subprocess
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('lab_cli', ROOT / 'deploy/gitlab-lab/install_cli.py')
lab_cli = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(lab_cli)


def wheel(directory):
    path = directory / 'grape-0.0.1-py3-none-any.whl'
    with zipfile.ZipFile(path, 'w') as archive:
        for name, content in {
            'src/__init__.py': '',
            'src/cli.py': 'print("isolated-cli")\n',
            'grape-0.0.1.dist-info/METADATA': 'Metadata-Version: 2.1\nName: grape\nVersion: 0.0.1\n',
            'grape-0.0.1.dist-info/WHEEL': 'Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n',
            'grape-0.0.1.dist-info/RECORD': '',
        }.items():
            archive.writestr(name, content)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_cli_hash_mismatch_fails_before_install(tmp_path):
    wheel(tmp_path)
    with pytest.raises(ValueError, match='SHA256'):
        lab_cli.verified_wheel(tmp_path, '0' * 64)


def test_missing_or_ambiguous_cli_artifact_is_rejected(tmp_path):
    with pytest.raises(ValueError, match='exactly one'):
        lab_cli.verified_wheel(tmp_path, '0' * 64)
    digest = wheel(tmp_path)
    (tmp_path / 'grape-0.0.2-py3-none-any.whl').write_bytes(b'other')
    with pytest.raises(ValueError, match='exactly one'):
        lab_cli.verified_wheel(tmp_path, digest)


@pytest.mark.skipif(os.name != 'posix', reason='Installer targets the Linux lab image')
def test_cli_ignores_application_src_and_pythonpath(tmp_path, monkeypatch):
    artifact = tmp_path / 'artifacts'
    artifact.mkdir()
    digest = wheel(artifact)
    app = tmp_path / 'app'
    (app / 'src').mkdir(parents=True)
    (app / 'src/__init__.py').write_text('raise RuntimeError("wrong application package")')
    monkeypatch.setenv('PIP_NO_INDEX', '1')
    launcher = tmp_path / 'grape'
    lab_cli.install(artifact, digest, tmp_path / 'venv', launcher)
    monkeypatch.setenv('PYTHONPATH', str(app))
    result = subprocess.run([str(launcher), '--version'], cwd=app, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == 'isolated-cli'
