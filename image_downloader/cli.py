"""Command line interface.

Usage::

    python -m image_downloader urls.txt
    python -m image_downloader urls.txt --output-dir ./images --workers 8

Exit codes:
    0  every valid URL was downloaded successfully
    1  at least one URL failed or was skipped
    2  the run could not start (bad arguments, unreadable input file, ...)
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Optional, Sequence

from .downloader import ImageDownloader, Status
from .urlfile import parse_url_file

logger = logging.getLogger(__name__)

EXIT_OK = 0
EXIT_PARTIAL_FAILURE = 1
EXIT_FATAL = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="image-downloader",
        description=(
            "Download all images listed in a plaintext file "
            "(one URL per line) to the local disk."
        ),
    )
    parser.add_argument(
        "url_file",
        type=Path,
        help="plaintext file containing one image URL per line",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        default=Path("downloads"),
        help="directory to store the images in (default: ./downloads)",
    )
    parser.add_argument(
        "-w",
        "--workers",
        type=int,
        default=4,
        help="number of parallel downloads (default: 4)",
    )
    parser.add_argument(
        "-t",
        "--timeout",
        type=float,
        default=30.0,
        help="per-request timeout in seconds (default: 30)",
    )
    parser.add_argument(
        "-r",
        "--retries",
        type=int,
        default=3,
        help="retries for transient errors per URL (default: 3)",
    )
    parser.add_argument(
        "--max-size",
        type=int,
        default=None,
        metavar="BYTES",
        help="abort any single download larger than this many bytes",
    )
    parser.add_argument(
        "--allow-any-content-type",
        action="store_true",
        help=(
            "also save responses whose Content-Type is not image/* "
            "(by default such responses are skipped)"
        ),
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="enable debug logging",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.workers < 1:
        logger.error("--workers must be at least 1")
        return EXIT_FATAL
    if args.timeout <= 0:
        logger.error("--timeout must be positive")
        return EXIT_FATAL

    try:
        url_file = parse_url_file(args.url_file)
    except OSError as exc:
        logger.error("Cannot read %s: %s", args.url_file, exc)
        return EXIT_FATAL

    for bad in url_file.invalid:
        logger.warning(
            "Ignoring line %d of %s (%s): %s",
            bad.line_number,
            args.url_file,
            bad.reason,
            bad.content,
        )

    if not url_file.urls:
        logger.error("No usable URLs found in %s", args.url_file)
        return EXIT_FATAL

    downloader = ImageDownloader(
        args.output_dir,
        timeout=args.timeout,
        max_retries=args.retries,
        max_workers=args.workers,
        require_image_content_type=not args.allow_any_content_type,
        max_bytes=args.max_size,
    )

    try:
        results = downloader.download_all(url_file.urls)
    except OSError as exc:
        logger.error("Cannot write to %s: %s", args.output_dir, exc)
        return EXIT_FATAL

    ok = sum(1 for r in results if r.status is Status.OK)
    skipped = [r for r in results if r.status is Status.SKIPPED]
    failed = [r for r in results if r.status is Status.FAILED]

    for result in skipped:
        logger.warning("Skipped %s: %s", result.url, result.error)
    for result in failed:
        logger.error("Failed %s: %s", result.url, result.error)

    logger.info(
        "Done: %d downloaded, %d skipped, %d failed (of %d URLs)",
        ok,
        len(skipped),
        len(failed),
        len(results),
    )

    return EXIT_OK if not failed and not skipped else EXIT_PARTIAL_FAILURE


if __name__ == "__main__":
    sys.exit(main())
