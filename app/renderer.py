"""把冻结内容图渲染为纯静态、可离线、无需脚本即可浏览的展厅站点。

一致性约束：地图、逐字稿、无脚本目录都从同一份冻结图 graph 生成，
因此一次发布内彼此必然一致；不查询“最新”数据库。
"""
from __future__ import annotations

import html
import json
from pathlib import Path

from . import timeutil

CSS = """
body{font-family:system-ui,'PingFang SC','Microsoft YaHei',sans-serif;margin:0;background:#f5f3ee;color:#222;line-height:1.6}
header{background:#1f3a5f;color:#fff;padding:14px 22px}
header a{color:#cfe0f5;margin-right:14px;text-decoration:none}
main{max-width:960px;margin:18px auto;padding:0 16px}
.card{background:#fff;border:1px solid #ddd;border-radius:8px;padding:14px 16px;margin:12px 0}
h1{font-size:22px}h2{font-size:18px;border-bottom:2px solid #1f3a5f;padding-bottom:4px}
.badge{display:inline-block;font-size:12px;padding:1px 8px;border-radius:10px;margin-left:6px;color:#fff}
.b-exact{background:#3a7d44}.b-approx{background:#b8860b}.b-range{background:#8a5a00}.b-unknown{background:#8a3b3b}
.status{font-size:12px;padding:1px 7px;border-radius:4px;border:1px solid #999}
.s-approved{background:#e4f3e6}.s-retracted{background:#f6e0e0}.s-other{background:#eee}
.meta{color:#666;font-size:13px}.tombstone{background:#f6e0e0;border-left:4px solid #b94a4a;padding:8px 12px;margin:6px 0}
.dot{position:absolute;width:12px;height:12px;border-radius:50%;background:#c0392b;border:2px solid #fff;box-sizing:border-box}
.mapwrap{position:relative;background:linear-gradient(#cfe3f2,#e9f1e4);border:1px solid #99b;height:360px;border-radius:8px}
.dotlabel{position:absolute;font-size:11px;background:#fff;padding:0 4px;border-radius:3px;white-space:nowrap}
.thumb{max-width:160px;border:1px solid #ccc}
a{color:#1f3a5f}
.timeline li{margin-bottom:6px}.undated{border-top:2px dashed #b94a4a;margin-top:14px;padding-top:8px}
nav.toc a{display:inline-block;margin:2px 10px 2px 0}
"""


def esc(s):
    return html.escape(str(s if s is not None else ""))


def badge(ev):
    label = timeutil.certainty_label(ev)
    cls = {"确切": "b-exact", "约": "b-approx", "区间": "b-range",
           "未定": "b-unknown"}.get(label, "b-unknown")
    return f'<span class="badge {cls}" title="事件发生时间的确定性">{label}</span>'


def status_badge(status):
    cls = {"approved": "s-approved", "retracted": "s-retracted"}.get(status, "s-other")
    label = {"approved": "已审校", "draft": "草稿", "submitted": "待审校",
             "rejected": "已退回", "retracted": "已撤回"}.get(status, status)
    return f'<span class="status {cls}">{esc(label)}</span>'


def _layout(title, body, release):
    nav = ('<nav class="toc"><a href="index.html">首页</a>'
           '<a href="boats.html">船只</a><a href="jargons.html">行话</a>'
           '<a href="photos.html">照片</a><a href="interviews.html">口述</a>'
           '<a href="places.html">地点</a><a href="map.html">地图</a>'
           '<a href="timeline.html">时间轴</a><a href="sources.html">来源与审校</a>'
           '<a href="catalog.html">无脚本目录</a></nav>')
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)} · 渔港记忆展厅</title>
<style>{CSS}</style></head>
<body><header><strong>渔港记忆展厅</strong><div style="margin-top:6px">{nav}</div>
<div class="meta" style="color:#cfe0f5">发布 #{release['id']} · 发布时间 {esc(release['created_at'])}
 · 内容哈希 {esc(release['content_hash'])}</div></header><main>
