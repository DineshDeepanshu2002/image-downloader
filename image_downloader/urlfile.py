"""Parsing of plaintext URL list files.

The input format is one URL per line. Blank lines and lines starting
with '#' are ignored so the files can be annotated. Anything else that
does not look like an absolute http(s) URL is reported as invalid
instead of being silently dropped -- in a live system we want bad input
to be visible, not swallowed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List
from urllib.parse import urlsplit

ALLOWED_SCHEMES = frozenset({"http", "https"})


@dataclass
class InvalidLine:
    """A line that could not be used, with enough context to debug it."""

    line_number: int
    content: str
    reason: str


@dataclass
class UrlFile:
    """Result of parsing a URL list file."""

    urls: List[str] = field(default_factory=list)
    invalid: List[InvalidLine] = field(default_factory=list)


def parse_url_file(path: Path) -> UrlFile:
    """Read *path* and return the URLs it contains.

    Duplicate URLs are kept only once (downloading the same file twice
    would just waste bandwidth and overwrite the first copy).

    Raises OSError if the file cannot be read.
    """
    result = UrlFile()
    seen = set()

    with path.open("r", encoding="utf-8") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue

            reason = _validate_url(line)
            if reason is not None:
                result.invalid.append(InvalidLine(line_number, line, reason))
                continue

            if line in seen:
                continue
            seen.add(line)
            result.urls.append(line)

    return result


def _validate_url(url: str) -> str | None:
    """Return a human-readable rejection reason, or None if the URL is usable."""
    try:
        parts = urlsplit(url)
    except ValueError as exc:
        return f"unparseable URL: {exc}"

    if parts.scheme not in ALLOWED_SCHEMES:
        return (
            f"unsupported scheme {parts.scheme or '(none)'!r}, expected http or https"
        )
    if not parts.netloc:
        return "URL has no host"
    return None
