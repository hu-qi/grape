"""Local deployment preparation and secret-free preflight contracts."""
import importlib.util
import json
from pathlib import Path
import stat
from unittest.mock import Mock

import pytest
import requests
import yaml

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('gitlab_labctl', ROOT / 'deploy/gitlab-lab/labctl.py')
lab = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(lab)


@pytest.fixture
def inventory():
    config = json.loads((ROOT / 'deploy/gitlab-lab/inventory.example.json').read_text())
    config['region'] = 'cn-north-4'
    for role in ('gitlab', 'grape'):
        config[role]['image'] = role + '@sha256:' + 'a' * 64
    config['caddy_image'] = 'caddy@sha256:' + 'b' * 64
    config['mysql_image'] = 'mysql@sha256:' + 'c' * 64
    config['grape'].update(revision='d' * 40, agent_engine='explicit-test-engine', agent_model='explicit-test-model')
    return config


def test_preparation_preserves_existing_credentials(inventory, tmp_path):
    target = tmp_path / 'isolated'
    lab.prepare(inventory, target)
    secret = (target / 'gitlab/secrets/root_password').read_text()
    with pytest.raises(FileExistsError):
        lab.prepare(inventory, target)
    assert (target / 'gitlab/secrets/root_password').read_text() == secret


def test_generated_secret_permissions_and_consistent_database_password(inventory, tmp_path):
    target = tmp_path / 'isolated'
    result = lab.prepare(inventory, target)
    mysql = dict(line.split('=', 1) for line in (target / 'grape/mysql.env').read_text().splitlines())
    runtime = dict(line.split('=', 1) for line in (target / 'grape/runtime.env').read_text().splitlines())
    assert mysql['MYSQL_PASSWORD'] == runtime['DEVOPS_DB_PASSWORD']
    assert stat.S_IMODE((target / 'grape/runtime.env').stat().st_mode) == 0o600
    assert mysql['MYSQL_PASSWORD'] not in json.dumps(result)
    assert result['cloud_resources_created'] is False
    assert result['full_e2e_verified'] is False


def test_missing_target_values_fail_before_writing(tmp_path):
    template = json.loads((ROOT / 'deploy/gitlab-lab/inventory.example.json').read_text())
    with pytest.raises(ValueError):
        lab.prepare(template, tmp_path / 'invalid')
    assert not (tmp_path / 'invalid').exists()


@pytest.mark.parametrize('value', ['gitlab:latest', 'gitlab:19.0.0', 'gitlab@sha256:wrong'])
def test_mutable_or_invalid_image_is_rejected(value):
    with pytest.raises(ValueError):
        lab.image(value)


def test_stacks_publish_only_https_and_persist_data():
    for role in ('gitlab', 'grape'):
        config = yaml.safe_load((ROOT / f'deploy/gitlab-lab/{role}.compose.yaml').read_text())
        assert config['services']['ingress']['ports'] == ['443:443']
        for name, service in config['services'].items():
            if name != 'ingress':
                assert not service.get('ports')
        assert config['volumes']
    assert '.lab-local/' in (ROOT / '.dockerignore').read_text()
    assert '.lab-local/' in (ROOT / '.gitignore').read_text()


def test_probe_does_not_follow_redirect_or_report_agent_acceptance(monkeypatch):
    responses = [Mock(status_code=200, text='GitLab OK'),
                 Mock(status_code=200, json=lambda: {'server': 'ok', 'agent_runtime': 'online', 'server_base_url': 'https://grape.example.test'})]
    monkeypatch.delenv('GITLAB_LAB_TOKEN', raising=False)
    monkeypatch.delenv('GRAPE_LAB_API_KEY', raising=False)
    calls = []

    def send(url, **kwargs):
        calls.append((url, kwargs))
        return responses.pop(0)

    monkeypatch.setattr(lab.requests, 'get', send)
    result = lab.probe('https://grape.example.test', 'https://gitlab.example.test')
    assert result['ok'] is True
    assert result['full_e2e_verified'] is False
    assert all(call[1]['verify'] is True and call[1]['allow_redirects'] is False for call in calls)


def test_probe_missing_agent_fails_preflight(monkeypatch):
    monkeypatch.delenv('GITLAB_LAB_TOKEN', raising=False)
    monkeypatch.delenv('GRAPE_LAB_API_KEY', raising=False)
    responses = [Mock(status_code=200, text='GitLab OK'),
                 Mock(status_code=200, json=lambda: {'server': 'ok', 'agent_runtime': 'missing', 'server_base_url': 'https://grape.example.test'})]
    monkeypatch.setattr(lab.requests, 'get', lambda *a, **k: responses.pop(0))
    assert lab.probe('https://grape.example.test', 'https://gitlab.example.test')['ok'] is False


def test_probe_errors_never_echo_credentials(monkeypatch):
    monkeypatch.setenv('GITLAB_LAB_TOKEN', 'secret-test-token')
    monkeypatch.delenv('GRAPE_LAB_API_KEY', raising=False)

    def fail(*args, **kwargs):
        raise requests.ConnectionError('secret-test-token remote echo')

    monkeypatch.setattr(lab.requests, 'get', fail)
    result = lab.probe('https://grape.example.test', 'https://gitlab.example.test')
    assert result['ok'] is False
    assert 'secret-test-token' not in json.dumps(result)


def test_in_repository_output_outside_ignored_directory_is_rejected(inventory):
    with pytest.raises(ValueError, match='under .lab-local'):
        lab.prepare(inventory, ROOT / 'deploy/accidental-secrets')


def test_generated_compose_passes_docker_validation(inventory, tmp_path):
    import shutil
    import subprocess
    if not shutil.which('docker'):
        pytest.skip('Docker CLI required for Compose validation')
    target = tmp_path / 'rendered'
    lab.prepare(inventory, target)
    for role in ('gitlab', 'grape'):
        result = subprocess.run(['docker', 'compose', 'config', '--quiet'], cwd=target / role,
                                capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, f'{role} Compose configuration rejected'


def test_opencode_config_locks_model_without_embedding_credentials(inventory, tmp_path):
    inventory['grape']['agent_engine'] = 'opencode'
    target = tmp_path / 'isolated'
    lab.prepare(inventory, target)
    config = json.loads((target / 'grape/configs/opencode.json').read_text())
    assert config['model'] == config['small_model'] == 'lab/explicit-test-model'
    assert config['enabled_providers'] == ['lab']
    assert config['provider']['lab']['options']['apiKey'] == '{env:LAB_MODEL_API_KEY}'
    assert (target / 'grape/engine.env').read_text() == ''
    assert 'OPENCODE_CONFIG=/app/configs/opencode.json' in (target / 'grape/runtime.env').read_text()
