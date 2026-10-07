# 渔港记忆展厅

从零搭建的渔港集体记忆数字展厅：Web 编辑台管理船只、行话、照片与口述；
HTTP/JSON API 维护史料陈述并走审校；每次发布冻结一张**内容图**并一致地
生成地图、逐字稿、无脚本目录与离线页。全部仅依赖 Python 3 标准库与 SQLite。

## 运行

```bash
python -m app.seed_demo data        # 可选：灌入演示数据
python -m app.server data 8070      # 启动
# 编辑台 http://127.0.0.1:8070/
# 公开版 http://127.0.0.1:8070/pub/  （发布前访问返回 404）
```

内置用户（请求头 `X-User-Id`，编辑台右上角切换）：
`1 editor1`、`2 editor2`（编辑）、`3 admin`（恢复版本/恢复撤稿需管理员）。

## 测试

```bash
python -m unittest discover -s tests -v
# 30 个用例：时间模型 / 领域规则 / 发布管线 / 五条验收剧情
```

## 需求落实对照

| 需求 | 实现 |
| --- | --- |
| Web 编辑船只、行话、照片、口述 | `app/web/editor.html` 单页编辑台，全部走 `/api/*` |
| API 维护史料陈述与审校 | `statements` 状态机 draft→in_review→approved/rejected，修订留痕 `statement_revisions` |
| 三个时间分开 | 每条史料 `occurred_at`（事件发生）/`collected_at`（资料采集）/`published_at`（公开），均为统一时间点 JSON |
| 未知年代用区间或未定 | `app/timeutil.py`：`exact` / `range` / `undated`，原始著录文字原样保留；非法日期直接拒绝 |
| 时间轴不靠导入顺序 | 排序只用年代物理区间；`undated` 排序键相同并沉底；重叠区间标 `weak/存疑`，不伪造先后 |
| 船会改名/转手/重建 | `ship_names`、`ship_owners`、`ship_events` 分表，带各自年代与来源 |
| 同名不自动认作同船 | 同名建两条独立船；系统不生成任何候选；候选必须人工创建 |
| 身份候选与关系证据 | `identity_candidates` + `identity_evidence(same/different)` |
| 合并可撤销并恢复引用 | 双编辑规则（创建者不能合并）；合并前整行备份到 `merge_ref_backups`；撤销恢复旧主档与全部引用，人工改指的行可标记保留 |
| 发布冻结 vs 动态最新 | `snapshot.build_snapshot` 冻结；`GET /api/diff` 对比冻结图与最新陈述，输出各类增/改/删 |
| 地图/逐字稿/无脚本目录一致生成 | `publisher.render_site` 只消费同一 snapshot；`verify_consistency` 交叉校验 GeoJSON、transcripts.json、catalog.txt、资源引用 |
| 同一照片多地点引用 | `photo_locations` 多对多；地图在每个地点重复标注同一照片（验收 A） |
| 受访者撤回一段 | 段落实体 `withdrawn` 标记，公开图剔除但库内保留文本（验收 B） |
| 两编辑合并船名 | 验收 C：第二编辑合并，可一键撤销恢复 |
| 音频时码缺失 | 段落实体 `missing_timecode`，页面/目录明确标注“时码缺失”，不猜时序（验收 D） |
| 离线页恢复旧资源 | 每版本自包含 `assets/`；`CURRENT` 原子指针；管理员可激活任意旧版本（验收 E） |
| 发布失败保留上个完整版本 | 渲染→校验→暂存目录→原子切换；任何异常清理暂存，指针不动；`fail_after_render` 演练注入故障 |
| 说明与授权分开追踪 | `caption_status` 与 `license_status` 独立流转；授权撤回时照片下架但说明保留 |
| 派生缩略图纳入撤稿 | 发布时为原片登记 `derived` 资产；撤稿级联 `assets.derived_from` 原片与全部派生件 |
| 显示不确定性与来源状态 | 时间轴/照片/陈述展示“年代区间/未定/存疑”、来源与审校徽章 |

## 模块

- `app/timeutil.py` 时间点模型与排序规则
- `app/db.py` SQLite schema（三类时间、身份、撤稿、发布版本表）
- `app/repo.py` 领域操作（身份合并/撤销、审校、撤稿级联、时间轴）
- `app/snapshot.py` 冻结内容图、冻结/动态对比、公开时间回填
- `app/publisher.py` 渲染、一致性校验、原子发布与版本恢复
- `app/server.py` HTTP API + 公开静态版本服务
- `app/seed_demo.py` 演示数据
