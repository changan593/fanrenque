#!/usr/bin/env python3
"""
步骤 14：旁白承载账的**程序化闸门**。不调 API。

`doc/05_唐假旁白系统.md` 5.4 立了一条规矩：
**每一条关键旁白都必须在剧本里落到一个承载上，账上缺一条 = 漏一条。**
这一步就是那句「可以程序化检查，不靠人自觉」的兑现。

## 它检查什么

1. `analysis.narration`（关键旁白，受原则二保护）的每一条，
   在剧本里**有且只有一个**承载标记
2. 没有重复承载
3. 删除率（N6 条数 ÷ 关键旁白条数），超过 15% 报警——判据用松了
4. 唐假开口条数与**世界静止次数**（`doc/05` 5.7 的第二道闸门）
5. 顺带报出剧本里**不属于关键旁白的 `【白】`**——
   那些是环境／动作白描，按新口径绝大多数该归画面

## 剧本里的标记约定

| 标记 | 含义 | 对应 |
| --- | --- | --- |
| `【白】` | 唐假画外音，世界静止 | N1 / N5 |
| `【卡】` | 字幕卡，**不冻结世界** | N5 |
| `【画】` | 画面承载，无声 | N2 / N3 / N4 |
| `【删】` | 建议删除，**必须写「被…覆盖」** | N6 |
| `【心·某某】` | 角色内心音 | 不变 |

`【画】` 与 `【删】` 后面要跟被承载的旁白原文（可用「节选」），
程序靠这段文字把它和 `analysis.narration` 对上号。

用法：
    python pipeline/s14_narration_ledger.py --episode S01E01
    python pipeline/s14_narration_ledger.py --script production/s01/E01_剧本.md --seq 1-4
"""
import argparse
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import paths
from common.jsonio import read_json

MARKS = {"白": "唐假", "卡": "字幕卡", "画": "画面", "删": "建议删除"}
DELETE_RATE_ALARM = 0.15
FREEZE_MAX = 6                    # doc/05 5.7：每集世界静止次数上限


def norm(s: str) -> str:
    """把剧本里的标注噪声剥掉，只留可比对的正文。"""
    s = re.sub(r"★[^<|]*", "", s)          # 编者按
    s = re.sub(r"（[^）]*）", "", s)        # 圆括号注
    s = re.sub(r"\[[^\]]*\]", "", s)       # [段号] / [24 节选]
    return re.sub(r"[\s　。，、！？；：「」『』…·]", "", s)


def parse_script(path: Path):
    """返回 [(镜号, 标记, 正文)]，镜号可能重复，所以用列表不用字典。"""
    out = []
    for shot, line in re.findall(r"^\|\s*(\d{3})\s*\|([^\n]*)$", path.read_text(encoding="utf-8"), re.M):
        for mark, body in re.findall(r"【(白|卡|画|删)】([^<|【]*)", line):
            out.append((shot, mark, body.strip()))
        for who, body in re.findall(r"【心·([^】]+)】([^<|【]*)", line):
            out.append((shot, "心", body.strip()))
    return out


def load_narration(lo: int, hi: int):
    rows = []
    for seq in range(lo, hi + 1):
        p = paths.chapter_json_path(seq)
        if not p.exists():
            continue
        for x in (read_json(p).get("analysis") or {}).get("narration") or []:
            rows.append({"seq": seq, "para": x["para"],
                         "function": x.get("function", ""), "text": x["text"]})
    return rows


def match(nar_text: str, marks) -> list:
    """一条关键旁白落在哪些标记上。用双向包含匹配，容忍剧本里的「节选」。"""
    nt = norm(nar_text)
    hits = []
    for shot, mark, body in marks:
        b = norm(body)
        if len(b) < 5 or len(nt) < 5:
            continue
        if nt in b or b in nt:
            hits.append((shot, mark, body))
    return hits


