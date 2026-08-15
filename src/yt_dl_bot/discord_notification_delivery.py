"""Typed delivery of bot notifications to Discord channels."""

from typing import TYPE_CHECKING, cast

from discord import Embed, File, TextChannel
from discord.ext import commands

from .application_results import DownloadResult
from .error_reporting import (
    format_exception_traceback,
    sanitize_discord_error_report,
    split_traceback_for_embeds,
)

if TYPE_CHECKING:
    from .discord_bot_main import DownloadBot


class DiscordNotificationDelivery:
    """Send the bot's typed notification payloads to their configured channels."""

    def __init__(self, bot: "DownloadBot") -> None:
        self.bot = bot
        self.settings = bot.settings

    def _channel(self, channel_id: int) -> TextChannel:
        return cast(TextChannel, self.bot.get_channel(channel_id))

    async def send_log(self, *lines: str) -> None:
        await self._channel(self.settings.LOG_CHANNEL).send("``" + "\n".join(lines) + "``")

    async def report_error(
        self,
        ctx: commands.Context["DownloadBot"],
        error: BaseException,
    ) -> None:
        log_channel = self._channel(self.settings.LOG_CHANNEL)
        error_log = format_exception_traceback(error)

        # Persist the complete traceback before attempting Discord I/O. This
        # ensures a failed notification never hides the original error.
        self.bot.logger.error(error_log)
        discord_error_log = sanitize_discord_error_report(error_log)

        await ctx.reply("Error: Check " + log_channel.mention)

        field_number = 1
        for field_batch in split_traceback_for_embeds(discord_error_log):
            embed = Embed()
            for field_value in field_batch:
                embed.add_field(
                    name=f"Traceback {field_number}",
                    value=field_value,
                    inline=False,
                )
                field_number += 1
            await log_channel.send(embed=embed)

    async def send_download_result(self, result: DownloadResult) -> None:
        await self._channel(self.settings.VIDEO_OUTPUT_CHANNEL).send(
            "**Download Success : **" + result.title + "\n" + result.source_url,
        )

    async def send_highlight(self, file: File, embed: Embed) -> None:
        await self._channel(self.settings.HIGHLIGHT_OUTPUT_CHANNEL).send(
            file=file,
            embed=embed,
        )
