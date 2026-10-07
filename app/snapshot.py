# -*- coding: utf-8 -*-
"""
发布内容图（content graph）。
- 发布时从当前工作库冻结一张不可变快照；
- 预览 = 冻结快照 vs 动态查询的最新陈述（diff_snapshots）；
- 公开时间（published_at）在首次随版本发布时回填。
"""
import json
import datetime

from .db import load_tp
from .timeutil import display, is_undated, sort_key, order_strength


def _assets_for(conn, asset_id):
    if not asset_id:
        return None, []
    a = conn.execute("SELECT * FROM assets WHERE id=?", (asset_id,)).fetchone()
    if not a:
        return None, []
    deriv = [dict(r) for r in conn.execute(
        "SELECT * FROM assets WHERE derived_from=? ORDER BY id",
        (asset_id,)).fetchall()]
    return dict(a), deriv


def build_snapshot(conn):
    """从动态库构造最新内容图（发布前预览即调用它）。"""
    snap = {"built_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "ships": [], "jargons": [], "locations": [], "photos": [],
            "interviews": [], "statements": [], "timeline": [],
            "map": []}

    for r in conn.execute(
            "SELECT * FROM ships WHERE superseded=0 ORDER BY id"):
        s = dict(r)
        s["names"] = [dict(x) for x in conn.execute(
            "SELECT * FROM ship_names WHERE ship_id=? ORDER BY id", (s["id"],))]
        s["owners"] = [dict(x) for x in conn.execute(
            "SELECT * FROM ship_owners WHERE ship_id=? ORDER BY id", (s["id"],))]
        s["events"] = [dict(x) for x in conn.execute(
            "SELECT * FROM ship_events WHERE ship_id=? ORDER BY id", (s["id"],))]
        snap["ships"].append(s)

    snap["jargons"] = [dict(r) for r in conn.execute(
        "SELECT * FROM jargons WHERE status='approved' ORDER BY id")]
    snap["locations"] = [dict(r) for r in conn.execute(
        "SELECT * FROM locations ORDER BY id")]
    snap["statements"] = [dict(r) for r in conn.execute(
        "SELECT * FROM statements WHERE status='approved' ORDER BY id")]

    for r in conn.execute(
            "SELECT * FROM photos WHERE status='approved' ORDER BY id"):
        p = dict(r)
        # 说明与授权分别判定；授权未授予/已撤的照片不进入公开图
        if p["license_status"] != "granted":
            continue
        asset, deriv = _assets_for(conn, p["asset_id"])
        if asset and not asset["active"]:
            continue  # 撤稿（含派生件任务）后资产不发布
        p["asset"] = asset
        p["derivatives"] = [d for d in deriv if d["active"]]
        p["location_ids"] = [x["location_id"] for x in conn.execute(
            "SELECT location_id FROM photo_locations WHERE photo_id=? "
            "ORDER BY location_id", (p["id"],))]
        p["ship_ids"] = [x["ship_id"] for x in conn.execute(
            "SELECT ship_id FROM photo_ships WHERE photo_id=? ORDER BY ship_id",
            (p["id"],))]
        snap["photos"].append(p)

    for r in conn.execute(
            "SELECT * FROM interviews WHERE status='approved' ORDER BY id"):
        iv = dict(r)
        if iv["consent"] != "granted":
            continue
        segs = []
        for x in conn.execute(
                "SELECT * FROM transcript_segments WHERE interview_id=? "
                "ORDER BY seq", (iv["id"],)):
            if x["withdrawn"]:
                continue  # 受访者撤回的段落不进入公开图（保留库内记录）
            d = dict(x)
            d["missing_timecode"] = not (x["tcin"] or x["tcout"])
            segs.append(d)
        asset, deriv = _assets_for(conn, iv["audio_asset"])
        if asset and not asset["active"]:
            continue
        iv["segments"] = segs
        iv["audio"] = asset
        snap["interviews"].append(iv)

    # 时间轴：只取公开内容，顺序只来自年代本身
    entries = []
    for s in snap["ships"]:
        for e in s["events"]:
            entries.append({"kind": "ship_event", "ref_id": e["id"],
                            "title": e["title"], "time": load_tp(e["occurred_at"]),
                            "source": e["source"]})
    for st in snap["statements"]:
        entries.append({"kind": "statement", "ref_id": st["id"],
                        "title": st["topic"], "time": load_tp(st["occurred_at"]),
                        "source": st["source"]})
    for j in snap["jargons"]:
        entries.append({"kind": "jargon", "ref_id": j["id"],
                        "title": j["term"], "time": load_tp(j["occurred_at"]),
                        "source": j["source"]})
    for p in snap["photos"]:
        entries.append({"kind": "photo", "ref_id": p["id"],
                        "title": p["caption"] or "（无说明照片）",
                        "time": load_tp(p["occurred_at"]),
                        "source": p["license"]})
    dated = [e for e in entries if not is_undated(e["time"])]
    undated = [e for e in entries if is_undated(e["time"])]
    dated.sort(key=lambda e: sort_key(e["time"]))
    for i, e in enumerate(dated):
        e["display"] = display(e["time"])
        e["order"] = "first" if i == 0 else order_strength(
            dated[i - 1]["time"], e["time"])
    for e in undated:
        e["display"] = display(e["time"])
        e["order"] = "undated"
    snap["timeline"] = dated + undated

    # 地图：一张照片可挂多个地点
    loc_by_id = {l["id"]: l for l in snap["locations"]}
    for p in snap["photos"]:
        for lid in p["location_ids"]:
            if lid in loc_by_id and loc_by_id[lid]["lat"] is not None:
                pass
    map_points = []
    for l in snap["locations"]:
        pids = [p["id"] for p in snap["photos"] if l["id"] in p["location_ids"]]
        if l["lat"] is not None and l["lng"] is not None:
            map_points.append({"location_id": l["id"], "name": l["name"],
                               "lat": l["lat"], "lng": l["lng"],
                               "photo_ids": pids})
    snap["map"] = map_points
    return snap


