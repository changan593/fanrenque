#!/usr/bin/env python3
"""
s17 —— 原文引用闸门

原则二（严格遵守原文）此前只有 s3 在管**章节分析**那一层：
`s2_analyze_chapters.numbered_text` 用 `enumerate(paragraphs, 1)` 把正文喂给模型，
所以全项目所有 `seqN[i]` 段号**都是 1 基**。

但人工写的角色卡、场景卡、幕文档里的 `seqN[i]` 从来没有被机器查过。
本闸门补上这一段：**凡是一行里同时出现 `seqN[i]` 和 `【原】「…」`，
就把那段引文拿去和 `source/novel.json` 的第 i 段（1 基）逐字比对。**

它抓到过的真问题：
  · `s8_character_dossier.py` 曾用 `enumerate(paras)` 输出 0 基段号，
    照着卷宗写卡的人会把整张卡的段号写成偏移 1 的值（已修，见 s8 第 249 行注释）。

判定：
  命中     —— 引文在第 i 段里（1 基），正确
  差一位   —— 引文在第 i-1 或 i+1 段里，**几乎一定是基准搞错了**
  找不到   —— 前后 window 段内都没有这句话，要么段号错得多，要么引文被改写过

用法：
  python pipeline/s17_citation_check.py
  python pipeline/s17_citation_check.py --path production/characters
  python pipeline/s17_citation_check.py --window 6 --show-ok
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import paths  # noqa: E402

CITE = re.compile(r"seq(\d+)\[(\d+)(?:[-–](\d+))?\]")
QUOTE = re.compile(r"「([^」]+)」")
PUNCT = str.maketrans("", "", "　 \t\n．，、。！？；：（）()《》〈〉「」『』…—-·\"'“”‘’")


def norm(s: str) -> str:
    """归一化：去空白与标点，全角括号与半角等价。与 doc/02 第二节同一套判据。"""
    return s.translate(PUNCT)


def load_paragraphs() -> dict[int, list[str]]:
    nov = json.loads((paths.ROOT / "source" / "novel.json").read_text(encoding="utf-8"))
    chapters = nov["chapters"] if isinstance(nov, dict) else nov
    return {c["seq"]: (c.get("paragraphs") or c.get("content", "").split("\n"))
            for c in chapters}


def quotes_on(line: str) -> list[str]:
    """取这一行里**被标成原文**的引文。

    只认 `【原】` 之后的 `「…」`。全项目的取证表都用这个标记区分
    「【原】逐字引用」与「【补】推导补全」（见 `doc/04_风格规范.md` 第五节），
    所以它是唯一可靠的判据。

    不这么收窄的话，误报会淹掉真问题：负面清单词（「多余手指／畸形手」）、
    文档自己的说明（「整条正文就是另一条旁白」）都写在带段号的行里，
    但它们根本不是原文。
    """
    if "【原】" not in line:
        return []
    tail = line.split("【原】", 1)[1]
    out = []
    for q in QUOTE.findall(tail):
        q = q.replace("**", "").replace("★", "").strip()
        if len(norm(q)) >= 6:
            out.append(q)
    return out


def locate(paras: list[str], quote: str, idx1: int, window: int) -> int | None:
    """在 idx1（1 基）附近找这句引文，返回命中的 1 基段号；找不到返回 None。

    引文里写了省略号（卡里常见「前半句…后半句」）时只比对省略号之前那一段——
    省略号本来就代表「这里跳过了原文」，整串拿去比对必然对不上。
    """
    head = re.split(r"…|\.\.\.|。。。", quote)[0]
    if len(norm(head)) >= 6:
        quote = head
    n = norm(quote)
    order = [idx1] + [idx1 + d for k in range(1, window + 1) for d in (-k, k)]
    for i in order:
        if 1 <= i <= len(paras) and n in norm(paras[i - 1]):
            return i
    return None


def build_index(paras: dict[int, list[str]]) -> list[tuple[int, int, str]]:
    """全书归一化索引，用来回答「这句话到底在哪」。"""
    return [(seq, i + 1, norm(p))
            for seq, ps in paras.items() for i, p in enumerate(ps)]


def whereis(index, quote: str) -> list[tuple[int, int]]:
    """这句引文在全书出现在哪些段。省略号之前那一段即可（同 locate）。"""
    head = re.split(r"…|\.\.\.|。。。", quote)[0]
    n = norm(head) if len(norm(head)) >= 6 else norm(quote)
    if len(n) < 6:
        return []
    return [(s, i) for s, i, p in index if n in p]


def main() -> int:
    ap = argparse.ArgumentParser(description="原文引用闸门：段号与引文逐字核对")
    ap.add_argument("--path", type=Path, default=None,
                    help="只查这个目录或文件（默认查 production/ 与 doc/）")
    ap.add_argument("--window", type=int, default=4, help="前后找多少段（默认 4）")
    ap.add_argument("--show-ok", action="store_true", help="把命中的也列出来")
    ap.add_argument("--relocate", action="store_true",
                    help="一行只有一条引用、一条【原】引文，且该引文全书只出现一次时，"
                         "把段号改成它真正所在的 seqN[i]。多引用／多引文的行一律不动。")
    ap.add_argument("--fix", action="store_true",
                    help="只改「差一位」那一类（已逐字核实过落点），其余一律不动")
    args = ap.parse_args()

    print("=" * 62)
    print("原文引用闸门　段号 1 基（doc/02 第二节；s2 numbered_text）")
    print("=" * 62)

    paras = load_paragraphs()
    roots = ([args.path.resolve()] if args.path
             else [paths.PRODUCTION_DIR, paths.ROOT / "doc"])
    files: list[Path] = []
    for r in roots:
        files += [r] if r.is_file() else sorted(r.rglob("*.md"))

    index = build_index(paras) if args.relocate else None
    ok = off_by_one = missing = no_seq = 0
    problems: list[str] = []
    fixes: dict[Path, list[tuple[int, str, str]]] = {}
    relocs: dict[Path, list[tuple[int, str, str]]] = {}
    for f in sorted(set(files)):
        rel = str(f.relative_to(paths.ROOT))
        for lineno, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            cites = CITE.findall(line)
            qs = quotes_on(line)
            if not cites or not qs:
                continue
            # 一行里可能写了好几个段号、好几条引文（取证表常见）。
            # 判据是**每条引文都要能在这一行给出的某个段号处找到**，
            # 而不是「每个段号都要有引文对上」——后者会把
            # 「两个段号 + 一条够长的引文」这种正常写法误报成错。
            spans = []
            for seq_s, i_s, j_s in cites:
                seq, i0 = int(seq_s), int(i_s)
                i1 = int(j_s) if j_s else i0
                spans.append((seq, i0, i1))
            for q in qs:
                hit = None
                for seq, i0, i1 in spans:
                    ps = paras.get(seq)
                    if ps is None:
                        continue
                    for i in range(i0, i1 + 1):
                        if locate(ps, q, i, 0) is not None:
                            hit = (seq, i)
                            break
                    if hit:
                        break
                if hit:
                    ok += 1
                    if args.show_ok:
                        print(f"  ✓ {rel}:{lineno}  seq{hit[0]}[{hit[1]}]")
                    continue
                # 没直接命中：看看是不是就差一位
                near = None
                for seq, i0, i1 in spans:
                    ps = paras.get(seq)
                    if ps is None:
                        continue
                    for i in range(i0, i1 + 1):
                        j = locate(ps, q, i, args.window)
                        if j is not None:
                            near = (seq, i, j)
                            break
                    if near:
                        break
                if near and abs(near[2] - near[1]) == 1:
                    off_by_one += 1
                    old, new = f"seq{near[0]}[{near[1]}]", f"seq{near[0]}[{near[2]}]"
                    problems.append(f"{rel}:{lineno}  {old} → 应为 {new}"
                                    f"　（差一位，多半是把 0 基当成了 1 基）")
                    fixes.setdefault(f, []).append((lineno, old, new))
                    continue
                missing += 1
                note = ""
                if near:
                    note = f"　→ 同章 [{near[2]}]"
                    if len(cites) == 1 and len(qs) == 1:
                        relocs.setdefault(f, []).append(
                            (lineno, f"seq{near[0]}[{near[1]}]",
                             f"seq{near[0]}[{near[2]}]"))
                elif index is not None:
                    loc = whereis(index, q)
                    if len(loc) == 1:
                        note = f"　→ 全书唯一落点 seq{loc[0][0]}[{loc[0][1]}]"
                        if len(cites) == 1 and len(qs) == 1:
                            relocs.setdefault(f, []).append(
                                (lineno, f"seq{spans[0][0]}[{spans[0][1]}]",
                                 f"seq{loc[0][0]}[{loc[0][1]}]"))
                    elif not loc:
                        note = "　→ 全书查无此句（引文被改写过？）"
                    else:
                        note = f"　→ 全书 {len(loc)} 处，需人工选"
                cite_txt = "／".join(f"seq{s}[{i}]" if i == j else f"seq{s}[{i}-{j}]"
                                    for s, i, j in spans)
                problems.append(f"{rel}:{lineno}  {cite_txt} 找不到"
                                f"「{q[:24]}」{note}")

    if args.relocate and relocs:
        n = 0
        for f, items in relocs.items():
            lines = f.read_text(encoding="utf-8").splitlines(keepends=True)
            for lineno, old, new in items:
                if old in lines[lineno - 1]:
                    lines[lineno - 1] = lines[lineno - 1].replace(old, new)
                    n += 1
            f.write_text("".join(lines), encoding="utf-8")
        print(f"\n✎ 已重定位 {n} 处（每处都是「一行一引用一引文、全书唯一落点」）")

    if args.fix and fixes:
        n = 0
        for f, items in fixes.items():
            lines = f.read_text(encoding="utf-8").splitlines(keepends=True)
            for lineno, old, new in items:
                line = lines[lineno - 1]
                # ★ 不碰 `seq7[2][33]`（一行挂两段）和 `seq3[18-19]`（区间）这两种写法：
                #   只替换 `seqN[i]` 后面**不再接 `[` 或 `-`** 的那种独立记号。
                if re.search(re.escape(old) + r"(?![\[\-–])", line):
                    lines[lineno - 1] = re.sub(
                        re.escape(old) + r"(?![\[\-–])", new, line, count=1)
                    n += 1
            f.write_text("".join(lines), encoding="utf-8")
        print(f"\n✎ 已改 {n} 处「差一位」（每一处的落点都逐字核实过，"
              f"且跳过 seqN[i][j] 与区间写法）")
        print("  「对不上」那一类没有动——那需要人回原文重新定位。\n")

    if problems:
        print(f"\n🔴 {len(problems)} 处引用对不上：\n")
        for p in problems[:60]:
            print(f"  {p}")
        if len(problems) > 60:
            print(f"  …… 另有 {len(problems) - 60} 处")

    print("-" * 62)
    print(f"可核对的引用 {ok + off_by_one + missing + no_seq} 条｜"
          f"命中 {ok}｜差一位 {off_by_one}｜对不上 {missing + no_seq}")

    if problems:
        print("\n改法：段号一律 1 基。差一位的直接 +1／-1；")
        print("      对不上的回 source/novel.json 重新定位，不要凭印象改引文。")
        return 1
    print("\n✓ 全绿")
    return 0


if __name__ == "__main__":
    sys.exit(main())
