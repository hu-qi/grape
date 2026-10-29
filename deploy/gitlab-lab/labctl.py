#!/usr/bin/env python3
"""Prepare isolated ECS stacks and collect read-only preflight evidence.

No cloud resource creation, application writes or full Agent acceptance is implied.
Credentials come from generated local files or environment variables, never argv.
"""
import argparse
import base64
import json
import os
from pathlib import Path
import re
import secrets
import shutil
from urllib.parse import urlsplit

import requests

TEMPLATES = Path(__file__).resolve().parent


def hostname(value):
    if not isinstance(value, str) or len(value) > 253 or not re.fullmatch(r'(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,63}', value):
        raise ValueError('A DNS hostname without scheme, path or port is required')
    return value.lower()


def image(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-zA-Z0-9._:/-]+@sha256:[a-f0-9]{64}', value):
        raise ValueError('Every image must use an immutable sha256 digest')
    return value


def validate_inventory(config):
    if not re.fullmatch(r'[a-z]{2}-[a-z]+-\d+', config.get('region', '')):
        raise ValueError('Explicit Huawei Cloud region required')
    if not re.fullmatch(r'[a-z][a-z0-9-]{0,39}', config.get('deployment_id', '')):
        raise ValueError('Invalid deployment ID')
    for role in ('gitlab', 'grape'):
        hostname(config[role]['host'])
        image(config[role]['image'])
    if config['gitlab']['host'].lower() == config['grape']['host'].lower():
        raise ValueError('GitLab and Grape require separate hostnames')
    for key in ('agent_engine', 'agent_model'):
        if not config['grape'].get(key) or config['grape'][key] == 'REQUIRED':
            raise ValueError('Explicit Agent engine and model required')
    if not re.fullmatch(r'[a-f0-9]{40}', config['grape'].get('revision', '')):
        raise ValueError('Full tested Grape commit SHA required')
    image(config['caddy_image'])
    image(config['mysql_image'])
    return config


def write_private(path, text):
    # Exclusive creation prevents credential rotation/overwrite on a second run.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w') as stream:
        stream.write(text)


def prepare(config, target):
    validate_inventory(config)
    target = Path(target)
    root = TEMPLATES.parents[1]
    if target.resolve().is_relative_to(root) and target.resolve().relative_to(root).parts[0] != '.lab-local':
        raise ValueError('In-repository generated files must be under .lab-local')
    target.mkdir(mode=0o700, parents=True, exist_ok=False)
    password = secrets.token_urlsafe(36)
    jwt = secrets.token_urlsafe(48)
    for role in ('gitlab', 'grape'):
        folder = target / role
        folder.mkdir(mode=0o700)
        (folder / 'tls').mkdir(mode=0o700)
        shutil.copyfile(TEMPLATES / f'{role}.compose.yaml', folder / 'compose.yaml')
        shutil.copyfile(TEMPLATES / 'Caddyfile', folder / 'Caddyfile')
        env = {'LAB_HOST': hostname(config[role]['host']), 'CADDY_IMAGE': config['caddy_image'],
               role.upper() + '_IMAGE': config[role]['image']}
        if role == 'grape':
            env['MYSQL_IMAGE'] = config['mysql_image']
        write_private(folder / '.env', ''.join(f'{k}={v}\n' for k, v in env.items()))
    folder = target / 'gitlab'
    (folder / 'secrets').mkdir(mode=0o700)
    write_private(folder / 'secrets/root_password', secrets.token_urlsafe(36) + '\n')
    folder = target / 'grape'
    write_private(folder / 'mysql.env', f'MYSQL_DATABASE=grape_lab\nMYSQL_USER=grape_lab\nMYSQL_PASSWORD={password}\nMYSQL_ROOT_PASSWORD={secrets.token_urlsafe(36)}\n')
    providers = json.dumps({'lab': {'url': 'https://' + hostname(config['gitlab']['host'])}}, separators=(',', ':'))
    write_private(folder / 'runtime.env', f'JWT_SECRET={jwt}\nGRAPE_ADMIN_PASSWORD={secrets.token_urlsafe(36)}\nDEVOPS_DB_PASSWORD={password}\nGRAPE_GITLAB_INSTANCES={providers}\n')
    (folder / 'configs').mkdir(mode=0o700)
    security = {'env': 'staging', 'jwt': {'kid_current': 'lab-v1', 'secret_keys': 'lab-v1:' + jwt},
                'credential_keys': 'lab-v1:' + base64.b64encode(secrets.token_bytes(32)).decode()}
    write_private(folder / 'configs/server-config.json', json.dumps({'server_base_url': 'https://' + hostname(config['grape']['host'])}) + '\n')
    write_private(folder / 'configs/security.json', json.dumps(security, indent=2) + '\n')
    write_private(target / 'inventory.json', json.dumps(config, indent=2) + '\n')
    return {'prepared': True, 'directory': str(target.resolve()), 'cloud_resources_created': False,
            'full_e2e_verified': False}


