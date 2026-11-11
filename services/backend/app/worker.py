"""Single scheduled worker: durable ingestion jobs and atomic analytics releases.

Run with ``python -m app.worker``. Deployment intentionally runs one worker;
row leases also protect jobs from accidental concurrent claimers.
"""
import argparse
import logging
import os
import random
import signal
import time

from sqlalchemy import delete, or_, select, update
from .classification import discover
from .config import APP_MODE
from .db import SessionLocal, init_db
from .ingestion import SourceError, SourcePaused, StackExchange, normalize, normalize_answer
from .models import Coverage, Job, Question, Release, State, Topic

log = logging.getLogger("worker")


def get_state(session, key):
    value = session.get(State, key)
    return dict(value.value) if value else {}


def put_state(session, key, value):
    row = session.get(State, key)
    if row:
        row.value = dict(value)
    else:
        session.add(State(key=key, value=dict(value)))


def upsert(session, item, source):
    values = normalize(item, source)
    question = session.get(Question, values["id"])
    if question:
        # Late activity pages must not roll back newer observed revisions.
        if values["last_activity_at"] < question.last_activity_at:
            return question
        for key, value in values.items():
            setattr(question, key, value)
    else:
        question = Question(**values, answers=[], topic_ids=[])
        session.add(question)
    return question


def schedule(session, now):
    source = os.getenv("STACKEXCHANGE_SITE", "stackoverflow")
    hour = now // 3600 * 3600
    slot = now // int(os.getenv("POLL_INTERVAL_SECONDS", "600"))
    jobs = [(f"creation-live:{slot}", "creation", {"start": hour - 7200, "end": hour, "page": 1, "source": source, "optional": False}),
            (f"activity:{slot}", "activity", {"start": now - 7200, "end": now - 60, "page": 1, "source": source, "optional": False})]
    if not get_state(session, "backfill_scheduled"):
        days = max(1, min(395, int(os.getenv("BACKFILL_DAYS", "90"))))
        # Daily intervals, newest first, prevent a long initial backfill from
        # starving fresh source activity.
        for day in range(days):
            end = hour - day * 86400
            jobs.append((f"backfill:{end}", "creation", {"start": end - 86400, "end": end, "page": 1, "source": source, "optional": True}))
        put_state(session, "backfill_scheduled", {"at": now, "days": days})
    for key, kind, payload in jobs:
        if not session.scalar(select(Job.id).where(Job.key == key)):
            session.add(Job(key=key, kind=kind, payload=payload, status="ready", available_at=now))
    session.commit()


def claim(session, now):
    query = select(Job).where(Job.available_at <= now, or_(Job.status == "ready", (Job.status == "leased") & (Job.leased_until < now))).order_by(Job.id).with_for_update(skip_locked=True)
    # Fresh jobs have priority over initial history, independent of insert ID.
    job = session.scalar(query.where(~Job.key.startswith("backfill:")).limit(1))
    if job is None:
        job = session.scalar(query.limit(1))
    if job:
        job.status = "leased"
        job.leased_until = now + 120
        session.commit()
    return job


