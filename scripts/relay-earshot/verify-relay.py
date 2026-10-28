"""Live acceptance runner. Missing prerequisites are FAIL/BLOCKED, never PASS."""
import argparse
import datetime as dt
import json
import math
import os
from pathlib import Path
import socket
import ssl
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request


def request(url, method='GET', body=None, headers=None):
    req = urllib.request.Request(url, method=method, headers=headers or {},
        data=None if body is None else json.dumps(body).encode())
    try:
        with urllib.request.urlopen(req, timeout=35) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--api-base', default=os.getenv('TP_RELAY_BASE_URL', ''))
    parser.add_argument('--admin-url', default=os.getenv('EARSHOT_ADMIN_URL', ''))
    parser.add_argument('--ssh-target', default=os.getenv('EARSHOT_SSH_TARGET', ''))
    parser.add_argument('--local-evidence', default='')
    parser.add_argument('--offlist-evidence', default='')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    rows, latency = {}, {}
    def row(n, ok, detail, blocked=False):
        rows[n] = {'n': n, 'result': 'PASS' if ok else 'FAIL', 'blocked': blocked,
                   'detail': detail}
    def check(n, fn):
        try:
            ok, detail = fn()
            row(n, ok, detail)
        except Exception as exc:
            row(n, False, type(exc).__name__ + ': ' + str(exc)[:180], True)

    def containers():
        if not args.ssh_target:
            raise RuntimeError('No EARSHOT_SSH_TARGET; cannot inspect remote docker')
        run = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
            args.ssh_target, 'docker ps --format "{{.Names}} {{.Status}}"'],
            capture_output=True, text=True, timeout=25)
        return run.returncode == 0 and any('new-api' in x and 'Up' in x for x in run.stdout.splitlines()), run.stdout.strip()
    check(1, containers)
    base = args.api_base.rstrip('/')
    origin = base.removesuffix('/v1')
    check(2, lambda: (lambda r: (r[0] == 200, 'HEAD / HTTP ' + str(r[0])))(request(origin + '/', 'HEAD')))
    def certificate():
        url = urllib.parse.urlsplit(origin)
        if url.scheme != 'https':
            raise RuntimeError('HTTPS endpoint not configured')
        with socket.create_connection((url.hostname, url.port or 443), timeout=15) as sock:
            with ssl.create_default_context().wrap_socket(sock, server_hostname=url.hostname) as tls:
                return True, 'CA + hostname verification succeeded; expires=' + tls.getpeercert()['notAfter']
    check(3, certificate)
    def offlist():
        if not args.offlist_evidence:
            raise RuntimeError('No off-whitelist/mobile request evidence')
        data = json.loads(Path(args.offlist_evidence).read_text(encoding='utf-8-sig'))
        return data.get('url') == args.admin_url and data.get('http_status') == 403, 'Imported external evidence: ' + args.offlist_evidence
    check(4, offlist)
    check(5, lambda: (lambda r: (r[0] == 404, '/panel HTTP ' + str(r[0])))(request(origin + '/panel')))
    def password():
        if not args.admin_url:
            raise RuntimeError('No admin URL')
        status, raw = request(args.admin_url.rstrip('/') + '/api/user/login', 'POST',
            {'username': 'root', 'password': '123456'}, {'Content-Type': 'application/json'})
        obj = json.loads(raw)
        return status == 200 and obj.get('success') is False, 'Default-password response: HTTP ' + str(status) + ', success=' + str(obj.get('success'))
    check(6, password)
    def registration():
        if not args.admin_url:
            raise RuntimeError('No admin URL')
        _, raw = request(args.admin_url.rstrip('/') + '/api/status')
        status_obj = json.loads(raw)
        registered = status_obj.get('data', {}).get('register_enabled')
        code, raw = request(args.admin_url.rstrip('/') + '/api/user/register', 'POST',
            {'username': 'esprobe' + str(int(time.time()))[-4:], 'password': 'Probe-Only-NoAccount-2026!'},
            {'Content-Type': 'application/json'})
        obj = json.loads(raw)
        return code == 200 and registered is False and obj.get('success') is False, 'register_enabled=' + str(registered) + ', success=' + str(obj.get('success'))
    check(7, registration)
    token = os.getenv('TP_RELAY_API_KEY', '')
    for n, model in [(8, 'earshot-fast'), (9, 'earshot-smart')]:
        def sample(model=model):
            if not token or not base:
                raise RuntimeError('Relay URL/token not configured; no live latency samples')
            samples, counts = [], []
            for _ in range(10):
                req = urllib.request.Request(base + '/chat/completions', method='POST',
                    headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'},
                    data=json.dumps({'model': model, 'stream': True, 'max_tokens': 64,
                                     'messages': [{'role': 'user', 'content': 'Count from 1 to 20.'}]}).encode())
                t0, first, frames, done = time.perf_counter(), None, 0, False
                with urllib.request.urlopen(req, timeout=45) as response:
                    for line in response:
                        if not line.startswith(b'data:'):
                            continue
                        value = line[5:].strip()
                        if value == b'[DONE]':
                            done = True
                            break
                        frames += 1
                        choices = json.loads(value).get('choices', [])
                        if choices and choices[0].get('delta', {}).get('content') and first is None:
                            first = (time.perf_counter() - t0) * 1000
                if not done or frames < 3 or first is None:
                    raise RuntimeError('SSE requires >=3 data frames, content and [DONE]')
                samples.append(round(first, 2)); counts.append(frames)
            ordered = sorted(samples)
            latency[model] = {'samples_ms': samples, 'frames': counts,
                'p50_ms': ordered[math.ceil(len(ordered) * .50) - 1],
                'p95_ms': ordered[math.ceil(len(ordered) * .95) - 1]}
            return True, json.dumps(latency[model])
        check(n, sample)
    fast = latency.get('earshot-fast', {})
    smart = latency.get('earshot-smart', {})
    row(10, bool(fast) and fast['p95_ms'] < 800 and bool(smart) and smart['p95_ms'] < 2500,
        'fast P95 <800ms / smart P95 <2500ms; ' + json.dumps(latency), not bool(fast))
    evidence = json.loads(Path(args.local_evidence).read_text(encoding='utf-8-sig')) if args.local_evidence else {}
    for n, key in [(11, 'live_fallback'), (12, 'legacy_config'), (13, 'production_chain'), (14, 'install_uninstall')]:
        item = evidence.get(key, {})
        row(n, item.get('ok') is True, item.get('detail', 'No current-run execution evidence'), not bool(item))
    result = {'timestamp': dt.datetime.now(dt.timezone.utc).isoformat(), 'rows': list(rows.values()),
              'latency': latency, 'api_base': base, 'token_suffix': token[-4:] if token else None}
    out = Path(args.output); out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    for value in rows.values():
        print(f"{value['n']:>2} {value['result']:<4} {value['detail'][:180]}")
    return 0 if all(x['result'] == 'PASS' for x in rows.values()) else 1


if __name__ == '__main__':
    raise SystemExit(main())
