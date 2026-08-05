# 巨潮资讯 · 公告聚合

抓取巨潮资讯（cninfo.com.cn）全文公告，持久化到 SQLite，并通过一个零依赖的单页面展示。
支持 **本地一键运行** 与 **Vercel Serverless 部署** 两种形态。

---

## 目录结构

```
.
├── main.py                        # 单文件后端：抓取 + SQLite + Flask API + 内嵌前端
├── index.html                     # 前端页面（内联 CSS/JS，无框架依赖）
├── requirements.txt               # requests + Flask（sqlite3 为标准库）
├── vercel.json                    # Vercel 路由 / 函数配置
├── announcements.db               # SQLite 数据（由 Actions 自动提交，作为只读兜底源）
├── api/
│   ├── announcements.py           # Vercel Serverless：GET /api/announcements
│   └── refresh.py                 # Vercel Serverless：POST /api/refresh
├── data/
│   └── snapshot.json              # 各区间快照（Actions 生成，Vercel 只读兜底源）
└── .github/workflows/cninfo.yml   # 每小时抓取（environment: cninfo）
```

---

## 一、本地运行

```bash
pip install -r requirements.txt
python main.py                     # 启动后台小时级抓取 + Web 服务
# 打开 http://localhost:5000
```

其它命令：

```bash
python main.py serve --port 8000 --no-scheduler   # 只起服务，不自动抓取
python main.py scrape --keep-days 30              # 抓一次就退出（Actions 用）
python main.py export-html                        # 把内嵌前端导出为 index.html
python main.py export-json                        # 导出 data/snapshot.json
```

> `main.py` 内嵌了一份完整的前端页面。即使把 `main.py` 单独拷走、目录里没有 `index.html`，
> `python main.py` 依然能正常提供完整页面——满足「单文件可启动」。

---

## 二、抓取逻辑

| 项目 | 实现 |
| --- | --- |
| 接口 | `POST https://www.cninfo.com.cn/new/hisAnnouncement/query` |
| 固定参数 | `pageSize=30`、`column=szse`、`tabName=fulltext` |
| 分页 | 手动「重新抓取」按钮 `MAX_PAGES=5`（150 条）；每小时定时任务 `SCHEDULE_MAX_PAGES=10`（300 条） |
| 去重 | `(stock_code, title, publish_time)` 三元组，DB 层同时用 `UNIQUE` 约束兜底 |
| PDF 地址 | `https://static.cninfo.com.cn/` + `adjunctUrl` |
| 时间处理 | `announcementTime`（毫秒时间戳）→ UTC+8 ISO 字符串 |

### `seDate` 抓取参数

POST 请求固定携带以下字段，`seDate` 传**空串**，表示**不限日期**，直接取巨潮返回的最新公告；如需限定区间，可显式传入 `2026-08-05~2026-08-06` 这类值（仍可手动调用 `build_se_date()` 生成）。

```text
pageNum=1&pageSize=30&column=szse&tabName=fulltext&plate=&stock=&searchkey=
&secid=&category=&trade=&seDate=&sortName=&sortType=&isHLtitle=true
```

> 早期版本曾按 UTC+8 时段（08:00–16:00 取今天+明天，其余仅今天）动态生成 `seDate`，
> 现已改为空串以简化逻辑、去掉时间依赖。

### 表结构

```sql
CREATE TABLE announcements (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_code   TEXT NOT NULL,     -- 代码
    short_name   TEXT,              -- 简称
    title        TEXT NOT NULL,     -- 公告标题
    publish_time TEXT NOT NULL,     -- ISO 格式，如 2026-08-05T11:42:12+08:00
    pdf_url      TEXT,              -- PDF 原始地址
    UNIQUE (stock_code, title, publish_time)
);
```

---

## 三、API

`GET /api/announcements?date_range=today`

`date_range` 取值与含义（以 UTC+8 当天为基准）：

| 值 | 界面文案 | 日期范围 |
| --- | --- | --- |
| `tomorrow` | 明天 | 明天当天 |
| `today` | 今天（默认） | 今天当天 |
| `last2` | 近2天 | 昨天 ~ 今天 |
| `last3` | 近3天 | 前天 ~ 今天 |

