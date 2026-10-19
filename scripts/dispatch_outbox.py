#!/usr/bin/env python3
"""Deliver ChatGPT-authored JSON; no language-model API and no shell text execution."""
import base64
import hashlib
import json
import os
import pathlib
import re
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

REPO = 'joserobertoaraujofilho-art/robox'
ACCOUNT = 'eumzemesmo'
TZ = ZoneInfo('America/Sao_Paulo')


def request_json(url, token, payload=None, method='GET', missing_ok=False):
    headers = {'Authorization': 'Bearer ' + token, 'Accept': 'application/json',
               'Content-Type': 'application/json', 'User-Agent': 'robox-bridge'}
    body = json.dumps(payload, ensure_ascii=False).encode('utf-8') if payload is not None else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        if missing_ok and exc.code == 404:
            return None
        raise RuntimeError('Remote HTTP error ' + str(exc.code)) from None
    except (urllib.error.URLError, TimeoutError, ValueError):
        raise RuntimeError('Remote connection/response uncertain; do not resend automatically.') from None


def gql(query, variables=None):
    key = os.environ.get('BUFFER_API_KEY', '')
    if not key:
        raise RuntimeError('BUFFER_API_KEY missing in GitHub Secrets.')
    data = request_json('https://api.buffer.com', key,
                        {'query': query, 'variables': variables or {}}, 'POST')
    if data.get('errors') or not isinstance(data.get('data'), dict):
        raise RuntimeError('Buffer GraphQL error; no automatic retry.')
    return data['data']


def receipt_api(item_id, payload=None):
    token = os.environ.get('GITHUB_TOKEN', '')
    if not token:
        raise RuntimeError('GitHub workflow token missing.')
    url = 'https://api.github.com/repos/' + REPO + '/contents/deliveries/' + item_id + '.json'
    return request_json(url, token, payload, 'PUT' if payload else 'GET', missing_ok=payload is None)


