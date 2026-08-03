# Staged Announcement Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Provide a bright local table workflow that separately collects CNINFO announcements, deduplicates them, then analyzes only title-screened, previously unanalyzed candidates.

**Architecture:** Store every collected CNINFO notice in the existing announcement-results collection with an explicit analysis state. Split the existing worker into collection and analysis operations exposed by distinct APIs, while retaining one task-status mechanism. The browser table requests only relevant processing states and filters its analysis-result column by opportunity/risk.

**Tech Stack:** Python 3.13, FastAPI, httpx, PyMongo 4.9 with MongoDB 3.6 compatibility, PyMuPDF, vanilla HTML/CSS/JavaScript.

---

### Task 1: Define categories and high-recall title screening

**Files:**
- Modify: `src/cninfo_miner/domain.py`
- Modify: `src/cninfo_miner/classification.py`
- Test: `tests/test_domain.py`
- Test: `tests/test_classification.py`

- [ ] Write failing tests for positive/negative performance labels, all-positive opportunity mapping, periodic-report inclusion, and ambiguous topic/event title inclusion.
- [ ] Run the targeted tests and verify their expected failures.
- [ ] Replace the former eight labels with the nine confirmed opportunity/risk labels, add label-to-view mapping, and implement strong plus combined title screening.
- [ ] Re-run targeted tests.

### Task 2: Persist raw announcements and analysis states

**Files:**
- Modify: `src/cninfo_miner/repository.py`
- Test: `tests/test_repository.py`

- [ ] Write failing tests for collection upsert preserving an existing confirmed result and for table queries that exclude raw and dismissed items.
- [ ] Run the repository tests and verify failure.
- [ ] Add raw-announcement upsert, candidate-state queries, atomic state updates, and table-list filtering in both repository implementations; retain MongoDB 3.6-compatible queries and indexes.
- [ ] Re-run repository tests.

### Task 3: Split collection and analysis workers

**Files:**
- Modify: `src/cninfo_miner/worker.py`
- Test: `tests/test_worker.py`

- [ ] Write failing asynchronous tests: collection fetches all pages and persists each notice; analysis skips previously finalized rows and sends only screened candidates to PDF/model processing.
- [ ] Run worker tests and verify failure.
- [ ] Implement `CollectionWorker` and a repository-backed analysis operation, updating task counters and final statuses.
- [ ] Re-run worker tests.

### Task 4: Replace the API task entrypoints and add in-process daily collection scheduling

**Files:**
- Modify: `src/cninfo_miner/main.py`
- Modify: `pyproject.toml`
- Test: `tests/test_api.py`

- [ ] Write failing API tests for manual collection, analysis of the unprocessed queue, and opportunity/risk table filtering.
- [ ] Run API tests and verify failure.
- [ ] Add collection and analysis endpoints, retain task progress lookup, configure a 19:00 Asia/Shanghai scheduler only for production runs, and update dependencies if required.
- [ ] Re-run API tests.

### Task 5: Implement the bright table UI

**Files:**
- Modify: `src/cninfo_miner/static/index.html`
- Modify: `src/cninfo_miner/static/app.js`
- Modify: `src/cninfo_miner/static/styles.css`
- Test: `tests/test_static_ui.py`

- [ ] Write failing static tests for the bright table controls, separate collection/analysis buttons, and analysis-result select options.
- [ ] Run the static tests and verify failure.
- [ ] Replace the card/tabs presentation with the bright table, status feedback, filter select, and separate task polling for collection and analysis.
- [ ] Re-run static tests.

### Task 6: Update documentation and verify end to end

**Files:**
- Modify: `README.md`

- [ ] Update the README with the two-stage workflow, the 19:00 runtime condition, and the revised categories.
- [ ] Run `python -m unittest discover -s tests -v`.
- [ ] Run `python -m compileall -q src run.py`.
- [ ] Restart the local server and create a real collection task through the local API; verify HTTP 201 and a JSON-safe task response.
