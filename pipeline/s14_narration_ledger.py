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
4. **现身次数**（只有现身档冻结世界，`doc/05` 5.7）
5. 顺带报出剧本里**不属于关键旁白的 `【白】`**——
   那些是环境／动作白描，按新口径绝大多数该归画面

## 剧本里的标记约定

| 标记 | 含义 | 对应 |
| --- | --- | --- |
| `【现】` | 唐假**现身**，世界静止 | N1 / N5 里需要观众停下来听的 |
| `【白】` | 唐假**只出声**，画面照常流动 | N1 / N5 |
| `【卡】` | 字幕卡，**不冻结世界** | N5 |
| `【画】` | 画面承载，无声 | N2 / N3 / N4 |
| `【删】` | 建议删除，**必须写「被…覆盖」** | N6 |
| `【心·某某】` | 角色内心音 | 不变 |

`【画】` 与 `【删】` 后面要跟被承载的旁白原文（可用「节选」），
程序靠这段文字把它和 `analysis.narration` 对上号。
`※` 后面写保留理由（白描留声时必填），`★` 后面是导演备注，两者都不参与比对。

**混合条允许拆开承载**（`doc/05` 5.3），但拆出来的几段必须在**同一个镜头**里，
且拼回去要**逐字等于原句**——程序会验这一条，蒸发一个字就报错。

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

MARKS = {"现": "唐假现身", "白": "唐假声音", "卡": "字幕卡",
         "画": "画面", "删": "建议删除"}
DELETE_RATE_ALARM = 0.15
FREEZE_MAX = 6                    # doc/05 5.7：每集世界静止次数上限


def norm(s: str) -> str:
    """把剧本里的标注噪声剥掉，只留可比对的正文。"""
    s = re.split(r"[★※←]", s)[0]           # 编者按、保留理由、覆盖说明
    s = re.sub(r"（[^）]*）", "", s)        # 圆括号注
    s = re.sub(r"\[[^\]]*\]", "", s)       # [段号] / [24 节选]
    # 引号必须全剥：原文用 ‘’ 而剧本常常不带，只差这一对就匹配不上
    return re.sub(r"[\s　。，、！？；：…·「」『』‘’“”\"'《》〈〉（）()]", "", s)


def parse_script(path: Path):
    """返回 [(镜号, 标记, 正文)]，镜号可能重复，所以用列表不用字典。"""
    out = []
    for shot, line in re.findall(r"^\|\s*(\d{3})\s*\|([^\n]*)$", path.read_text(encoding="utf-8"), re.M):
        for mark, body in re.findall(r"【(现|白|卡|画|删)】([^<|【]*)", line):
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
    nt, raw = norm(nar_text), nar_text.strip()
    hits = []
    for shot, mark, body in marks:
        b = norm(body)
        if len(nt) < 5 or len(b) < 5:
            # 「。。。」这类，正规化后是空串，退回原文比对
            if raw and raw in body:
                hits.append((shot, mark, body))
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

    print(f"剧本 {script.resolve().relative_to(paths.ROOT)}　seq {lo}~{hi}")
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
            spots = {(s, m) for s, m, _ in hits}
            if len(spots) > 1 and kinds - {"心"}:
                # 落在多处不一定是错——doc/05 5.3 允许「混合条拆条承载」。
                # 判据：必须都在同一个镜头里，且拆出来的几段拼起来要
                # 覆盖原句全部信息，一个字都不能在拆的过程中蒸发。
                one_shot = len({s for s, _, _ in hits}) == 1
                joined = "".join(norm(b) for _, _, b in hits)
                if not (one_shot and joined == norm(n["text"])):
                    multi.append((n, hits))
            disp = "／".join(sorted(MARKS.get(k, "内心音") for k in kinds))
        rows.append({**n, "hits": hits, "disposition": disp})

    # 剧本里给了【白】但不属于关键旁白的——白描，按新口径多数该归画面
    carried = {(s, m, b) for n in nar for s, m, b in match(n["text"], marks)}
    plain = [(shot, body, "※" in body) for shot, mark, body in marks
             if mark in ("白", "现") and (shot, mark, body) not in carried]

    voiced = sum(1 for r in rows if "唐假" in r["disposition"])
    deleted = sum(1 for r in rows if "建议删除" in r["disposition"])
    rate = deleted / len(nar) if nar else 0
    # 只有现身档冻结世界。声音档他只出声不出现，画面照常流动。
    # 见 production/s01/E01_剧本.md 附二与 doc/05 5.7。
    freeze = by_mark.get("现", 0)

    print(f"\n── 闸门 ──")
    ok = True
    def gate(name, cond, detail=""):
        nonlocal ok
        print(f"  {'✓' if cond else '✗'} {name}" + (f"　{detail}" if detail else ""))
        ok = ok and cond
    gate("关键旁白全部上账", not unaccounted,
         "" if not unaccounted else f"缺 {len(unaccounted)} 条")
    gate("无重复承载；拆条的拼回去等于原句", not multi,
         "" if not multi else f"{len(multi)} 条不合格")
    gate(f"删除率 ≤ {DELETE_RATE_ALARM:.0%}", rate <= DELETE_RATE_ALARM,
         f"{deleted}/{len(nar)} = {rate:.0%}")
    gate(f"现身档（世界静止）≤ {FREEZE_MAX} 次", freeze <= FREEZE_MAX,
         f"当前 {freeze} 次")
    unjust = [x for x in plain if not x[2]]
    gate("派给唐假的白描都写了保留理由（※）", not unjust,
         f"{len(plain)} 处白描留声，其中 {len(unjust)} 处没写理由"
         if plain else "无白描留声")

    if multi:
        print(f"\n── 承载有问题的（{len(multi)} 条）──")
        for n, hits in multi:
            print(f"  seq{n['seq']}[{n['para']}] {n['text'][:34]}")
            for s_, m_, b_ in hits:
                print(f"      镜{s_}【{MARKS.get(m_, '内心音')}】{b_[:40]}")
    if unaccounted:
        print(f"\n── 未上账的关键旁白（{len(unaccounted)} 条，按原则二这就是漏）──")
        for n in unaccounted:
            print(f"  seq{n['seq']}[{n['para']}] ({n['function']}) {n['text'][:46]}")
    if plain:
        print(f"\n── 派给唐假的白描（{len(plain)} 处，{len(unjust)} 处缺理由）──")
        for shot, body, ok_ in plain[:12]:
            print(f"  {'✓' if ok_ else '✗'} 镜{shot}  {body[:44]}")
        if len(plain) > 12:
            print(f"  …… 另有 {len(plain) - 12} 处")

    if args.out:
        md = [f"# {args.episode or script.stem} 旁白承载账\n",
              f"由 `pipeline/s14_narration_ledger.py` 生成，**不要手改**——",
              f"要改就去改剧本里的标记，然后重跑。\n",
              f"关键旁白 {len(nar)} 条｜唐假 {voiced}｜建议删除 {deleted}"
              f"（{rate:.0%}）｜白描留声 {len(plain)} 处\n",
              "| seq[段] | 功能 | 旁白 | 处置 | 镜号 |",
              "| --- | --- | --- | --- | --- |"]
        for r in rows:
            shots = "、".join(sorted({s for s, _, _ in r["hits"]})) or "**未上账**"
            md.append(f"| seq{r['seq']}[{r['para']}] | {r['function']} |"
                      f" {r['text'][:60]} | {r['disposition']} | {shots} |")
        args.out.write_text("\n".join(md) + "\n", encoding="utf-8")
        print(f"\n{args.out.resolve().relative_to(paths.ROOT)}")

    print()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