def save_receipt(item_id, value, sha=None):
    encoded = base64.b64encode((json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode()).decode()
    payload = {'message': 'Registrar entrega ' + item_id, 'content': encoded, 'branch': 'main'}
    if sha:
        payload['sha'] = sha
    result = receipt_api(item_id, payload)
    return result['content']['sha']


def validate(path, data, now=None):
    now = now or datetime.now(TZ)
    if path.is_symlink() or not isinstance(data, dict):
        raise ValueError('Invalid outbox file.')
    item_id = data.get('id', '')
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{5,70}', item_id) or path.stem != item_id:
        raise ValueError('Invalid post identifier.')
    if data.get('account') != ACCOUNT:
        raise ValueError('Wrong X account.')
    text = data.get('text')
    if not isinstance(text, str) or text != text.strip() or not 1 <= len(text) <= 280:
        raise ValueError('Post must contain 1 to 280 characters without outer whitespace.')
    if any(c in text for c in ('@', '#', '\x00')) or any(ord(c) > 0xFFFF or 0x2600 <= ord(c) <= 0x27BF for c in text):
        raise ValueError('Mentions, hashtags and emoji are not allowed.')
    if path.parent == pathlib.Path('outbox/atemporais'):
        if data.get('category') != 'atemporal' or data.get('sources'):
            raise ValueError('News must never enter the automatic lane.')
        match = re.fullmatch(r'(\d{4}-\d{2}-\d{2})-(0800|1800)', item_id)
        if not match:
            raise ValueError('Automatic post must identify its date and 0800/1800 slot.')
        slot = datetime.strptime(item_id, '%Y-%m-%d-%H%M').replace(tzinfo=TZ)
        if not slot <= now <= slot + timedelta(hours=2):
            raise ValueError('Outside the slot delivery window; skipping rather than backfilling.')
        if 'http://' in text.lower() or 'https://' in text.lower():
            raise ValueError('Factual links must use the human-reviewed lane.')
    elif path.parent == pathlib.Path('outbox/aprovados'):
        approval = data.get('approval', {})
        if not isinstance(approval, dict) or approval.get('by') != 'user_in_chatgpt' or not approval.get('reference'):
            raise ValueError('Explicit human approval with reference is required.')
        if approval.get('text_sha256') != hashlib.sha256(text.encode('utf-8')).hexdigest():
            raise ValueError('Approval must match the exact post text.')
    else:
        raise ValueError('This folder cannot publish.')
    return item_id, text


def deliver(path):
    data = json.loads(path.read_text(encoding='utf-8'))
    item_id, text = validate(path, data)
    existing = receipt_api(item_id)
    if existing:
        previous = json.loads(base64.b64decode(existing['content']))
        print('ALREADY_CLAIMED', item_id, previous.get('state'), '- not sending again')
        if previous.get('state') != 'buffer_accepted':
            raise RuntimeError('Earlier attempt is unresolved; inspect Buffer before retrying.')
        return
    organizations = gql('query { account { organizations { id name } } }')['account']['organizations']
    matches = []
    for organization in organizations:
        channels = gql('query ($id: OrganizationId!) { channels(input: { organizationId: $id }) { id name service } }',
                       {'id': organization['id']})['channels']
        for channel in channels:
            if channel['service'].lower() in ('twitter', 'x') and channel['name'].lstrip('@').lower() == ACCOUNT:
                matches.append(channel)
    if len(matches) != 1:
        raise RuntimeError('Expected exactly one connected X channel matching ' + ACCOUNT)
    channel = matches[0]
    receipt = {'id': item_id, 'state': 'claimed', 'text_sha256': hashlib.sha256(text.encode()).hexdigest(),
               'account': ACCOUNT, 'channel_id': channel['id'], 'source_commit': os.environ.get('GITHUB_SHA'),
               'run_id': os.environ.get('GITHUB_RUN_ID'), 'created_at': datetime.now(TZ).isoformat()}
    # Reserve durably BEFORE the mutation. A replay/timeout must not duplicate a public post.
    claim_sha = save_receipt(item_id, receipt)
    try:
        result = gql('''mutation ($input: CreatePostInput!) {
          createPost(input: $input) {
            ... on PostActionSuccess { post { id text } }
            ... on MutationError { message }
          }
        }''', {'input': {'text': text, 'channelId': channel['id'], 'schedulingType': 'automatic',
                        'mode': 'shareNow', 'saveToDraft': False}})
        post = result['createPost'].get('post')
        if not post or post.get('text') != text:
            raise RuntimeError('Buffer did not confirm the exact requested post.')
        receipt.update(state='buffer_accepted', buffer_post_id=post['id'], updated_at=datetime.now(TZ).isoformat())
        save_receipt(item_id, receipt, claim_sha)
        print('BUFFER_ACCEPTED', item_id, post['id'])
        print('Acceptance is not independent confirmation of visibility on X.')
    except Exception:
        raise RuntimeError('Delivery unresolved. A reservation prevents duplicate sends; inspect the run and Buffer.') from None


def main():
    if os.environ.get('GITHUB_REPOSITORY') != REPO or os.environ.get('GITHUB_EVENT_NAME') != 'push':
        raise RuntimeError('Delivery is restricted to push events in the authorized repository.')
    config = json.loads(pathlib.Path('config/operacao.json').read_text(encoding='utf-8'))
    if config.get('enabled') is not True or config.get('mode') != 'hybrid':
        print('PUBLICATION_DISABLED')
        return
    before, head = os.environ['BEFORE_SHA'], os.environ['GITHUB_SHA']
    if not all(re.fullmatch(r'[0-9a-f]{40}', sha) for sha in (before, head)):
        raise ValueError('Invalid commit metadata.')
    names = subprocess.check_output(['git', 'diff', '--name-only', '--diff-filter=A', before, head, '--',
                                     'outbox/atemporais/', 'outbox/aprovados/'], text=True).splitlines()
    paths = [pathlib.Path(name) for name in names if name.endswith('.json')]
    if len(paths) > 2:
        raise ValueError('Too many posts in one delivery commit.')
    for path in paths:
        deliver(path)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('ERROR:', str(exc), file=sys.stderr)
        sys.exit(1)
