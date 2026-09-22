---
name: google-seo-audit
description: 为公开网站或本地 Web 项目建立可计划、可执行、可复测的 Google SEO 技术审计；覆盖抓取、索引信号、内容和 JavaScript 渲染，并生成基线、修复计划与前后对比证据。
---

# Google SEO 审计工具

目标不是只给检查清单，而是形成可重复的闭环：`范围定义 → 基线采集 → 问题分级 → 修复计划 → 复测 → 变化记录`。

## 开始前

先取得网站 URL 或本地项目路径，并确认业务最重要的页面类型。缺少信息时，可先从主页、`robots.txt`、sitemap 和主要导航发现代表性 URL，再让用户补充优先页面。

把每次审计放在独立输出目录中，保留日期、范围、采集方式和限制。不要覆盖历史基线。

## 工作模式

### 1. 建立基线

公开网站优先运行：

```bash
python3 scripts/seo_audit.py audit https://example.com --sitemap --crawl --max-depth 5 --render --output audit-baseline.json
```

默认采用 sitemap 与受限同域递归发现的并集：sitemap 提供站点声明的 URL，递归爬取补充导航和正文链接中存在但未进入 sitemap 的页面。递归仅限同一主机、HTML 页面、默认 5 层且默认忽略查询参数；仍受全局 `--limit` 限制。需要审计参数化页面时才启用 `--include-query`，避免筛选、排序和追踪参数造成 URL 爆炸。

“全站审计”表示在上述边界内尽量覆盖可发现页面，不宣称数学意义上的绝对全量。报告必须列出 sitemap URL 数、递归新增 URL 数、最终去重 URL 数、达到的最大深度、是否触及上限，以及被排除的 URL 类型。

`--render` 会在环境已安装 Playwright 时采集渲染后 HTML，使用常规浏览器 UA、`domcontentloaded`、有限等待和自动重试。必须读取输出中的 `rendering.requested/succeeded/failed` 和逐页 `render_error`；不得把 `limitations: []`、浏览器成功启动或个别页面成功等同于全量渲染成功。

浏览器失败时先执行有限诊断：重试失败 URL、用新页面上下文复测代表性 URL，并核对普通 HTTP、DNS/TLS 与浏览器响应是否一致。完成这些步骤后仍失败，将其归为“渲染采集器证据缺口”，在摘要中用一行说明成功率和错误聚类；不要反复展开推测，也不要据此判断真实用户或 Google 无法访问。若原始 HTML 已包含标题、主要正文和关键链接，可对“不是空白客户端壳”给出高置信结论，但动态内容完整性仍标记未验证。

sitemap/递归模式默认最多审计 1000 个 URL，可用 `--limit` 调高或调低；大型站点应按页面模板分批，避免单次运行过久。

本地项目应先识别框架、路由和可用的预览命令。经用户授权启动本地预览后，对本地 URL 使用同一采集器。也要检查代码中生成 metadata、canonical、robots 和 sitemap 的实现位置。

### 2. 生成行动方案

读取 [报告与优先级规范](references/reporting.md)，将采集结果转换为两部分：

- 可观察的技术问题：每条包含 URL、证据、影响范围、建议改法、负责人/依赖、复测方法。
- 验证计划：明确成功条件、复测日期或触发条件，以及需要 Google Search Console 才能验证的指标。

优先级综合考虑严重度、受影响 URL 数量、页面业务价值、修复成本和证据置信度。优先处理阻断抓取/索引的问题与高影响低成本项。

### 3. 修复后复测

以相同范围和采集方式生成新快照，然后比较：

```bash
python3 scripts/seo_audit.py compare audit-baseline.json audit-after.json --output audit-diff.json
```

逐项判定为 `fixed`、`remaining`、`new` 或 `not_tested`。只有证据满足预先写明的成功条件，才标记已修复；范围、环境或采集方式不同造成的差异要单独说明。

## 必查证据

采集 HTTP 状态码、最终 URL、重定向、原始 HTML，以及条件允许时的浏览器渲染 HTML。检查 `robots.txt`、sitemap、robots meta / `X-Robots-Tag`、canonical、title、description、主要正文、可抓取链接和 JSON-LD。

对 SSG、预渲染或客户端 JavaScript 页面，比较原始与渲染结果，标出仅在客户端出现的关键标题、正文或链接。无法渲染时记录为证据缺口，不要猜测。

只把证据充分、可复现且需要行动的事项放入主问题清单。不确定假设、未完成检查和工具故障集中放在简短的“证据缺口”部分，不创建 P0–P3 修复项，除非已有独立证据证明实际问题。每项主问题优先写确定事实，再写有限影响；避免连续使用“可能、暂时、需确认”堆叠同一限制。

## 结论边界

将技术可观察结果与 Google 实际表现严格分开。没有 Search Console 数据时，不判断页面已被 Google 收录、排名是否变化或流量是否改善。取得 Search Console 后，可验证覆盖率、抓取、查询、点击、展示和平均排名趋势，但不要把相关性写成因果关系，也不要保证排名提升。
