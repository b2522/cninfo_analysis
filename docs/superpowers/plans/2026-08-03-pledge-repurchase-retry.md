# Pledge Classification, Repurchase Summary, and Retry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 排除日常解除后再质押公告，完善回购/增持的 PDF 事实摘要，并在技术分析失败后重试一次。

**Architecture:** 在 `classification.py` 保持所有确定性标题和 PDF 事实规则；`worker.py` 复用既有模型和 HTTP 客户端，先并发执行第一轮、再串行重试异常候选；`repository.py` 的启动修正迁移处理既有错误分类。不会改变页面 API 或模型配置保存方式。

**Tech Stack:** Python 3.13、FastAPI、SQLite、httpx、unittest、PyMuPDF。

---

### Task 1: 标题窄排除与历史记录迁移

**Files:**
- Modify: `src/cninfo_miner/classification.py`
- Modify: `src/cninfo_miner/repository.py`
- Modify: `tests/test_classification.py`
- Test: `tests/test_repository.py`

- [ ] **Step 1: Write failing tests**

```python
self.assertNotIn("大股东减持、质押和股权变动", screen_categories("关于控股股东部分股份解除质押及质押的公告"))
self.assertIn("大股东减持、质押和股权变动", screen_categories("关于控股股东解除质押及质押、可能导致控制权变更的提示性公告"))
```

并增加 SQLite 历史已确认日常质押记录被一次性改为 `dismissed` 的测试。

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m unittest tests.test_classification tests.test_repository -v`
Expected: 新规则断言失败。

- [ ] **Step 3: Implement minimum rule**

新增窄排除判断及高风险关键词例外，并在已有启动修正函数中处理历史记录。

- [ ] **Step 4: Run focused tests**

Run: `python -m unittest tests.test_classification tests.test_repository -v`
Expected: PASS。

### Task 2: 回购与增持事实摘要

**Files:**
- Modify: `src/cninfo_miner/classification.py`
- Modify: `src/cninfo_miner/worker.py`
- Modify: `tests/test_classification.py`
- Modify: `tests/test_worker.py`

- [ ] **Step 1: Write failing tests**

用包含回购金额、价格上限、累计股数和成交区间的 PDF 文本断言完整摘要；用包含增持股数、比例、均价的 PDF 文本断言事实摘要；断言缺失字段输出“本公告未披露”。

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m unittest tests.test_classification tests.test_worker -v`
Expected: 新摘要断言失败。

- [ ] **Step 3: Implement minimum extractors**

扩展回购事实提取；增加增持事实提取，仅在 PDF 中确有增持事实时覆盖模型摘要并写入结构化 metrics。

- [ ] **Step 4: Run focused tests**

Run: `python -m unittest tests.test_classification tests.test_worker -v`
Expected: PASS。

### Task 3: 技术失败后重试一次

**Files:**
- Modify: `src/cninfo_miner/worker.py`
- Modify: `tests/test_worker.py`

- [ ] **Step 1: Write failing tests**

模拟首次 PDF 或模型调用异常、第二次成功；再模拟两次异常，断言最终状态为 `dismissed`、错误字段清空且任务完成。

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m unittest tests.test_worker.AnalysisWorkerTests -v`
Expected: 重试断言失败。

- [ ] **Step 3: Implement retry queue**

让候选处理函数返回是否发生技术异常。第一轮并发收集失败候选，全部完成后串行重试一次；二次异常转 `dismissed`。

- [ ] **Step 4: Run focused tests**

Run: `python -m unittest tests.test_worker.AnalysisWorkerTests -v`
Expected: PASS。

### Task 4: 全量回归验证

**Files:**
- Verify only: `src/cninfo_miner/**/*.py`
- Verify only: `tests/**/*.py`

- [ ] **Step 1: Compile source**

Run: `python -m compileall -q src`
Expected: exit code 0。

- [ ] **Step 2: Run full suite**

Run: `python -m unittest discover -s tests -v`
Expected: all tests PASS。

- [ ] **Step 3: Inspect changed files**

Run: `git diff --check; git diff -- src/cninfo_miner/classification.py src/cninfo_miner/worker.py src/cninfo_miner/repository.py tests`
Expected: only scoped changes, no whitespace errors。
