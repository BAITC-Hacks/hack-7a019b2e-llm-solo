"""Loopback-only static preview of explicitly exported files, without an API."""
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import stat
from urllib.parse import unquote, urlsplit


ALLOWED_FILES = frozenset({
    'nodes_roles.csv', 'clusters.csv', 'top_nodes.csv',
    'report.html', 'graph.json', 'metrics.json', 'run.json',
    'frontend/index.html', 'frontend/app.js', 'frontend/data.js', 'frontend/styles.css',
})
CONTENT_TYPES = {
    '.html': 'text/html; charset=utf-8', '.json': 'application/json; charset=utf-8',
    '.csv': 'text/csv; charset=utf-8', '.js': 'text/javascript; charset=utf-8',
    '.css': 'text/css; charset=utf-8',
}


class ExportRequestHandler(SimpleHTTPRequestHandler):
    """Never expose arbitrary files, directory listings, or linked external files."""

    def end_headers(self):
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Cache-Control', 'no-store')
        super().end_headers()

    def list_directory(self, path):
        self.send_error(403, 'Directory listing is disabled')
        return None

    def send_head(self):
        try:
            url = urlsplit(self.path)
            if url.scheme or url.netloc:
                self.send_error(403, 'Only local export paths are allowed')
                return None
            path = unquote(url.path, encoding='utf-8', errors='strict')
        except (ValueError, UnicodeError):
            self.send_error(400, 'Invalid export path')
            return None
        if path == '/frontend':
            # Keep relative app.js/styles.css URLs under /frontend/ in browsers.
            self.send_response(308)
            self.send_header('Location', '/frontend/')
            self.send_header('Content-Length', '0')
            self.end_headers()
            return None
        aliases = {'/': 'report.html', '/frontend/': 'frontend/index.html'}
        relative = aliases.get(path, path[1:] if path.startswith('/') else '')
        if relative not in ALLOWED_FILES:
            self.send_error(403, 'Only generated export files are available')
            return None
        root = Path(self.directory)
        try:
            # resolve follows symbolic links and Windows junctions. Open the
            # checked destination, rather than the original link path.
            target = (root / relative).resolve(strict=True)
            if not target.is_relative_to(root) or not target.is_file():
                self.send_error(403, 'File is outside the export directory')
                return None
            handle = target.open('rb')
        except FileNotFoundError:
            self.send_error(404, 'Export file is missing')
            return None
        except (OSError, RuntimeError):
            self.send_error(403, 'Export file is not accessible')
            return None
        try:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode):
                handle.close()
                self.send_error(403, 'Only regular export files are available')
                return None
            self.send_response(200)
            self.send_header('Content-Type', CONTENT_TYPES[Path(relative).suffix.lower()])
            self.send_header('Content-Length', str(info.st_size))
            self.end_headers()
            return handle
        except Exception:
            handle.close()
            raise


def make_server(out, port=8765):
    """Build a static server; the caller owns its serving and shutdown lifecycle.

    Port zero selects a free local port for isolated tests. This is a local
    single-user preview, not an authenticated hosting service.
    """
    if not isinstance(port, int) or isinstance(port, bool) or not 0 <= port <= 65535:
        raise ValueError('Порт должен быть целым числом от 0 до 65535')
    root = Path(out).resolve(strict=True)
    if not root.is_dir():
        raise ValueError('Каталог результатов не существует')
    handler = partial(ExportRequestHandler, directory=str(root))
    return ThreadingHTTPServer(('127.0.0.1', port), handler)
