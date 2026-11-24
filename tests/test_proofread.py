# -*- coding: utf-8 -*-
"""校对模块的测试。

校对逻辑的价值全在「拦掉模型的胡改」上，所以测试重点是各种异常输入
能否被正确拒绝 —— 这些都是实测踩到过的真实问题。
"""
import unittest

from transcribe_cli import proofread as pr


class TestParseChanges(unittest.TestCase):
    """解析模型输出的各种形态。"""

    def setUp(self):
        self.seg_ids = [1, 2, 3]
        self.originals = ["第一行内容", "第二行内容", "叫做影射"]

    def parse(self, raw):
        return pr.parse_changes(raw, self.seg_ids, self.originals)

    def test_normal_correction(self):
        """正常错别字修正。"""
        res = self.parse("3|影射|映射")
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0].original, "影射")
        self.assertEqual(res[0].corrected, "映射")
        self.assertEqual(res[0].full_corrected, "叫做映射")
        self.assertEqual(res[0].seg_id, 3)

    def test_placeholder_rejected(self):
        """模型输出「无错误」这类占位语要拒绝。"""
        self.assertEqual(len(self.parse("3|无错误|（无错误）")), 0)
        self.assertEqual(len(self.parse("3|影射|无错误")), 0)
        self.assertEqual(len(self.parse("3|内容多（无错误）|内容多")), 0)

    def test_letter_deletion_rejected(self):
        """纯删除且被删内容含字母/数字要拒绝（不能删数学符号）。

        实测踩到过：「D定义语」→「定义语」把符号 D 删掉了。
        """
        self.originals[2] = "D定义语"
        self.assertEqual(len(self.parse("3|D定义语|定义语")), 0)

    def test_case_only_change_rejected(self):
        """纯大小写差异不算改动。"""
        self.originals[2] = "我给一个X"
        self.assertEqual(len(self.parse("3|一个X|一个x")), 0)

    def test_punctuation_rejected(self):
        """错误词里带句末标点说明模型塞了整句进来。"""
        self.assertEqual(len(self.parse("3|影射。|映射")), 0)

    def test_length_gap_rejected(self):
        """长度差超过 2 字说明模型截断或补多了。"""
        self.originals[2] = "字变量"
        self.assertEqual(len(self.parse("3|字变量|字符变量")), 1)   # 差 1 字，留
        self.assertEqual(len(self.parse("3|字变量|字符变量的一个数")), 0)  # 差 5 字，拒

    def test_out_of_range_line_rejected(self):
        """行号越界要拒绝。"""
        self.assertEqual(len(self.parse("99|影射|映射")), 0)

    def test_nonexistent_word_rejected(self):
        """错误词不在原文里，说明模型编的。"""
        self.assertEqual(len(self.parse("3|不存在的词|映射")), 0)

    def test_ok_marker_ignored(self):
        """模型说全部没问题时不产出改动。"""
        self.assertEqual(len(self.parse("OK")), 0)
        self.assertEqual(len(self.parse("无需修改")), 0)

    def test_multiple_errors_same_line(self):
        """同一行多处错误合并成一条改动。"""
        self.originals[2] = "影射和单摄"
        res = self.parse("3|影射|映射\n3|单摄|单射")
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0].full_corrected, "映射和单射")

    def test_code_fence_ignored(self):
        """模型可能用 ``` 包裹。"""
        res = self.parse("```\n3|影射|映射\n```")
        self.assertEqual(len(res), 1)


class TestApplyCorrections(unittest.TestCase):
    """把校对结果应用到段落。"""

    def test_apply(self):
        class S:
            def __init__(self, i, t):
                self.id = i
                self.text = t

        segs = [S(1, "影射"), S(2, "正常")]
        changes = [
            pr.ProofreadChange(
                seg_id=1, index=0, original="影射", corrected="映射",
                full_original="影射", full_corrected="映射",
            )
        ]
        out = pr.apply_corrections(segs, changes)
        self.assertEqual(out[0].text, "映射")
        self.assertEqual(out[1].text, "正常")
        # 原对象不应被改
        self.assertEqual(segs[0].text, "影射")


class TestWriteCorrectedSrt(unittest.TestCase):
    """写出校对后的 SRT。"""

    def test_write(self):
        import tempfile
        from pathlib import Path

        class S:
            def __init__(self, i, start, end, t):
                self.id = i
                self.start = start
                self.end = end
                self.text = t

        segs = [S(1, 0.0, 1.5, "映射"), S(2, 2.0, 3.25, "第二段")]
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "out.srt"
            pr.write_corrected_srt(segs, p)
            content = p.read_text(encoding="utf-8")
            self.assertIn("00:00:00,000 --> 00:00:01,500", content)
            self.assertIn("映射", content)
            self.assertIn("第二段", content)


if __name__ == "__main__":
    unittest.main()
