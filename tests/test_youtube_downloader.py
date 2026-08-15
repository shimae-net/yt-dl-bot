import unittest
from pathlib import Path
from unittest.mock import Mock, call

import yt_dlp
from download_adapter_test_support import DownloadAdapterTestCase

from yt_dl_bot.artifact_discovery import DownloadedArtifacts
from yt_dl_bot.download_engine import DownloadOutcome
from yt_dl_bot.download_primitives import (
    DownloadDependencies,
    DownloadRetryLimitExceeded,
    PermanentDownloadError,
    RetryPolicy,
)
from yt_dl_bot.youtube_downloader import YouTubeDownloader


class YouTubeDownloaderBoundaryTest(DownloadAdapterTestCase, unittest.TestCase):
    downloader_type = YouTubeDownloader

    def test_download_uses_injected_clock_paths_and_artifact_mover(self):
        result = self.downloader.download_video("https://youtu.be/video")

        self.assertEqual(
            result,
            DownloadOutcome(
                video_id="video:id",
                title="Example video",
                source_url="https://youtu.be/video",
                artifacts=DownloadedArtifacts(
                    video=Path(
                        "/archive/2026-07-28-0905_video：id.mp4",
                    ),
                    metadata=(
                        Path(
                            "/archive/metadata/2026-07-28-0905_video：id.info.json",
                        ),
                    ),
                    thumbnails=(
                        Path(
                            "/archive/thumbnail/2026-07-28-0905_video：id.webp",
                        ),
                    ),
                ),
            ),
        )
        download = self.ydl_instances[-1]
        self.assertEqual(
            download.options["outtmpl"],
            "/tmp/downloads/2026-07-28-0905_video：id.%(ext)s",
        )
        self.assertEqual(
            download.extract_calls,
            [("https://youtu.be/video", True)],
        )
        self.assertEqual(
            self.move.call_args_list,
            [
                call(
                    Path("/tmp/downloads/2026-07-28-0905_video：id.mp4"),
                    Path("/archive"),
                ),
                call(
                    Path("/tmp/downloads/2026-07-28-0905_video：id.info.json"),
                    Path("/archive/metadata"),
                ),
                call(
                    Path("/tmp/downloads/2026-07-28-0905_video：id.webp"),
                    Path("/archive/thumbnail"),
                ),
            ],
        )
        self.mkdir.assert_has_calls(
            [
                call(Path("/tmp/downloads"), parents=True, exist_ok=True),
                call(Path("/archive"), parents=True, exist_ok=True),
                call(Path("/archive/metadata"), parents=True, exist_ok=True),
                call(Path("/archive/thumbnail"), parents=True, exist_ok=True),
            ]
        )

    def test_download_result_prefers_canonical_source_metadata(self):
        self.download_info.update(
            {
                "fulltitle": "Canonical full title",
                "webpage_url": "https://www.youtube.com/watch?v=video-id",
            }
        )

        result = self.downloader.download_video("https://youtu.be/video-id")

        self.assertEqual(result.title, "Canonical full title")
        self.assertEqual(
            result.source_url,
            "https://www.youtube.com/watch?v=video-id",
        )

    def test_download_result_falls_back_when_metadata_is_missing_or_empty(self):
        requested_url = "https://youtu.be/requested-video"
        artifact_info = {
            key: value for key, value in self.download_info.items() if key not in {"id", "title"}
        }
        cases = (
            (
                "title and original URL",
                {
                    "id": "video-id",
                    "fulltitle": "",
                    "title": "Short title",
                    "webpage_url": "",
                    "original_url": "https://youtube.com/watch?v=original",
                },
                ("video-id", "Short title", "https://youtube.com/watch?v=original"),
            ),
            (
                "ID and requested URL",
                {
                    "id": "video-id",
                    "fulltitle": None,
                    "title": "",
                    "webpage_url": None,
                    "original_url": "",
                },
                ("video-id", "video-id", requested_url),
            ),
            (
                "requested URL when all identifying metadata is absent",
                {},
                ("", requested_url, requested_url),
            ),
            (
                "requested URL and empty video ID when metadata is empty",
                {
                    "id": "",
                    "fulltitle": "",
                    "title": "",
                    "webpage_url": "",
                    "original_url": "",
                },
                ("", requested_url, requested_url),
            ),
        )

        for label, metadata, expected in cases:
            with self.subTest(label):
                self.downloaded_info = artifact_info | metadata

                result = self.downloader.download_video(requested_url)

                self.assertEqual(
                    (result.video_id, result.title, result.source_url),
                    expected,
                )

    def test_trailing_slash_does_not_change_configured_paths(self):
        with_slashes = DownloadDependencies(
            ydl_factory=self.dependencies.ydl_factory,
            now=self.dependencies.now,
            sleep=self.sleep,
            path_exists=self.dependencies.path_exists,
            make_directory=self.mkdir,
            move=self.move,
            tmp_path="/tmp/downloads/",
            save_path="/archive/",
        )

        self.assertEqual(
            with_slashes.tmp_path,
            self.dependencies.tmp_path,
        )
        self.assertEqual(
            with_slashes.save_path,
            self.dependencies.save_path,
        )

    def test_download_options_are_fresh_for_each_call(self):
        first = self.downloader.engine.build_options("/tmp/first.%(ext)s")
        first["postprocessors"].append({"key": "test-only"})

        second = self.downloader.engine.build_options("/tmp/second.%(ext)s")

        self.assertNotIn(
            {"key": "test-only"},
            second["postprocessors"],
        )

    def test_download_retries_with_injected_sleep(self):
        error = yt_dlp.utils.DownloadError(
            "This live event will begin in 1 minutes.",
        )
        self.downloader.get_info = Mock(
            side_effect=[error, self.download_info],
        )

        self.downloader.download_video("https://youtu.be/video")

        self.sleep.assert_called_once_with(30.0)
        self.assertEqual(self.downloader.get_info.call_count, 2)

    def test_permanent_download_error_fails_without_sleeping(self):
        error = yt_dlp.utils.DownloadError("Video unavailable")
        self.downloader.get_info = Mock(side_effect=error)

        with self.assertRaises(PermanentDownloadError) as raised:
            self.downloader.download_video("https://youtu.be/video")

        self.assertIs(raised.exception.original_error, error)
        self.assertEqual(raised.exception.attempts, 1)
        self.sleep.assert_not_called()

    def test_retry_attempt_limit_is_enforced(self):
        error = yt_dlp.utils.DownloadError(
            "This live event will begin in 1 minutes.",
        )
        self.downloader = YouTubeDownloader(
            self.dependencies,
            retry_policy=RetryPolicy(
                max_attempts=2,
                max_wait_seconds=3600,
            ),
        )
        self.downloader.get_info = Mock(side_effect=error)

        with self.assertRaises(DownloadRetryLimitExceeded) as raised:
            self.downloader.download_video("https://youtu.be/video")

        self.assertEqual(raised.exception.attempts, 2)
        self.assertEqual(raised.exception.waited_seconds, 30.0)
        self.sleep.assert_called_once_with(30.0)

    def test_total_wait_limit_is_enforced_before_sleep(self):
        error = yt_dlp.utils.DownloadError(
            "This live event will begin in 2 hours.",
        )
        self.downloader = YouTubeDownloader(
            self.dependencies,
            retry_policy=RetryPolicy(
                max_attempts=10,
                max_wait_seconds=3600,
            ),
        )
        self.downloader.get_info = Mock(side_effect=error)

        with self.assertRaises(DownloadRetryLimitExceeded) as raised:
            self.downloader.download_video("https://youtu.be/video")

        self.assertEqual(raised.exception.attempts, 1)
        self.assertEqual(raised.exception.waited_seconds, 0)
        self.sleep.assert_not_called()

    def test_retry_can_use_the_entire_wait_budget(self):
        error = yt_dlp.utils.DownloadError(
            "This live event will begin in 1 minutes.",
        )
        self.downloader = YouTubeDownloader(
            self.dependencies,
            retry_policy=RetryPolicy(
                max_attempts=2,
                max_wait_seconds=30,
            ),
        )
        self.downloader.get_info = Mock(
            side_effect=[error, self.download_info],
        )

        self.downloader.download_video("https://youtu.be/video")

        self.sleep.assert_called_once_with(30.0)
        self.assertEqual(self.downloader.get_info.call_count, 2)