def main() -> int:
    ap = argparse.ArgumentParser(description="旁白承载账与闸门检查")
    ap.add_argument("--episode", help="集号，如 S01E01。自动查 seq 区间与剧本路径")
    ap.add_argument("--script", type=Path, help="剧本 md 路径")
    ap.add_argument("--seq", help="seq 区间，如 1-4")
    ap.add_argument("--out", type=Path, help="把账写成 md")
    args = ap.parse_args()

    if args.episode:
        eps = read_json(paths.PLOT_DIR / "episodes.json")["episodes"]
        e = next((x for x in eps if x["code"] == args.episode), None)
        if not e:
            raise SystemExit(f"episodes.json 里没有 {args.episode}")
        lo, hi = e["seq_start"], e["seq_end"]
        season = args.episode[1:3]
        script = args.script or (paths.PRODUCTION_DIR / f"s{season}" /
                                 f"E{args.episode[-2:]}_剧本.md")
    else:
        if not (args.script and args.seq):
            raise SystemExit("要么给 --episode，要么给 --script 加 --seq")
        lo, hi = (int(x) for x in args.seq.split("-"))
        script = args.script

    if not script.exists():
        raise SystemExit(f"找不到剧本 {script}")

    nar = load_narration(lo, hi)
    marks = parse_script(script)
    by_mark = Counter(m for _, m, _ in marks)

    print(f"剧本 {script.relative_to(paths.ROOT)}　seq {lo}~{hi}")
    print(f"关键旁白 {len(nar)} 条（analysis.narration，受原则二保护）")
    print(f"剧本标记：" + "　".join(
        f"{MARKS.get(k, '内心音')} {v}" for k, v in sorted(by_mark.items())))

    rows, unaccounted, multi = [], [], []
    for n in nar:
        hits = match(n["text"], marks)
        kinds = {m for _, m, _ in hits}
        if not hits:
            unaccounted.append(n)
            disp = "—"
        else:
            if len({(s, m) for s, m, _ in hits}) > 1 and kinds - {"心"}:
                multi.append((n, hits))
            disp = "／".join(sorted(MARKS.get(k, "内心音") for k in kinds))
        rows.append({**n, "hits": hits, "disposition": disp})

    # 剧本里给了【白】但不属于关键旁白的——白描，按新口径多数该归画面
    plain = []
    for shot, mark, body in marks:
        if mark != "白":
            continue
        if not any(norm(body) and (norm(body) in norm(n["text"]) or norm(n["text"]) in norm(body))
                   for n in nar if len(norm(n["text"])) > 4):
            plain.append((shot, body))

    voiced = sum(1 for r in rows if "唐假" in r["disposition"])
    deleted = sum(1 for r in rows if "建议删除" in r["disposition"])
    rate = deleted / len(nar) if nar else 0
    freeze = voiced + len(plain)          # 每条【白】都要冻一次世界

    print(f"\n── 闸门 ──")
    ok = True
    def gate(name, cond, detail=""):
        nonlocal ok
        print(f"  {'✓' if cond else '✗'} {name}" + (f"　{detail}" if detail else ""))
        ok = ok and cond
    gate("关键旁白全部上账", not unaccounted,
         "" if not unaccounted else f"缺 {len(unaccounted)} 条")
    gate("无重复承载", not multi, "" if not multi else f"{len(multi)} 条落在多个标记上")
    gate(f"删除率 ≤ {DELETE_RATE_ALARM:.0%}", rate <= DELETE_RATE_ALARM,
         f"{deleted}/{len(nar)} = {rate:.0%}")
    gate(f"世界静止 ≤ {FREEZE_MAX} 次", freeze <= FREEZE_MAX,
         f"当前 {freeze} 次（唐假 {voiced} + 白描 {len(plain)}）")

    if unaccounted:
        print(f"\n── 未上账的关键旁白（{len(unaccounted)} 条，按原则二这就是漏）──")
        for n in unaccounted:
            print(f"  seq{n['seq']}[{n['para']}] ({n['function']}) {n['text'][:46]}")
    if plain:
        print(f"\n── 剧本给了唐假、但不属于关键旁白的白描（{len(plain)} 处）──")
        print("  按新口径这些绝大多数该改成【画】：")
        for shot, body in plain[:12]:
            print(f"  镜{shot}  {body[:44]}")
        if len(plain) > 12:
            print(f"  …… 另有 {len(plain) - 12} 处")

    if args.out:
        md = [f"# {args.episode or script.stem} 旁白承载账\n",
              f"由 `pipeline/s14_narration_ledger.py` 生成，**不要手改**——",
              f"要改就去改剧本里的标记，然后重跑。\n",
              f"关键旁白 {len(nar)} 条｜唐假 {voiced}｜建议删除 {deleted}"
              f"（{rate:.0%}）｜白描待改判 {len(plain)} 处\n",
              "| seq[段] | 功能 | 旁白 | 处置 | 镜号 |",
              "| --- | --- | --- | --- | --- |"]
        for r in rows:
            shots = "、".join(sorted({s for s, _, _ in r["hits"]})) or "**未上账**"
            md.append(f"| seq{r['seq']}[{r['para']}] | {r['function']} |"
                      f" {r['text'][:60]} | {r['disposition']} | {shots} |")
        args.out.write_text("\n".join(md) + "\n", encoding="utf-8")
        print(f"\n{args.out.relative_to(paths.ROOT)}")

    print()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
