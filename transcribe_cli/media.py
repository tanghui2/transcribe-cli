# -*- coding: utf-8 -*-
"""音频抽取：把任意音视频文件解码为 16kHz 单声道 PCM wav。

faster-whisper 内部用 PyAV 解码音频，其实不强制要求外部 ffmpeg，
但用外部 ffmpeg 抽成 wav 有两个好处：
1. 解码一次、复用多次（后续调摘要/翻译时不必重复解码）
2. 可以先把长视频切成段，支持断点续跑

如果目标机器没有 ffmpeg，会回退到 faster-whisper 自带的 PyAV 解码路径。
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

# faster-whisper 要求 16kHz 单声道
TARGET_SR = 16_000


@dataclass
class MediaInfo:
    """媒体文件的基本信息。"""

    path: Path
    duration: float          # 秒
    sample_rate: int
    channels: int
    size_bytes: int

    @property
    def size_mb(self) -> float:
        return self.size_bytes / 1024 / 1024


def has_ffmpeg() -> bool:
    """检测系统是否可用 ffmpeg。"""
    return shutil.which("ffmpeg") is not None


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    """执行命令，失败时抛出带 stderr 的异常。"""
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise RuntimeError(
            f"命令执行失败（{proc.returncode}）：{' '.join(cmd[:3])}...\n{proc.stderr[-800:]}"
        )
    return proc


def probe(path: str | Path) -> MediaInfo:
    """读取媒体时长与采样率。用 ffprobe，没有则返回时长 0。"""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"文件不存在：{path}")

    size = path.stat().st_size
    duration, sr, channels = 0.0, 0, 0

    if shutil.which("ffprobe"):
        proc = _run([
            "ffprobe", "-v", "error",
            "-show_entries", "stream=codec_type,sample_rate,channels,duration",
            "-show_entries", "format=duration",
            "-of", "json", str(path),
        ])
        try:
            data = json.loads(proc.stdout)
            fmt_dur = data.get("format", {}).get("duration")
            if fmt_dur:
                duration = float(fmt_dur)
            for st in data.get("streams", []):
                if st.get("codec_type") == "audio":
                    sr = int(st.get("sample_rate") or 0)
                    channels = int(st.get("channels") or 0)
                    if not duration and st.get("duration"):
                        duration = float(st["duration"])
                    break
        except (json.JSONDecodeError, ValueError, KeyError):
            pass

    return MediaInfo(path=path, duration=duration, sample_rate=sr, channels=channels, size_bytes=size)


def extract_wav(
    path: str | Path,
    out_wav: str | Path | None = None,
    start: float | None = None,
    duration: float | None = None,
) -> Path:
    """抽取音频为 16kHz 单声道 wav。

    start / duration 单位为秒，用于只处理片段（长视频分段时用）。
    没有 ffmpeg 时返回 None，调用方应回退到 PyAV 解码。
    """
    path = Path(path)
    if not has_ffmpeg():
        return None

    if out_wav is None:
        out_wav = Path(tempfile.gettempdir()) / f"{path.stem}_16k.wav"
    out_wav = Path(out_wav)
    out_wav.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "ffmpeg", "-nostdin", "-y",
        "-i", str(path),
    ]
    if start is not None:
        cmd += ["-ss", f"{start:.3f}"]
    if duration is not None:
        cmd += ["-t", f"{duration:.3f}"]
    cmd += [
        "-vn",                      # 丢弃视频轨
        "-ac", "1",                 # 单声道
        "-ar", str(TARGET_SR),      # 16kHz
        "-acodec", "pcm_s16le",
        str(out_wav),
    ]
    _run(cmd)
    return out_wav


def decode_with_pyav(path: str | Path) -> tuple:
    """用 PyAV 直接解码为 16kHz 单声道 float32 数组。

    没有 ffmpeg 时的回退路径。返回 (samples, sample_rate)。
    """
    import av
    import numpy as np

    path = Path(path)
    container = av.open(str(path))
    resampler = av.audio.resampler.AudioResampler(format="s16", layout="mono", rate=TARGET_SR)

    chunks = []
    for frame in container.decode(audio=0):
        for out in resampler.resample(frame):
            arr = out.to_ndarray()
            # PyAV 对 packed s16 返回 shape (1, n) 或 (n,)，统一成一维
            chunks.append(arr.reshape(-1).astype(np.float32) / 32768.0)
    container.close()

    if not chunks:
        return np.zeros(0, dtype=np.float32), TARGET_SR
    return np.concatenate(chunks), TARGET_SR
