from __future__ import annotations

import json
from dataclasses import dataclass
import random
import time
from urllib import error
from typing import Iterable
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .base import PostBatch, RedditComment, RedditPost
from .config import CollectorConfig, CollectorQuery


REDDIT_BASE_URL = "https://www.reddit.com"


@dataclass(frozen=True)
class RedditClient:
    config: CollectorConfig

    def iter_post_batches(self, query: CollectorQuery) -> Iterable[PostBatch]:
        seen_ids: set[str] = set()
        keywords = self._get_keywords_for_run(query)
        for subreddit in query.subreddits:
            if keywords:
                for keyword in keywords:
                    listing = self._load_json(
                        f"/r/{subreddit}/search.json",
                        {
                            "q": keyword,
                            "restrict_sr": "1",
                            "sort": query.sort,
                            "t": query.timeframe,
                            "limit": str(self.config.search_batch_size),
                            "include_over_18": "on",
                        },
                    )
                    posts = self._extract_posts(listing, seen_ids)
                    if posts:
                        yield PostBatch(posts=posts, source_label=f"{subreddit}:{keyword}")
            else:
                listing_sort = query.sort if query.sort in {"hot", "new", "top"} else "top"
                params = {"limit": str(self.config.search_batch_size)}
                if listing_sort == "top":
                    params["t"] = query.timeframe
                listing = self._load_json(f"/r/{subreddit}/{listing_sort}.json", params)
                posts = self._extract_posts(listing, seen_ids)
                if posts:
                    yield PostBatch(posts=posts, source_label=f"{subreddit}:{listing_sort}")

    def _get_keywords_for_run(self, query: CollectorQuery) -> tuple[str, ...]:
        if not query.keywords:
            return ()
        keywords = list(query.keywords)
        if self.config.shuffle_keywords_each_run:
            rng = random.Random(self.config.shuffle_seed)
            rng.shuffle(keywords)
        return tuple(keywords)

    def fetch_comments(self, subreddit: str, external_id: str, limit: int) -> tuple[RedditComment, ...]:
        listing = self._load_json(f"/r/{subreddit}/comments/{external_id}.json", {"limit": str(limit)})
        if not isinstance(listing, list) or len(listing) < 2:
            return ()
        comments_root = listing[1].get("data", {}).get("children", [])
        return tuple(self._iter_comments(comments_root))

    def hydrate_post(self, post: RedditPost, comments_limit: int) -> RedditPost:
        comments = self.fetch_comments(post.subreddit, post.external_id, comments_limit)
        return RedditPost(
            external_id=post.external_id,
            subreddit=post.subreddit,
            title=post.title,
            body=post.body,
            author=post.author,
            permalink=post.permalink,
            url=post.url,
            flair=post.flair,
            score=post.score,
            upvote_ratio=post.upvote_ratio,
            comment_count=post.comment_count,
            created_utc=post.created_utc,
            comments=comments,
            raw_payload=post.raw_payload,
        )

    def _build_post(self, payload: dict) -> RedditPost | None:
        if payload.get("stickied") or payload.get("locked"):
            return None
        if payload.get("over_18"):
            return None
        return RedditPost(
            external_id=str(payload.get("id", "")),
            subreddit=str(payload.get("subreddit", "")),
            title=str(payload.get("title", "")).strip(),
            body=str(payload.get("selftext", "")).strip(),
            author=str(payload.get("author", "[deleted]")),
            permalink=f"{REDDIT_BASE_URL}{payload.get('permalink', '')}",
            url=str(payload.get("url", "")),
            flair=payload.get("link_flair_text"),
            score=int(payload.get("score", 0)),
            upvote_ratio=float(payload["upvote_ratio"]) if payload.get("upvote_ratio") is not None else None,
            comment_count=int(payload.get("num_comments", 0)),
            created_utc=int(payload.get("created_utc", 0)),
            raw_payload=payload,
        )

    def _extract_posts(self, listing: dict | list, seen_ids: set[str]) -> tuple[RedditPost, ...]:
        if not isinstance(listing, dict):
            return ()
        posts: list[RedditPost] = []
        for child in listing.get("data", {}).get("children", []):
            payload = child.get("data", {})
            post = self._build_post(payload)
            if post is None or post.external_id in seen_ids:
                continue
            seen_ids.add(post.external_id)
            posts.append(post)
        return tuple(posts)

    def _iter_comments(self, nodes: Iterable[dict]) -> Iterable[RedditComment]:
        for node in nodes:
            kind = node.get("kind")
            data = node.get("data", {})
            if kind != "t1":
                continue
            body = str(data.get("body", "")).strip()
            if not body or data.get("stickied"):
                continue
            yield RedditComment(
                external_id=str(data.get("id", "")),
                author=str(data.get("author", "[deleted]")),
                body=body,
                score=int(data.get("score", 0)),
                created_utc=int(data.get("created_utc", 0)),
                permalink=f"{REDDIT_BASE_URL}{data.get('permalink', '')}",
            )

    def _load_json(self, path: str, params: dict[str, str]) -> dict | list:
        query_string = urlencode(params)
        request = Request(
            url=f"{REDDIT_BASE_URL}{path}?{query_string}",
            headers={
                "User-Agent": self.config.user_agent,
                "Accept": "application/json",
            },
        )
        attempts = max(1, self.config.reddit_retry_attempts + 1)
        for attempt in range(1, attempts + 1):
            try:
                with urlopen(request, timeout=self.config.request_timeout_seconds) as response:
                    return json.loads(response.read().decode("utf-8"))
            except error.HTTPError as exc:
                details = exc.read().decode("utf-8", errors="replace")
                if exc.code == 429 and attempt < attempts:
                    print(
                        f"[collector] reddit rate limit 429 for {path}, retry {attempt}/{attempts - 1}",
                        flush=True,
                    )
                    time.sleep(self.config.reddit_retry_delay_seconds)
                    continue
                raise SystemExit(f"Reddit HTTP error {exc.code} for {path}: {details}") from exc
            except error.URLError as exc:
                if attempt < attempts:
                    time.sleep(self.config.reddit_retry_delay_seconds)
                    continue
                raise SystemExit(f"Reddit request failed for {path}: {exc.reason}") from exc
        raise SystemExit(f"Reddit request failed for {path}")
