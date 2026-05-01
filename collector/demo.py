from __future__ import annotations

from .base import RedditComment, RedditPost


def build_demo_posts() -> list[RedditPost]:
    return [
        RedditPost(
            external_id="demo-reddit-001",
            subreddit="AskReddit",
            title="What habit improved your life the most?",
            body="Please share practical examples and why they worked.",
            author="demo_user",
            permalink="https://reddit.example/demo-reddit-001",
            url="https://reddit.example/demo-reddit-001",
            flair="Discussion",
            score=450,
            upvote_ratio=0.94,
            comment_count=21,
            created_utc=1710000000,
            comments=(
                RedditComment(
                    "c1",
                    "alpha",
                    "Daily walking was the turning point for me. It was easy to keep doing even on bad days, and once it became automatic it improved my sleep, mood, and weight.",
                    120,
                    1710000100,
                    "https://reddit.example/c1",
                ),
                RedditComment(
                    "c2",
                    "beta",
                    "Budgeting every expense for three months changed how I saw money. I stopped guessing and started making decisions based on numbers instead of stress.",
                    90,
                    1710000200,
                    "https://reddit.example/c2",
                ),
                RedditComment(
                    "c3",
                    "gamma",
                    "Going to bed at the same time every night fixed more problems than any productivity app. My focus became steadier and I stopped relying on panic to get work done.",
                    80,
                    1710000300,
                    "https://reddit.example/c3",
                ),
            ),
            raw_payload={"demo": True},
        ),
        RedditPost(
            external_id="demo-reddit-002",
            subreddit="AskReddit",
            title="What financial lesson took you too long to learn?",
            body="I want answers that changed real behavior, not slogans.",
            author="demo_user_2",
            permalink="https://reddit.example/demo-reddit-002",
            url="https://reddit.example/demo-reddit-002",
            flair="Discussion",
            score=388,
            upvote_ratio=0.91,
            comment_count=19,
            created_utc=1710001000,
            comments=(
                RedditComment(
                    "c4",
                    "delta",
                    "Lifestyle inflation is much more dangerous than one big bad purchase. Every small upgrade felt harmless until my fixed expenses became hard to undo.",
                    150,
                    1710001100,
                    "https://reddit.example/c4",
                ),
                RedditComment(
                    "c5",
                    "epsilon",
                    "An emergency fund is not optional. The first time my car and my health both failed in the same month, I understood that savings are not 'idle money'.",
                    124,
                    1710001200,
                    "https://reddit.example/c5",
                ),
                RedditComment(
                    "c6",
                    "zeta",
                    "Tracking subscriptions and auto-payments saved me more money than trying to optimize every grocery trip. Leaks beat one-time mistakes over the long run.",
                    112,
                    1710001300,
                    "https://reddit.example/c6",
                ),
            ),
            raw_payload={"demo": True},
        ),
    ]
