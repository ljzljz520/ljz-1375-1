"""不依赖网络服务的单元测试：时间模型、内容图漂移、撤稿资产。"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import content, timeutil  # noqa: E402


class TimeModelTest(unittest.TestCase):
    def test_point_precision_preserved(self):
        self.assertEqual(timeutil.normalize_event_time(
            {"type": "point", "date": "1962-05"})["precision"], "month")
        self.assertEqual(timeutil.display(
            {"type": "point", "date": "1962-05", "precision": "month"}), "1962年5月")

    def test_range_precision(self):
        rt = timeutil.normalize_event_time({"type": "range", "start": "1958", "end": "1962-05"})
        self.assertEqual(rt["start"], "1958")
        self.assertEqual(rt["end"], "1962-05")
        self.assertIn("至", timeutil.display(rt))

    def test_undated_not_sortable(self):
        u = timeutil.normalize_event_time({"type": "undated", "note": "x"})
        self.assertIsNone(timeutil.sort_key(u))
        self.assertEqual(timeutil.certainty_label(u), "未定")

    def test_range_missing_lowerbound_is_undated(self):
        # 只有上界、没有下界的区间不能可靠排入时间轴前段
        rt = timeutil.normalize_event_time({"type": "range", "end": "1900"})
        self.assertIsNone(timeutil.sort_key(rt))

    def test_buckets_ignore_import_order(self):
        # id 顺序故意颠倒：1901 在最后给，排序后必须最前
        evs = [
            {"id": 10, "title": "1971 重建", "event_time": {"type": "point", "date": "1971"}},
            {"id": 11, "title": "1901 最早", "event_time": {"type": "point", "date": "1901"}},
            {"id": 12, "title": "不可考", "event_time": {"type": "undated"}},
        ]
        b = timeutil.buckets(evs)
        self.assertEqual([e["title"] for e in b["dated"]], ["1901 最早", "1971 重建"])
        self.assertEqual([e["title"] for e in b["undated"]], ["不可考"])

    def test_ties_use_title_not_id(self):
        # 同年代并列：次级键是标题而非 id（id 大的标题码位更靠前时应排前）
        evs = [
            {"id": 99, "title": "A", "event_time": {"type": "point", "date": "1962"}},
            {"id": 1, "title": "B", "event_time": {"type": "point", "date": "1962"}},
        ]
        b = timeutil.buckets(evs)
        self.assertEqual([e["title"] for e in b["dated"]], ["A", "B"])

    def test_invalid_range_rejected(self):
        with self.assertRaises(ValueError):
            timeutil.normalize_event_time({"type": "range", "start": "2000", "end": "1900"})


class DriftTest(unittest.TestCase):
    """用真实内存 SQLite 构造冻结图，再改动动态数据，验证漂移检测。"""
    def _conn(self):
        import sqlite3
        from app.db import SCHEMA
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA)
        now = timeutil.now_iso()
        conn.execute(
            "INSERT INTO editors(id,name,role) VALUES (1,'e','editor')")
        conn.execute(
            "INSERT INTO statements(id,subject_table,subject_id,title,body,event_time,"
            "collected_at,source_type,source_ref,status,created_by,created_at,updated_at)"
            " VALUES (1,'boats',1,'a','x',?,?,'oral','r','approved',1,?,?)",
            (json.dumps({"type": "point", "date": "1962"}), now, now, now))
        conn.commit()
        return conn

    def test_no_drift_when_identical(self):
        conn = self._conn()
        frozen = content.freeze(conn)
        live = content.build_live_graph(conn)
        self.assertFalse(content.diff_graphs(frozen, live)["drifted"])

    def test_detect_modified_statement(self):
        conn = self._conn()
        frozen = content.freeze(conn)
        conn.execute("UPDATE statements SET body='被改动' WHERE id=1")
        conn.commit()
        live = content.build_live_graph(conn)
        d = content.diff_graphs(frozen, live)
        self.assertTrue(d["drifted"])
        self.assertIn(1, d["sections"]["statements"]["modified"])

    def test_hash_stable_across_freeze_timestamp(self):
        conn = self._conn()
        g1 = content.freeze(conn)
        g2 = content.freeze(conn)
        # 冻结时刻不同，但实质内容哈希一致
        self.assertEqual(content.content_hash(g1), content.content_hash(g2))


class AssetTest(unittest.TestCase):
    def test_thumbnail_and_tombstone(self):
        tmp = tempfile.mkdtemp()
        os.environ["YUGANG_DATA"] = tmp
        import importlib
        from app import config, assets
        importlib.reload(config)
        importlib.reload(assets)
        src = assets.save_raw("photos", 7, "a.bin", b"PHOTO-BYTES")
        self.assertFalse(src.startswith("assets/"), "逻辑路径不含 assets/ 前缀")
        self.assertTrue(assets.abs_path(src).exists())
        thumb = assets.derive_thumbnail(src, 7)
        data = (config.ASSET_DIR / thumb).read_bytes()
        self.assertTrue(data.startswith(b"\x89PNG"))
        assets.write_tombstone(config.ASSET_DIR / thumb)
        self.assertEqual((config.ASSET_DIR / thumb).read_text().strip(), "RETRACTED")


if __name__ == "__main__":
    unittest.main(verbosity=2)
