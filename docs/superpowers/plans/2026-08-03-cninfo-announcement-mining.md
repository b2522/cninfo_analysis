# CNINFO Announcement Mining Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a single-user local Web application that discovers, parses, classifies, stores, and reviews CNINFO announcements in nine confirmed categories.

**Architecture:** A FastAPI service owns the HTTP API, background task runner, and static browser UI. Domain services depend on narrow CNINFO, PDF, LLM, and repository interfaces. MongoDB persists task state and announcement analysis; browser localStorage retains OpenAI-compatible client configuration.

**Tech Stack:** Python 3.13, FastAPI, Uvicorn, Motor/PyMongo, httpx, PyMuPDF, Pydantic, pytest, vanilla HTML/CSS/JavaScript.

---

## Planned file structure

- `pyproject.toml`: Python project metadata and runtime/test dependencies.
- `.env.example`: safe configuration names only.
- `src/cninfo_miner/config.py`: environment config validation.
- `src/cninfo_miner/domain.py`: Pydantic models and fixed category/rule definitions.
- `src/cninfo_miner/classification.py`: deterministic initial screening and high-confidence validation.
- `src/cninfo_miner/repository.py`: MongoDB repository interface and implementation.
- `src/cninfo_miner/cninfo.py`: HTTP client, pagination, and response mapping.
- `src/cninfo_miner/pdf_text.py`: PDF download/hash/text extraction.
- `src/cninfo_miner/llm.py`: OpenAI-compatible structured analysis client.
- `src/cninfo_miner/worker.py`: incremental analysis orchestration and retryable item status.
- `src/cninfo_miner/main.py`: FastAPI routes and application lifespan.
- `src/cninfo_miner/static/{index.html,app.js,styles.css}`: local UI.
- `tests/`: focused unit and API tests.

### Task 1: Bootstrap and domain contracts

**Files:** Create `pyproject.toml`, `.env.example`, `src/cninfo_miner/__init__.py`, `src/cninfo_miner/domain.py`, `tests/test_domain.py`.

- [ ] Write a failing test for the confirmed positive earnings rule and excluded loss/large-order labels.
- [ ] Run `python -m pytest tests/test_domain.py -q`; verify expected missing-module failure.
- [ ] Implement the minimum domain models/category set and fixed earnings eligibility.
- [ ] Re-run the targeted test and full suite.

### Task 2: Deterministic screen and confidence guard

**Files:** Create `src/cninfo_miner/classification.py`, `tests/test_classification.py`.

- [ ] Write failing tests that title/metadata screening returns candidates for each supported family, excludes order-only titles, and that high confidence requires a matching evidence span.
- [ ] Run targeted tests; verify RED.
- [ ] Implement only the keyword-family screen and evidence-based validator.
- [ ] Run tests; verify GREEN.

### Task 3: Safe CNINFO and PDF adapters

**Files:** Create `src/cninfo_miner/cninfo.py`, `src/cninfo_miner/pdf_text.py`, `tests/test_cninfo.py`, `tests/test_pdf_text.py`.

- [ ] Test form body pagination, no Cookie header, response mapping, attachment URL formation, and PDF hash/text extraction with local fixtures.
- [ ] Verify RED, implement adapters, verify GREEN.
- [ ] Manually use a narrow live query after tests to validate actual response field mapping and market coverage before using production ranges.

### Task 4: Mongo persistence and incrementality

**Files:** Create `src/cninfo_miner/repository.py`, `tests/test_repository.py`.

- [ ] Use a repository fake for tests covering upsert identity, changed-content reprocessing, task status persistence, and failed-item retry selection.
- [ ] Verify RED, implement interface and Mongo implementation, verify GREEN.
- [ ] Add idempotent indexes only in the named configured database.

### Task 5: OpenAI-compatible analysis and worker

**Files:** Create `src/cninfo_miner/llm.py`, `src/cninfo_miner/worker.py`, `tests/test_llm.py`, `tests/test_worker.py`.

- [ ] Test JSON request construction, malformed-model-result handling, evidence validation, progress updates, partial failure continuation, and incremental reuse with fakes.
- [ ] Verify RED, implement minimum adapters/orchestration, verify GREEN.

### Task 6: FastAPI endpoints and UI

**Files:** Create `src/cninfo_miner/main.py`, `src/cninfo_miner/static/index.html`, `src/cninfo_miner/static/app.js`, `src/cninfo_miner/static/styles.css`, `tests/test_api.py`.

- [ ] Test date-range validation, task creation, task read, result view filters, and retry endpoint before implementation.
- [ ] Verify RED, implement routes, then a functional polished local UI with four views, date chips, settings dialog, progress, result/detail panes, and event timeline.
- [ ] Verify API tests GREEN and smoke test the browser page.

### Task 7: End-to-end validation and documentation

**Files:** Modify `README.md`; add test fixtures as needed.

- [ ] Document exact local setup, anonymous MongoDB warning, source endpoint validation, and browser-local API key behavior.
- [ ] Run `python -m pytest -q` and a compile/import smoke test.
- [ ] Run a manual narrow-range source validation with rate limits and no Cookie; record actual field mapping in README.
- [ ] Compare behavior line-by-line with the design acceptance criteria.
