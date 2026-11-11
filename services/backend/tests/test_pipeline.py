import unittest
import gzip
import io
import json
from unittest.mock import patch
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.classification import discover, plain_text, tokens
from app.db import Base
from app.ingestion import SourceError, SourcePaused, StackExchange, normalize
from app.models import Coverage, Job, Question, Release
from app.worker import process_job, upsert, publish


def item(number=1, created=150, activity=160):
    return {"question_id": number, "creation_date": created, "last_activity_date": activity,
            "title": "Python dependency resolution with pip", "body": "<p>Dependency version conflict installing packages.</p>",
            "tags": ["python", "pip"], "answer_count": 1, "is_answered": True,
            "link": f"https://stackoverflow.com/questions/{number}", "owner": {"display_name": "Author"}, "content_license": "CC BY-SA 4.0"}


class ProviderTests(unittest.TestCase):
    def test_gzip_source_response_is_decoded(self):
        payload = {"items": [], "has_more": False}
        response = io.BytesIO(gzip.compress(json.dumps(payload).encode()))
        response.headers = {"Content-Encoding": "gzip"}
        with patch("urllib.request.urlopen", return_value=response):
            self.assertEqual(StackExchange._http("https://api.stackexchange.com/2.3/questions"), payload)

    def test_optional_quota_reserve_resets_next_day(self):
        client = StackExchange({"quota_day": 1, "quota_remaining": 100}, clock=lambda: 86400 * 2 + 1,
                               transport=lambda _: {"items": [], "has_more": False, "quota_remaining": 9999})
        client.questions(1, 2, optional=True)
        self.assertEqual(client.state["quota_remaining"], 9999)

    def test_short_technical_tokens_survive(self):
        self.assertEqual(tokens("C# C++ Go R"), ["c#", "c++", "go", "r"])

    def test_backoff_survives_new_client(self):
        saved = []
        client = StackExchange(save_state=lambda s: saved.append(dict(s)), clock=lambda: 100, transport=lambda url: {"items": [], "has_more": False, "backoff": 30, "quota_remaining": 500})
        client.questions(1, 2)
        with self.assertRaises(SourcePaused):
            StackExchange(saved[-1], clock=lambda: 110).questions(2, 3)

    def test_activity_uses_activity_bounds_not_creation_filter(self):
        urls = []
        client = StackExchange(transport=lambda url: urls.append(url) or {"items": [], "has_more": False})
        client.questions(100, 200, mode="activity")
        self.assertIn("min=100", urls[0])
        self.assertNotIn("fromdate", urls[0])

    def test_duplicate_requests_paused(self):
        client = StackExchange(clock=lambda: 100, transport=lambda _: {"items": [], "has_more": False})
        client.questions(1, 2)
        with self.assertRaises(SourcePaused):
            client.questions(1, 2)

    def test_optional_requests_preserve_quota(self):
        with patch.dict("os.environ", {"QUOTA_RESERVE": "200"}):
            with self.assertRaises(SourcePaused):
                StackExchange({"quota_remaining": 100}).questions(1, 2, optional=True)

    def test_status_fields_are_independent_and_html_removed(self):
        record = normalize(item())
        self.assertEqual(record["answer_count"], 1)
        self.assertTrue(record["is_answered"])
        self.assertIsNone(record["accepted_answer_id"])
        self.assertNotIn("<p>", record["body"])
        self.assertEqual(plain_text("<script>bad()</script><p>Hello &amp; world</p>"), "Hello & world")


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    def test_pagination_checkpoints_and_replay_are_atomic(self):
        job = Job(key="test", kind="creation", payload={"start": 100, "end": 200, "page": 1, "source": "stackoverflow"})
        self.session.add(job)
        self.session.commit()
        class Client:
            state = {"quota_remaining": 1000}
            def questions(self, *args):
                return {"items": [item()], "has_more": args[2] == 1}
        process_job(self.session, job, Client())
        self.assertEqual(job.payload["page"], 2)
        self.assertEqual(self.session.scalars(select(Coverage)).all(), [])
        process_job(self.session, job, Client())
        self.assertEqual(job.status, "completed")
        self.assertEqual(len(self.session.scalars(select(Question)).all()), 1)
        self.assertEqual(len(self.session.scalars(select(Coverage)).all()), 1)

    def test_late_revision_does_not_overwrite_newer_data(self):
        upsert(self.session, item(activity=190), "stackoverflow")
        self.session.commit()
        old = item(activity=170)
        old["title"] = "stale title"
        upsert(self.session, old, "stackoverflow")
        self.session.commit()
        self.assertEqual(self.session.get(Question, "stackoverflow:1").last_activity_at, 190)
        self.assertNotEqual(self.session.get(Question, "stackoverflow:1").title, "stale title")

    def test_boundary_question_is_not_counted_in_preceding_interval(self):
        job = Job(key="boundary", kind="creation", payload={"start": 100, "end": 200, "page": 1, "source": "stackoverflow"})
        self.session.add(job)
        self.session.commit()
        class Client:
            state = {}
            def questions(self, *args):
                return {"items": [item(created=200)], "has_more": False}
        process_job(self.session, job, Client())
        self.assertIsNone(self.session.get(Question, "stackoverflow:1"))

    def test_classifier_uses_content_and_is_reproducible(self):
        rows = [normalize(item(number=number)) for number in range(1, 4)]
        unrelated = normalize(item(number=9))
        unrelated.update(title="Rendering polygon meshes in a window", body="OpenGL shaders vertices triangles display", tags=["python", "opengl"])
        rows.append(unrelated)
        with patch.dict("os.environ", {"TOPIC_MODEL": ""}):
            topics, memberships, provenance = discover(rows)
            reverse = discover(list(reversed(rows)))
        self.assertTrue(topics)
        self.assertTrue(memberships["stackoverflow:1"])
        self.assertEqual(memberships["stackoverflow:9"], [])
        self.assertEqual((topics, memberships, provenance), reverse)
        self.assertFalse(provenance["quality_evaluated"])

    def test_publication_reclassifies_and_clips_retained_coverage(self):
        now = 1800000000
        self.session.add(Coverage(source="stackoverflow", start=now - 500 * 86400, end=now, complete=True))
        for number in (1, 2):
            upsert(self.session, item(number=number, created=now - 86400, activity=now - 3600), "stackoverflow")
        upsert(self.session, item(number=3, created=now - 500 * 86400), "stackoverflow")
        self.session.commit()
        with patch.dict("os.environ", {"TOPIC_MODEL": "", "RETENTION_DAYS": "395"}):
            publish(self.session, now)
        self.assertIsNone(self.session.get(Question, "stackoverflow:3"))
        coverage = self.session.scalar(select(Coverage))
        self.assertEqual(coverage.start, now - 395 * 86400)
        release = self.session.scalar(select(Release))
        self.assertEqual(len(release.snapshot["questions"]), 2)
        self.assertTrue(release.snapshot["questions"][0]["topic_ids"])


if __name__ == "__main__":
    unittest.main()
