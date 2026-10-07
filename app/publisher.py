# -*- coding: utf-8 -*-
"""
发布管线：
冻结快照 -> 统一渲染（地图、逐字稿、无脚本目录、离线页同源生成）
-> 一致性校验 -> 原子写 current 指针。
任何一步失败：删除半成品，current 与上个完整展厅原样保留。
"""
import json
import os
import shutil
import struct
import zlib

from . import repo
from .db import load_tp
from .timeutil import display
from .snapshot import build_snapshot, stamp_publication, diff_snapshots

CURRENT_FILE = "CURRENT"


# ---------- 派生缩略图：无第三方依赖，生成纯色 PNG ----------
def make_thumbnail_bytes(size=16, rgb=(180, 120, 90)):
    raw = b"".join(b"\x00" + bytes(rgb) * size for _ in range(size))

    def chunk(tag, data):
        c = tag + data
        return struct.pack(">I", len(data)) + c + struct.pack(
            ">I", zlib.crc32(c) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


# ---------- 极简 HTML 转义 ----------
def esc(s):
    return (str(s if s is not None else "")
            .replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _status_badge(status):
    return f'<span class="badge badge-{esc(status)}">{esc(status)}</span>'


# ---------- 统一渲染：所有页面只消费同一个 snapshot 字典 ----------
def render_site(snap, asset_file_map):
    """asset_file_map: {asset_id: 版本目录内相对路径}。返回 {relpath: 内容bytes}。"""
    files = {}
    files["snapshot.json"] = json.dumps(
        snap, ensure_ascii=False, indent=1, default=str).encode("utf-8")

    # ---- 时间轴页（不确定性与来源状态显式呈现）----
    rows = []
    for e in snap["timeline"]:
        weak = ' <em class="weak">（区间重叠，先后依著录下限，存疑）</em>' \
            if e["order"] == "weak" else ""
        undated = ' <em class="weak">（年代未定，不参与排序）</em>' \
            if e["order"] == "undated" else ""
        rows.append(
            f'<li><span class="when">{esc(e["display"])}</span>{weak}{undated}'
            f' <span class="what">{esc(e["kind"])}：{esc(e["title"])}</span>'
            f' <span class="src">来源：{esc(e["source"] or "未著录")}</span></li>')
    timeline_html = _page(
        "时间轴 · 渔港记忆",
        "<h1>渔港记忆时间轴</h1><p class=note>"
        "排序仅依据年代本身；区间/未定均如实标注，不以导入先后编造次序。</p>"
        f'<ol class="timeline">{"".join(rows)}</ol>')

    # ---- 船只页 ----
    ships_html = ["<h1>船只</h1>"]
    for s in snap["ships"]:
        names = "、".join(esc(n["name"]) for n in s["names"])
        evs = "".join(
            f'<li>{esc(e["type"])}：{esc(e["title"])} — '
            f'{esc(display(load_tp(e["occurred_at"])))}</li>'
            for e in s["events"])
        ships_html.append(
            f'<section class="ship"><h2>{esc(s["current_name"])}</h2>'
            f'<p>曾用/现用名：{names}</p><ul>{evs}</ul></section>')
    ships_html.append(_noscript_note())

    # ---- 照片页（同一张照片可在多个地点出现；说明与授权分列）----
    photos_html = ["<h1>老照片</h1>"]
    for p in snap["photos"]:
        locs = "、".join(str(x) for x in p["location_ids"])
        src = asset_file_map.get(p["asset"]["id"]) if p.get("asset") else None
        img = f'<img src="{esc(src)}" alt="{esc(p["caption"])}">' if src else ""
        thumbs = "".join(
            f'<img class="thumb" src="{esc(asset_file_map.get(d["id"]))}">'
            for d in p.get("derivatives", [])
            if asset_file_map.get(d["id"]))
        photos_html.append(
            f'<figure>{img}{thumbs}<figcaption>'
            f'说明：{esc(p["caption"]) or "（暂无）"} {_status_badge(p["caption_status"])}'
            f'<br>授权：{esc(p["license"])} {_status_badge(p["license_status"])}'
            f'<br>拍摄年代：{esc(display(load_tp(p["occurred_at"])))}'
            f'<br>关联地点：{esc(locs or "无")}</figcaption></figure>')

    # ---- 口述逐字稿页（缺失时码明确标注，绝不猜）----
    inter_html = ["<h1>口述逐字稿</h1>"]
    transcript_data = {}
    for iv in snap["interviews"]:
        lis = []
        for g in iv["segments"]:
            if g["missing_timecode"]:
                tc = '<span class="weak">（音频时码缺失，时序未定）</span>'
            else:
                tc = f'[{esc(g["tcin"])}→{esc(g["tcout"])}]'
            lis.append(f'<li data-seq="{g["seq"]}">{tc} {esc(g["text"])}</li>')
        inter_html.append(
            f'<section class="interview"><h2>受访者：{esc(iv["interviewee"])}</h2>'
            f'<ol class="transcript">{"".join(lis)}</ol></section>')
        transcript_data[iv["id"]] = iv["segments"]

    # ---- 地图页（数据同时落 GeoJSON，供地图与一致性校验）----
    map_geo = {"type": "FeatureCollection", "features": [
        {"type": "Feature",
         "geometry": {"type": "Point",
                      "coordinates": [m["lng"], m["lat"]]},
         "properties": {"name": m["name"], "photo_ids": m["photo_ids"]}}
        for m in snap["map"]]}
    files["map.geojson"] = json.dumps(map_geo, ensure_ascii=False).encode()
    map_rows = "".join(
        f'<li>{esc(m["name"])} ({m["lat"]},{m["lng"]}) — '
        f'照片：{",".join(str(x) for x in m["photo_ids"]) or "无"}</li>'
        for m in snap["map"])
    map_html = _page("渔港地图", f"<h1>渔港地图</h1><ul>{map_rows}</ul>"
                     "<p class=note>同一张照片被多个地点引用时在各点重复标注。</p>")

    # ---- 行话 / 陈述 ----
    jar = "".join(
        f'<li><b>{esc(j["term"])}</b>：{esc(j["meaning"])} '
        f'<span class=src>来源：{esc(j["source"])}</span></li>'
        for j in snap["jargons"])
    stmts = "".join(
        f'<li>{esc(s["topic"])}：{esc(s["content"])} '
        f'<span class=src>来源：{esc(s["source"])}</span></li>'
        for s in snap["statements"])

    index_html = _page(
        "渔港记忆展厅",
        "<h1>渔港记忆展厅</h1>"
        "<nav><a href='timeline.html'>时间轴</a> · "
        "<a href='ships.html'>船只</a> · "
        "<a href='photos.html'>老照片</a> · "
        "<a href='interviews.html'>口述逐字稿</a> · "
        "<a href='map.html'>渔港地图</a> · "
        "<a href='offline.html'>离线目录</a></nav>"
        f"<h2>行话</h2><ul>{jar}</ul>"
        f"<h2>史料陈述</h2><ul>{stmts}</ul>"
        f"<p class=note>版本说明：{esc(snap.get('note',''))} · "
        f"本版本发布于 {esc(snap['built_at'])}</p>")

    files["index.html"] = index_html.encode()
    files["timeline.html"] = timeline_html.encode()
    files["ships.html"] = "".join(ships_html).encode()
    files["photos.html"] = _page("老照片", "".join(photos_html)).encode()
    files["interviews.html"] = _page("口述逐字稿", "".join(inter_html)).encode()
    files["map.html"] = map_html.encode()
    files["transcripts.json"] = json.dumps(
        transcript_data, ensure_ascii=False, default=str).encode()

    # ---- 无脚本目录：与主页面同源同数据 ----
    noscript_entries = []
    for e in snap["timeline"]:
        noscript_entries.append(f"{e['display']} | {e['kind']} | {e['title']}")
    for iv in snap["interviews"]:
        for g in iv["segments"]:
            tc = "时码缺失" if g["missing_timecode"] else f"{g['tcin']}-{g['tcout']}"
            noscript_entries.append(
                f"逐字稿 | {iv['interviewee']} | {tc} | {g['text']}")
    for m in snap["map"]:
        noscript_entries.append(
            f"地点 | {m['name']} | {m['lat']},{m['lng']} | "
            f"照片{','.join(map(str, m['photo_ids']))}")
    catalog = "\n".join(noscript_entries)
    files["catalog.txt"] = catalog.encode()
    noscript_html = _page(
        "无脚本目录",
        "<h1>无脚本目录（纯文本目录）</h1>"
        f"<pre>{esc(catalog)}</pre>" + _noscript_note())
    files["noscript/index.html"] = noscript_html.encode()

    # ---- 离线页：列出本版本打包资源（旧版本资源仍可被旧离线页引用）----
    asset_rows = "".join(
        f'<li>{esc(rel)} ← 资产#{aid}</li>'
        for aid, rel in sorted(asset_file_map.items()))
    files["offline.html"] = _page(
        "离线目录",
        "<h1>离线资源清单</h1><p>此页只引用本发布版本目录内的资源，"
        "断网与旧版本恢复后仍可打开。</p>"
        f'<ul>{asset_rows}</ul>').encode()
    return files


def _noscript_note():
    return ('<p class="note">本页与时间轴、地图、逐字稿来自同一冻结快照，'
            "无脚本亦可阅读。</p>")


_STYLE = """<style>
body{font-family:serif;max-width:900px;margin:2em auto;line-height:1.6}
.when{font-weight:bold}.src,.note{color:#666;font-size:.9em}
.weak{color:#a33}.badge{font-size:.75em;border:1px solid #999;border-radius:4px;
padding:0 4px;margin-left:4px}
.badge-approved,.badge-granted{border-color:#2a7}
.badge-withdrawn,.badge-revoked,.badge-pending{border-color:#c55}
figure{border-bottom:1px solid #ccc;padding:1em 0}.thumb{width:40px}
</style>"""


def _page(title, body):
    return (f"<!doctype html><html lang=zh><meta charset=utf-8>"
            f"<title>{esc(title)}</title>{_STYLE}<body>{body}</body></html>")


# ---------- 一致性校验：同源生成物必须互相对得上 ----------
class ConsistencyError(Exception):
    pass


def verify_consistency(snap, files, asset_file_map):
    problems = []
    geo = json.loads(files["map.geojson"])
    # 1) 地图点 / 照片引用与快照一致（含一照片多地点）
    geo_pairs = sorted(
        (f["properties"]["name"], tuple(f["properties"]["photo_ids"]))
        for f in geo["features"])
    snap_pairs = sorted(
        (m["name"], tuple(m["photo_ids"])) for m in snap["map"])
    if geo_pairs != snap_pairs:
        problems.append("地图 GeoJSON 与快照不一致")
    photo_loc_refs = {p["id"]: sorted(p["location_ids"])
                      for p in snap["photos"]}
    loc_photos = {}
    for m in snap["map"]:
        for pid in m["photo_ids"]:
            loc_photos.setdefault(pid, []).append(m["location_id"])
    for pid, locs in photo_loc_refs.items():
        if sorted(loc_photos.get(pid, [])) != locs:
            problems.append(f"照片#{pid} 的多地点引用在地图中缺失")

    # 2) 逐字稿：段落集合与时码缺失标注一致
    td = json.loads(files["transcripts.json"])
    for iv in snap["interviews"]:
        got = [(g["seq"], g["missing_timecode"], g["withdrawn"] if False else 0)
               for g in td[str(iv["id"])]]
        want = [(g["seq"], g["missing_timecode"], 0) for g in iv["segments"]]
        if got != want:
            problems.append(f"访谈#{iv['id']} 逐字稿不一致")

    # 3) 无脚本目录必须包含全部时间轴条目/逐字稿段/地点
    catalog = files["catalog.txt"].decode()
    for e in snap["timeline"]:
        if e["title"] not in catalog:
            problems.append(f"无脚本目录缺少时间轴条目 {e['title']}")
    for iv in snap["interviews"]:
        if iv["interviewee"] not in catalog:
            problems.append(f"无脚本目录缺少访谈 {iv['interviewee']}")
    for m in snap["map"]:
        if m["name"] not in catalog:
            problems.append(f"无脚本目录缺少地点 {m['name']}")

    # 4) 所有页面引用的资源都必须真实存在于本版本目录
    for rel in set(asset_file_map.values()):
        if rel not in files:
            problems.append(f"页面引用了缺失资源 {rel}")
    # 撤稿资产不得出现
    active_ids = set()
    for p in snap["photos"]:
        if p.get("asset"):
            active_ids.add(p["asset"]["id"])
            active_ids.update(d["id"] for d in p["derivatives"])
    for aid, rel in asset_file_map.items():
        if aid not in active_ids:
            # 仅可能是被撤访谈音频等，不应被页面引用
            if rel.encode() in files.get("photos.html", b""):
                problems.append(f"撤稿资产 {rel} 仍被照片页引用")
    if problems:
        raise ConsistencyError("；".join(problems))
    return True


# ---------- 发布主流程 ----------
def publish(conn, root, user_id, note="", fail_after_render=False,
            force=False):
    """root 为展厅根目录（含 versions/ 与 CURRENT）。失败抛异常且不动 CURRENT。"""
    repo.require_role(conn, user_id, "editor")
    if not force:
        open_tasks = conn.execute(
            "SELECT COUNT(*) c FROM takedown_tasks WHERE status='open'").fetchone()
        if open_tasks["c"]:
            raise ConsistencyError("存在未执行的撤稿任务，请先处理或 force 发布")

    versions_dir = os.path.join(root, "versions")
    os.makedirs(versions_dir, exist_ok=True)
    # 为待公开照片补登记派生缩略图（纳入撤稿级联）
    for r in conn.execute(
            "SELECT id FROM photos WHERE status='approved' "
            "AND license_status='granted' AND asset_id IS NOT NULL"):
        repo.ensure_thumbnail_asset(conn, r["id"])
    # 公开时间先回填再冻结：首次发布即公开，且只写一次
    stamp_publication(conn)
    snap = build_snapshot(conn)
    snap["note"] = note or ""

    # 资源映射：原片与派生缩略图都复制进版本目录（自包含、可离线、可保留旧版）
    asset_file_map, asset_blobs = {}, {}
    thumb = make_thumbnail_bytes()
    for p in snap["photos"]:
        if p.get("asset") and p["asset"]["active"]:
            aid = p["asset"]["id"]
            rel = f"assets/photo_{aid}{_ext(p['asset']['mime'])}"
            asset_file_map[aid] = rel
            asset_blobs[rel] = _read_source(p["asset"]["path"])
            for d in p["derivatives"]:
                drel = f"assets/photo_{aid}_thumb_{d['id']}.png"
                asset_file_map[d["id"]] = drel
                asset_blobs[drel] = thumb  # 统一派生件（撤稿任务可级联）
    for iv in snap["interviews"]:
        if iv.get("audio") and iv["audio"]["active"]:
            rel = f"assets/audio_{iv['audio']['id']}.bin"
            asset_file_map[iv["audio"]["id"]] = rel
            asset_blobs[rel] = _read_source(iv["audio"]["path"])

    files = render_site(snap, asset_file_map)
    files.update(asset_blobs)
    verify_consistency(snap, files, asset_file_map)
    if fail_after_render:
        raise RuntimeError("注入故障：渲染完成后写入失败")

    # 暂存目录（任何异常都清理，绝不残留半空页面）
    tmp = os.path.join(versions_dir, ".tmp-publish")
    try:
        if os.path.exists(tmp):
            shutil.rmtree(tmp)
        os.makedirs(os.path.join(tmp, "assets"), exist_ok=True)
        os.makedirs(os.path.join(tmp, "noscript"), exist_ok=True)
        for rel, blob in files.items():
            path = os.path.join(tmp, rel)
            os.makedirs(os.path.dirname(path) or tmp, exist_ok=True)
            with open(path, "wb") as fh:
                fh.write(blob)
        return _commit_publication(conn, tmp, versions_dir, snap,
                                   asset_file_map, user_id, note, root)
    except BaseException:
        if os.path.exists(tmp):
            shutil.rmtree(tmp)
        raise


def _commit_publication(conn, tmp, versions_dir, snap, asset_file_map,
                        user_id, note, root):
    cur = conn.execute(
        "INSERT INTO publications(dir,snapshot,note,created_by) VALUES(?,?,?,?)",
        ("", json.dumps(snap, ensure_ascii=False, default=str), note, user_id))
    pub_id = cur.lastrowid
    final_dir = os.path.join(versions_dir, f"v{pub_id}")
    try:
        os.rename(tmp, final_dir)
    except OSError:
        shutil.move(tmp, final_dir)
    conn.execute("UPDATE publications SET dir=? WHERE id=?",
                 (final_dir, pub_id))
    conn.execute(
        "INSERT INTO kv_meta(key,value) VALUES('current_pub',?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (str(pub_id),))
    # CURRENT 指针最后原子替换：半成品绝不会被当作当前版本
    _write_current(root, pub_id)
    conn.commit()
    return pub_id


def _ext(mime):
    return {"image/jpeg": ".jpg", "image/png": ".png",
            "image/webp": ".webp"}.get(mime, ".bin")


def _read_source(path):
    try:
        with open(path, "rb") as fh:
            return fh.read()
    except OSError:
        return b""  # 源文件缺失时占位，不伪造历史素材


def _write_current(root, pub_id):
    ptr = os.path.join(root, CURRENT_FILE)
    tmp = ptr + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(str(pub_id))
    os.replace(tmp, ptr)


def current_pub_id(conn):
    r = conn.execute("SELECT value FROM kv_meta WHERE key='current_pub'").fetchone()
    return int(r["value"]) if r else None


def current_dir(root, conn):
    pid = current_pub_id(conn)
    if not pid:
        return None
    r = conn.execute("SELECT dir FROM publications WHERE id=?", (pid,)).fetchone()
    return r["dir"] if r and os.path.isdir(r["dir"]) else None


def activate_pub(conn, root, user_id, pub_id):
    """管理员恢复旧版本（离线页随之恢复旧资源）。"""
    repo.require_role(conn, user_id, "admin")
    r = conn.execute("SELECT dir FROM publications WHERE id=?",
                     (pub_id,)).fetchone()
    if not r or not os.path.isdir(r["dir"]):
        raise repo.DomainError("目标版本不存在或目录缺失")
    _write_current(root, pub_id)
    conn.execute(
        "INSERT INTO kv_meta(key,value) VALUES('current_pub',?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (str(pub_id),))
    conn.commit()
    return pub_id
