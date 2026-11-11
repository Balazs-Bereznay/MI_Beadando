"""Explicit synthetic fixtures, never loaded in live mode."""
import random
import time
from sqlalchemy import select
from .analytics import publish_release
from .models import Coverage, Question, Release, State, Topic

DEMO_TOPICS = [
    ("python-dependencies", "Python dependency management", "Python", ["python", "pip", "poetry"], "Resolving package conflicts, virtual environments, and reproducible Python builds.", 40, 60),
    ("react-state", "React state & rendering", "Frontend", ["reactjs", "javascript", "state"], "Understanding component state, effects, and unexpected rendering behavior.", 32, 61),
    ("typescript-types", "TypeScript type inference", "Languages", ["typescript", "generics"], "Working through generic constraints, inferred types, and type-safe interfaces.", 25, 43),
    ("docker-networking", "Container networking", "Infrastructure", ["docker", "networking"], "Connecting services across containers, resolving DNS, and configuring ports.", 21, 47),
    ("postgres-performance", "PostgreSQL query performance", "Databases", ["postgresql", "sql", "indexing"], "Investigating slow queries, index selection, and execution plans.", 31, 42),
    ("auth-tokens", "Authentication & token refresh", "Security", ["oauth", "jwt", "authentication"], "Managing login sessions, token expiration, and authorization flows.", 18, 25),
    ("llm-streaming", "Streaming AI responses", "AI & ML", ["streaming", "python", "llm"], "Handling streamed tokens, connection lifecycles, and incremental responses.", 0, 12),
    ("rust-ownership", "Rust ownership & lifetimes", "Languages", ["rust", "borrow-checker"], "Understanding borrowed values, ownership boundaries, and compiler lifetime errors.", 25, 20),
    ("ci-builds", "Continuous integration builds", "Infrastructure", ["github-actions", "ci"], "Debugging workflow environments, build caching, and failing pipelines.", 15, 15),
]


def seed_demo(session):
    active = session.get(State, "active_release")
    if active:
        existing = session.get(Release, active.value["id"])
        if existing and not existing.demo:
            raise RuntimeError("Refusing to put demo data into a live database")
        return
    if session.scalar(select(Question.id).limit(1)):
        raise RuntimeError("Demo mode requires an empty or existing demo database")
    end = int(time.time()) // 3600 * 3600
    rng = random.Random(42)
    number = 1
    for slug, name, category, tags, description, previous, current in DEMO_TOPICS:
        session.add(Topic(id=slug, slug=slug, name=name, description=description, category=category, tags=tags,
                          metadata_json={"method": "synthetic-demo"}))
        for low, high, count in [(end - 90 * 86400, end - 14 * 86400, 110),
                                 (end - 14 * 86400, end - 7 * 86400, previous),
                                 (end - 7 * 86400, end, current)]:
            for i in range(count):
                created = rng.randrange(low, high)
                answered = i % 3 != 0
                accepted = i % 5 == 0 and answered
                session.add(Question(id=f"stackoverflow:{number}", source="stackoverflow", source_question_id=number,
                    title=f"{name}: {['unexpected behavior after an upgrade', 'configuration differs between environments', 'how to debug this workflow', 'handling an edge case'][i % 4]}",
                    body="This is a synthetic example for exploring the interface. It is not a real Stack Overflow question. Configure live mode to collect actual source evidence.",
                    tags=tags, created_at=created, last_activity_at=min(end - 1, created + 3600 * (i % 24)),
                    answer_count=2 if answered else 0, accepted_answer_id=number * 10 if accepted else None,
                    is_answered=answered, score=rng.randrange(-1, 26), url="", owner={"display_name": "Demo contributor"},
                    content_license="Synthetic demo", status="available", answers=[], topic_ids=[slug], updated_at=end))
                number += 1
    session.add(Coverage(source="stackoverflow", start=end - 90 * 86400, end=end, complete=True))
    session.flush()
    publish_release(session, as_of=end, demo=True, taxonomy_version="demo-1")
