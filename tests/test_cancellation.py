import asyncio
import concurrent.futures
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock

import yt_dlp

from yt_dl_bot.cancellation import (
    CancellationToken,
    DownloadCancelled,
    to_thread_cancellable,
)
from yt_dl_bot.download_engine import (
    DownloadEngine,
    generic_download_policy,
    youtube_download_policy,
)
from yt_dl_bot.download_primitives import DownloadDependencies, RetryPolicy
from yt_dl_bot.video_download_service import VideoDownloadService


def dependencies():
    return DownloadDependencies(
        ydl_factory=Mock(),
        now=Mock(),
        sleep=Mock(),
        path_exists=Mock(return_value=False),
        make_directory=Mock(),
        move=Mock(),
        tmp_path=Path("/tmp/downloads"),
        save_path=Path("/archive"),
    )


class CancellationTokenTest(unittest.TestCase):
    def test_cancelled_before_download_stops_before_external_io(self):
        deps = dependencies()
        engine = DownloadEngine(deps, generic_download_policy())
        token = CancellationToken()
        token.cancel()

        with self.assertRaises(DownloadCancelled):
            engine.download_video(
                "https://example.test/video",
                cancellation_token=token,
            )

        deps.ydl_factory.assert_not_called()
        deps.now.assert_not_called()

    def test_progress_hook_stops_active_yt_dlp_download(self):
        engine = DownloadEngine(dependencies(), generic_download_policy())
        token = CancellationToken()
        progress_hook = engine.build_options(
            "/tmp/video.%(ext)s",
            cancellation_token=token,
        )["progress_hooks"][0]

        progress_hook({"status": "downloading"})
        token.cancel()

        with self.assertRaises(DownloadCancelled):
            progress_hook({"status": "downloading"})

    def test_normal_options_do_not_install_cancellation_hook(self):
        engine = DownloadEngine(dependencies(), generic_download_policy())

        options = engine.build_options("/tmp/video.%(ext)s")

        self.assertNotIn("progress_hooks", options)

    def test_cancel_interrupts_scheduled_retry_wait(self):
        waiting = threading.Event()

        class ObservableToken(CancellationToken):
            def wait(self, timeout):
                waiting.set()
                return super().wait(timeout)

        token = ObservableToken()
        engine = DownloadEngine(
            dependencies(),
            youtube_download_policy(
                RetryPolicy(max_attempts=3, max_wait_seconds=600),
            ),
        )
        info_loader = Mock(
            side_effect=yt_dlp.utils.DownloadError(
                "This live event will begin in 5 minutes.",
            )
        )

        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(
                engine.download_video,
                "https://youtu.be/video",
                info_loader,
                token,
            )
            self.assertTrue(waiting.wait(2))
            token.cancel()

            with self.assertRaises(DownloadCancelled):
                future.result(timeout=2)

        engine.dependencies.sleep.assert_not_called()
        info_loader.assert_called_once_with("https://youtu.be/video")
        engine.dependencies.ydl_factory.assert_not_called()
        engine.dependencies.now.assert_not_called()

    def test_application_service_does_not_translate_cancellation(self):
        downloader = Mock()
        cancellation = DownloadCancelled("cancelled")
        downloader.download_video_cancellable.side_effect = cancellation

        with self.assertRaises(DownloadCancelled) as raised:
            VideoDownloadService(downloader).download(
                "https://example.test/video",
                cancellation_token=CancellationToken(),
            )

        self.assertIs(raised.exception, cancellation)


class ThreadCancellationTest(unittest.IsolatedAsyncioTestCase):
    async def test_worker_cancelled_before_external_io_never_starts_it(self):
        worker_started = threading.Event()
        allow_boundary_check = threading.Event()
        worker_cancelled = threading.Event()
        external_io = Mock()

        def blocking_work(*, cancellation_token):
            worker_started.set()
            allow_boundary_check.wait()
            try:
                cancellation_token.raise_if_cancelled()
                external_io()
            except DownloadCancelled:
                worker_cancelled.set()
                raise

        task = asyncio.create_task(to_thread_cancellable(blocking_work))
        self.assertTrue(await asyncio.to_thread(worker_started.wait, 2))

        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        allow_boundary_check.set()

        self.assertTrue(await asyncio.to_thread(worker_cancelled.wait, 2))
        external_io.assert_not_called()

    async def test_async_cancellation_is_signalled_to_running_worker(self):
        worker_started = threading.Event()
        worker_cancelled = threading.Event()

        def blocking_work(*, cancellation_token):
            worker_started.set()
            try:
                cancellation_token.wait(30)
            except DownloadCancelled:
                worker_cancelled.set()
                raise

        task = asyncio.create_task(
            to_thread_cancellable(blocking_work),
        )
        await asyncio.to_thread(worker_started.wait, 2)

        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

        observed = await asyncio.to_thread(worker_cancelled.wait, 2)
        self.assertTrue(observed)

    async def test_active_progress_hook_observes_async_cancellation(self):
        engine = DownloadEngine(dependencies(), generic_download_policy())
        worker_started = threading.Event()
        emit_progress = threading.Event()
        worker_cancelled = threading.Event()

        def blocking_work(*, cancellation_token):
            progress_hook = engine.build_options(
                "/tmp/video.%(ext)s",
                cancellation_token=cancellation_token,
            )["progress_hooks"][0]
            worker_started.set()
            emit_progress.wait()
            try:
                progress_hook({"status": "downloading"})
            except DownloadCancelled:
                worker_cancelled.set()
                raise

        task = asyncio.create_task(to_thread_cancellable(blocking_work))
        self.assertTrue(await asyncio.to_thread(worker_started.wait, 2))

        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        emit_progress.set()

        self.assertTrue(await asyncio.to_thread(worker_cancelled.wait, 2))

    async def test_normal_completion_returns_worker_result(self):
        def blocking_work(value, *, cancellation_token):
            cancellation_token.raise_if_cancelled()
            return value * 2

        result = await to_thread_cancellable(blocking_work, 21)

        self.assertEqual(result, 42)


if __name__ == "__main__":
    unittest.main()
