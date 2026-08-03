# GitHub Actions 定时公告抓取与分析 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a GitHub Actions workflow that runs the existing collection and LLM-analysis cycle every four hours and commits the updated SQLite database to the default branch.

**Architecture:** The workflow is the sole runtime addition. It checks out the last committed SQLite database, installs the Python 3.13 project dependencies, supplies LLM configuration through GitHub Secrets, and invokes the existing `CollectionWorker` and `AnalysisWorker` directly against `SQLiteRepository`, constructing `LlmConfig` only in process memory. A serialized workflow job commits only `data/cninfo_announcement_mining.sqlite3` with the provided `GITHUB_TOKEN`.

**Tech Stack:** GitHub Actions, Ubuntu hosted runner, Python 3.13, existing FastAPI project workers, SQLite, GitHub Actions `GITHUB_TOKEN`.

---

## File structure

- Create: `.github/workflows/collect-announcements.yml` — scheduled/manual workflow, task execution, and SQLite commit.
- Create: `docs/superpowers/plans/2026-08-03-github-actions-scheduled-collection.md` — this implementation plan.
- No application Python files change. The workflow uses the existing `SQLiteRepository`, `DEFAULT_DATABASE_PATH`, `manual_collection_range`, `CollectionWorker`, and `AnalysisWorker` interfaces.

### Task 1: Add the scheduled collection workflow

**Files:**
- Create: `.github/workflows/collect-announcements.yml`

- [ ] **Step 1: Create the workflow with a deliberately incomplete secret validation block**

Create the workflow header, triggers, write permission, concurrency group, Python setup, and dependency installation. Add a `Run collection and analysis` step that validates only `LLM_BASE_URL` and `LLM_MODEL`, intentionally omitting `LLM_API_KEY`.

```yaml
name: Collect announcements

on:
  schedule:
    - cron: "0 */4 * * *"
  workflow_dispatch:

permissions:
  contents: write

concurrency:
  group: collect-announcements
  cancel-in-progress: false
```

- [ ] **Step 2: Run the workflow contract check and verify it fails**

Run from repository root:

```powershell
python -c "from pathlib import Path; workflow = Path('.github/workflows/collect-announcements.yml').read_text(encoding='utf-8'); expected = ('LLM_BASE_URL', 'LLM_API_KEY', 'LLM_MODEL'); missing = [name for name in expected if name not in workflow]; assert not missing, f'missing secret validation for {missing}'; assert '0 */4 * * *' in workflow; assert 'contents: write' in workflow; assert 'concurrency:' in workflow; assert 'data/cninfo_announcement_mining.sqlite3' in workflow"
```

Expected: failure indicating that `LLM_API_KEY` validation is missing.

- [ ] **Step 3: Complete the minimal workflow implementation**

Replace the incomplete workflow with the final content below. It must:

- schedule every four hours with `0 */4 * * *` (UTC) and support `workflow_dispatch`;
- use `permissions: contents: write` and a non-cancelling concurrency group;
- set Python `3.13`, install the project with `python -m pip install .`, and make the package importable with `PYTHONPATH: src`;
- read `LLM_BASE_URL`, `LLM_API_KEY`, and `LLM_MODEL` only through `${{ secrets.* }}` environment variables and fail without printing values if any is absent;
- use the existing SQLite repository and recovery routines, then invoke `CollectionWorker` and `AnalysisWorker` directly so the execution includes collection, deduplication, and LLM analysis without persisting LLM configuration;
- stage exactly the SQLite file and push only when it changed.

