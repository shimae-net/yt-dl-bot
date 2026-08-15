import unittest

from yt_dl_bot.download_primitives import DownloadDependencies, RetryPolicy
from yt_dl_bot.download_service import (
    DownloadDependencies as LegacyDownloadDependencies,
)
from yt_dl_bot.download_service import RetryPolicy as LegacyRetryPolicy


class DownloadServiceCompatibilityTest(unittest.TestCase):
    def test_legacy_module_reexports_download_primitives(self):
        self.assertIs(LegacyDownloadDependencies, DownloadDependencies)
        self.assertIs(LegacyRetryPolicy, RetryPolicy)


if __name__ == "__main__":
    unittest.main()
