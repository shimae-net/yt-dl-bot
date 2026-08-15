"""Shared yt-dlp download engine with explicit site policies."""

import datetime
import shutil
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import yt_dlp

from .artifact_discovery import DownloadedArtifacts, discover_download_artifacts
from .artifact_store import ArtifactStore
from .download_primitives import (
    DownloadDependencies,
    DownloadRetryLimitExceeded,
    PermanentDownloadError,
    RetryPolicy,
    RetryStatus,
)
from .external_error_adapter import youtube_scheduled_notice

DownloadInfo = dict[str, object]
InfoLoader = Callable[[str], DownloadInfo]


class Cancellation(Protocol):
    def raise_if_cancelled(self) -> None: ...

    def wait(self, timeout: float) -> None: ...


class DownloadSettings(Protocol):
    TMP_PATH: Path
    SAVE_PATH: Path


def _make_directory(
    path: Path,
    *,
    parents: bool = False,
    exist_ok: bool = False,
) -> None:
    path.mkdir(parents=parents, exist_ok=exist_ok)


@dataclass(frozen=True)
class DownloadPolicy:
    retry_policy: RetryPolicy | None
    scheduled_notice: bool
    require_metadata: bool
    require_thumbnail: bool
    live_from_start: bool
    use_cookie_file: bool
    cookie_path: Path = Path("cookie/cookies.txt")


@dataclass(frozen=True)
class DownloadOutcome:
    """Stable adapter output that does not expose yt-dlp metadata."""

    video_id: str
    title: str
    source_url: str
    artifacts: DownloadedArtifacts


def youtube_download_policy(retry_policy: RetryPolicy | None = None) -> DownloadPolicy:
    return DownloadPolicy(
        retry_policy=retry_policy or RetryPolicy(),
        scheduled_notice=True,
        require_metadata=True,
        require_thumbnail=True,
        live_from_start=True,
        use_cookie_file=False,
    )


def generic_download_policy() -> DownloadPolicy:
    return DownloadPolicy(
        retry_policy=None,
        scheduled_notice=False,
        require_metadata=False,
        require_thumbnail=False,
        live_from_start=False,
        use_cookie_file=True,
    )


def default_download_dependencies(settings: DownloadSettings) -> DownloadDependencies:
    return DownloadDependencies(
        ydl_factory=yt_dlp.YoutubeDL,
        now=datetime.datetime.now,
        sleep=time.sleep,
        path_exists=Path.exists,
        make_directory=_make_directory,
        move=shutil.move,
        tmp_path=Path(settings.TMP_PATH),
        save_path=Path(settings.SAVE_PATH),
    )


def build_output_name(info: Mapping[str, object], now: datetime.datetime) -> str:
    replacements: dict[str, str | int | None] = {
        "\\": "＼",
        "/": "／",
        '"': "”",
        "'": "’",
        ":": "：",
        "<": "＜",
        ">": "＞",
        "|": "｜",
        "?": "？",
    }
    return f"{now.strftime('%Y-%m-%d-%H%M')}_{info['id']}".translate(str.maketrans(replacements))