```yaml
name: Collect announcements

on:
  schedule:
    - cron: "0 */4 * * *"
  workflow_dispatch:

permissions:
  contents: write

concurrency:
  group: collect-announcements
  cancel-in-progress: false

jobs:
  collect-and-analyze:
    runs-on: ubuntu-latest
    timeout-minutes: 55
    steps:
      - name: Check out repository
        uses: actions/checkout@v5
        with:
          fetch-depth: 0

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.13"

      - name: Install dependencies
        run: python -m pip install .

      - name: Run collection and analysis
        env:
          PYTHONPATH: src
          LLM_BASE_URL: ${{ secrets.LLM_BASE_URL }}
          LLM_API_KEY: ${{ secrets.LLM_API_KEY }}
          LLM_MODEL: ${{ secrets.LLM_MODEL }}
        run: |
          for name in LLM_BASE_URL LLM_API_KEY LLM_MODEL; do
            if [ -z "${!name}" ]; then
              echo "Required repository secret is missing: $name" >&2
              exit 1
            fi
          done
          python - <<'PY'
          import asyncio
          import os
          from datetime import UTC, datetime

          from cninfo_miner.llm import LlmConfig
          from cninfo_miner.main import DEFAULT_DATABASE_PATH, manual_collection_range
          from cninfo_miner.repository import SQLiteRepository
          from cninfo_miner.worker import AnalysisWorker, CollectionWorker

          async def run() -> None:
              repository = SQLiteRepository(DEFAULT_DATABASE_PATH)
              repository.recover_interrupted_tasks()
              repository.requeue_dismissed_termination_notices()
              repository.requeue_classification_corrections()

              start_date, end_date = manual_collection_range(datetime.now(UTC))
              collection_task = repository.create_task(start_date, end_date, task_type="collection")
              await CollectionWorker(repository).run(collection_task["id"], start_date, end_date)

              analysis_task = repository.create_task(task_type="analysis")
              config = LlmConfig(
                  base_url=os.environ["LLM_BASE_URL"],
                  api_key=os.environ["LLM_API_KEY"],
                  model=os.environ["LLM_MODEL"],
              )
              await AnalysisWorker(repository).run(analysis_task["id"], config)

          asyncio.run(run())
          PY

      - name: Commit SQLite updates
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
          git add -- data/cninfo_announcement_mining.sqlite3
          if git diff --cached --quiet; then
            echo "SQLite database is unchanged."
            exit 0
          fi
          git commit -m "chore(data): update announcements"
          git push
```

- [ ] **Step 4: Run the workflow contract check and verify it passes**

Run the command from Step 2 again.

Expected: process exits with code 0.

- [ ] **Step 5: Install and run actionlint against the new workflow**

On a machine with Go available:

```powershell
go install github.com/rhysd/actionlint/cmd/actionlint@latest
actionlint .github/workflows/collect-announcements.yml
```

Expected: process exits with code 0 and emits no workflow errors.

- [ ] **Step 6: Commit the workflow**

After repository initialization and remote setup:

```powershell
git add .github/workflows/collect-announcements.yml docs/superpowers/plans/2026-08-03-github-actions-scheduled-collection.md
git commit -m "ci: schedule announcement collection"
```

### Task 2: Verify the end-to-end GitHub Actions setup

**Files:**
- Verify: `.github/workflows/collect-announcements.yml`
- Verify: `data/cninfo_announcement_mining.sqlite3`

- [ ] **Step 1: Add required repository secrets in GitHub**

In the GitHub repository, add these Actions secrets:

```text
LLM_BASE_URL
LLM_API_KEY
LLM_MODEL
```

Expected: all three appear in the repository’s Actions secrets list, without exposing their values.

- [ ] **Step 2: Permit `GITHUB_TOKEN` to write contents**

In GitHub repository Actions settings, select the workflow permission that allows read and write access. If the default branch is protected, also allow GitHub Actions to push or use an approved bot exception.

Expected: a manual run can push a commit to the default branch.

- [ ] **Step 3: Dispatch the workflow manually**

Use **Actions → Collect announcements → Run workflow** on the default branch.

Expected: collection and analysis complete successfully; no LLM secret value appears in the job log.

- [ ] **Step 4: Verify persistence**

Inspect the workflow’s final step and Git history:

```powershell
git log -1 --oneline -- data/cninfo_announcement_mining.sqlite3
git status --short
```

Expected: when data changed, the latest commit has message `chore(data): update announcements`; otherwise the log states that the database was unchanged. The worktree is clean.

