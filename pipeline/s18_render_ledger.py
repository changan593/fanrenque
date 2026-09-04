#!/usr/bin/env python3
"""
步骤 18：**出图台账闸门**。不调 API。

规范见 `doc/16_出图工序规范.md`。

## 为什么要有这一步

第一季 47 集、12,958 镜。镜头成品图**不入库**（按每张 2MB 算是 50GB 以上，
而且它由「卡 + 幕提示词 + 风格段」确定地推导出来，属于删了能重跑的那一类）。
**入库的是台账**——「哪一镜用了哪一版、谁验收的、什么时候」是人的判断，删了就没了。

台账是手写的，手写就会漏、会串号、会写了通过却没填版本。
这一步把这几件事做成每次都能重跑的检查，和 `s14` 管旁白承载账是同一个思路。

## 它检查什么

1. **台账在不在**、表头对不对
2. **幕文档里的每一镜，台账里都有行**——漏镜是最常见的错
3. 台账里**没有幕文档里不存在的镜号**（串号／手误）
4. 验收 `✅` 的行**必须有采用版本**，且文件名合规
   （`E{集}_M{镜}[_{层}]_t{版次}.png`，镜号三位、版次两位）
5. **双层镜头两层都要有行**：剧本声音栏标 `【现】` 的镜头（唐假现身、世界静止）
   一镜出两张，底图 `base` ＋ 叠加层 `tangjia`

**跑通不代表图好看**——它只保证「没有漏镜、没有糊账」。
质量靠 `doc/16` 第七节的三层人工验收。

## 用法

    python pipeline/s18_render_ledger.py                # 全季
    python pipeline/s18_render_ledger.py --episode E01  # 单集
    python pipeline/s18_render_ledger.py --todo         # 只列还没出的镜
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import paths  # noqa: E402

LEDGER_NAME = "出图台账.md"
HEAD = ["镜", "幕", "层", "采用版本", "验收", "日期", "备注"]
OK, BAD, TODO = "✅", "❌", "—"

# E01_M012_t03.png ／ E01_M006_base_t02.png
FRAME_RE = re.compile(r"^E(\d{2})_M(\d{3})(?:_([a-z]+))?_t(\d{2})\.png$")
SHOT_RE = re.compile(r"^#+\s*镜(\d{3})[｜|]")
ACT_RE = re.compile(r"^(\d{2})_")


def episodes(season: Path) -> list[Path]:
    return [p for p in sorted(season.iterdir())
            if p.is_dir() and re.fullmatch(r"E\d{2}", p.name)]


def acts_of(ep: Path) -> list[Path]:
    return [p for p in sorted(ep.iterdir()) if p.is_dir() and ACT_RE.match(p.name)]


def shots_of_act(act: Path) -> list[str]:
    """幕提示词里的镜号，按出现顺序。"""
    md = act / "提示词.md"
    if not md.exists():
        return []
    return [m.group(1) for line in md.read_text(encoding="utf-8").splitlines()
            if (m := SHOT_RE.match(line.strip()))]


def two_layer_shots(ep: Path) -> set[str]:
    """剧本里标【现】的镜号——唐假现身、世界静止，一镜两张。"""
    script = ep / "剧本.md"
    if not script.exists():
        return set()
    out = set()
    for line in script.read_text(encoding="utf-8").splitlines():
        if "【现】" not in line:
            continue
        # 表格行首格是镜号
        cells = [c.strip() for c in line.split("|")]
        for c in cells[:3]:
            if re.fullmatch(r"\d{3}", c):
                out.add(c)
                break
    return out


def parse_ledger(path: Path) -> tuple[list[dict], list[str]]:
    """返回（数据行, 结构问题）。"""
    problems: list[str] = []
    rows: list[dict] = []
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines()
             if ln.strip().startswith("|")]
    if not lines:
        return rows, [f"{path}：没有表格"]
    head = [c.strip() for c in lines[0].strip().strip("|").split("|")]
    if head != HEAD:
        problems.append(f"{path}：表头应为 {' | '.join(HEAD)}，实际是 {' | '.join(head)}")
        return rows, problems
    for lineno, ln in enumerate(lines[2:], start=3):     # 跳过表头与分隔行
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        if len(cells) != len(HEAD):
            problems.append(f"{path}:{lineno}：应有 {len(HEAD)} 格，实际 {len(cells)} 格")
            continue
        rows.append(dict(zip(HEAD, cells)) | {"_line": lineno})
    return rows, problems


def check_episode(ep: Path, todo_only: bool) -> tuple[list[str], dict]:
    problems: list[str] = []
    want = want_of(ep)                              # (镜, 层) -> 幕

    stat = {"应出": len(want), "通过": 0, "退回": 0, "未做": 0, "缺行": 0,
            "有台账": False, "有提示词": bool(want)}
    ledger = ep / LEDGER_NAME
    if not ledger.exists():
        # 还没开工不是错误——这是常态。只有已经建了台账才查糊账。
        stat["缺行"] = len(want)
        return problems, stat
    stat["有台账"] = True

    rows, structural = parse_ledger(ledger)
    problems += structural
    seen: set[tuple[str, str]] = set()
    rel = ledger.relative_to(paths.ROOT)

    for r in rows:
        key = (r["镜"], r["层"] or "—")
        if key in seen:
            problems.append(f"{rel}:{r['_line']}：镜 {key[0]}（层 {key[1]}）重复登记")
        seen.add(key)
        if key not in want:
            problems.append(
                f"{rel}:{r['_line']}：镜 {key[0]}（层 {key[1]}）在幕文档里不存在")
            continue
        if r["幕"] != want[key]:
            problems.append(
                f"{rel}:{r['_line']}：镜 {key[0]} 记在幕 {r['幕']}，实际属于幕 {want[key]}")
        verdict = r["验收"]
        if verdict == OK:
            stat["通过"] += 1
            got = r["采用版本"]
            if not got or got == TODO:
                problems.append(f"{rel}:{r['_line']}：验收 ✅ 却没填采用版本")
            elif not (m := FRAME_RE.match(got)):
                problems.append(f"{rel}:{r['_line']}：采用版本「{got}」不合命名规范"
                                f"　E{{集}}_M{{镜}}[_{{层}}]_t{{版次}}.png")
            else:
                if m.group(2) != key[0]:
                    problems.append(f"{rel}:{r['_line']}：文件名里的镜号 {m.group(2)}"
                                    f" 与本行的 {key[0]} 对不上")
                if (m.group(3) or "—") != key[1]:
                    problems.append(f"{rel}:{r['_line']}：文件名里的层"
                                    f"「{m.group(3) or '—'}」与本行的「{key[1]}」对不上")
                if m.group(1) != ep.name[1:]:
                    problems.append(f"{rel}:{r['_line']}：文件名里的集号 E{m.group(1)}"
                                    f" 与 {ep.name} 对不上")
        elif verdict == BAD:
            stat["退回"] += 1
            if not r["备注"].strip():
                problems.append(f"{rel}:{r['_line']}：退回 ❌ 必须在备注里写"
                                f"「改了哪一层」（doc/16 第八节）")
        elif verdict == TODO:
            stat["未做"] += 1
        else:
            problems.append(f"{rel}:{r['_line']}：验收栏应为 ✅ / ❌ / —，实际「{verdict}」")

    for key in sorted(set(want) - seen):
        stat["缺行"] += 1
        if not todo_only:
            problems.append(f"{rel}：镜 {key[0]}（层 {key[1]}，幕 {want[key]}）台账里没有行")
    return problems, stat


def want_of(ep: Path) -> dict[tuple[str, str], str]:
    """(镜, 层) -> 幕。双层镜头拆成 base / tangjia 两行。"""
    want: dict[tuple[str, str], str] = {}
    for act in acts_of(ep):
        for shot in shots_of_act(act):
            want[(shot, "—")] = act.name[:2]
    for shot in two_layer_shots(ep):
        act = next((a for (s, _), a in want.items() if s == shot), None)
        if act is None:
            continue
        want.pop((shot, "—"), None)
        want[(shot, "base")] = act
        want[(shot, "tangjia")] = act
    return want


def scaffold(ep: Path) -> int:
    """按幕文档生成空台账，省得手敲两百行。已存在则不覆盖。"""
    ledger = ep / LEDGER_NAME
    if ledger.exists():
        print(f"{ledger.relative_to(paths.ROOT)} 已存在，不覆盖。")
        return 1
    want = want_of(ep)
    if not want:
        print(f"{ep.name} 还没有画面提示词，无从生成。")
        return 1
    rows = sorted(want.items(), key=lambda kv: (kv[1], kv[0][0], kv[0][1]))
    out = [
        f"# {ep.name}｜出图台账",
        "",
        f"规范见 [`../../../doc/16_出图工序规范.md`](../../../doc/16_出图工序规范.md)。",
        f"闸门：`python pipeline/s18_render_ledger.py --episode {ep.name}`",
        "",
        "- **验收**：`✅` 通过 ／ `❌` 退回重做 ／ `—` 还没做",
        "- **采用版本**：通过时必填，命名 `E{集}_M{镜}[_{层}]_t{版次}.png`",
        "- ★ **退回的行必须在备注里写「改了哪一层」**（`doc/16` 第八节的返工决策树）",
        f"- 双层镜头（剧本标 `【现】`，唐假现身）一镜两行：`base` 底图 ＋ `tangjia` 叠加层",
        "",
        "| " + " | ".join(HEAD) + " |",
        "| " + " | ".join("---" for _ in HEAD) + " |",
    ]
    for (shot, layer), act in rows:
        out.append(f"| {shot} | {act} | {layer} | {TODO} | {TODO} |  |  |")
    ledger.write_text("\n".join(out) + "\n", encoding="utf-8")
    n2 = sum(1 for (_, l), _ in rows if l != "—")
    print(f"已生成 {ledger.relative_to(paths.ROOT)}"
          f"（{len(rows)} 行＝{len(rows) - n2} 单层 + {n2 // 2} 双层镜头×2）")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="出图台账闸门（doc/16_出图工序规范.md）")
    ap.add_argument("--episode", help="只查一集，如 E01")
    ap.add_argument("--todo", action="store_true", help="只统计进度，不报缺行")
    ap.add_argument("--scaffold", action="store_true",
                    help="按幕文档生成空台账（需配合 --episode）")
    args = ap.parse_args()

    if args.scaffold:
        if not args.episode:
            print("--scaffold 需要配合 --episode，如 --episode E01"); return 1
        ep = paths.PRODUCTION_DIR / "s01" / args.episode
        if not ep.is_dir():
            print(f"找不到 {args.episode}"); return 1
        return scaffold(ep)

    season = paths.PRODUCTION_DIR / "s01"
    if not season.is_dir():
        print("找不到 production/s01"); return 1
    eps = episodes(season)
    if args.episode:
        eps = [e for e in eps if e.name == args.episode]
        if not eps:
            print(f"找不到 {args.episode}"); return 1

    print("=" * 62)
    print("出图台账闸门　doc/16_出图工序规范.md")
    print("=" * 62)

    keys = ("应出", "通过", "退回", "未做", "缺行")
    all_problems, total = [], dict.fromkeys(keys, 0)
    started, ready = [], []
    for ep in eps:
        problems, stat = check_episode(ep, args.todo)
        all_problems += problems
        for k in keys:
            total[k] += stat[k]
        if stat["有台账"]:
            started.append((ep.name, stat))
        elif stat["有提示词"]:
            ready.append((ep.name, stat["应出"]))

    if started:
        print("\n已开工的集：\n")
        print("  集    应出   通过   退回   未做   缺行")
        for name, s in started:
            print(f"  {name}  {s['应出']:>4}   {s['通过']:>4}   "
                  f"{s['退回']:>4}   {s['未做']:>4}   {s['缺行']:>4}")

    if ready:
        print("\n画面提示词已就绪、还没建台账的集：\n")
        for name, n in ready:
            print(f"  {name}　{n} 镜次")
        print(f"\n  开工时在 production/s01/EXX/ 下建 {LEDGER_NAME}，表头：")
        print("  | " + " | ".join(HEAD) + " |")

    if not started and not ready:
        print("\n还没有任何一集写完画面提示词。")

    if all_problems:
        print(f"\n🔴 {len(all_problems)} 处问题\n")
        for p in all_problems[:60]:
            print("  " + p)
        if len(all_problems) > 60:
            print(f"  …… 还有 {len(all_problems) - 60} 处")
        print("-" * 62)
        return 1

    print("-" * 62)
    print(f"全季应出 {total['应出']} 镜次｜通过 {total['通过']}"
          f"｜退回 {total['退回']}｜未做 {total['未做']}")
    print("\n✓ 台账无糊账")
    print("  注意：本闸门只保证「没有漏镜、没有糊账」，")
    print("  画面质量靠 doc/16 第七节的三层人工验收。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
