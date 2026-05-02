from __future__ import annotations

from .base import AnalysisResult, RedditPost
from .config import CollectorConfig, CollectorQuery
from .formatter import build_question, question_contains_excluded_keywords, sanitize_text_for_voiceover


def passes_metadata_precheck(post: RedditPost, query: CollectorQuery, config: CollectorConfig) -> bool:
    haystack = " ".join(filter(None, [post.title, post.body, post.flair or ""])).lower()
    title = post.title.strip()
    question_text = build_question(post)
    question_length = len(sanitize_text_for_voiceover(question_text))

    if post.score < query.min_score:
        return False
    if post.comment_count < query.min_comments:
        return False
    if len(title) < query.min_question_length:
        return False
    if question_length > query.max_question_length:
        return False
    if len(title) + len(post.body.strip()) < query.min_combined_text_length:
        return False
    if query.require_questionish_title and "?" not in title:
        return False
    if query.flair_tags:
        flair = (post.flair or "").strip().lower()
        if not any(tag.lower() == flair for tag in query.flair_tags):
            return False
    if query.keywords and not any(keyword.lower() in haystack for keyword in query.keywords):
        return False
    if any(keyword.lower() in haystack for keyword in query.excluded_keywords):
        return False
    if any(phrase.lower() in haystack for phrase in config.analysis_blocked_phrases):
        return False
    if question_contains_excluded_keywords(question_text, query.question_excluded_keywords):
        return False
    return True


def analyze_post(post: RedditPost, query: CollectorQuery, config: CollectorConfig) -> AnalysisResult:
    haystack = " ".join(filter(None, [post.title, post.body, post.flair or ""])).lower()
    question_text = build_question(post)
    reasons: list[str] = []
    matched_keywords = tuple(sorted({keyword for keyword in query.keywords if keyword.lower() in haystack}))
    score = 0.0
    combined_length = len(post.title.strip()) + len(post.body.strip())
    question_length = len(sanitize_text_for_voiceover(question_text))
    viable_comments = [
        comment
        for comment in post.comments
        if config.min_comment_length <= _voiceover_text_length(comment.body) <= config.max_comment_length
    ]
    unique_authors = {comment.author.strip().lower() for comment in viable_comments if comment.author.strip()}
    total_answer_chars = sum(_voiceover_text_length(comment.body) for comment in viable_comments)

    if post.score >= query.min_score:
        score += 2.0
        reasons.append(f"score>={query.min_score}")
    else:
        score -= 2.0
        reasons.append(f"score<{query.min_score}")

    if post.comment_count >= query.min_comments:
        score += 2.0
        reasons.append(f"comments>={query.min_comments}")
    else:
        score -= 1.5
        reasons.append(f"comments<{query.min_comments}")

    if post.comment_count <= query.max_comment_count:
        score += 0.5
        reasons.append(f"comments<={query.max_comment_count}")
    else:
        reasons.append(f"comments>{query.max_comment_count}")

    if matched_keywords:
        score += min(3.0, 0.8 * len(matched_keywords))
        reasons.append(f"keyword_match:{', '.join(matched_keywords)}")

    flair = (post.flair or "").strip().lower()
    if query.flair_tags:
        flair_matches = [tag for tag in query.flair_tags if tag.lower() == flair]
        if flair_matches:
            score += 1.5
            reasons.append(f"flair_match:{', '.join(flair_matches)}")
        else:
            score -= 0.5
            reasons.append("flair_miss")

    if post.upvote_ratio is not None and post.upvote_ratio >= 0.75:
        score += 0.5
        reasons.append("healthy_upvote_ratio")

    title = post.title.strip()
    if len(title) >= query.min_question_length:
        score += 0.5
        reasons.append("question_length_ok")
    else:
        score -= 0.5
        reasons.append("question_too_short")

    if question_length <= query.max_question_length:
        score += 0.5
        reasons.append(f"question_chars<={query.max_question_length}")
    else:
        score -= 2.0
        reasons.append(f"question_chars>{query.max_question_length}")

    if combined_length >= query.min_combined_text_length:
        score += 0.5
        reasons.append("combined_length_ok")
    else:
        score -= 1.0
        reasons.append("combined_length_too_short")

    if query.require_questionish_title and "?" not in title:
        score -= 1.0
        reasons.append("missing_question_mark")

    blocked = [keyword for keyword in query.excluded_keywords if keyword.lower() in haystack]
    if question_contains_excluded_keywords(question_text, query.question_excluded_keywords):
        blocked.extend(query.question_excluded_keywords)
        reasons.append("question_excluded_keyword_match")
    blocked.extend(
        phrase for phrase in config.analysis_blocked_phrases
        if phrase.lower() in haystack
    )
    if blocked:
        score -= 3.0
        reasons.append(f"excluded_keywords:{', '.join(blocked)}")

    if viable_comments:
        score += 1.0
        reasons.append(f"usable_answers:{len(viable_comments)}")
    else:
        score -= 2.0
        reasons.append("no_usable_answers")

    if len(unique_authors) >= config.analysis_min_author_diversity:
        score += 1.0
        reasons.append(f"author_diversity>={config.analysis_min_author_diversity}")
    else:
        score -= 1.5
        reasons.append(f"author_diversity<{config.analysis_min_author_diversity}")

    if total_answer_chars >= config.analysis_min_total_answer_chars:
        score += 1.0
        reasons.append(f"answer_chars>={config.analysis_min_total_answer_chars}")
    else:
        score -= 1.0
        reasons.append(f"answer_chars<{config.analysis_min_total_answer_chars}")

    accepted = (
        score >= config.analysis_min_accepted_score
        and question_length <= query.max_question_length
        and not blocked
        and bool(viable_comments)
    )
    return AnalysisResult(
        accepted=accepted,
        score=round(score, 2),
        reasons=tuple(reasons),
        matched_keywords=matched_keywords,
    )


def _voiceover_text_length(text: str) -> int:
    return len(sanitize_text_for_voiceover(text))
