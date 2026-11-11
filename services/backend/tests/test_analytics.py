import time
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient
from app.analytics import Analysis, covered, growth, publish_release
from app.db import Base, get_session
from app.models import Coverage, Question, State, Topic
from app.main import app


@pytest.fixture
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        yield session
    engine.dispose()


def setup_data(session, previous=40, current=60, complete=True):
    end = int(time.time()) // 3600 * 3600
    session.add_all([Topic(id="python", name="Python dependency problems", slug="python", description="Package dependency resolution", category="Python", tags=["pip"]),
                     Topic(id="packages", name="Package management", slug="packages", description="Resolving dependencies", category="Tools", tags=["pip"])])
    for i in range(previous + current):
        created = end - 8 * 86400 if i < previous else end - 86400
        session.add(Question(id=f"stackoverflow:{i}", source="stackoverflow", source_question_id=i, title=f"Question {i}", body="Safe plain text",
            tags=["python", "pip"], created_at=created, last_activity_at=created, owner={"display_name": "Author"},
            content_license="CC BY-SA 4.0", url=f"https://stackoverflow.com/questions/{i}", topic_ids=["python", "packages"]))
    session.add(Coverage(source="stackoverflow", start=end - 60 * 86400, end=end, complete=complete))
    session.flush()
    release = publish_release(session, end)
    session.commit()
    return end, release


def test_growth_contract():
    assert growth(60, 40, True) == {"absolute_growth": 20, "relative_growth_pct": 50.0, "growth_state": "growing"}
    assert growth(12, 0, True)["growth_state"] == "newly_active"
    assert growth(12, 0, True)["relative_growth_pct"] is None
    assert growth(0, 12, True)["relative_growth_pct"] == -100
    assert growth(12, 0, False)["growth_state"] == "insufficient_history"
    assert growth(0, 0, True)["growth_state"] == "no_activity"


def test_coverage_requires_contiguous_intervals():
    spans = [{"source": "stackoverflow", "start": 0, "end": 10, "complete": True},
             {"source": "stackoverflow", "start": 11, "end": 20, "complete": True}]
    assert not covered(spans, 0, 20)
    spans[1]["start"] = 10
    assert covered(spans, 0, 20)
    assert not covered(spans, 0, 21)


def test_distinct_overlapping_topics_and_immutable_release(session):
    end, release = setup_data(session)
    a = Analysis(release, end - 7 * 86400, end)
    assert len(a.questions) == 100
    assert a.row(a.topics[0])["current_count"] == 60
    assert a.row(a.topics[1])["current_count"] == 60
    question = session.get(Question, "stackoverflow:0")
    question.topic_ids = []
    question.created_at = end - 10
    session.commit()
    assert Analysis(release, end - 7 * 86400, end).row(a.topics[0])["current_count"] == 60


def test_missing_history_never_zero_filled(session):
    end, release = setup_data(session, 0, 0, False)
    a = Analysis(release, end - 7 * 86400, end)
    assert a.row(a.topics[0])["absolute_growth"] is None
    assert all(point["count"] is None for point in a.history(a.questions))


def test_half_open_windows(session):
    end, release = setup_data(session, 0, 0)
    for i, created in enumerate([end - 14 * 86400, end - 7 * 86400, end - 1, end]):
        session.add(Question(id=f"stackoverflow:b{i}", source="stackoverflow", source_question_id=1000+i,
            title="Boundary", body="", created_at=created, last_activity_at=created, topic_ids=["python"]))
    session.flush()
    release = publish_release(session, end)
    a = Analysis(release, end - 7 * 86400, end)
    row = a.row(next(t for t in a.topics if t["id"] == "python"))
    assert row["previous_count"] == 1
    assert row["current_count"] == 2


@pytest.fixture
def client(session):
    app.dependency_overrides[get_session] = lambda: session
    # No context manager: isolate startup/demo seed from this test database.
    client = TestClient(app)
    yield client
    app.dependency_overrides.clear()


def test_api_no_release_and_validation(client, session):
    assert client.get("/api/v1/status").json()["available"] is False
    assert client.get("/api/v1/dashboard").status_code == 503
    setup_data(session)
    assert client.get("/api/v1/dashboard?period=2d").status_code == 422
    assert client.get("/api/v1/topics?limit=1000").status_code == 422
    assert client.get("/api/v1/dashboard?from=2026-01-01&to=2026-01-02").status_code == 422
    assert client.get("/api/v1/dashboard?release_id=missing").status_code == 410


def test_api_dashboard_and_search(client, session):
    setup_data(session)
    body = client.get("/api/v1/dashboard").json()
    assert body["summary"]["new_questions"] == 60
    assert body["summary"]["growing_topics"] == 2
    assert body["topics"][0]["absolute_growth"] == 20
    assert client.get("/api/v1/topics?q=python%20dependency").json()["total"] == 1
    assert client.get("/api/v1/topics?q=nonexistent").json()["topics"] == []


def test_pagination_is_filter_and_release_bound(client, session):
    setup_data(session)
    body = client.get("/api/v1/topics?limit=1").json()
    cursor = body["next_cursor"]
    assert cursor
    assert client.get("/api/v1/topics", params={"limit": 1, "cursor": cursor}).status_code == 200
    assert client.get("/api/v1/topics", params={"limit": 1, "cursor": cursor, "q": "python"}).status_code == 422


def test_removed_source_suppresses_pinned_preview(client, session):
    end, release = setup_data(session)
    q = session.get(Question, "stackoverflow:0")
    q.status = "unavailable_unconfirmed"
    session.commit()
    response = client.get(f"/api/v1/questions/stackoverflow/0?release_id={release.id}")
    assert response.status_code == 200
    public = response.json()["question"]
    assert public["excerpt"] == public["url"] == ""
    assert public["title"] == "Source question unavailable"


def test_partial_growth_not_ranked(client, session):
    setup_data(session, complete=False)
    body = client.get("/api/v1/dashboard").json()
    assert body["meta"]["coverage_state"] == "partial"
    assert body["topics"] == []
    assert body["summary"]["new_questions"] == 60


def test_descriptive_search_and_unknown_query(client, session):
    setup_data(session)
    assert client.get('/api/v1/topics?q=python%20dependencies%20problems').json()['total'] == 1
    assert client.get('/api/v1/topics?unexpected=yes').status_code == 422


def test_snapshot_capacity_preserves_previous_release(session, monkeypatch):
    _, release = setup_data(session)
    monkeypatch.setenv('MAX_SNAPSHOT_QUESTIONS', '1')
    with pytest.raises(ValueError, match='capacity'):
        publish_release(session)
    assert session.get(State, 'active_release').value['id'] == release.id


def test_file_credentials_override_environment(tmp_path, monkeypatch):
    from app.config import env
    secret_file = tmp_path / 'key'
    secret_file.write_text('test-secret\n', encoding='utf-8')
    monkeypatch.setenv('TEST_CREDENTIAL', 'incorrect')
    monkeypatch.setenv('TEST_CREDENTIAL_FILE', str(secret_file))
    assert env('TEST_CREDENTIAL') == 'test-secret'


def test_retired_topics_are_not_ranked(client, session):
    _, release = setup_data(session)
    session.get(Topic, 'python').metadata_json = {'retired': True}
    publish_release(session)
    session.commit()
    assert all(row['id'] != 'python' for row in client.get('/api/v1/topics').json()['topics'])
