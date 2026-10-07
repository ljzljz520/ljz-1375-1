# -*- coding: utf-8 -*-
"""领域操作层：CRUD、船只身份候选/证据/合并与撤销、陈述审校、
撤稿级联、时间轴。所有年代写入前强制规范化。"""
import json
import sqlite3

from . import timeutil
from .db import dump_tp, load_tp


class DomainError(Exception):
    """业务规则违例（权限、身份冲突、状态机错误等）。"""


def tp_arg(v):
    """接受 dict 或著录字符串，返回规范化时间点 JSON。"""
    if v is None or v == "":
        return None
    if isinstance(v, dict):
        return dump_tp(timeutil.make_tp(v["kind"], v.get("start"),
                                        v.get("end"), v.get("label"),
                                        v.get("note")))
    return dump_tp(timeutil.parse_tp(str(v)))


def require_editor(conn, user_id):
    r = conn.execute("SELECT role FROM users WHERE id=? AND active=1",
                     (user_id,)).fetchone()
    if not r:
        raise DomainError("用户不存在或已停用")
    return r["role"]


def require_role(conn, user_id, role):
    r = require_editor(conn, user_id)
    if r != role:
        raise DomainError(f"需要 {role} 权限")
    return r


def audit(conn, user_id, action, detail=None):
    conn.execute("INSERT INTO audit_log(actor_id, action, detail) VALUES(?,?,?)",
                 (user_id, action, json.dumps(detail, ensure_ascii=False)
                  if detail is not None else None))


# ================= 船只 =================
def create_ship(conn, user_id, current_name, note=""):
    require_editor(conn, user_id)
    cur = conn.execute("INSERT INTO ships(current_name, note) VALUES(?,?)",
                       (current_name, note))
    sid = cur.lastrowid
    conn.execute("""INSERT INTO ship_names(ship_id, name, is_current)
                    VALUES(?,?,1)""", (sid, current_name))
    audit(conn, user_id, "ship.create", {"id": sid, "name": current_name})
    return sid


def add_ship_name(conn, user_id, ship_id, name, used_from=None,
                  used_to=None, source="", make_current=False):
    require_editor(conn, user_id)
    cur = conn.execute("""INSERT INTO ship_names(ship_id,name,used_from,used_to,
                          source,is_current) VALUES(?,?,?,?,?,?)""",
                       (ship_id, name, tp_arg(used_from), tp_arg(used_to),
                        source, 1 if make_current else 0))
    if make_current:
        conn.execute("UPDATE ship_names SET is_current=0 WHERE ship_id=?",
                     (ship_id,))
        conn.execute("UPDATE ship_names SET is_current=1 WHERE id=?",
                     (cur.lastrowid,))
        conn.execute("UPDATE ships SET current_name=? WHERE id=?",
                     (name, ship_id))
    return cur.lastrowid


def add_ship_owner(conn, user_id, ship_id, owner, occurred_at=None, source=""):
    require_editor(conn, user_id)
    cur = conn.execute("""INSERT INTO ship_owners(ship_id,owner,occurred_at,source)
                          VALUES(?,?,?,?)""",
                       (ship_id, owner, tp_arg(occurred_at), source))
    return cur.lastrowid


def add_ship_event(conn, user_id, ship_id, etype, title, occurred_at=None,
                   collected_at=None, detail="", source=""):
    require_editor(conn, user_id)
    _active_ship(conn, ship_id)
    cur = conn.execute("""INSERT INTO ship_events(ship_id,type,title,occurred_at,
                          collected_at,detail,source) VALUES(?,?,?,?,?,?,?)""",
                       (ship_id, etype, title, tp_arg(occurred_at),
                        tp_arg(collected_at), detail, source))
    return cur.lastrowid


def list_ships(conn, include_superseded=False):
    sql = "SELECT * FROM ships"
    if not include_superseded:
        sql += " WHERE superseded=0"
    sql += " ORDER BY id"
    return [dict(r) for r in conn.execute(sql).fetchall()]


