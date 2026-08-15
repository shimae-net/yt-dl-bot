"""Compatibility exports for the former download-service module.

Infrastructure-facing protocols, dependencies, and retry primitives now live
in :mod:`yt_dl_bot.download_primitives`. This module deliberately remains so
existing integrations importing its public types continue to work.
"""

from .download_primitives import (
    DownloadDependencies,
    DownloadRetryLimitExceeded,
    DownloadWaitError,
    MakeDirectory,
    PermanentDownloadError,
    RetryDecision,
    RetryPolicy,
    RetryStatus,
    YouTubeDL,
    YouTubeDLFactory,
)

__all__ = [
    "DownloadDependencies",
    "DownloadRetryLimitExceeded",
    "DownloadWaitError",
    "MakeDirectory",
    "PermanentDownloadError",
    "RetryDecision",
    "RetryPolicy",
    "RetryStatus",
    "YouTubeDL",
    "YouTubeDLFactory",
]
