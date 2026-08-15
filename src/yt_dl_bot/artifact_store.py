"""Transactional storage for completed download artifacts."""

import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .artifact_discovery import DownloadedArtifacts
from .download_primitives import DownloadDependencies


@dataclass(frozen=True)
class ArtifactMove:
    """One source-to-destination entry in an artifact storage operation."""

    source: Path
    destination_directory: Path
    destination: Path


class ArtifactStore:
    """Move downloaded artifacts into their durable directory layout."""

    def __init__(
        self,
        *,
        save_path: Path,
        path_exists: Callable[[Path], bool],
        ensure_directory: Callable[[Path], None],
        move: Callable[[Path, Path], object],
    ) -> None:
        self.save_path = Path(save_path)
        self.path_exists = path_exists
        self.ensure_directory = ensure_directory
        self.move = move

    @classmethod
    def from_dependencies(cls, dependencies: DownloadDependencies) -> "ArtifactStore":
        return cls(
            save_path=dependencies.save_path,
            path_exists=dependencies.path_exists,
            ensure_directory=dependencies.ensure_directory,
            move=dependencies.move,
        )

    def store(
        self,
        artifacts: DownloadedArtifacts,
        *,
        cancellation_check: Callable[[], None] | None = None,
    ) -> DownloadedArtifacts:
        """Store all artifacts, rolling completed moves back on failure.

        ``cancellation_check`` implements cooperative cancellation. It is called
        before creating durable destinations and before every forward move. A
        cancellation observed after a move has completed can therefore stop the
        next move, but cannot interrupt the filesystem operation already in
        progress. Rollback moves are still allowed so partially stored artifacts
        are restored to their temporary locations.
        """
        move_plan = self.plan(artifacts)
        self._check_cancellation(cancellation_check)
        self._ensure_destinations()
        self._reject_collisions(move_plan)
        self._execute(move_plan, cancellation_check)
        return DownloadedArtifacts(
            video=move_plan[0].destination,
            metadata=tuple(move.destination for move in move_plan[1 : 1 + len(artifacts.metadata)]),
            thumbnails=tuple(move.destination for move in move_plan[1 + len(artifacts.metadata) :]),
        )

    def plan(self, artifacts: DownloadedArtifacts) -> tuple[ArtifactMove, ...]:
        """Describe the stable destination layout without changing the filesystem."""
        metadata_path = self.save_path / "metadata"
        thumbnail_path = self.save_path / "thumbnail"
        return (
            ArtifactMove(
                artifacts.video,
                self.save_path,
                self.save_path / artifacts.video.name,
            ),
            *(
                ArtifactMove(metadata, metadata_path, metadata_path / metadata.name)
                for metadata in artifacts.metadata
            ),
            *(
                ArtifactMove(thumbnail, thumbnail_path, thumbnail_path / thumbnail.name)
                for thumbnail in artifacts.thumbnails
            ),
        )

    def _ensure_destinations(self) -> None:
        for destination in (
            self.save_path,
            self.save_path / "metadata",
            self.save_path / "thumbnail",
        ):
            self.ensure_directory(destination)

    def _reject_collisions(self, move_plan: tuple[ArtifactMove, ...]) -> None:
        for move in move_plan:
            if self.path_exists(move.destination):
                raise shutil.Error(
                    f"Destination path already exists: {move.destination}",
                )

    def _execute(
        self,
        move_plan: tuple[ArtifactMove, ...],
        cancellation_check: Callable[[], None] | None,
    ) -> None:
        completed_moves: list[ArtifactMove] = []
        try:
            for planned_move in move_plan:
                self._check_cancellation(cancellation_check)
                self.move(planned_move.source, planned_move.destination_directory)
                completed_moves.append(planned_move)
        except Exception as move_error:
            rollback_errors: list[Exception] = []
            for planned_move in reversed(completed_moves):
                try:
                    self.move(planned_move.destination, planned_move.source)
                except Exception as rollback_error:
                    rollback_errors.append(rollback_error)
            if rollback_errors:
                move_error.add_note(
                    "Failed to roll back one or more artifact moves: "
                    + "; ".join(str(error) for error in rollback_errors),
                )
            raise

    @staticmethod
    def _check_cancellation(cancellation_check: Callable[[], None] | None) -> None:
        if cancellation_check is not None:
            cancellation_check()
