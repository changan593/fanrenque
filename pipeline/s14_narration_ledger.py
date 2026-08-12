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
3. **台词与心理活动的覆盖**——按分句回剧本逐字找。这一项**只报告不拦**，
   因为「唐真如此评价自己」这类归属语自动判不出是漏还是已由结构表达
4. 删除率（N6 条数 ÷ 关键旁白条数），超过 15% 报警——判据用松了
5. **现身次数**（只有现身档冻结世界，`doc/05` 5.7）
6. 顺带报出剧本里**不属于关键旁白的 `【白】`**——
   那些是环境／动作白描，按新口径绝大多数该归画面

## 剧本里的标记约定

| 标记 | 含义 | 对应 |
| --- | --- | --- |
| `【现】` | 唐假**现身**，世界静止 | N1 / N5 里需要观众停下来听的 |
| `【白】` | 唐假**只出声**，画面照常流动 | N1 / N5 |
| `【卡】` | 字幕卡，**不冻结世界** | N5 |
| `【画】` | 画面承载，无声 | N2 / N3 / N4 |
| `【删】` | 建议删除，**必须写「被…覆盖」** | N6 |
| `【台】` | 抽取误判：这条其实是现场台词，已作台词呈现 | —— |
| `【心·某某】` | 角色内心音 | 不变 |

`【画】` 与 `【删】` 后面要跟被承载的旁白原文（可用「节选」），
程序靠这段文字把它和 `analysis.narration` 对上号。
`※` 后面写保留理由（白描留声时必填），`★` 后面是导演备注，两者都不参与比对。

**混合条允许拆开承载**（`doc/05` 5.3），但拆出来的几段必须落在**同一个或相邻镜头**，
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
         "画": "画面", "删": "建议删除", "台": "实为台词"}
DELETE_RATE_ALARM = 0.15
FREEZE_MAX = 6                    # doc/05 5.7：每集世界静止次数上限


def norm(s: str) -> str:
    """把剧本里的标注噪声剥掉，只留可比对的正文。"""
    s = re.split(r"[★※←]", s)[0]           # 编者按、保留理由、覆盖说明
    s = re.sub(r"（[^）]*）", "", s)        # 圆括号注
    s = re.sub(r"\[[^\]]*\]", "", s)       # [段号] / [24 节选]
    # 引号必须全剥：原文用 ‘’ 而剧本常常不带，只差这一对就匹配不上
    return re.sub(r"[\s　。，、！？；：…·「」『』‘’“”\"'《》〈〉（）()]", "", s)


def norm_all(s: str) -> str:
    """整份文件用的正规化。与 norm() 的区别：**逐处**剥掉 ★／※ 注释，
    而不是在第一个记号处整体截断——后者用在全文上会把剧本几乎全丢掉。"""
    s = re.sub(r"[★※][^<|\n]*", "", s)
    s = re.sub(r"（[^）]*）|\[[^\]]*\]", "", s)
    return re.sub(r"[\s　。，、！？；：…·「」『』‘’“”\"\'《》〈〉（）()]", "", s)


def parse_script(path: Path):
    """返回 [(镜号, 标记, 正文)]，镜号可能重复，所以用列表不用字典。

    **一次扫完一行，不分两轮**——拆条拼回去要按原句顺序，而顺序就是
    标记在单元格里出现的先后。分两轮收（先收 `【画】` 再收 `【心·】`）
    会把同一镜里「内心音 + 画面」这类混合拆条拼反，验不过 5.3 的等值检查。
    """
    out = []
    for shot, line in re.findall(r"^\|\s*\**(\d{3})\**\s*\|([^\n]*)$", path.read_text(encoding="utf-8"), re.M):
        for mark, body in re.findall(r"【(现|白|卡|画|删|台|心·[^】]+)】([^<|【]*)", line):
            out.append((shot, "心" if mark.startswith("心·") else mark, body.strip()))
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


