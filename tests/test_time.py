# -*- coding: utf-8 -*-
import unittest

from app import timeutil as T


class TestTimeModel(unittest.TestCase):
    def test_exact_precision(self):
        for s, disp in [("1958", "1958年"),
                        ("1958-03", "1958年3月"),
                        ("1958-03-12", "1958年3月12日")]:
            tp = T.parse_tp(s)
            self.assertEqual(tp["kind"], "exact")
            self.assertIn(disp, T.display(tp))

    def test_range_and_undated(self):
        tp = T.parse_tp("1952..1958")
        self.assertEqual(tp["kind"], "range")
        self.assertIn("区间", T.display(tp))
        u = T.parse_tp("年代不详")
        self.assertEqual(u["kind"], "undated")
        self.assertIn("未定", T.display(u))
        self.assertTrue(T.is_undated(T.parse_tp("")))

    def test_bad_dates_rejected(self):
        for bad in ["1958-13", "1958-02-30", "abc", "1990..1980"]:
            with self.assertRaises(ValueError):
                T.parse_tp(bad)

    def test_undated_all_sink_no_import_order(self):
        u1 = T.make_tp("undated", label="甲")
        u2 = T.make_tp("undated", label="乙")
        # 两个未定年代排序键完全相同——次序不可由外部（如导入顺序）决定
        self.assertEqual(T.sort_key(u1), T.sort_key(u2))
        self.assertEqual(T.order_strength(u1, u2), "")

    def test_range_overlap_is_weak(self):
        a = T.parse_tp("1952..1955")
        b = T.parse_tp("1954..1960")
        self.assertLess(T.sort_key(a), T.sort_key(b))
        self.assertEqual(T.order_strength(a, b), "weak")  # 不能断言先后
        c = T.parse_tp("1956..1960")
        self.assertEqual(T.order_strength(a, c), "firm")

    def test_precision_order_not_fabricated(self):
        # 同为 1958 年、精度不同：物理区间重叠 -> weak
        y = T.parse_tp("1958")
        d = T.parse_tp("1958-06-01")
        self.assertEqual(T.order_strength(y, d), "weak")


if __name__ == "__main__":
    unittest.main()
