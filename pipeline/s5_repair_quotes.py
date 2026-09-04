#!/usr/bin/env python3
"""
步骤 5：把不逐字的引用**对齐回原文**。**不调 API，纯程序修复。**

体检发现 31 章共 91 条引用不达标。逐条比对原文后，全部是机械性错误，
没有一条是真的凭空编造：

| 类型 | 数量 | 长什么样 |
| --- | --- | --- |
| 同段多句拼接 | 77 | 「那是自然，谢谢各位仙师前来了！诸位仙师吃好喝好……」——原文里这是
|            |    | 两句台词，中间隔着「姚城主正值中年，面目刚正，此时却是皱着眉陪笑道：」，
|            |    | 模型把旁白吞了，两句拼成一条 |
| 单句子串   | 10 | 只摘了半句 |
| 代词/称呼替换 | 3 | 原文「唐真听这话便想起」→ 模型写「他听这话便想起」 |
| 用错时期的名字 | 1 | 原文「姚安饶」→ 模型写「姚安恕」（那是她出家后的法号） |

既然全是机械性的，就该用程序确定性修好，而不是花钱重跑模型——
重跑既不保证修得掉，还可能引入新的偏差。

## 安全保证

**改完的文本一定是原文的字面子串**，这是构造上的保证，不是事后检查：

1. 拼接类 → 拆成原文里那几条引语，逐条落回，一个字不改
2. 子串与替换类 → 用 SequenceMatcher 找出原文里与之对齐的那一段，
   直接取 `原文[起:止]`
3. 修完立刻重算逐字率，**没有变好就整章回滚**，绝不写坏数据

用法：
    python pipeline/s5_repair_quotes.py --dry-run     # 只报会改什么，不落盘
    python pipeline/s5_repair_quotes.py               # 修全书
    python pipeline/s5_repair_quotes.py --seqs 9,90   # 只修指定章
    python pipeline/s5_repair_quotes.py --refresh      # 不改 analysis，只重算质量块并回写有变化的章
"""
import argparse
import difflib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import paths, quality, verbatim
from common.jsonio import read_json, write_json
from common.names import base_name, is_unnamed

# 一段里的各条引语。与 verbatim._SPEECH_RE 同源：不要求闭引号配对，
# 因为原文里确有 `？“` 这种收尾误用左引号的地方。
SEG_RE = re.compile(r'[“"]([^“”"]{1,})')

# 需要修的字段：(列表字段, 文本键, 段号键)
TEXT_FIELDS = [("dialogues", "text", "para"),
               ("monologues", "text", "para"),
               ("narration", "text", "para")]
NESTED_FIELDS = [("characters", "appearance_quotes"),
                 ("scenes", "description_quotes")]


