# -*- coding: utf-8 -*-
"""SQLite 数据层。三类时间字段一律存 JSON 文本（timeutil 时间点）：
   occurred_at  事件发生时间
   collected_at 资料采集时间
   published_at 公开时间（发布时回填）"""
import json
import os
import sqlite3

SCHEMA = """
PRAGMA foreign_keys = ON;

-- 用户与审计 --------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY,
  username TEXT UNIQUE NOT NULL,
  role TEXT NOT NULL CHECK(role IN ('editor','admin')),
  active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY,
  ts TEXT NOT NULL DEFAULT (datetime('now')),
  actor_id INTEGER REFERENCES users(id),
  action TEXT NOT NULL,
  detail TEXT
);

-- 船只 --------------------------------------------------------
CREATE TABLE IF NOT EXISTS ships (
  id INTEGER PRIMARY KEY,
  current_name TEXT NOT NULL,
  note TEXT DEFAULT '',
  superseded INTEGER NOT NULL DEFAULT 0,   -- 合并后旧主档标记
  merged_into INTEGER REFERENCES ships(id),
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS ship_names (
  id INTEGER PRIMARY KEY,
  ship_id INTEGER NOT NULL REFERENCES ships(id),
  name TEXT NOT NULL,
  used_from TEXT,                         -- timepoint JSON 或 NULL
  used_to TEXT,
  is_current INTEGER NOT NULL DEFAULT 0,
  source TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS ship_owners (
  id INTEGER PRIMARY KEY,
  ship_id INTEGER NOT NULL REFERENCES ships(id),
  owner TEXT NOT NULL,
  occurred_at TEXT,                       -- timepoint JSON：转手发生年代
  source TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS ship_events (
  id INTEGER PRIMARY KEY,
  ship_id INTEGER NOT NULL REFERENCES ships(id),
  type TEXT NOT NULL CHECK(type IN ('build','rename','transfer','rebuild','sight','other')),
  title TEXT NOT NULL,
  occurred_at TEXT,
  collected_at TEXT,
  detail TEXT DEFAULT '',
  source TEXT DEFAULT ''
);

-- 身份候选与证据、合并 ----------------------------------------
CREATE TABLE IF NOT EXISTS identity_candidates (
  id INTEGER PRIMARY KEY,
  ship_a INTEGER NOT NULL REFERENCES ships(id),
  ship_b INTEGER NOT NULL REFERENCES ships(id),
  status TEXT NOT NULL DEFAULT 'open'
     CHECK(status IN ('open','merged','rejected','undone')),
  created_by INTEGER REFERENCES users(id),
  decided_by INTEGER REFERENCES users(id),
  decided_at TEXT,
  note TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS identity_evidence (
  id INTEGER PRIMARY KEY,
  candidate_id INTEGER NOT NULL REFERENCES identity_candidates(id),
  side TEXT NOT NULL CHECK(side IN ('same','different')),
  content TEXT NOT NULL,
  author_id INTEGER REFERENCES users(id),
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS ship_merges (
  id INTEGER PRIMARY KEY,
  candidate_id INTEGER NOT NULL REFERENCES identity_candidates(id),
  kept_ship INTEGER NOT NULL REFERENCES ships(id),
  old_ship INTEGER NOT NULL REFERENCES ships(id),
  undone INTEGER NOT NULL DEFAULT 0,
  merged_at TEXT NOT NULL DEFAULT (datetime('now')),
  undone_at TEXT
);

-- 合并时逐表备份受影响行，撤销时按此恢复引用 ------------------
CREATE TABLE IF NOT EXISTS merge_ref_backups (
  id INTEGER PRIMARY KEY,
  merge_id INTEGER NOT NULL REFERENCES ship_merges(id),
  table_name TEXT NOT NULL,
  row_pk INTEGER NOT NULL,
  old_payload TEXT NOT NULL,              -- 合并前整行 JSON
  manually_retargeted INTEGER NOT NULL DEFAULT 0
);

-- 行话 --------------------------------------------------------
CREATE TABLE IF NOT EXISTS jargons (
  id INTEGER PRIMARY KEY,
  term TEXT NOT NULL,
  meaning TEXT DEFAULT '',
  occurred_at TEXT,
  collected_at TEXT,
  source TEXT DEFAULT '',
  status TEXT NOT NULL DEFAULT 'draft'
     CHECK(status IN ('draft','approved','withdrawn'))
);

-- 地点 --------------------------------------------------------
CREATE TABLE IF NOT EXISTS locations (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  lat REAL,
  lng REAL,
  note TEXT DEFAULT ''
);

-- 资产（原件 / 派生件）与照片 ---------------------------------
CREATE TABLE IF NOT EXISTS assets (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL CHECK(kind IN ('photo','audio','derived')),
  path TEXT NOT NULL,
  mime TEXT DEFAULT '',
  derived_from INTEGER REFERENCES assets(id),
  active INTEGER NOT NULL DEFAULT 1,      -- 撤稿置 0，文件不删但不发布
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS photos (
  id INTEGER PRIMARY KEY,
  asset_id INTEGER REFERENCES assets(id),
  caption TEXT DEFAULT '',                -- 说明，独立于授权追踪
  caption_status TEXT NOT NULL DEFAULT 'draft'
     CHECK(caption_status IN ('draft','approved','withdrawn')),
  license TEXT DEFAULT '',                -- 授权（来源/权利人/许可）
  license_status TEXT NOT NULL DEFAULT 'pending'
     CHECK(license_status IN ('pending','granted','revoked')),
  occurred_at TEXT,
  collected_at TEXT,
  published_at TEXT,
  status TEXT NOT NULL DEFAULT 'draft'
     CHECK(status IN ('draft','approved','withdrawn'))
);

CREATE TABLE IF NOT EXISTS photo_locations (
  photo_id INTEGER NOT NULL REFERENCES photos(id),
  location_id INTEGER NOT NULL REFERENCES locations(id),
  PRIMARY KEY (photo_id, location_id)
);

CREATE TABLE IF NOT EXISTS photo_ships (
  photo_id INTEGER NOT NULL REFERENCES photos(id),
  ship_id INTEGER NOT NULL REFERENCES ships(id),
  PRIMARY KEY (photo_id, ship_id)
);

-- 受访者与口述 ------------------------------------------------
CREATE TABLE IF NOT EXISTS interviews (
  id INTEGER PRIMARY KEY,
  interviewee TEXT NOT NULL,
  collected_at TEXT,                      -- 采访（采集）时间
  consent TEXT NOT NULL DEFAULT 'pending'
     CHECK(consent IN ('pending','granted','revoked')),
  audio_asset INTEGER REFERENCES assets(id),
  status TEXT NOT NULL DEFAULT 'draft'
     CHECK(status IN ('draft','approved','withdrawn'))
);

CREATE TABLE IF NOT EXISTS transcript_segments (
  id INTEGER PRIMARY KEY,
  interview_id INTEGER NOT NULL REFERENCES interviews(id),
  seq INTEGER NOT NULL,                   -- 导入次序仅作存储序，不参与时间轴
  tcin TEXT,                              -- 时码，缺失为 NULL
  tcout TEXT,
  text TEXT NOT NULL,
  withdrawn INTEGER NOT NULL DEFAULT 0    -- 受访者撤回该段
);

-- 史料陈述（API 维护、需审校）---------------------------------
CREATE TABLE IF NOT EXISTS statements (
  id INTEGER PRIMARY KEY,
  topic TEXT NOT NULL,
  content TEXT NOT NULL,
  occurred_at TEXT,
  collected_at TEXT,
  published_at TEXT,
  ship_id INTEGER REFERENCES ships(id),
  location_id INTEGER REFERENCES locations(id),
  interview_id INTEGER REFERENCES interviews(id),
  source TEXT DEFAULT '',
  status TEXT NOT NULL DEFAULT 'draft'
     CHECK(status IN ('draft','in_review','approved','withdrawn','rejected')),
  version INTEGER NOT NULL DEFAULT 1,
  created_by INTEGER REFERENCES users(id),
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS statement_revisions (
  id INTEGER PRIMARY KEY,
  statement_id INTEGER NOT NULL REFERENCES statements(id),
  version INTEGER NOT NULL,
  payload TEXT NOT NULL,
  editor_id INTEGER REFERENCES users(id),
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 撤稿任务（级联派生件）---------------------------------------
CREATE TABLE IF NOT EXISTS takedown_tasks (
  id INTEGER PRIMARY KEY,
  reason TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open'
     CHECK(status IN ('open','done','reinstated')),
  effects TEXT NOT NULL DEFAULT '{}',   -- 执行前状态快照，便于恢复
  created_by INTEGER REFERENCES users(id),
  done_at TEXT
);

CREATE TABLE IF NOT EXISTS takedown_items (
  id INTEGER PRIMARY KEY,
  task_id INTEGER NOT NULL REFERENCES takedown_tasks(id),
  target_type TEXT NOT NULL CHECK(target_type IN ('photo','interview','statement','asset')),
  target_id INTEGER NOT NULL,
  cascade_derivatives INTEGER NOT NULL DEFAULT 1
);

-- 发布版本 -----------------------------------------------------
CREATE TABLE IF NOT EXISTS publications (
  id INTEGER PRIMARY KEY,
  dir TEXT NOT NULL,
  snapshot TEXT NOT NULL,                 -- 冻结内容图 JSON
  manifest TEXT NOT NULL DEFAULT '{}',
  note TEXT DEFAULT '',
  created_by INTEGER REFERENCES users(id),
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS kv_meta (
  key TEXT PRIMARY KEY,
  value TEXT
);
"""

SEED_USERS = [
    ("editor1", "editor"), ("editor2", "editor"), ("admin", "admin"),
]


def connect(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(path):
    fresh = not os.path.exists(path)
    conn = connect(path)
    try:
        conn.executescript(SCHEMA)
        if fresh:
            for name, role in SEED_USERS:
                conn.execute("INSERT INTO users(username, role) VALUES (?,?)", (name, role))
        conn.commit()
    finally:
        conn.close()
    return conn if False else None


# --- 时间点字段读写助手：入库前强制校验，杜绝脏年代 ----------
def dump_tp(tp):
    if tp is None:
        return None
    return json.dumps(tp, ensure_ascii=False)


def load_tp(s):
    return json.loads(s) if s else None
