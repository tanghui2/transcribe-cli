"""转写文本自动校对。

ASR 转出来的中文，错误集中在三类：
1. **同音字错别字** —— 「的/得/地」「在/再」「做/作」「以/已」
2. **专有名词** —— 人名、产品名、课程名、专业术语被写成同音的常用词
3. **口语噪声** —— 「嗯」「啊」「那个」这类无意义填充词、重复词

用本地大模型做校对，但**不能整篇重写**：
- 整篇重写会引入大量模型自己的措辞，失去字幕逐字对应的价值
- 逐段调用太慢（1135 段就是 1135 次请求）

所以采用**分块 + 只报告改动**的策略：
每块 20 段送进模型，要求它只输出「行号|修正后的文本」，
没有问题的行一律不输出。这样：
- 输出量小，解析简单（每行一个分隔符）
- 模型只做「改这一个字」的判断，不做重写
- 块与块之间带重叠上下文，避免跨块专有名词被误改

**关键设计：只改错别字，不改内容。** 提示词里明确禁止增删信息，
因为字幕的价值在于忠实于说话人原话。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Callable

from .core import DEFAULT_MODEL, ollama_available, ollama_generate

# 每块送多少段进模型。太小会丢上下文（专有名词），太大响应慢且容易漏改。
CHUNK_SIZE = 20

# 相邻块的重叠段数。重叠部分提供上下文，防止块首的专有名词被误判。
OVERLAP = 3

# 相似度下限：改后文本与原文的字符相似度必须不低于此值，否则视为异常。
#
# 现在是词语替换模式（模型只给「错误词|正确词」，由代码做替换），
# 所以整行的相似度天然很高，正常改动都在 0.8 以上。
# 这个阈值主要拦两类异常：
#   1. 模型编造的错误词导致替换出乱码
#   2. 替换次数过多把整句改面目全非
# 实测（真实课程转写）：正常改动 0.85–1.00，异常情况 < 0.7。
MIN_SIMILARITY = 0.7

SYSTEM_PROMPT = (
    "你是中文语音转写的校对员，专门修正语音识别（ASR）产生的错误。\n"
    "\n"
    "你会看到若干行编号文本，每行是说话人原话的转写片段。请找出其中的错误并修正。\n"
    "\n"
    "只修正这三类问题：\n"
    "1. 同音字、近音字错别字（的/得/地、在/再、做/作、以/已、必须/必需 等）\n"
    "2. 专有名词、术语、人名、产品名被转写成同音常用词\n"
    "3. 明显的语气词重复、结巴式重复（如「我们我们今天」）\n"
    "\n"
    "严格禁止：\n"
    "- 不得增删、替换任何有意义的词语\n"
    "- 不得改变句子的意思、语气或信息量\n"
    "- 不得添加原文没有的解释、括号、标点符号\n"
    "- 不要润色口语，不要把口语改成书面语\n"
    "\n"
    "输出格式（严格遵守）：\n"
    "每一处需要修正的地方，输出一行，格式为：\n"
    "`行号|错误词|正确词`\n"
    "其中「错误词」必须是该行原文中连续出现的字词，「正确词」是它的修正形式。\n"
    "一行里有多处错误就输出多行，同一行可以重复出现。\n"
    "不要输出整个句子，只输出要替换的那几个字。\n"
    "没有问题的地方一律不要输出。\n"
    "不要输出任何解释、前言、结尾语或代码块标记。\n"
    "如果所有行都没有问题，就只输出 `OK`。"
)


@dataclass
class ProofreadChange:
    """一处校对改动。"""

    seg_id: int          # 原段落 id
    index: int           # 在段列表中的序号（用于界面定位）
    original: str        # 原文中被替换的那一段
    corrected: str       # 修正后的那一段
    full_original: str = ""   # 整行原文
    full_corrected: str = ""  # 整行修正后
    reason: str = ""     # 改动类型说明（可选，模型不填就不显示）

    @property
    def is_real(self) -> bool:
        """去掉空白后是否真的变了 —— 防止模型把同一句话改出差异。"""
        return self.full_original.strip() != self.full_corrected.strip()

    @property
    def similarity(self) -> float:
        """改前改后的字符相似度。"""
        return SequenceMatcher(
            None, self.full_original.strip(), self.full_corrected.strip()
        ).ratio()


@dataclass
class ProofreadResult:
    """整篇校对结果。"""

    changes: list[ProofreadChange] = field(default_factory=list)
    scanned: int = 0             # 检查了多少段
    blocks: int = 0              # 分了多少块
    failed_blocks: int = 0       # 有多少块调用失败（Ollama 不可用等）
    rejected: int = 0            # 有多少处改动因校验不通过被丢弃
    skipped: bool = False        # 是否整体跳过（Ollama 不可用）

    @property
    def changed_count(self) -> int:
        return len([c for c in self.changes if c.is_real])

    def to_dict(self) -> dict:
        return {
            "scanned": self.scanned,
            "blocks": self.blocks,
            "failedBlocks": self.failed_blocks,
            "rejected": self.rejected,
            "skipped": self.skipped,
            "changedCount": self.changed_count,
            "changes": [
                {
                    "segId": c.seg_id,
                    "index": c.index,
                    "original": c.original,
                    "corrected": c.corrected,
                    "fullOriginal": c.full_original,
                    "fullCorrected": c.full_corrected,
                    "reason": c.reason,
                    "similarity": round(c.similarity, 3),
                }
                for c in self.changes
            ],
        }


# ---------------------------------------------------------------- 解析

# 行首的行号与分隔符：允许 `12|词|词`、`12 | 词 | 词`、`12:词:词`
_LINE_RE = re.compile(r"^\s*(\d+)\s*[|｜:：]\s*(.+?)\s*$")

# 模型填在「正确词」位置的占位语 —— 这些不是真的修正内容
_PLACEHOLDER_WORDS = {
    "无错误", "无", "无错", "没错", "正确", "不变", "无需修改", "没有错误",
    "不需要修改", "原文", "保持不变", "无需更改", "不需要更改", "原样", "照旧",
}

# 整行里出现这些占位标记就丢弃该行。
# 模型有时写成「内容多（无错误）」这种把占位语塞进错误词位置的情况，
# 只比对「正确词」拦不住，所以这里做整行匹配。
_PLACEHOLDER_MARK = re.compile(
    r"[（(【\[]?\s*(无错误|无错|没错|不需要修改|无需修改|不需要更改|无需更改|没有错误|保持不变|原样)\s*[)）】\]]?"
)

# 错误词/正确词里若含这些标点，说明模型塞了整句而不是词语
_HAS_PUNCT = re.compile(r"[。！？，；：、,.!?;]")


def _strip_paren(s: str) -> str:
    """剥掉包裹的中英文括号与首尾空白。"""
    s = s.strip()
    for l, r in (("（", "）"), ("(", ")"), ("【", "】"), ("[", "]"), ("「", "」")):
        if s.startswith(l) and s.endswith(r) and len(s) > len(l) + len(r) - 1:
            s = s[len(l):-len(r)].strip()
    return s


def parse_changes(block: str, seg_ids: list[int], originals: list[str]) -> list[ProofreadChange]:
    """从模型的一次响应里解析出改动。

    模型输出格式为 `行号|错误词|正确词`：
    - 行号按 1 起算，是块内序号
    - 错误词必须在该行原文里连续出现，否则无法安全替换

    同一行可能有多个错误词，逐个替换后合并成一条改动。
    """
    # 每行收集所有 (错误词, 正确词) 对
    per_line: dict[int, list[tuple[str, str]]] = {}

    for raw in block.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.upper() in {"OK", "无", "无需修改", "没有错误"}:
            continue
        # 模型偶尔会用 ``` 包裹
        if line.startswith("```"):
            continue
        m = _LINE_RE.match(line)
        if not m:
            continue
        no = int(m.group(1))
        # 行号越界说明模型在胡说，丢弃
        if no < 1 or no > len(seg_ids):
            continue
        # 模型有时把「无错误」「无」「正确」这类占位语混在输出里
        # （可能在错误词位置、正确词位置，也可能带括号）。
        # 只要这一行出现占位标记，整行丢弃 —— 宁可漏改也不错改。
        if _PLACEHOLDER_MARK.search(line):
            continue
        rest = m.group(2)
        parts = re.split(r"\s*[|｜]\s*", rest)
        if len(parts) < 2:
            continue
        wrong = parts[0].strip()
        right = parts[1].strip()
        if not wrong or not right or wrong == right:
            continue
        # 纯大小写差异不算改动（「一个X」→「一个x」没有实际意义）
        if wrong.lower() == right.lower():
            continue
        # 错误词/正确词里带句末标点说明模型没按格式输出（把整句塞进来了）
        if _HAS_PUNCT.search(wrong) or _HAS_PUNCT.search(right):
            continue
        # 长度差过大说明模型把词截断了或补了一大截，不是错别字修正。
        # 错别字修正通常是等长或差 1–2 字（「单摄」→「单射」等长，
        # 「3inx」→「sinx」少 1 字）。
        if abs(len(wrong) - len(right)) > 2:
            continue
        # 纯删除类改动（修正词是错误词的子串）要额外小心：
        # 如果被删的内容含英文字母或数字，那多半是数学符号或变量名，
        # 不能删。实测踩到过：「D定义语」→「定义语」把符号 D 删掉了。
        if right in wrong and wrong != right:
            deleted = wrong.replace(right, "", 1)
            if re.search(r"[A-Za-z0-9]", deleted):
                continue
        per_line.setdefault(no, []).append((wrong, right))

    out: list[ProofreadChange] = []
    for no, pairs in per_line.items():
        original = originals[no - 1]
        text = original
        applied = 0
        for wrong, right in pairs:
            # 错误词必须在这一行里真实存在，否则是模型编的
            if wrong not in text:
                continue
            # 只替换第一处，避免重复词被批量替换
            text = text.replace(wrong, right, 1)
            applied += 1
        if applied == 0:
            continue
        out.append(
            ProofreadChange(
                seg_id=seg_ids[no - 1],
                index=no - 1,
                original=pairs[0][0],
                corrected=pairs[0][1],
                full_original=original,
                full_corrected=text,
            )
        )
    return out


# ---------------------------------------------------------------- 主流程


def _build_block_prompt(items: list[tuple[int, str]]) -> str:
    """把一段块的文本拼成提示词。items 是 (块内序号从1开始, 文本)。"""
    lines = [f"{i}\t{text}" for i, text in items]
    return (
        "以下是语音转写的片段，每行行号后是转写文本。\n"
        "请找出并修正其中的 ASR 错误。\n\n"
        + "\n".join(lines)
    )


def proofread(
    segments: list,
    model: str = DEFAULT_MODEL,
    chunk_size: int = CHUNK_SIZE,
    overlap: int = OVERLAP,
    on_progress: Callable[[int, int], None] | None = None,
) -> ProofreadResult:
    """对转写段落做自动校对。

    segments 是 core.Segment 的列表（需要 .id 与 .text 属性）。
    on_progress(done_blocks, total_blocks) 用于回报进度。
    """
    result = ProofreadResult()

    # 只校对有内容的段落
    targets = [s for s in segments if s.text and s.text.strip()]
    result.scanned = len(targets)
    if not targets:
        result.skipped = True
        return result

    if not ollama_available():
        result.skipped = True
        return result

    # 分块（带重叠）
    blocks: list[list[tuple[int, str]]] = []
    step = max(1, chunk_size - overlap)
    for start in range(0, len(targets), step):
        chunk = targets[start:start + chunk_size]
        if not chunk:
            break
        blocks.append([
            (i + 1, s.text.strip())
            for i, s in enumerate(chunk)
        ])
        if start + chunk_size >= len(targets):
            break
    result.blocks = len(blocks)

    # 记录每块对应的段
    block_segs: list[list] = []
    for start in range(0, len(targets), step):
        chunk = targets[start:start + chunk_size]
        if not chunk:
            break
        block_segs.append(chunk)
        if start + chunk_size >= len(targets):
            break

    for bi, (items, segs) in enumerate(zip(blocks, block_segs)):
        prompt = _build_block_prompt(items)
        res = ollama_generate(
            prompt=prompt,
            system=SYSTEM_PROMPT,
            model=model,
            temperature=0.1,     # 低温：要稳定不要发挥
            max_tokens=1200,
        )
        if res is None:
            result.failed_blocks += 1
        else:
            seg_ids = [s.id for s in segs]
            originals = [s.text.strip() for s in segs]
            changes = parse_changes(res, seg_ids, originals)
            # 去重：同一段落可能被重复报告，保留第一条
            seen: set[int] = set()
            for c in changes:
                if c.seg_id in seen:
                    continue
                seen.add(c.seg_id)
                if not c.is_real:
                    continue
                # 相似度过低说明模型串行了（把别行的修正结果写到了这一行）。
                # 校对只改错别字，不该整句替换，这种一律丢弃。
                if c.similarity < MIN_SIMILARITY:
                    result.rejected += 1
                    continue
                result.changes.append(c)
        if on_progress:
            on_progress(bi + 1, len(blocks))

    # 按段落顺序排，方便界面展示
    result.changes.sort(key=lambda c: c.index)
    return result


def apply_corrections(segments: list, changes: list[ProofreadChange]) -> list:
    """把校对结果应用到段落上，返回新的段落列表（不修改原对象）。"""
    from copy import copy

    mapping = {c.seg_id: c.corrected for c in changes if c.is_real}
    out = []
    for s in segments:
        if s.id in mapping:
            ns = copy(s)
            ns.text = mapping[s.id]
            out.append(ns)
        else:
            out.append(s)
    return out


def write_corrected_srt(segments: list, out_path) -> None:
    """写校对后的 SRT（时间轴不变，只换文本）。"""
    from pathlib import Path

    from .core import _srt_timestamp

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    lines = []
    idx = 0
    for seg in segments:
        if not seg.text.strip():
            continue
        idx += 1
        lines.append(str(idx))
        lines.append(f"{_srt_timestamp(seg.start)} --> {_srt_timestamp(seg.end)}")
        lines.append(seg.text.strip())
        lines.append("")
    out_path.write_text("\n".join(lines), encoding="utf-8")
