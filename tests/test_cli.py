"""End-to-end tests: run the CLI against a real local HTTP server."""

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from image_downloader.cli import EXIT_FATAL, EXIT_OK, EXIT_PARTIAL_FAILURE, main

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"fake png body"


class _Handler(BaseHTTPRequestHandler):
    routes = {}

    def do_GET(self):  # noqa: N802 (name mandated by BaseHTTPRequestHandler)
        route = self.routes.get(self.path)
        if route is None:
            self.send_error(404)
            return
        status, content_type, body = route
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # keep test output clean
        pass


@pytest.fixture
def http_server():
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    thread.join()


def test_downloads_all_listed_images(http_server, tmp_path):
    _Handler.routes = {
        "/images/271947.jpg": (200, "image/jpeg", b"first"),
        "/images/24174.jpg": (200, "image/jpeg", b"second"),
        "/img/992147.jpg": (200, "image/jpeg", b"third"),
    }
    url_file = tmp_path / "urls.txt"
    url_file.write_text(
        f"{http_server}/images/271947.jpg\n"
        f"{http_server}/images/24174.jpg\n"
        f"{http_server}/img/992147.jpg\n"
    )
    out = tmp_path / "out"

    exit_code = main([str(url_file), "--output-dir", str(out)])

    assert exit_code == EXIT_OK
    assert sorted(p.name for p in out.iterdir()) == [
        "24174.jpg",
        "271947.jpg",
        "992147.jpg",
    ]
    assert (out / "271947.jpg").read_bytes() == b"first"


def test_partial_failure_exit_code(http_server, tmp_path):
    _Handler.routes = {"/ok.png": (200, "image/png", PNG_BYTES)}
    url_file = tmp_path / "urls.txt"
    url_file.write_text(f"{http_server}/ok.png\n{http_server}/missing.png\n")
    out = tmp_path / "out"

    exit_code = main([str(url_file), "--output-dir", str(out), "--retries", "0"])

    assert exit_code == EXIT_PARTIAL_FAILURE
    assert (out / "ok.png").read_bytes() == PNG_BYTES


def test_missing_url_file_is_fatal(tmp_path):
    exit_code = main([str(tmp_path / "nope.txt")])
    assert exit_code == EXIT_FATAL


def test_file_with_no_usable_urls_is_fatal(tmp_path):
    url_file = tmp_path / "urls.txt"
    url_file.write_text("# only comments\n\nnot a url\n")
    exit_code = main([str(url_file)])
    assert exit_code == EXIT_FATAL
