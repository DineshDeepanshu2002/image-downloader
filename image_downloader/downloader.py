"""Downloading of images to the local disk.

Design notes
------------
* Downloads are streamed to a temporary ``.part`` file in the target
  directory and moved into place with ``os.replace`` once complete.
  A crashed or interrupted run can therefore never leave a truncated
  file behind that looks like a valid image.
* Transient server/network errors (connection resets, 429/5xx) are
  retried with exponential backoff at the HTTP adapter level.
* One failing URL never aborts the run; each URL produces an
  independent :class:`DownloadResult` that the caller can report on.
* Filenames are derived from the URL path, sanitised, and de-duplicated
  so that two different URLs can never overwrite each other within one
  run.
"""

from __future__ import annotations

import enum
import hashlib
import logging
import os
import re
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable, Iterable, List, Optional
from urllib.parse import unquote, urlsplit

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

_CHUNK_SIZE = 64 * 1024

# Characters we allow in a filename; everything else is replaced.
# Deliberately conservative so names are safe on Linux, macOS and Windows.
_UNSAFE_CHARS = re.compile(r"[^A-Za-z0-9._-]")

# Fallback extensions for common image content types when the URL path
# itself has no usable extension.
_EXTENSION_FOR_CONTENT_TYPE = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/bmp": ".bmp",
    "image/tiff": ".tiff",
    "image/svg+xml": ".svg",
    "image/avif": ".avif",
}


class Status(enum.Enum):
    OK = "ok"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class DownloadResult:
    """Outcome of a single URL."""

    url: str
    status: Status
    path: Optional[Path] = None
    error: Optional[str] = None


