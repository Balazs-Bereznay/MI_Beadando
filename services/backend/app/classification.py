"""Automatic, reproducible content-aware topic discovery.

The default lightweight classifier is intentionally labelled lexical. Set
TOPIC_MODEL to a local SentenceTransformer directory to use semantic features.
No source text is sent to a third-party model service.
"""
from collections import Counter, defaultdict
from hashlib import sha256
from html import unescape
from html.parser import HTMLParser
import math
import os
import re
from pathlib import Path


def model_fingerprint(path):
    digest = sha256()
    for file in sorted(Path(path).rglob("*")):
        if file.is_file():
            digest.update(str(file.relative_to(path)).encode())
            with file.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
    return digest.hexdigest()[:16]


def category_for(tags):
    categories = {
        "Web Development": {"javascript", "typescript", "reactjs", "vue.js", "angular", "html", "css", "next.js"},
        "Data & Databases": {"sql", "postgresql", "mysql", "mongodb", "pandas", "numpy", "dataframe", "database"},
        "Infrastructure": {"docker", "kubernetes", "linux", "azure", "amazon-web-services", "devops", "nginx"},
        "Mobile": {"android", "ios", "swift", "flutter", "react-native", "kotlin"},
        "Languages & Tooling": {"python", "java", "c++", "c#", "rust", "go", "git", "pip"},
    }
    scores = [(len(set(tags) & members), name) for name, members in categories.items()]
    score, name = max(scores)
    return name if score else "Development"


class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.hidden += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self.hidden:
            self.hidden -= 1
        if tag in ("p", "div", "br", "li", "pre"):
            self.parts.append(" ")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def plain_text(value):
    parser = TextExtractor()
    parser.feed(value or "")
    return re.sub(r"\s+", " ", unescape(" ".join(parser.parts))).strip()


STOP = set("a an the this that these those is are was were be been to of in on for from and or with without how what why when where i my we it its not can could would should does do using use get getting have has will want need error issue problem question code please thanks like trying make work works new any into as at by if then than also out only one two all some".split())


def tokens(text):
    return [t for t in re.findall(r"[a-z][a-z0-9_+#.-]*", text.lower()) if t not in STOP and (len(t) > 2 or t in {"c#", "c", "go", "r", "js", "ts"})]


def features(question):
    counts = Counter(tokens(question.get("title", "")) * 4)
    counts.update(tokens(question.get("body", "")[:3000]))
    for tag in question.get("tags", []):
        counts[tag.lower()] += 3
    for answer in question.get("answers", [])[:2]:
        counts.update(tokens(answer.get("body", "")[:500]))
    norm = math.sqrt(sum(v * v for v in counts.values())) or 1
    return {k: v / norm for k, v in counts.items()}


def cosine(a, b):
    return sum(v * b.get(k, 0) for k, v in a.items())


def discover(questions):
    """Return topic definitions, question memberships, and provenance.

    Rebuild over the same retained corpus for both comparison periods. Stable
    IDs derive from a content signature, and each build receives its own hash.
    """
    questions = sorted(questions, key=lambda q: q["id"])
    vectors = [features(q) for q in questions]
    method = "lexical-content-v1"
    model_path = os.getenv("TOPIC_MODEL", "").strip()
    semantic = None
    if model_path:
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer(model_path, local_files_only=True)
        semantic = model.encode([
            q.get("title", "") + " " + q.get("body", "")[:1500] + " " + " ".join(q.get("tags", [])) + " " + " ".join(a.get("body", "")[:300] for a in q.get("answers", [])[:2])
            for q in questions
        ], normalize_embeddings=True, batch_size=32, show_progress_bar=False)
        method = "sentence-transformer:" + model_fingerprint(model_path)
    groups = []
    by_tag = defaultdict(set)
    # Tag candidates bound comparisons; content similarity makes the decision.
    for index, (q, vector) in enumerate(zip(questions, vectors)):
        candidates = set()
        for tag in q.get("tags", []):
            candidates.update(by_tag[tag])
        best, best_score = None, 0.0
        for gi in sorted(candidates):
            group = groups[gi]
            score = float(semantic[index] @ semantic[group["representative"]]) if semantic is not None else cosine(vector, group["vector"])
            if score > best_score:
                best, best_score = gi, score
        threshold = 0.62 if semantic is not None else 0.37
        if best is None or best_score < threshold:
            best = len(groups)
            groups.append({"representative": index, "vector": vector, "members": []})
        groups[best]["members"].append(index)
        for tag in q.get("tags", []):
            by_tag[tag].add(best)
    topics, topic_groups = [], []
    memberships = {q["id"]: [] for q in questions}
    for group in groups:
        if len(group["members"]) < 2:
            continue
        tags, terms = Counter(), Counter()
        for i in group["members"]:
            tags.update(questions[i].get("tags", []))
            terms.update(tokens(questions[i].get("title", "")))
        primary = tags.most_common(1)[0][0] if tags else "development"
        phrases = [t for t, _ in terms.most_common() if t not in tags][:2]
        if not phrases:
            continue
        signature = primary + ":" + "-".join(sorted(phrases))
        topic_id = sha256(signature.encode()).hexdigest()[:16]
        name = primary + " · " + " & ".join(phrases)
        if any(t["id"] == topic_id for t in topics):
            for i in group["members"]:
                memberships[questions[i]["id"]].append(topic_id)
            continue
        topics.append({"id": topic_id, "name": name, "slug": re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-"),
                       "description": f"Questions about {' and '.join(phrases)} in {primary}. Automatically grouped from question text, original tags, and available answer context.",
                       "category": category_for(list(tags)), "tags": [t for t, _ in tags.most_common(8)],
                       "metadata_json": {"method": method, "provisional": True, "representative_question": questions[group["representative"]]["id"]}})
        topic_groups.append((topic_id, group))
        for i in group["members"]:
            memberships[questions[i]["id"]].append(topic_id)
    # A sufficiently close question may contribute to several topic views.
    for index, q in enumerate(questions):
        for topic_id, group in topic_groups:
            if topic_id in memberships[q["id"]]:
                continue
            if not set(q.get("tags", [])) & set(questions[group["representative"]].get("tags", [])):
                continue
            score = float(semantic[index] @ semantic[group["representative"]]) if semantic is not None else cosine(vectors[index], group["vector"])
            if score >= (0.72 if semantic is not None else 0.60):
                memberships[q["id"]].append(topic_id)
    version_material = method + "|" + "|".join(t["id"] for t in topics) + "|" + "|".join(q["id"] + ":" + ",".join(sorted(set(memberships[q["id"]]))) for q in questions)
    return topics, {k: sorted(set(v)) for k, v in memberships.items()}, {"method": method, "version": sha256(version_material.encode()).hexdigest()[:16], "quality_evaluated": False}
