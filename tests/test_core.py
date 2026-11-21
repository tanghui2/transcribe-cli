# -*- coding: utf-8 -*-
"""核心逻辑单元测试（标准库 unittest，零额外依赖）。

不跑真实转写（需要 3GB 模型 + GPU），只验证纯逻辑：
时间戳转换、字幕导出、降级行为、参数解析。

运行：python -m unittest discover tests -v
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

# 保证从项目根目录导入 transcribe_cli
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from transcribe_cli import core, media          # noqa: E402
from transcribe_cli.__main__ import build_parser  # noqa: E402


def sample_transcript() -> core.Transcript:
    """构造一个三段的样例转写结果。"""
    segs = [
        core.Segment(id=1, start=0.0, end=2.5, text="第一句"),
        core.Segment(id=2, start=2.5, end=5.0, text="第二句"),
        core.Segment(id=3, start=5.0, end=7.25, text="第三句"),
    ]
    return core.Transcript(
        segments=segs, language="zh", duration=7.25, model="test", elapsed=1.0
    )


class TestTimestamp(unittest.TestCase):
    def test_常见时间戳(self):
        cases = [
            (0, "00:00:00,000"),
            (1.5, "00:00:01,500"),
            (61.25, "00:01:01,250"),
            (3661.001, "01:01:01,001"),
            (359999.999, "99:59:59,999"),
        ]
        for seconds, expected in cases:
            with self.subTest(seconds=seconds):
                self.assertEqual(core._srt_timestamp(seconds), expected)

    def test_负数被夹到零(self):
        """负数时间戳应夹到 0，不能产生非法字符串。"""
        self.assertEqual(core._srt_timestamp(-5.0), "00:00:00,000")


class TestTranscript(unittest.TestCase):
    def test_文本拼接(self):
        self.assertEqual(sample_transcript().text, "第一句第二句第三句")

    def test_段时长(self):
        self.assertAlmostEqual(sample_transcript().segments[2].duration, 2.25)

    def test_to_dict_可序列化(self):
        d = sample_transcript().to_dict()
        self.assertEqual(d["language"], "zh")
        self.assertEqual(len(d["segments"]), 3)
        self.assertEqual(d["segments"][0]["end"], 2.5)
        json.dumps(d, ensure_ascii=False)


class TestSubtitleExport(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_srt_格式(self):
        out = core.write_srt(sample_transcript(), self.tmp / "a.srt")
        text = out.read_text(encoding="utf-8")
        self.assertIn("00:00:00,000 --> 00:00:02,500", text)
        self.assertIn("第一句", text)
        self.assertTrue(text.startswith("1\n"))

    def test_vtt_用点分隔毫秒(self):
        """VTT 用点、SRT 用逗号。写错播放器就不认。"""
        out = core.write_vtt(sample_transcript(), self.tmp / "a.vtt")
        text = out.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("WEBVTT"))
        self.assertIn("00:00:00.000 --> 00:00:02.500", text)

    def test_双语字幕_对齐(self):
        tr = sample_transcript()
        lines = ["First sentence", "Second sentence", "Third sentence"]
        out = core.write_translated_srt(tr, lines, self.tmp / "b.srt")
        text = out.read_text(encoding="utf-8")
        self.assertIn("第一句", text)
        self.assertIn("First sentence", text)
        self.assertIn("00:00:02,500 --> 00:00:05,000", text)
        for t in lines:
            self.assertIn(t, text)

    def test_双语字幕_译文缺失不崩(self):
        tr = sample_transcript()
        out = core.write_translated_srt(tr, ["only one"], self.tmp / "c.srt")
        text = out.read_text(encoding="utf-8")
        self.assertIn("第一句", text)
        self.assertIn("only one", text)


class TestDegradation(unittest.TestCase):
    """可选依赖不可用时必须优雅降级，不能让整条链路崩掉。"""

    UNREACHABLE = "http://127.0.0.1:59999"

    def test_ollama_不可用返回None(self):
        self.assertIsNone(core.ollama_generate("测试", base_url=self.UNREACHABLE))

    def test_ollama_可用性检测(self):
        self.assertFalse(core.ollama_available(self.UNREACHABLE))

    def test_空转写_摘要返回None(self):
        tr = core.Transcript(segments=[], language="zh", duration=0, model="t", elapsed=0)
        self.assertIsNone(core.summarize(tr))

    def test_空转写_翻译返回None(self):
        tr = core.Transcript(segments=[], language="zh", duration=0, model="t", elapsed=0)
        self.assertIsNone(core.translate(tr))


class TestParser(unittest.TestCase):
    def test_默认值(self):
        a = build_parser().parse_args(["x.mp4"])
        self.assertEqual(a.media, "x.mp4")
        self.assertEqual(a.device, "cuda")
        self.assertFalse(a.summarize)
        self.assertFalse(a.bilingual_srt)

    def test_完整参数(self):
        a = build_parser().parse_args([
            "x.mp4", "--language", "zh", "--summarize",
            "--translate", "English", "--bilingual-srt", "--words", "-o", "./out",
        ])
        self.assertEqual(a.language, "zh")
        self.assertTrue(a.summarize)
        self.assertEqual(a.translate, "English")
        self.assertTrue(a.bilingual_srt)
        self.assertTrue(a.words)
        self.assertEqual(a.out_dir, "./out")

    def test_非法设备被拒(self):
        with self.assertRaises(SystemExit):
            build_parser().parse_args(["x.mp4", "--device", "tpu"])


class TestMedia(unittest.TestCase):
    def test_文件不存在抛错(self):
        with self.assertRaises(FileNotFoundError):
            media.probe("不存在的文件.mp4")

    def test_无ffmpeg时返回None(self):
        """没有 ffmpeg 应返回 None 让调用方回退 PyAV，而不是抛异常。"""
        original = media.has_ffmpeg
        media.has_ffmpeg = lambda: False
        try:
            with tempfile.TemporaryDirectory() as d:
                self.assertIsNone(media.extract_wav(Path(d) / "x.mp4"))
        finally:
            media.has_ffmpeg = original


if __name__ == "__main__":
    unittest.main(verbosity=2)
