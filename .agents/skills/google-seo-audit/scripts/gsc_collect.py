#!/usr/bin/env python3
"""Collect explicit Search Console datasets with read-only credentials, outside Codex.

Only Google read operations are implemented. Supply the resulting snapshot to Codex,
not this process's credentials. Uses stdlib for an OAuth access token; service-account
refresh additionally requires google-auth and requests.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

READONLY = 'https://www.googleapis.com/auth/webmasters.readonly'
READWRITE = 'https://www.googleapis.com/auth/webmasters'
API = 'https://www.googleapis.com/webmasters/v3/sites/'
INSPECT = 'https://searchconsole.googleapis.com/v1/urlInspection/index:inspect'
TOKENINFO = 'https://oauth2.googleapis.com/tokeninfo'
DATASETS = ('site', 'pages', 'queries', 'inspection', 'sitemaps')


class CollectionError(Exception):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request_json(url, method='GET', payload=None, token=None):
    """Per-request timeout and bounded retries; never print upstream credential errors."""
    headers = {'Accept': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    data = None
    if payload is not None:
        data = json.dumps(payload).encode()
        headers['Content-Type'] = 'application/json'
    opener = urllib.request.build_opener(NoRedirect())
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, data=data, headers=headers, method=method)
            with opener.open(req, timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            if (exc.code == 429 or 500 <= exc.code < 600) and attempt < 2:
                time.sleep(2 ** attempt)
                continue
            raise CollectionError(f'Google HTTP {exc.code}; data was not collected') from None
        except (OSError, ValueError):
            raise CollectionError('Google request failed; check network or credential validity') from None


def verify_scope(token, transport=request_json):
    info = transport(TOKENINFO + '?' + urllib.parse.urlencode({'access_token': token}))
    scopes = set(info.get('scope', '').split())
    if READONLY not in scopes or READWRITE in scopes:
        raise CollectionError('A webmasters.readonly token without webmasters write scope is required')


def token_provider(credentials_path):
    if not credentials_path:
        token = os.environ.get('GSC_ACCESS_TOKEN', '')
        if not token:
            raise CollectionError('Set GSC_ACCESS_TOKEN or provide --credentials outside the Codex process')
        verify_scope(token)
        return lambda: token
    try:
        from google.oauth2 import service_account
        from google.auth.transport.requests import Request
    except ImportError:
        raise CollectionError('Service-account mode requires google-auth and requests') from None
    try:
        # This explicit loader accepts service-account credentials, not arbitrary ADC files.
        credentials = service_account.Credentials.from_service_account_file(
            credentials_path, scopes=[READONLY])
        refresh_request = Request()
        def current_token():
            try:
                if not credentials.valid:
                    credentials.refresh(refresh_request)
                    verify_scope(credentials.token)
            except Exception:
                raise CollectionError('Unable to refresh service account with read-only scope') from None
            return credentials.token
        current_token()
        return current_token
    except Exception:
        raise CollectionError('Unable to authorize service account with read-only scope') from None


def validate_property(site):
    if site.startswith('sc-domain:'):
        domain = site[len('sc-domain:'):]
        if not re.fullmatch(r'[a-zA-Z0-9](?:[a-zA-Z0-9.-]*[a-zA-Z0-9])?', domain):
            raise CollectionError('Invalid domain property')
    else:
        parsed = urllib.parse.urlsplit(site)
        if parsed.scheme not in ('http', 'https') or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise CollectionError('Use the exact URL-prefix or sc-domain property from GSC')


def belongs_to_property(url, site):
    p = urllib.parse.urlsplit(url)
    if p.scheme not in ('http', 'https') or not p.hostname or p.username or p.password or p.fragment:
        return False
    if site.startswith('sc-domain:'):
        domain = site[len('sc-domain:'):].lower()
        return p.hostname.lower() == domain or p.hostname.lower().endswith('.' + domain)
    base = urllib.parse.urlsplit(site)
    return p.scheme == base.scheme and p.netloc.lower() == base.netloc.lower() and p.path.startswith(base.path)


class GscReader:
    """No caller-controlled HTTP paths/methods and no write API surface."""
    def __init__(self, site, token, transport=request_json):
        validate_property(site)
        self.site, self.token, self.transport = site, token, transport
        self.base = API + urllib.parse.quote(site, safe='')

    def property_info(self):
        return self.transport(self.base, token=self.token())

    def sitemaps(self):
        return self.transport(self.base + '/sitemaps', token=self.token())

    def inspect(self, url):
        if not belongs_to_property(url, self.site):
            raise CollectionError('Inspection URL is outside the selected property')
        return self.transport(INSPECT, 'POST', {
            'inspectionUrl': url, 'siteUrl': self.site, 'languageCode': 'zh-CN',
        }, token=self.token())

    def analytics(self, start, end, dimensions, max_rows, page=None):
        body = {'startDate': start, 'endDate': end, 'type': 'web',
                'dataState': 'final', 'dimensions': dimensions}
        if page:
            if not belongs_to_property(page, self.site):
                raise CollectionError('Query page is outside the selected property')
            body['dimensionFilterGroups'] = [{'filters': [
                {'dimension': 'page', 'operator': 'equals', 'expression': page}]}]
        rows, offset = [], 0
        while len(rows) < max_rows:
            limit = min(25000, max_rows - len(rows))
            result = self.transport(self.base + '/searchAnalytics/query', 'POST',
                                    {**body, 'startRow': offset, 'rowLimit': limit}, token=self.token())
            batch = result.get('rows', [])
            rows.extend(batch)
            if len(batch) < limit:
                break
            offset += len(batch)
        return {'request': body, 'rows': rows, 'row_limit_reached': len(rows) >= max_rows,
                'completeness': 'API returns available top rows; pagination does not guarantee all queries or pages'}


def write_json(root, name, value):
    payload = (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode()
    (root / name).write_bytes(payload)
    return {'name': name, 'sha256': hashlib.sha256(payload).hexdigest(), 'bytes': len(payload)}


def positive(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError('must be positive')
    return number


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--site', required=True, help='Exact GSC property, e.g. sc-domain:example.com')
    p.add_argument('--datasets', nargs='+', choices=DATASETS, required=True,
                   help='Collect only these datasets; queries require an explicit URL list')
    p.add_argument('--start-date', help='YYYY-MM-DD, GSC America/Los_Angeles dates')
    p.add_argument('--end-date', help='YYYY-MM-DD; only finalized data is requested')
    p.add_argument('--compare-previous', action='store_true')
    p.add_argument('--urls-file', type=Path, help='UTF-8 file with one exact URL per line')
    p.add_argument('--max-rows', type=positive, default=50000)
    p.add_argument('--credentials', help='Service-account JSON path, accessible only to collector')
    p.add_argument('--output-dir', type=Path, required=True, help='New snapshot directory; must not exist')
    return p


def validate_args(args):
    validate_property(args.site)
    performance = bool(set(args.datasets) & {'site', 'pages', 'queries'})
    windows = []
    if performance:
        try:
            start, end = dt.date.fromisoformat(args.start_date), dt.date.fromisoformat(args.end_date)
            if start > end:
                raise ValueError()
        except (TypeError, ValueError):
            raise CollectionError('Performance datasets require valid --start-date <= --end-date') from None
        windows.append(('current', start.isoformat(), end.isoformat()))
        if args.compare_previous:
            days = (end - start).days + 1
            windows.append(('previous', (start - dt.timedelta(days=days)).isoformat(),
                            (start - dt.timedelta(days=1)).isoformat()))
    elif args.compare_previous:
        raise CollectionError('--compare-previous requires a performance dataset')
    urls = []
    if set(args.datasets) & {'inspection', 'queries'}:
        if not args.urls_file:
            raise CollectionError('inspection and queries require --urls-file to specify scope')
        urls = list(dict.fromkeys(line.strip() for line in args.urls_file.read_text().splitlines() if line.strip()))
        if not urls or any(not belongs_to_property(url, args.site) for url in urls):
            raise CollectionError('URL list is empty or contains URLs outside the selected property')
    return windows, urls


def collect(args, reader):
    windows, urls = validate_args(args)
    # Permission check before creating an output artifact or reading other datasets.
    prop = reader.property_info()
    if prop.get('permissionLevel') not in ('siteOwner', 'siteFullUser', 'siteRestrictedUser'):
        raise CollectionError('The account does not have access to the selected property')
    root = args.output_dir
    root.mkdir(parents=True, exist_ok=False)
    manifest = {'schema_version': 1, 'site': args.site, 'scope': READONLY,
                'collected_at': dt.datetime.now(dt.timezone.utc).isoformat(),
                'timezone': 'America/Los_Angeles', 'data_state': 'final',
                'datasets': sorted(set(args.datasets)), 'requested_urls': urls,
                'date_windows': windows, 'files': [], 'errors': [], 'status': 'collecting',
                'limitations': [
                    'URL Inspection is the Google indexed version, not a live test.',
                    'Missing rows do not mean zero traffic or not indexed; query data may be anonymized.',
                    'Final data can lag the requested end date; inspect returned dates.',
                    'Tokens and credentials are intentionally absent from this snapshot.',
                ]}
    manifest['files'].append(write_json(root, 'property.json', {'siteUrl': args.site, 'permissionLevel': prop['permissionLevel']}))

    def capture(name, fn):
        try:
            manifest['files'].append(write_json(root, name, fn()))
        except CollectionError as exc:
            manifest['errors'].append({'dataset': name, 'error': str(exc)})
        finally:
            write_json(root, 'manifest.json', manifest)

    for label, start, end in windows:
        for dataset, dims in [('site', []), ('pages', ['page'])]:
            if dataset in args.datasets:
                capture(f'{label}-{dataset}.json', lambda dims=dims: reader.analytics(start, end, dims, args.max_rows))
        if 'site' in args.datasets:
            capture(f'{label}-dates.json', lambda: reader.analytics(start, end, ['date'], args.max_rows))
        if 'queries' in args.datasets:
            for i, url in enumerate(urls):
                capture(f'{label}-queries-{i:04d}.json', lambda url=url: reader.analytics(start, end, ['query'], args.max_rows, page=url))
    if 'sitemaps' in args.datasets:
        capture('sitemaps.json', reader.sitemaps)
    if 'inspection' in args.datasets:
        for i, url in enumerate(urls):
            capture(f'inspection-{i:04d}.json', lambda url=url: {'url': url, 'result': reader.inspect(url)})
            time.sleep(0.2)  # Stay below the documented per-property inspection minute quota.
    manifest['status'] = 'partial' if manifest['errors'] else 'complete'
    write_json(root, 'manifest.json', manifest)
    return manifest


def main():
    args = parser().parse_args()
    try:
        validate_args(args)  # Reject invalid scope before touching credentials/network.
        if args.output_dir.exists():
            raise CollectionError('Output directory already exists; use a new snapshot directory')
        reader = GscReader(args.site, token_provider(args.credentials))
        manifest = collect(args, reader)
        print(json.dumps({'status': manifest['status'], 'files': len(manifest['files']),
                          'failed_datasets': len(manifest['errors'])}))
        return 2 if manifest['errors'] else 0
    except (CollectionError, OSError) as exc:
        # Do not leak credential file paths, OAuth tokens or upstream response bodies.
        print(str(exc) if isinstance(exc, CollectionError) else 'Unable to read inputs or write the snapshot', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
