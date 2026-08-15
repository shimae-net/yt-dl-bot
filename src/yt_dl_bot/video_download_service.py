"""Application services for checking and downloading videos."""

import shutil
from typing import Protocol

import yt_dlp

from .application_errors import VideoCheckError, VideoDownloadError
from .application_results import DownloadResult
from .artifact_discovery import ArtifactDiscoveryError
from .download_engine import Cancellation, DownloadOutcome
from .download_primitives import DownloadWaitError
from .external_error_adapter import error_detail, is_twitch_offline

DOWNLOAD_ADAPTER_ERRORS = (
    yt_dlp.utils.DownloadError,
    yt_dlp.utils.ExtractorError,
    DownloadWaitError,
    ArtifactDiscoveryError,
    OSError,
    shutil.Error,
)


class DownloadAdapter(Protocol):
    def check_availability(self, url: str) -> str: ...

    def download_video(self, url: str) -> DownloadOutcome: ...

    def download_video_cancellable(
        self,
        url: str,
        cancellation_token: Cancellation,
    ) -> DownloadOutcome: ...


class TwitchStreamOffline(Exception):
    """The requested Twitch channel is not currently live."""


class VideoDownloadService:
    def __init__(self, downloader: DownloadAdapter) -> None:
        self.downloader = downloader

    def check(self, url: str) -> str:
        try:
            return self.downloader.check_availability(url=url)
        except DOWNLOAD_ADAPTER_ERRORS as error:
            raise VideoCheckError(
                f"Unable to check video: {error_detail(error)}",
                original_error=error,
            ) from error

    def download(
        self,
        url: str,
        cancellation_token: Cancellation | None = None,
    ) -> DownloadResult:
        try:
            if cancellation_token is None:
                outcome = self.downloader.download_video(url=url)
            else:
                outcome = self.downloader.download_video_cancellable(
                    url=url,
                    cancellation_token=cancellation_token,
                )
        except DOWNLOAD_ADAPTER_ERRORS as error:
            raise VideoDownloadError(
                f"Unable to download video: {error_detail(error)}",
                original_error=error,
            ) from error
        return DownloadResult.from_outcome(outcome)


class TwitchDownloadService(VideoDownloadService):
    def check(self, url: str) -> str:
        try:
            return self.downloader.check_availability(url=url)
        except DOWNLOAD_ADAPTER_ERRORS as error:
            if is_twitch_offline(error):
                raise TwitchStreamOffline(error_detail(error)) from error
            raise VideoCheckError(
                f"Unable to check Twitch stream: {error_detail(error)}",
                original_error=error,
            ) from error
