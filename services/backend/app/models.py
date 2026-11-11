import time
from sqlalchemy import Boolean, Integer, String, Text, JSON, Index
from sqlalchemy.orm import Mapped, mapped_column
from .db import Base


class Question(Base):
    __tablename__ = "questions"
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    source: Mapped[str] = mapped_column(String(50), default="stackoverflow")
    source_question_id: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[int] = mapped_column(Integer, index=True)
    last_activity_at: Mapped[int] = mapped_column(Integer, index=True)
    answer_count: Mapped[int] = mapped_column(Integer, default=0)
    accepted_answer_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_answered: Mapped[bool] = mapped_column(Boolean, default=False)
    score: Mapped[int] = mapped_column(Integer, default=0)
    url: Mapped[str] = mapped_column(Text, default="")
    owner: Mapped[dict] = mapped_column(JSON, default=dict)
    content_license: Mapped[str] = mapped_column(String(50), default="")
    status: Mapped[str] = mapped_column(String(50), default="available")
    answers: Mapped[list] = mapped_column(JSON, default=list)
    topic_ids: Mapped[list] = mapped_column(JSON, default=list)
    updated_at: Mapped[int] = mapped_column(Integer, default=lambda: int(time.time()))
    __table_args__ = (Index("question_source_identity", "source", "source_question_id", unique=True),)


class Topic(Base):
    __tablename__ = "topics"
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    slug: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(100), default="Other")
    tags: Mapped[list] = mapped_column(JSON, default=list)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)


class Coverage(Base):
    __tablename__ = "coverage"
    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(50), default="stackoverflow")
    start: Mapped[int] = mapped_column(Integer, index=True)
    end: Mapped[int] = mapped_column(Integer)
    complete: Mapped[bool] = mapped_column(Boolean, default=False)


class State(Base):
    __tablename__ = "state"
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, default=dict)


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(200), unique=True)
    kind: Mapped[str] = mapped_column(String(50))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(30), default="ready", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[int] = mapped_column(Integer, default=0)
    leased_until: Mapped[int] = mapped_column(Integer, default=0)


class Release(Base):
    __tablename__ = "analytics_releases"
    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    as_of: Mapped[int] = mapped_column(Integer)
    published_at: Mapped[int] = mapped_column(Integer)
    taxonomy_version: Mapped[str] = mapped_column(String(100))
    demo: Mapped[bool] = mapped_column(Boolean, default=False)
    snapshot: Mapped[dict] = mapped_column(JSON)
