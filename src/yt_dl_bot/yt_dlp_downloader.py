"""Generic yt-dlp download adapter."""

from .download_engine import (
    Cancellation,
    DownloadEngine,
    DownloadOutcome,
    DownloadSettings,
    default_download_dependencies,
    generic_download_policy,
)
from .download_primitives import DownloadDependencies
from .setting import Settings


class YtDlpDownloader:
    def __init__(
        self,
        dependencies: DownloadDependencies | None = None,
        settings: DownloadSettings | None = None,
    ) -> None:
        if dependencies is None:
            dependencies = default_download_dependencies(
                settings or Settings(),
            )
        self.dependencies = dependencies
        self.engine = DownloadEngine(
            dependencies,
            generic_download_policy(),
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