class DownloadEngine:
    def __init__(
        self,
        dependencies: DownloadDependencies,
        policy: DownloadPolicy,
        artifact_store: ArtifactStore | None = None,
    ) -> None:
        self.dependencies = dependencies
        self.policy = policy
        self.artifact_store = artifact_store or ArtifactStore.from_dependencies(dependencies)

    def get_info(self, url: str) -> DownloadInfo:
        with self.dependencies.ydl_factory() as ydl:
            return ydl.extract_info(url, download=False)

    def check_availability(self, url: str, info_loader: InfoLoader | None = None) -> str:
        info_loader = info_loader or self.get_info
        try:
            info = info_loader(url)
        except yt_dlp.utils.DownloadError as error:
            if self.policy.scheduled_notice:
                notice = youtube_scheduled_notice(error)
                if notice is not None:
                    return notice
            raise
        return f"Video title : {info['title']}\nDownload start..."

    def download_video(
        self,
        url: str,
        info_loader: InfoLoader | None = None,
        cancellation_token: Cancellation | None = None,
    ) -> DownloadOutcome:
        self._raise_if_cancelled(cancellation_token)
        info_loader = info_loader or self.get_info
        info = self._load_download_info(
            url,
            info_loader,
            cancellation_token,
        )
        self._raise_if_cancelled(cancellation_token)
        title = build_output_name(info, self.dependencies.now())
        tmp_path = self.dependencies.tmp_path
        self.dependencies.ensure_directory(tmp_path)
        outpath = tmp_path / f"{title}.%(ext)s"

        with self.dependencies.ydl_factory(
            self.build_options(
                str(outpath),
                cancellation_token=cancellation_token,
            ),
        ) as ydl:
            self._raise_if_cancelled(cancellation_token)
            downloaded_info = ydl.extract_info(url, download=True)
            self._raise_if_cancelled(cancellation_token)
            artifacts = discover_download_artifacts(
                info=downloaded_info,
                ydl=ydl,
                output_stem=tmp_path / title,
                path_exists=self.dependencies.path_exists,
                require_metadata=self.policy.require_metadata,
                require_thumbnail=self.policy.require_thumbnail,
            )

        self._raise_if_cancelled(cancellation_token)
        stored_artifacts = self.artifact_store.store(
            artifacts,
            cancellation_check=(
                cancellation_token.raise_if_cancelled if cancellation_token is not None else None
            ),
        )
        return DownloadOutcome(
            video_id=str(downloaded_info.get("id") or ""),
            title=str(
                downloaded_info.get("fulltitle")
                or downloaded_info.get("title")
                or downloaded_info.get("id")
                or url
            ),
            source_url=str(
                downloaded_info.get("webpage_url") or downloaded_info.get("original_url") or url
            ),
            artifacts=stored_artifacts,
        )

    def _load_download_info(
        self,
        url: str,
        info_loader: InfoLoader,
        cancellation_token: Cancellation | None,
    ) -> DownloadInfo:
        retry_policy = self.policy.retry_policy
        if retry_policy is None:
            self._raise_if_cancelled(cancellation_token)
            return info_loader(url)

        attempts = 0
        waited_seconds = 0.0
        while True:
            self._raise_if_cancelled(cancellation_token)
            attempts += 1
            try:
                return info_loader(url)
            except (
                yt_dlp.utils.DownloadError,
                yt_dlp.utils.ExtractorError,
                KeyError,
            ) as error:
                decision = retry_policy.decide(error)
                if decision.status is RetryStatus.PERMANENT_FAILURE:
                    raise PermanentDownloadError(
                        "Download failure is not retryable",
                        original_error=error,
                        attempts=attempts,
                        waited_seconds=waited_seconds,
                    ) from error

                wait_seconds = decision.wait_seconds
                if (
                    attempts >= retry_policy.max_attempts
                    or waited_seconds + wait_seconds > retry_policy.max_wait_seconds
                ):
                    raise DownloadRetryLimitExceeded(
                        "Download retry limit exceeded",
                        original_error=error,
                        attempts=attempts,
                        waited_seconds=waited_seconds,
                    ) from error
                if cancellation_token is not None:
                    cancellation_token.wait(wait_seconds)
                else:
                    self.dependencies.sleep(wait_seconds)
                waited_seconds += wait_seconds

    @staticmethod
    def _raise_if_cancelled(cancellation_token: Cancellation | None) -> None:
        if cancellation_token is not None:
            cancellation_token.raise_if_cancelled()

    def build_options(
        self,
        outpath: str,
        cancellation_token: Cancellation | None = None,
    ) -> dict[str, object]:
        options: dict[str, object] = {
            "outtmpl": outpath,
            "format": "bestvideo+bestaudio/best",
            "merge_output_format": "mkv",
            "noplaylist": True,
            "nooverwrites": True,
            "keepvideo": False,
            "hls_use_mpegts": True,
            "writeinfojson": True,
            "embed_metadata": True,
            "writethumbnail": True,
            "embedthumbnail": True,
            "socket_timeout": 300,
            "fragment_retries": 300,
            "postprocessor_args": {
                "videoconvertor": ["-c:v", "copy"],
            },
            "postprocessors": [
                {
                    "key": "FFmpegVideoConvertor",
                    "preferedformat": "mp4",
                },
                {
                    "key": "FFmpegMetadata",
                    "add_metadata": True,
                },
                {
                    "key": "EmbedThumbnail",
                    "already_have_thumbnail": True,
                },
            ],
        }
        if self.policy.live_from_start:
            options["live_from_start"] = True
        if self.policy.use_cookie_file and self.dependencies.path_exists(self.policy.cookie_path):
            options["cookiefile"] = str(self.policy.cookie_path)
        if cancellation_token is not None:
            options["progress_hooks"] = [
                lambda _: cancellation_token.raise_if_cancelled(),
            ]
        return options
