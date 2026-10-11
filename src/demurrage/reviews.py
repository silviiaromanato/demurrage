"""Human review of a classification: the next question, and applying answers."""

from __future__ import annotations

from typing import Any

from demurrage.models import NOT_OUR_VESSEL, TOPICS, EmailExtraction, EmailReview


def review_for(e: EmailExtraction, fleet: dict[str, Any]) -> EmailReview | None:
    """Next question: voyage?, vessel, then topics."""
    if e.extraction_status != "complete" or e.voyage_about == "no":
        return None
    if e.voyage_about == "uncertain":
        return EmailReview(
            code="VOYAGE_UNCERTAIN",
            prompt=f"Is {e.filename} about a vessel voyage?",
            options=["yes", "no"],
        )
    if e.vessel_name is None:
        vessels = [v["name"] for v in fleet.get("vessels", [])]
        return EmailReview(
            code="VESSEL_UNCERTAIN",
            prompt=f"Which vessel is {e.filename} about?",
            options=[*vessels, NOT_OUR_VESSEL],
        )
    for topic in TOPICS:
        if e.topics[topic] == "uncertain":
            return EmailReview(
                code=f"TOPIC_{topic}",
                prompt=f"Does {e.filename} discuss {topic}?",
                options=["yes", "no"],
            )
    return None


def apply_review(e: EmailExtraction, review: EmailReview) -> EmailExtraction:
    """Write a human answer onto the extraction fields."""
    if review.status != "answered" or review.answer is None:
        raise ValueError("can only apply an answered review")
    updates: dict[str, Any] = {}
    if review.code == "VOYAGE_UNCERTAIN":
        updates["voyage_about"] = review.answer
        if review.answer == "no":
            updates["topics"] = dict.fromkeys(TOPICS, "no")
    elif review.code == "VESSEL_UNCERTAIN":
        if review.answer == NOT_OUR_VESSEL:
            updates["voyage_about"] = "no"
            updates["vessel_name"] = None
            updates["topics"] = dict.fromkeys(TOPICS, "no")
        else:
            updates["vessel_name"] = review.answer
    elif review.code.startswith("TOPIC_"):
        topic = review.code.removeprefix("TOPIC_")
        updates["topics"] = {**e.topics, topic: review.answer}
    else:
        raise ValueError(f"unknown review code {review.code}")
    return e.model_copy(update=updates)


def _prior_sort_key(review: EmailReview) -> tuple[int, int | str]:
    if review.code == "VOYAGE_UNCERTAIN":
        return (0, 0)
    if review.code == "VESSEL_UNCERTAIN":
        return (1, 0)
    if review.code.startswith("TOPIC_"):
        topic = review.code.removeprefix("TOPIC_")
        return (2, TOPICS.index(topic) if topic in TOPICS else 99)
    return (3, review.code)


def review_applicable(e: EmailExtraction, review: EmailReview) -> bool:
    if review.code == "VOYAGE_UNCERTAIN":
        return e.voyage_about == "uncertain"
    if review.code == "VESSEL_UNCERTAIN":
        return e.voyage_about == "yes" and e.vessel_name is None
    if review.code.startswith("TOPIC_"):
        topic = review.code.removeprefix("TOPIC_")
        return e.topics.get(topic) == "uncertain"
    return False


def replay_reviews(e: EmailExtraction, priors: list[EmailReview]) -> EmailExtraction:
    """Re-apply earlier human answers that still match an open question."""
    for prior in sorted(priors, key=_prior_sort_key):
        if prior.status == "answered" and review_applicable(e, prior):
            e = apply_review(e, prior)
    return e


def advance_extraction(
    e: EmailExtraction,
    answered: EmailReview,
    fleet: dict[str, Any],
    answered_reviews: list[EmailReview],
) -> EmailExtraction:
    e = apply_review(e, answered)
    return e.model_copy(
        update={"answered_reviews": answered_reviews, "review": review_for(e, fleet)}
    )