def stamp_publication(conn):
    """发布时刻回填公开时间（仅首次；再发布不改写原始公开时间）。"""
    now = datetime.datetime.now().date().isoformat()
    conn.execute("""UPDATE statements SET published_at=?
        WHERE status='approved' AND published_at IS NULL""",
                 (json.dumps({"kind": "exact", "start": now, "end": now,
                              "label": ""}, ensure_ascii=False),))
    conn.execute("""UPDATE photos SET published_at=?
        WHERE status='approved' AND published_at IS NULL""",
                 (json.dumps({"kind": "exact", "start": now, "end": now,
                              "label": ""}, ensure_ascii=False),))


# ---- 冻结图 vs 最新动态查询：发布预览的增/改/删 ----
def _index(snap, key, id_attr="id"):
    return {item[id_attr]: item for item in snap[key]}


def diff_snapshots(old, new):
    changes = {}
    for key in ("ships", "jargons", "locations", "photos",
                "interviews", "statements"):
        a, b = _index(old, key), _index(new, key)
        added = sorted(set(b) - set(a))
        removed = sorted(set(a) - set(b))
        changed = []
        for i in sorted(set(a) & set(b)):
            if _public_hash(a[i]) != _public_hash(b[i]):
                changed.append(i)
        if added or removed or changed:
            changes[key] = {"added": added, "removed": removed,
                            "changed": changed}
    old_tl = [(e["kind"], e["ref_id"]) for e in old.get("timeline", [])]
    new_tl = [(e["kind"], e["ref_id"]) for e in new.get("timeline", [])]
    if old_tl != new_tl or len(old_tl) != len(new_tl):
        changes["timeline"] = {"old_count": len(old_tl),
                               "new_count": len(new_tl)}
    return changes


def _public_hash(item):
    """比较时忽略内部工作字段。"""
    d = {k: v for k, v in item.items()
         if k not in ("updated_at", "built_at")}
    return json.dumps(d, ensure_ascii=False, sort_keys=True, default=str)
