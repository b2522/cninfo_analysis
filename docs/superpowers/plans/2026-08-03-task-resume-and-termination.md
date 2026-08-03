# 任务恢复与终止减持分类 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 页面刷新后恢复活动任务进度，并用 PDF 原文证据兜底确认“提前终止减持、未减持”机会公告。

**Architecture:** SQLite 仓储新增活动任务查询和受影响历史记录恢复。FastAPI 公开活动任务端点；前端初始化时加载、渲染、轮询多个任务。分析 Worker 在模型无标签时根据标题和 PDF 正文执行最小确定性兜底。

**Tech Stack:** Python 3.13、FastAPI、标准库 SQLite、原生 JavaScript、unittest。

---

### Task 1: 活动任务 API

**Files:**
- Modify: `src/cninfo_miner/repository.py`
- Modify: `src/cninfo_miner/main.py`
- Test: `tests/test_repository.py`, `tests/test_api.py`

- [ ] 写失败测试：SQLite 只返回排队中、进行中的任务。
- [ ] 写失败测试：`GET /api/tasks/active` 返回持久化任务。
- [ ] 实现仓储查询与 API 端点。
- [ ] 运行定向测试。

### Task 2: 刷新后的任务状态 UI

**Files:**
- Modify: `src/cninfo_miner/static/index.html`
- Modify: `src/cninfo_miner/static/app.js`
- Modify: `src/cninfo_miner/static/styles.css`
- Test: `tests/test_static_ui.py`

- [ ] 写失败静态 UI 测试，要求加载活动任务并支持抓取/分析并行显示。
- [ ] 以活动任务映射替代单个内存任务 ID；初始化时查询并恢复轮询。
- [ ] 保持紧凑亮色状态条，并按任务类型禁用对应按钮。
- [ ] 运行静态 UI 测试。

### Task 3: 终止减持的 PDF 证据兜底

**Files:**
- Modify: `src/cninfo_miner/classification.py`
- Modify: `src/cninfo_miner/worker.py`
- Modify: `src/cninfo_miner/repository.py`
- Test: `tests/test_classification.py`, `tests/test_worker.py`, `tests/test_repository.py`

- [ ] 写失败测试：标题和 PDF 都含“提前终止减持”时，模型未返回标签也确认为机会。
- [ ] 写失败测试：无 PDF 原文时不得确认。
- [ ] 实现最小证据检测与 Worker 兜底。
- [ ] 将已排除的受影响历史记录恢复为待分析。
- [ ] 运行定向测试。

### Task 4: 整体验证

**Files:**
- Verify: 全部测试与 Python 编译

- [ ] 执行 `python -m unittest discover -s tests -v`。
- [ ] 执行 `python -m compileall -q src run.py`。
- [ ] 重启 `run.py` 并核对健康接口和活动任务端点。
