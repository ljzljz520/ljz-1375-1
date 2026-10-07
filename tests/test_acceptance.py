# -*- coding: utf-8 -*-
"""端到端验收测试：覆盖五条验收剧情 + 公开时间/撤稿/保留旧版。"""
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.request
import urllib.error

from app import db as dbmod
from app.server import serve


class HttpBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.dbp = os.path.join(self.tmp, "hall.db")
        self.root = os.path.join(self.tmp, "site")
        self.src = os.path.join(self.tmp, "sources")
        os.makedirs(self.src)
        for f, data in [("p1.jpg", b"JPEG-A"), ("p2.jpg", b"JPEG-B"),
                        ("a.wav", b"WAV"), ("p3.jpg", b"JPEG-C")]:
            with open(os.path.join(self.src, f), "wb") as fh:
                fh.write(data)
        dbmod.init_db(self.dbp)
        self.httpd = serve(self.dbp, self.root, port=0)
        self.port = self.httpd.server_address[1]
        self.th = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.th.start()
        time.sleep(0.05)

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def api(self, path, body=None, user=1, method="POST"):
        data = json.dumps(body or {}).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}", data=data
            if method == "POST" else None, method=method,
            headers={"Content-Type": "application/json",
                     "X-User-Id": str(user)})
        try:
            with urllib.request.urlopen(req) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as ex:
            payload = json.loads(ex.read())
            raise AssertionError(f"{path} -> {ex.code}: {payload}")

    def pub(self, rel="/pub/"):
        with urllib.request.urlopen(
                f"http://127.0.0.1:{self.port}{rel}") as r:
            return r.read()

    def seed_locations(self):
        l1 = self.api("/api/locations", {"name": "东码头", "lat": 30.1,
                                         "lng": 122.1})["result"]
        l2 = self.api("/api/locations", {"name": "西港湾", "lat": 30.2,
                                         "lng": 122.2})["result"]
        return l1, l2

    def publish_photo(self, path, caption, locs, user=1, license_info="家属授权"):
        aid = self.api("/api/assets", {"kind": "photo", "path": path,
                                       "mime": "image/jpeg"}, user)["result"]
        pid = self.api("/api/photos", {"asset_id": aid, "caption": caption,
                                       "license": license_info,
                                       "occurred_at": "1963",
                                       "collected_at": "2018",
                                       "location_ids": locs}, user)["result"]
        self.api(f"/api/photos/{pid}/caption",
                 {"caption": caption, "status": "approved"}, user)
        self.api(f"/api/photos/{pid}/license",
                 {"license": license_info, "status": "granted"}, user)
        self.api(f"/api/photos/{pid}/status", {"status": "approved"}, user)
        return pid, aid


class A_SamePhotoMultipleLocations(HttpBase):
    def test_photo_referenced_by_two_places(self):
        l1, l2 = self.seed_locations()
        pid, aid = self.publish_photo(os.path.join(self.src, "p1.jpg"),
                                      "同一张码头照", [l1, l2])
        v = self.api("/api/publish", {"note": "v1"})["result"]
        page = self.pub("/pub/map.html").decode()
        geo = json.loads(self.pub("/pub/map.geojson"))
        # 两个地点都引用同一张照片
        refs = {f["properties"]["name"]: f["properties"]["photo_ids"]
                for f in geo["features"]}
        self.assertEqual(refs["东码头"], [pid])
        self.assertEqual(refs["西港湾"], [pid])
        self.assertIn(str(pid), page)
        # 快照里照片 location_ids 同时含两地
        snap = json.loads(self.pub("/pub/snapshot.json"))
        p = next(x for x in snap["photos"] if x["id"] == pid)
        self.assertEqual(sorted(p["location_ids"]), [l1, l2])


class B_IntervieweeWithdrawsSegment(HttpBase):
    def test_segment_withdrawn_then_reinstated(self):
        audio = self.api("/api/assets",
                         {"kind": "audio", "path": os.path.join(self.src,
                                                                 "a.wav")},
                         1)["result"]
        iid = self.api("/api/interviews", {"interviewee": "王阿婆",
                                           "collected_at": "2020-07",
                                           "audio_asset": audio})["result"]
        g1 = self.api(f"/api/interviews/{iid}/segments",
                      {"text": "可以公开的一段", "tcin": "00:00",
                       "tcout": "00:10"})["result"]
        g2 = self.api(f"/api/interviews/{iid}/segments",
                      {"text": "后来要求撤回的一段",
                       "tcin": "00:10", "tcout": "00:22"})["result"]
        self.api(f"/api/interviews/{iid}/consent", {"consent": "granted"})
        self.api(f"/api/interviews/{iid}/status", {"status": "approved"})
        self.api("/api/publish", {"note": "含两段"})
        before = self.pub("/pub/transcripts.json").decode()
        self.assertIn("后来要求撤回的一段", before)
        # 受访者撤回一段
        self.api(f"/api/segments/{g2}/withdraw", {}, user=2)
        self.api("/api/publish", {"note": "撤段后"})
        after = self.pub("/pub/transcripts.json").decode()
        self.assertIn("可以公开的一段", after)
        self.assertNotIn("后来要求撤回的一段", after)
        # 撤回段落在页面中也不可见
        self.assertNotIn("后来要求撤回的一段",
                         self.pub("/pub/interviews.html").decode())


