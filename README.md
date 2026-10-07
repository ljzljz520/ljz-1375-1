# 渔港记忆展厅

一个从空项目建起的“渔港记忆”数字展厅：**网页编辑**船只、行话、照片与口述；
**API 维护**史料陈述与审校；发布时把“已审校内容”**冻结成纯静态、可离线、
无需脚本**的展厅站点。全部使用 Python 3 标准库 + 原生前端，零第三方依赖。

## 运行

```bash
bash scripts/run.sh              # 或 python3 run.py
# 编辑器    http://localhost:8000/
# 公开展厅  http://localhost:8000/site/
```

首次启动自动建库并写入演示种子（两艘同名“鲁岱渔3301”、一张被两个地点
引用的照片、一段缺失时码的逐字稿、一个身份候选）。

测试：

```bash
bash scripts/test.sh              # 20 个测试（单元 + HTTP 端到端验收）
```

数据默认落在 `./data`，可用环境变量隔离：`YUGANG_DATA`、`YUGANG_DB`、
`YUGANG_ASSETS`、`YUGANG_RELEASES`、`YUGANG_PORT`、`YUGANG_EDITOR`。

## 目录

```
app/
  config.py      路径配置（可被环境变量覆盖）
  timeutil.py    事件时间模型：确切/约/区间/未定 + 时间轴排序
  db.py          SQLite 建表与种子
  serializers.py 行→字典
  store.py       业务：身份候选/合并撤销、审校、撤稿、时间轴
  content.py     动态内容图、冻结、冻结图↔最新图漂移比较
  assets.py      上传保存、派生缩略图（纯 stdlib PNG）、撤稿墓碑
  renderer.py    冻结图 → 纯静态站点（地图/逐字稿/无脚本目录同源）
  publisher.py   发布管线、校验、原子切换、失败保留旧版、回滚
  server.py      stdlib HTTP：编辑/审校 API + 编辑器 + 展厅托管
web/             原生 HTML/CSS/JS 编辑台
tests/           单元测试 + 五个验收场景端到端测试
```

## 三个时间，严格分开

每条史料都区分三个时间，界面与页面分别标注：

1. **事件发生时间** `event_time`：结构化、可不确定；
2. **资料采集时间** `collected_at`：采访/登记/入藏的时间；
3. **公开发布时间** `releases.created_at`：某一版展厅冻结上线的时间（页眉显示）。

未知年代不用假日期。事件时间支持四种形态：

```jsonc
{"type":"point","date":"1962-05","precision":"month"}        // 确切到月
{"type":"point","date":"1965","approx":true}                 // “约1965年”
{"type":"range","start":"1971","end":"1972"}                 // 区间
{"type":"range","start":null,"end":"1900"}                   // 只有上界
{"type":"undated","note":"档案残缺，无人记得"}               // 年代未定
```

**时间轴只按事件发生时间排序**：可排序的进时间轴（同年代并列用标题做稳定
次级键），缺少可用下界的区间与“未定”一律进入“年代未定”分组。排序绝不
使用行 ID、采集时间或导入顺序——最晚录入的最早事件仍会排到最前（见
`test_07_timeline_order_uses_event_time_not_import_order`）。页面用徽标
`确切/约/区间/未定` 显式表达不确定性。

## 船只身份：同名不自动认作同船

- 新建同名船只只是两艘独立船，身份关系进入 **identity_candidates（身份候选）**，
  候选挂 **identity_evidence（关系证据）**，证据可“支持”或“反证”，并标注
  类型（档案/口述/船名/照片/船体）。
- 同名、改名、转手、重建都不会自动合并；**两位编辑确认**后才执行合并。
- 合并是**可撤销**操作：合并时把改名史、转手史、事件、指向该船的陈述引用
  逐条记入 `boat_merge_items`；撤销时据此把身份、历史与引用**精确回迁**，
  并删除合并产生的别名、重开候选。已发布版本中的陈述另有
  `release_statements` 冻结快照，可追溯恢复。

## 发布：冻结图 vs 动态最新查询

- 平时 API/网页查询的是数据库里**最新**内容（动态图 `build_live_graph`，
  只收 `approved` 陈述与未撤回条目）。
- 点“发布”时 `freeze()` 生成**不可变内容图**（含当刻时间轴），算出内容
  哈希，整体渲染为静态站。`/api/diff` 比较“当前公开冻结图”和“动态查询
  最新图”，按区块列出新增/移除/变更与时间轴漂移。
- **地图、逐字稿、无脚本目录来自同一份冻结图**，一次发布内必然一致，
  不回查数据库。每个版本目录自包含（HTML + assets + manifest）。

发布管线步骤（`publisher.publish`）：