def process_job(session, job, client):
    payload = dict(job.payload)
    result = client.questions(payload["start"], payload["end"], payload.get("page", 1), job.kind, payload.get("optional", False))
    for item in result["items"]:
        # Provider boundaries may be inclusive; the analytic domain is [a,b).
        if job.kind == "creation" and not payload["start"] <= item.get("creation_date", 0) < payload["end"]:
            continue
        upsert(session, item, payload["source"])
    if result["has_more"]:
        if payload.get("page", 1) >= 20 and payload["end"] - payload["start"] > 2:
            # Bounded paging also works with anonymous API page limits. Split
            # into smaller fixed windows; existing rows deduplicate on replay.
            midpoint = (payload["start"] + payload["end"]) // 2
            for start, end in ((payload["start"], midpoint), (midpoint, payload["end"])):
                key = f"{job.key}:split:{start}:{end}"
                if not session.scalar(select(Job.id).where(Job.key == key)):
                    session.add(Job(key=key, kind=job.kind, payload={**payload, "start": start, "end": end, "page": 1}, status="ready", available_at=int(time.time())))
            job.status = "completed_split"
            session.commit()
            return
        payload["page"] = payload.get("page", 1) + 1
        job.payload = payload
        job.status = "ready"
        job.available_at = int(time.time()) + 1
    else:
        job.status = "completed"
        if job.kind == "creation":
            existing = session.scalar(select(Coverage.id).where(Coverage.source == payload["source"], Coverage.start == payload["start"], Coverage.end == payload["end"], Coverage.complete.is_(True)))
            if not existing:
                session.add(Coverage(source=payload["source"], start=payload["start"], end=payload["end"], complete=True))
    health = get_state(session, "source_health")
    health.update(last_success_at=int(time.time()), error=None, quota_remaining=client.state.get("quota_remaining"))
    if job.kind == "creation" and not result["has_more"]:
        health["covered_through"] = max(payload["end"], health.get("covered_through", 0))
    put_state(session, "source_health", health)
    session.commit()  # Writes, coverage, and next page advance together.


def reconcile(session, client, now):
    state = get_state(session, "reconciliation")
    interval = int(os.getenv("RECONCILE_INTERVAL_SECONDS", "3600"))
    if now - state.get("at", 0) < interval:
        return
    source = client.site
    questions = session.scalars(select(Question).where(Question.source == source).order_by(Question.updated_at, Question.id).limit(100)).all()
    if not questions:
        return
    result = client.refresh([q.source_question_id for q in questions])
    seen = set()
    for item in result["items"]:
        seen.add(item["question_id"])
        upsert(session, item, source)
    for question in questions:
        if question.source_question_id not in seen:
            question.status = "unavailable_unconfirmed"
            question.body = ""
            question.answers = []
            question.updated_at = now
    put_state(session, "reconciliation", {"at": now, "checked": len(questions)})
    session.commit()


def enrich(session, client, now):
    state = get_state(session, "answer_enrichment")
    if now - state.get("at", 0) < 600:
        return
    limit = max(0, min(20, int(os.getenv("ANSWER_ENRICHMENT_BATCH", "3"))))
    questions = session.scalars(select(Question).where(Question.answer_count > 0, Question.status.in_(["available", "closed"])).order_by(Question.updated_at).limit(limit)).all()
    for question in questions:
        result = client.answers(question.source_question_id)
        question.answers = [normalize_answer(answer) for answer in result["items"]]
        # This is deliberately a bounded sample, never an exhaustive answer set.
        question.updated_at = now
        session.commit()
        time.sleep(0.1)
    put_state(session, "answer_enrichment", {"at": now, "sampled": True, "batch": len(questions)})
    session.commit()


