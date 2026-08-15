import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, call

from yt_dl_bot.artifact_discovery import DownloadedArtifacts
from yt_dl_bot.artifact_store import ArtifactStore
from yt_dl_bot.cancellation import CancellationToken, DownloadCancelled


def artifacts(root: Path) -> DownloadedArtifacts:
    return DownloadedArtifacts(
        video=root / "video.mp4",
        metadata=(root / "video.info.json",),
        thumbnails=(root / "video.webp",),
    )


class ArtifactStoreTest(unittest.TestCase):
    def test_cancellation_before_storage_prevents_all_durable_side_effects(self):
        ensure_directory = Mock()
        move = Mock()
        store = ArtifactStore(
            save_path=Path("/archive"),
            path_exists=Mock(return_value=False),
            ensure_directory=ensure_directory,
            move=move,
        )
        token = CancellationToken()
        token.cancel()

        with self.assertRaises(DownloadCancelled):
            store.store(
                artifacts(Path("/tmp/downloads")),
                cancellation_check=token.raise_if_cancelled,
            )

        ensure_directory.assert_not_called()
        move.assert_not_called()

    def test_cancellation_between_moves_stops_forward_progress_and_rolls_back(self):
        token = CancellationToken()
        move = Mock(side_effect=lambda *_: token.cancel())
        store = ArtifactStore(
            save_path=Path("/archive"),
            path_exists=Mock(return_value=False),
            ensure_directory=Mock(),
            move=move,
        )
        downloaded = artifacts(Path("/tmp/downloads"))

        with self.assertRaises(DownloadCancelled):
            store.store(
                downloaded,
                cancellation_check=token.raise_if_cancelled,
            )

        self.assertEqual(
            move.call_args_list,
            [
                call(downloaded.video, Path("/archive")),
                call(Path("/archive/video.mp4"), downloaded.video),
            ],
        )

    def test_plan_uses_the_existing_directory_layout(self):
        store = ArtifactStore(
            save_path=Path("/archive"),
            path_exists=Mock(return_value=False),
            ensure_directory=Mock(),
            move=Mock(),
        )

        move_plan = store.plan(artifacts(Path("/tmp/downloads")))

        self.assertEqual(
            tuple(move.destination for move in move_plan.moves),
            (
                Path("/archive/video.mp4"),
                Path("/archive/metadata/video.info.json"),
                Path("/archive/thumbnail/video.webp"),
            ),
        )
        self.assertEqual(
            move_plan.destinations,
            DownloadedArtifacts(
                video=Path("/archive/video.mp4"),
                metadata=(Path("/archive/metadata/video.info.json"),),
                thumbnails=(Path("/archive/thumbnail/video.webp"),),
            ),
        )

    def test_store_moves_artifacts_and_returns_destination_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_path = root / "tmp"
            source_path.mkdir()
            downloaded = artifacts(source_path)
            for artifact in (
                downloaded.video,
                *downloaded.metadata,
                *downloaded.thumbnails,
            ):
                artifact.write_text(artifact.name)
            store = ArtifactStore(
                save_path=root / "archive",
                path_exists=Path.exists,
                ensure_directory=lambda path: path.mkdir(parents=True, exist_ok=True),
                move=shutil.move,
            )

            stored = store.store(downloaded)

            self.assertEqual(stored.video, root / "archive/video.mp4")
            self.assertEqual(
                stored.metadata,
                (root / "archive/metadata/video.info.json",),
            )
            self.assertEqual(
                stored.thumbnails,
                (root / "archive/thumbnail/video.webp",),
            )
            self.assertTrue(stored.video.exists())
            self.assertTrue(all(path.exists() for path in stored.metadata))
            self.assertTrue(all(path.exists() for path in stored.thumbnails))

    def test_collision_is_rejected_before_any_move(self):
        move = Mock()
        store = ArtifactStore(
            save_path=Path("/archive"),
            path_exists=lambda path: path == Path("/archive/metadata/video.info.json"),
            ensure_directory=Mock(),
            move=move,
        )

        with self.assertRaisesRegex(
            shutil.Error,
            "Destination path already exists",
        ):
            store.store(artifacts(Path("/tmp/downloads")))

        move.assert_not_called()

    def test_store_preserves_all_destination_directories_without_sidecars(self):
        ensure_directory = Mock()
        store = ArtifactStore(
            save_path=Path("/archive"),
            path_exists=Mock(return_value=False),
            ensure_directory=ensure_directory,
            move=Mock(),
        )

        store.store(
            DownloadedArtifacts(
                video=Path("/tmp/downloads/video.mp4"),
                metadata=(),
                thumbnails=(),
            )
        )

        self.assertEqual(
            ensure_directory.call_args_list,
            [
                call(Path("/archive")),
                call(Path("/archive/metadata")),
                call(Path("/archive/thumbnail")),
            ],
        )

    def test_partial_failure_rolls_back_and_preserves_original_error(self):
        move_error = OSError("injected metadata move failure")
        rollback_error = OSError("injected video rollback failure")
        move = Mock(side_effect=[None, move_error, rollback_error])
        store = ArtifactStore(
            save_path=Path("/archive"),
            path_exists=Mock(return_value=False),
            ensure_directory=Mock(),
            move=move,
        )
        downloaded = artifacts(Path("/tmp/downloads"))

        with self.assertRaises(OSError) as raised:
            store.store(downloaded)

        self.assertIs(raised.exception, move_error)
        self.assertEqual(
            raised.exception.__notes__,
            ["Failed to roll back one or more artifact moves: injected video rollback failure"],
        )
        self.assertEqual(
            move.call_args_list,
            [
                call(downloaded.video, Path("/archive")),
                call(downloaded.metadata[0], Path("/archive/metadata")),
                call(Path("/archive/video.mp4"), downloaded.video),
            ],
        )


if __name__ == "__main__":
    unittest.main()
