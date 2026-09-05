"""
逐字核验：把模型摘出来的对话/独白/旁白/描写，回原文里做精确比对。

这是整条管道里唯一不依赖模型判断的客观闸门。小模型最容易犯的错是
「顺手改写」和「凭印象编造」，这两种错都会在这里暴露成命中率下降。

判定分三档：
  exact  逐字命中（归一化后原文包含该串）
  near   高度相似但不完全一致（模型改写了，需要修正）
  miss   原文里根本找不到（臆造，最严重）
"""
import re
import unicodedata
from difflib import SequenceMatcher

# 中英标点归一：只统一形态，不删内容
_PUNCT_MAP = {
    "“": '"', "”": '"', "„": '"', "‟": '"', "「": '"', "」": '"',
    "‘": "'", "’": "'", "『": "'", "』": "'",
    "！": "!", "？": "?", "，": ",", "。": ".", "；": ";", "：": ":",
    "（": "(", "）": ")", "、": ",", "—": "-", "－": "-", "～": "~",
    "…": "...", "《": "<", "》": ">",
}
_WS = re.compile(r"\s+")


def norm(s: str) -> str:
    """归一化：全角转半角、统一标点、去掉所有空白。"""
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", s)
    s = "".join(_PUNCT_MAP.get(c, c) for c in s)
    return _WS.sub("", s)


