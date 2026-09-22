import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

MODULE = Path(__file__).resolve().parents[1] / '.agents/skills/google-seo-audit/scripts/gsc_collect.py'
spec = importlib.util.spec_from_file_location('gsc_collect', MODULE)
gsc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gsc)


class GscTests(unittest.TestCase):
    def args(self, root, datasets=('site', 'pages')):
        return gsc.parser().parse_args([
            '--site', 'sc-domain:example.com', '--datasets', *datasets,
            '--start-date', '2026-08-01', '--end-date', '2026-08-28',
            '--output-dir', str(Path(root) / 'snapshot'), '--compare-previous',
        ])

    def test_requires_readonly_scope_and_rejects_write_scope(self):
        gsc.verify_scope('not-a-real-token', lambda *a: {'scope': gsc.READONLY})
        for scope in ['', gsc.READWRITE, gsc.READONLY + ' ' + gsc.READWRITE]:
            with self.assertRaises(gsc.CollectionError):
                gsc.verify_scope('not-a-real-token', lambda *a: {'scope': scope})

    def test_property_boundaries(self):
        self.assertTrue(gsc.belongs_to_property('https://www.example.com/a', 'sc-domain:example.com'))
        self.assertFalse(gsc.belongs_to_property('https://example.com.evil.test/a', 'sc-domain:example.com'))
        self.assertFalse(gsc.belongs_to_property('https://evil-example.com', 'sc-domain:example.com'))
        self.assertFalse(gsc.belongs_to_property('https://user:password@example.com', 'sc-domain:example.com'))
        self.assertFalse(gsc.belongs_to_property('http://example.com/path/a', 'https://example.com/path/'))
        self.assertFalse(gsc.belongs_to_property('https://example.com/other', 'https://example.com/path/'))
        self.assertTrue(gsc.belongs_to_property('https://example.com/path/a?q=1', 'https://example.com/path/'))

    def test_cross_property_inspection_never_reaches_transport(self):
        reader = gsc.GscReader('sc-domain:example.com', lambda: 'secret', transport=lambda *a, **kw: self.fail('unexpected network'))
        with self.assertRaises(gsc.CollectionError):
            reader.inspect('https://other.test/')

    def test_explicit_datasets_and_date_windows(self):
        with tempfile.TemporaryDirectory() as root:
            args = self.args(root)
            windows, urls = gsc.validate_args(args)
            self.assertEqual(windows, [('current', '2026-08-01', '2026-08-28'), ('previous', '2026-07-04', '2026-07-31')])
            self.assertEqual(urls, [])
            args.start_date = '2026-08-30'
            with self.assertRaises(gsc.CollectionError):
                gsc.validate_args(args)

    def test_page_query_and_inspection_require_explicit_urls(self):
        with tempfile.TemporaryDirectory() as root:
            args = self.args(root, ('queries',))
            with self.assertRaises(gsc.CollectionError):
                gsc.validate_args(args)
            urls = Path(root) / 'urls.txt'
            urls.write_text('https://example.com/a\nhttps://other.test/\n')
            args.urls_file = urls
            with self.assertRaises(gsc.CollectionError):
                gsc.validate_args(args)

    def test_pagination_preserves_exact_page_filter_and_marks_limit(self):
        calls = []
        def transport(url, method, payload, **kwargs):
            calls.append((url, method, payload))
            return {'rows': [{'keys': ['query'], 'clicks': 1}] * payload['rowLimit']}
        reader = gsc.GscReader('https://example.com/', lambda: 'secret', transport)
        data = reader.analytics('2026-08-01', '2026-08-28', ['query'], 25001, page='https://example.com/a?q=1')
        self.assertEqual([c[2]['startRow'] for c in calls], [0, 25000])
        self.assertEqual([c[2]['rowLimit'] for c in calls], [25000, 1])
        self.assertTrue(data['row_limit_reached'])
        self.assertEqual(data['request']['dataState'], 'final')
        self.assertEqual(data['request']['dimensionFilterGroups'][0]['filters'][0]['expression'], 'https://example.com/a?q=1')
        self.assertNotIn('secret', json.dumps(data))

    def test_snapshot_contains_checksums_and_only_selected_reads(self):
        calls = []
        def transport(url, method='GET', payload=None, token=None):
            calls.append((url, method, payload))
            if url.endswith('/searchAnalytics/query'):
                return {'rows': [{'clicks': 3, 'impressions': 20}]}
            return {'permissionLevel': 'siteRestrictedUser', 'siteUrl': 'sc-domain:example.com'}
        with tempfile.TemporaryDirectory() as root:
            args = self.args(root)
            reader = gsc.GscReader(args.site, lambda: 'secret-token', transport)
            result = gsc.collect(args, reader)
            self.assertEqual(result['status'], 'complete')
            self.assertEqual(len(calls), 7)  # property plus totals/dates/pages for two windows
            self.assertTrue(all(method == ('POST' if url.endswith('/searchAnalytics/query') else 'GET') for url, method, _ in calls))
            self.assertFalse(any('urlInspection' in url or url.endswith('/sitemaps') for url, _, _ in calls))
            for file in result['files']:
                contents = (args.output_dir / file['name']).read_bytes()
                self.assertEqual(hashlib.sha256(contents).hexdigest(), file['sha256'])
                self.assertNotIn(b'secret-token', contents)
            with self.assertRaises(FileExistsError):
                gsc.collect(args, reader)

    def test_partial_failures_preserve_other_files_without_zero_fabrication(self):
        def transport(url, method='GET', payload=None, token=None):
            if url.endswith('/searchAnalytics/query'):
                if payload['dimensions'] == ['page']:
                    raise gsc.CollectionError('Google HTTP 429; data was not collected')
                return {}  # unavailable data stays empty, never fabricated zero rows
            return {'permissionLevel': 'siteRestrictedUser'}
        with tempfile.TemporaryDirectory() as root:
            args = self.args(root)
            result = gsc.collect(args, gsc.GscReader(args.site, lambda: 'secret', transport))
            self.assertEqual(result['status'], 'partial')
            self.assertEqual(len(result['errors']), 2)
            self.assertFalse((args.output_dir / 'current-pages.json').exists())
            self.assertEqual(json.loads((args.output_dir / 'current-site.json').read_text())['rows'], [])

    def test_failed_inspection_recorded_in_manifest(self):
        def transport(url, method='GET', payload=None, token=None):
            if url == gsc.INSPECT:
                raise gsc.CollectionError('Google HTTP 403; data was not collected')
            return {'permissionLevel': 'siteRestrictedUser'}
        with tempfile.TemporaryDirectory() as root:
            urls = Path(root) / 'urls.txt'
            urls.write_text('https://example.com/a\n')
            args = gsc.parser().parse_args(['--site', 'sc-domain:example.com', '--datasets', 'inspection', '--urls-file', str(urls), '--output-dir', str(Path(root) / 'snapshot')])
            with patch.object(gsc.time, 'sleep'):
                result = gsc.collect(args, gsc.GscReader(args.site, lambda: 'secret', transport))
            self.assertEqual(result['status'], 'partial')
            self.assertEqual(result['errors'][0]['dataset'], 'inspection-0000.json')
            self.assertEqual(result['requested_urls'], ['https://example.com/a'])

    def test_permission_denied_does_not_create_snapshot(self):
        with tempfile.TemporaryDirectory() as root:
            args = self.args(root)
            reader = gsc.GscReader(args.site, lambda: 'secret', lambda *a, **k: {'permissionLevel': 'siteUnverifiedUser'})
            with self.assertRaises(gsc.CollectionError):
                gsc.collect(args, reader)
            self.assertFalse(args.output_dir.exists())


if __name__ == '__main__':
    unittest.main()
