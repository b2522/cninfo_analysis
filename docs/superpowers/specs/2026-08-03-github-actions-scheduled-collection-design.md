# GitHub Actions 定时公告抓取与分析

**日期：** 2026-08-03

## 目标

通过 GitHub Actions 每四小时运行一次公告抓取、去重和 LLM 分析，并将更新后的 SQLite 数据库提交回默认分支，供项目后续运行继续使用。

## 触发方式

- 定时：`0 */4 * * *`（UTC）。对应北京时间每日 00:00、04:00、08:00、12:00、16:00、20:00。
- 手动：`workflow_dispatch`。

GitHub Actions 的 cron 调度可能出现延后执行；工作流不依赖固定分钟的精确触发。

## 工作流设计

新增 `.github/workflows/collect-announcements.yml`，步骤如下：

1. 检出默认分支完整历史，以便向同一分支提交 SQLite 更新。
2. 配置 Python 3.13 并安装项目依赖。
3. 从仓库 Secrets 读取 `LLM_BASE_URL`、`LLM_API_KEY` 和 `LLM_MODEL`。
4. 使用现有 SQLiteRepository：创建采集任务并运行 CollectionWorker；再在内存中以 GitHub Secrets 构造 LlmConfig、创建分析任务并运行 AnalysisWorker。不得将 LLM 配置写入 SQLite。
5. 配置 GitHub Actions bot 的 Git 身份，只在 SQLite 文件发生变化时提交并推送。

## 约束与安全性

- 工作流仅提交 `data/cninfo_announcement_mining.sqlite3`；不提交日志、`.env` 或任何其他运行产物。
- workflow 设置 `permissions: contents: write`，以便 `GITHUB_TOKEN` 推送数据库更新。
- 使用工作流级 concurrency 锁，禁止同一仓库的两个采集任务同时修改 SQLite 文件。
- LLM 密钥只通过 GitHub Secrets 注入，不写入 SQLite、Git 历史或日志。
- 若抓取或分析失败，步骤失败并停止，数据库不会被提交。

## 验证

- 静态验证 YAML 可被 GitHub Actions 解析。
- 在手动触发后，检查 Actions 日志、SQLite 文件变更和机器人提交。

