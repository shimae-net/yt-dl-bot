import datetime
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from yt_dl_bot.chat_highlights import (
    ChatHighlightPipeline,
    HighlightAnalyzer,
    MatplotlibGraphRenderer,
    PytchatSource,
)
from yt_dl_bot.highlight import Highlight


class HighlightAnalyzerTest(unittest.TestCase):
    def setUp(self):
        self.analyzer = HighlightAnalyzer(bucket_seconds=30)

    def test_elapsed_seconds(self):
        self.assertEqual(self.analyzer.elapsed_seconds("01:02:03"), 3723)
        self.assertEqual(self.analyzer.elapsed_seconds("02:03"), 123)
        self.assertEqual(self.analyzer.elapsed_seconds("12"), 12)
        self.assertIsNone(self.analyzer.elapsed_seconds("invalid"))
        self.assertIsNone(self.analyzer.elapsed_seconds(None))

    def test_count_comments_groups_valid_times_into_buckets(self):
        counts = self.analyzer.count_comments(["0:00", "0:29", "0:30", "1:01", "invalid", "-1"])

        self.assertEqual(counts, [2, 1, 1])

    def test_count_score_does_not_persist_comments(self):
        scores = self.analyzer.count_score([0, 10, 20])

        self.assertEqual(len(scores), 3)
        self.assertEqual(scores[0], 0)
        self.assertGreater(scores[2], scores[1])

    def test_peak_times_returns_start_before_activity(self):
        self.assertEqual(self.analyzer.peak_times([0, 0, 0.5]), [30])

    def test_peak_times_returns_empty_for_no_activity(self):
        self.assertEqual(self.analyzer.peak_times([]), [])
        self.assertEqual(self.analyzer.peak_times([0, 0]), [])


class HighlightTest(unittest.TestCase):
    def test_is_an_immutable_typed_value(self):
        highlight = Highlight(seconds=30, url="https://youtu.be/video?t=30s")

        with self.assertRaises(FrozenInstanceError):
            highlight.seconds = 60


class PytchatSourceTest(unittest.TestCase):
    @patch("yt_dl_bot.chat_highlights.create")
    def test_collects_elapsed_times_and_always_terminates_chat(self, create):
        chat = create.return_value
        chat.is_alive.side_effect = [True, False]
        chat.get.return_value.items = [
            SimpleNamespace(elapsedTime="0:01"),
            SimpleNamespace(elapsedTime="0:02"),
        ]

        elapsed_times = list(PytchatSource().collect_elapsed_times("video-id"))

        create.assert_called_once_with(video_id="video-id", force_replay=True)
        self.assertEqual(elapsed_times, ["0:01", "0:02"])
        chat.terminate.assert_called_once_with()

    @patch("yt_dl_bot.chat_highlights.create")
    def test_large_collection_is_lazy_and_not_buffered(self, create):
        chat = create.return_value
        chat.is_alive.return_value = True
        chat.get.return_value.items = (
            SimpleNamespace(elapsedTime=f"0:{second:02}") for second in range(1_000_000)
        )

        elapsed_times = PytchatSource().collect_elapsed_times("video-id")

        create.assert_not_called()
        self.assertNotIsInstance(elapsed_times, list)
        self.assertEqual(next(elapsed_times), "0:00")
        create.assert_called_once_with(video_id="video-id", force_replay=True)
        chat.get.assert_called_once_with()

        elapsed_times.close()
        chat.terminate.assert_called_once_with()


class ChatHighlightPipelineTest(unittest.TestCase):
    def setUp(self):
        self.settings = SimpleNamespace(TMP_PATH="downloads/cache/")

    def test_dependencies_and_clock_are_injected(self):
        source = Mock()
        source.collect_elapsed_times.return_value = ["0:00", "0:30", "1:00"]
        renderer = Mock()
        clock = Mock()
        clock.now.return_value = datetime.datetime(2026, 7, 28, 12, 34)
        module = ChatHighlightPipeline(
            "video-id",
            settings=self.settings,
            chat_source=source,
            graph_renderer=renderer,
            clock=clock,
        )

        highlights = module.get_highlight()

        source.collect_elapsed_times.assert_called_once_with("video-id")
        renderer.render.assert_called_once()
        rendered_scores, bucket_seconds, image_path = renderer.render.call_args.args
        self.assertEqual(len(rendered_scores), 3)
        self.assertEqual(bucket_seconds, 30)
        self.assertEqual(
            image_path,
            Path("downloads/cache/scoregraph_2026-07-28-1234_video-id.png"),
        )
        self.assertEqual(
            highlights,
            (Highlight(seconds=0, url="https://youtu.be/video-id?t=0s"),),
        )

    def test_analysis_helpers_delegate_to_analyzer(self):
        module = ChatHighlightPipeline("video-id", settings=self.settings)

        self.assertEqual(module.get_peak_times([0, 0, 0.5]), [30])


class MatplotlibGraphRendererTest(unittest.TestCase):
    @patch("yt_dl_bot.chat_highlights.plt")
    def test_render_creates_parent_and_closes_figure(self, plt):
        figure = plt.figure.return_value
        image_path = Path("downloads/cache/graph.png")

        with patch.object(Path, "mkdir") as mkdir:
            MatplotlibGraphRenderer().render([0, 1], 30, image_path)

        mkdir.assert_called_once_with(parents=True, exist_ok=True)
        plt.plot.assert_called_once_with([0, 30], [0, 1])
        figure.savefig.assert_called_once_with(image_path)
        plt.close.assert_called_once_with(figure)


if __name__ == "__main__":
    unittest.main()
