"""yt-dlp integration protocols, dependencies, and retry primitives.

This module defines the infrastructure-facing values shared by download
adapters and the download engine. Application use cases live in
``video_download_service``.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Protocol

from .external_error_adapter import youtube_scheduled_delay


class YouTubeDL(Protocol):
    def __enter__(self) -> "YouTubeDL": ...

    def __exit__(self, *args: object) -> None: ...

    def extract_info(self, url: str, *, download: bool) -> dict[str, object]: ...

    def prepare_filename(self, info: dict[str, object]) -> str: ...


class YouTubeDLFactory(Protocol):
    def __call__(self, options: dict[str, object] | None = None) -> YouTubeDL: ...


class MakeDirectory(Protocol):
    def __call__(
        self,
        path: Path,
        *,
        parents: bool = False,
        exist_ok: bool = False,
    ) -> None: ...


@dataclass(frozen=True)
class DownloadDependencies:
    """Injectable operations used by download adapters."""

    ydl_factory: YouTubeDLFactory
    now: Callable[[], datetime]
    sleep: Callable[[float], None]
    path_exists: Callable[[Path], bool]
    make_directory: MakeDirectory
    move: Callable[[Path, Path], object]
    tmp_path: Path
    save_path: Path

    def __post_init__(self) -> None:
        object.__setattr__(self, "tmp_path", Path(self.tmp_path))
        object.__setattr__(self, "save_path", Path(self.save_path))

    def ensure_directory(self, path: Path) -> None:
        path = Path(path)
        if not self.path_exists(path):
            self.make_directory(path, parents=True, exist_ok=True)


class RetryStatus(Enum):
    RETRYABLE = "retryable"
    PERMANENT_FAILURE = "permanent_failure"


@dataclass(frozen=True)
class RetryDecision:
    status: RetryStatus
    wait_seconds: float = 0


class DownloadWaitError(Exception):
    """Base exception for failures while waiting for a scheduled download."""

    def __init__(
        self,
        message: str,
        *,
        original_error: BaseException,
        attempts: int,
        waited_seconds: float,
    ) -> None:
        super().__init__(message)
        self.original_error = original_error
        self.attempts = attempts
        self.waited_seconds = waited_seconds


class PermanentDownloadError(DownloadWaitError):
    """The failure is not a recognized scheduled-live condition."""


class DownloadRetryLimitExceeded(DownloadWaitError):
    """A scheduled download exceeded its configured retry budget."""


@dataclass(frozen=True)
class RetryPolicy:
    """Bound retry policy for YouTube scheduled-live metadata checks."""

    max_attempts: int = 10
    max_wait_seconds: float = 6 * 60 * 60

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if self.max_wait_seconds < 0:
            raise ValueError("max_wait_seconds must not be negative")

    def decide(self, error: BaseException) -> RetryDecision:
        wait_seconds = youtube_scheduled_delay(error)
        if wait_seconds is None:
            return RetryDecision(RetryStatus.PERMANENT_FAILURE)
        return RetryDecision(RetryStatus.RETRYABLE, wait_seconds)
