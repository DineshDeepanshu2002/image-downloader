"""Step definitions for features/download.feature (pytest-bdd).

These acceptance tests exercise the CLI end-to-end against a real local
HTTP server, phrased in the language of the original requirement.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from image_downloader.cli import EXIT_FATAL, EXIT_OK, EXIT_PARTIAL_FAILURE, main

scenarios("features/download.feature")

JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"acceptance test image"


@dataclass
class World:
    """State shared between the steps of one scenario."""

    url_file: Path | None = None
    output_dir: Path | None = None
    expected_images: int = 0
    exit_code: int | None = None
    routes: dict[str, bytes] = field(default_factory=dict)


class _Handler(BaseHTTPRequestHandler):
    world: World

    def do_GET(self) -> None:  # noqa: N802 (name mandated by base class)
        body = self.world.routes.get(self.path)
        if body is None:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture
def world(tmp_path: Path) -> World:
    w = World(output_dir=tmp_path / "out")
    return w


@pytest.fixture
def server_url(world: World):
    _Handler.world = world
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    thread.join()


def _write_url_file(world: World, tmp_path: Path, urls: list[str]) -> None:
    world.url_file = tmp_path / "urls.txt"
    world.url_file.write_text("\n".join(urls) + "\n")


@given(parsers.parse("a URL file listing {count:d} available images"))
def url_file_with_images(
    world: World, server_url: str, tmp_path: Path, count: int
) -> None:
    urls = []
    for i in range(count):
        path = f"/images/{i}.jpg"
        world.routes[path] = JPEG_BYTES
        urls.append(server_url + path)
    world.expected_images = count
    _write_url_file(world, tmp_path, urls)


@given(
    parsers.parse(
        "a URL file listing {count:d} available images and {dead:d} dead link"
    )
)
def url_file_with_images_and_dead_link(
    world: World, server_url: str, tmp_path: Path, count: int, dead: int
) -> None:
    urls = []
    for i in range(count):
        path = f"/images/{i}.jpg"
        world.routes[path] = JPEG_BYTES
        urls.append(server_url + path)
    for i in range(dead):
        urls.append(f"{server_url}/missing/{i}.jpg")
    world.expected_images = count
    _write_url_file(world, tmp_path, urls)


@given("a URL file that does not exist")
def url_file_missing(world: World, tmp_path: Path) -> None:
    world.url_file = tmp_path / "does-not-exist.txt"


@when("I run the downloader on that file")
def run_downloader(world: World) -> None:
    assert world.url_file is not None and world.output_dir is not None
    world.exit_code = main(
        [
            str(world.url_file),
            "--output-dir",
            str(world.output_dir),
            "--retries",
            "0",
        ]
    )


@then(parsers.parse("all {count:d} images are stored on the local disk"))
def images_are_on_disk(world: World, count: int) -> None:
    assert world.output_dir is not None
    files = list(world.output_dir.iterdir())
    assert len(files) == count
    for file in files:
        assert file.read_bytes() == JPEG_BYTES


@then("no images are stored on the local disk")
def no_images_on_disk(world: World) -> None:
    assert world.output_dir is not None
    assert not world.output_dir.exists() or not list(world.output_dir.iterdir())


@then("the exit code is 0")
def exit_ok(world: World) -> None:
    assert world.exit_code == EXIT_OK


@then("the exit code signals a partial failure")
def exit_partial(world: World) -> None:
    assert world.exit_code == EXIT_PARTIAL_FAILURE


@then("the exit code signals a fatal error")
def exit_fatal(world: World) -> None:
    assert world.exit_code == EXIT_FATAL
