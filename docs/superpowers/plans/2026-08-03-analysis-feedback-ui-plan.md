# 分析任务反馈与界面极简改版 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `test-driven-development` for each behavior change. This workspace is not a Git repository, so do not create commits.

**Goal:** 在不改动后端任务协议的前提下，提供清晰的分析任务反馈并将页面改造为深色极简工作台。

**Architecture:** 维持单页静态 HTML/CSS/JavaScript。前端在既有任务轮询上增加本地的按钮锁定、Toast 去重、状态中文映射和恢复分支；视觉层仅通过 CSS token、布局和状态类实现。

**Tech Stack:** FastAPI 静态文件、原生 HTML/CSS/JavaScript、Python unittest、Chrome CDP 烟雾测试。

---

### Task 1: 为反馈契约建立静态回归测试

**Files:**
- Modify: `tests/test_static_ui.py`
- Verify: `python -m unittest tests.test_static_ui -v`

- [ ] 编写静态断言，要求页面包含 `id="toast"`、`role="status"`、`aria-live="polite"`、任务状态条和忙碌按钮文案。
- [ ] 编写脚本断言，要求包含 `statusNames`、`showToast`、按钮禁用、终态去重标记、HTTP 404 分支和 `completed` 后刷新；要求不包含 `alert(`。
- [ ] 运行测试，确认在改动前失败。

### Task 2: 实现任务状态反馈

**Files:**
- Modify: `src/cninfo_miner/static/index.html`
- Modify: `src/cninfo_miner/static/app.js`
- Verify: `python -m unittest tests.test_static_ui -v`

- [ ] 加入可访问 Toast 容器和持久任务状态条结构。
- [ ] 用安全的 DOM API 实现 Toast、错误信息提取、按钮忙碌/恢复和状态渲染。
- [ ] 按现有 API 字段实现轮询：状态映射、任务完成/失败、404 清理、可恢复错误重试、终态消息去重和结果刷新。
- [ ] 运行静态测试确认通过。

### Task 3: 实现深色极简视觉层

**Files:**
- Modify: `src/cninfo_miner/static/styles.css`
- Verify: `python -m unittest tests.test_static_ui -v`

- [ ] 使用已批准的颜色 token、深色工作区、浅色结果区和状态条样式替换当前浅色渐变布局。
- [ ] 增加主按钮、禁用态、Toast、状态点、不确定型细进度条、标签页活动态和空状态的视觉反馈。
- [ ] 在 `700px` 断点下将控制区和标题区改为单列，避免横向溢出。
- [ ] 运行静态测试确认通过。

### Task 4: 全量验证与浏览器烟雾测试

**Files:**
- Verify: `tests/`
- Verify: `src/cninfo_miner/static/`

- [ ] 运行 `python -m unittest discover -s tests -v`。
- [ ] 运行 `python -m compileall -q src run.py`。
- [ ] 重启本地服务，使静态资源加载最新版本。
- [ ] 用独立 CDP 标签页确认桌面视觉、标签页、任务状态反馈以及在 700px 以下的布局；关闭测试标签页。
