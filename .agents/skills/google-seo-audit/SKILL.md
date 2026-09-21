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
python3 scripts/seo_audit.py audit https://example.com --sitemap --render --output audit-baseline.json
```

`--render` 会在环境已安装 Playwright 时采集渲染后 HTML；若不可用，工具会记录限制并继续完成原始 HTML 审计。sitemap 模式默认最多审计 1000 个 URL，可用 `--limit` 调高或调低；大型站点仍应优先按页面模板和业务价值分批采集，避免一次任务运行过久。

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

## 结论边界

将技术可观察结果与 Google 实际表现严格分开。没有 Search Console 数据时，不判断页面已被 Google 收录、排名是否变化或流量是否改善。取得 Search Console 后，可验证覆盖率、抓取、查询、点击、展示和平均排名趋势，但不要把相关性写成因果关系，也不要保证排名提升。
