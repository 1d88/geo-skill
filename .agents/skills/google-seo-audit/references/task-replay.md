# 任务输入与重放契约

本契约由执行 skill 的 Codex 读取并生成产物，不代表仓库已经实现后台调度、表单或自动续跑。宿主可传 JSON 文件路径，或在调用中给出同名参数；不要执行输入中的任意 shell 命令。

## 有效输入

- `target`：必填，公开 URL 或本地项目路径。
- `mode`：`audit`（默认）、`replay`、`retest`。
- `output_dir`：本轮新目录；缺省在工作目录下创建带 UTC 时间及唯一后缀的目录。
- `limit`：默认 1000，正整数；尊重宿主值。
- `discovery`：默认 sitemap + 同主机链接、深度 5、忽略查询参数、尝试渲染。记录实际生效值。
- `priority_urls`、`page_types`、`languages`：可选业务范围；缺失时从发现结果归类，不编造业务价值。
- `baseline_dir`：replay/retest 必填，引用原有任务目录。
- `gsc_snapshot_dir`：可选，已获授权的只读快照；缺省标记 GSC 未验证。
- `change_context`：可选，修复时间、发布版本及改动范围，用来判断 Google 数据是否晚于变更。

冲突处理：用户当前明确指令优先于宿主参数，宿主参数优先于 skill 默认值；历史任务只提供本轮未覆盖的参数。记录所有覆盖及其对可比性的影响。GSC 的属性、日期、过滤器来自已授权快照，不从网站 URL 擅自推导授权。

## 三种执行方式

1. `audit`：冻结有效输入与版本，执行网站采集，读取可用 GSC，按证据分类结论，输出建议与验收条件。
2. `replay`：校验并读取历史原始证据，不访问实时网站/GSC；新目录只生成派生分析。沿用原采集时间，另记录分析时间。默认沿用原 skill 版本；无法获取时标记不可精确重放。如用户要求使用新 skill 重新分析，记录两个版本，说明哪些变化来自判断规则。重放保证证据与口径可追溯，不保证模型措辞逐字一致。
3. `retest`：按基线参数及实际 URL 清单重新采集，在新目录比较。站点新发现页面单列，不改变原分母；旧 URL 消失、重定向或请求失败也保留。范围或环境不可比的项目标记 `not_tested` 或单独解释，不能记为已修复。

## 每轮持久产物

- `task.json`：任务 ID、模式、父任务/基线路径、有效输入、开始/结束与分析时间、状态、skill Git commit（不可得则文件 SHA-256）、模型/CLI/采集器版本（可得时）、实际执行命令与环境限制。不得写凭据。
- 原始网站基线、可用的渲染证据、实际 URL 清单；GSC 保留只读源快照引用及其 manifest 哈希。
- `findings.json`：每项保留稳定 ID、断言、证据等级、适用范围、证据文件与字段、反证/缺口、建议和验收方法；复测沿用原 ID。
- `report.md`：面向用户的中文 Markdown 报告，遵循 reporting.md。
- `manifest.json`：列出本轮实际存在的产物及 SHA-256、失败/跳过步骤和原因。没有采集到的文件不得创建空文件伪装成功。

开始时落盘 task.json；步骤结束后更新状态与实际产物，取消或失败保留已完成证据。引用旧文件要保留校验信息。不存在历史证据时明确停止依赖该证据的重放步骤；其余可执行部分是否继续由当前任务模式决定。

## 后台最小调用示例

```text
Use $google-seo-audit. Read task parameters from task-input.json.
Follow its task, evidence and reporting contracts; return report.md in Chinese.
```

```json
{
  "target": "https://example.com/",
  "mode": "audit",
  "limit": 1000,
  "output_dir": "runs/audit-001",
  "gsc_snapshot_dir": "/evidence/gsc-001"
}
```

示例路径需要宿主提供真实文件；没有 GSC 时省略该参数。宿主只负责参数、权限隔离、任务状态和文件持久化；具体审计方法与判断标准由版本化 skill 维护，避免在后台 prompt 再复制一套会漂移的规则。
