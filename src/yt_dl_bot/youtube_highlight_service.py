"""Application service for creating and archiving YouTube highlights."""

import shutil
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Protocol, cast

import yt_dlp
from pytchat import exceptions as pytchat_exceptions

from .application_errors import ArtifactStorageError, HighlightCreationError
from .application_results import HighlightResult
from .chat_highlights import ChatHighlightPipeline
from .download_primitives import MakeDirectory
from .external_error_adapter import error_detail
from .highlight import Highlight

YOUTUBE_METADATA_ERRORS = (
    yt_dlp.utils.DownloadError,
    yt_dlp.utils.ExtractorError,
    ValueError,
)

CHAT_PROCESSING_ERRORS = (
    pytchat_exceptions.ChatParseException,
    pytchat_exceptions.ResponseContextError,
    pytchat_exceptions.NoContents,
    pytchat_exceptions.NoContinuation,
    pytchat_exceptions.IllegalFunctionCall,
    pytchat_exceptions.InvalidVideoIdException,
    pytchat_exceptions.UnknownConnectionError,
    pytchat_exceptions.RetryExceedMaxCount,
    pytchat_exceptions.ChatDataFinished,
    pytchat_exceptions.ReceivedUnknownContinuation,
    pytchat_exceptions.FailedExtractContinuation,
    pytchat_exceptions.VideoInfoParseError,
    pytchat_exceptions.PatternUnmatchError,
    OSError,
)


class HighlightChat(Protocol):
    image_path: Path

    def get_highlight(self) -> Sequence[Highlight]: ...


class HighlightYouTubeAdapter(Protocol):
    def get_video_id(self, url: str) -> str: ...

    def get_info(self, url: str) -> Mapping[str, object]: ...


class HighlightSettings(Protocol):
    GRAPH_SAVE_PATH: Path
    TMP_PATH: Path


class MoveFile(Protocol):
    def __call__(self, source: Path, destination: Path) -> object: ...


class YouTubeHighlightService:
    def __init__(
        self,
        settings: HighlightSettings,
        youtube: HighlightYouTubeAdapter,
        chat_factory: Callable[[str], HighlightChat] | None = None,
        path_exists: Callable[[Path], bool] = Path.exists,
        make_directory: MakeDirectory | None = None,
        move: MoveFile | None = None,
    ) -> None:
        self.settings = settings
        self.youtube = youtube
        self.chat_factory = chat_factory or (
            lambda video_id: ChatHighlightPipeline(video_id, settings=settings)
        )
        self.path_exists = path_exists
        self.make_directory = make_directory or (
            lambda path, *, parents=False, exist_ok=False: path.mkdir(
                parents=parents,
                exist_ok=exist_ok,
            )
        )
        self.move = move or shutil.move

    def create(self, url: str) -> HighlightResult:
        try:
            video_id = self.youtube.get_video_id(url=url)
            video_info = self.youtube.get_info(url=url)
        except YOUTUBE_METADATA_ERRORS as error:
            raise HighlightCreationError(
                f"Unable to create highlights: {error_detail(error)}",
                original_error=error,
            ) from error

        chat = self.chat_factory(video_id)
        try:
            highlights = chat.get_highlight()
        except CHAT_PROCESSING_ERRORS as error:
            raise HighlightCreationError(
                f"Unable to create highlights: {error_detail(error)}",
                original_error=error,
            ) from error

        try:
            if not isinstance(video_info, Mapping):
                raise TypeError("video metadata must be a mapping")
            title = video_info.get("fulltitle") or video_info["title"]
            channel_name = video_info["channel"]
            thumbnail_url = video_info["thumbnail"]
            for field_name, value in (
                ("title", title),
                ("channel", channel_name),
                ("thumbnail", thumbnail_url),
            ):
                if not isinstance(value, str) or not value:
                    raise ValueError(
                        f"video metadata field {field_name!r} must be a non-empty string",
                    )
        except (KeyError, TypeError, ValueError) as error:
            raise HighlightCreationError(
                f"Unable to create highlights: {error_detail(error)}",
                original_error=error,
            ) from error
        title = cast(str, title)
        channel_name = cast(str, channel_name)
        thumbnail_url = cast(str, thumbnail_url)
        return HighlightResult(
            title=title,
            channel_name=channel_name,
            thumbnail_url=thumbnail_url,
            graph_image=Path(chat.image_path),
            highlights=tuple(highlights),
        )

    def archive_graph(self, graph_image: Path) -> None:
        try:
            output_path = Path(self.settings.GRAPH_SAVE_PATH)
            if not self.path_exists(output_path):
                self.make_directory(
                    output_path,
                    parents=True,
                    exist_ok=True,
                )
            self.move(Path(graph_image), output_path)
        except (OSError, shutil.Error) as error:
            raise ArtifactStorageError(
                f"Unable to archive highlight graph: {error}",
                original_error=error,
            ) from error
