# ---standard library---
import asyncio
from typing import TYPE_CHECKING, cast

# ---third party library---
from discord import File
from discord.ext import commands

# ---local library---
from .command_arguments import (
    YouTubeURL,
    handle_url_argument_error,
)
from .download_orchestration import coordinate_download
from .highlight_presenter import create_highlight_embed

if TYPE_CHECKING:
    from ..discord_bot_main import DownloadBot


class YouTubeCog(commands.Cog):
    def __init__(self, bot: "DownloadBot") -> None:
        self.bot = bot
        self.settings = bot.settings
        self.download_service = bot.services.youtube_download
        self.highlight_service = bot.services.youtube_highlight

    @commands.group(name="youtube")
    async def youtube_cog(self, ctx: commands.Context["DownloadBot"]) -> None:
        if ctx.invoked_subcommand is None:
            await ctx.send("Error: missing option")

    # discord.py's Group.command typing loses converter callback parameters.
    @youtube_cog.command(name="download", ignore_extra=False)  # type: ignore[arg-type]
    async def download_video(
        self,
        ctx: commands.Context["DownloadBot"],
        url: str = commands.parameter(converter=YouTubeURL),
    ) -> None:
        await coordinate_download(
            ctx,
            self.bot,
            self.download_service,
            url,
            before_reply=self._log_check_result,
        )

    def _log_check_result(self, text: str) -> None:
        for line in text.split("\n"):
            self.bot.logger.info(line)

    @download_video.error
    async def download_video_error(
        self,
        ctx: commands.Context["DownloadBot"],
        error: commands.CommandError,
    ) -> None:
        if await handle_url_argument_error(
            ctx,
            error,
            usage="youtube download <url>",
        ):
            return
        command = self.bot.get_command("send_error_log")
        if command is None:
            raise RuntimeError("send_error_log command is not loaded")
        cog_command = cast(commands.Command[commands.Cog, ..., object], command)
        await ctx.invoke(cog_command, error)

    # discord.py's Group.command typing loses converter callback parameters.
    @youtube_cog.command(name="highlight", ignore_extra=False)  # type: ignore[arg-type]
    async def get_highlight(
        self,
        ctx: commands.Context["DownloadBot"],
        url: str = commands.parameter(converter=YouTubeURL),
    ) -> None:
        await ctx.reply("Starting get highlight...")

        result = await asyncio.to_thread(
            self.highlight_service.create,
            url,
        )
        graph_image = result.graph_image
        self.bot.logger.debug(graph_image)
        file = await asyncio.to_thread(
            File,
            graph_image,
            filename="image.png",
        )

        embed = create_highlight_embed(result)

        command = self.bot.get_command("send_highlight_output_log")
        if command is None:
            raise RuntimeError("send_highlight_output_log command is not loaded")
        cog_command = cast(commands.Command[commands.Cog, ..., object], command)
        await ctx.invoke(cog_command, file, embed)
        await asyncio.to_thread(
            self.highlight_service.archive_graph,
            graph_image,
        )

    @get_highlight.error
    async def get_highlight_error(
        self,
        ctx: commands.Context["DownloadBot"],
        error: commands.CommandError,
    ) -> None:
        if await handle_url_argument_error(
            ctx,
            error,
            usage="youtube highlight <url>",
        ):
            return
        command = self.bot.get_command("send_error_log")
        if command is None:
            raise RuntimeError("send_error_log command is not loaded")
        cog_command = cast(commands.Command[commands.Cog, ..., object], command)
        await ctx.invoke(cog_command, error)


async def setup(bot: "DownloadBot") -> None:
    await bot.add_cog(YouTubeCog(bot))