def publish(session, now):
    from .analytics import publish_release
    hour = now // 3600 * 3600
    state = get_state(session, "worker_publication")
    if state.get("hour") == hour:
        return
    if not session.scalar(select(Coverage.id).where(Coverage.complete.is_(True)).limit(1)):
        return
    retention = now - max(60, int(os.getenv("RETENTION_DAYS", "395"))) * 86400
    session.execute(delete(Question).where(Question.created_at < retention))
    session.execute(delete(Coverage).where(Coverage.end <= retention))
    session.execute(update(Coverage).where(Coverage.start < retention, Coverage.end > retention).values(start=retention))
    session.execute(delete(Release).where(Release.published_at < now - 7 * 86400))
    session.execute(delete(Job).where(Job.status == "completed", Job.available_at < now - 7 * 86400))
    rows = session.scalars(select(Question).where(Question.status != "unavailable_unconfirmed")).all()
    documents = [{"id": q.id, "title": q.title, "body": q.body, "tags": q.tags, "answers": q.answers} for q in rows]
    topics, memberships, provenance = discover(documents)
    unmatched = [key for key, assigned in memberships.items() if not assigned]
    if unmatched:
        topics.append({'id': 'unclassified', 'name': 'Other / Unclassified', 'slug': 'unclassified',
                       'description': 'Observed questions without a sufficiently coherent automatic topic assignment.',
                       'category': 'Other', 'tags': [], 'metadata_json': {'unclassified': True, 'method': provenance['method']}})
        for key in unmatched:
            memberships[key] = ['unclassified']
    active_ids = {topic["id"] for topic in topics}
    previous_members = {}
    for question in rows:
        for topic_id in question.topic_ids:
            previous_members.setdefault(topic_id, set()).add(question.id)
    next_members = {topic_id: {qid for qid, assigned in memberships.items() if topic_id in assigned} for topic_id in active_ids}
    for topic in session.scalars(select(Topic)).all():
        if topic.id not in active_ids:
            successors = [new_id for new_id, ids in next_members.items() if previous_members.get(topic.id, set()) & ids]
            topic.metadata_json = {**topic.metadata_json, "retired": True, "successors": sorted(successors), "retired_at": now}
    for values in topics:
        topic = session.get(Topic, values["id"])
        if topic:
            values["metadata_json"] = {**topic.metadata_json, **values["metadata_json"], "retired": False}
            for key, value in values.items():
                setattr(topic, key, value)
        else:
            values["metadata_json"]["predecessors"] = sorted(old_id for old_id, ids in previous_members.items() if old_id != values["id"] and ids & next_members[values["id"]])
            session.add(Topic(**values))
    for question in rows:
        question.topic_ids = memberships[question.id]
    for question in session.scalars(select(Question).where(Question.status == "unavailable_unconfirmed")).all():
        question.topic_ids = []
    put_state(session, "classification", provenance)
    session.flush()
    publish_release(session, as_of=hour, demo=False, taxonomy_version=provenance["version"])
    put_state(session, "worker_publication", {"hour": hour, "at": now})
    session.commit()


def run_cycle(max_pages=20):
    now = int(time.time())
    with SessionLocal() as session:
        active = get_state(session, 'active_release')
        if active:
            release = session.get(Release, active['id'])
            if release and release.demo:
                raise RuntimeError('Live ingestion refuses a demo database. Use a separate Compose project and volume.')
        schedule(session, now)
        def save_provider(value):
            put_state(session, "provider_rate", value)
            session.commit()
        client = StackExchange(get_state(session, "provider_rate"), save_provider)
        for _ in range(max_pages):
            job = claim(session, int(time.time()))
            if not job:
                break
            try:
                process_job(session, job, client)
            except (SourcePaused, SourceError) as exc:
                session.rollback()
                job = session.get(Job, job.id)
                job.status = "ready"
                if isinstance(exc, SourcePaused):
                    job.available_at = exc.until
                else:
                    job.attempts += 1
                    job.available_at = int(time.time()) + min(3600, 30 * 2 ** min(job.attempts, 6)) + random.randint(0, 15)
                    if job.attempts >= 10:
                        job.status = "dead_letter"
                health = get_state(session, "source_health")
                health.update(error=str(exc), quota_remaining=client.state.get("quota_remaining"))
                put_state(session, "source_health", health)
                session.commit()
                log.warning("Acquisition paused: %s", exc)
                if not job.payload.get("optional"):
                    break
            time.sleep(0.1)
        for task in (reconcile, enrich):
            try:
                task(session, client, now)
            except (SourcePaused, SourceError) as exc:
                session.rollback()
                log.info("Optional source processing paused: %s", exc)
        publish(session, now)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s %(message)s")
    init_db()
    if APP_MODE == "demo":
        log.info("Demo mode: worker does not acquire live data; API seeds an explicitly labelled fixture.")
        if args.once:
            return
        while True:
            time.sleep(30)
    stopped = False
    def stop(*_):
        nonlocal stopped
        stopped = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    while not stopped:
        try:
            run_cycle()
        except Exception:
            log.exception("Worker cycle failed; previous published release remains available")
            if args.once:
                raise SystemExit(1)
        if args.once:
            break
        # Frequent queue servicing; scheduler deduplicates ten-minute slots.
        for _ in range(15):
            if stopped:
                break
            time.sleep(1)


if __name__ == "__main__":
    main()
