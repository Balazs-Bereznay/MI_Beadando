"""Stack Exchange adapter with persisted provider backoff and paging cursors."""
import hashlib
import gzip
import json
import os
from pathlib import Path
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib

from .classification import plain_text


def secret(name, default=""):
    path = os.getenv(name + "_FILE")
    return Path(path).read_text(encoding="utf-8").strip() if path else os.getenv(name, default)


class SourcePaused(Exception):
    def __init__(self, until, reason):
        self.until = until
        super().__init__(reason)


class SourceError(Exception):
    pass


class StackExchange:
    def __init__(self, state=None, save_state=None, transport=None, clock=time.time):
        self.state = state or {}
        self.save_state = save_state or (lambda value: None)
        self.transport = transport or self._http
        self.clock = clock
        self.site = os.getenv("STACKEXCHANGE_SITE", "stackoverflow")
        self.key = secret("STACKEXCHANGE_API_KEY")
        self.token = secret("STACKEXCHANGE_ACCESS_TOKEN")

    @staticmethod
    def _http(url):
        request = urllib.request.Request(url, headers={"User-Agent": "DeveloperTrends/1.0 (+public analytics)"})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                body = response.read()
                encoding = response.headers.get("Content-Encoding", "").lower()
                if encoding == "gzip" or body.startswith(b"\x1f\x8b"):
                    body = gzip.decompress(body)
                elif encoding == "deflate":
                    body = zlib.decompress(body)
                return json.loads(body.decode("utf-8"))
        except urllib.error.HTTPError as exc:
            # Never include request URLs (which carry credentials) in logs.
            raise SourceError(f"Source returned HTTP {exc.code}") from None
        except (urllib.error.URLError, TimeoutError, ValueError):
            raise SourceError("Source request failed or returned invalid JSON") from None

    def request(self, path, params=None, optional=False):
        now = int(self.clock())
        quota_day = now // 86400
        if self.state.get("quota_day", quota_day) != quota_day:
            self.state.pop("quota_remaining", None)
            self.state.pop("quota_reset_at", None)
        self.state["quota_day"] = quota_day
        method = "questions" if path.startswith("questions") else path.split("/")[0]
        blocked = max(self.state.get("blocked_until", {}).get(method, 0), self.state.get("quota_reset_at", 0))
        if now < blocked:
            raise SourcePaused(blocked, "Source backoff or exhausted quota")
        reserve = int(os.getenv("QUOTA_RESERVE", "200"))
        if optional and self.state.get("quota_remaining", reserve + 1) <= reserve:
            raise SourcePaused(now + 600, "Optional acquisition paused to reserve source quota")
        query = {"site": self.site, "pagesize": 100, **(params or {})}
        if self.key:
            query["key"] = self.key
        if self.token:
            query["access_token"] = self.token
        fingerprint = hashlib.sha256((path + json.dumps(query, sort_keys=True)).encode()).hexdigest()
        recent = {k: v for k, v in self.state.get("recent", {}).items() if v > now - 60}
        if fingerprint in recent:
            raise SourcePaused(recent[fingerprint] + 60, "Identical request cache interval")
        recent[fingerprint] = now
        self.state["recent"] = recent
        self.save_state(self.state)
        result = self.transport("https://api.stackexchange.com/2.3/" + path + "?" + urllib.parse.urlencode(query))
        if not isinstance(result, dict):
            raise SourceError("Invalid source response")
        if "backoff" in result:
            self.state.setdefault("blocked_until", {})[method] = now + int(result["backoff"])
        if "quota_remaining" in result:
            self.state["quota_remaining"] = int(result["quota_remaining"])
            if self.state["quota_remaining"] == 0:
                self.state["quota_reset_at"] = ((now // 86400) + 1) * 86400
        self.save_state(self.state)
        if "error_id" in result:
            raise SourceError(f"Source API error {result['error_id']}")
        if not isinstance(result.get("items"), list) or "has_more" not in result:
            raise SourceError("Source response omitted pagination fields")
        return result

    def questions(self, start, end, page=1, mode="creation", optional=False):
        params = {"sort": mode, "order": "asc", "page": page, "filter": "withbody"}
        if mode == "creation":
            params.update(fromdate=start, todate=end)
        else:
            params.update(min=start, max=end)
        return self.request("questions", params, optional=optional)

    def refresh(self, ids):
        if not ids or len(ids) > 100:
            raise ValueError("Refresh requires 1–100 source question IDs")
        return self.request("questions/" + ";".join(str(i) for i in ids), {"filter": "withbody"}, optional=True)

    def answers(self, question_id, page=1):
        return self.request(f"questions/{question_id}/answers", {"sort": "votes", "order": "desc", "page": page, "pagesize": 3, "filter": "withbody"}, optional=True)


def safe_link(value):
    parsed = urllib.parse.urlparse(value or "")
    hostname = (parsed.hostname or "").lower()
    if parsed.scheme == "https" and (hostname == "stackoverflow.com" or hostname.endswith(".stackexchange.com") or hostname == "stackexchange.com"):
        return value
    return ""


def normalize(item, source="stackoverflow", now=None):
    if "question_id" not in item or "creation_date" not in item:
        raise SourceError("Source question omitted its identity or creation timestamp")
    owner = item.get("owner") or {}
    return {"id": f"{source}:{item['question_id']}", "source": source, "source_question_id": int(item["question_id"]),
            "title": plain_text(item.get("title", "")), "body": plain_text(item.get("body", "")),
            "tags": list(dict.fromkeys(item.get("tags", []))), "created_at": int(item["creation_date"]),
            "last_activity_at": int(item.get("last_activity_date", item["creation_date"])),
            "answer_count": int(item.get("answer_count", 0)), "accepted_answer_id": item.get("accepted_answer_id"),
            "is_answered": bool(item.get("is_answered", False)), "score": int(item.get("score", 0)),
            "url": safe_link(item.get("link", "")), "owner": {"display_name": plain_text(owner.get("display_name", "")), "link": safe_link(owner.get("link", ""))},
            "content_license": item.get("content_license", ""), "status": "migrated" if item.get("migrated_to") else "closed" if item.get("closed_date") else "available",
            "updated_at": int(now or time.time())}


def normalize_answer(item):
    owner = item.get("owner") or {}
    return {"answer_id": item.get("answer_id"), "body": plain_text(item.get("body", "")),
            "score": item.get("score", 0), "is_accepted": bool(item.get("is_accepted")),
            "owner": {"display_name": plain_text(owner.get("display_name", "")), "link": safe_link(owner.get("link", ""))},
            "content_license": item.get("content_license", ""), "url": f"https://stackoverflow.com/a/{item['answer_id']}"}