def _flat(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def segments(par: str) -> list[str]:
    """
    切出一段里的各条引语。

    开头的冒号逗号要剥掉：原文里有「红儿声音有些颤抖“：前些天晚上……」这种
    把引号点在冒号前面的写法，不剥的话切出来是「：前些天晚上……」，
    跟模型抽的「前些天晚上……」永远对不上。
    """
    out = []
    for m in SEG_RE.finditer(par):
        s = m.group(1).strip().rstrip('”"').lstrip("：:，,、；; ").strip()
        if s:
            out.append(s)
    return out


def split_concat(quote: str, paras: list[str], claimed) -> list[str] | None:
    """
    拼接类：模型把同一段里被旁白隔开的多条引语拼成了一条。
    拆回原文的那几条，逐条返回。找不到就返回 None，不硬拆。
    """
    order = ([claimed - 1] if isinstance(claimed, int) and 0 < claimed <= len(paras) else [])
    order += [i for i in range(len(paras)) if i not in order]
    for i in order:
        segs = segments(paras[i])
        if len(segs) < 2:
            continue
        # 从头开始贪心地吃掉引语，看能不能正好拼出模型那条
        target, acc, used = _flat(quote), "", []
        for s in segs:
            if not target.startswith(_flat(acc + s)):
                if acc and target == _flat(acc):
                    break
                acc, used = "", []          # 断了就重来，允许从中间某条起拼
                if target.startswith(_flat(s)):
                    acc, used = s, [s]
                continue
            acc, used = acc + s, used + [s]
            if _flat(acc) == target:
                return used
    return None


def split_across_paras(quote: str, paras: list[str], claimed,
                       span: int = 3) -> list[tuple[str, int]] | None:
    """
    跨段拼接：同一个人连着说了两段，模型把它们接成了一条。
    比如 seq11 的第 77 段「…赶忙跑过去！」和第 78 段「结果发现屏风后小姐依然躺着…」。

    只在**相邻**几段内拼（默认 3 段），避免把全书的引语乱凑成一条。
    返回 [(原文引语, 段号), ...]。
    """
    if not isinstance(claimed, int) or not 0 < claimed <= len(paras):
        return None
    target = _flat(quote)
    lo = max(0, claimed - 1 - span)
    hi = min(len(paras), claimed + span)
    # 把窗口内所有引语摊平成一串，再从任意一条起试着往后拼。
    # 不能一遇到不匹配就停——本章第 77 段的第一条是「我见到了那个妖魔。」，
    # 模型抽的那条是从第二条开始的，遇错即停就永远走不到第二条。
    flat_segs = [(s, i + 1) for i in range(lo, hi) for s in segments(paras[i])]
    for a in range(len(flat_segs)):
        acc, used = "", []
        for s, pn in flat_segs[a:]:
            if not target.startswith(_flat(acc + s)):
                break
            acc, used = acc + s, used + [(s, pn)]
            if _flat(acc) == target and len({p for _, p in used}) > 1:
                return used
    return None


def strip_narration(quote: str, paras: list[str], claimed) -> list[str] | None:
    """
    模型把整段（含旁白）当成一条台词时，从原文对应段里把引语逐条剥出来。
    只在能定位到原文段、且该段确实有多条引语时才动手。
    """
    order = ([claimed - 1] if isinstance(claimed, int) and 0 < claimed <= len(paras) else [])
    order += [i for i in range(len(paras)) if i not in order]
    flat_q = _flat(quote)
    for i in order:
        segs = segments(paras[i])
        if len(segs) < 2:
            continue
        # 模型那条里必须能找到这些引语的主要部分，否则不是同一段
        hit = [s for s in segs if _flat(s)[:8] and _flat(s)[:8] in flat_q]
        if len(hit) >= 2:
            return hit
    return None


# 断句符。落在这些字符的边上，说明取到的是一个完整的语义单元而不是半截话。
_END_MARKS = "。！？；;…”\"）)】》"
_START_AFTER = _END_MARKS + "，,、：:“\"（(【《 "


def _boundary_bonus(src: str, lo: int, hi: int) -> float:
    b = 0.0
    if lo == 0 or src[lo - 1] in _START_AFTER:
        b += 0.05
    if hi >= len(src) or src[hi - 1] in _END_MARKS:
        b += 0.05
    return b


def align_span(quote: str, paras: list[str], claimed) -> tuple[str, int] | None:
    """
    子串／代词替换类：在原文里找出与这条引用对齐的那一段，原样返回。
    返回 (原文片段, 段号)。取的是 `原文[起:止]`，所以一定逐字。
    """
    order = ([claimed - 1] if isinstance(claimed, int) and 0 < claimed <= len(paras) else [])
    order += [i for i in range(len(paras)) if i not in order]
    best = (0.0, None, None)
    n = len(quote)
    for i in order:
        src = paras[i]
        sm = difflib.SequenceMatcher(None, quote, src, autojunk=False)
        m = sm.find_longest_match(0, n, 0, len(src))
        # 锚点长度随引用长度自适应。固定要 4 字会卡死短引用：
        # 「红色脸谱」对原文「红色的脸谱」，中间隔了个「的」，
        # 最长公共块只有 2 字，于是这条永远修不掉。
        if m.size < max(2, min(4, n // 3)):
            continue
        # 按**位置**对齐，再在小窗口里搜最优边界。
        #
        # 不能取匹配块的交集：头尾不匹配的部分会被切掉——原文「以姚安饶此时…」
        # 对上模型的「以姚安恕此时…」，一字之差落在开头，主语「以姚安饶」被整个砍掉；
        # 「好几个姚安饶了。」被截成「好几个姚安」。
        #
        # 也不能只按 m.b - m.a 取等长：原文前缀比模型的长时（「唐真」两字 vs
        # 「他」一字），起点会落进名字中间，得到「真听这话便想起…」。
        #
        # 所以两边各留 ±8 字的余量搜一遍，按相似度取最优。问题引用总共几十条，
        # 这点开销可以忽略。
        base_lo = max(0, m.b - m.a)
        for dlo in range(-8, 9):
            lo = base_lo + dlo
            if lo < 0 or lo >= len(src):
                continue
            for dhi in range(-8, 9):
                hi = lo + n + dhi
                if hi <= lo or hi > len(src):
                    continue
                span = src[lo:hi].strip().strip('“”"').strip()
                if not span or len(span) < n * 0.8:
                    continue
                r = difflib.SequenceMatcher(None, quote, span, autojunk=False).ratio()
                # 光看相似度会**奖励删字**：模型写「他不知他怎么了…」，原文是
                # 「楼主不知他怎么了…」，此时「不知他怎么了…」的相似度反而最高，
                # 因为它更短。于是主语被悄悄删掉，读起来还通顺，但意思残了。
                # 所以给「起止落在自然断句处」的候选加分，让完整的句子胜出。
                r += _boundary_bonus(src, lo, hi)
                if r > best[0]:
                    best = (r, span, i + 1)
    # 门槛提到 0.75：位置对齐之后，真正同一句话的相似度应当很高。
    # 达不到说明根本不是同一句，宁可留给人工也不要瞎改。
    return (best[1], best[2]) if best[0] >= 0.75 else None


def fill_missing_dialogue(doc: dict) -> list[dict]:
    """
    把原文里有、但没被抽进 dialogues 的台词补上。

    原则二明写「原文人物对话……不能漏掉」，所以漏抽和臆造一样要治。
    补进来的 text **逐字取自原文**；说话人无从判断时填「未明说」——
    宁可留白，也不替原文给角色分配台词。
    """
    a, paras = doc["analysis"], doc["paragraphs"]
    dials = a.get("dialogues")
    if not isinstance(dials, list):
        return []
    got = "".join(verbatim.norm(d.get("text", "")) for d in dials if isinstance(d, dict))
    add = []
    for i, par in enumerate(paras, 1):
        for seg in segments(par):
            if not verbatim.is_speech(seg):
                continue
            if verbatim.norm(seg) in got:
                continue
            got += verbatim.norm(seg)          # 同一句在本章重复出现只补一次
            add.append({"para": i, "speaker": "未明说", "addressee": "",
                        "text": seg, "manner": "", "_filled": True})
    if add:
        a["dialogues"] = sorted(dials + add, key=lambda d: d.get("para") or 0)
    return add


def fill_missing_characters(doc: dict) -> list[str]:
    """
    说了话却没被登记进人物名单的角色，补一条最小条目。

    不这么做的代价很实在：资产聚合按 characters 统计出场，
    红儿在某章说了三句话却没登记，她那一章的出场就丢了。
    补的是「这个名字在本章说过话」这一条已有证据，不新增任何原文没有的信息。
    """
    a = doc["analysis"]
    chars = a.get("characters")
    if not isinstance(chars, list):
        return []
    known = {c.get("name") for c in chars if isinstance(c, dict)}
    added = []
    # 两个来源都要管：说了话的，和被登记在场景里的。
    # 只管前者会留下 79 处「场景出现未登记人物」，聚合时同样丢出场。
    names = [(d.get("speaker") or "").strip()
             for d in a.get("dialogues") or [] if isinstance(d, dict)]
    for sc in a.get("scenes") or []:
        if isinstance(sc, dict):
            names += [p.strip() for p in (sc.get("present_characters") or [])
                      if isinstance(p, str)]
    for sp in names:
        if not sp or sp == "未明说" or sp in known or is_unnamed(sp):
            continue
        if base_name(sp) in known:
            continue
        known.add(sp)
        added.append(sp)
        # role_in_chapter 用枚举内的「参与」：这个人在本章说过话或在场，正是「参与」的定义。
        # 早先写的「出场」不在 schema/提示词的枚举里，曾污染 286 条，已迁移。
        chars.append({"name": sp, "aliases": [], "role_in_chapter": "参与",
                      "mentioned_only": False, "appearance_quotes": [],
                      "state": "", "actions": [], "_filled": True})
    return added


def has_unregistered(doc: dict) -> bool:
    """本章有没有「说了话或在场，却不在人物名单」的具名角色。"""
    a = doc.get("analysis") or {}
    chars = a.get("characters")
    if not isinstance(chars, list):
        return False
    known = {c.get("name") for c in chars if isinstance(c, dict)}
    names = [(d.get("speaker") or "") for d in a.get("dialogues") or []
             if isinstance(d, dict)]
    for sc in a.get("scenes") or []:
        if isinstance(sc, dict):
            names += [p for p in (sc.get("present_characters") or []) if isinstance(p, str)]
    for n in names:
        n = (n or "").strip()
        if not n or n == "未明说" or n in known or is_unnamed(n):
            continue
        if base_name(n) in known:
            continue
        return True
    return False


def repair_chapter(doc: dict) -> tuple[dict, list[dict]]:
    """返回 (修好的 doc, 修改记录)。不落盘。"""
    a, paras = doc["analysis"], doc["paragraphs"]
    log: list[dict] = []

    def bad(text, claimed):
        return verbatim.check_quote(text, paras, claimed)["status"] in ("near", "miss")

    for field, tkey, pkey in TEXT_FIELDS:
        items = a.get(field)
        if not isinstance(items, list):
            continue
        out = []
        for obj in items:
            if not isinstance(obj, dict) or not bad(obj.get(tkey, ""), obj.get(pkey)):
                out.append(obj)
                continue
            q, claimed = obj.get(tkey, ""), obj.get(pkey)
            cross = split_across_paras(q, paras, claimed)
            if cross:
                # 跨段的要各自带回自己的段号，否则段号会指错地方
                for s, pn in cross:
                    n = dict(obj)
                    n[tkey], n[pkey] = s, pn
                    out.append(n)
                log.append({"field": field, "kind": "拆分跨段", "before": q,
                            "after": [s for s, _ in cross]})
                continue
            parts = split_concat(q, paras, claimed)
            if not parts and field == "dialogues" and '“' in q:
                # 模型把**整段**（旁白 + 嵌套引号）当成了一条台词。
                # 单纯做对齐会留下孤零零的引号，比如
                # 「真好看真好看！”他围着姚安饶转圈…开口道：“好徒弟…」。
                # 正确做法是把这一段里的引语逐条剥出来。
                parts = strip_narration(q, paras, claimed)
            if parts and len(parts) > 1:
                for s in parts:
                    n = dict(obj)
                    n[tkey] = s
                    out.append(n)
                log.append({"field": field, "kind": "拆分拼接", "before": q,
                            "after": parts})
                continue
            span = align_span(q, paras, claimed)
            if span and _flat(span[0]) != _flat(q):
                n = dict(obj)
                n[tkey], n[pkey] = span[0], span[1]
                out.append(n)
                log.append({"field": field, "kind": "对齐原文", "before": q,
                            "after": [span[0]]})
                continue
            out.append(obj)                      # 修不了就原样留着，交人工
        a[field] = out

    for field, qkey in NESTED_FIELDS:
        for obj in a.get(field) or []:
            if not isinstance(obj, dict) or not isinstance(obj.get(qkey), list):
                continue
            new = []
            for q in obj[qkey]:
                if not isinstance(q, str) or not bad(q, None):
                    new.append(q)
                    continue
                span = align_span(q, paras, None)
                if span and _flat(span[0]) != _flat(q):
                    new.append(span[0])
                    log.append({"field": f"{field}.{qkey}", "kind": "对齐原文",
                                "before": q, "after": [span[0]]})
                else:
                    new.append(q)
            obj[qkey] = new
    return doc, log


def refresh_only(seqs: list[int], dry_run: bool) -> int:
    """不改 analysis，只按当前内容重算质量块，回写有变化的章。

    用旧版脚本跑出的章（闸门规则不同）、或手改过 analysis 之后，靠它把 passed / coverage /
    fail_reasons 对齐到现行闸门。s3 只报不写，所以这件事放在这里。"""
    changed, flipped = [], []
    for seq in seqs:
        p = paths.chapter_json_path(seq)
        if not p.exists():
            continue
        doc = read_json(p)
        before = json.dumps(doc.get("quality"), sort_keys=True, ensure_ascii=False)
        was = (doc.get("quality") or {}).get("passed")
        quality.refresh(doc)
        if json.dumps(doc["quality"], sort_keys=True, ensure_ascii=False) == before:
            continue
        changed.append(seq)
        if doc["quality"]["passed"] != was:
            flipped.append(f"seq{seq} {was}→{doc['quality']['passed']}")
        if not dry_run:
            write_json(p, doc)
    print(f"{'（dry-run，未落盘）' if dry_run else ''}重算 {len(seqs)} 章，质量块有变化 {len(changed)} 章"
          + (f"：{changed[:30]}{' …' if len(changed) > 30 else ''}" if changed else ""))
    if flipped:
        print(f"  passed 翻转 {len(flipped)} 章：{'；'.join(flipped[:20])}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="把不逐字的引用对齐回原文（不调 API）")
    ap.add_argument("--seqs", help="只修这些 seq，逗号分隔。默认全书")
    ap.add_argument("--dry-run", action="store_true", help="只报告，不落盘")
    ap.add_argument("--no-fill", action="store_true",
                    help="只做引用对齐，不补漏抽的台词与未登记的人物")
    ap.add_argument("--refresh", action="store_true",
                    help="不改 analysis，只按当前内容重算质量块（逐字/覆盖/passed/fail_reasons）并回写有变化的章。"
                         "用旧版脚本跑出的章或手改过 analysis 之后用它对齐闸门结论")
    args = ap.parse_args()

    if args.seqs:
        seqs = sorted(int(x) for x in args.seqs.replace(" ", ",").split(",") if x.strip())
    else:
        seqs = sorted(int(p.stem[2:]) for p in paths.CHAPTERS_DIR.glob("ch*.json"))
    if args.refresh:
        return refresh_only(seqs, args.dry_run)

    touched, reverted, total_fix = 0, [], 0
    before_bad = after_bad = 0
    all_log = []
    for seq in seqs:
        p = paths.chapter_json_path(seq)
        if not p.exists():
            continue
        doc = read_json(p)
        v0 = verbatim.check_analysis(doc["analysis"], doc["paragraphs"])
        c0 = verbatim.coverage_report(doc["analysis"], doc["paragraphs"])
        # 三类都要进来，不能只看逐字：
        # 漏台词和臆造一样违反原则二；人物没登记则会让资产聚合丢掉出场。
        need = bool(v0["problems"])
        if not args.no_fill:
            need = need or c0["dialogue_coverage"] < 1.0 or has_unregistered(doc)
        if not need:
            continue
        before_bad += len(v0["problems"])

        fixed, log = repair_chapter(json.loads(json.dumps(doc)))
        if not args.no_fill:
            fd = fill_missing_dialogue(fixed)
            fc = fill_missing_characters(fixed)
            if fd:
                log.append({"field": "dialogues", "kind": "补漏台词",
                            "before": f"（原文有但未抽取 {len(fd)} 条）",
                            "after": [x["text"] for x in fd]})
            if fc:
                log.append({"field": "characters", "kind": "补登记人物",
                            "before": "（说了话但不在人物名单）", "after": fc})
        if not log:
            after_bad += len(v0["problems"])
            continue
        v1 = verbatim.check_analysis(fixed["analysis"], fixed["paragraphs"])

        # 没变好就整章回滚。宁可不修，也不能写坏数据。
        if v1["verbatim_rate"] < v0["verbatim_rate"] or v1["counts"]["miss"] > v0["counts"]["miss"]:
            reverted.append(seq)
            after_bad += len(v0["problems"])
            continue

        c1 = verbatim.coverage_report(fixed["analysis"], fixed["paragraphs"])
        after_bad += len(v1["problems"])
        total_fix += len(log)
        touched += 1
        all_log.append({"seq": seq, "before_rate": v0["verbatim_rate"],
                        "after_rate": v1["verbatim_rate"],
                        "before_coverage": c0["dialogue_coverage"],
                        "after_coverage": c1["dialogue_coverage"], "fixes": log})
        print(f"seq{seq:4d} 逐字 {v0['verbatim_rate']:6.1%}→{v1['verbatim_rate']:6.1%}"
              f"  台词覆盖 {c0['dialogue_coverage']:6.1%}→{c1['dialogue_coverage']:6.1%}"
              f"  修 {len(log)} 处")
        if not args.dry_run:
            # 修完整块重算 quality（逐字、覆盖、passed、fail_reasons），别让它与正文对不上。
            # 早先只回写 verbatim，留下 374 份过期的 coverage 与 31 份该翻绿的 passed。
            quality.refresh(fixed)
            fixed["quality"]["repaired_by"] = "s5_repair_quotes"
            write_json(p, fixed)

    print(f"\n{'（dry-run，未落盘）' if args.dry_run else ''}"
          f"涉及 {touched} 章，修复 {total_fix} 处引用，"
          f"问题条数 {before_bad} → {after_bad}")
    if reverted:
        print(f"⚠ {len(reverted)} 章修复后未变好，已整章回滚：{reverted}")
    if all_log and not args.dry_run:
        out = paths.QUOTE_REPAIR_LOG
        write_json(out, all_log)
        print(f"修改留痕：{out.relative_to(paths.ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