def match(nar_text: str, marks, para: int | None = None,
          others: set[str] | None = None) -> list:
    """一条关键旁白落在哪些标记上。用双向包含匹配，容忍剧本里的「节选」。

    `others` 是**别的**关键旁白的正规化文本集合。一个正文如果整条就是另一条
    旁白，它就是那一条的承载，不是本条拆出来的片段——哪怕它字面上确实是
    本条的一截。同段里出现两条旁白、短的那条又原样嵌在长的那条里时
    （如 seq30[33] 的「七关。」与「开到第七层箱子，被叫做『七关』…」），
    没有这一条会把长的那条同时算到两个镜头上。
    """
    nt, raw = norm(nar_text), nar_text.strip()
    others = others or set()
    hits = []
    for shot, mark, body in marks:
        b = norm(body)
        # 段号写法有两种：`[24]` 和 `[24 节选]`。后缀要允许，但 `[240]` 不能算命中。
        tag = para is None or re.search(rf"\[{para}(?:\D[^\]]*)?\]", body) is not None
        if len(nt) < 5:
            # 旁白本身就短（「砰！」「。。。」），正文比对会命中一片，
            # 所以**必须同时对上段号**，否则无从区分。
            if raw and raw in body and tag:
                hits.append((shot, mark, body))
            continue
        if nt in b:
            # 整条落在这一镜里（正文可以更长，容忍「节选」以外的补字）
            hits.append((shot, mark, body))
        elif b and b in nt and b not in others:
            # 反向包含只可能是**拆条**拆出来的片段，而拆条片段一定带段号。
            # 不查段号的话，别条旁白里凑巧出现的同样几个字也会命中——
            # 例如「修整城隍庙」既是 seq22[1] 的一截，又原样出现在 seq22[27] 里。
            if tag:
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
        planned = e.get("minutes")
        season = args.episode[1:3]
        script = args.script or (paths.PRODUCTION_DIR / f"s{season}" /
                                 f"E{args.episode[-2:]}_剧本.md")
    else:
        if not (args.script and args.seq):
            raise SystemExit("要么给 --episode，要么给 --script 加 --seq")
        lo, hi = (int(x) for x in args.seq.split("-"))
        planned = None
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

    # 每条旁白「别的旁白」的正规化文本，供 match() 排除误判的拆条片段
    all_norms = [norm(n["text"]) for n in nar]

    rows, unaccounted, multi = [], [], []
    for i, n in enumerate(nar):
        others = {t for j, t in enumerate(all_norms) if j != i and t}
        hits = match(n["text"], marks, n["para"], others)
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
                # 真正的安全性来自「拼回去逐字等于原句」；镜号只要求**相邻**，
                # 因为「引出句 + 下一镜的字幕卡」是合法拆法。
                nums = sorted({int(s) for s, _, _ in hits})
                adjacent = all(b - a <= 1 for a, b in zip(nums, nums[1:]))
                joined = "".join(norm(b) for _, _, b in hits)
                if not (adjacent and joined == norm(n["text"])):
                    multi.append((n, hits))
            disp = "／".join(sorted(MARKS.get(k, "内心音") for k in kinds))
        rows.append({**n, "hits": hits, "disposition": disp})

    # 剧本里给了【白】但不属于关键旁白的——白描，按新口径多数该归画面
    carried = {(s, m, b)
               for i, n in enumerate(nar)
               for s, m, b in match(n["text"], marks, n["para"],
                                    {t for j, t in enumerate(all_norms) if j != i and t})}
    plain = [(shot, body, "※" in body) for shot, mark, body in marks
             if mark in ("白", "现") and (shot, mark, body) not in carried]

    # 导演备注跨 <br> 断行时忘了续写 ★／※ —— E05、E07、E08 各犯过一次。
    # 后果不是内容错，而是那半句备注**混进了正文**，逐字覆盖检查会把它当台词报出来。
    # 判据：一个 <br> 片段既没有任何标记，也不像台词（没有「某某：」冒号），
    # 而它所在的单元格里前面已经出现过 ★／※。
    raw_lines = script.read_text(encoding="utf-8").split("\n")
    orphan_notes = []
    for line in raw_lines:
        cells = line.split("|")
        if len(cells) < 5 or not re.fullmatch(r"\d{3}", cells[1].strip().strip("*")):
            continue
        seen_note = False
        for frag in cells[4].split("<br>"):
            f = frag.strip()
            if not f:
                continue
            if re.match(r"^[★※]", f):
                seen_note = True
                continue
            marked = f.startswith("【")
            speech = re.match(r"^[^：:【】]{1,14}：", f)
            if seen_note and not marked and not speech:
                orphan_notes.append((cells[1].strip(), f[:44]))
    if orphan_notes:
        print(f"\n── ⚠ 疑似跨 <br> 掉了 ★／※ 的备注（{len(orphan_notes)} 处）──")
        print("  这些片段会被当成正文参与逐字比对。补上记号即可。")
        for shot, frag in orphan_notes:
            print(f"  镜{shot}  {frag}")

    # 台词与心理活动：原则二对这两类没有口子，逐条回剧本正文找
    scene_text = norm_all(script.read_text(encoding="utf-8"))
    lost = {}
    for seq in range(lo, hi + 1):
        pth = paths.chapter_json_path(seq)
        if not pth.exists():
            continue
        a = read_json(pth).get("analysis") or {}
        for field in ("dialogues", "monologues"):
            for it in a.get(field) or []:
                # 按分句查，不按整段查。原文一段里常常「引号台词 + 叙述」混排，
                # 剧本会把它拆到画面栏与声音栏两处——那是合法的，不算漏。
                # 但每个分句都必须在剧本里找得到，少一句就是真漏。
                gone = [f for f in re.split(r"[。！？；，]", it["text"])
                        if len(norm_all(f)) >= 4 and norm_all(f) not in scene_text]
                if gone:
                    lost.setdefault(field, []).append(
                        (seq, it["para"], "／".join(g.strip() for g in gone)))

    voiced = sum(1 for r in rows if "唐假" in r["disposition"])
    deleted = sum(1 for r in rows if "建议删除" in r["disposition"])
    rate = deleted / len(nar) if nar else 0
    # 只有现身档冻结世界。声音档他只出声不出现，画面照常流动。
    # 见 production/s01/E01_剧本.md 附二与 doc/05 5.7。
    # 按**镜号**去重：一次静止里唐假连说两句，观众只被打断一次，算一次。
    freeze_shots = sorted({shot for shot, mark, _ in marks if mark == "现"})
    freeze = len(freeze_shots)

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

    # 时长核对：逐镜秒数之和 vs 幕标注之和 vs episodes.json 的规划分钟数。
    # 三者本该一致，只报告不拦——秒数是估算，不是硬约束。
    shot_sec, act_sec = 0, 0
    per_act: list[tuple[str, int]] = []
    for line in script.read_text(encoding="utf-8").split("\n"):
        if line.startswith("## "):
            per_act.append((line[3:].strip(), 0))
        cells = line.split("|")
        if len(cells) >= 5 and re.fullmatch(r"\d{3}", cells[1].strip().strip("*")):
            m = re.search(r"(\d+)s", cells[2])
            if m and per_act:
                shot_sec += int(m.group(1))
                per_act[-1] = (per_act[-1][0], per_act[-1][1] + int(m.group(1)))
    for a, b in re.findall(r"约\s*(\d+):(\d+)", script.read_text(encoding="utf-8")):
        act_sec += int(a) * 60 + int(b)

    def mmss(s: int) -> str:
        return f"{s // 60}:{s % 60:02d}"

    print(f"\n── 时长（报告，不设预设值）──")
    line = f"  逐镜秒数合计 {mmss(shot_sec)}"
    if act_sec:
        line += f"　幕标注合计 {mmss(act_sec)}"
    if planned:
        line += f"　（s10 排分集用的估值 {planned:.1f} 分，非验收标准）"
    print(line)
    if act_sec and abs(shot_sec - act_sec) > 60:
        print(f"  ⚠ 逐镜与幕标注差 {mmss(abs(shot_sec - act_sec))}，剧本自己对不上自己")
        for name, sec in per_act:
            m = re.search(r"约\s*(\d+):(\d+)", name)
            if not m or not sec:
                continue
            want = int(m.group(1)) * 60 + int(m.group(2))
            if abs(sec - want) > 30:
                print(f"      {name.split('　')[0]}：标 {mmss(want)}，实 {mmss(sec)}")
    # 规划分钟数只是 s10 排分集边界时用的估值，**不是成片验收标准**——
    # doc/00 已决定不对时长做预设。所以这里只并排列出，不判它对错。

    if lost:
        n = sum(len(v) for v in lost.values())
        print(f"\n── ⚠ 台词／心理活动里没在剧本中逐字找到的片段（{n} 条）──")
        print("  **这一项是报告，不是闸门**：自动判断分不清「真的漏了」和")
        print("  「『唐真如此评价自己』这类归属语，内容已由剧本结构表达」。")
        print("  逐条看，真漏的补进去，归属语可以放过。")
        for field, items in lost.items():
            for seq, para, text in items[:8]:
                print(f"  [{field[:3]}] seq{seq}[{para}] {text[:46]}")
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
