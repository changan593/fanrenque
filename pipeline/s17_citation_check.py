#!/usr/bin/env python3
"""
步骤 17：原文引用闸门。不调 API。

原则二（严格遵守原文）此前只有 s3 在管**章节分析**那一层。人工写的角色卡、场景卡、
幕文档里的 `seqN[i]` 从来没被机器查过——本闸门补上：**凡是一行里同时出现段号引用
和 `【原】「…」`，就把引文拿去和 `source/novel.json` 的对应段逐字比对。**

记法与比对规则在 `common/cite.py`（段号 1 基；`seq2[1][18]` 一行挂两段；
`seq3[18-19]` 区间；引文里的省略号表示跳过原文，按片段顺序比对）。

判定：
  命中     引文逐字落在本行给出的某个段号里
  差一位   只在第 i±1 段找到——几乎一定是基准搞错了（把 0 基当成了 1 基）
  对不上   前后 window 段内都没有，要么段号错得多，要么引文被改写过

它抓到过的真问题：`s8_character_dossier.py` 曾用 `enumerate(paras)` 输出 0 基段号，
照着卷宗写卡的人会把整张卡的段号写成偏移 1 的值（已修）。

用法：
  python pipeline/s17_citation_check.py                       # 全量：production/ 与 doc/
  python pipeline/s17_citation_check.py --path production/characters
  python pipeline/s17_citation_check.py --fix                 # 只改「差一位」
  python pipeline/s17_citation_check.py --relocate            # 引文全书唯一落点时改段号
  python pipeline/s17_citation_check.py --show-ok
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import cite, paths  # noqa: E402
from common.novel import load_novel  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="原文引用闸门：段号与引文逐字核对")
    ap.add_argument("--path", type=Path, default=None,
                    help="只查这个目录或文件（默认查 production/ 与 doc/）")
    ap.add_argument("--window", type=int, default=4, help="前后找多少段（默认 4）")
    ap.add_argument("--show-ok", action="store_true", help="把命中的也列出来")
    ap.add_argument("--relocate", action="store_true",
                    help="一行只有一个段号、一条【原】引文，且该引文全书只出现一次时，"
                         "把段号改成它真正所在的 seqN[i]。多段号／多引文的行一律不动。")
    ap.add_argument("--fix", action="store_true",
                    help="只改「差一位」那一类（已逐字核实过落点），其余一律不动")
    args = ap.parse_args()

    print("=" * 62)
    print("原文引用闸门　段号 1 基（doc/02 第二节；s2 numbered_text）")
    print("=" * 62)

    chapters = {c["seq"]: c["paragraphs"] for c in load_novel()["chapters"]}
    roots = ([args.path.resolve()] if args.path
             else [paths.PRODUCTION_DIR, paths.DOC_DIR])
    files: list[Path] = []
    for r in roots:
        files += [r] if r.is_file() else sorted(r.rglob("*.md"))

    ok = off_by_one = missing = 0
    problems: list[str] = []
    fixes: dict[Path, list[tuple[int, str, str]]] = {}
    relocs: dict[Path, list[tuple[int, str, str]]] = {}
    for f in sorted(set(files)):
        rel = str(f.relative_to(paths.ROOT))
        for lineno, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            spans = cite.parse_cites(line)
            qs = cite.quotes_after_marker(line)
            if not spans or not qs:
                continue
            # 一行里可能写了好几个段号、好几条引文（取证表常见）。
            # 判据是**每条引文都要能在这一行给出的某个段号处找到**，
            # 而不是「每个段号都要有引文对上」——后者会把
            # 「两个段号 + 一条够长的引文」这种正常写法误报成错。
            for q in qs:
                hit = None
                for seq, i0, i1 in spans:
                    ps = chapters.get(seq)
                    if ps is None:
                        continue
                    for i in range(i0, i1 + 1):
                        if cite.locate(ps, q, i, 0) is not None:
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
                    ps = chapters.get(seq)
                    if ps is None:
                        continue
                    for i in range(i0, i1 + 1):
                        j = cite.locate(ps, q, i, args.window)
                        if j is not None:
                            near = (seq, i, j)
                            break
                    if near:
                        break
                cite_txt = "／".join(f"seq{s}[{i}]" if i == j else f"seq{s}[{i}-{j}]"
                                    for s, i, j in spans)
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
                    if len(spans) == 1 and len(qs) == 1:
                        relocs.setdefault(f, []).append(
                            (lineno, f"seq{near[0]}[{near[1]}]", f"seq{near[0]}[{near[2]}]"))
                elif args.relocate:
                    loc = cite.find_anywhere(q, chapters.items())
                    if len(loc) == 1:
                        note = f"　→ 全书唯一落点 seq{loc[0][0]}[{loc[0][1]}]"
                        if len(spans) == 1 and len(qs) == 1:
                            relocs.setdefault(f, []).append(
                                (lineno, f"seq{spans[0][0]}[{spans[0][1]}]",
                                 f"seq{loc[0][0]}[{loc[0][1]}]"))
                    elif not loc:
                        note = "　→ 全书查无此句（引文被改写过？）"
                    else:
                        note = f"　→ 全书 {len(loc)} 处，需人工选"
                problems.append(f"{rel}:{lineno}  {cite_txt} 找不到「{q[:24]}」{note}")

    def _rewrite(table: dict[Path, list[tuple[int, str, str]]], label: str) -> None:
        n = 0
        for f, items in table.items():
            lines = f.read_text(encoding="utf-8").splitlines(keepends=True)
            for lineno, old, new in items:
                # 只替换独立的 `seqN[i]`：后面不再接 `[`（多段）或 `-`（区间）。
                pat = re.escape(old) + r"(?![\[\-–])"
                if re.search(pat, lines[lineno - 1]):
                    lines[lineno - 1] = re.sub(pat, new, lines[lineno - 1], count=1)
                    n += 1
            f.write_text("".join(lines), encoding="utf-8")
        print(f"\n✎ 已{label} {n} 处")

    if args.relocate and relocs:
        _rewrite(relocs, "重定位")
    if args.fix and fixes:
        _rewrite(fixes, "改「差一位」")
        print("  「对不上」那一类没有动——那需要人回原文重新定位。\n")

    if problems:
        print(f"\n🔴 {len(problems)} 处引用对不上：\n")
        for p in problems[:60]:
            print(f"  {p}")
        if len(problems) > 60:
            print(f"  …… 另有 {len(problems) - 60} 处")

    print("-" * 62)
    print(f"可核对的引用 {ok + off_by_one + missing} 条｜"
          f"命中 {ok}｜差一位 {off_by_one}｜对不上 {missing}")

    if problems:
        print("\n改法：段号一律 1 基。差一位的直接 +1／-1；")
        print("      对不上的回 source/novel.json 重新定位，不要凭印象改引文。")
        return 1
    print("\n✓ 全绿")
    return 0


if __name__ == "__main__":
    sys.exit(main())
