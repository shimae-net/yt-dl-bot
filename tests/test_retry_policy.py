import unittest

import yt_dlp

from yt_dl_bot.download_primitives import (
    RetryDecision,
    RetryPolicy,
    RetryStatus,
)


class RetryPolicyTest(unittest.TestCase):
    def test_distinguishes_retryable_and_permanent_failures(self):
        policy = RetryPolicy()

        retryable = policy.decide(
            yt_dlp.utils.DownloadError(
                "This live event will begin shortly.",
            )
        )
        permanent = policy.decide(
            yt_dlp.utils.ExtractorError("Unsupported URL"),
        )

        self.assertEqual(
            retryable,
            RetryDecision(RetryStatus.RETRYABLE, 15),
        )
        self.assertEqual(
            permanent,
            RetryDecision(RetryStatus.PERMANENT_FAILURE),
        )
        self.assertEqual(
            policy.decide(yt_dlp.utils.DownloadError("Premieres in 7 hours.")),
            RetryDecision(RetryStatus.RETRYABLE, 23400),
        )

    def test_rejects_invalid_retry_limits(self):
        with self.assertRaises(ValueError):
            RetryPolicy(max_attempts=0)
        with self.assertRaises(ValueError):
            RetryPolicy(max_wait_seconds=-1)
