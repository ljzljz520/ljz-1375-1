"""验收测试：覆盖题目五个场景 + 三条贯穿不变量。"""
import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from helpers import Server, tiny_png_b64  # noqa: E402


class HarborCase(unittest.TestCase):
    srv: Server = None

    @classmethod
    def setUpClass(cls):
        cls.srv = Server()

    @classmethod
    def tearDownClass(cls):
        cls.srv.stop()

    # ---------- 场景1：同一张照片被多个地点引用 ----------
    def test_01_photo_referenced_by_multiple_places(self):
        s = self.srv
        st, photo = s.get("/api/state")
        pid = 1  # 种子照片
        refs = [p for p in photo["photos"] if p["id"] == pid][0]["refs"]
        place_targets = sorted(r["ref_id"] for r in refs if r["ref_table"] == "places")
        self.assertEqual(place_targets, [1, 2], "同一张照片应同时被两个地点引用")

        # 再加一个新建地点引用同照片，验证 M:N 不复制照片实体
        _, place = s.post("/api/places", {"name": "南防波堤", "lat": 30.0, "lng": 121.99})
        nid = place["id"]
        st, _ = s.post(f"/api/photos/{pid}/refs", {"ref_table": "places", "ref_id": nid})
        self.assertEqual(st, 200)
        _, st2 = s.get("/api/state")
        refs2 = [p for p in st2["photos"] if p["id"] == pid][0]["refs"]
        self.assertEqual(sorted(r["ref_id"] for r in refs2 if r["ref_table"] == "places"),
                         [1, 2, nid])
        # 仍是同一张照片（同资源路径），不是三份拷贝
        self.assertEqual(len([p for p in st2["photos"] if p["id"] == pid]), 1)

    # ---------- 场景2：受访者撤回一段口述 ----------
    def test_02_interviewee_withdraws_segment(self):
        s = self.srv
        _, iv = s.get("/api/interviews/1")
        seg2 = [x for x in iv["segments"] if x["seq"] == 2][0]
        self.assertTrue(seg2["timecode_missing"], "第二段应标记为时码缺失（场景4）")
        st, _ = s.post(f"/api/segments/{seg2['id']}/withdraw",
                       {"reason": "受访者事后表示不愿公开"})
        self.assertEqual(st, 200)
        _, iv2 = s.get("/api/interviews/1")
        after = [x for x in iv2["segments"] if x["id"] == seg2["id"]][0]
        self.assertTrue(after["withdrawn"])
        # 撤稿任务生成
        _, st3 = s.get("/api/state")
        self.assertTrue(any(t["target_table"] == "interview_segments"
                            and t["target_id"] == seg2["id"] for t in st3["tasks"]))

    # ---------- 场景3：两位编辑确认后合并船名；可撤销并恢复引用 ----------
    def test_03_merge_then_undo_restores_refs(self):
        s = self.srv
        _, state = s.get("/api/state")
        cand = [c for c in state["candidates"] if c["boat_a"] == 1 and c["boat_b"] == 2][0]
        # 给被并船 #2 先挂一条陈述（草稿也跟着迁移）
        _, sm = s.post("/api/statements", {
            "subject_table": "boats", "subject_id": 2,
            "title": "3301号B 档案备注", "body": "待考",
            "event_time": {"type": "undated", "note": "档案残缺"}})
        stmt_b = sm["id"]

        # 同名不应自动合并：初始候选是 open
        self.assertEqual(cand["status"], "open")
        # 两位编辑确认：创建者本人不能合并（需第二位编辑）
        st_self, err_self = s.post(f"/api/candidates/{cand['id']}/merge",
                                   {"surviving_boat_id": 1, "absorbed_boat_id": 2},
                                   editor=1)
        self.assertEqual(st_self, 400)
        self.assertIn("第二位编辑", err_self["error"])
        # 第二位编辑（审校）确认合并
        st, merge = s.post(f"/api/candidates/{cand['id']}/merge",
                           {"surviving_boat_id": 1, "absorbed_boat_id": 2}, editor=2)
        self.assertEqual(st, 200)
        mid = merge["merge_id"]
        _, st2 = s.get("/api/state")
        b2 = [b for b in st2["boats"] if b["id"] == 2][0]
        self.assertEqual(b2["status"], "merged_into")
        self.assertEqual(b2["merged_into_id"], 1)
        # 陈述引用已迁移到 #1
        stmt = [x for x in st2["statements"] if x["id"] == stmt_b][0]
        self.assertEqual(stmt["subject_id"], 1)
        # 被并船现用名作为别名保留在 #1
        _, b1 = s.get("/api/boats/1/detail")
        self.assertTrue(any(n["name"] == "鲁岱渔3301" for n in b1["names"]))

        # 撤销合并 -> 恢复身份、历史与陈述引用
        st, _ = s.post(f"/api/merges/{mid}/undo", {}, editor=2)
        self.assertEqual(st, 200)
        _, st3 = s.get("/api/state")
        b2u = [b for b in st3["boats"] if b["id"] == 2][0]
        self.assertEqual(b2u["status"], "active")
        self.assertIsNone(b2u["merged_into_id"])
        stmt_u = [x for x in st3["statements"] if x["id"] == stmt_b][0]
        self.assertEqual(stmt_u["subject_id"], 2, "撤销合并后引用应恢复到原船")
        _, b1u = s.get("/api/boats/1/detail")
        self.assertFalse(any(n.get("note", "").startswith("合并自船只 #2")
                             for n in b1u["names"]), "合并别名应删除")

    # ---------- 场景4：音频时码缺失被显式处理 ----------
    def test_04_missing_timecode_is_explicit(self):
        s = self.srv
        _, iv = s.get("/api/interviews/1")
        seg2 = [x for x in iv["segments"] if x["seq"] == 2][0]
        self.assertTrue(seg2["timecode_missing"])
        self.assertIsNone(seg2["timecode"])
        seg1 = [x for x in iv["segments"] if x["seq"] == 1][0]
        self.assertFalse(seg1["timecode_missing"])
        self.assertEqual(seg1["timecode"], "00:00:12")

    # ---------- 场景5 + 贯穿：发布、撤稿、失败保留旧版、离线恢复旧资源 ----------
    def test_05_publish_withdraw_failover_and_rollback(self):
        s = self.srv
        # 首次发布（种子）
        st, r1 = s.post("/api/publish", {"note": "v1 完整版本"})
        self.assertEqual(st, 200)
        v1 = r1["release_id"]

        # 上传一张真实照片原件，生成派生缩略图
        _, state = s.get("/api/state")
        new_photo = [p for p in state["photos"] if p["id"] == 1][0]
        st, up = s.post("/api/photos/1/raw",
                        {"filename": "harbor.png", "content": tiny_png_b64(),
                         "encoding": "base64"})
        self.assertEqual(st, 200)
        self.assertTrue(up["thumb_path"], "应生成派生缩略图")
        self.assertFalse(up["asset_path"].startswith("assets/"),
                         "逻辑路径不得携带 assets/ 前缀，否则发布会拼出双重前缀")
        self.assertFalse(up["thumb_path"].startswith("assets/"))
        orig_abs = s.data_path("assets", up["asset_path"])
        thumb_abs = s.data_path("assets", up["thumb_path"])
        self.assertTrue(orig_abs.exists() and thumb_abs.exists())

        # 发布 v2，资源随版拷贝（逻辑路径映射到站点 assets/ 下，无双重前缀）
        _, r2 = s.post("/api/publish", {"note": "v2 含照片原件与缩略图"})
        v2 = r2["release_id"]
        rel1 = s.data_path("releases", f"r{v1:04d}")
        rel2 = s.data_path("releases", f"r{v2:04d}")
        self.assertNotIn("assets/assets/", " ".join(r2["files"] + r2["assets"]),
                         "发布清单不得出现双重 assets 前缀")
        staged_orig = rel2 / ("assets/" + up["asset_path"])
        staged_thumb = rel2 / ("assets/" + up["thumb_path"])
        self.assertTrue(staged_orig.exists() and staged_thumb.exists())
        self.assertEqual(staged_thumb.read_bytes()[:8], b"\x89PNG\r\n\x1a\n",
                         "缩略图是真实派生 PNG")
        # HTML 中引用的资源必须确实存在于版本目录（无坏链）
        photos_html = (rel2 / "photos.html").read_text("utf-8")
        self.assertIn("assets/" + up["thumb_path"], photos_html,
                      "照片页应引用已入版的缩略图")

        # 撤稿照片 -> 说明/授权分别保留字段；原件与派生缩略图都进任务
        st, wd = s.post("/api/photos/1/withdraw", {"reason": "家属撤回肖像授权"})
        self.assertEqual(st, 200)
        task_id = wd["task_id"]
        _, tasks = s.get("/api/state")
        task = [t for t in tasks["tasks"] if t["id"] == task_id][0]
        self.assertIn(up["asset_path"], task["assets"])
        self.assertIn(up["thumb_path"], task["assets"], "派生缩略图必须纳入撤稿任务")

        # 发布 v3：照片不再出现，资源落盘为墓碑，任务关闭
        _, r3 = s.post("/api/publish", {"note": "v3 撤稿"})
        v3 = r3["release_id"]
        rel3 = s.data_path("releases", f"r{v3:04d}")
        tomb_orig = rel3 / ("assets/" + up["asset_path"])
        tomb_thumb = rel3 / ("assets/" + up["thumb_path"])
        self.assertTrue(tomb_orig.exists() and tomb_thumb.exists())
        self.assertEqual(tomb_orig.read_text().strip(), "RETRACTED")
        self.assertEqual(tomb_thumb.read_text().strip(), "RETRACTED")
        photos_html = (rel3 / "photos.html").read_text("utf-8")
        self.assertNotIn("harbor.png", photos_html, "撤稿照片不应再在图页出现")

        # 回滚到 v2 -> 离线恢复旧资源（真实图片回来，不是墓碑）
        st, rb = s.post(f"/api/releases/{v2}/rollback", {})
        self.assertEqual(st, 200)
        # current 链接指向 v2 目录，旧资源完整恢复
        cur = os.path.realpath(s.data_path("releases", "current"))
        self.assertEqual(os.path.basename(cur), f"r{v2:04d}")
        restored = Path(cur) / ("assets/" + up["thumb_path"])
        self.assertEqual(restored.read_bytes()[:8], b"\x89PNG\r\n\x1a\n",
                         "离线页恢复旧资源：缩略图从旧版完整恢复")

    # ---------- 失败发布保留上个完整版本，绝不发布半空页面 ----------
    def test_06_failed_publish_keeps_previous_release(self):
        import subprocess
        import time
        s = self.srv
        # 先用正常服务确认当前有完整版本（上一用例回滚后 current 存在）
        st, val = s.get("/api/validate")
        self.assertTrue(val["published"] and val["ok"])
        good_release = val["release_id"]

        # 起一个注入故障的实例（渲染完成后、校验前抛错）
        env_port = None
        import socket as _sock
        sk = _sock.socket(); sk.bind(("127.0.0.1", 0)); env_port = sk.getsockname()[1]; sk.close()
        env = dict(os.environ)
        env.update({"YUGANG_DATA": s.tmp, "YUGANG_PORT": str(env_port),
                    "YUGANG_FAULT_AFTER_FILES": "3"})
        proc = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve().parent.parent / "run.py")],
            cwd=Path(__file__).resolve().parent.parent, env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            base = f"http://127.0.0.1:{env_port}"
            import urllib.request
            for _ in range(60):
                try:
                    urllib.request.urlopen(base + "/api/editors", timeout=2); break
                except Exception:
                    time.sleep(0.15)
            req = urllib.request.Request(base + "/api/publish",
                                         data=json.dumps({"note": "注定失败"}).encode(),
                                         headers={"Content-Type": "application/json"},
                                         method="POST")
            import urllib.error
            try:
                urllib.request.urlopen(req, timeout=10)
                failed = False
            except urllib.error.HTTPError as e:
                failed = e.code == 409
            self.assertTrue(failed, "故障注入下发布必须失败并返回 409")
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()

        # 原服务的 current 未变、依旧完整
        st, val2 = s.get("/api/validate")
        self.assertTrue(val2["ok"], "旧版本必须仍完整可访问")
        self.assertEqual(val2["release_id"], good_release, "不得切换到半空版本")
        # 失败版本被标记 failed 且暂存目录被清理
        st, rels = s.get("/api/releases")
        failed_rows = [r for r in rels if r["state"] == "failed"]
        self.assertTrue(failed_rows, "应留下 failed 发布记录便于排查")
        for r in failed_rows:
            d = s.data_path("releases", f"r{r['id']:04d}")
            self.assertFalse(d.exists(), "失败暂存目录必须清理")

    # ---------- 时间轴不靠导入顺序；未定年代单独成组 ----------
    def test_07_timeline_order_uses_event_time_not_import_order(self):
        s = self.srv
        # 造一条已审校陈述：事件年代很早，但在最后才录入（导入顺序靠后）
        _, c = s.post("/api/statements", {
            "subject_table": "boats", "subject_id": 3,
            "title": "最早的事件但最晚录入", "body": "1901 年的旧事",
            "event_time": {"type": "point", "date": "1901"}})
        sid = c["id"]
        s.post(f"/api/statements/{sid}/reviews", {"action": "submit"})
        s.post(f"/api/statements/{sid}/reviews", {"action": "approve"}, editor=2)
        # 一条未定年代陈述，也审校通过
        _, u = s.post("/api/statements", {
            "subject_table": "boats", "subject_id": 3,
            "title": "年代不可考的习俗", "body": "?",
            "event_time": {"type": "undated", "note": "无人记得"}})
        uid = u["id"]
        s.post(f"/api/statements/{uid}/reviews", {"action": "submit"})
        s.post(f"/api/statements/{uid}/reviews", {"action": "approve"}, editor=2)

        _, tl = s.get("/api/timeline")
        dated_years = []
        for e in tl["dated"]:
            et = e["event_time"]
            dated_years.append(et["date"][:4] if et["type"] == "point" else et["start"][:4])
        self.assertEqual(dated_years, sorted(dated_years), "时间轴必须严格按事件年代升序")
        self.assertEqual(dated_years[0], "1901", "最晚录入的最早事件必须排在最前")
        self.assertIn("年代不可考的习俗", [e["title"] for e in tl["undated"]],
                      "未定年代不得混排到可排序序列")
        # 绝不使用 id 顺序：1901 陈述的 id 最大却在最前
        self.assertGreater(sid, 1)

    # ---------- 发布冻结 vs 动态查询：漂移被检测 ----------
    def test_08_freeze_vs_live_drift_detected(self):
        s = self.srv
        # 先发布一版
        s.post("/api/publish", {"note": "冻结基线"})
        _, before = s.get("/api/diff")
        self.assertFalse(before["drifted"], "刚发布后冻结图应与最新一致")
        # 改动态内容（新增已审校陈述）
        _, c = s.post("/api/statements", {
            "subject_table": "jargons", "subject_id": 1,
            "title": "新增释义", "body": "又一条",
            "event_time": {"type": "range", "start": "1950", "end": "1955"}})
        s.post(f"/api/statements/{c['id']}/reviews", {"action": "submit"})
        s.post(f"/api/statements/{c['id']}/reviews", {"action": "approve"}, editor=2)
        _, after = s.get("/api/diff")
        self.assertTrue(after["drifted"])
        self.assertIn("statements", after["sections"])

    # ---------- 说明与授权分开追踪 ----------
    def test_09_caption_and_license_tracked_separately(self):
        s = self.srv
        _, st0 = s.get("/api/state")
        # 找一张未撤稿照片，否则新建一张
        photo = next((p for p in st0["photos"] if not p["withdrawn"]), None)
        if photo is None:
            _, c = s.post("/api/photos", {"caption": "旧说明", "license": "保留授权"})
            pid = c["id"]
        else:
            pid = photo["id"]
        _, before = s.get("/api/state")
        pb = [p for p in before["photos"] if p["id"] == pid][0]
        old_license = pb["license"]
        s.post(f"/api/photos/{pid}/caption", {"caption": "只改了说明文字"})
        _, mid = s.get("/api/state")
        pm = [p for p in mid["photos"] if p["id"] == pid][0]
        self.assertEqual(pm["caption"], "只改了说明文字")
        self.assertEqual(pm["license"], old_license, "改说明不得改动授权")
        s.post(f"/api/photos/{pid}/license", {"license": "CC0", "license_ref": "新凭证"})
        _, after = s.get("/api/state")
        pa = [p for p in after["photos"] if p["id"] == pid][0]
        self.assertEqual(pa["license"], "CC0")
        self.assertEqual(pa["caption"], "只改了说明文字", "改授权不得改动说明")


if __name__ == "__main__":
    unittest.main(verbosity=2)
