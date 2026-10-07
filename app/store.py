"""业务逻辑：CRUD、身份候选/合并撤销、撤稿、审校、时间轴。"""
from __future__ import annotations

import json
from pathlib import Path

from . import config, serializers, timeutil
from .db import connect


class StoreError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


class Store:
    def __init__(self, conn=None):
        self.owns = conn is None
        self.conn = conn or connect()

    def close(self):
        if self.owns:
            self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    # ---------- 通用 ----------
    def _one(self, table, id_, *allowed):
        r = self.conn.execute(f"SELECT * FROM {table} WHERE id=?", (id_,)).fetchone()
        if not r:
            raise StoreError(f"{table}#{id_} 不存在", 404)
        return r

    def _touch(self, table, id_):
        self.conn.execute(f"UPDATE {table} SET updated_at=? WHERE id=?",
                          (timeutil.now_iso(), id_))

    # ---------- 编辑器 ----------
    def list_editors(self):
        return [dict(id=r["id"], name=r["name"], role=r["role"])
                for r in self.conn.execute("SELECT * FROM editors ORDER BY id")]

    # ---------- 船只 ----------
    def list_boats(self, include_merged=True):
        sql = "SELECT * FROM boats"
        if not include_merged:
            sql += " WHERE status!='merged_into'"
        return [serializers.boat(r) for r in self.conn.execute(sql + " ORDER BY id")]

    def get_boat(self, id_):
        r = self._one("boats", id_)
        d = serializers.boat(r)
        d["names"] = [serializers.boat_name(x) for x in self.conn.execute(
            "SELECT * FROM boat_names WHERE boat_id=? ORDER BY id", (id_,))]
        d["owners"] = [serializers.boat_owner(x) for x in self.conn.execute(
            "SELECT * FROM boat_owners WHERE boat_id=? ORDER BY id", (id_,))]
        d["events"] = [serializers.boat_event(x) for x in self.conn.execute(
            "SELECT * FROM boat_events WHERE boat_id=? ORDER BY id", (id_,))]
        d["statements"] = [serializers.statement(x) for x in self.conn.execute(
            "SELECT * FROM statements WHERE subject_table='boats' AND subject_id=? ORDER BY id", (id_,))]
        return d

    def create_boat(self, data, editor_id):
        now = timeutil.now_iso()
        et = timeutil.normalize_event_time(data.get("built_event_time"))
        cur = self.conn.execute(
            "INSERT INTO boats(current_name,hull_no,built_event_time,notes,created_at,updated_at)"
            " VALUES (?,?,?,?,?,?)",
            (data["current_name"], data.get("hull_no"),
             json.dumps(et, ensure_ascii=False), data.get("notes"), now, now))
        return cur.lastrowid

    def add_boat_name(self, boat_id, data):
        self._one("boats", boat_id)
        et = json.dumps(timeutil.normalize_event_time(data.get("event_time")), ensure_ascii=False)
        cur = self.conn.execute(
            "INSERT INTO boat_names(boat_id,name,event_time,note,created_at) VALUES (?,?,?,?,?)",
            (boat_id, data["name"], et, data.get("note"), timeutil.now_iso()))
        return cur.lastrowid

    def add_boat_owner(self, boat_id, data):
        self._one("boats", boat_id)
        et = json.dumps(timeutil.normalize_event_time(data.get("event_time")), ensure_ascii=False)
        cur = self.conn.execute(
            "INSERT INTO boat_owners(boat_id,owner,event_time,note,created_at) VALUES (?,?,?,?,?)",
            (boat_id, data["owner"], et, data.get("note"), timeutil.now_iso()))
        return cur.lastrowid

    def add_boat_event(self, boat_id, data):
        self._one("boats", boat_id)
        et = json.dumps(timeutil.normalize_event_time(data.get("event_time")), ensure_ascii=False)
        cur = self.conn.execute(
            "INSERT INTO boat_events(boat_id,kind,title,event_time,detail,created_at)"
            " VALUES (?,?,?,?,?,?)",
            (boat_id, data.get("kind", "note"), data["title"], et,
             data.get("detail"), timeutil.now_iso()))
        return cur.lastrowid

    # ---------- 身份候选 / 证据 ----------
    def list_candidates(self):
        out = []
        for r in self.conn.execute("SELECT * FROM identity_candidates ORDER BY id"):
            c = serializers.candidate(r)
            c["evidence"] = [serializers.evidence(e) for e in self.conn.execute(
                "SELECT * FROM identity_evidence WHERE candidate_id=? ORDER BY id", (c["id"],))]
            c["boat_a_name"] = self.conn.execute(
                "SELECT current_name FROM boats WHERE id=?", (c["boat_a"],)).fetchone()[0]
            c["boat_b_name"] = self.conn.execute(
                "SELECT current_name FROM boats WHERE id=?", (c["boat_b"],)).fetchone()[0]
            out.append(c)
        return out

    def create_candidate(self, data, editor_id):
        a, b = int(data["boat_a"]), int(data["boat_b"])
        if a == b:
            raise StoreError("不能把船与自身建立候选")
        for bid in (a, b):
            self._one("boats", bid)
        cur = self.conn.execute(
            "INSERT INTO identity_candidates(boat_a,boat_b,relation,confidence,created_by,created_at)"
            " VALUES (?,?,?,?,?,?)",
            (a, b, data.get("relation", "unknown"), data.get("confidence", "possible"),
             editor_id, timeutil.now_iso()))
        return cur.lastrowid

    def add_evidence(self, candidate_id, data, editor_id):
        self._one("identity_candidates", candidate_id)
        cur = self.conn.execute(
            "INSERT INTO identity_evidence(candidate_id,kind,detail,supports,created_by,created_at)"
            " VALUES (?,?,?,?,?,?)",
            (candidate_id, data.get("kind", "oral"), data["detail"],
             1 if data.get("supports", True) else 0, editor_id, timeutil.now_iso()))
        return cur.lastrowid

    def reject_candidate(self, candidate_id):
        self._one("identity_candidates", candidate_id)
        self.conn.execute("UPDATE identity_candidates SET status='rejected' WHERE id=?",
                          (candidate_id,))

    # ---------- 合并 / 撤销合并（恢复引用） ----------
    def merge_boats(self, candidate_id, editor_id, surviving=None, absorbed=None):
        c = self._one("identity_candidates", candidate_id)
        if c["status"] == "merged":
            raise StoreError("候选已合并")
        if c["status"] == "rejected":
            raise StoreError("候选已被拒绝，不能合并")
        # 两位编辑确认：①至少两条“支持”证据；②执行者不得是创建候选的同一人。
        support = self.conn.execute(
            "SELECT COUNT(*) n FROM identity_evidence WHERE candidate_id=? AND supports=1",
            (candidate_id,)).fetchone()["n"]
        if support < 2:
            raise StoreError(f"证据不足：合并需至少 2 条支持证据（当前 {support} 条）")
        if c["created_by"] is not None and int(editor_id) == int(c["created_by"]):
            raise StoreError("需第二位编辑确认：不能由候选创建者本人执行合并")
        surv = int(surviving or c["boat_a"])
        absb = int(absorbed or c["boat_b"])
        if surv == absb or {surv, absb} != {c["boat_a"], c["boat_b"]}:
            raise StoreError("合并双方必须是候选中的两艘船")
        self._one("boats", surv)
        ab = self._one("boats", absb)
        if ab["status"] == "merged_into":
            raise StoreError("该船已被并入别的船")
        now = timeutil.now_iso()
        cur = self.conn.execute(
            "INSERT INTO boat_merges(surviving_boat_id,absorbed_boat_id,created_by,created_at)"
            " VALUES (?,?,?,?)", (surv, absb, editor_id, now))
        merge_id = cur.lastrowid

        def _move(table, ids):
            for rid in ids:
                self.conn.execute(
                    f"UPDATE {table} SET boat_id=? WHERE id=?", (surv, rid))
                self.conn.execute(
                    "INSERT INTO boat_merge_items(merge_id,ref_table,ref_id) VALUES (?,?,?)",
                    (merge_id, table, rid))

        # 记录并迁移改名、转手、事件历史
        for table in ("boat_names", "boat_owners", "boat_events"):
            ids = [r["id"] for r in self.conn.execute(
                f"SELECT id FROM {table} WHERE boat_id=?", (absb,))]
            _move(table, ids)
        # 记录并迁移指向被并船的陈述引用
        stmt_ids = [r["id"] for r in self.conn.execute(
            "SELECT id FROM statements WHERE subject_table='boats' AND subject_id=?", (absb,))]
        for sid in stmt_ids:
            self.conn.execute("UPDATE statements SET subject_id=? WHERE id=?", (surv, sid))
            self.conn.execute(
                "INSERT INTO boat_merge_items(merge_id,ref_table,ref_id) VALUES (?,?,?)",
                (merge_id, "statements", sid))
        # 被并船现用名作为别名保留到存活船（撤销时删除）
        self.conn.execute(
            "INSERT INTO boat_names(boat_id,name,event_time,note,created_at) VALUES (?,?,?,?,?)",
            (surv, ab["current_name"], ab["built_event_time"],
             f"合并自船只 #{absb}（合并记录#{merge_id}）", now))
        alias_id = self.conn.execute("SELECT last_insert_rowid() id").fetchone()["id"]
        self.conn.execute(
            "INSERT INTO boat_merge_items(merge_id,ref_table,ref_id,moved) VALUES (?,?,?,0)",
            (merge_id, "boat_names_alias", alias_id))
        self.conn.execute(
            "UPDATE boats SET status='merged_into', merged_into_id=?, updated_at=? WHERE id=?",
            (surv, now, absb))
        self.conn.execute("UPDATE identity_candidates SET status='merged' WHERE id=?",
                          (candidate_id,))
        return merge_id

    def undo_merge(self, merge_id, editor_id):
        m = self._one("boat_merges", merge_id)
        if m["undone"]:
            raise StoreError("合并已撤销")
        surv, absb, now = m["surviving_boat_id"], m["absorbed_boat_id"], timeutil.now_iso()
        items = self.conn.execute(
            "SELECT * FROM boat_merge_items WHERE merge_id=?", (merge_id,)).fetchall()
        # 逆序处理：先删合并产生的别名，再把记录精确回迁到被并船
        for it in reversed(items):
            if it["ref_table"] == "boat_names_alias":
                self.conn.execute("DELETE FROM boat_names WHERE id=?", (it["ref_id"],))
            elif it["ref_table"] == "statements":
                self.conn.execute(
                    "UPDATE statements SET subject_id=? WHERE id=? AND subject_id=?",
                    (absb, it["ref_id"], surv))
            else:
                self.conn.execute(
                    f"UPDATE {it['ref_table']} SET boat_id=? WHERE id=? AND boat_id=?",
                    (absb, it["ref_id"], surv))
        self.conn.execute("DELETE FROM boat_merge_items WHERE merge_id=?", (merge_id,))
        # 恢复被并船身份
        self.conn.execute(
            "UPDATE boats SET status='active', merged_into_id=NULL, updated_at=? WHERE id=?",
            (now, absb))
        self.conn.execute("UPDATE boat_merges SET undone=1, undone_at=? WHERE id=?",
                          (now, merge_id))
        # 重开对应候选
        self.conn.execute(
            "UPDATE identity_candidates SET status='open' WHERE boat_a IN (?,?) "
            "AND boat_b IN (?,?) AND status='merged'", (surv, absb, surv, absb))

    def list_merges(self):
        return [serializers.merge(r) for r in self.conn.execute(
            "SELECT * FROM boat_merges ORDER BY id")]

    # ---------- 地点 / 人物 ----------
    def list_places(self):
        out = []
        for r in self.conn.execute("SELECT * FROM places ORDER BY id"):
            d = serializers.place(r)
            d["photo_ids"] = [x["photo_id"] for x in self.conn.execute(
                "SELECT photo_id FROM photo_refs WHERE ref_table='places' AND ref_id=? ORDER BY id",
                (r["id"],))]
            out.append(d)
        return out

    def create_place(self, data):
        now = timeutil.now_iso()
        cur = self.conn.execute(
            "INSERT INTO places(name,lat,lng,kind,note,created_at) VALUES (?,?,?,?,?,?)",
            (data["name"], data.get("lat"), data.get("lng"),
             data.get("kind"), data.get("note"), now))
        return cur.lastrowid

    def list_persons(self):
        return [serializers.person(r) for r in self.conn.execute("SELECT * FROM persons ORDER BY id")]

    # ---------- 行话 ----------
    def list_jargons(self):
        return [serializers.jargon(r) for r in self.conn.execute(
            "SELECT * FROM jargons ORDER BY id")]

    def create_jargon(self, data):
        now = timeutil.now_iso()
        cur = self.conn.execute(
            "INSERT INTO jargons(term,pronunciation,meaning,example,collected_at,created_at,updated_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (data["term"], data.get("pronunciation"), data.get("meaning"),
             data.get("example"), data.get("collected_at") or now, now, now))
        return cur.lastrowid

    # ---------- 照片 ----------
    def list_photos(self):
        out = []
        for r in self.conn.execute("SELECT * FROM photos ORDER BY id"):
            d = serializers.photo(r)
            d["refs"] = [dict(ref_table=x["ref_table"], ref_id=x["ref_id"], note=x["note"])
                         for x in self.conn.execute(
                             "SELECT * FROM photo_refs WHERE photo_id=? ORDER BY id", (r["id"],))]
            out.append(d)
        return out

    def create_photo(self, data):
        now = timeutil.now_iso()
        et = json.dumps(timeutil.normalize_event_time(data.get("taken_event_time")), ensure_ascii=False)
        cur = self.conn.execute(
            "INSERT INTO photos(caption,license,license_ref,asset_path,thumb_path,place_id,"
            "taken_event_time,collected_at,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (data.get("caption"), data.get("license"), data.get("license_ref"),
             data.get("asset_path"), data.get("thumb_path"), data.get("place_id"),
             et, data.get("collected_at") or now, now))
        pid = cur.lastrowid
        for ref in data.get("refs", []):
            self.conn.execute(
                "INSERT OR IGNORE INTO photo_refs(photo_id,ref_table,ref_id,note,created_at)"
                " VALUES (?,?,?,?,?)",
                (pid, ref["ref_table"], ref["ref_id"], ref.get("note"), now))
        return pid

    def update_photo_license(self, photo_id, data):
        """授权与说明分开追踪：只改授权字段。"""
        self._one("photos", photo_id)
        self.conn.execute(
            "UPDATE photos SET license=?, license_ref=? WHERE id=?",
            (data.get("license"), data.get("license_ref"), photo_id))

    def update_photo_caption(self, photo_id, data):
        self._one("photos", photo_id)
        self.conn.execute("UPDATE photos SET caption=? WHERE id=?",
                          (data.get("caption"), photo_id))

    def add_photo_ref(self, photo_id, data):
        self._one("photos", photo_id)
        self.conn.execute(
            "INSERT OR IGNORE INTO photo_refs(photo_id,ref_table,ref_id,note,created_at)"
            " VALUES (?,?,?,?,?)",
            (photo_id, data["ref_table"], int(data["ref_id"]),
             data.get("note"), timeutil.now_iso()))

    # ---------- 访谈 / 逐字稿 ----------
    def list_interviews(self):
        return [serializers.interview(r) for r in self.conn.execute(
            "SELECT * FROM interviews ORDER BY id")]

    def get_interview(self, id_):
        r = self._one("interviews", id_)
        d = serializers.interview(r)
        d["segments"] = [serializers.segment(x) for x in self.conn.execute(
            "SELECT * FROM interview_segments WHERE interview_id=? ORDER BY seq,id", (id_,))]
        d["person_name"] = (self.conn.execute(
            "SELECT name FROM persons WHERE id=?", (d["person_id"],)).fetchone() or [None])[0]
        return d

    def create_interview(self, data):
        now = timeutil.now_iso()
        cur = self.conn.execute(
            "INSERT INTO interviews(person_id,title,audio_path,place_id,collected_at,created_at)"
            " VALUES (?,?,?,?,?,?)",
            (data.get("person_id"), data["title"], data.get("audio_path"),
             data.get("place_id"), data.get("collected_at") or now, now))
        return cur.lastrowid

    def add_segment(self, interview_id, data):
        self._one("interviews", interview_id)
        seq = int(data.get("seq") or (self.conn.execute(
            "SELECT COALESCE(MAX(seq),0)+1 s FROM interview_segments WHERE interview_id=?",
            (interview_id,)).fetchone()["s"]))
        tc = data.get("timecode")
        cur = self.conn.execute(
            "INSERT INTO interview_segments(interview_id,timecode,timecode_missing,seq,speaker,text,created_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (interview_id, None if not tc else tc,
             1 if not tc else 0, seq, data.get("speaker"), data["text"], timeutil.now_iso()))
        return cur.lastrowid

    def withdraw_segment(self, segment_id, reason, editor_id):
        self._one("interview_segments", segment_id)
        now = timeutil.now_iso()
        self.conn.execute(
            "UPDATE interview_segments SET withdrawn=1, withdrawn_at=?, withdrawn_reason=? WHERE id=?",
            (now, reason, segment_id))
        self.conn.execute(
            "INSERT INTO retraction_tasks(target_table,target_id,reason,assets_json,created_by,created_at)"
            " VALUES (?,?,?,?,?,?)",
            ("interview_segments", segment_id, reason, "[]", editor_id, now))

    # ---------- 陈述与审校 ----------
    def list_statements(self, status=None):
        sql = "SELECT * FROM statements"
        args = ()
        if status:
            sql += " WHERE status=?"
            args = (status,)
        return [serializers.statement(r) for r in self.conn.execute(sql + " ORDER BY id", args)]

    def get_statement(self, id_):
        r = self._one("statements", id_)
        d = serializers.statement(r)
        d["reviews"] = [serializers.review(x) for x in self.conn.execute(
            "SELECT * FROM statement_reviews WHERE statement_id=? ORDER BY id", (id_,))]
        return d

    def create_statement(self, data, editor_id):
        now = timeutil.now_iso()
        et = data.get("event_time")
        et_raw = json.dumps(timeutil.normalize_event_time(et), ensure_ascii=False) if et else None
        cur = self.conn.execute(
            "INSERT INTO statements(subject_table,subject_id,title,body,event_time,collected_at,"
            "source_type,source_ref,status,created_by,created_at,updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (data["subject_table"], data.get("subject_id"), data["title"], data["body"],
             et_raw, data.get("collected_at") or now, data.get("source_type"),
             data.get("source_ref"), "draft", editor_id, now, now))
        return cur.lastrowid

    def update_statement(self, id_, data, editor_id):
        r = self._one("statements", id_)
        et = data.get("event_time")
        et_raw = json.dumps(timeutil.normalize_event_time(et), ensure_ascii=False) if et else None
        # 编辑已通过陈述 -> 回退为草稿，需要重新审校（防止静默改动已公开内容）
        new_status = r["status"]
        if r["status"] == "approved":
            new_status = "draft"
        self.conn.execute(
            "UPDATE statements SET title=?,body=?,event_time=?,collected_at=?,source_type=?,"
            "source_ref=?,status=?,updated_at=? WHERE id=?",
            (data.get("title", r["title"]), data.get("body", r["body"]), et_raw,
             data.get("collected_at") or r["collected_at"],
             data.get("source_type") or r["source_type"],
             data.get("source_ref") or r["source_ref"], new_status, timeutil.now_iso(), id_))

    def review_statement(self, id_, action, note, editor_id):
        r = self._one("statements", id_)
        allowed = {"submit": ("draft", "rejected"), "approve": ("submitted",),
                   "reject": ("submitted",), "retract": ("approved",)}
        if action not in allowed:
            raise StoreError("未知审校动作")
        if r["status"] not in allowed[action]:
            raise StoreError(f"陈述当前状态 {r['status']}，不能 {action}")
        now = timeutil.now_iso()
        new_status = {"submit": "submitted", "approve": "approved",
                      "reject": "rejected", "retract": "retracted"}[action]
        self.conn.execute("UPDATE statements SET status=?,updated_at=? WHERE id=?",
                          (new_status, now, id_))
        self.conn.execute(
            "INSERT INTO statement_reviews(statement_id,editor_id,action,note,created_at)"
            " VALUES (?,?,?,?,?)", (id_, editor_id, action, note, now))

    # ---------- 撤稿任务 ----------
    def list_tasks(self, status=None):
        sql = "SELECT * FROM retraction_tasks"
        args = ()
        if status:
            sql += " WHERE status=?"
            args = (status,)
        return [serializers.task(r) for r in self.conn.execute(sql + " ORDER BY id", args)]

    def withdraw_photo(self, photo_id, reason, editor_id):
        """撤照片：说明/授权分开追踪；原件与派生缩略图都进入撤稿任务。"""
        r = self._one("photos", photo_id)
        now = timeutil.now_iso()
        assets = []
        for p in (r["asset_path"], r["thumb_path"]):
            if p:
                assets.append(p)
        self.conn.execute(
            "UPDATE photos SET withdrawn=1, withdrawn_at=?, withdrawn_reason=? WHERE id=?",
            (now, reason, photo_id))
        cur = self.conn.execute(
            "INSERT INTO retraction_tasks(target_table,target_id,reason,status,assets_json,"
            "created_by,created_at) VALUES (?,?,?,?,?,?,?)",
            ("photos", photo_id, reason, "open",
             json.dumps(assets, ensure_ascii=False), editor_id, now))
        return cur.lastrowid

    def withdraw_interview(self, interview_id, reason, editor_id):
        r = self._one("interviews", interview_id)
        now = timeutil.now_iso()
        assets = [r["audio_path"]] if r["audio_path"] else []
        self.conn.execute(
            "UPDATE interviews SET withdrawn=1, withdrawn_at=?, withdrawn_reason=? WHERE id=?",
            (now, reason, interview_id))
        cur = self.conn.execute(
            "INSERT INTO retraction_tasks(target_table,target_id,reason,status,assets_json,"
            "created_by,created_at) VALUES (?,?,?,?,?,?,?)",
            ("interviews", interview_id, reason, "open",
             json.dumps(assets, ensure_ascii=False), editor_id, now))
        return cur.lastrowid

    def withdraw_jargon(self, jargon_id, reason, editor_id):
        self._one("jargons", jargon_id)
        now = timeutil.now_iso()
        self.conn.execute(
            "UPDATE jargons SET withdrawn=1, withdrawn_at=?, withdrawn_reason=? WHERE id=?",
            (now, reason, jargon_id))
        cur = self.conn.execute(
            "INSERT INTO retraction_tasks(target_table,target_id,reason,status,assets_json,"
            "created_by,created_at) VALUES (?,?,?,?,?,?,?)",
            ("jargons", jargon_id, reason, "open", "[]", editor_id, now))
        return cur.lastrowid

    def complete_task(self, task_id):
        """发布管线在生成后调用：资产已移除/替换为墓碑，任务关闭。"""
        self._one("retraction_tasks", task_id)
        self.conn.execute("UPDATE retraction_tasks SET status='done', done_at=? WHERE id=?",
                          (timeutil.now_iso(), task_id))

    # ---------- 时间轴 ----------
    def timeline_events(self):
        """合并所有可入轴的事件。排序只用事件时间，不用 id/采集时间/导入顺序。"""
        events = []
        for r in self.conn.execute(
                "SELECT * FROM statements WHERE status='approved'"):
            s = serializers.statement(r)
            if s["event_time"]:
                events.append({"kind": "statement", "ref_id": s["id"],
                               "title": s["title"], "event_time": s["event_time"],
                               "source_status": s["status"],
                               "collected_at": s["collected_at"]})
        for r in self.conn.execute("SELECT * FROM boat_events"):
            e = serializers.boat_event(r)
            if e["event_time"]:
                events.append({"kind": "boat_event", "ref_id": e["id"],
                               "boat_id": e["boat_id"], "title": e["title"],
                               "event_time": e["event_time"]})
        return timeutil.buckets(events)

    def state(self):
        """供编辑器一次拉取的整库视图。"""
        return {
            "editors": self.list_editors(),
            "boats": self.list_boats(),
            "places": self.list_places(),
            "persons": self.list_persons(),
            "jargons": [j for j in self.list_jargons() if not j["withdrawn"]],
            "photos": self.list_photos(),
            "interviews": self.list_interviews(),
            "statements": self.list_statements(),
            "candidates": self.list_candidates(),
            "merges": self.list_merges(),
            "tasks": self.list_tasks(),
            "timeline": self.timeline_events(),
        }
