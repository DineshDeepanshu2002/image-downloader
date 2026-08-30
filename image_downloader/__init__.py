"""Download images listed in a plaintext URL file to the local disk."""

from .downloader import DownloadResult, ImageDownloader, Status
from .urlfile import parse_url_file

__all__ = ["DownloadResult", "ImageDownloader", "Status", "parse_url_file"]
__version__ = "1.0.0"