<h1>{esc(title)}</h1>
{body}
</main></body></html>"""


def _three_times(item):
    """展示三个时间：事件发生 / 资料采集 / 系统发布（发布时间在页眉）。"""
    bits = []
    ev = item.get("event_time") or item.get("taken_event_time")
    bits.append("事件发生：" + timeutil.display(ev) + badge(ev))
    if item.get("collected_at"):
        bits.append("资料采集：" + esc(item["collected_at"]))
    return '<div class="meta">' + " ｜ ".join(bits) + "</div>"


def render_site(graph, release, out_dir: Path, asset_map: dict) -> list[str]:
    """渲染整站。asset_map: 逻辑相对路径 -> 发布目录内 assets 子路径。
    返回写入的全部文件相对路径（供发布校验清单）。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []

    def put(rel, content):
        p = out_dir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, str):
            p.write_text(content, encoding="utf-8")
        else:
            p.write_bytes(content)
        written.append(rel.replace("\\", "/"))

    rel = release
    # 首页
    total = (len(graph["boats"]) + len(graph["jargons"]) + len(graph["photos"])
             + len(graph["interviews"]) + len(graph["places"])
             + len(graph["statements"]))
    body = f"""
    <div class="card"><p>本展厅为<b>静态冻结版本</b>，所有地图、逐字稿与目录同源生成，
    可离线浏览，且<b>无需脚本</b>。条目旁的徽标标明史料时间的确定性与审校状态。</p>
    <p>船只 {len(graph['boats'])} · 行话 {len(graph['jargons'])} · 照片 {len(graph['photos'])}
     · 口述 {len(graph['interviews'])} · 地点 {len(graph['places'])}
     · 已审校陈述 {len(graph['statements'])}（合计 {total} 项）</p></div>
    <div class="card"><h2>最近时间轴</h2>{_timeline_html(graph['timeline'], limit=5)}</div>
    """
    put("index.html", _layout("首页", body, rel))

    # 船只
    cards = []
    for b in graph["boats"]:
        rows = ""
        for n in b["names"]:
            rows += f"<li>曾用名「{esc(n['name'])}」 {esc(timeutil.display(n['event_time']))}{badge(n['event_time'])}</li>"
        for o in b["owners"]:
            rows += f"<li>船东 {esc(o['owner'])} {esc(timeutil.display(o['event_time']))}{badge(o['event_time'])}</li>"
        for e in b["events"]:
            rows += f"<li>{esc(e['kind'])}：{esc(e['title'])} {esc(timeutil.display(e['event_time']))}{badge(e['event_time'])}</li>"
        stmts = "".join(
            f"<li>{status_badge(s['status'])} {esc(s['title'])}</li>"
            for s in graph["statements"]
            if s["subject_table"] == "boats" and s["subject_id"] == b["id"])
        merged = "（已被并入）" if b["status"] == "merged_into" else ""
        cards.append(f"<div class='card'><h2>#{b['id']} {esc(b['current_name'])} {esc(merged)}</h2>"
                     f"<div class='meta'>船号 {esc(b.get('hull_no') or '未知')} · "
                     f"建造 {esc(timeutil.display(b['built_event_time']))}{badge(b['built_event_time'])}</div>"
                     f"<ul>{rows}</ul>{('<h3>史料陈述</h3><ul>'+stmts+'</ul>') if stmts else ''}</div>")
    put("boats.html", _layout("船只", "".join(cards), rel))

    # 行话
    cards = []
    for j in graph["jargons"]:
        cards.append(f"<div class='card'><h2>{esc(j['term'])} "
                     f"<span class='meta'>{esc(j.get('pronunciation') or '')}</span></h2>"
                     f"<p>{esc(j.get('meaning') or '')}</p>"
                     f"<p class='meta'>例：{esc(j.get('example') or '')}</p>"
                     f"<div class='meta'>资料采集：{esc(j['collected_at'])}</div></div>")
    put("jargons.html", _layout("行话", "".join(cards) or "<p>暂无。</p>", rel))

    # 照片（说明与授权分开显示；多地点引用）
    cards = []
    place_names = {p["id"]: p["name"] for p in graph["places"]}
    for p_ in graph["photos"]:
        img = ""
        if p_.get("thumb_path") and p_["thumb_path"] in asset_map:
            img = f"<img class='thumb' src='{esc(asset_map[p_['thumb_path']])}' alt='照片缩略图'>"
        refs = [f"{esc(place_names.get(r['ref_id'], '#'+str(r['ref_id'])))}"
                for r in p_["refs"] if r["ref_table"] == "places"]
        cards.append(
            "<div class='card'>" + img +
            f"<h2>照片 #{p_['id']}</h2>"
            f"<p><b>说明：</b>{esc(p_.get('caption') or '（无说明）')}</p>"
            f"<p class='meta'><b>授权：</b>{esc(p_.get('license') or '未授权/待核实')}"
            f"（{esc(p_.get('license_ref') or '无凭证')}）<br>"
            f"说明与授权分开登记、分开追踪。</p>"
            + _three_times(p_)
            + f"<p class='meta'>被 {len(p_['refs'])} 处引用：{esc('、'.join(refs)) or '—'}</p></div>")
    put("photos.html", _layout("照片", "".join(cards) or "<p>暂无。</p>", rel))

    # 口述与逐字稿
    cards = []
    for iv in graph["interviews"]:
        segs = []
        for s in iv["segments"]:
            if s["withdrawn"]:
                segs.append(
                    "<div class='tombstone'>"
                    f"[本段已由受访者撤回"
                    + (f"：{esc(s['withdrawn_reason'])}" if s.get("withdrawn_reason") else "")
                    + "]</div>")
            else:
                tc = esc(s["timecode"]) if s.get("timecode") else \
                    "<span class='status s-other' title='原始录音无此时码'>时码缺失</span>"
                segs.append(
                    f"<div class='card'><span class='meta'>{tc} · {esc(s.get('speaker') or '')}</span>"
                    f"<div>{esc(s['text'])}</div></div>")
        audio = ""
        if iv.get("audio_path") and iv["audio_path"] in asset_map:
            audio = (f"<audio controls preload='none' src='{esc(asset_map[iv['audio_path']])}'></audio>"
                     "<noscript><p class='meta'>音频需脚本/播放器；逐字稿可直接阅读。</p></noscript>")
        elif not iv.get("audio_path"):
            audio = "<p class='meta'>（无音频文件，仅逐字稿）</p>"
        cards.append(
            f"<div class='card'><h2>口述 #{iv['id']}：{esc(iv['title'])}</h2>"
            f"<div class='meta'>资料采集：{esc(iv['collected_at'])}</div>{audio}"
            + "<h3>逐字稿（与时码一致生成）</h3>" + "".join(segs) + "</div>")
    put("interviews.html", _layout("口述", "".join(cards) or "<p>暂无。</p>", rel))

    # 地点
    cards = []
    for p_ in graph["places"]:
        ref_photos = [ph for ph in graph["photos"]
                      if any(r["ref_table"] == "places" and r["ref_id"] == p_["id"]
                             for r in ph["refs"])]
        imgs = "".join(
            f"<img class='thumb' src='{esc(asset_map[ph['thumb_path']])}' alt='{esc(ph.get('caption') or '')}'>"
            for ph in ref_photos if ph.get("thumb_path") in asset_map)
        cards.append(f"<div class='card'><h2>{esc(p_['name'])}</h2>"
                     f"<div class='meta'>{esc(p_.get('kind') or '')} · 坐标 "
                     f"{esc(p_.get('lat'))},{esc(p_.get('lng'))}</div>"
                     f"<p>{esc(p_.get('note') or '')}</p>{imgs or '<p class=meta>无照片</p>'}</div>")
    put("places.html", _layout("地点", "".join(cards), rel))

    # 地图（与地点、照片同一份冻结数据；纯 CSS 定位，无脚本、可离线）
    dots = []
    geo = [p for p in graph["places"] if p.get("lat") is not None and p.get("lng") is not None]
    if geo:
        lats = [p["lat"] for p in geo]
        lngs = [p["lng"] for p in geo]
        lat_lo, lat_hi = min(lats), max(lats)
        lng_lo, lng_hi = min(lngs), max(lngs)
        span_lat = (lat_hi - lat_lo) or 0.01
        span_lng = (lng_hi - lng_lo) or 0.01
        for p in geo:
            top = (1 - (p["lat"] - lat_lo) / span_lat) * 92 + 2
            left = (p["lng"] - lng_lo) / span_lng * 92 + 2
            dots.append(
                f"<a class='dot' style='top:{top:.1f}%;left:{left:.1f}%' "
                f"title='{esc(p['name'])}' href='places.html'></a>"
                f"<span class='dotlabel' style='top:{top:.1f}%;left:{left+2:.1f}%'>{esc(p['name'])}</span>")
    put("map.html", _layout("地图",
        f"<div class='mapwrap'>{''.join(dots)}</div>"
        "<p class='meta'>地图点位与地点页、照片引用来自同一冻结图，无脚本可查看。</p>", rel))

    # 时间轴
    put("timeline.html", _layout("时间轴", _timeline_html(graph["timeline"]), rel))

    # 来源与审校
    cards = []
    for s in graph["statements"]:
        cards.append(
            f"<div class='card'><h2>{esc(s['title'])} {status_badge(s['status'])}</h2>"
            f"<p>{esc(s['body'])}</p>"
            + _three_times(s)
            + f"<p class='meta'>来源类型：{esc(s.get('source_type') or '未注')} · "
            f"来源出处：{esc(s.get('source_ref') or '未注')}</p></div>")
    put("sources.html", _layout("来源与审校", "".join(cards) or "<p>暂无已审校陈述。</p>", rel))

    # 无脚本目录：确定性枚举冻结图中一切条目与资源（不依赖运行时/数据库）
    lines = []
    for sec, label in (("boats", "船只"), ("jargons", "行话"), ("photos", "照片"),
                       ("interviews", "口述"), ("places", "地点"),
                       ("statements", "已审校陈述")):
        for item in graph[sec]:
            name = item.get("current_name") or item.get("term") or item.get("title") \
                or item.get("caption") or ("#%s" % item["id"])
            lines.append(f"{label}\t#{item['id']}\t{name}")
    for logical, pub in sorted(asset_map.items()):
        lines.append(f"资源\t{pub}\t<- {logical}")
    pre = "\n".join(lines)
    cat_html = ("<p>本目录在发布时由冻结图一次性生成，纯静态、无需脚本与数据库；"
                "离线页面恢复后仍可据此核对资源。</p>"
                f"<pre class='card'>{esc(pre)}</pre>")
    put("catalog.html", _layout("无脚本目录", cat_html, rel))
    # 机器可读清单（一致性核对用）。files 覆盖页面与清单本身，assets 覆盖资源。
    manifest_assets = sorted(asset_map.values())
    put("catalog.txt", "\n".join(lines) + "\n")
    manifest = {
        "release_id": release["id"], "content_hash": release["content_hash"],
        "created_at": release["created_at"], "assets": manifest_assets,
        "files": sorted(set(written) | {"manifest.json"}),
        "pages": ["index.html", "boats.html", "jargons.html", "photos.html",
                  "interviews.html", "places.html", "map.html", "timeline.html",
                  "sources.html", "catalog.html"]}
    put("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    # manifest.json 自身也纳入完整性清单
    return sorted(set(written) | {"manifest.json"})


def _timeline_html(tl, limit=None):
    dated = tl["dated"][:limit] if limit else tl["dated"]
    rows = "".join(
        f"<li>{esc(timeutil.display(e['event_time']))}{badge(e['event_time'])} "
        f"{esc(e['title'])}</li>" for e in dated)
    html_dated = f"<ul class='timeline'>{rows}</ul>" if rows else "<p>暂无。</p>"
    if limit:
        return html_dated + "<p><a href='timeline.html'>查看完整时间轴</a></p>"
    und = "".join(f"<li>{esc(e['title'])} {badge(None)}</li>" for e in tl["undated"])
    return (html_dated
            + ("<div class='undated'><h2>年代未定 / 无法可靠排序</h2>"
               f"<ul class='timeline'>{und}</ul>"
               "<p class='meta'>这些事件缺少可用的事件时间上下界，"
               "不以采集时间或导入顺序代替先后。</p></div>" if und else ""))