class ImageDownloader:
    """Downloads a batch of image URLs into a target directory.

    Parameters
    ----------
    output_dir:
        Directory the images are written to. Created if missing.
    timeout:
        Per-request timeout in seconds (applies to both connecting and
        to gaps between received chunks).
    max_retries:
        How often transient errors (connection problems, 429/5xx) are
        retried before a URL is reported as failed.
    max_workers:
        Number of parallel downloads.
    require_image_content_type:
        If True (default), responses whose Content-Type is not
        ``image/*`` are skipped. This protects against URL lists that
        accidentally point at HTML error pages, which would otherwise be
        saved to disk as broken "images".
    max_bytes:
        Optional hard cap on the size of a single download. Exceeding
        it aborts that download and reports it as failed.
    """

    def __init__(
        self,
        output_dir: Path,
        *,
        timeout: float = 30.0,
        max_retries: int = 3,
        max_workers: int = 4,
        require_image_content_type: bool = True,
        max_bytes: Optional[int] = None,
        session_factory: Callable[[], requests.Session] | None = None,
    ) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        if max_workers < 1:
            raise ValueError("max_workers must be at least 1")

        self._output_dir = output_dir
        self._timeout = timeout
        self._max_retries = max_retries
        self._max_workers = max_workers
        self._require_image_content_type = require_image_content_type
        self._max_bytes = max_bytes
        self._session_factory = session_factory or self._default_session

        # Filenames handed out during this run; guarded by a lock
        # because downloads happen on multiple threads.
        self._claimed_names: set[str] = set()
        self._names_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def download_all(self, urls: Iterable[str]) -> List[DownloadResult]:
        """Download every URL and return one result per URL.

        Results are returned in input order.
        """
        urls = list(urls)
        if not urls:
            return []

        self._output_dir.mkdir(parents=True, exist_ok=True)

        results: dict[int, DownloadResult] = {}
        with ThreadPoolExecutor(max_workers=self._max_workers) as pool:
            futures = {
                pool.submit(self._download_one, url): index
                for index, url in enumerate(urls)
            }
            for future in as_completed(futures):
                index = futures[future]
                results[index] = future.result()

        return [results[i] for i in range(len(urls))]

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _default_session(self) -> requests.Session:
        session = requests.Session()
        retry = Retry(
            total=self._max_retries,
            backoff_factor=0.5,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=("GET",),
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        return session

    def _download_one(self, url: str) -> DownloadResult:
        logger.debug("Downloading %s", url)
        try:
            with self._session_factory() as session:
                response = session.get(url, stream=True, timeout=self._timeout)
                with response:
                    if response.status_code != 200:
                        return DownloadResult(
                            url,
                            Status.FAILED,
                            error=f"HTTP {response.status_code}",
                        )

                    content_type = self._content_type(response)
                    if (
                        self._require_image_content_type
                        and not content_type.startswith("image/")
                    ):
                        return DownloadResult(
                            url,
                            Status.SKIPPED,
                            error=(
                                f"Content-Type {content_type or '(missing)'!r} "
                                "is not an image"
                            ),
                        )

                    target = self._claim_filename(url, content_type)
                    try:
                        self._write_atomically(response, target)
                    except Exception:
                        self._release_filename(target.name)
                        raise

            logger.info("Saved %s -> %s", url, target)
            return DownloadResult(url, Status.OK, path=target)

        except requests.RequestException as exc:
            logger.warning("Failed to download %s: %s", url, exc)
            return DownloadResult(url, Status.FAILED, error=str(exc))
        except OSError as exc:
            logger.warning("Failed to store %s: %s", url, exc)
            return DownloadResult(url, Status.FAILED, error=str(exc))
        except _TooLarge as exc:
            logger.warning("Aborted %s: %s", url, exc)
            return DownloadResult(url, Status.FAILED, error=str(exc))

    @staticmethod
    def _content_type(response: requests.Response) -> str:
        raw = response.headers.get("Content-Type", "")
        # Strip parameters such as "; charset=utf-8".
        return raw.split(";", 1)[0].strip().lower()

    def _write_atomically(self, response: requests.Response, target: Path) -> None:
        """Stream the response body to *target* via a temp file."""
        temp_path = target.with_name(f".{target.name}.{uuid.uuid4().hex}.part")
        received = 0
        try:
            with temp_path.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=_CHUNK_SIZE):
                    received += len(chunk)
                    if self._max_bytes is not None and received > self._max_bytes:
                        raise _TooLarge(
                            f"download exceeded size limit of {self._max_bytes} bytes"
                        )
                    handle.write(chunk)
            os.replace(temp_path, target)
        finally:
            temp_path.unlink(missing_ok=True)

    # -- filename handling ---------------------------------------------

    def _claim_filename(self, url: str, content_type: str) -> Path:
        """Choose a unique, filesystem-safe name for this URL."""
        base = self._name_from_url(url, content_type)
        with self._names_lock:
            candidate = base
            counter = 1
            # Avoid clobbering files from previous runs as well as files
            # claimed by parallel downloads in this run.
            while (
                candidate in self._claimed_names
                or (self._output_dir / candidate).exists()
            ):
                stem, ext = os.path.splitext(base)
                candidate = f"{stem}_{counter}{ext}"
                counter += 1
            self._claimed_names.add(candidate)
        return self._output_dir / candidate

    def _release_filename(self, name: str) -> None:
        with self._names_lock:
            self._claimed_names.discard(name)

    @staticmethod
    def _name_from_url(url: str, content_type: str) -> str:
        path = urlsplit(url).path
        name = unquote(PurePosixPath(path).name)
        name = _UNSAFE_CHARS.sub("_", name).lstrip(".")

        if not name:
            # URL like http://host/ -- fall back to a stable hash so the
            # same URL always maps to the same name.
            name = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]

        stem, ext = os.path.splitext(name)
        if not ext:
            ext = _EXTENSION_FOR_CONTENT_TYPE.get(content_type, "")
            name = stem + ext

        # Keep well clear of typical 255-byte filesystem limits.
        if len(name) > 150:
            stem, ext = os.path.splitext(name)
            digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:8]
            name = f"{stem[:100]}_{digest}{ext[:20]}"

        return name


class _TooLarge(Exception):
    """Raised internally when a download exceeds the configured size cap."""
