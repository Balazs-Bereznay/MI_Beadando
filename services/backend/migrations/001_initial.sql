-- Initial PostgreSQL schema reference. Startup creates these tables automatically.
-- Do not apply to a populated database.

CREATE TABLE questions (
	id VARCHAR(100) NOT NULL, 
	source VARCHAR(50) NOT NULL, 
	source_question_id INTEGER NOT NULL, 
	title TEXT NOT NULL, 
	body TEXT NOT NULL, 
	tags JSON NOT NULL, 
	created_at INTEGER NOT NULL, 
	last_activity_at INTEGER NOT NULL, 
	answer_count INTEGER NOT NULL, 
	accepted_answer_id INTEGER, 
	is_answered BOOLEAN NOT NULL, 
	score INTEGER NOT NULL, 
	url TEXT NOT NULL, 
	owner JSON NOT NULL, 
	content_license VARCHAR(50) NOT NULL, 
	status VARCHAR(50) NOT NULL, 
	answers JSON NOT NULL, 
	topic_ids JSON NOT NULL, 
	updated_at INTEGER NOT NULL, 
	PRIMARY KEY (id)
);

CREATE INDEX ix_questions_created_at ON questions (created_at);

CREATE UNIQUE INDEX question_source_identity ON questions (source, source_question_id);

CREATE INDEX ix_questions_last_activity_at ON questions (last_activity_at);

CREATE TABLE topics (
	id VARCHAR(100) NOT NULL, 
	name TEXT NOT NULL, 
	slug VARCHAR(200) NOT NULL, 
	description TEXT NOT NULL, 
	category VARCHAR(100) NOT NULL, 
	tags JSON NOT NULL, 
	metadata_json JSON NOT NULL, 
	PRIMARY KEY (id)
);

CREATE TABLE coverage (
	id SERIAL NOT NULL, 
	source VARCHAR(50) NOT NULL, 
	start INTEGER NOT NULL, 
	"end" INTEGER NOT NULL, 
	complete BOOLEAN NOT NULL, 
	PRIMARY KEY (id)
);

CREATE INDEX ix_coverage_start ON coverage (start);

CREATE TABLE state (
	key VARCHAR(100) NOT NULL, 
	value JSON NOT NULL, 
	PRIMARY KEY (key)
);

CREATE TABLE jobs (
	id SERIAL NOT NULL, 
	key VARCHAR(200) NOT NULL, 
	kind VARCHAR(50) NOT NULL, 
	payload JSON NOT NULL, 
	status VARCHAR(30) NOT NULL, 
	attempts INTEGER NOT NULL, 
	available_at INTEGER NOT NULL, 
	leased_until INTEGER NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (key)
);

CREATE INDEX ix_jobs_status ON jobs (status);

CREATE TABLE analytics_releases (
	id VARCHAR(50) NOT NULL, 
	as_of INTEGER NOT NULL, 
	published_at INTEGER NOT NULL, 
	taxonomy_version VARCHAR(100) NOT NULL, 
	demo BOOLEAN NOT NULL, 
	snapshot JSON NOT NULL, 
	PRIMARY KEY (id)
);
