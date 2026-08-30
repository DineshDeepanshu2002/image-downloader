from pathlib import Path

import pytest

from image_downloader.urlfile import parse_url_file


def write(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "urls.txt"
    path.write_text(content, encoding="utf-8")
    return path


def test_parses_one_url_per_line(tmp_path):
    path = write(
        tmp_path,
        "http://example.com/a.jpg\nhttps://example.com/b.png\n",
    )
    result = parse_url_file(path)
    assert result.urls == [
        "http://example.com/a.jpg",
        "https://example.com/b.png",
    ]
    assert result.invalid == []


def test_ignores_blank_lines_comments_and_whitespace(tmp_path):
    path = write(
        tmp_path,
        "\n  \n# a comment\n  http://example.com/a.jpg  \n",
    )
    result = parse_url_file(path)
    assert result.urls == ["http://example.com/a.jpg"]
    assert result.invalid == []


def test_deduplicates_urls(tmp_path):
    path = write(
        tmp_path,
        "http://example.com/a.jpg\nhttp://example.com/a.jpg\n",
    )
    result = parse_url_file(path)
    assert result.urls == ["http://example.com/a.jpg"]


def test_reports_invalid_lines_with_line_numbers(tmp_path):
    path = write(
        tmp_path,
        "http://example.com/a.jpg\n"
        "not a url\n"
        "ftp://example.com/b.jpg\n"
        "http:///no-host.jpg\n",
    )
    result = parse_url_file(path)
    assert result.urls == ["http://example.com/a.jpg"]
    assert [bad.line_number for bad in result.invalid] == [2, 3, 4]
    assert "scheme" in result.invalid[1].reason


def test_missing_file_raises_oserror(tmp_path):
    with pytest.raises(OSError):
        parse_url_file(tmp_path / "does-not-exist.txt")
