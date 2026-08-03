# Sale of Repurchased Shares and Repurchase Summary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Classify sales of repurchased shares as risk and emit a complete, fixed-order factual repurchase summary.

**Architecture:** Keep title screening high recall and final decisions evidence-gated. Extend the existing deterministic classification helpers rather than adding a second classification path; requeue only confirmed legacy repurchase records that need the new summary format.

**Tech Stack:** Python 3.13, unittest, SQLite, FastAPI.

---

### Task 1: Define the new rules with failing tests

**Files:**
- Modify: `tests/test_classification.py`
- Modify: `tests/test_repository.py`

- [ ] Add a title-screening assertion that `出售已回购股份` maps to the existing risk category.
- [ ] Add an evidence/summary assertion for a PDF containing repurchase plan amount, price ceiling, cumulative amount, highest and lowest transaction prices.
- [ ] Add a repository migration assertion showing an old confirmed “回购公司股份” summary returns to `candidate`.
- [ ] Run `python -m unittest tests.test_classification tests.test_repository -v`; expect failure before production changes.

### Task 2: Implement deterministic classification and extraction

**Files:**
- Modify: `src/cninfo_miner/classification.py`
- Modify: `src/cninfo_miner/worker.py`

- [ ] Add title/evidence handling for sales of repurchased shares under `大股东减持、质押和股权变动`.
- [ ] Extend `repurchase_evidence()` to extract and order the required plan/progress facts, with `本公告未披露` for absent values.
- [ ] Reuse the existing worker evidence-gated confirmation pathway; do not add a model-only confirmation route.
- [ ] Run the focused tests; expect pass.

### Task 3: Requeue legacy summaries and verify integration

**Files:**
- Modify: `src/cninfo_miner/repository.py`
- Modify: `tests/test_worker.py`

- [ ] Extend the one-time SQLite correction to requeue old confirmed repurchase results that lack the expanded fields.
- [ ] Add worker coverage proving evidence-only confirmation retains the new summary.
- [ ] Run `python -m unittest discover -s tests -v` and `python -m compileall -q src`.
- [ ] Restart or verify the local service, then inspect the target announcement records.
