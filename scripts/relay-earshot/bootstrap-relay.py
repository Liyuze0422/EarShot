"""API bootstrap pinned to QuantumNous/new-api v1.0.0-rc.47; never print keys."""
import http.cookiejar
import json
import os
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request

BASE = os.environ.get('NEW_API_ADMIN_BASE', 'http://127.0.0.1:3000')
OPENER = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
HEADERS = {'Content-Type': 'application/json'}


def api(method, path, body=None, required=True):
    req = urllib.request.Request(BASE + path, data=None if body is None else json.dumps(body).encode(),
                                 headers=HEADERS, method=method)
    try:
        with OPENER.open(req, timeout=90) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        if not required:
            return {'success': False, 'http_status': exc.code}
        raise RuntimeError(f'{method} {path}: HTTP {exc.code}') from None
    if required and result.get('success') is not True:
        raise RuntimeError(f'{method} {path}: success != true')
    return result


def items(payload):
    data = payload['data']
    return data if isinstance(data, list) else data.get('items', data.get('data', []))


def option(key, value):
    api('PUT', '/api/option/', {'key': key, 'value': str(value)})


def main():
    password = os.environ['NEW_ADMIN_PASS']
    setup = api('GET', '/api/setup')['data']
    if not setup['status']:
        if setup.get('root_init'):
            raise RuntimeError('Pre-existing root account: fresh-install bootstrap cannot replace it')
        # Current upstream creates root via setup; no root/123456 is shipped.
        api('POST', '/api/setup', {'username': 'root', 'password': password,
            'confirmPassword': password, 'SelfUseModeEnabled': True, 'DemoSiteEnabled': False})
    data = api('POST', '/api/user/login', {'username': 'root', 'password': password})['data']
    if data.get('access_token'):
        HEADERS['Authorization'] = 'Bearer ' + data['access_token']
    user = data.get('user', data)
    if not user.get('id'):
        raise RuntimeError('Admin login needs additional verification')
    HEADERS['New-Api-User'] = str(user['id'])
    old = api('POST', '/api/user/login', {'username': 'root', 'password': '123456'}, False)
    if old.get('success'):
        raise RuntimeError('Default password still accepted')
    option('RegisterEnabled', 'false')
    options = {x['key']: x['value'] for x in api('GET', '/api/option/')['data']}
    ratios = json.loads(options.get('GroupRatio', '{}'))
    ratios['earshot'] = 1
    option('GroupRatio', json.dumps(ratios))
    group_key = 'group_ratio_setting.group_special_usable_group'
    groups = json.loads(options.get(group_key, '{}'))
    groups.setdefault(user.get('group', 'default'), {})['earshot'] = 'EarShot'
    option(group_key, json.dumps(groups))
    model_ratios = json.loads(options.get('ModelRatio', '{}'))
    completion = json.loads(options.get('CompletionRatio', '{}'))
    fast_model = os.environ.get('DOUBAO_MODEL', 'doubao-1.5-lite-32k-250115')
    doubao = bool(os.environ.get('DOUBAO_KEY'))
    for alias, upstream in [('earshot-smart', 'deepseek-chat'),
                            ('earshot-fast', fast_model if doubao else 'deepseek-chat')]:
        if upstream not in model_ratios:
            raise RuntimeError('Upstream model price missing; configure ModelRatio through API: ' + upstream)
        model_ratios[alias] = model_ratios[upstream]
        completion[alias] = completion.get(upstream, 1)
    option('ModelRatio', json.dumps(model_ratios))
    option('CompletionRatio', json.dumps(completion))
    definitions = [('earshot-deepseek', 1, 'https://api.deepseek.com', os.environ['DEEPSEEK_KEY'],
        {'earshot-smart': 'deepseek-chat', **({} if doubao else {'earshot-fast': 'deepseek-chat'})})]
    if doubao:
        definitions.append(('earshot-doubao', 45, 'https://ark.cn-beijing.volces.com',
                            os.environ['DOUBAO_KEY'], {'earshot-fast': fast_model}))
    current = {x['name']: x for x in items(api('GET', '/api/channel/?page_size=100'))}
    for name, kind, base, key, mapping in definitions:
        ch = {'type': kind, 'name': name, 'key': key, 'base_url': base,
              'models': ','.join(mapping), 'model_mapping': json.dumps(mapping),
              'group': 'earshot', 'weight': 10, 'test_model': next(iter(mapping))}
        if name in current:
            ch['id'] = current[name]['id']
            api('PUT', '/api/channel/', ch)
        else:
            api('POST', '/api/channel/', {'mode': 'single', 'channel': ch})
    current = {x['name']: x for x in items(api('GET', '/api/channel/?page_size=100'))}
    channel_ids = []
    for name, _, _, _, mapping in definitions:
        cid = current[name]['id']
        api('GET', f'/api/channel/test/{cid}?model={urllib.parse.quote(next(iter(mapping)))}')
        channel_ids.append(cid)
    tokens = [x for x in items(api('GET', '/api/token/?page_size=100')) if x['name'] == 'earshot-prod']
    if not tokens:
        api('POST', '/api/token/', {'name': 'earshot-prod', 'expired_time': -1,
            'remain_quota': int(os.environ['TOKEN_QUOTA']), 'unlimited_quota': False,
            'group': 'earshot', 'model_limits_enabled': True,
            'model_limits': 'earshot-fast,earshot-smart'})
        tokens = [x for x in items(api('GET', '/api/token/?page_size=100')) if x['name'] == 'earshot-prod']
    if len(tokens) != 1:
        raise RuntimeError('Expected exactly one earshot-prod token')
    token_id = tokens[0]['id']
    # Preserve consumed quota on re-run, but enforce the original model/group scope.
    api('PUT', '/api/token/', {**tokens[0], 'group': 'earshot',
        'model_limits_enabled': True, 'model_limits': 'earshot-fast,earshot-smart'})
    token = api('POST', f'/api/token/{token_id}/key')['data']['key']
    path = Path('/opt/api/relay-token.env')
    path.write_text('TP_RELAY_API_KEY=' + token + '\n')
    path.chmod(0o600)
    result = {'version': 'v1.0.0-rc.47', 'default_password_rejected': True,
              'register_enabled': False, 'channel_ids': channel_ids, 'token_id': token_id,
              'token_suffix': token[-4:], 'doubao_enabled': doubao,
              'fast_upstream': fast_model if doubao else 'deepseek-chat'}
    Path('/opt/api/bootstrap-result.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result))


if __name__ == '__main__':
    main()
