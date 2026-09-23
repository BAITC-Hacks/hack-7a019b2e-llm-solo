"""Real loopback HTTP regressions for the limited static export preview."""
from http.client import HTTPConnection
from pathlib import Path
import tempfile
from threading import Thread
import unittest

from moneygraph.server import ALLOWED_FILES, make_server


class StaticExportServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.out = self.root / 'out'
        self.out.mkdir()
        for relative in ALLOWED_FILES:
            path = self.out / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('export:' + relative, encoding='utf-8')
        for relative in ('research/private.json', 'verification/check.json', '.env', 'frontend/extra.json'):
            path = self.out / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('private fixture', encoding='utf-8')
        (self.root / 'outside.json').write_text('outside fixture', encoding='utf-8')
        self.server = make_server(self.out, 0)
        self.worker = Thread(target=self.server.serve_forever,
                             kwargs={'poll_interval': .01}, daemon=True)
        self.worker.start()
        self.addCleanup(self.close_server)

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.worker.join(timeout=5)

    def request(self, target, method='GET', headers=None):
        client = HTTPConnection('127.0.0.1', self.server.server_address[1], timeout=5)
        try:
            client.request(method, target, headers=headers or {})
            response = client.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            client.close()

    def test_only_loopback_is_bound_and_exports_have_security_headers(self):
        self.assertEqual(self.server.server_address[0], '127.0.0.1')
        for relative in sorted(ALLOWED_FILES):
            with self.subTest(relative=relative):
                status, headers, body = self.request('/' + relative)
                self.assertEqual(status, 200)
                self.assertEqual(body, ('export:' + relative).encode())
                self.assertEqual(headers['Cache-Control'], 'no-store')
                self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')
                self.assertNotIn('Content-Security-Policy', headers)

    def test_html_aliases_and_json_query_use_allowed_files(self):
        for target, relative in (('/', 'report.html'), ('/frontend/', 'frontend/index.html'),
                                 ('/graph.json?v=1', 'graph.json')):
            with self.subTest(target=target):
                status, headers, body = self.request(target)
                self.assertEqual(status, 200)
                self.assertEqual(body, ('export:' + relative).encode())
                expected_type = 'application/json' if relative.endswith('.json') else 'text/html'
                self.assertTrue(headers['Content-Type'].startswith(expected_type))
        status, headers, body = self.request('/frontend')
        self.assertEqual(status, 308)
        self.assertEqual(headers['Location'], '/frontend/')
        self.assertEqual(body, b'')

    def test_listing_hidden_research_and_traversal_are_forbidden(self):
        paths = ['/research/', '/research/private.json', '/verification/', '/verification/check.json',
                 '/.env', '/frontend/extra.json', '/../outside.json', '/%2e%2e/outside.json',
                 '/frontend/../graph.json', '/frontend/%2e%2e/graph.json',
                 '/%2e%2e%5coutside.json', '/graph.json/']
        for path in paths:
            with self.subTest(path=path):
                status, headers, body = self.request(path)
                self.assertEqual(status, 403)
                self.assertEqual(headers['Cache-Control'], 'no-store')
                self.assertNotIn(b'private fixture', body)
                self.assertNotIn(b'outside fixture', body)

    def test_head_has_get_length_without_body_and_forbidden_head_is_denied(self):
        status, headers, body = self.request('/graph.json', 'HEAD')
        self.assertEqual(status, 200)
        self.assertEqual(body, b'')
        self.assertEqual(int(headers['Content-Length']), len(b'export:graph.json'))
        status, headers, body = self.request('/research/private.json', 'HEAD')
        self.assertEqual(status, 403)
        self.assertEqual(body, b'')

    def test_missing_allowed_export_is_404_and_post_does_not_write(self):
        (self.out / 'metrics.json').unlink()
        self.assertEqual(self.request('/metrics.json')[0], 404)
        before = (self.out / 'graph.json').read_bytes()
        self.assertEqual(self.request('/graph.json', 'POST')[0], 501)
        self.assertEqual((self.out / 'graph.json').read_bytes(), before)

    def test_external_symbolic_link_is_not_served(self):
        path = self.out / 'graph.json'
        path.unlink()
        try:
            path.symlink_to(self.root / 'outside.json')
        except OSError as exc:
            self.skipTest('Creating symlinks is unavailable for this OS account: ' + str(exc))
        for method in ('GET', 'HEAD'):
            with self.subTest(method=method):
                status, _, body = self.request('/graph.json', method)
                self.assertEqual(status, 403)
                self.assertNotIn(b'outside fixture', body)

    def test_invalid_port_is_rejected_before_socket_binding(self):
        for port in (-1, 65536, True, '8765'):
            with self.subTest(port=port), self.assertRaises(ValueError):
                make_server(self.out, port)


if __name__ == '__main__':
    unittest.main()
