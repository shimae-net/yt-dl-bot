import asyncio
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from yt_dl_bot.application_errors import (
    VideoCheckError,
)
from yt_dl_bot.application_results import (
    DownloadResult,
    HighlightResult,
)
from yt_dl_bot.cogs.twitchcog import TwitchCog
from yt_dl_bot.cogs.youtubecog import YouTubeCog
from yt_dl_bot.highlight import Highlight
from yt_dl_bot.video_download_service import (
    TwitchStreamOffline,
)


class CogDelegationTest(unittest.IsolatedAsyncioTestCase):
    def make_bot(self):
        bot = Mock()
        bot.settings = SimpleNamespace()
        bot.services = SimpleNamespace(
            youtube_download=Mock(),
            youtube_highlight=Mock(),
            twitch_download=Mock(),
        )
        bot.logger = Mock()
        bot.get_command.side_effect = lambda name: name

        return bot

    @staticmethod
    def to_thread_mock():
        async def run(function, *args, **kwargs):
            return function(*args, **kwargs)

        return AsyncMock(side_effect=run)

    async def test_youtube_cog_only_coordinates_download_responses(self):
        bot = self.make_bot()
        bot.services.youtube_download.check.return_value = "ready"
        result = DownloadResult(
            video_id="video",
            title="Example video",
            source_url="https://youtu.be/video",
            video_file=Path("/archive/video.mkv"),
            metadata_files=(),
            thumbnail_files=(),
        )
        bot.services.youtube_download.download.return_value = result
        ctx = Mock()
        ctx.reply = AsyncMock()
        ctx.invoke = AsyncMock()
        cog = YouTubeCog(bot)

        to_thread = self.to_thread_mock()
        with patch("asyncio.to_thread", to_thread):
            await YouTubeCog.download_video.callback(
                cog,
                ctx,
                "https://youtu.be/video",
            )

        self.assertEqual(to_thread.await_count, 2)
        self.assertIs(
            to_thread.await_args_list[0].args[0],
            bot.services.youtube_download.check,
        )
        self.assertIs(
            to_thread.await_args_list[1].args[0],
            bot.services.youtube_download.download,
        )
        bot.services.youtube_download.check.assert_called_once_with(
            "https://youtu.be/video",
        )
        bot.services.youtube_download.download.assert_called_once_with(
            "https://youtu.be/video",
            cancellation_token=unittest.mock.ANY,
        )
        ctx.reply.assert_awaited_once_with("ready")
        ctx.invoke.assert_awaited_once_with(
            "send_video_output_log",
            result=result,
        )

    async def test_twitch_cog_maps_offline_result_to_reply(self):
        bot = self.make_bot()
        bot.services.twitch_download.check.side_effect = TwitchStreamOffline
        ctx = Mock()
        ctx.reply = AsyncMock()
        ctx.invoke = AsyncMock()
        cog = TwitchCog(bot)

        to_thread = self.to_thread_mock()
        with patch("asyncio.to_thread", to_thread):
            await TwitchCog.download_video.callback(
                cog,
                ctx,
                "https://www.twitch.tv/channel",
            )

        ctx.reply.assert_awaited_once_with(
            "このチャンネルでライブは始まっていません。",
        )
        bot.services.twitch_download.download.assert_not_called()
        ctx.invoke.assert_not_awaited()

    async def test_twitch_cog_coordinates_successful_download(self):
        bot = self.make_bot()
        url = "https://www.twitch.tv/channel"
        result = DownloadResult(
            video_id="stream",
            title="Example stream",
            source_url=url,
            video_file=Path("/archive/stream.mkv"),
            metadata_files=(),
            thumbnail_files=(),
        )
        ctx = Mock()
        events = []

        def check(check_url):
            events.append(("check", check_url))
            return "ready"

        async def reply(message):
            events.append(("reply", message))

        async def download(function, download_url):
            events.append(("download", function, download_url))
            return result

        def log_success(message):
            events.append(("log", message))

        async def invoke(command, **kwargs):
            events.append(("invoke", command, kwargs))

        ctx.reply = AsyncMock(side_effect=reply)
        ctx.invoke = AsyncMock(side_effect=invoke)
        bot.services.twitch_download.check.side_effect = check
        bot.logger.info.side_effect = log_success
        cog = TwitchCog(bot)

        to_thread = self.to_thread_mock()
        cancellable_download = AsyncMock(side_effect=download)
        with (
            patch("asyncio.to_thread", to_thread),
            patch(
                "yt_dl_bot.cogs.download_orchestration.to_thread_cancellable",
                cancellable_download,
            ),
        ):
            await TwitchCog.download_video.callback(cog, ctx, url)

        bot.services.twitch_download.check.assert_called_once_with(url)
        cancellable_download.assert_awaited_once_with(
            bot.services.twitch_download.download,
            url,
        )
        self.assertEqual(
            events,
            [
                ("check", url),
                ("reply", "ready"),
                ("download", bot.services.twitch_download.download, url),
                ("log", "Download Success!"),
                (
                    "invoke",
                    "send_video_output_log",
                    {"result": result},
                ),
            ],
        )

    async def test_youtube_cog_converts_highlight_result_to_discord_types(self):
        bot = self.make_bot()
        result = HighlightResult(
            title="Title",
            channel_name="Channel",
            thumbnail_url="https://example.test/thumb.jpg",
            graph_image=Path("/tmp/graph.png"),
            highlights=(
                Highlight(1, "field-one"),
                Highlight(2, "field-two"),
            ),
        )
        bot.services.youtube_highlight.create.return_value = result
        ctx = Mock()
        ctx.reply = AsyncMock()
        ctx.invoke = AsyncMock()
        cog = YouTubeCog(bot)

        with (
            patch(
                "yt_dl_bot.cogs.youtubecog.File",
                return_value="discord-file",
            ) as file_factory,
            patch(
                "yt_dl_bot.cogs.youtubecog.create_highlight_embed",
                return_value="discord-embed",
            ) as create_embed,
            patch("asyncio.to_thread", self.to_thread_mock()) as to_thread,
        ):
            await YouTubeCog.get_highlight.callback(
                cog,
                ctx,
                "https://youtu.be/video",
            )

        bot.services.youtube_highlight.create.assert_called_once_with(
            "https://youtu.be/video",
        )
        bot.services.youtube_highlight.archive_graph.assert_called_once_with(
            Path("/tmp/graph.png"),
        )
        create_embed.assert_called_once_with(result)
        ctx.invoke.assert_awaited_once_with(
            "send_highlight_output_log",
            "discord-file",
            "discord-embed",
        )
        self.assertEqual(to_thread.await_count, 3)
        self.assertIs(
            to_thread.await_args_list[0].args[0],
            bot.services.youtube_highlight.create,
        )
        self.assertIs(to_thread.await_args_list[1].args[0], file_factory)
        self.assertIs(
            to_thread.await_args_list[2].args[0],
            bot.services.youtube_highlight.archive_graph,
        )

    async def test_cancellation_stops_before_download_and_error_reply(self):
        bot = self.make_bot()
        ctx = Mock()
        ctx.reply = AsyncMock()
        ctx.invoke = AsyncMock()
        cog = YouTubeCog(bot)
        to_thread = AsyncMock(side_effect=asyncio.CancelledError)

        with (
            patch("asyncio.to_thread", to_thread),
            self.assertRaises(asyncio.CancelledError),
        ):
            await YouTubeCog.download_video.callback(
                cog,
                ctx,
                "https://youtu.be/video",
            )

        bot.services.youtube_download.download.assert_not_called()
        ctx.reply.assert_not_awaited()
        ctx.invoke.assert_not_awaited()

    async def test_command_failure_is_notified_once_by_error_handler(self):
        bot = self.make_bot()
        failure = VideoCheckError(
            "Unable to check video",
            original_error=RuntimeError("yt-dlp failed"),
        )
        bot.services.youtube_download.check.side_effect = failure
        ctx = Mock()
        ctx.reply = AsyncMock()
        ctx.invoke = AsyncMock()
        cog = YouTubeCog(bot)

        with (
            patch("asyncio.to_thread", self.to_thread_mock()),
            self.assertRaises(VideoCheckError),
        ):
            await YouTubeCog.download_video.callback(
                cog,
                ctx,
                "https://youtu.be/video",
            )

        ctx.invoke.assert_not_awaited()

        await YouTubeCog.download_video_error(
            cog,
            ctx,
            failure,
        )

        ctx.invoke.assert_awaited_once_with(
            "send_error_log",
            failure,
        )
