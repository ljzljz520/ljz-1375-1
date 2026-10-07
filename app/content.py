"""内容图：动态查询最新已审内容；冻结快照；冻结图与最新图的漂移比较。"""
from __future__ import annotations

import copy
import hashlib
import json

from . import serializers, timeutil
from .db import connect


def build_live_graph(conn) -> dict:
    """仅收录“可公开”的最新内容（approved 陈述、未撤回条目）。

    这是动态查询结果；发布时把它整体冻结，后续任何改动都形成“漂移”。
    """
    def rows(sql):
        return [dict(r) for r in conn.execute(sql)]

    approved = [serializers.statement(r) for r in conn.execute(
        "SELECT * FROM statements WHERE status='approved' ORDER BY id")]
    boats = []
    for r in conn.execute("SELECT * FROM boats ORDER BY id"):
        b = serializers.boat(r)
        b["names"] = [serializers.boat_name(x) for x in conn.execute(
            "SELECT * FROM boat_names ORDER BY id") if x["boat_id"] == r["id"]]
        b["owners"] = [serializers.boat_owner(x) for x in conn.execute(
            "SELECT * FROM boat_owners ORDER BY id") if x["boat_id"] == r["id"]]
        b["events"] = [serializers.boat_event(x) for x in conn.execute(
            "SELECT * FROM boat_events ORDER BY id") if x["boat_id"] == r["id"]]
        boats.append(b)
    photos = []
    for r in conn.execute("SELECT * FROM photos ORDER BY id"):
        if r["withdrawn"]:
            continue
        p = serializers.photo(r)
        p["refs"] = [dict(ref_table=x["ref_table"], ref_id=x["ref_id"])
                     for x in conn.execute(
                         "SELECT * FROM photo_refs WHERE photo_id=? ORDER BY id", (r["id"],))]
        photos.append(p)
    interviews = []
    for r in conn.execute("SELECT * FROM interviews ORDER BY id"):
        if r["withdrawn"]:
            continue
        iv = serializers.interview(r)
        # 逐字稿保留已撤回段（带 withdrawn 标记），发布页以墓碑方式呈现
        iv["segments"] = [serializers.segment(x) for x in conn.execute(
            "SELECT * FROM interview_segments WHERE interview_id=? ORDER BY seq,id",
            (r["id"],))]
        interviews.append(iv)
    graph = {
        "version": 1,
        "generated_at": timeutil.now_iso(),
        "boats": boats,
        "places": [serializers.place(r) for r in conn.execute("SELECT * FROM places ORDER BY id")],
        "persons": [serializers.person(r) for r in conn.execute("SELECT * FROM persons ORDER BY id")],
        "jargons": [serializers.jargon(r) for r in conn.execute(
            "SELECT * FROM jargons WHERE withdrawn=0 ORDER BY id")],
        "photos": photos,
        "interviews": interviews,
        "statements": approved,
        "timeline": None,  # 由 freeze 时填充
    }
    return graph


def _timeline_from_graph(graph):
    events = []
    for s in graph["statements"]:
        if s["event_time"]:
            events.append({"kind": "statement", "ref_id": s["id"],
                           "title": s["title"], "event_time": s["event_time"]})
    for b in graph["boats"]:
        for e in b["events"]:
            if e["event_time"]:
                events.append({"kind": "boat_event", "ref_id": e["id"],
                               "boat_id": b["id"], "title": e["title"],
                               "event_time": e["event_time"]})
    return timeutil.buckets(events)


def freeze(conn) -> dict:
    """生成一次内容冻结（生成时间戳与时间轴一并固化）。"""
    graph = build_live_graph(conn)
    graph["timeline"] = _timeline_from_graph(graph)
    return graph


def content_hash(graph: dict) -> str:
    """内容哈希忽略 generated_at（冻结时间）本身，只对实质内容。"""
    g = copy.deepcopy(graph)
    g.pop("generated_at", None)
    blob = json.dumps(g, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _index(nodes, keyname="id"):
    return {n[keyname]: n for n in nodes}


def diff_graphs(frozen: dict, live: dict) -> dict:
    """比较发布时冻结图与动态查询的最新图，返回漂移明细。"""
    sections = ("boats", "places", "persons", "jargons", "photos",
                "interviews", "statements")
    drift = {}
    changed = False
    for sec in sections:
        fi, li = _index(frozen.get(sec, [])), _index(live.get(sec, []))
        added = sorted(set(li) - set(fi))
        removed = sorted(set(fi) - set(li))
        modified = []
        for k in sorted(set(fi) & set(li)):
            if json.dumps(fi[k], ensure_ascii=False, sort_keys=True) != \
               json.dumps(li[k], ensure_ascii=False, sort_keys=True):
                modified.append(k)
        if added or removed or modified:
            changed = True
            drift[sec] = {"added": added, "removed": removed, "modified": modified}
    # 时间轴漂移（新增可入轴事件等）
    ft = frozen.get("timeline") or {"dated": [], "undated": []}
    lt = _timeline_from_graph(live)
    timeline_changed = (
        [e["title"] + json.dumps(e.get("event_time"), ensure_ascii=False) for e in ft["dated"]]
        != [e["title"] + json.dumps(e.get("event_time"), ensure_ascii=False) for e in lt["dated"]]
        or [e["title"] for e in ft["undated"]] != [e["title"] for e in lt["undated"]])
    return {"drifted": changed or timeline_changed, "sections": drift,
            "timeline_changed": timeline_changed}


def load_frozen(graph_json: str) -> dict:
    return json.loads(graph_json)
