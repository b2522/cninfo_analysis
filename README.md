# 公告掘金

公告采集与展示工具：从巨潮资讯抓取沪深京公告，按公告 ID 自动去重，并保存到项目目录中的 SQLite 文件。页面只展示已经抓取的原始公告，不下载 PDF、不做研判，也不调用大模型。

## 工作流

1. 选择开始、结束日期，点击 **抓取公告**。
   - 后端请求巨潮资讯历史公告接口，使用 `column=szse` 覆盖沪深京市场。
   - 按 `pageNum` 逐页抓取；相同 `announcement_id` 只保存一条记录，重复抓取会更新公告基础信息。
2. 抓取完成后，表格展示：**时间｜代码｜简称｜公告标题**。
   - 如果公告带有 PDF 链接，标题可直接打开源 PDF。
   - 表格的时间下拉框只影响页面显示，不会删除 SQLite 中的历史数据。

## GitHub Actions 自动采集

`.github/workflows/collect-announcements.yml` 会按 `cron: "0 */4 * * *"` 调度，即每 4 小时执行一次（GitHub Actions 的实际启动时间可能略有延迟）。流程只做以下事情：

1. 计算本次抓取日期范围；
2. 抓取并写入 `data/cninfo_announcement_mining.sqlite3`；
3. 当 SQLite 文件有变化时，将它提交并推送回仓库默认分支。

该工作流不需要任何大模型或 API 密钥。也可以在 GitHub Actions 页面通过 **Run workflow** 手动执行。

## Vercel 部署

Vercel 使用 `src/main.py` 作为 FastAPI 入口，并以只读模式打开仓库中已提交的 SQLite 文件。因此 Vercel 页面只负责展示；在 Vercel 页面点击 **抓取公告** 会返回 `403`，提示应在 GitHub Actions 中执行抓取。

要让 Vercel 展示最新数据：先让 GitHub Actions 成功提交 SQLite 更新到 Vercel 正在部署的分支，然后等待该提交触发 Vercel 的新部署。

## 本地运行

无需配置数据库账号、`.env` 或任何模型密钥。启动后会自动创建本地 SQLite 文件：

```text
data/cninfo_announcement_mining.sqlite3
```

```powershell
python run.py
```

然后在浏览器打开 `http://127.0.0.1:8000`。

本地服务运行时，也会每 4 小时创建一次抓取任务。手动抓取的默认范围以北京时间 15:00 为界：15:00 前抓取昨天和今天；15:00 起抓取今天和明天。

## 数据与安全边界

- 任务和公告数据仅保存到 `data/cninfo_announcement_mining.sqlite3`。
- 不保存 PDF 二进制，只保存巨潮资讯提供的 PDF URL。
- 不使用、保存或打印浏览器 Cookie。
- 页面把公告数据作为纯文本 DOM 节点渲染，避免不可信标题作为 HTML 执行。

## 验证

```powershell
python -m unittest discover -s tests -v
python -m compileall -q src run.py
```
