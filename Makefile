# Run `make help` to list targets.
PY = PYTHONPATH=. uv run

.PHONY: help up down logs dev worker test seed demo demo-snapshot reanalyze synth eval eval-heldout replay dashboard fetch-corpus

help:            ## list targets
	@grep -E '^[a-z-]+:.*##' Makefile | sed 's/:.*##/\t/'

up:              ## build + start everything (db, redis, api, worker)
	docker compose up -d --build

down:            ## stop everything
	docker compose down

logs:            ## follow api + worker logs
	docker compose logs -f api worker

dev:             ## run the API locally with reload (needs `docker compose up -d db redis`)
	$(PY) uvicorn app.api.main:app --reload --port 8000

worker:          ## run the worker locally
	$(PY) arq app.worker.WorkerSettings

test:            ## unit tests (no LLM, no DB)
	uv run pytest tests -q

synth:           ## generate synthetic calls + answer key (uses Gemini)
	$(PY) python data/synth_generate.py

fetch-corpus:    ## download a small sample of the HF telecom corpus
	$(PY) python data/fetch_corpus.py

seed:            ## run the full pipeline on 69 synthetic + 15 corpus calls (needs GEMINI_API_KEY; ~30 min on a free key)
	docker compose exec api python scripts/seed.py

demo:            ## instant: restore the already-analysed demo database (no API key needed); vault excluded
	docker compose exec -T db psql -q -U convo -d convo -c "TRUNCATE calls, llm_calls, review_queue, judge_results RESTART IDENTITY CASCADE"
	gunzip -c data/demo_snapshot.sql.gz | docker compose exec -T db psql -q -U convo -d convo
	@echo "demo data loaded: open http://localhost:8000/ui"

demo-snapshot:   ## (maintainer) refresh data/demo_snapshot.sql.gz from the current database
	docker compose exec -T db pg_dump -U convo -d convo --data-only --schema=public | gzip > data/demo_snapshot.sql.gz

reanalyze:       ## re-score every stored call (after a prompt/config change)
	docker compose exec api python scripts/seed.py --reanalyze

eval:            ## offline evals on the dev split (served from data/cache: no API key needed)
	$(PY) python evals/run_evals.py --split dev --live-limit 15

eval-heldout:    ## final numbers on the held-out split (do not tune on this)
	$(PY) python evals/run_evals.py --split heldout --live-limit 15

replay:          ## stream one synthetic call over the live WebSocket
	$(PY) python scripts/live_replay.py data/synthetic/calls/syn_001.json --delay 1

dashboard:       ## build the React dashboard into dashboard/dist (served at /ui)
	cd dashboard && npm install && npm run build
