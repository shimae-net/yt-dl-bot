"""Shared Discord orchestration for successful video downloads."""

import asyncio
from collections.abc import Callable
from typing import TYPE_CHECKING, cast

from discord.ext import commands

from ..cancellation import to_thread_cancellable
from ..video_download_service import VideoDownloadService

if TYPE_CHECKING:
    from ..discord_bot_main import DownloadBot


async def coordinate_download(
    ctx: commands.Context["DownloadBot"],
    bot: "DownloadBot",
    download_service: VideoDownloadService,
    url: str,
    *,
    before_reply: Callable[[str], None] | None = None,
    check_result: str | None = None,
) -> None:
    """Check, download, and announce a video while preserving cancellation."""
    if check_result is None:
        check_result = await asyncio.to_thread(download_service.check, url)
    if before_reply is not None:
        before_reply(check_result)
    await ctx.reply(check_result)

    result = await to_thread_cancellable(download_service.download, url)
    bot.logger.info("Download Success!")
    command = bot.get_command("send_video_output_log")
    if command is None:
        raise RuntimeError("send_video_output_log command is not loaded")
    cog_command = cast(commands.Command[commands.Cog, ..., object], command)
    await ctx.invoke(cog_command, result=result)
