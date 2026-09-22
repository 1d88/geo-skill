# Google Search Console：只读采集与证据验证

用于用户提供指定 GSC 属性、日期范围或已导出快照，要求用 Google 数据验证网站审计结论的任务。GSC 只能辅助验证搜索表现和索引记录，不能证明生成式 AI 引用率。

## 权限与账号

优先由宿主应用的独立采集进程调用官方 API，再将 JSON 快照提供给 Codex。采集进程和 Codex 使用不同的系统身份或容器；密钥文件不在 Codex 可读路径中，快照只读挂载，报告另写目录。单纯不传递环境变量不等于文件权限隔离。

仅请求 `https://www.googleapis.com/auth/webmasters.readonly`。采集器验证实际 token scope，拒绝缺少 readonly 或含 `webmasters` 读写 scope 的 token。Google 用户 OAuth 由宿主应用完成授权并维护 token；服务账号模式由管理员把专用账号加入指定 GSC 属性，并配置仅采集进程可读的密钥文件。先给予所需的最低属性权限；若某个接口返回 403，明确记录未采集，不自动提升权限。

不要要求用户提供账号密码，不操作已登录 GSC 的浏览器，不提交收录、sitemap，不添加/删除属性，不调用写接口。也不要把原始服务账号密钥或 OAuth refresh token 放进模型上下文、prompt、任务目录或日志。

## 外部采集器

`scripts/gsc_collect.py` 仅实现 `sites.get`、`searchanalytics.query`、`urlInspection.index.inspect`、`sitemaps.list`。两个 POST 方法均为官方只读查询，不能仅依据 HTTP 动词判断是否写操作。接口地址和方法由脚本固定，无通用 URL、方法或 SQL 转发参数。

在独立采集环境运行，以下日期及属性仅作示例，必须替换为用户指定的范围：

```bash
python3 scripts/gsc_collect.py \
  --site sc-domain:example.com \
  --datasets site pages queries inspection sitemaps \
  --start-date 2026-08-01 --end-date 2026-08-28 \
  --compare-previous \
  --urls-file /collector/input/urls.txt \
  --output-dir /collector/snapshots/audit-001
```

认证二选一：

- 宿主在采集进程环境注入短期 `GSC_ACCESS_TOKEN`，不能通过命令行参数明文传 token；stdlib 足以运行。OAuth 登录与 refresh token 管理由宿主负责，脚本不实现交互式 OAuth 登录。
- 服务账号模式增加 `--credentials /collector/secrets/gsc-reader.json`。仅采集环境安装 `python3 -m pip install -r scripts/requirements-gsc.txt`；脚本只申请 readonly scope，并自动刷新。

不要直接将宿主 `process.env` 全量传给 Codex。提供给 Codex 的只有最终快照目录。

`--datasets` 必填，只采集所选的数据。`site` 读取站点整体与逐日表现；`pages` 按页面读取；`queries` 仅查询 `--urls-file` 中精确页面的查询词；`inspection` 仅检查该 URL 清单；`sitemaps` 读取提交信息。日期只对表现数据必填。URL 清单需一行一个完整 URL，拒绝跨属性 URL。

`--max-rows` 默认 50000，是每个表现查询的本地上限，Google API 单次最多 25000 行，脚本自动分页并记录是否达到本地上限。分页完成不保证获得所有查询词或页面。采集器只设置单次 HTTP 请求超时及有限重试，不限制整个任务总时长。

输出目录必须是新目录，不覆盖旧基线。`manifest.json` 包含属性、日期、时区、所选数据、文件 SHA-256、采集错误和 `complete/partial` 状态。`complete` 只表示所请求的 API 调用成功，不表示站点或搜索词全量覆盖。中途失败保留已获得的文件；退出码 0 表示采集请求完成，2 表示部分数据失败，1 表示输入/授权/文件错误。

## Codex 如何使用快照

1. 只读取用户或宿主提供的快照。先核对 manifest 的 property、日期、datasets、错误及各文件 SHA-256；不自行扩展账号、属性或时间范围。
2. 保留原始快照，派生摘要另写。缺文件或校验失败的相关数据不得写为已验证；快照为 `partial` 时按数据集判断，已成功且校验通过的文件仍可支持其范围内的结论，失败部分保持未知；用户手工导出没有 manifest 时明确标注为手工来源，不伪造校验或完整性。
3. 对照网站本次采集证据与 GSC 记录。逐 URL 使用原始 URL，不随意去尾斜杠、合并路径或参数。针对页面查询词，只使用带精确页面过滤的结果。
4. 将“技术复测结果”“Google 索引记录”“搜索表现对比”分开写，每项带采集时间、日期范围或 `lastCrawlTime`。Google 抓取时间早于修复时间，应写“等待 Google 重抓”，不能判定修复失败。

## 解释边界

- 日维度日期使用 GSC 的 `America/Los_Angeles` 口径。`dataState: final` 排除未完成数据，但不保证请求结束日已有数据；缺失日不自动补零。
- 站点总量独立查询，页面/查询词/设备/国家表不相加当作站点总量。匿名化查询和内部主要行限制可造成明细与总量不同。
- `clicks/impressions/ctr/position` 的变化是观察结果；不能仅凭修复前后对比断言因果或承诺排名提升。
- URL Inspection 查询 Google 已知的索引版本，不是实时测试，也不会请求收录。请求失败、未检查、Google 返回未收录是不同状态。
- sitemap 读取成功不代表所有 URL 已索引。只对实际检查的 URL 给出索引结论。
- 没有 GSC 快照仍可完成网站技术审计，只需标记“GSC 未验证”，不要反复猜测缺失证据。

## 官方参考

- https://developers.google.com/webmaster-tools/v1/how-tos/authorizing
- https://developers.google.com/webmaster-tools/v1/searchanalytics/query
- https://developers.google.com/webmaster-tools/v1/urlInspection.index/inspect
- https://developers.google.com/webmaster-tools/v1/sites/get
- https://developers.google.com/webmaster-tools/v1/sitemaps/list
