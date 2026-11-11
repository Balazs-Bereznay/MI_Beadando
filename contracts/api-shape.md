# Public API contract

Base `/api/v1`. GET only. Every analytical result has `meta`: `{release_id, taxonomy_version, as_of, published_at, current_window:{start,end}, previous_window:{start,end}, coverage_state:'complete'|'partial', freshness_state:'current'|'outdated', limitations:string[], demo:boolean}`. All dates are ISO UTC strings. Query: `period=24h|7d|30d` (default 7d), `source=stackoverflow`, `category` (optional), `sort=absolute|relative|volume|activity|name`, `q`, `release_id` (optional). Custom dates `from` and `to` ISO UTC, mutually exclusive with period. Unknown/invalid query returns 422. No release returns 503 `{detail:'No published dataset yet. ...'}` except status/methodology.

Topic row: `{id,slug,name,description,category,tags:string[],current_count,previous_count,absolute_growth:number|null,relative_growth_pct:number|null,growth_state:'growing'|'declining'|'stable'|'newly_active'|'no_activity'|'insufficient_history',total_count,last_activity_at:string|null,small_baseline:boolean,history:Point[]}`.
Point: `{date:string,count:number|null,coverage_state:'complete'|'partial'}`.

- `/status`: `{available:boolean,release_id:string|null,as_of:string|null,published_at:string|null,last_success_at:string|null,coverage_start:string|null,coverage_end:string|null,freshness_state:'current'|'outdated'|'unavailable',source:'stackoverflow',source_health:string,demo:boolean,message:string, categories:string[]}`.
- `/dashboard`: `{meta,summary:{new_questions:number,growing_topics:number,fastest_growing_topic:Topic|null,newly_active_topics:number},topics:Topic[],emerging_topics:Topic[],history:Point[]}`. topics is top 10 positive growth under selected sort.
- `/topics`: `{meta,topics:Topic[],total:number,next_cursor:string|null,categories:string[]}`. Supports `growth=all|growing|declining|newly_active|stable`, `activity=all|active|inactive`, cursor and limit (default 24, max100).
- `/topics/{id}`: `{meta,topic:Topic,answer_stats:{no_answers:number,with_answers:number,accepted:number,source_answered:number},related_topics:Topic[],taxonomy_note:string}`.
- `/topics/{id}/history`: `{meta,history:Point[]}`; spans current + previous windows.
- `/topics/{id}/questions`: `{meta,questions:Question[],total:number,next_cursor:string|null}`; sort=newest|oldest|activity|score, q, limit, cursor. Lists current-window questions by default; `scope=all` lists retained questions.
- `/questions/{source}/{id}` (numeric source ID): `{question:Question,meta}`.
- `/methodology`: `{title:string,definitions:object,limitations:string[],source_url:string,license_url:string}`.

Question: `{id:string,source:string,source_question_id:number,title:string,excerpt:string,tags:string[],created_at:string,last_activity_at:string,answer_count:number,accepted_answer_id:number|null,is_answered:boolean,score:number,url:string,owner:{display_name?:string,link?:string},content_license:string,status:string,answers:Answer[]}`.
Answer: `{answer_id:number,body:string,score:number,is_accepted:boolean,owner:{display_name?:string,link?:string},content_license:string,url:string}`. Body/excerpt are plain text; render as text, never raw HTML. Demo has no real source links (empty URL) and is prominently labeled. Only present original links for nonempty URL and status=available.

Use same release_id across related requests; new status release can trigger coherent refresh. No credentials in browser. Retired/unknown topics 404; expired release 410. Health `/health` returns `{status:'ok'}`.
