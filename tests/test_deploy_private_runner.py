import json

import pytest

from scripts import deploy_private_runner as release


def fixture(monkeypatch, tmp_path, email=release.OWNER, drift=False):
    (tmp_path / '.clasprc.json').write_text(json.dumps({'tokens': {'jinyt': {
        'client_id': 'test', 'client_secret': 'test', 'refresh_token': 'test'}}}))
    monkeypatch.setattr(release.Path, 'home', lambda: tmp_path)
    calls = []
    content = {'files': [{'name': 'appsscript', 'type': 'JSON', 'source': json.dumps({
        'webapp': {'executeAs': 'USER_DEPLOYING', 'access': 'ANYONE_ANONYMOUS'}})}]}
    def request(url, **kwargs):
        calls.append((url, kwargs))
        if 'oauth2.googleapis.com/token' in url:
            return {'access_token': 'test'}
        if 'userinfo' in url:
            return {'email': email}
        if '/content' in url:
            return {'files': []} if drift and '?' not in url else content
        if url.endswith('/versions'):
            return {'versionNumber': 200}
        if '/deployments/' in url:
            return {'deploymentConfig': {'versionNumber': 162}}
        return {'ok': False, 'error': {'message': 'Unauthorized.'}}
    monkeypatch.setattr(release, 'request_json', request)
    return calls


def test_wrong_actual_account_stops_before_project_access(monkeypatch, tmp_path):
    calls = fixture(monkeypatch, tmp_path, email='operation@welive.network')
    with pytest.raises(RuntimeError, match='Wrong deployment account'):
        release.deploy(162)
    assert len(calls) == 2


def test_unreviewed_source_changes_cannot_be_released(monkeypatch, tmp_path):
    calls = fixture(monkeypatch, tmp_path, drift=True)
    with pytest.raises(RuntimeError, match='differs from the tested version'):
        release.deploy(162)
    assert not any(options.get('method') in ('PUT', 'POST') for _, options in calls)


def test_owner_release_creates_fresh_version_and_probes_execution_identity(monkeypatch, tmp_path):
    calls = fixture(monkeypatch, tmp_path)
    release.deploy(162)
    updates = [json.loads(options['data']) for _, options in calls if options.get('method') == 'PUT']
    assert updates[0]['deploymentConfig']['versionNumber'] == 200
    assert calls[-1][0].endswith('?taskTrackerPrivateRunner=1')