def https_url(value):
    parsed = urlsplit(value)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('', '/'):
        raise ValueError('Probe requires a credential-free HTTPS origin')
    return value.rstrip('/')


def probe(grape_url, gitlab_url, ca_bundle=None):
    grape_url, gitlab_url = https_url(grape_url), https_url(gitlab_url)
    if grape_url == gitlab_url:
        raise ValueError('Separate Grape and GitLab origins required')
    result = {'mode': 'read_only_preflight', 'checks': {}, 'full_e2e_verified': False}
    checks = [('gitlab_health', gitlab_url + '/-/health', {}),
              ('grape_health', grape_url + '/api/health', {})]
    if os.environ.get('GITLAB_LAB_TOKEN'):
        checks.append(('gitlab_authenticated', gitlab_url + '/api/v4/user', {'PRIVATE-TOKEN': os.environ['GITLAB_LAB_TOKEN']}))
    if os.environ.get('GRAPE_LAB_API_KEY'):
        checks.append(('grape_authenticated', grape_url + '/api/gitlab/instances', {'Authorization': 'Bearer ' + os.environ['GRAPE_LAB_API_KEY']}))
    for name, url, headers in checks:
        try:
            response = requests.get(url, headers=headers, timeout=15, allow_redirects=False, verify=ca_bundle or True)
            passed = response.status_code == 200
            if name == 'gitlab_health' and passed:
                passed = response.text.strip() == 'GitLab OK'
            if name == 'grape_health' and passed:
                health = response.json()
                passed = health.get('server') == 'ok'
                result['checks']['agent_runtime_online'] = {'ok': health.get('agent_runtime') == 'online'}
                result['checks']['advertised_grape_origin'] = {'ok': health.get('server_base_url', '').rstrip('/') == grape_url}
            if name.endswith('_authenticated') and passed:
                data = response.json()
                passed = bool(data.get('id')) if name.startswith('gitlab') else isinstance(data.get('instances'), list)
            result['checks'][name] = {'ok': passed, 'http_status': response.status_code}
        except (requests.RequestException, ValueError):
            result['checks'][name] = {'ok': False, 'error': 'connection_or_response_failed'}
    result['ok'] = all(check['ok'] for check in result['checks'].values())
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    init = commands.add_parser('prepare')
    init.add_argument('--inventory', type=Path, required=True)
    init.add_argument('--directory', type=Path, required=True)
    check = commands.add_parser('probe')
    check.add_argument('--grape-url', required=True)
    check.add_argument('--gitlab-url', required=True)
    check.add_argument('--ca-bundle')
    check.add_argument('--output', type=Path)
    args = parser.parse_args()
    try:
        if args.command == 'prepare':
            result = prepare(json.loads(args.inventory.read_text()), args.directory)
        else:
            result = probe(args.grape_url, args.gitlab_url, args.ca_bundle)
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                write_private(args.output, json.dumps(result, indent=2) + '\n')
    except (ValueError, KeyError, OSError):
        print(json.dumps({'ok': False, 'error': 'Invalid configuration, unavailable file or existing output; existing secrets were preserved'}))
        return 1
    print(json.dumps(result, indent=2))
    return 0 if result.get('prepared') or result.get('ok') else 1


if __name__ == '__main__':
    raise SystemExit(main())