> 「近 N 天」= 含今天在内**向前**推算的自然日；「明天」用于查看提前披露的次日公告。
> 若想改成向后看，只需修改 `main.py` / `api/announcements.py` 顶部的 `DATE_RANGES` 字典。

响应示例：

```json
{
  "ok": true,
  "date_range": "today",
  "label": "今天",
  "start_date": "2026-08-05",
  "end_date": "2026-08-05",
  "count": 60,
  "source": "sqlite",
  "last_scraped_at": "2026-08-05T15:36:52+08:00",
  "data": [
    {
      "stock_code": "301149",
      "short_name": "隆华新材",
      "title": "关于完成工商变更登记并换发营业执照的公告",
      "publish_time": "2026-08-05T11:42:12+08:00",
      "pdf_url": "https://static.cninfo.com.cn/finalpage/2026-08-05/1225458732.PDF"
    }
  ]
}
```

其它端点：`POST /api/refresh`（立即抓取）、`GET /api/health`（本地版）。

---

## 四、前端

- 顶部日期下拉框切换时，仅重绘 `<tbody>`，`<colgroup>` 不动，因此**列宽恒定不变**，页面不刷新。
- 表格 `table-layout: fixed`，四列宽度：

| 列 | 宽度 | 对齐 |
| --- | --- | --- |
| 代码 | `8ch`（等宽字体，8 个数字字符） | 居中 |
| 简称 | `6em`（6 个汉字） | 居中 |
| 公告标题 | 剩余全部宽度 | **左对齐** |
| 公告时间 | `16ch`（正好是 `2026-08-05 11:42` 的长度） | 居中 |

- 表头：居中、加粗、背景 `#f5f5f5`、滚动时吸顶。
- 标题为超链接指向 `pdf_url`，带 `target="_blank" rel="noopener noreferrer"`，**在新标签页打开 PDF**，当前列表页的筛选状态不受影响。
- 原生 `fetch`，无任何框架/CDN 依赖；窄屏时表格横向滚动（`min-width: 760px`）。

---

## 五、GitHub Actions（每小时抓取）

`.github/workflows/cninfo.yml`，`environment: cninfo`，`cron: "5 * * * *"`（UTC，每小时第 5 分钟）。

流程：检出 → 抓取写入 `announcements.db` → 导出 `data/snapshot.json` →
（可选）推送到 Vercel KV → 提交回仓库 → 输出 Job Summary 表格。

需要在仓库 **Settings → Environments → 新建环境 `cninfo`** 后配置（可选）：

| Secret | 说明 |
| --- | --- |
| `KV_REST_API_URL` | Vercel KV / Upstash Redis 的 REST 地址 |
| `KV_REST_API_TOKEN` | 对应的读写 Token |

未配置时该步骤自动跳过，不影响其余流程。

> 数据每小时提交一次，`--keep-days 30` 会自动清理 30 天前的记录，防止库文件无限膨胀。

---

## 六、Vercel 部署与 SQLite 兼容方案

```bash
npm i -g vercel
vercel --prod
```

或直接在 Vercel 控制台导入 GitHub 仓库。

> ## ⚠️ 必做：把项目的 Framework Preset 设为 `Other`（其它）——否则必报错
>
> 本项目用 `api/*.py` 做 Serverless Functions（每个文件一个 `app = Flask()`），
> **不是**单一 Flask 框架应用。一旦 Vercel 把项目识别成 "Flask 框架"，它会去根目录
> 找单一入口点（`app.py` / `main.py` / `wsgi.py` / `api/index.py`），找不到就报：
> `No Flask entrypoint found in default locations, but found potential entrypoints:
> api/announcements.py (variable: app)`。这与之前的 `Found main.py ...` 是同一个根因。
>
> **修复步骤（控制台，最稳）：**
> 1. Vercel 控制台 → 项目 → **Settings → Framework Preset → 选 `Other`** → Save。
> 2. 本地重新执行 `vercel --prod`。
>
> **如果控制台已设 `Other` 但 CLI 仍报同样的错**（本地 `.vercel` 缓存了旧的框架配置）：
> - 删掉本地 `.vercel` 目录后重新 `vercel link`（重连时框架选 `Other`/None），或
> - 直接 `vercel rm <项目名>` 删掉项目，再 `vercel --prod` 重建（创建向导里框架选 `Other`）。
>   重建会清空该项目的环境变量，需重新配置 KV 等 Secret。
>
> `.vercelignore` 已排除 `main.py`，但**仅靠 `.vercelignore` 不够**——必须让云端
> Framework Preset = Other，Vercel 才会把 `api/*.py` 当函数、关闭 Flask 框架检测。

