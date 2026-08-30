import responses
from requests.exceptions import ConnectionError as RequestsConnectionError

from image_downloader.downloader import ImageDownloader, Status

JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"fake image body"


def make_downloader(tmp_path, **kwargs):
    kwargs.setdefault("max_workers", 1)
    kwargs.setdefault("max_retries", 0)
    return ImageDownloader(tmp_path / "out", **kwargs)


@responses.activate
def test_downloads_image_to_disk(tmp_path):
    responses.get(
        "http://example.com/images/271947.jpg",
        body=JPEG_BYTES,
        content_type="image/jpeg",
    )
    downloader = make_downloader(tmp_path)

    results = downloader.download_all(["http://example.com/images/271947.jpg"])

    assert len(results) == 1
    assert results[0].status is Status.OK
    assert results[0].path.name == "271947.jpg"
    assert results[0].path.read_bytes() == JPEG_BYTES


@responses.activate
def test_results_keep_input_order(tmp_path):
    responses.get(
        "http://example.com/a.jpg", body=JPEG_BYTES, content_type="image/jpeg"
    )
    responses.get(
        "http://example.com/b.jpg", body=JPEG_BYTES, content_type="image/jpeg"
    )
    downloader = make_downloader(tmp_path, max_workers=4)

    results = downloader.download_all(
        ["http://example.com/a.jpg", "http://example.com/b.jpg"]
    )

    assert [r.url for r in results] == [
        "http://example.com/a.jpg",
        "http://example.com/b.jpg",
    ]


@responses.activate
def test_same_filename_from_different_hosts_does_not_clobber(tmp_path):
    responses.get("http://one.com/cat.jpg", body=b"one", content_type="image/jpeg")
    responses.get("http://two.com/cat.jpg", body=b"two", content_type="image/jpeg")
    downloader = make_downloader(tmp_path)

    results = downloader.download_all(
        ["http://one.com/cat.jpg", "http://two.com/cat.jpg"]
    )

    names = sorted(r.path.name for r in results)
    assert names == ["cat.jpg", "cat_1.jpg"]
    contents = sorted(r.path.read_bytes() for r in results)
    assert contents == [b"one", b"two"]


@responses.activate
def test_existing_file_from_previous_run_is_not_overwritten(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "cat.jpg").write_bytes(b"old")
    responses.get("http://example.com/cat.jpg", body=b"new", content_type="image/jpeg")
    downloader = ImageDownloader(out, max_workers=1, max_retries=0)

    results = downloader.download_all(["http://example.com/cat.jpg"])

    assert results[0].path.name == "cat_1.jpg"
    assert (out / "cat.jpg").read_bytes() == b"old"


@responses.activate
def test_http_error_is_reported_not_raised(tmp_path):
    responses.get("http://example.com/gone.jpg", status=404)
    downloader = make_downloader(tmp_path)

    results = downloader.download_all(["http://example.com/gone.jpg"])

    assert results[0].status is Status.FAILED
    assert "404" in results[0].error


@responses.activate
def test_connection_error_is_reported_not_raised(tmp_path):
    responses.get(
        "http://example.com/a.jpg",
        body=RequestsConnectionError("connection refused"),
    )
    downloader = make_downloader(tmp_path)

    results = downloader.download_all(["http://example.com/a.jpg"])

    assert results[0].status is Status.FAILED
    assert results[0].error


@responses.activate
def test_one_failure_does_not_stop_the_rest(tmp_path):
    responses.get("http://example.com/bad.jpg", status=500)
    responses.get(
        "http://example.com/good.jpg", body=JPEG_BYTES, content_type="image/jpeg"
    )
    downloader = make_downloader(tmp_path)

    results = downloader.download_all(
        ["http://example.com/bad.jpg", "http://example.com/good.jpg"]
    )

    assert results[0].status is Status.FAILED
    assert results[1].status is Status.OK


@responses.activate
def test_non_image_content_type_is_skipped(tmp_path):
    responses.get(
        "http://example.com/error.jpg",
        body="<html>Not Found</html>",
        content_type="text/html",
    )
    downloader = make_downloader(tmp_path)

    results = downloader.download_all(["http://example.com/error.jpg"])

    assert results[0].status is Status.SKIPPED
    assert "text/html" in results[0].error
    assert list((tmp_path / "out").iterdir()) == []


@responses.activate
def test_content_type_check_can_be_disabled(tmp_path):
    responses.get(
        "http://example.com/blob",
        body=b"data",
        content_type="application/octet-stream",
    )
    downloader = make_downloader(tmp_path, require_image_content_type=False)

    results = downloader.download_all(["http://example.com/blob"])

    assert results[0].status is Status.OK


@responses.activate
def test_extension_is_added_from_content_type_when_missing(tmp_path):
    responses.get(
        "http://example.com/img/992147",
        body=JPEG_BYTES,
        content_type="image/jpeg",
    )
    downloader = make_downloader(tmp_path)

    results = downloader.download_all(["http://example.com/img/992147"])

    assert results[0].path.name == "992147.jpg"


@responses.activate
def test_unsafe_filename_characters_are_sanitised(tmp_path):
    responses.get(
        "http://example.com/a%2F..%2Fevil.jpg",
        body=JPEG_BYTES,
        content_type="image/jpeg",
    )
    downloader = make_downloader(tmp_path)

    results = downloader.download_all(["http://example.com/a%2F..%2Fevil.jpg"])

    assert results[0].status is Status.OK
    # Whatever the exact name, it must resolve inside the output dir.
    assert results[0].path.resolve().parent == (tmp_path / "out").resolve()
    assert "/" not in results[0].path.name


@responses.activate
def test_size_limit_aborts_oversized_download(tmp_path):
    responses.get(
        "http://example.com/huge.jpg",
        body=b"x" * 1000,
        content_type="image/jpeg",
    )
    downloader = make_downloader(tmp_path, max_bytes=100)

    results = downloader.download_all(["http://example.com/huge.jpg"])

    assert results[0].status is Status.FAILED
    assert "size limit" in results[0].error
    # No partial file may be left behind.
    assert list((tmp_path / "out").iterdir()) == []


@responses.activate
def test_no_part_files_remain_after_successful_run(tmp_path):
    responses.get(
        "http://example.com/a.jpg", body=JPEG_BYTES, content_type="image/jpeg"
    )
    downloader = make_downloader(tmp_path)

    downloader.download_all(["http://example.com/a.jpg"])

    leftovers = [p for p in (tmp_path / "out").iterdir() if p.suffix == ".part"]
    assert leftovers == []


def test_empty_url_list_returns_empty_result(tmp_path):
    downloader = make_downloader(tmp_path)
    assert downloader.download_all([]) == []
