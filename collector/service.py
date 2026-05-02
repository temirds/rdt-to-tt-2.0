from __future__ import annotations

from dataclasses import dataclass

from .analysis import analyze_post, passes_metadata_precheck
from .base import PostBatch, PreparedThread, RedditPost
from .config import CollectorConfig, CollectorQuery
from .db import CollectorDatabase
from .demo import build_demo_posts
from .formatter import build_answers, build_question, render_thread, sanitize_question_text
from .language import detect_language
from .reddit import RedditClient


@dataclass(frozen=True)
class CollectorSummary:
    fetched_posts: int
    fetched_batches: int
    scanned_posts: int
    prepared_posts: int
    saved_posts: int
    skipped_posts: int


class CollectorService:
    def __init__(
        self,
        config: CollectorConfig | None = None,
        reddit_client: RedditClient | None = None,
    ) -> None:
        self.config = config or CollectorConfig()
        self.reddit_client = reddit_client or RedditClient(self.config)
        self.database = CollectorDatabase(self.config.db_path)
        self.database.initialize()

    def ingest(self, query: CollectorQuery) -> CollectorSummary:
        fetched_posts = 0
        fetched_batches = 0
        scanned_posts = 0
        prepared_posts = 0
        saved_posts = 0
        skipped_posts = 0
        self._print_start(is_demo=False)

        for batch in self.reddit_client.iter_post_batches(query):
            if scanned_posts >= self.config.max_scanned_posts_per_run:
                self._print_stop("reached max_scanned_posts_per_run", scanned_posts, saved_posts)
                return CollectorSummary(
                    fetched_posts=fetched_posts,
                    fetched_batches=fetched_batches,
                    scanned_posts=scanned_posts,
                    prepared_posts=prepared_posts,
                    saved_posts=saved_posts,
                    skipped_posts=skipped_posts,
                )
            if saved_posts >= self.config.target_saved_posts_per_run:
                self._print_stop("reached target_saved_posts_per_run", scanned_posts, saved_posts)
                return CollectorSummary(
                    fetched_posts=fetched_posts,
                    fetched_batches=fetched_batches,
                    scanned_posts=scanned_posts,
                    prepared_posts=prepared_posts,
                    saved_posts=saved_posts,
                    skipped_posts=skipped_posts,
                )
            fetched_batches += 1
            fetched_posts += len(batch.posts)
            batch_scanned_before = scanned_posts
            batch_saved_before = saved_posts
            batch_prepared_before = prepared_posts
            batch_skipped_before = skipped_posts
            for raw_post in batch.posts:
                if scanned_posts >= self.config.max_scanned_posts_per_run:
                    self._print_stop("reached max_scanned_posts_per_run", scanned_posts, saved_posts)
                    self._print_batch_result(
                        batch,
                        fetched_posts,
                        scanned_posts - batch_scanned_before,
                        prepared_posts - batch_prepared_before,
                        saved_posts - batch_saved_before,
                        skipped_posts - batch_skipped_before,
                        scanned_posts,
                        saved_posts,
                        self.config.max_scanned_posts_per_run,
                    )
                    return CollectorSummary(
                        fetched_posts=fetched_posts,
                        fetched_batches=fetched_batches,
                        scanned_posts=scanned_posts,
                        prepared_posts=prepared_posts,
                        saved_posts=saved_posts,
                        skipped_posts=skipped_posts,
                    )
                if saved_posts >= self.config.target_saved_posts_per_run:
                    self._print_stop("reached target_saved_posts_per_run", scanned_posts, saved_posts)
                    self._print_batch_result(
                        batch,
                        fetched_posts,
                        scanned_posts - batch_scanned_before,
                        prepared_posts - batch_prepared_before,
                        saved_posts - batch_saved_before,
                        skipped_posts - batch_skipped_before,
                        scanned_posts,
                        saved_posts,
                        self.config.max_scanned_posts_per_run,
                    )
                    return CollectorSummary(
                        fetched_posts=fetched_posts,
                        fetched_batches=fetched_batches,
                        scanned_posts=scanned_posts,
                        prepared_posts=prepared_posts,
                        saved_posts=saved_posts,
                        skipped_posts=skipped_posts,
                    )
                scanned_posts += 1
                if self.database.has_thread(raw_post.external_id):
                    skipped_posts += 1
                    continue
                if not passes_metadata_precheck(raw_post, query, self.config):
                    skipped_posts += 1
                    continue

                hydrated_post = self.reddit_client.hydrate_post(raw_post, self.config.comments_fetch_limit)
                prepared = self.prepare_thread(hydrated_post, query)
                if prepared is None:
                    skipped_posts += 1
                    continue

                prepared_posts += 1
                saved = self.database.save_thread(
                    source_name=self.config.source_name,
                    prepared=prepared,
                    store_raw_payload=self.config.store_raw_payload,
                )
                if saved.inserted:
                    saved_posts += 1
                if scanned_posts >= self.config.max_scanned_posts_per_run or saved_posts >= self.config.target_saved_posts_per_run:
                    break
            self._print_batch_result(
                batch,
                fetched_posts,
                scanned_posts - batch_scanned_before,
                prepared_posts - batch_prepared_before,
                saved_posts - batch_saved_before,
                skipped_posts - batch_skipped_before,
                scanned_posts,
                saved_posts,
                self.config.max_scanned_posts_per_run,
            )

        return CollectorSummary(
            fetched_posts=fetched_posts,
            fetched_batches=fetched_batches,
            scanned_posts=scanned_posts,
            prepared_posts=prepared_posts,
            saved_posts=saved_posts,
            skipped_posts=skipped_posts,
        )

    def prepare_thread(self, post: RedditPost, query: CollectorQuery) -> PreparedThread | None:
        analysis = analyze_post(post, query, self.config)
        if not analysis.accepted:
            return None

        question = sanitize_question_text(build_question(post))
        answers = build_answers(
            post,
            min_comment_length=self.config.min_comment_length,
            max_comment_length=self.config.max_comment_length,
            excluded_keywords=query.answer_excluded_keywords,
        )
        if len(answers) < self.config.min_usable_comments:
            return None
        answers = answers[: self.config.max_usable_comments]

        original_text = render_thread(question, answers)
        language = detect_language(original_text, self.config.english_stopwords)

        return PreparedThread(
            post=post,
            language=language,
            analysis=analysis,
            question=question,
            answers=answers,
            original_text=original_text,
        )

    def ingest_demo(self, query: CollectorQuery) -> CollectorSummary:
        fetched_posts = build_demo_posts()
        fetched_batches = 1
        scanned_posts = 0
        prepared_posts = 0
        saved_posts = 0
        skipped_posts = 0
        self._print_start(is_demo=True)
        demo_batch = PostBatch(posts=tuple(fetched_posts), source_label="demo")
        batch_scanned_before = scanned_posts
        batch_saved_before = saved_posts
        batch_prepared_before = prepared_posts
        batch_skipped_before = skipped_posts

        for post in fetched_posts:
            if scanned_posts >= self.config.max_scanned_posts_per_run:
                self._print_stop("reached max_scanned_posts_per_run", scanned_posts, saved_posts)
                break
            if saved_posts >= self.config.target_saved_posts_per_run:
                self._print_stop("reached target_saved_posts_per_run", scanned_posts, saved_posts)
                break
            scanned_posts += 1
            if self.database.has_thread(post.external_id):
                skipped_posts += 1
                continue
            prepared = self.prepare_thread(post, query)
            if prepared is None:
                skipped_posts += 1
                continue
            prepared_posts += 1
            saved = self.database.save_thread(
                source_name=f"{self.config.source_name}-demo",
                prepared=prepared,
                store_raw_payload=self.config.store_raw_payload,
            )
            if saved.inserted:
                saved_posts += 1
            if scanned_posts >= self.config.max_scanned_posts_per_run or saved_posts >= self.config.target_saved_posts_per_run:
                break
        self._print_batch_result(
            demo_batch,
            len(fetched_posts),
            scanned_posts - batch_scanned_before,
            prepared_posts - batch_prepared_before,
            saved_posts - batch_saved_before,
            skipped_posts - batch_skipped_before,
            scanned_posts,
            saved_posts,
            self.config.max_scanned_posts_per_run,
        )

        return CollectorSummary(
            fetched_posts=len(fetched_posts),
            fetched_batches=fetched_batches,
            scanned_posts=scanned_posts,
            prepared_posts=prepared_posts,
            saved_posts=saved_posts,
            skipped_posts=skipped_posts,
        )

    def _print_start(self, is_demo: bool = False) -> None:
        mode = "demo" if is_demo else "reddit"
        print(
            f"[collector] start mode={mode} "
            f"target={self.config.target_saved_posts_per_run} max_scan={self.config.max_scanned_posts_per_run}",
            flush=True,
        )

    @staticmethod
    def _print_batch_result(
        batch: PostBatch,
        fetched_posts: int,
        batch_scanned: int,
        batch_prepared: int,
        batch_saved: int,
        batch_skipped: int,
        scanned_total: int,
        saved_total: int,
        max_scanned_posts: int,
    ) -> None:
        print(
            f"[collector] [{scanned_total}/{max_scanned_posts}] source={batch.source_label} "
            f"posts={len(batch.posts)} scanned={batch_scanned} prepared={batch_prepared} "
            f"saved={batch_saved} skipped={batch_skipped} total_scanned={scanned_total} "
            f"total_saved={saved_total} pool={fetched_posts}",
            flush=True,
        )

    @staticmethod
    def _print_stop(reason: str, scanned_posts: int, saved_posts: int) -> None:
        print(f"[collector] stop reason={reason} scanned={scanned_posts} saved={saved_posts}", flush=True)