class C_TwoEditorsMergeShips(HttpBase):
    def test_merge_undo_restores(self):
        a = self.api("/api/ships", {"name": "海丰"})["result"]
        b = self.api("/api/ships", {"name": "海丰号"}, user=2)["result"]
        # 同名之外的一对，也允许；核心是不能自动合并
        cid = self.api("/api/candidates",
                       {"ship_a": a, "ship_b": b, "note": "疑为同一船"})["result"]
        self.api(f"/api/candidates/{cid}/evidence",
                 {"side": "same", "content": "船板钢印一致"}, user=2)
        # editor1 不能自己合并
        with self.assertRaises(AssertionError):
            self.api(f"/api/candidates/{cid}/merge",
                     {"keep_ship_id": a}, user=1)
        mid = self.api(f"/api/candidates/{cid}/merge",
                       {"keep_ship_id": a}, user=2)["result"]
        st = self.api("/api/state", method="GET")
        shipb = next(s for s in st["ships"] if s["id"] == b)
        self.assertTrue(shipb["superseded"])
        # 撤销合并恢复引用
        self.api(f"/api/merges/{mid}/undo", {}, user=1)
        st = self.api("/api/state", method="GET")
        shipb = next(s for s in st["ships"] if s["id"] == b)
        self.assertFalse(shipb["superseded"])
        self.assertIsNone(shipb["merged_into"])

    def test_same_names_stay_separate(self):
        a = self.api("/api/ships", {"name": "同名船"})["result"]
        b = self.api("/api/ships", {"name": "同名船"}, user=2)["result"]
        self.assertNotEqual(a, b)
        st = self.api("/api/state", method="GET")
        self.assertEqual(len([s for s in st["ships"]
                              if s["current_name"] == "同名船"]), 2)
        self.assertEqual(len(st["candidates"]), 0)


class D_MissingAudioTimecode(HttpBase):
    def test_missing_timecode_marked_not_invented(self):
        audio = self.api("/api/assets",
                         {"kind": "audio",
                          "path": os.path.join(self.src, "a.wav")})["result"]
        iid = self.api("/api/interviews", {"interviewee": "李大爷",
                                           "collected_at": "2019",
                                           "audio_asset": audio})["result"]
        self.api(f"/api/interviews/{iid}/segments",
                 {"text": "带时码段", "tcin": "00:05", "tcout": "00:09"})
        self.api(f"/api/interviews/{iid}/segments",
                 {"text": "老磁带无字的一段"})  # 时码缺失
        self.api(f"/api/interviews/{iid}/consent", {"consent": "granted"})
        self.api(f"/api/interviews/{iid}/status", {"status": "approved"})
        self.api("/api/publish", {"note": "时码缺失"})
        td = json.loads(self.pub("/pub/transcripts.json"))
        segs = td[str(iid)]
        self.assertFalse(segs[0]["missing_timecode"])
        self.assertTrue(segs[1]["missing_timecode"])
        page = self.pub("/pub/interviews.html").decode()
        self.assertIn("音频时码缺失", page)
        # 无脚本目录同样如实标注
        self.assertIn("时码缺失", self.pub("/pub/noscript/").decode())


