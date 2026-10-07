"""发布管线与发布版本管理。

流程：构造最新内容图 → 与基准冻结图比较（漂移检测）→ 以最终冻结图渲染
纯静态站点 → 拷贝资源/写撤稿墓碑 → 链接与完整性校验 → 原子切换 current。
任何一步失败：current 仍指向上个完整版本，绝不发布半空页面。
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from . import assets as assets_mod
from . import config, content, renderer, timeutil
from .db import connect

_FAULT_AFTER = int(os.environ.get("YUGANG_FAULT_AFTER_FILES", "-1"))


class PublishError(Exception):
    pass


def _current_release_id(conn):
    r = conn.execute("SELECT id FROM releases WHERE state='current' ORDER BY id DESC LIMIT 1").fetchone()
    return r["id"] if r else None


def current_release():
    conn = connect()
    try:
        rid = _current_release_id(conn)
        if not rid:
            return None
        from . import serializers
        return serializers.release(conn.execute(
            "SELECT * FROM releases WHERE id=?", (rid,)).fetchone())
    finally:
        conn.close()


def diff_against_current():
    """动态查询最新陈述/内容，与当前公开版本的冻结图比较。"""
    conn = connect()
    try:
        rid = _current_release_id(conn)
        live = content.build_live_graph(conn)
        if not rid:
            return {"has_current": False, "drifted": True,
                    "sections": {}, "timeline_changed": True}
        row = conn.execute("SELECT * FROM releases WHERE id=?", (rid,)).fetchone()
        frozen = content.load_frozen(row["graph_json"])
        d = content.diff_graphs(frozen, live)
        d["has_current"] = True
        d["current_release_id"] = rid
        return d
    finally:
        conn.close()


def _collect_assets(graph):
    """返回 {逻辑相对路径: (是否撤稿墓碑)}。原件与派生缩略图都收集。"""
    mp = {}
    for p in graph["photos"]:
        for key in ("asset_path", "thumb_path"):
            v = p.get(key)
            if v:
                mp[v] = False
    for iv in graph["interviews"]:
        if iv.get("audio_path"):
            mp[iv["audio_path"]] = False
    return mp


def _check_links(out_dir: Path, written, asset_map):
    """校验所有本地引用的资源/页面都真实存在（半空页面守卫）。"""
    import re
    missing = []
    html_files = [f for f in written if f.endswith(".html")]
    href = re.compile(r'(?:href|src)="([^"#:]+)"')
    for f in html_files:
        text = (out_dir / f).read_text(encoding="utf-8")
        for link in href.findall(text):
            if link.startswith(("http://", "https://", "mailto:")):
                continue
            target = (out_dir / f).parent / link
            if not target.exists():
                missing.append(f"{f} -> {link}")
    # manifest 中声明的资源必须存在
    for pub in asset_map.values():
        if not (out_dir / pub).exists():
            missing.append(f"manifest asset -> {pub}")
    return missing


def publish(note: str = "", editor_id: int | None = None,
            expected_hash: str | None = None) -> dict:
    conn = connect()
    try:
        # 1) 最新动态查询，冻结
        graph = content.freeze(conn)
        h = content.content_hash(graph)

        # 2) 漂移比较：发布面板可先给 expected_hash；不一致则拒绝（避免发布陈旧认知）
        rid_cur = _current_release_id(conn)
        if expected_hash and rid_cur:
            row = conn.execute("SELECT content_hash FROM releases WHERE id=?",
                               (rid_cur,)).fetchone()
            if row["content_hash"] != expected_hash:
                raise PublishError("当前公开版本已变化，请重新查看差异后再发布")

        # 3) 创建 building 发布记录与暂存目录
        now = timeutil.now_iso()
        cur = conn.execute(
            "INSERT INTO releases(content_hash,graph_json,dir,manifest_json,created_at,state,note)"
            " VALUES (?,?,?,?,?,?,?)",
            (h, json.dumps(graph, ensure_ascii=False), "", "{}", now, "building", note))
        release_id = cur.lastrowid
        stage = config.RELEASE_DIR / f"r{release_id:04d}"
        if stage.exists():
            shutil.rmtree(stage)
        stage.mkdir(parents=True)

        release_meta = {"id": release_id, "content_hash": h, "created_at": now}
        # building 记录先独立落库，保证即使后续失败也留下可排查记录
        conn.commit()
        try:
            # 4) 资源：拷贝；对撤稿任务中登记的资产写墓碑。
            # 已从内容图移除（如撤稿照片）但仍在撤稿任务中的资源也要随版落盘为墓碑。
            asset_map = {}
            logical = _collect_assets(graph)
            tombstoned = set()
            for t in conn.execute("SELECT * FROM retraction_tasks WHERE status='open'"):
                for a in json.loads(t["assets_json"] or "[]"):
                    tombstoned.add(a)
                    logical.setdefault(a, True)
            for logical_path, _ in logical.items():
                src = assets_mod.abs_path(logical_path)
                pub = "assets/" + logical_path
                dest = stage / pub
                dest.parent.mkdir(parents=True, exist_ok=True)
                if logical_path in tombstoned or (src and not src.exists()):
                    assets_mod.write_tombstone(dest)
                else:
                    shutil.copyfile(src, dest)
                asset_map[logical_path] = pub

            # 5) 渲染站点
            written = renderer.render_site(graph, release_meta, stage, asset_map)

            if _FAULT_AFTER >= 0 and len(written) > _FAULT_AFTER:
                raise PublishError(f"模拟发布失败（已生成 {len(written)} 个文件）")

            # 6) 链接 / 完整性校验
            missing = _check_links(stage, written, asset_map)
            if missing:
                raise PublishError("发布校验失败，缺资源：" + "; ".join(missing[:5]))

            # 7) 关闭“已在本版真正处理”的撤稿任务：其登记资产必须都已随版落盘
            #    （为墓碑或随图拷贝）。资产未覆盖的任务保持 open，避免误关闭。
            for t in conn.execute("SELECT * FROM retraction_tasks WHERE status='open'").fetchall():
                listed = json.loads(t["assets_json"] or "[]")
                # 无资产的撤稿（如撤回逐字稿段）：其效果已随图渲染，可直接关闭。
                if all(a in asset_map for a in listed):
                    conn.execute("UPDATE retraction_tasks SET status='done', done_at=? WHERE id=?",
                                 (timeutil.now_iso(), t["id"]))

            # 8) 记录冻结陈述快照
            for s in graph["statements"]:
                conn.execute(
                    "INSERT OR REPLACE INTO release_statements(release_id,statement_id,version_snapshot)"
                    " VALUES (?,?,?)",
                    (release_id, s["id"], json.dumps(s, ensure_ascii=False)))

            manifest = {"release_id": release_id, "content_hash": h,
                        "created_at": now, "files": written,
                        "assets": sorted(asset_map.values())}
            conn.execute(
                "UPDATE releases SET dir=?, manifest_json=?, state='current' WHERE id=?",
                (str(stage), json.dumps(manifest, ensure_ascii=False), release_id))
            # 旧当前版本 -> superseded
            if rid_cur:
                conn.execute("UPDATE releases SET state='superseded' WHERE id=?", (rid_cur,))
            conn.commit()
        except Exception:
            # 仅回滚“成功后才应生效”的数据变更；building 记录已独立提交，仍然存在
            conn.rollback()
            try:
                conn.execute("UPDATE releases SET state='failed' WHERE id=?", (release_id,))
                conn.commit()
            except Exception:
                pass
            if stage.exists():
                shutil.rmtree(stage, ignore_errors=True)
            raise

        # 9) 原子切换 current（事务提交后才动链接）
        _promote(stage)
        return {"release_id": release_id, "content_hash": h,
                "files": written, "assets": sorted(asset_map.values()),
                "superseded": rid_cur}
    finally:
        conn.close()


def _promote(stage: Path):
    config.RELEASE_DIR.mkdir(parents=True, exist_ok=True)
    link = config.CURRENT_LINK
    tmp = config.RELEASE_DIR / "current.tmp"
    if tmp.is_symlink() or tmp.exists():
        if tmp.is_symlink() or tmp.is_file():
            tmp.unlink()
        else:
            shutil.rmtree(tmp)
    os.symlink(stage, tmp)
    os.replace(tmp, link)  # 原子替换


def rollback(release_id: int) -> dict:
    """恢复旧版本：完整历史版本资源仍在磁盘上，切换 current 即可，旧资源随之恢复。"""
    conn = connect()
    try:
        row = conn.execute("SELECT * FROM releases WHERE id=?", (release_id,)).fetchone()
        if not row:
            raise PublishError("目标版本不存在")
        if row["state"] in ("building", "failed"):
            raise PublishError("不能恢复到未成功/失败的版本")
        stage = Path(row["dir"])
        if not stage.exists():
            raise PublishError("版本资源已不在磁盘，无法离线恢复")
        rid_cur = _current_release_id(conn)
        now = timeutil.now_iso()
        cur = conn.execute(
            "INSERT INTO releases(content_hash,graph_json,dir,manifest_json,created_at,"
            "state,note,rollback_of) VALUES (?,?,?,?,?,?,?,?)",
            (row["content_hash"], row["graph_json"], row["dir"], row["manifest_json"],
             now, "current", f"回滚恢复自 #{release_id}", release_id))
        new_id = cur.lastrowid
        if rid_cur:
            conn.execute("UPDATE releases SET state='superseded' WHERE id=?", (rid_cur,))
        # 被恢复版本本身保留历史状态（superseded/rolled_back 均可），新记录承担 current
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    _promote(stage)
    return {"new_release_id": new_id, "restored_from": release_id}


def list_releases():
    from . import serializers
    conn = connect()
    try:
        return [serializers.release(r) for r in conn.execute(
            "SELECT * FROM releases ORDER BY id")]
    finally:
        conn.close()


def validate_current() -> dict:
    """核对线上 current 版本的文件与清单是否齐全（验收/巡检用）。"""
    link = config.CURRENT_LINK
    if not link.exists():
        return {"published": False, "missing": [], "ok": False}
    stage = Path(os.path.realpath(link))
    manifest = json.loads((stage / "manifest.json").read_text(encoding="utf-8"))
    missing = [f for f in manifest["files"] if not (stage / f).exists()]
    missing += [a for a in manifest["assets"] if not (stage / a).exists()]
    return {"published": True, "release_id": manifest["release_id"],
            "ok": not missing, "missing": missing}
