"""Discover files produced by yt-dlp without assuming fixed extensions."""

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from .download_primitives import YouTubeDL

VIDEO_EXTENSIONS = frozenset(
    {
        ".avi",
        ".flv",
        ".m4v",
        ".mkv",
        ".mov",
        ".mp4",
        ".ts",
        ".webm",
    }
)
THUMBNAIL_EXTENSIONS = frozenset(
    {
        ".avif",
        ".gif",
        ".jpeg",
        ".jpg",
        ".png",
        ".webp",
    }
)


class ArtifactDiscoveryError(Exception):
    def __init__(
        self,
        missing: Iterable[str],
        candidates: Iterable[Path],
    ) -> None:
        self.missing = tuple(missing)
        self.candidates = tuple(candidates)
        super().__init__(
            "Missing download artifacts: " + ", ".join(self.missing),
        )


@dataclass(frozen=True)
class DownloadedArtifacts:
    video: Path
    metadata: tuple[Path, ...]
    thumbnails: tuple[Path, ...]


def _path(value: object) -> Path | None:
    if not isinstance(value, (str, Path)) or not str(value):
        return None
    if "://" in str(value):
        return None
    return Path(value)


def _append_path(target: list[Path], value: object) -> None:
    path = _path(value)
    if path is not None and path not in target:
        target.append(path)


def _classify(
    paths: Iterable[Path],
) -> tuple[list[Path], list[Path], list[Path]]:
    videos: list[Path] = []
    metadata: list[Path] = []
    thumbnails: list[Path] = []
    for path in paths:
        if path.name.endswith(".info.json"):
            _append_path(metadata, path)
        elif path.suffix.lower() in THUMBNAIL_EXTENSIONS:
            _append_path(thumbnails, path)
        elif path.suffix.lower() in VIDEO_EXTENSIONS:
            _append_path(videos, path)
    return videos, metadata, thumbnails


def discover_download_artifacts(
    *,
    info: dict[str, object],
    ydl: YouTubeDL,
    output_stem: str | Path,
    path_exists: Callable[[Path], bool],
    require_metadata: bool,
    require_thumbnail: bool,
) -> DownloadedArtifacts:
    """Resolve final artifacts from yt-dlp's returned path information."""
    output_stem = Path(output_stem)
    primary: list[Path] = []
    secondary: list[Path] = []
    sidecars: list[Path] = []

    _append_path(primary, info.get("filepath"))

    files_to_move = info.get("__files_to_move") or {}
    if isinstance(files_to_move, dict):
        for destination in files_to_move.values():
            _append_path(primary, destination)

    _append_path(primary, ydl.prepare_filename(info))

    requested_downloads = info.get("requested_downloads")
    if isinstance(requested_downloads, (list, tuple)):
        for requested in requested_downloads:
            if isinstance(requested, dict):
                _append_path(secondary, requested.get("filepath"))
                _append_path(secondary, requested.get("_filename"))

    _append_path(secondary, info.get("_filename"))
    if isinstance(files_to_move, dict):
        for source in files_to_move:
            _append_path(secondary, source)

    _append_path(sidecars, info.get("infojson_filename"))
    _append_path(sidecars, info.get("thumbnail_filename"))
    thumbnail_entries = info.get("thumbnails")
    if isinstance(thumbnail_entries, (list, tuple)):
        for thumbnail in thumbnail_entries:
            if not isinstance(thumbnail, dict):
                continue
            _append_path(sidecars, thumbnail.get("filepath"))
            _append_path(sidecars, thumbnail.get("filename"))
            extension = thumbnail.get("ext")
            if isinstance(extension, str) and extension:
                _append_path(
                    sidecars,
                    output_stem.with_suffix("." + extension.lstrip(".")),
                )

    all_candidates = primary + secondary + sidecars
    videos, metadata, thumbnails = _classify(all_candidates)

    for video in videos:
        _append_path(metadata, video.with_suffix(".info.json"))
    _append_path(metadata, output_stem.with_suffix(".info.json"))

    existing_videos = tuple(path for path in videos if path_exists(path))
    existing_metadata = tuple(path for path in metadata if path_exists(path))
    existing_thumbnails = tuple(path for path in thumbnails if path_exists(path))

    missing = []
    if not existing_videos:
        missing.append("video")
    if require_metadata and not existing_metadata:
        missing.append("metadata")
    if require_thumbnail and not existing_thumbnails:
        missing.append("thumbnail")
    if missing:
        raise ArtifactDiscoveryError(
            missing,
            videos + metadata + thumbnails,
        )

    return DownloadedArtifacts(
        video=existing_videos[0],
        metadata=existing_metadata,
        thumbnails=existing_thumbnails,
    )
