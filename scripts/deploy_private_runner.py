"""Release an existing private-runner version with a verified owner identity."""
import argparse
import json
from pathlib import Path
import urllib.parse
import urllib.request

OWNER = 'jinyt@welive.network'
PROJECT = '1M6lV7tRFSuUfCIJ4PFyFik1aIF7T6uOwAwU14YUxnGBxi6q8MZJaRtqI'
DEPLOYMENT = 'AKfycbzu5yX8s1toHk8TrdW8y0Jo3YWaTETty8AlWEnZgAVv-M_v1i5fM6uZu7GBzzmkfkLJRg'


def request_json(url, *, headers=None, data=None, method=None):
    request = urllib.request.Request(url, headers=headers or {}, data=data, method=method)
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def deploy(version, profile='jinyt'):
    credentials = json.loads((Path.home() / '.clasprc.json').read_text())['tokens'][profile]
    form = {key: credentials[key] for key in ('client_id', 'client_secret', 'refresh_token')}
    form['grant_type'] = 'refresh_token'
    token = request_json('https://oauth2.googleapis.com/token', data=urllib.parse.urlencode(form).encode())['access_token']
    headers = {'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'}
    user = request_json('https://www.googleapis.com/oauth2/v2/userinfo', headers=headers)
    # Credential profile labels can outlive account changes. Verify the actual
    # OAuth identity before any write; never trust "default" or a description.
    if user.get('email', '').lower() != OWNER:
        raise RuntimeError('Wrong deployment account; private runner requires ' + OWNER)
    base = 'https://script.googleapis.com/v1/projects/' + PROJECT
    content = request_json(base + '/content?versionNumber=' + str(version), headers=headers)
    manifest = json.loads(next(file['source'] for file in content['files'] if file['name'] == 'appsscript'))
    if manifest.get('webapp') != {'executeAs': 'USER_DEPLOYING', 'access': 'ANYONE_ANONYMOUS'}:
        raise RuntimeError('Version does not preserve the existing public webhook configuration')
    current = request_json(base + '/content', headers=headers)
    def sources(project):
        return {file['name']: (file['type'], file.get('source', '')) for file in project['files']}
    if sources(current) != sources(content):
        raise RuntimeError('Current source differs from the tested version; review before releasing')
    # A fresh version created by the verified account is necessary. Repointing
    # to a version created by another account can retain that execution identity.
    release = request_json(base + '/versions', headers=headers, method='POST',
                           data=json.dumps({'description': 'Verified owner release of tested version ' + str(version)}).encode())
    release_version = release['versionNumber']
    endpoint = base + '/deployments/' + DEPLOYMENT
    config = request_json(endpoint, headers=headers)['deploymentConfig']
    config['versionNumber'] = release_version
    config['description'] = 'Telegram webhook + private task runner (runs as jinyt)'
    request_json(endpoint, headers=headers, method='PUT', data=json.dumps({'deploymentConfig': config}).encode())
    probe = request_json('https://script.google.com/macros/s/' + DEPLOYMENT + '/exec?taskTrackerPrivateRunner=1',
                         headers={'Content-Type': 'application/json'}, data=b'{}')
    if probe.get('error', {}).get('message') != 'Unauthorized.':
        raise RuntimeError('Deployment updated, but private runner identity probe failed')
    print('Private runner version ' + str(release_version) + ' verified as ' + OWNER)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', type=int, required=True)
    parser.add_argument('--profile', default='jinyt')
    arguments = parser.parse_args()
    deploy(arguments.version, arguments.profile)
