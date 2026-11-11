"""Release-pinned question facts. Publication never mixes taxonomies or windows."""
import time
import uuid
import os
from datetime import datetime, timezone
from sqlalchemy import select, func
from .models import Coverage, Question, Release, State, Topic


def iso(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat().replace("+00:00", "Z") if value is not None else None


def question_fact(q):
    return {key: getattr(q, key) for key in (
        "id", "source", "source_question_id", "title", "body", "tags", "created_at",
        "last_activity_at", "answer_count", "accepted_answer_id", "is_answered", "score", "url",
        "owner", "content_license", "status", "answers", "topic_ids", "updated_at")}


def publish_release(session, as_of=None, demo=False, taxonomy_version="1"):
    """Build then atomically activate a complete snapshot; caller owns transaction."""
    now = int(time.time())
    end = int(as_of if as_of is not None else now // 3600 * 3600)
    limit = int(os.getenv('MAX_SNAPSHOT_QUESTIONS', '100000'))
    count = session.scalar(select(func.count()).select_from(Question).where(Question.created_at < end))
    if count > limit:
        raise ValueError('Dataset exceeds configured snapshot capacity; previous release preserved. Increase capacity only after profiling or migrate to relational release facts.')
    questions = [question_fact(q) for q in session.scalars(select(Question).where(Question.created_at < end))]
    topics = [{k: getattr(t, k) for k in ("id", "name", "slug", "description", "category", "tags", "metadata_json")}
              for t in session.scalars(select(Topic))]
    coverage = [{"source": c.source, "start": c.start, "end": c.end, "complete": c.complete}
                for c in session.scalars(select(Coverage))]
    topic_ids = {t["id"] for t in topics}
    if any(not set(q["topic_ids"]).issubset(topic_ids) for q in questions):
        raise ValueError("Cannot publish dangling topic membership")
    release = Release(id=str(uuid.uuid4()), as_of=end, published_at=now,
                      taxonomy_version=str(taxonomy_version), demo=demo,
                      snapshot={"questions": questions, "topics": topics, "coverage": coverage})
    session.add(release)
    session.flush()
    active = session.get(State, "active_release")
    if active is None:
        session.add(State(key="active_release", value={"id": release.id}))
    else:
        active.value = {"id": release.id}
    session.flush()
    return release


def covered(intervals, start, end, source="stackoverflow"):
    cursor = start
    for interval in sorted(intervals, key=lambda x: x["start"]):
        if not interval["complete"] or interval["source"] != source or interval["end"] <= cursor:
            continue
        if interval["start"] > cursor:
            return False
        cursor = max(cursor, interval["end"])
        if cursor >= end:
            return True
    return cursor >= end


def growth(current, previous, valid):
    if not valid:
        return {"absolute_growth": None, "relative_growth_pct": None, "growth_state": "insufficient_history"}
    absolute = current - previous
    state = "newly_active" if previous == 0 and current > 0 else (
        "no_activity" if current == previous == 0 else "growing" if absolute > 0 else "declining" if absolute < 0 else "stable")
    return {"absolute_growth": absolute, "relative_growth_pct": round(100 * absolute / previous, 2) if previous else None,
            "growth_state": state}


class Analysis:
    def __init__(self, release, start, end, source="stackoverflow", category=None):
        self.release, self.start, self.end, self.source = release, start, end, source
        self.previous_start = start - (end - start)
        self.coverage = release.snapshot["coverage"]
        self.valid = covered(self.coverage, self.previous_start, end, source)
        self.topics = [t for t in release.snapshot["topics"] if not t.get('metadata_json', {}).get('retired')
                       and (not category or t["category"] == category)]
        ids = {t["id"] for t in self.topics}
        self.questions = [q for q in release.snapshot["questions"] if q["source"] == source
                          and q["status"] not in ("removed", "deleted")
                          and (not category or ids.intersection(q["topic_ids"]))]

    def meta(self):
        limitations = []
        if not self.valid:
            limitations.append("One or both comparison periods have incomplete observed coverage; growth is withheld.")
        if self.release.demo:
            limitations.append("Demonstration dataset: synthetic questions and statistics, not Stack Overflow observations.")
        limitations.append("Questions may belong to multiple topics; topic totals overlap.")
        if not self.release.demo:
            limitations.append('Automatic topic assignments are provisional and have not passed a corpus-level quality evaluation.')
        return {"release_id": self.release.id, "taxonomy_version": self.release.taxonomy_version,
                "as_of": iso(self.release.as_of), "published_at": iso(self.release.published_at),
                "current_window": {"start": iso(self.start), "end": iso(self.end)},
                "previous_window": {"start": iso(self.previous_start), "end": iso(self.start)},
                "coverage_state": "complete" if self.valid else "partial",
                "freshness_state": "outdated" if time.time() - self.release.as_of > 5400 else "current",
                "limitations": limitations, "demo": self.release.demo}

    def topic_questions(self, topic_id):
        return [q for q in self.questions if topic_id in q["topic_ids"]]

    def history(self, questions, both=True):
        start = self.previous_start if both else self.start
        step = 3600 if self.end - self.start <= 86400 else 86400
        points = []
        pos = start
        # Pre-count each fact once rather than repeatedly scanning per bucket.
        counts = {}
        for q in questions:
            if start <= q["created_at"] < self.end:
                bucket = (q["created_at"] - start) // step
                counts[bucket] = counts.get(bucket, 0) + 1
        while pos < self.end:
            complete = covered(self.coverage, pos, min(pos + step, self.end), self.source)
            observed = counts.get((pos - start) // step, 0)
            points.append({"date": iso(pos), "count": observed if complete or observed else None,
                           "coverage_state": "complete" if complete else "partial"})
            pos += step
        return points

    def row(self, topic):
        qs = self.topic_questions(topic["id"])
        current = sum(self.start <= q["created_at"] < self.end for q in qs)
        previous = sum(self.previous_start <= q["created_at"] < self.start for q in qs)
        return {k: topic[k] for k in ("id", "name", "slug", "description", "category", "tags")} | {
            "current_count": current, "previous_count": previous, **growth(current, previous, self.valid),
            "total_count": len(qs), "last_activity_at": iso(max((q["last_activity_at"] for q in qs), default=None)),
            "small_baseline": 0 < previous < 10, "history": self.history(qs, both=False)}


def sort_topics(rows, sort):
    if sort == "name":
        return sorted(rows, key=lambda t: (t["name"].casefold(), t["id"]))
    if sort == "activity":
        return sorted(rows, key=lambda t: (t["last_activity_at"] or "", t["id"]), reverse=True)
    key = {"absolute": "absolute_growth", "relative": "relative_growth_pct", "volume": "current_count"}[sort]
    return sorted(rows, key=lambda t: (-(t[key] if t[key] is not None else float("-inf")), -t["current_count"], t["id"]))
