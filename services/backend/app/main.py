import base64
import hashlib
import json
import time
from contextlib import asynccontextmanager
from datetime import datetime
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from sqlalchemy import select, text
from .analytics import Analysis, iso, sort_topics
from .config import APP_MODE
from .db import get_session, init_db, SessionLocal
from .models import Question, Release, State
from .search import matches


@asynccontextmanager
async def lifespan(app):
    init_db()
    if APP_MODE == "demo":
        from .seed import seed_demo
        with SessionLocal() as session:
            seed_demo(session)
            session.commit()
    yield


app = FastAPI(title="Developer Trends API", version="1.0.0", lifespan=lifespan)


@app.middleware("http")
async def security_headers(request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/health")
@app.get("/healthz")
def health(session=Depends(get_session)):
    session.execute(text("SELECT 1"))
    return {"status": "ok"}


def get_release(session, release_id=None):
    active = session.get(State, "active_release")
    if release_id:
        release = session.get(Release, release_id)
        if not release:
            raise HTTPException(410, "This release is unavailable. Refresh to the current dataset.")
    else:
        release = session.get(Release, active.value["id"]) if active else None
    if not release:
        raise HTTPException(503, "No published dataset yet. Source ingestion is preparing the first update.")
    if release.demo != (APP_MODE == "demo"):
        raise HTTPException(503, "Dataset mode does not match application mode. Use a separate database for demo data.")
    return release


def choice(request, name, values, default):
    value = request.query_params.get(name, default)
    if value not in values:
        raise HTTPException(422, f"Invalid {name}; expected one of: {', '.join(values)}")
    return value


def analysis(request, session):
    allowed = {'source', 'release_id', 'period', 'from', 'to', 'category', 'sort', 'q',
               'growth', 'activity', 'cursor', 'limit', 'scope'}
    unknown = set(request.query_params) - allowed
    if unknown:
        raise HTTPException(422, 'Unknown query parameter: ' + ', '.join(sorted(unknown)))
    source = choice(request, "source", ["stackoverflow"], "stackoverflow")
    release = get_release(session, request.query_params.get("release_id"))
    period = choice(request, "period", ["24h", "7d", "30d"], "7d")
    start_value, end_value = request.query_params.get("from"), request.query_params.get("to")
    if start_value or end_value:
        if not start_value or not end_value or "period" in request.query_params:
            raise HTTPException(422, "Supply both from and to, without period.")
        try:
            dates = [datetime.fromisoformat(v.replace("Z", "+00:00")) for v in (start_value, end_value)]
            if any(d.tzinfo is None for d in dates):
                raise ValueError()
            start, end = [int(d.timestamp()) for d in dates]
        except ValueError:
            raise HTTPException(422, "Dates must be ISO timestamps with timezone.")
        if end <= start or end - start > 366 * 86400 or end > release.as_of:
            raise HTTPException(422, "Range must be positive, at most 366 days, and end by the data boundary.")
    else:
        end = release.as_of
        start = end - {"24h": 86400, "7d": 7 * 86400, "30d": 30 * 86400}[period]
    return Analysis(release, start, end, source, request.query_params.get("category") or None)


def page(request, items, release_id):
    try:
        limit = int(request.query_params.get("limit", "24"))
        if not 1 <= limit <= 100:
            raise ValueError()
    except ValueError:
        raise HTTPException(422, "Limit must be between 1 and 100.")
    query = sorted((k, v) for k, v in request.query_params.items() if k not in ("cursor", "release_id"))
    signature = hashlib.sha256(json.dumps([request.url.path, release_id, query]).encode()).hexdigest()[:20]
    offset = 0
    cursor = request.query_params.get("cursor")
    if cursor:
        try:
            payload = json.loads(base64.urlsafe_b64decode(cursor.encode()))
            offset = payload["offset"]
            if payload["signature"] != signature or not isinstance(offset, int) or offset < 0:
                raise ValueError()
        except Exception:
            raise HTTPException(422, "Cursor does not match this release and filter selection.")
    selected = items[offset:offset + limit]
    next_cursor = None
    if offset + limit < len(items):
        next_cursor = base64.urlsafe_b64encode(json.dumps({"offset": offset + limit, "signature": signature}).encode()).decode()
    return selected, next_cursor


@app.get("/api/v1/status")
def status(response: Response, session=Depends(get_session)):
    active = session.get(State, "active_release")
    release = session.get(Release, active.value["id"]) if active else None
    if release and release.demo != (APP_MODE == "demo"):
        release = None
    state = session.get(State, "source_health")
    provider = state.value if state else {}
    coverage = release.snapshot["coverage"] if release else []
    available = release is not None
    freshness = ("outdated" if time.time() - release.as_of > 5400 else "current") if release else "unavailable"
    response.headers["ETag"] = '"' + (release.id if release else "unavailable") + '"'
    return {"available": available, "release_id": release.id if release else None,
            "as_of": iso(release.as_of) if release else None, "published_at": iso(release.published_at) if release else None,
            "last_success_at": iso(provider.get("last_success_at")),
            "coverage_start": iso(min((c["start"] for c in coverage if c["complete"]), default=None)),
            "coverage_end": iso(max((c["end"] for c in coverage if c["complete"]), default=None)),
            "freshness_state": freshness, "source": "stackoverflow", "source_health": "error" if provider.get("error") else "ok" if available else "pending",
            "demo": APP_MODE == "demo", "message": "Synthetic demonstration dataset" if APP_MODE == "demo" else "Source update delayed; last published data remains available." if provider.get("error") else "Regularly updated from the public Stack Exchange API." if available else "Waiting for the first source update.",
            "categories": sorted({t["category"] for t in release.snapshot["topics"]}) if release else []}


@app.get("/api/v1/dashboard")
def dashboard(request: Request, session=Depends(get_session)):
    a = analysis(request, session)
    sort = choice(request, "sort", ["absolute", "relative", "volume", "activity", "name"], "absolute")
    rows = [a.row(t) for t in a.topics if not t.get('metadata_json', {}).get('unclassified')]
    growing = [r for r in rows if r["absolute_growth"] is not None and r["absolute_growth"] > 0]
    ranked = sort_topics([r for r in growing if sort != "relative" or r["relative_growth_pct"] is not None], sort)
    emerging = sort_topics([r for r in rows if r["growth_state"] == "newly_active"], "volume")
    return {"meta": a.meta(), "summary": {"new_questions": sum(a.start <= q["created_at"] < a.end for q in a.questions),
            "growing_topics": len(growing), "fastest_growing_topic": ranked[0] if ranked else None,
            "newly_active_topics": len(emerging)}, "topics": ranked[:10], "emerging_topics": emerging[:6], "history": a.history(a.questions)}


@app.get("/api/v1/topics")
def topics(request: Request, session=Depends(get_session)):
    a = analysis(request, session)
    sort = choice(request, "sort", ["absolute", "relative", "volume", "activity", "name"], "absolute")
    growth_filter = choice(request, "growth", ["all", "growing", "declining", "newly_active", "stable"], "all")
    activity = choice(request, "activity", ["all", "active", "inactive"], "all")
    q = request.query_params.get("q", "").casefold().strip()[:300]
    rows = [a.row(t) for t in a.topics]
    if q:
        rows = [r for r in rows if matches(q, r)]
    if growth_filter != "all":
        rows = [r for r in rows if r["growth_state"] == growth_filter]
    if activity != "all":
        rows = [r for r in rows if (r["current_count"] > 0) == (activity == "active")]
    rows = sort_topics(rows, sort)
    selected, cursor = page(request, rows, a.release.id)
    return {"meta": a.meta(), "topics": selected, "total": len(rows), "next_cursor": cursor,
            "categories": sorted({t["category"] for t in a.release.snapshot["topics"]})}


def find_topic(a, topic_id):
    topic = next((t for t in a.release.snapshot["topics"] if t["id"] == topic_id), None)
    if not topic:
        raise HTTPException(404, "Topic not found.")
    if topic.get('metadata_json', {}).get('retired'):
        raise HTTPException(410, 'This topic definition has been retired. Search Explore Topics for its current replacements; earlier published releases preserve the former definition.')
    return topic


@app.get("/api/v1/topics/{topic_id}")
def topic_detail(topic_id: str, request: Request, session=Depends(get_session)):
    a = analysis(request, session)
    topic = find_topic(a, topic_id)
    qs = a.topic_questions(topic_id)
    period_qs = [q for q in qs if a.start <= q["created_at"] < a.end]
    memberships = {t for q in qs for t in q["topic_ids"] if t != topic_id}
    related = [t for t in a.topics if t["id"] in memberships or (t["id"] != topic_id and set(t["tags"]) & set(topic["tags"]))]
    return {"meta": a.meta(), "topic": a.row(topic), "answer_stats": {
        "no_answers": sum(q["answer_count"] == 0 for q in period_qs),
        "with_answers": sum(q["answer_count"] > 0 for q in period_qs),
        "accepted": sum(q["accepted_answer_id"] is not None for q in period_qs),
        "source_answered": sum(bool(q["is_answered"]) for q in period_qs)},
        "related_topics": [a.row(t) for t in related[:4]],
        "taxonomy_note": f"Both periods use topic definition {a.release.taxonomy_version}. History is recalculated when definitions change.",
        "lineage": topic.get('metadata_json', {})}


@app.get("/api/v1/topics/{topic_id}/history")
def topic_history(topic_id: str, request: Request, session=Depends(get_session)):
    a = analysis(request, session)
    find_topic(a, topic_id)
    return {"meta": a.meta(), "history": a.history(a.topic_questions(topic_id))}


def public_question(q, session):
    # Current suppression overrides immutable historical content snapshots.
    current = session.get(Question, q["id"])
    suppressed = current is not None and current.status in ("unavailable_unconfirmed", "deleted", "removed")
    has_attribution = bool(q.get("content_license") and q.get("owner", {}).get("display_name"))
    result = {k: q[k] for k in ("id", "source", "source_question_id", "title", "tags", "answer_count", "accepted_answer_id", "is_answered", "score", "url", "owner", "content_license", "status")}
    result.update(created_at=iso(q["created_at"]), last_activity_at=iso(q["last_activity_at"]),
                  excerpt=q["body"][:1200] if has_attribution and not suppressed else "",
                  answers=[answer for answer in q["answers"] if answer.get("content_license") and answer.get("owner", {}).get("display_name")] if not suppressed else [])
    if suppressed:
        result.update(status=current.status, title="Source question unavailable", url="", tags=[])
    return result


@app.get("/api/v1/topics/{topic_id}/questions")
def topic_questions(topic_id: str, request: Request, session=Depends(get_session)):
    a = analysis(request, session)
    find_topic(a, topic_id)
    sort = choice(request, "sort", ["newest", "oldest", "activity", "score"], "newest")
    scope = choice(request, "scope", ["current", "all"], "current")
    query = request.query_params.get("q", "").casefold().strip()[:300]
    qs = [q for q in a.topic_questions(topic_id) if (scope == "all" or a.start <= q["created_at"] < a.end)
          and (not query or query in q["title"].casefold())]
    key = {"newest": "created_at", "oldest": "created_at", "activity": "last_activity_at", "score": "score"}[sort]
    qs.sort(key=lambda q: (q[key], q["id"]), reverse=sort != "oldest")
    selected, cursor = page(request, qs, a.release.id)
    return {"meta": a.meta(), "questions": [public_question(q, session) for q in selected], "total": len(qs), "next_cursor": cursor}


@app.get("/api/v1/questions/{source}/{question_id}")
def question_preview(source: str, question_id: int, request: Request, session=Depends(get_session)):
    a = analysis(request, session)
    q = next((q for q in a.release.snapshot["questions"] if q["source"] == source and q["source_question_id"] == question_id), None)
    if not q:
        raise HTTPException(404, "Question not found.")
    return {"meta": a.meta(), "question": public_question(q, session)}


@app.get("/api/v1/methodology")
def methodology():
    return {"title": "Understand the signal", "definitions": {
        "question_activity": "Counts newly created questions, not unique defects or technology reliability.",
        "absolute_growth": "Current-period questions minus preceding equal-period questions.",
        "relative_growth": "100 × (current − previous) / previous; unavailable when previous is zero or coverage is incomplete.",
        "newly_active": "Current activity with a verified zero baseline in a covered comparison period.",
        "topics": "Automatically generated interpretations of related source questions; a question may belong to multiple topics.",
        "windows": "UTC half-open intervals, ending at the release's displayed completed-hour boundary.",
        "example": "40 previous questions and 60 current questions produce +20 questions and +50%."},
        "limitations": ["Public API observations are not a complete historical archive.", "Missing coverage is never treated as zero activity.",
            "Answer status reflects the last observation, not proof of a universally solved problem.",
            "Topic definitions can evolve; both comparison periods use one version.",
            "Initial classifier quality and capacity require evaluation before production decisions."],
        "source_url": "https://api.stackexchange.com/docs", "license_url": "https://stackoverflow.com/help/licensing"}