def _best_window_ratio(needle: str, hay: str) -> float:
    """
    在原文里找与 needle 最像的一段，返回相似度，用来区分 near（模型改写）和 miss（凭空编造）。

    窗口必须与 needle 等长：窗口比 needle 长会把相似度上限压到 2n/(n+w)，
    一个字的改写也会掉到 0.8 以下，改写就被误判成编造了。
    先用最长公共子串锚定位置，再在锚点附近逐位取等长窗口比对。
    """
    if not needle or not hay:
        return 0.0
    n = len(needle)
    if n >= len(hay):
        return SequenceMatcher(None, needle, hay).ratio()

    sm = SequenceMatcher(None, needle, hay, autojunk=False)
    m = sm.find_longest_match(0, n, 0, len(hay))
    if m.size == 0:
        return 0.0

    anchor = m.b - m.a                      # 若逐字命中，窗口应从这里开始
    lo = max(0, anchor - n // 2)
    hi = min(len(hay) - n, anchor + n // 2)
    best = 0.0
    for i in range(lo, hi + 1):
        r = SequenceMatcher(None, needle, hay[i:i + n]).ratio()
        if r > best:
            best = r
            if best > 0.99:
                break
    return best


_NEAR_THRESHOLD = 0.70   # 实测：加一字的改写 0.96、成句改写 0.6~0.8、凭空编造 <0.5


def check_quote(quote: str, paragraphs: list[str], para: int | None = None,
                near_threshold: float = _NEAR_THRESHOLD) -> dict:
    """
    核验单条引用。

    para 是模型自报的段落号（1 起）。先在该段里找，找不到再全章找——
    这样既能验内容，也能验模型有没有把位置标对。
    """
    q = norm(quote)
    result = {"quote": quote, "para_claimed": para, "status": "miss",
              "para_found": None, "similarity": 0.0, "para_correct": None}
    if not q:
        result["status"] = "empty"
        return result

    norm_paras = [norm(p) for p in paragraphs]

    # 1) 先查自报段落
    if para and 1 <= para <= len(norm_paras) and q in norm_paras[para - 1]:
        result.update(status="exact", para_found=para, similarity=1.0, para_correct=True)
        return result

    # 2) 全章找逐字命中（含跨段拼接的情况）
    for i, np in enumerate(norm_paras, 1):
        if q in np:
            # 没自报段号的字段（如描写原句）不参与段号正确性统计，留 None
            result.update(status="exact", para_found=i, similarity=1.0,
                          para_correct=(para == i) if para else None)
            return result
    if q in norm("\n".join(paragraphs)):
        result.update(status="exact", para_found=-1, similarity=1.0,
                      para_correct=None)   # 跨段命中，段落号本就无法对应
        return result

    # 3) 没逐字命中，判断是改写还是臆造
    whole = norm("".join(paragraphs))
    sim = _best_window_ratio(q, whole)
    result["similarity"] = round(sim, 4)
    result["status"] = "near" if sim >= near_threshold else "miss"
    return result


def locate(quote: str, paragraphs: list[str]) -> tuple[int, int] | None:
    """
    逐字命中的引用落在第几段到第几段（1 起，含两端）；没命中返回 None，单段命中时两端相等。

    check_quote 对跨段命中只给 para_found=-1——一条引用横跨两段，段号本就对不上「一段」。
    但 s5 修越界段号、剧本引用都需要一个能落笔的位置，这里按引用开头落在哪一段给出起点。
    """
    q = norm(quote)
    if not q:
        return None
    norm_paras = [norm(p) for p in paragraphs]
    pos = "".join(norm_paras).find(q)
    if pos < 0:
        return None
    end_pos, start, offset = pos + len(q) - 1, None, 0
    for i, np in enumerate(norm_paras, 1):
        nxt = offset + len(np)
        if start is None and pos < nxt:
            start = i
        if end_pos < nxt:
            return start, i
        offset = nxt
    return None


# 需要逐字的字段：(JSON 路径, 取文本的键, 取段落号的键)
VERBATIM_FIELDS = [
    ("dialogues", "text", "para"),
    ("monologues", "text", "para"),
    ("narration", "text", "para"),
]
# 人物/场景里的描写原句是嵌套列表，单独处理
NESTED_QUOTE_FIELDS = [
    ("characters", "appearance_quotes"),
    ("scenes", "description_quotes"),
]


def check_analysis(analysis: dict, paragraphs: list[str]) -> dict:
    """
    对一份章节抽取结果做全量逐字核验，返回可直接写进章节 json 的报告。
    """
    items = []
    for field, tkey, pkey in VERBATIM_FIELDS:
        for i, obj in enumerate(analysis.get(field) or []):
            if not isinstance(obj, dict):
                continue
            r = check_quote(obj.get(tkey, ""), paragraphs, obj.get(pkey))
            r["field"] = f"{field}[{i}]"
            items.append(r)
    for field, qkey in NESTED_QUOTE_FIELDS:
        for i, obj in enumerate(analysis.get(field) or []):
            if not isinstance(obj, dict):
                continue
            for j, q in enumerate(obj.get(qkey) or []):
                r = check_quote(q if isinstance(q, str) else str(q), paragraphs, None)
                r["field"] = f"{field}[{i}].{qkey}[{j}]"
                items.append(r)

    total = len(items)
    counts = {k: sum(1 for r in items if r["status"] == k)
              for k in ("exact", "near", "miss", "empty")}
    rate = counts["exact"] / total if total else 1.0
    wrong_para = [r for r in items if r["para_correct"] is False and r["status"] == "exact"]

    return {
        "total_quotes": total,
        "counts": counts,
        "verbatim_rate": round(rate, 4),
        "misplaced_para_count": len(wrong_para),
        # 只留问题项，全部命中时不占地方
        "problems": [r for r in items if r["status"] in ("near", "miss", "empty")][:80],
    }


# 一条台词：从开引号起，到下一个任意引号字符或段落结束为止。
# 不要求闭引号配对——原文里确有 `？“` 这种收尾误用左引号的地方，
# 若强行要求配对，正则会一路吞到几段之后，把整块叙述误当成一条台词。
_SPEECH_RE = re.compile(r"[“\"]([^“”\"]{2,})")


# 中文引号不只用来引说话，也用来标绰号、术语、反语。
# 「凡人」「高人」「六指大神」「仙人脑」「天残手」这些都被引号括着，但都不是台词。
# 不区分就会把覆盖率压得很低，掩盖掉真正漏掉的台词。
_SENT_MARK = "。！？…～~，,、；;：:"


def is_speech(seg: str) -> bool:
    """判断一段引号内容是不是「有人在说话」，而不是被引起来的名词。"""
    s = (seg or "").strip()
    if len(s) < 2:
        return False
    if len(s) >= 7:                       # 够长的，基本只能是话
        return True
    return any(ch in s for ch in _SENT_MARK)   # 短的要看有没有句读


def find_speech(paragraphs: list[str], strict: bool = True) -> list[str]:
    """
    逐段扫描台词。必须按段扫，跨段扫会让一个坏引号污染后面整片正文。
    strict=True 时滤掉「加引号的名词」，只留真正的说话。
    """
    segs = [s.strip() for p in paragraphs for s in _SPEECH_RE.findall(p) if s.strip()]
    return [s for s in segs if is_speech(s)] if strict else segs


def coverage_report(analysis: dict, paragraphs: list[str]) -> dict:
    """
    覆盖率：原文里带引号的说话有多少被抽进 dialogues。
    漏台词和编台词一样致命，这项专门盯漏。
    """
    speech = find_speech(paragraphs)
    # 把抽出的台词按文件顺序拼成一串再做子串查找，是有意的：模型常把一句拆成两条，
    # 拼起来才对得上。以前用 set 拼，顺序随哈希变，同一份数据两次跑出的覆盖率可能不同；
    # 现在按列表顺序拼，结果可复现。
    got_all = "".join(norm(d.get("text", "")) for d in (analysis.get("dialogues") or [])
                      if isinstance(d, dict))
    missed = [s for s in speech if norm(s) not in got_all]
    return {
        "raw_speech_count": len(speech),
        "extracted_dialogue_count": len(analysis.get("dialogues") or []),
        "missed_count": len(missed),
        "dialogue_coverage": round(1 - len(missed) / len(speech), 4) if speech else 1.0,
        "missed_samples": missed[:20],
    }