`api/announcements.py`、`api/refresh.py` 自动识别为 Python Serverless Functions，
`index.html` 由 Vercel 静态托管。

### Vercel 上的定时更新（Hobby 账户）

> **`vercel.json` 里已不再配置 `crons`。** 原因：Vercel Hobby 账户只允许**每天一次**
> 的 Cron Job，而本项目需要每小时刷新；`7 * * * *`（每小时）会触发
> `Hobby accounts are limited to daily cron jobs` 报错。
>
> 替代方案（免费、符合限制、且只抓「定时 10 页」逻辑不动）：
>
> 1. **GitHub Actions 每小时抓取并向仓库提交 `data/snapshot.json`**（已是现状，
>    `cron: "5 * * * *"`，`environment: cninfo`，抓取前 10 页共 300 条）。
> 2. 在 Vercel 控制台开启 **Deployment → Deploy on push to production branch**，
>    Actions 提交数据后会自动触发一次重新部署，重新打包最新的 `data/snapshot.json`。
>    → 这样 Vercel 上的数据同样是**每小时**更新，且不占用 Vercel 自己的 cron 配额。
>
> 若配置了 KV（方案 1），`/api/announcements` 直接读 KV 同样无需 cron；
> 若什么都没配，页面仍可用方案 5「实时回源」兜底（点击「重新抓取」按钮现抓）。
>
> 手动触发重新部署的命令（可选）：
> `vercel --prod`（或用 GitHub Actions 末尾加一步调用 Vercel Deploy Hook）。


### 为什么不能直接用 SQLite

Vercel Serverless Functions 的文件系统是**只读**的，唯一可写目录是 `/tmp`，
而 `/tmp` 只属于单个实例、随实例回收而消失。因此 `announcements.db` 在 Vercel 上
**只能读、不能持久写**。`api/announcements.py` 采用五级降级来解决：

| 优先级 | 数据源 | 说明 |
| --- | --- | --- |
| 1 | **Vercel KV / Upstash Redis** | 配置 `KV_REST_API_URL` + `KV_REST_API_TOKEN` 后，Actions 每小时推快照，函数直接读，**无需重新部署即可更新数据**。推荐方案。 |
| 2 | `data/snapshot.json` | Actions 提交的 JSON 快照，随部署分发。零成本，但数据新鲜度取决于最近一次部署。 |
| 3 | `announcements.db` | 随仓库打包的只读 SQLite，用 `file:...?mode=ro` 打开。 |
| 4 | `/tmp/announcements.db` | 同实例热缓存，TTL 15 分钟。 |
| 5 | **实时回源 cninfo** | 前三者都拿不到数据时直接现抓前 5 页，并回写 `/tmp` 缓存。保证页面永远有数据。 |

推荐组合：**方案 1（KV）+ 方案 5（兜底）**。
在 Vercel 项目里创建一个 KV / Upstash 集成后，Vercel 会自动注入 `KV_REST_API_URL`
与 `KV_REST_API_TOKEN` 到函数环境；把同一对值配到 GitHub Environment `cninfo` 的
Secrets 里，写入链路就打通了。

若完全不想接 KV，只用方案 2/3 也能跑——数据更新的方式见上方「Vercel 上的定时更新（Hobby 账户）」，
核心就是开启 Deploy on push，让 Actions 每小时的数据提交自动触发重新部署。

---

## 七、注意事项

- `column=szse` 是巨潮的全文检索通道，返回结果同时覆盖深市与沪市（含科创板/创业板）。
- 手动「重新抓取」抓前 5 页共 150 条；每小时定时任务抓前 10 页共 300 条。如需调整，分别改 `MAX_PAGES` / `SCHEDULE_MAX_PAGES`（同时注意巨潮的频率限制）。
- 抓取带有 3 次退避重试与 0.4s 页间限速，避免触发风控。
- 非交易日接口返回空列表属正常现象，Actions 不会因此失败。