1. 冻结最新已审内容并计算哈希（可用 `expected_hash` 防止基于过期认知发布）；
2. 建 `building` 版本记录与独立暂存目录；
3. 拷贝原件/派生资源，对撤稿任务登记的资产写墓碑；
4. 渲染站点；
5. **链接与完整性校验**（HTML 引用、manifest 声明的文件/资源必须全部存在）；
6. 关闭已处理的撤稿任务、写冻结陈述快照；
7. 提交后以**符号链接原子替换** `releases/current`。

任何一步失败：回滚数据变更、标记版本为 `failed`、清理暂存目录，`current`
仍指向上个完整版本——**不发布半空页面**。`/api/validate` 可随时核对线上
版本完整性。历史版本完整保留，可一键**回滚**，离线恢复旧资源
（见 `test_06_failed_publish_keeps_previous_release`）。

## 照片：说明 / 授权分开；撤稿含派生缩略图

- `caption`（说明）与 `license`/`license_ref`（授权）是不同字段，
  分别有独立接口更新，改一个不会动另一个；公开展厅也分两行展示。
- 一张照片通过 `photo_refs` 被**多个地点/船只/口述**引用（M:N），引用不复制照片。
- 上传原件后服务端生成**派生缩略图**并登记为独立资源。撤稿时建立
  `retraction_tasks`，把**原件与派生缩略图都列入**；发布时这些资源随版
  替换为 `RETRACTED` 墓碑（撤稿照片不再出现在照片页，但旧版资源仍在）。

## 口述：可撤回单段；时码缺失显式处理

- 逐字稿按 `seq` 排序，每段可单独被受访者撤回；发布页以墓碑呈现
  “本段已由受访者撤回：原因”，正文不再泄露。整篇访谈也可撤稿。
- 允许**没有音频文件**（仅逐字稿）；段落允许**时码缺失**，以
  `timecode_missing` 显式标记，发布页显示“时码缺失”而非伪造一个时间码。

## 审校管线与来源状态

陈述状态：`draft → submitted → approved`（可 `rejected`/`retracted`）。
只有 `approved` 进入公开图与时间轴；已通过陈述若被编辑，自动回退为草稿
需重新审校，防止静默改动已公开内容。每条陈述记录来源类型/出处，页面
显示审校状态徽标与三个时间。

## 主要 API

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/state` | 编辑台整库视图 |
| GET | `/api/timeline` | 按事件时间分好序的时间轴 |
| POST | `/api/boats` `/api/boats/{id}/names|owners|events` | 船只与改名/转手/重建 |
| POST | `/api/candidates` `…/{id}/evidence` `…/merge` `…/reject` | 身份候选/证据/合并 |
| POST | `/api/merges/{id}/undo` | 撤销合并并恢复引用 |
| POST | `/api/jargons` `/api/places` | 行话、地点 |
| POST | `/api/photos` `…/raw` `…/caption` `…/license` `…/refs` `…/withdraw` | 照片 |
| POST | `/api/interviews` `…/segments` | 口述与逐字稿（可缺时码） |
| POST | `/api/segments/{id}/withdraw` | 受访者撤回单段 |
| POST | `/api/statements` `…/reviews` | 陈述与审校动作 |
| POST | `/api/publish` | 比较并发布（冻结→渲染→校验→原子切换） |
| GET | `/api/diff` `/api/releases` `/api/validate` | 漂移/版本/线上完整性 |
| POST | `/api/releases/{id}/rollback` | 回滚恢复旧版本（含旧资源） |

写接口用 `X-Editor-Id` 指定操作者（默认 1）。

## 验收用例对照

| 题目验收点 | 测试 |
|---|---|
| 同一张照片被多个地点引用 | `test_01_photo_referenced_by_multiple_places` |
| 受访者撤回一段口述 | `test_02_interviewee_withdraws_segment` |
| 两位编辑合并船名，且合并可撤销并恢复引用 | `test_03_merge_then_undo_restores_refs` |
| 音频时码缺失 | `test_04_missing_timecode_is_explicit` |
| 离线页恢复旧资源（发布/撤稿/回滚） | `test_05_publish_withdraw_failover_and_rollback` |
| 任务失败保留上个完整版本、不发布半空页面 | `test_06_failed_publish_keeps_previous_release` |
| 时间轴不靠导入顺序、未定年代分组 | `test_07_timeline_order_uses_event_time_not_import_order` |
| 冻结图 vs 动态最新陈述漂移比较 | `test_08_freeze_vs_live_drift_detected` |
| 说明与授权分开追踪 | `test_09_caption_and_license_tracked_separately` |
| 派生缩略图纳入撤稿任务 | `test_05`（断言 thumb 在任务资产中、随版成墓碑） |
| 网页显示不确定性与来源状态 | 渲染徽标 `确切/约/区间/未定` + 审校状态（time/render 单测与逐页核验） |