class E_OfflinePageAndOldResources(HttpBase):
    def test_failed_publish_keeps_old_version(self):
        l1, _ = self.seed_locations()
        self.publish_photo(os.path.join(self.src, "p1.jpg"), "初版照片", [l1])
        v1 = self.api("/api/publish", {"note": "第一版"})["result"]
        old_asset = self.pub("/pub/map.geojson")  # 旧版可访问
        # 再发布时注入故障：必须保留 v1 而不是出现半空页面
        with self.assertRaises(AssertionError):
            self.api("/api/publish", {"note": "坏版本",
                                      "fail_after_render": True})
        pubs = self.api("/api/pubs", method="GET")
        self.assertEqual(pubs["current"], v1)  # 指针未动
        self.assertEqual(self.pub("/pub/").decode().count("第一版"), 1)
        # 不存在 tmp 残留
        self.assertFalse(os.path.exists(
            os.path.join(self.root, "versions", ".tmp-publish")))

    def test_rollback_restores_old_offline_assets(self):
        l1, _ = self.seed_locations()
        pid1, aid1 = self.publish_photo(
            os.path.join(self.src, "p1.jpg"), "旧版独有照片", [l1])
        v1 = self.api("/api/publish", {"note": "旧版"})["result"]
        off1 = self.pub("/pub/offline.html").decode()
        # 记录 v1 的资源路径
        snap1 = json.loads(self.pub("/pub/snapshot.json"))
        old_rel = f"assets/photo_{aid1}.jpg"
        self.assertTrue(os.path.isfile(
            os.path.join(self.root, "versions", f"v{v1}", old_rel)))
        # 撤稿旧照片 -> 新版本不再含该资源
        tk = self.api("/api/takedowns",
                      {"reason": "权利人撤稿",
                       "targets": [{"type": "photo", "id": pid1}]})["result"]
        self.api(f"/api/takedowns/{tk}/execute", {}, user=2)
        pid2, aid2 = self.publish_photo(
            os.path.join(self.src, "p2.jpg"), "新版照片", [l1])
        v2 = self.api("/api/publish", {"note": "新版", "force": True})["result"]
        self.assertNotIn(old_rel, self.pub("/pub/offline.html").decode())
        # 管理员恢复旧版本 -> 离线页重新引用旧资源且资源仍可读
        self.api("/api/publish/activate",
                 {"publication_id": v1}, user=3)
        self.assertIn(old_rel, self.pub("/pub/offline.html").decode())
        self.assertEqual(self.pub(f"/pub/{old_rel}"), b"JPEG-A")


class F_LicenseCaptionAndUncertainty(HttpBase):
    def test_caption_and_license_tracked_separately(self):
        l1, _ = self.seed_locations()
        pid, aid = self.publish_photo(
            os.path.join(self.src, "p3.jpg"), "暂定说明", [l1])
        # 授权撤回：说明仍在，但照片不能公开
        self.api(f"/api/photos/{pid}/license",
                 {"license": "家属授权", "status": "revoked"}, user=2)
        self.api("/api/publish", {"note": "授权撤回"})
        snap = json.loads(self.pub("/pub/snapshot.json"))
        self.assertFalse(any(p["id"] == pid for p in snap["photos"]))
        st = self.api("/api/state", method="GET")
        p = next(x for x in st["photos"] if x["id"] == pid)
        self.assertEqual(p["license_status"], "revoked")
        self.assertEqual(p["caption_status"], "approved")  # 说明不受影响

    def test_takedown_includes_thumbnail_and_uncertainty_shown(self):
        l1, _ = self.seed_locations()
        pid, aid = self.publish_photo(
            os.path.join(self.src, "p1.jpg"), "年代存疑照片", [l1])
        self.api("/api/publish", {"note": "有缩略图"})
        snap = json.loads(self.pub("/pub/snapshot.json"))
        deriv = snap["photos"][0]["derivatives"]
        self.assertEqual(len(deriv), 1)  # 发布时派生缩略图
        tk = self.api("/api/takedowns",
                      {"reason": "撤稿含缩略图",
                       "targets": [{"type": "photo", "id": pid}]})["result"]
        self.api(f"/api/takedowns/{tk}/execute", {}, user=2)
        self.api("/api/publish", {"note": "撤后", "force": True})
        snap2 = json.loads(self.pub("/pub/snapshot.json"))
        self.assertFalse(any(p["id"] == pid for p in snap2["photos"]))

        # 时间轴不确定性公开可见：区间 + 未定
        sid = self.api("/api/statements",
                       {"topic": "区间事件", "content": "约五十年代",
                        "occurred_at": "1952..1958",
                        "source": "方志"})["result"]
        self.api(f"/api/statements/{sid}/submit")
        self.api(f"/api/statements/{sid}/review", {"approve": True}, user=2)
        self.api("/api/publish", {"note": "区间", "force": True})
        html = self.pub("/pub/timeline.html").decode()
        self.assertIn("年代区间", html)

    def test_publication_time_stamped_once(self):
        sid = self.api("/api/statements",
                       {"topic": "公开时间", "content": "x",
                        "occurred_at": "1949", "collected_at": "2020-01",
                        "source": "档案"})["result"]
        self.api(f"/api/statements/{sid}/submit")
        self.api(f"/api/statements/{sid}/review", {"approve": True}, user=2)
        self.api("/api/publish", {"note": "首版"})
        st = self.api("/api/state", method="GET")
        first = next(s for s in st["statements"] if s["id"] == sid)
        self.assertIsNotNone(first["published_at"])
        published = first["published_at"]
        self.api("/api/publish", {"note": "再版"})
        st = self.api("/api/state", method="GET")
        second = next(s for s in st["statements"] if s["id"] == sid)
        self.assertEqual(second["published_at"], published)  # 不被改写


if __name__ == "__main__":
    unittest.main()