def get_ship(conn, sid):
    r = conn.execute("SELECT * FROM ships WHERE id=?", (sid,)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["names"] = [dict(x) for x in conn.execute(
        "SELECT * FROM ship_names WHERE ship_id=? ORDER BY id", (sid,))]
    d["owners"] = [dict(x) for x in conn.execute(
        "SELECT * FROM ship_owners WHERE ship_id=? ORDER BY id", (sid,))]
    d["events"] = [dict(x) for x in conn.execute(
        "SELECT * FROM ship_events WHERE ship_id=? ORDER BY id", (sid,))]
    return d



def _active_ship(conn, ship_id):
    r = conn.execute("SELECT * FROM ships WHERE id=?", (ship_id,)).fetchone()
    if not r:
        raise DomainError("船只不存在")
    if r["superseded"]:
        raise DomainError(
            f"船只 #{ship_id} 已合并入 #{r['merged_into']}，请对保留船操作")
    return r

# ================= 行话 =================
def create_jargon(conn, user_id, term, meaning="", occurred_at=None,
                  collected_at=None, source=""):
    require_editor(conn, user_id)
    cur = conn.execute("""INSERT INTO jargons(term,meaning,occurred_at,
                          collected_at,source) VALUES(?,?,?,?,?)""",
                       (term, meaning, tp_arg(occurred_at),
                        tp_arg(collected_at), source))
    return cur.lastrowid


def list_jargons(conn):
    return [dict(r) for r in conn.execute(
        "SELECT * FROM jargons ORDER BY id").fetchall()]


# ================= 地点 / 资产 / 照片 =================
def create_location(conn, user_id, name, lat=None, lng=None, note=""):
    require_editor(conn, user_id)
    cur = conn.execute(
        "INSERT INTO locations(name,lat,lng,note) VALUES(?,?,?,?)",
        (name, lat, lng, note))
    return cur.lastrowid


def list_locations(conn):
    return [dict(r) for r in conn.execute(
        "SELECT * FROM locations ORDER BY id").fetchall()]


def create_asset(conn, user_id, kind, path, mime="", derived_from=None):
    require_editor(conn, user_id)
    if kind not in ("photo", "audio", "derived"):
        raise DomainError("非法资产类型")
    cur = conn.execute("""INSERT INTO assets(kind,path,mime,derived_from)
                          VALUES(?,?,?,?)""", (kind, path, mime, derived_from))
    return cur.lastrowid


def create_photo(conn, user_id, asset_id=None, caption="", license_info="",
                 occurred_at=None, collected_at=None, location_ids=(),
                 ship_ids=()):
    require_editor(conn, user_id)
    cur = conn.execute("""INSERT INTO photos(asset_id,caption,license,
                          occurred_at,collected_at) VALUES(?,?,?,?,?)""",
                       (asset_id, caption, license_info,
                        tp_arg(occurred_at), tp_arg(collected_at)))
    pid = cur.lastrowid
    for lid in location_ids:
        conn.execute("INSERT OR IGNORE INTO photo_locations VALUES(?,?)",
                     (pid, lid))
    for sid in ship_ids:
        conn.execute("INSERT OR IGNORE INTO photo_ships VALUES(?,?)",
                     (pid, sid))
    audit(conn, user_id, "photo.create", {"id": pid})
    return pid


def set_photo_caption(conn, user_id, pid, caption, status):
    require_editor(conn, user_id)
    if status not in ("draft", "approved", "withdrawn"):
        raise DomainError("非法说明状态")
    conn.execute("UPDATE photos SET caption=?, caption_status=? WHERE id=?",
                 (caption, status, pid))
    audit(conn, user_id, "photo.caption", {"id": pid, "status": status})


def set_photo_license(conn, user_id, pid, license_info, status):
    """说明与授权分开追踪：授权单独流转，不动说明。"""
    require_editor(conn, user_id)
    if status not in ("pending", "granted", "revoked"):
        raise DomainError("非法授权状态")
    conn.execute("UPDATE photos SET license=?, license_status=? WHERE id=?",
                 (license_info, status, pid))
    audit(conn, user_id, "photo.license", {"id": pid, "status": status})


def link_photo_location(conn, user_id, pid, lid):
    require_editor(conn, user_id)
    conn.execute("INSERT OR IGNORE INTO photo_locations VALUES(?,?)",
                 (pid, lid))


def list_photos(conn):
    out = []
    for r in conn.execute("SELECT * FROM photos ORDER BY id"):
        d = dict(r)
        d["locations"] = [x["location_id"] for x in conn.execute(
            "SELECT location_id FROM photo_locations WHERE photo_id=?",
            (d["id"],))]
        d["ships"] = [x["ship_id"] for x in conn.execute(
            "SELECT ship_id FROM photo_ships WHERE photo_id=?",
            (d["id"],))]
        out.append(d)
    return out


# ================= 受访者 / 口述 / 逐字稿 =================
def create_interview(conn, user_id, interviewee, collected_at=None,
                     audio_asset=None):
    require_editor(conn, user_id)
    cur = conn.execute("""INSERT INTO interviews(interviewee,collected_at,
                          audio_asset) VALUES(?,?,?)""",
                       (interviewee, tp_arg(collected_at), audio_asset))
    return cur.lastrowid


def add_segment(conn, user_id, interview_id, text, tcin=None, tcout=None,
                seq=None):
    require_editor(conn, user_id)
    if seq is None:
        r = conn.execute("""SELECT COALESCE(MAX(seq),0)+1 AS s
                            FROM transcript_segments WHERE interview_id=?""",
                         (interview_id,)).fetchone()
        seq = r["s"]
    cur = conn.execute("""INSERT INTO transcript_segments(interview_id,seq,
                          tcin,tcout,text) VALUES(?,?,?,?,?)""",
                       (interview_id, seq, tcin, tcout, text))
    return cur.lastrowid


def withdraw_segment(conn, user_id, seg_id):
    """受访者撤回一段：保留文本记录但公开侧不再发布。"""
    require_editor(conn, user_id)
    conn.execute("UPDATE transcript_segments SET withdrawn=1 WHERE id=?",
                 (seg_id,))
    audit(conn, user_id, "segment.withdraw", {"id": seg_id})


def list_segments(conn, interview_id):
    return [dict(r) for r in conn.execute(
        "SELECT * FROM transcript_segments WHERE interview_id=? ORDER BY seq",
        (interview_id,)).fetchall()]


# ================= 史料陈述与审校 =================
def create_statement(conn, user_id, topic, content, occurred_at=None,
                     collected_at=None, ship_id=None, location_id=None,
                     interview_id=None, source=""):
    require_editor(conn, user_id)
    cur = conn.execute("""INSERT INTO statements(topic,content,occurred_at,
        collected_at,ship_id,location_id,interview_id,source,created_by)
        VALUES(?,?,?,?,?,?,?,?,?)""",
        (topic, content, tp_arg(occurred_at), tp_arg(collected_at),
         ship_id, location_id, interview_id, source, user_id))
    sid = cur.lastrowid
    _snapshot_revision(conn, sid, user_id)
    audit(conn, user_id, "statement.create", {"id": sid})
    return sid


def _current_payload(conn, sid):
    r = conn.execute("SELECT * FROM statements WHERE id=?", (sid,)).fetchone()
    return {k: r[k] for k in r.keys()}


def _snapshot_revision(conn, sid, user_id):
    row = _current_payload(conn, sid)
    conn.execute("""INSERT INTO statement_revisions(statement_id,version,payload,
                    editor_id) VALUES(?,?,?,?)""",
                 (sid, row["version"], json.dumps(row, ensure_ascii=False),
                  user_id))


def update_statement(conn, user_id, sid, **fields):
    require_editor(conn, user_id)
    row = conn.execute("SELECT * FROM statements WHERE id=?", (sid,)).fetchone()
    if not row:
        raise DomainError("陈述不存在")
    if row["status"] in ("approved", "withdrawn"):
        # 已发布过的陈述改动需重新走审校：先退回草稿
        conn.execute("UPDATE statements SET status='draft' WHERE id=?", (sid,))
    sets, vals = [], []
    for k in ("topic", "content", "source"):
        if k in fields:
            sets.append(f"{k}=?")
            vals.append(fields[k])
    for k in ("ship_id", "location_id", "interview_id"):
        if k in fields:
            sets.append(f"{k}=?")
            vals.append(fields[k])
    for k in ("occurred_at", "collected_at"):
        if k in fields:
            sets.append(f"{k}=?")
            vals.append(tp_arg(fields[k]))
    if sets:
        vals += [sid]
        conn.execute(f"UPDATE statements SET {', '.join(sets)}, "
                     f"version=version+1, updated_at=datetime('now') WHERE id=?",
                     vals)
    _snapshot_revision(conn, sid, user_id)


def submit_statement(conn, user_id, sid):
    require_editor(conn, user_id)
    _set_status(conn, sid, {"draft": "in_review", "rejected": "in_review"})
    audit(conn, user_id, "statement.submit", {"id": sid})


def review_statement(conn, user_id, sid, approve):
    """审校通过/退回。"""
    require_editor(conn, user_id)
    target = "approved" if approve else "rejected"
    _set_status(conn, sid, {"in_review": target})
    audit(conn, user_id, "statement.review",
          {"id": sid, "result": target})


def withdraw_statement(conn, user_id, sid):
    require_editor(conn, user_id)
    _set_status(conn, sid, {s: "withdrawn" for s in
                            ("draft", "in_review", "approved", "rejected")})
    audit(conn, user_id, "statement.withdraw", {"id": sid})


def _set_status(conn, sid, allowed):
    row = conn.execute("SELECT status FROM statements WHERE id=?",
                       (sid,)).fetchone()
    if not row:
        raise DomainError("陈述不存在")
    if row["status"] not in allowed:
        raise DomainError(f"陈述状态 {row['status']} 不能执行该流转")
    conn.execute("UPDATE statements SET status=? WHERE id=?",
                 (allowed[row["status"]], sid))


def list_statements(conn, status=None):
    sql = "SELECT * FROM statements"
    if status:
        sql += f" WHERE status='{status}'"
    sql += " ORDER BY id"
    return [dict(r) for r in conn.execute(sql).fetchall()]


# ================= 身份候选、证据、合并与撤销 =================
def create_candidate(conn, user_id, ship_a, ship_b, note=""):
    require_editor(conn, user_id)
    if ship_a == ship_b:
        raise DomainError("不能对同一条船建立候选")
    for sid in (ship_a, ship_b):
        r = conn.execute("SELECT superseded FROM ships WHERE id=?",
                         (sid,)).fetchone()
        if not r:
            raise DomainError(f"船只 {sid} 不存在")
        if r["superseded"]:
            raise DomainError(f"船只 {sid} 已被合并，不能再入候选")
    dup = conn.execute("""SELECT id FROM identity_candidates
                          WHERE status='open' AND
                          ((ship_a=? AND ship_b=?) OR (ship_a=? AND ship_b=?))""",
                       (ship_a, ship_b, ship_b, ship_a)).fetchone()
    if dup:
        raise DomainError("已存在开放候选")
    cur = conn.execute("""INSERT INTO identity_candidates(ship_a,ship_b,
                          created_by,note) VALUES(?,?,?,?)""",
                       (ship_a, ship_b, user_id, note))
    audit(conn, user_id, "candidate.create",
          {"id": cur.lastrowid, "a": ship_a, "b": ship_b})
    return cur.lastrowid


def add_evidence(conn, user_id, cand_id, side, content):
    require_editor(conn, user_id)
    if side not in ("same", "different"):
        raise DomainError("证据立场只能是 same/different")
    cur = conn.execute("""INSERT INTO identity_evidence(candidate_id,side,
                          content,author_id) VALUES(?,?,?,?)""",
                       (cand_id, side, content, user_id))
    return cur.lastrowid


# 合并时需要把 ship_id 引用改指 kept_ship 的表
_RETARGET_TABLES = [
    ("ship_names", "ship_id"),
    ("ship_owners", "ship_id"),
    ("ship_events", "ship_id"),
    ("statements", "ship_id"),
    ("photo_ships", "ship_id"),
]


def merge_candidate(conn, user_id, cand_id, kept_ship):
    """双编辑规则：合并者不能是候选创建者；同名不自动合并。"""
    require_editor(conn, user_id)
    cand = conn.execute("SELECT * FROM identity_candidates WHERE id=?",
                        (cand_id,)).fetchone()
    if not cand:
        raise DomainError("候选不存在")
    if cand["status"] != "open":
        raise DomainError("候选不在开放状态")
    if user_id == cand["created_by"]:
        raise DomainError("合并必须由创建者之外的第二位编辑执行")
    a, b = cand["ship_a"], cand["ship_b"]
    if kept_ship not in (a, b):
        raise DomainError("保留船必须是候选中的一条")
    old_ship = b if kept_ship == a else a

    cur = conn.execute("""INSERT INTO ship_merges(candidate_id,kept_ship,
                          old_ship) VALUES(?,?,?)""",
                       (cand_id, kept_ship, old_ship))
    merge_id = cur.lastrowid

    # 备份受影响行（整行），撤销时恢复
    for table, col in _RETARGET_TABLES:
        rows = conn.execute(f"SELECT rowid AS _pk, * FROM {table} WHERE {col}=?",
                            (old_ship,)).fetchall()
        for r in rows:
            pk = r["_pk"]
            payload = {k: r[k] for k in r.keys() if k != "_pk"}
            conn.execute("""INSERT INTO merge_ref_backups(merge_id,table_name,
                          row_pk,old_payload) VALUES(?,?,?,?)""",
                         (merge_id, table, pk,
                          json.dumps(payload, ensure_ascii=False)))
            if table == "photo_ships":
                # 保留船已含同一 (photo,ship) 时，改指会撞唯一键：
                # 备份后直接删除旧关联（撤销时按备份重建）
                clash = conn.execute(
                    "SELECT 1 FROM photo_ships WHERE photo_id=? AND ship_id=?",
                    (payload["photo_id"], kept_ship)).fetchone()
                if clash:
                    conn.execute(
                        "DELETE FROM photo_ships WHERE rowid=?", (pk,))
                    continue
            conn.execute(f"UPDATE {table} SET {col}=? WHERE rowid=?",
                         (kept_ship, pk))

    conn.execute("UPDATE ships SET superseded=1, merged_into=? WHERE id=?",
                 (kept_ship, old_ship))
    conn.execute("""UPDATE identity_candidates SET status='merged',
                    decided_by=?, decided_at=datetime('now') WHERE id=?""",
                 (user_id, cand_id))
    audit(conn, user_id, "ship.merge",
          {"merge": merge_id, "kept": kept_ship, "old": old_ship})
    return merge_id


def undo_merge(conn, user_id, merge_id):
    """撤销合并：恢复旧主档与全部引用；撤销后人工再改指的行保持不动。"""
    require_editor(conn, user_id)
    m = conn.execute("SELECT * FROM ship_merges WHERE id=?",
                     (merge_id,)).fetchone()
    if not m:
        raise DomainError("合并记录不存在")
    if m["undone"]:
        raise DomainError("该合并已撤销")
    old_ship, kept_ship = m["old_ship"], m["kept_ship"]

    backups = conn.execute("""SELECT * FROM merge_ref_backups
                              WHERE merge_id=? ORDER BY id""",
                           (merge_id,)).fetchall()
    # 先清掉当前指向 kept、且当初由 old 改指过来的非人工行，
    # 再按备份写回 old（避免主键冲突）
    for b in backups:
        if b["manually_retargeted"]:
            continue
        exists = conn.execute(
            "SELECT 1 FROM photo_ships WHERE rowid=?",
            (b["row_pk"],)).fetchone()
        if exists:
            conn.execute("DELETE FROM photo_ships WHERE rowid=? AND ship_id=?",
                         (b["row_pk"], kept_ship))
    for b in backups:
        if b["manually_retargeted"]:
            continue
        payload = json.loads(b["old_payload"])
        cols = ", ".join(payload.keys())
        qs = ", ".join("?" for _ in payload)
        conn.execute(f"UPDATE {b['table_name']} SET ({cols})=({qs}) "
                     f"WHERE rowid=?", (*payload.values(), b["row_pk"]))
    # 若备份行在去重中被物理删除，按 payload 重建
    for b in backups:
        if b["manually_retargeted"]:
            continue
        table = b["table_name"]
        if not conn.execute(f"SELECT 1 FROM {table} WHERE rowid=?",
                            (b["row_pk"],)).fetchone():
            payload = json.loads(b["old_payload"])
            cols = ", ".join(payload.keys())
            qs = ", ".join("?" for _ in payload)
            conn.execute(f"INSERT INTO {table}(rowid,{cols}) "
                         f"VALUES(?,{qs})", (b["row_pk"], *payload.values()))

    conn.execute("UPDATE ships SET superseded=0, merged_into=NULL WHERE id=?",
                 (old_ship,))
    conn.execute("UPDATE ship_merges SET undone=1, undone_at=datetime('now') "
                 "WHERE id=?", (merge_id,))
    conn.execute("UPDATE identity_candidates SET status='open' WHERE id=?",
                 (m["candidate_id"],))
    audit(conn, user_id, "merge.undo", {"merge": merge_id})


def mark_ref_manual(conn, user_id, merge_id, table_name, row_pk):
    """某引用在合并后被人工改指过：撤销合并时保持现状不回退。"""
    require_editor(conn, user_id)
    conn.execute("""UPDATE merge_ref_backups SET manually_retargeted=1
                    WHERE merge_id=? AND table_name=? AND row_pk=?""",
                 (merge_id, table_name, row_pk))


# ================= 时间轴 =================
def timeline(conn, only_approved=False):
    """汇总事件/陈述/行话/照片。只用年代本身排序，绝不用导入次序。"""
    entries = []

    def add(kind, rid, title, tp_json, source, status="approved", extra=None):
        if only_approved and status != "approved":
            return
        tp = load_tp(tp_json)
        entries.append({"kind": kind, "ref_id": rid, "title": title,
                        "time": tp, "source": source or "",
                        "status": status, "extra": extra or {}})

    for r in conn.execute("SELECT * FROM ship_events").fetchall():
        add("ship_event", r["id"], r["title"], r["occurred_at"],
            r["source"], "approved")
    for r in conn.execute("SELECT * FROM statements").fetchall():
        add("statement", r["id"], r["topic"], r["occurred_at"],
            r["source"], r["status"])
    for r in conn.execute("SELECT * FROM jargons").fetchall():
        add("jargon", r["id"], r["term"], r["occurred_at"],
            r["source"], r["status"])
    for r in conn.execute("SELECT * FROM photos").fetchall():
        add("photo", r["id"], r["caption"] or "（无说明照片）",
            r["occurred_at"], r["license"], r["status"])

    dated = [e for e in entries if not timeutil.is_undated(e["time"])]
    undated = [e for e in entries if timeutil.is_undated(e["time"])]
    dated.sort(key=lambda e: timeutil.sort_key(e["time"]))
    # 相邻条目标注先后强度
    for i, e in enumerate(dated):
        if i == 0:
            e["order"] = "first"
        else:
            e["order"] = timeutil.order_strength(dated[i - 1]["time"],
                                                 e["time"])
    for e in undated:
        e["order"] = "undated"
    return dated + undated


# ================= 撤稿任务 =================
def create_takedown(conn, user_id, reason, targets, cascade=True):
    """targets: [(type,id), ...]。type∈photo/interview/statement/asset。"""
    require_editor(conn, user_id)
    cur = conn.execute("INSERT INTO takedown_tasks(reason,created_by) VALUES(?,?)",
                       (reason, user_id))
    tid = cur.lastrowid
    for t, i in targets:
        conn.execute("""INSERT INTO takedown_items(task_id,target_type,
                      target_id,cascade_derivatives) VALUES(?,?,?,?)""",
                     (tid, t, i, 1 if cascade else 0))
    audit(conn, user_id, "takedown.create", {"id": tid, "targets": targets})
    return tid


def _derivative_assets(conn, asset_id):
    return [r["id"] for r in conn.execute(
        "SELECT id FROM assets WHERE derived_from=? AND active=1",
        (asset_id,)).fetchall()]


def execute_takedown(conn, user_id, tid):
    require_editor(conn, user_id)
    task = conn.execute("SELECT * FROM takedown_tasks WHERE id=?",
                        (tid,)).fetchone()
    if not task or task["status"] != "open":
        raise DomainError("撤稿任务不存在或已执行")
    effects = {"photos": {}, "interviews": {}, "statements": {}, "assets": {}}
    items = conn.execute("SELECT * FROM takedown_items WHERE task_id=?",
                         (tid,)).fetchall()

    def deactivate_asset(asset_id):
        if asset_id and asset_id not in effects["assets"]:
            r = conn.execute("SELECT * FROM assets WHERE id=?",
                             (asset_id,)).fetchone()
            if r and r["active"]:
                effects["assets"][asset_id] = True
                conn.execute("UPDATE assets SET active=0 WHERE id=?",
                             (asset_id,))
                # 派生缩略图等一并撤下（纳入撤稿任务）
                for d in _derivative_assets(conn, asset_id):
                    if d not in effects["assets"]:
                        effects["assets"][d] = True
                        conn.execute("UPDATE assets SET active=0 WHERE id=?",
                                     (d,))

    for it in items:
        t, i = it["target_type"], it["target_id"]
        if t == "photo":
            r = conn.execute("SELECT * FROM photos WHERE id=?", (i,)).fetchone()
            if r:
                effects["photos"][i] = {"status": r["status"],
                                        "caption_status": r["caption_status"],
                                        "license_status": r["license_status"],
                                        "asset_id": r["asset_id"]}
                conn.execute("UPDATE photos SET status='withdrawn' WHERE id=?",
                             (i,))
                if it["cascade_derivatives"]:
                    deactivate_asset(r["asset_id"])
        elif t == "interview":
            r = conn.execute("SELECT * FROM interviews WHERE id=?",
                             (i,)).fetchone()
            if r:
                effects["interviews"][i] = {"status": r["status"],
                                            "consent": r["consent"],
                                            "audio_asset": r["audio_asset"]}
                conn.execute(
                    "UPDATE interviews SET status='withdrawn' WHERE id=?", (i,))
                # 撤回授权后，全部逐字稿段落撤下，但文本保留
                conn.execute(
                    "UPDATE transcript_segments SET withdrawn=1 "
                    "WHERE interview_id=?", (i,))
                if it["cascade_derivatives"]:
                    deactivate_asset(r["audio_asset"])
        elif t == "statement":
            r = conn.execute("SELECT * FROM statements WHERE id=?",
                             (i,)).fetchone()
            if r:
                effects["statements"][i] = r["status"]
                conn.execute(
                    "UPDATE statements SET status='withdrawn' WHERE id=?", (i,))
        elif t == "asset":
            deactivate_asset(i)

    conn.execute("UPDATE takedown_tasks SET status='done', effects=?, "
                 "done_at=datetime('now') WHERE id=?",
                 (json.dumps(effects, ensure_ascii=False), tid))
    audit(conn, user_id, "takedown.execute", {"id": tid})
    return effects


def reinstate_takedown(conn, user_id, tid):
    """按执行前快照恢复（撤销撤稿）。"""
    require_role(conn, user_id, "admin")
    task = conn.execute("SELECT * FROM takedown_tasks WHERE id=?",
                        (tid,)).fetchone()
    if not task or task["status"] != "done":
        raise DomainError("撤稿任务未执行，不能恢复")
    effects = json.loads(task["effects"] or "{}")
    for aid in effects.get("assets", {}):
        conn.execute("UPDATE assets SET active=1 WHERE id=?", (aid,))
    for pid, st in effects.get("photos", {}).items():
        conn.execute("""UPDATE photos SET status=?, caption_status=?,
                        license_status=? WHERE id=?""",
                     (st["status"], st["caption_status"],
                      st["license_status"], pid))
    for iid, st in effects.get("interviews", {}).items():
        conn.execute("UPDATE interviews SET status=?, consent=? WHERE id=?",
                     (st["status"], st["consent"], iid))
    for sid, st in effects.get("statements", {}).items():
        conn.execute("UPDATE statements SET status=? WHERE id=?", (st, sid))
    conn.execute("UPDATE takedown_tasks SET status='reinstated' WHERE id=?",
                 (tid,))
    audit(conn, user_id, "takedown.reinstate", {"id": tid})


# ---------- 补充状态流转：行话 / 访谈 / 照片总状态 ----------
def set_jargon_status(conn, user_id, jid, status):
    require_editor(conn, user_id)
    if status not in ("draft", "approved", "withdrawn"):
        raise DomainError("非法状态")
    conn.execute("UPDATE jargons SET status=? WHERE id=?", (status, jid))


def set_interview_consent(conn, user_id, iid, consent):
    require_editor(conn, user_id)
    if consent not in ("pending", "granted", "revoked"):
        raise DomainError("非法同意书状态")
    conn.execute("UPDATE interviews SET consent=? WHERE id=?", (consent, iid))
    if consent == "revoked":
        # 撤回同意：已逐字化段落全部撤下（文本保留）
        conn.execute(
            "UPDATE transcript_segments SET withdrawn=1 WHERE interview_id=?",
            (iid,))
    audit(conn, user_id, "interview.consent", {"id": iid, "consent": consent})


def set_interview_status(conn, user_id, iid, status):
    require_editor(conn, user_id)
    if status not in ("draft", "approved", "withdrawn"):
        raise DomainError("非法状态")
    conn.execute("UPDATE interviews SET status=? WHERE id=?", (status, iid))


def set_photo_status(conn, user_id, pid, status):
    require_editor(conn, user_id)
    if status not in ("draft", "approved", "withdrawn"):
        raise DomainError("非法状态")
    conn.execute("UPDATE photos SET status=? WHERE id=?", (status, pid))


def ensure_thumbnail_asset(conn, photo_id):
    """照片首次进入发布管线时，为原片登记一个派生缩略图资产，
    使其成为撤稿任务可级联的对象。"""
    p = conn.execute("SELECT asset_id FROM photos WHERE id=?",
                     (photo_id,)).fetchone()
    if not p or not p["asset_id"]:
        return None
    existing = conn.execute(
        "SELECT id FROM assets WHERE derived_from=? AND kind='derived'",
        (p["asset_id"],)).fetchone()
    if existing:
        return existing["id"]
    cur = conn.execute(
        "INSERT INTO assets(kind,path,mime,derived_from) VALUES('derived',"
        "?,'image/png',?)",
        (f"derived/thumb_{p['asset_id']}.png", p["asset_id"]))
    return cur.lastrowid
