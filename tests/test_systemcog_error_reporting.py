import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from yt_dl_bot.cogs.systemcog import SystemCog
from yt_dl_bot.discord_notification_delivery import DiscordNotificationDelivery
from yt_dl_bot.error_reporting import (
    REDACTED,
    format_exception_traceback,
    sanitize_discord_error_report,
)


class SendErrorLogTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.channel = Mock(mention="#logs")
        self.channel.send = AsyncMock()
        self.bot = Mock(
            settings=SimpleNamespace(LOG_CHANNEL=1),
            logger=Mock(),
        )
        self.bot.get_channel.return_value = self.channel
        self.cog = SystemCog(self.bot)
        self.ctx = Mock(reply=AsyncMock())

    def test_system_cog_uses_typed_notification_delivery(self):
        self.assertIsInstance(self.cog.notifications, DiscordNotificationDelivery)

    @staticmethod
    def _long_exception():
        try:
            raise RuntimeError("x" * 30_000)
        except RuntimeError as error:
            return error

    async def test_long_traceback_is_sent_as_valid_embeds_and_logged_in_full(self):
        error = self._long_exception()
        expected_log = format_exception_traceback(error)

        await SystemCog.send_error_log.callback(self.cog, self.ctx, error)

        self.bot.logger.error.assert_called_once_with(expected_log)
        self.assertGreater(self.channel.send.await_count, 1)
        sent_values = []
        for sent_call in self.channel.send.await_args_list:
            embed = sent_call.kwargs["embed"]
            self.assertLessEqual(len(embed.fields), 5)
            self.assertLessEqual(len(embed), 6000)
            for field in embed.fields:
                self.assertLessEqual(len(field.value), 1024)
                sent_values.append(field.value)
        self.assertEqual(
            "".join(sent_values),
            sanitize_discord_error_report(expected_log),
        )

    async def test_complete_traceback_is_logged_before_notification_failure(self):
        error = self._long_exception()
        expected_log = format_exception_traceback(error)
        self.ctx.reply.side_effect = RuntimeError("Discord unavailable")

        with self.assertRaisesRegex(RuntimeError, "Discord unavailable"):
            await SystemCog.send_error_log.callback(self.cog, self.ctx, error)

        self.bot.logger.error.assert_called_once_with(expected_log)
        self.channel.send.assert_not_awaited()

    async def test_discord_report_is_redacted_but_local_log_retains_full_traceback(self):
        secret = "super-secret-token"
        local_path = "/home/bot/private/worker.py"
        try:
            raise RuntimeError(
                f"token={secret} path={local_path} "
                "url=https://user:pass@example.test/watch?token=query-secret&v=123"
            )
        except RuntimeError as error:
            caught_error = error
            expected_log = format_exception_traceback(error)

        await SystemCog.send_error_log.callback(self.cog, self.ctx, caught_error)

        self.bot.logger.error.assert_called_once_with(expected_log)
        self.assertIn(secret, self.bot.logger.error.call_args.args[0])
        self.assertIn(local_path, self.bot.logger.error.call_args.args[0])

        discord_report = "".join(
            field.value
            for sent_call in self.channel.send.await_args_list
            for field in sent_call.kwargs["embed"].fields
        )
        self.assertIn(REDACTED, discord_report)
        self.assertNotIn(secret, discord_report)
        self.assertNotIn("query-secret", discord_report)
        self.assertNotIn("user:pass", discord_report)
        self.assertNotIn(local_path, discord_report)
        self.assertIn("RuntimeError", discord_report)
        self.assertIn("worker.py", discord_report)


if __name__ == "__main__":
    unittest.main()
