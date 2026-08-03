# 公告分类与表格工作台 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 精确修正公告机会分类，并将本地页面改造成带可见任务进度的紧凑表格工作台。

**Architecture:** `classification.py` 仅负责标题级筛选和从 PDF 文本生成确定性回购事实；`worker.py` 在下载 PDF 后优先使用这些可验证的事实，否则保持原有模型研判。`repository.py` 提供一次性、模式化的 SQLite 历史记录重置；`main.py` 在本地 SQLite 启动时执行它。前端继续使用已有 API，只调整静态 HTML/CSS/JS 的表格工具栏、时间范围和任务状态渲染。

**Tech Stack:** Python 3、FastAPI、SQLite、httpx、PyMuPDF、原生 HTML/CSS/JavaScript、unittest、Node.js（JavaScript 语法检查）。

---

### Task 1: 标题筛选与回购原文规则

**Files:**
- Modify: `src/cninfo_miner/classification.py`
- Test: `tests/test_classification.py`

- [ ] **Step 1: 写入失败的标题筛选测试**

```python
self.assertNotIn(
    "回购、增持和股权激励",
    screen_categories("2025年限制性股票激励计划预留授予激励对象名单（预留授予日）"),
)
self.assertNotIn(
    "产能投产和重大项目",
    screen_categories("关于购买土地使用权并投资建设项目的进展公告"),
)
```

- [ ] **Step 2: 运行分类测试确认失败**

Run: `python -m unittest tests.test_classification -v`  
Expected: 新增断言失败。

- [ ] **Step 3: 实现最小标题排除和回购事实解析**

在 `screen_categories()` 前判断两个窄排除模式，仅移除对应类别；新增回购进展和回购方案的 PDF 事实提取函数。函数返回 `None` 或包含 `label`、`summary`、`evidence`、`stage` 的结构，不硬编码公告 ID。进展摘要必须以 `回购进展：` 开头；方案摘要必须以 `计划回购金额：` 开头。

- [ ] **Step 4: 为 PDF 事实解析补充测试**

使用最小 PDF 文本夹具断言：
- “截至…累计回购 0 股/暂未实施”的进展能生成机会结论；
- “不低于 3,000 万元（含）且不超过 5,000 万元（含）”能生成金额摘要。

- [ ] **Step 5: 运行分类测试确认通过**

Run: `python -m unittest tests.test_classification -v`  
Expected: PASS。

### Task 2: 分析工作流优先使用已验证回购事实

**Files:**
- Modify: `src/cninfo_miner/worker.py`
- Test: `tests/test_worker.py`

- [ ] **Step 1: 写入失败的工作流测试**

模拟模型返回空标签，但 PDF 原文具备回购进展或回购方案事实。断言仓储最终记录：

```python
assert saved["analysis_status"] == "confirmed"
assert saved["labels"] == ["回购、增持和股权激励"]
assert saved["summary"].startswith("回购进展：")
```

以及方案摘要中包含 `计划回购金额：3,000 万元至 5,000 万元`。

- [ ] **Step 2: 运行目标测试确认失败**

Run: `python -m unittest tests.test_worker.AnalysisWorkerTests -v`  
Expected: 新增测试失败，因为当前仅有终止减持的规则兜底。

- [ ] **Step 3: 在下载 PDF 后接入回购兜底**

在现有终止减持兜底附近调用 Task 1 的回购事实函数。它优先于模型空标签/不相关标签，仍复用现有下载 PDF、httpx 连接和任务进度机制；不要增加第二次 PDF 下载或新的模型调用。

- [ ] **Step 4: 运行工作流测试确认通过**

Run: `python -m unittest tests.test_worker -v`  
Expected: PASS。

### Task 3: SQLite 历史数据模式化修正

**Files:**
- Modify: `src/cninfo_miner/repository.py`
- Modify: `src/cninfo_miner/main.py`
- Test: `tests/test_repository.py`
- Test: `tests/test_api.py`

- [ ] **Step 1: 写入失败的 SQLite 修正测试**

在临时 SQLite 库中插入四种状态：回购进展 dismissed、回购方案 confirmed、激励对象名单 confirmed、购买土地使用权进展 confirmed。断言修正后：

```python
assert repurchase_progress["analysis_status"] == "candidate"
assert repurchase_plan["analysis_status"] == "candidate"
assert grant_list["analysis_status"] == "dismissed"
assert land_progress["analysis_status"] == "dismissed"
```

