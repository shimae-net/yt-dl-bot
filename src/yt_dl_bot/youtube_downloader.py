"""YouTube download adapter."""

from .download_engine import (
    Cancellation,
    DownloadEngine,
    DownloadOutcome,
    DownloadSettings,
    default_download_dependencies,
    youtube_download_policy,
)
from .download_primitives import DownloadDependencies, RetryPolicy
from .setting import Settings
from .url_validation import extract_youtube_video_id


class YouTubeDownloader:
    def __init__(
        self,
        dependencies: DownloadDependencies | None = None,
        retry_policy: RetryPolicy | None = None,
        settings: DownloadSettings | None = None,
    ) -> None:
        if dependencies is None:
            dependencies = default_download_dependencies(
                settings or Settings(),
            )
        self.dependencies = dependencies
        self.retry_policy = retry_policy or youtube_download_policy().retry_policy
        self.engine = DownloadEngine(
            dependencies,
            youtube_download_policy(self.retry_policy),
        )

    def check_availability(self, url: str) -> str:
        return self.engine.check_availability(url, info_loader=self.get_info)

    def download_video(self, url: str) -> DownloadOutcome:
        return self.engine.download_video(url, info_loader=self.get_info)

    def download_video_cancellable(
        self,
        url: str,
        cancellation_token: Cancellation,
    ) -> DownloadOutcome:
        return self.engine.download_video(
            url,
            info_loader=self.get_info,
            cancellation_token=cancellation_token,
        )

    def get_info(self, url: str) -> dict[str, object]:
        return self.engine.get_info(url)

    def get_video_id(self, url: str) -> str:
        return extract_youtube_video_id(url)
