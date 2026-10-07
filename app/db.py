"""SQLite 连接、建表（含三个时间字段的贯穿设计）与初始化种子数据。"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from . import config, timeutil

SCHEMA = """
CREATE TABLE IF NOT EXISTS editors (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'editor'           -- editor | reviewer
);

-- 船只：同名不自动认作同船，身份另由 candidates / merges 维护
CREATE TABLE IF NOT EXISTS boats (
    id INTEGER PRIMARY KEY,
    current_name TEXT NOT NULL,
    hull_no TEXT,
    built_event_time TEXT,                        -- 结构：{"type":...}
    status TEXT NOT NULL DEFAULT 'active',        -- active | merged_into | retired
    merged_into_id INTEGER REFERENCES boats(id),
    notes TEXT,
    created_at TEXT NOT NULL,                     -- 系统时间（入库）
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS boat_names (           -- 改名史
    id INTEGER PRIMARY KEY,
    boat_id INTEGER NOT NULL REFERENCES boats(id),
    name TEXT NOT NULL,
    event_time TEXT,
    note TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS boat_owners (          -- 转手史
    id INTEGER PRIMARY KEY,
    boat_id INTEGER NOT NULL REFERENCES boats(id),
    owner TEXT NOT NULL,
    event_time TEXT,
    note TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS boat_events (          -- 重建/沉毁等，进入时间轴
    id INTEGER PRIMARY KEY,
    boat_id INTEGER NOT NULL REFERENCES boats(id),
    kind TEXT NOT NULL,                           -- built | rebuilt | renamed | ownership | retired ...
    title TEXT NOT NULL,
    event_time TEXT,
    detail TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS persons (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    role TEXT,                                   -- 渔民/受访者/船匠 ...
    bio TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS places (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    lat REAL,
    lng REAL,
    kind TEXT,                                   -- 码头/滩涂/船厂 ...
    note TEXT,
    created_at TEXT NOT NULL
);

-- 照片：说明(caption) 与 授权(license) 分开追踪
CREATE TABLE IF NOT EXISTS photos (
    id INTEGER PRIMARY KEY,
    caption TEXT,
    license TEXT,                                -- 授权状态，独立于说明
    license_ref TEXT,
    asset_path TEXT,                             -- 原件
    thumb_path TEXT,                             -- 派生缩略图（同属撤稿对象）
    place_id INTEGER REFERENCES places(id),
    taken_event_time TEXT,                       -- 拍摄时间（不确定）
    collected_at TEXT NOT NULL,                  -- 【资料采集时间】
    withdrawn INTEGER NOT NULL DEFAULT 0,
    withdrawn_at TEXT,
    withdrawn_reason TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS photo_refs (          -- 照片被多地点/条目引用（M:N）
    id INTEGER PRIMARY KEY,
    photo_id INTEGER NOT NULL REFERENCES photos(id),
    ref_table TEXT NOT NULL,                     -- places | boats | interviews
    ref_id INTEGER NOT NULL,
    note TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(photo_id, ref_table, ref_id)
);

CREATE TABLE IF NOT EXISTS jargons (
    id INTEGER PRIMARY KEY,
    term TEXT NOT NULL,
    pronunciation TEXT,
    meaning TEXT,
    example TEXT,
    collected_at TEXT NOT NULL,
    withdrawn INTEGER NOT NULL DEFAULT 0,
    withdrawn_at TEXT,
    withdrawn_reason TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS interviews (
    id INTEGER PRIMARY KEY,
    person_id INTEGER REFERENCES persons(id),
    title TEXT NOT NULL,
    audio_path TEXT,
    place_id INTEGER REFERENCES places(id),
    collected_at TEXT NOT NULL,                  -- 【资料采集时间】
    withdrawn INTEGER NOT NULL DEFAULT 0,
    withdrawn_at TEXT,
    withdrawn_reason TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS interview_segments (
    id INTEGER PRIMARY KEY,
    interview_id INTEGER NOT NULL REFERENCES interviews(id),
    timecode TEXT,                               -- 可能缺失 -> NULL
    timecode_missing INTEGER NOT NULL DEFAULT 0,
    seq INTEGER NOT NULL,
    speaker TEXT,
    text TEXT NOT NULL,
    withdrawn INTEGER NOT NULL DEFAULT 0,        -- 受访者可撤回单段
    withdrawn_at TEXT,
    withdrawn_reason TEXT,
    created_at TEXT NOT NULL
);

-- 史料陈述：审校管线。API 只维护陈述，网页展示其状态
CREATE TABLE IF NOT EXISTS statements (
    id INTEGER PRIMARY KEY,
    subject_table TEXT NOT NULL,                 -- boats | persons | places | jargons ...
    subject_id INTEGER,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    event_time TEXT,                            -- 【事件发生时间】（不确定）
    collected_at TEXT,                          -- 【资料采集时间】
    source_type TEXT,                           -- interview | document | photo | oral
    source_ref TEXT,
    status TEXT NOT NULL DEFAULT 'draft',        -- draft | submitted | approved | rejected | retracted
    created_by INTEGER REFERENCES editors(id),
    created_at TEXT NOT NULL,                   -- 系统时间
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS statement_reviews (
    id INTEGER PRIMARY KEY,
    statement_id INTEGER NOT NULL REFERENCES statements(id),
    editor_id INTEGER NOT NULL REFERENCES editors(id),
    action TEXT NOT NULL,                        -- submit | approve | reject | retract
    note TEXT,
    created_at TEXT NOT NULL
);

-- 身份候选与关系证据：同名只生成候选，不自动合并
CREATE TABLE IF NOT EXISTS identity_candidates (
    id INTEGER PRIMARY KEY,
    boat_a INTEGER NOT NULL REFERENCES boats(id),
    boat_b INTEGER NOT NULL REFERENCES boats(id),
    relation TEXT NOT NULL,                      -- same_vessel | renamed | rebuilt | unknown
    confidence TEXT NOT NULL DEFAULT 'possible', -- possible | likely
    status TEXT NOT NULL DEFAULT 'open',         -- open | merged | rejected
    created_by INTEGER REFERENCES editors(id),
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS identity_evidence (
    id INTEGER PRIMARY KEY,
    candidate_id INTEGER NOT NULL REFERENCES identity_candidates(id),
    kind TEXT NOT NULL,                          -- name | document | oral | photo | hull
    detail TEXT NOT NULL,
    supports INTEGER NOT NULL DEFAULT 1,
    created_by INTEGER REFERENCES editors(id),
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS boat_merges (         -- 可撤销的合并日志
    id INTEGER PRIMARY KEY,
    surviving_boat_id INTEGER NOT NULL REFERENCES boats(id),
    absorbed_boat_id INTEGER NOT NULL REFERENCES boats(id),
    undone INTEGER NOT NULL DEFAULT 0,
    undone_at TEXT,
    created_by INTEGER REFERENCES editors(id),
    created_at TEXT NOT NULL
);
-- 合并时被迁移的每条记录，撤销时据此精确回迁、恢复引用
CREATE TABLE IF NOT EXISTS boat_merge_items (
    id INTEGER PRIMARY KEY,
    merge_id INTEGER NOT NULL REFERENCES boat_merges(id),
    ref_table TEXT NOT NULL,          -- boat_names | boat_owners | boat_events | statements
    ref_id INTEGER NOT NULL,
    moved INTEGER NOT NULL DEFAULT 1
);

-- 撤稿任务：撤照片时派生缩略图一并纳入
CREATE TABLE IF NOT EXISTS retraction_tasks (
    id INTEGER PRIMARY KEY,
    target_table TEXT NOT NULL,                  -- photos | interview_segments | interviews | jargons | statements
    target_id INTEGER NOT NULL,
    reason TEXT,
    status TEXT NOT NULL DEFAULT 'open',         -- open | done
    assets_json TEXT NOT NULL DEFAULT '[]',      -- 需移除的原件/派生资源
    created_by INTEGER REFERENCES editors(id),
    created_at TEXT NOT NULL,
    done_at TEXT
);

CREATE TABLE IF NOT EXISTS releases (
    id INTEGER PRIMARY KEY,
    content_hash TEXT NOT NULL,
    graph_json TEXT NOT NULL,                    -- 冻结内容图
    dir TEXT NOT NULL,
    manifest_json TEXT NOT NULL,
    created_at TEXT NOT NULL,                    -- 【公开发布时间】
    state TEXT NOT NULL DEFAULT 'building',      -- building | failed | superseded | current | rolled_back
    note TEXT,
    rollback_of INTEGER REFERENCES releases(id)
);

-- 发布版与陈述的冻结关系（撤销合并/撤回时可据此恢复引用）
CREATE TABLE IF NOT EXISTS release_statements (
    release_id INTEGER NOT NULL REFERENCES releases(id),
    statement_id INTEGER NOT NULL REFERENCES statements(id),
    version_snapshot TEXT NOT NULL,
    PRIMARY KEY (release_id, statement_id)
);

CREATE INDEX IF NOT EXISTS idx_stmts_subject ON statements(subject_table, subject_id);
CREATE INDEX IF NOT EXISTS idx_photos_place ON photos(place_id);
CREATE INDEX IF NOT EXISTS idx_seg_interview ON interview_segments(interview_id);
"""


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    db_path = Path(db_path or config.DB_PATH)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(seed: bool = True) -> None:
    conn = connect()
    try:
        conn.executescript(SCHEMA)
        conn.commit()
        if seed and conn.execute("SELECT COUNT(*) c FROM editors").fetchone()["c"] == 0:
            _seed(conn)
        conn.commit()
    finally:
        conn.close()


def _et(payload):
    return json.dumps(timeutil.normalize_event_time(payload), ensure_ascii=False)


def _seed(conn: sqlite3.Connection) -> None:
    now = timeutil.now_iso()
    conn.executemany(
        "INSERT INTO editors(id,name,role) VALUES (?,?,?)",
        [(1, "阿芳（编辑）", "editor"), (2, "老周（审校）", "reviewer")],
    )
    # 地点（其中两个将引用同一张照片 -> 验收点1）
    conn.executemany(
        "INSERT INTO places(id,name,lat,lng,kind,note,created_at) VALUES (?,?,?,?,?,?,?)",
        [
            (1, "东码头", 30.012, 121.988, "码头", "渔船靠泊卸渔获处", now),
            (2, "修船厂滩涂", 30.005, 121.971, "船厂", "木船上岸大修处", now),
        ],
    )
    conn.executemany(
        "INSERT INTO persons(id,name,role,bio,created_at,updated_at) VALUES (?,?,?,?,?,?)",
        [(1, "陈阿毛", "老船长/受访者", "1950年代起随船出海", now, now)],
    )
    # 两艘同名“鲁岱渔3301”，身份存疑 -> 不自动认作同船
    conn.executemany(
        "INSERT INTO boats(id,current_name,hull_no,built_event_time,status,notes,created_at,updated_at)"
        " VALUES (?,?,?,?,?,?,?,?)",
        [
            (1, "鲁岱渔3301", "HD-3301-A", _et({"type": "point", "date": "1958"}),
             "active", "东码头老木船，曾改名", now, now),
            (2, "鲁岱渔3301", None, _et({"type": "range", "start": "1978", "end": "1982"}),
             "active", "修船厂档案中的同名号船，待考证", now, now),
            (3, "浙奉机127", "FB-127", _et({"type": "point", "date": "1965", "approx": True}),
             "active", "机动船", now, now),
        ],
    )
    conn.executemany(
        "INSERT INTO boat_names(boat_id,name,event_time,note,created_at) VALUES (?,?,?,?,?)",
        [(1, "鲁岱渔33", _et({"type": "point", "date": "1962"}), "1962年改名前船名", now)],
    )
    conn.executemany(
        "INSERT INTO boat_owners(boat_id,owner,event_time,note,created_at) VALUES (?,?,?,?,?)",
        [(1, "陈阿毛", _et({"type": "range", "start": "1968", "end": "1990"}), "购入经营", now)],
    )
    conn.executemany(
        "INSERT INTO boat_events(boat_id,kind,title,event_time,detail,created_at) VALUES (?,?,?,?,?,?)",
        [
            (1, "built", "鲁岱渔3301 建造",
             _et({"type": "point", "date": "1958"}), "东码头船匠建造", now),
            (1, "rebuilt", "鲁岱渔3301 大修重建", _et({"type": "range", "start": "1971", "end": "1972"}),
             "更换龙骨与三块舷板", now),
            (3, "built", "浙奉机127 建造（年代约）", _et({"type": "point", "date": "1965", "approx": True}),
             "受访者回忆，待核", now),
        ],
    )
    # 行话
    conn.execute(
        "INSERT INTO jargons(id,term,pronunciation,meaning,example,collected_at,created_at,updated_at)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (1, "扯篷", "tsa ban", "升帆，喻开船出海", "“早点扯篷，赶潮水。”", now, now, now),
    )
    # 照片：说明与授权分开；稍后由测试/上传补资源文件
    conn.execute(
        "INSERT INTO photos(id,caption,license,license_ref,place_id,taken_event_time,collected_at,created_at)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (1, "东码头晨雾中归航的渔船", "CC BY-SA 4.0", "家属授权书#7", 1,
         _et({"type": "range", "start": "1980", "end": "1985"}), now, now),
    )
    # 同照片被第二地点引用
    conn.execute(
        "INSERT INTO photo_refs(photo_id,ref_table,ref_id,note,created_at) VALUES (?,?,?,?,?)",
        (1, "places", 1, "主拍摄地", now),
    )
    conn.execute(
        "INSERT INTO photo_refs(photo_id,ref_table,ref_id,note,created_at) VALUES (?,?,?,?,?)",
        (1, "places", 2, "修船期间亦在此展示", now),
    )
    # 口述访谈：三段，第二段缺时码
    conn.execute(
        "INSERT INTO interviews(id,person_id,title,place_id,collected_at,created_at) VALUES (?,?,?,?,?,?)",
        (1, 1, "陈阿毛口述：3301 的来历", 1, now, now),
    )
    conn.executemany(
        "INSERT INTO interview_segments(interview_id,timecode,timecode_missing,seq,speaker,text,created_at)"
        " VALUES (?,?,?,?,?,?,?)",
        [
            (1, "00:00:12", 0, 1, "陈阿毛", "这艘3301是五八年东码头打造的，早先叫鲁岱渔33。", now),
            (1, None, 1, 2, "陈阿毛", "修船厂那边档案也有艘3301，我看不一定是同一艘。", now),
            (1, "00:02:40", 0, 3, "陈阿毛", "“扯篷”就是升帆，赶潮水出海。", now),
        ],
    )
    # 史料陈述（一条已通过、一条未定年代、一条草稿）
    conn.executemany(
        "INSERT INTO statements(id,subject_table,subject_id,title,body,event_time,collected_at,"
        "source_type,source_ref,status,created_by,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            (1, "boats", 1, "3301于1958年建造", "东码头船匠1958年建造，原名鲁岱渔33。",
             _et({"type": "point", "date": "1958"}), now, "oral", "interviews#1", "approved", 1, now, now),
            (2, "boats", 2, "修船厂同名号船", "档案显示另一艘3301，关系待考。",
             _et({"type": "undated", "note": "档案残缺"}), now, "document", "船厂档案",
             "submitted", 1, now, now),
            (3, "jargons", 1, "扯篷释义", "升帆，引申为开船。", None, now,
             "oral", "interviews#1", "draft", 1, now, now),
        ],
    )
    conn.execute(
        "INSERT INTO statement_reviews(statement_id,editor_id,action,note,created_at) VALUES (?,?,?,?,?)",
        (1, 2, "approve", "与档案互证", now),
    )
    # 身份候选 + 证据
    conn.execute(
        "INSERT INTO identity_candidates(id,boat_a,boat_b,relation,confidence,status,created_by,created_at)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (1, 1, 2, "unknown", "possible", "open", 1, now),
    )
    conn.executemany(
        "INSERT INTO identity_evidence(candidate_id,kind,detail,supports,created_by,created_at)"
        " VALUES (?,?,?,?,?,?)",
        [
            (1, "name", "两船同名“鲁岱渔3301”", 1, 1, now),
            (1, "oral", "陈阿毛回忆两艘3301船型相似，疑似同一艘", 1, 2, now),
            (1, "document", "建造年代与材质记录不一致（1958木船 vs 1978-1982）", 0, 2, now),
        ],
    )
