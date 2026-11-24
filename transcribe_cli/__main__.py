# -*- coding: utf-8 -*-
"""命令行入口。

用法示例：
    python -m transcribe_cli 录播.mp4
    python -m transcribe_cli 录播.mp4 --summarize --out-dir ./out
    python -m transcribe_cli 录播.mp4 --translate English
    python -m transcribe_cli --check
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import cuda_setup


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="transcribe-cli",
        description="本地音视频转写 / 摘要 / 翻译（faster-whisper + Ollama，全程离线）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("media", nargs="?", help="音视频文件路径")
    p.add_argument("-m", "--model-dir", default=r"E:/whisper_model", help="faster-whisper 模型目录")
    p.add_argument("--device", default="cuda", choices=["cuda", "cpu"], help="推理设备")
    p.add_argument("--compute-type", default=None, help="计算精度（cuda 默认 float16，cpu 默认 int8）")
    p.add_argument("--language", default=None, help="语种，如 zh / en；默认自动检测")
    p.add_argument("--beam-size", type=int, default=5, help="beam search 宽度")
    p.add_argument("--no-vad", action="store_true", help="关闭 VAD 静音过滤")
    p.add_argument("--words", action="store_true", help="输出词级时间戳")
    p.add_argument("--prompt", default=None, help="初始提示词（引导专有名词识别）")
    p.add_argument("--summarize", action="store_true", help="生成中文纪要")
    p.add_argument("--translate", default=None, metavar="LANG", help="翻译到指定语言，如 English")
    p.add_argument(
        "--bilingual-srt",
        action="store_true",
        help="翻译逐段进行并写回 SRT，生成时间轴对齐的双语字幕（更慢但可直接用于字幕）",
    )
    p.add_argument("-o", "--out-dir", default="./out", help="输出目录")
    p.add_argument("--stem", default=None, help="输出文件名主干，默认取输入文件名")
    p.add_argument("--check", action="store_true", help="只做环境自检并退出")
    p.add_argument(
        "--events",
        action="store_true",
        help="以 NDJSON 向 stdout 输出结构化进度事件（供 GUI 消费），普通输出仍走 stderr",
    )
    p.add_argument("-v", "--verbose", action="store_true", help="打印调试信息")
    return p


def _emit(payload: dict) -> None:
    """输出一个 NDJSON 事件行。

    只有 --events 开启时生效；事件走 stdout，普通文本走 stdout 会被 GUI
    当作普通日志处理，两者在 GUI 侧靠 __event 字段区分。
    """
    payload = {"__event": True, **payload}
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main(argv: list[str] | None = None) -> int:
    # Windows 控制台默认 GBK，打包成 exe 后中文输出会乱码。
    # 这里强制 UTF-8，GUI 端按 UTF-8 解码即可。
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            # 已被重定向或不支持 reconfigure 时忽略
            pass

    args = build_parser().parse_args(argv)

    # 必须在 import faster_whisper 之前注入 CUDA DLL 路径
    cuda_setup.setup_cuda(verbose=args.verbose)

    if args.check:
        from . import core

        info = cuda_setup.report_runtime()
        print("=== 运行时 ===")
        for k, v in info.items():
            print(f"  {k}: {v}")
        print(f"  ollama 可用: {core.ollama_available()}")
        try:
            import faster_whisper

            print(f"  faster_whisper: {faster_whisper.__version__}")
        except Exception as e:
            print(f"  faster_whisper: 导入失败 {e}")
        return 0

    if not args.media:
        print("错误：需要指定音视频文件（--check 仅做自检）", file=sys.stderr)
        return 2

    media_path = Path(args.media)
    if not media_path.exists():
        print(f"错误：文件不存在 {media_path}", file=sys.stderr)
        return 2

    compute_type = args.compute_type or ("float16" if args.device == "cuda" else "int8")

    # 打印媒体信息
    from . import media as media_mod

    info = media_mod.probe(media_path)
    print(f"输入：{media_path.name}")
    if info.duration:
        print(f"时长：{info.duration:.1f} 秒（{info.duration / 60:.1f} 分钟）")
    print(f"大小：{info.size_mb:.1f} MB")
    print(f"设备：{args.device} / {compute_type}")
    print("-" * 50)

    from . import core

    # 转写
    seg_buffer: list[dict] = []

    def on_progress(seg, info):
        pct = (seg.end / info.duration * 100) if info.duration else 0
        # 终端里逐行刷新进度；事件模式下由 GUI 自己渲染，这里不再重复打印
        if not args.events:
            print(f"\r  [{pct:5.1f}%] {seg.text.strip()[:60]}", end="", flush=True)
        # 事件模式：只推送新增的那一段，避免 O(n²) 的数据传输
        if args.events:
            seg_buffer.append({
                "start": round(seg.start, 3),
                "end": round(seg.end, 3),
                "text": seg.text,
            })
            speed = info.duration / max(info.elapsed, 0.01) if info.duration else 0
            _emit({
                "event": "segments",
                "pct": round(pct, 2),
                "speed": round(speed, 2),
                "new": seg_buffer[-1],
                "count": len(seg_buffer),
            })

    print("转写中…")
    if args.events:
        _emit({"event": "stage", "stage": "decode", "state": "done", "detail": "16kHz 单声道"})
        _emit({"event": "stage", "stage": "asr", "state": "now", "detail": f"{args.device} · {compute_type}"})
    try:
        transcript = core.transcribe(
            media_path,
            model_dir=args.model_dir,
            language=args.language,
            device=args.device,
            compute_type=compute_type,
            beam_size=args.beam_size,
            vad_filter=not args.no_vad,
            word_timestamps=args.words,
            initial_prompt=args.prompt,
            on_progress=on_progress,
        )
    except Exception as e:
        # CUDA 显存不足是最常见的失败原因：另一个转写任务占着显存时，
        # 新进程会在模型加载阶段直接崩掉，且异常信息往往被吞掉。
        msg = str(e).lower()
        if "out of memory" in msg or "cuda" in msg and "memory" in msg:
            print(
                "\n转写失败：GPU 显存不足。\n"
                "  可能原因：另一个转写任务正在占用显存。\n"
                "  解决办法：\n"
                "    1) 等其他任务跑完再试\n"
                "    2) 改用 CPU：--device cpu\n"
                "    3) 换更小的模型目录（-m 指向 base/small 级别的模型）",
                file=sys.stderr,
            )
            return 1
        print(f"\n转写失败：{e}", file=sys.stderr)
        if args.verbose:
            import traceback

            traceback.print_exc()
        return 1

    print("\r" + " " * 70, end="")
    print(f"转写完成：{len(transcript.segments)} 段 / {transcript.elapsed:.1f} 秒"
          f"（{transcript.duration / max(transcript.elapsed, 0.01):.1f}x 实时）")

    if args.events:
        _emit({"event": "stage", "stage": "asr", "state": "done", "detail": f"{len(transcript.segments)} 段"})

    # 输出
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = args.stem or media_path.stem

    srt_path = core.write_srt(transcript, out_dir / f"{stem}.srt")
    vtt_path = core.write_vtt(transcript, out_dir / f"{stem}.vtt")
    txt_path = out_dir / f"{stem}.txt"
    txt_path.write_text(transcript.text, encoding="utf-8")
    json_path = out_dir / f"{stem}.json"
    json_path.write_text(
        json.dumps(transcript.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"输出：\n  {srt_path}\n  {vtt_path}\n  {txt_path}\n  {json_path}")

    # 摘要
    summary_path = None
    if args.summarize:
        print("-" * 50)
        print("生成纪要（本地 Ollama）…")
        if args.events:
            _emit({"event": "stage", "stage": "summary", "state": "now", "detail": "qwen3:8b · 本地"})
        summary = core.summarize(transcript)
        if summary:
            summary_path = out_dir / f"{stem}.summary.md"
            summary_path.write_text(summary, encoding="utf-8")
            print(f"  {summary_path}")
            print()
            print(summary)
            if args.events:
                _emit({"event": "stage", "stage": "summary", "state": "done", "detail": "已生成"})
                _emit({"event": "summary", "data": parse_summary(summary)})
        else:
            print("  跳过：Ollama 不可用或转写内容为空")

    # 翻译
    if args.translate:
        print("-" * 50)
        print(f"翻译到 {args.translate}（本地 Ollama）…")
        if args.events:
            _emit({"event": "stage", "stage": "trans", "state": "now", "detail": f"→ {args.translate}"})
        if args.bilingual_srt:
            # 逐段翻译并写回 SRT，保持时间轴对齐
            lines = core.translate_segments(transcript, target_lang=args.translate)
            if lines:
                tr_path = core.write_translated_srt(
                    transcript, lines, out_dir / f"{stem}.{args.translate}.srt"
                )
                print(f"  {tr_path}（双语字幕，时间轴对齐）")
                if args.events:
                    _emit({"event": "stage", "stage": "trans", "state": "done", "detail": "已对齐"})
                    _emit({"event": "bilingual", "data": build_bilingual(transcript, lines)})
            else:
                print("  跳过：Ollama 不可用或转写内容为空")
        else:
            translated = core.translate(transcript, target_lang=args.translate)
            if translated:
                tr_path = out_dir / f"{stem}.{args.translate}.md"
                tr_path.write_text(translated, encoding="utf-8")
                print(f"  {tr_path}")
            else:
                print("  跳过：Ollama 不可用或转写内容为空")

    # 导出完成
    if args.events:
        _emit({"event": "stage", "stage": "export", "state": "now", "detail": "写入文件"})
        produced = []
        for p in (srt_path, vtt_path, txt_path, json_path):
            try:
                produced.append({
                    "name": p.name,
                    "path": str(p),
                    "size": p.stat().st_size,
                })
            except OSError:
                pass
        # 纪要文件也一并报上去，GUI 直接照单读取，不用自己猜文件名
        if summary_path is not None and summary_path.exists():
            try:
                produced.append({
                    "name": summary_path.name,
                    "path": str(summary_path),
                    "size": summary_path.stat().st_size,
                })
            except OSError:
                pass
        _emit({"event": "stage", "stage": "export", "state": "done", "detail": f"{len(produced)} 个文件"})
        _emit({"event": "exports", "data": produced, "stem": stem, "outDir": str(out_dir)})

    return 0


def parse_summary(raw: str) -> dict:
    """把纪要 markdown 解析成结构化数据，供 GUI 渲染。

    格式：【概要】/【要点】/【待办】三段。
    """
    result = {"overview": "", "points": [], "todos": ""}
    current = None
    for line in raw.splitlines():
        text = line.strip()
        if text.startswith("【概要】"):
            current = "overview"
            continue
        if text.startswith("【要点】"):
            current = "points"
            continue
        if text.startswith("【待办】"):
            current = "todos"
            continue
        if not text:
            continue
        if current == "overview":
            result["overview"] += text
        elif current == "points":
            # 去掉 "1. " 这类序号前缀
            cleaned = text.split(". ", 1)[-1] if text[:2].strip().rstrip(".").isdigit() else text
            result["points"].append(cleaned)
        elif current == "todos":
            result["todos"] += text
    result["overview"] = result["overview"].strip()
    result["todos"] = result["todos"].strip()
    return result


def build_bilingual(transcript, lines: list[str]) -> list[dict]:
    """把段落与逐段译文合成双语对照数据。"""
    out = []
    for i, seg in enumerate(transcript.segments):
        if i >= len(lines):
            break
        out.append({
            "start": round(seg.start, 3),
            "end": round(seg.end, 3),
            "zh": seg.text.strip(),
            "en": lines[i].strip(),
        })
    return out


if __name__ == "__main__":
    raise SystemExit(main())
