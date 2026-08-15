import datetime
from collections import deque
from collections.abc import Iterable, Iterator, Sequence
from logging import getLogger
from pathlib import Path
from typing import Protocol, cast

import matplotlib.pyplot as plt
from pytchat import create

from .highlight import Highlight
from .setting import Settings


class HighlightSettings(Protocol):
    TMP_PATH: Path


class ChatSource(Protocol):
    """Source of elapsed-time values from a video's chat."""

    def collect_elapsed_times(self, video_id: str) -> Iterable[object]: ...


class PytchatSource:
    """Read replay chat through pytchat and release it after collection."""

    def collect_elapsed_times(self, video_id: str) -> Iterator[object]:
        chat = create(video_id=video_id, force_replay=True)
        try:
            while chat.is_alive():
                for comment in chat.get().items:
                    yield comment.elapsedTime
        finally:
            chat.terminate()


class HighlightAnalyzer:
    """Pure chat aggregation, scoring and peak-detection logic."""

    def __init__(self, bucket_seconds: int = 30):
        self.bucket_seconds = bucket_seconds

    @staticmethod
    def elapsed_seconds(elapsed_time: object) -> int | None:
        if not isinstance(elapsed_time, str):
            return None
        parts = elapsed_time.replace(",", "").split(":")
        try:
            values = [int(part) for part in parts]
        except (TypeError, ValueError):
            return None

        if len(values) == 3:
            return values[0] * 3600 + values[1] * 60 + values[2]
        if len(values) == 2:
            return values[0] * 60 + values[1]
        if len(values) == 1:
            return values[0]
        return None

    def count_comments(self, elapsed_times: Iterable[object]) -> list[int]:
        counts: list[int] = []
        for elapsed_time in elapsed_times:
            elapsed = self.elapsed_seconds(elapsed_time)
            if elapsed is None or elapsed < 0:
                continue
            bucket = elapsed // self.bucket_seconds
            if bucket >= len(counts):
                counts.extend([0] * (bucket + 1 - len(counts)))
            counts[bucket] += 1
        return counts

    @staticmethod
    def count_score(comment_counts: Iterable[int]) -> list[float]:
        score_data: list[float] = []
        average_count = deque([1000] * 8)
        for comment_count in comment_counts:
            score = 0.0
            if comment_count > 0:
                score = comment_count / (sum(average_count) / len(average_count))
                average_count.append(comment_count)
                average_count.popleft()
            score_data.append(score)
        return score_data

    def peak_times(self, score_data: Sequence[float]) -> list[int]:
        if not score_data or max(score_data) <= 0:
            return []

        max_score = max(score_data)
        peak_times = []
        index = 0
        while index < len(score_data):
            if score_data[index] > max_score * 0.3:
                peak_index = max(index - 1, 0)
                quiet_buckets = 2
                while quiet_buckets >= 0 and index < len(score_data):
                    if score_data[index] > max_score * 0.3:
                        quiet_buckets = 2
                    else:
                        quiet_buckets -= 1
                    index += 1
                peak_times.append(peak_index * self.bucket_seconds)
            index += 1
        return peak_times


class GraphRenderer(Protocol):
    def render(
        self,
        score_data: Sequence[float],
        bucket_seconds: int,
        image_path: Path,
    ) -> None: ...


class MatplotlibGraphRenderer:
    """Render activity scores with matplotlib."""

    def render(
        self,
        score_data: Sequence[float],
        bucket_seconds: int,
        image_path: Path,
    ) -> None:
        image_path.parent.mkdir(parents=True, exist_ok=True)
        figure = plt.figure()
        try:
            plt.plot(
                [index * bucket_seconds for index in range(len(score_data))],
                score_data,
            )
            plt.grid(axis="y", linestyle="dotted")
            figure.savefig(image_path)
        finally:
            plt.close(figure)


class Clock(Protocol):
    def now(self) -> datetime.datetime: ...


class SystemClock:
    def now(self) -> datetime.datetime:
        return datetime.datetime.now()


class ChatHighlightPipeline:
    """Coordinate chat collection, analysis and graph rendering."""

    BUCKET_SECONDS = 30

    def __init__(
        self,
        video_id: str,
        settings: HighlightSettings | None = None,
        *,
        chat_source: ChatSource | None = None,
        analyzer: HighlightAnalyzer | None = None,
        graph_renderer: GraphRenderer | None = None,
        clock: Clock | None = None,
    ) -> None:
        # BaseSettings fields are populated from the environment at runtime.
        # Its generated static constructor cannot express that zero-argument use.
        settings_factory = cast("type[HighlightSettings]", Settings)
        settings = settings or settings_factory()
        self.logger = getLogger(__name__)
        self.video_id = video_id
        self.url = f"https://youtu.be/{video_id}"
        self.chat_source = chat_source or PytchatSource()
        self.analyzer = analyzer or HighlightAnalyzer(self.BUCKET_SECONDS)
        self.graph_renderer = graph_renderer or MatplotlibGraphRenderer()
        self.clock = clock or SystemClock()
        date = self.clock.now().strftime("%Y-%m-%d-%H%M")
        self.image_name = f"scoregraph_{date}_{video_id}.png"
        self.image_path = Path(settings.TMP_PATH) / self.image_name

    def collect_comment_counts(self) -> list[int]:
        """Return comment counts grouped into 30-second buckets."""
        elapsed_times = self.chat_source.collect_elapsed_times(self.video_id)
        return self.analyzer.count_comments(elapsed_times)

    def count_score(self, comment_counts: Iterable[int]) -> list[float]:
        return self.analyzer.count_score(comment_counts)

    def render_score_graph(self, score_data: Sequence[float]) -> None:
        self.graph_renderer.render(
            score_data,
            self.analyzer.bucket_seconds,
            self.image_path,
        )

    def get_peak_times(self, score_data: Sequence[float]) -> list[int]:
        return self.analyzer.peak_times(score_data)

    def get_highlight(self) -> tuple[Highlight, ...]:
        self.logger.info("Collecting chat activity for %s", self.video_id)
        comment_counts = self.collect_comment_counts()
        score_data = self.count_score(comment_counts)
        self.render_score_graph(score_data)

        highlights: list[Highlight] = []
        for seconds in self.get_peak_times(score_data):
            url = f"{self.url}?t={seconds}s"
            self.logger.info("Highlight: %s", url)
            highlights.append(Highlight(seconds=seconds, url=url))
        return tuple(highlights)