并验证所有重置记录清空标签、摘要、证据、指标与错误信息。

- [ ] **Step 2: 运行目标测试确认失败**

Run: `python -m unittest tests.test_repository -v`  
Expected: 新增方法不存在或断言失败。

- [ ] **Step 3: 添加仓储修正方法并在应用启动调用**

在 SQLiteRepository 添加单一方法，以标题模式而非公告 ID 处理历史记录：
- 回购 + 进展、回购 + 方案 → `candidate`；
- 激励对象名单、购买土地使用权 + 进展 → `dismissed`。

在 `create_app()` 的 SQLite 初始化分支调用该方法，紧随中断任务恢复和提前终止减持重入队逻辑。保留任务密钥仅在浏览器 localStorage 的既有约束，不从启动迁移中发起模型请求。

- [ ] **Step 4: 运行仓储/API 测试确认通过**

Run: `python -m unittest tests.test_repository tests.test_api -v`  
Expected: PASS。

### Task 4: A 方案纯表格工作台与时间范围

**Files:**
- Modify: `src/cninfo_miner/static/index.html`
- Modify: `src/cninfo_miner/static/styles.css`
- Modify: `src/cninfo_miner/static/app.js`
- Test: `tests/test_static_ui.py`

- [ ] **Step 1: 写入失败的静态页面测试**

断言 HTML/JS 具备：
- `时间`下拉控件和值 `today`、`last-2-days`、`last-3-days`；
- 任务状态条容器；
- 固定列类名和一个标准 `<table>`；
- 不再包含公告区域卡片的遗留标记。

- [ ] **Step 2: 运行 UI 测试确认失败**

Run: `python -m unittest tests.test_static_ui -v`  
Expected: 新增断言失败。

- [ ] **Step 3: 最小改造 HTML 结构**

将当前公告展示维持为单一表格面板：紧凑工具栏内放置“时间”下拉框、手动抓取、分析和现有结果视图筛选。时间选项严格为：今天、最近 2 天、最近 3 天。工具栏下增加一行可复用的活动任务状态容器。

- [ ] **Step 4: 实现前端行为与状态恢复**

在 `app.js`：
- 将三个时间选项映射为以本地当前日期为终点的日期范围；
- 使用它填充抓取请求，不改变后端的 `start_date/end_date` API；
- 首次加载、创建任务后、现有轮询过程中调用活动任务接口，渲染每个采集/分析任务的状态、处理数、候选数、失败数；
- 保持已有模型配置只写入浏览器 `localStorage` 的逻辑不变；
- 结果筛选改变时仅替换 `<tbody>`，不重建表头。

- [ ] **Step 5: 实现亮色紧凑样式**

在 `styles.css` 仅调整相关选择器：任务状态使用一行小型状态条；表格 `table-layout: fixed`，列宽由 `<colgroup>` 或既有固定类控制；`thead th` 继续 sticky。移除导致公告列表形成卡片/区块的样式，不改动未涉及的配置弹窗。

- [ ] **Step 6: 运行 UI 测试与 JavaScript 语法检查**

Run: `python -m unittest tests.test_static_ui -v`  
Expected: PASS。

Run: `node --check src/cninfo_miner/static/app.js`  
Expected: no output, exit 0。

### Task 5: 集成回归与本机验证

**Files:**
- No additional production files expected.

- [ ] **Step 1: 运行完整单元测试**

Run: `python -m unittest discover -s tests -v`  
Expected: 全部 PASS。

- [ ] **Step 2: 编译检查**

Run: `python -m compileall -q src`  
Expected: exit 0。

- [ ] **Step 3: 启动或重启本地服务并验证健康接口**

Run: 使用项目现有启动命令运行服务，然后 `Invoke-WebRequest http://127.0.0.1:8000/api/health`。  
Expected: HTTP 200，响应显示 `storage: sqlite`。

- [ ] **Step 4: 浏览器回归检查**

在 `http://127.0.0.1:8000/` 验证：
- 时间下拉框只显示三项；
- “时间/代码/简称/公告标题/分析结果”表头稳定；
- 刷新页面后活动任务状态仍可见；
- 机会列表不展示激励对象名单和购地进展公告；
- 点击分析后，回购进展及回购方案在完成后显示规定摘要。

- [ ] **Step 5: 提醒运行者的版本控制状态**

当前工作目录没有 `.git`，因此不执行 `git add`/`git commit`；如后续初始化仓库，再按任务粒度提交。
