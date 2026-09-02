#!/usr/bin/env python3
"""
一键跑全部闸门。不调 API，不改任何入库文件（s3 会重写 .run/reports/ 下的体检报告，那不入库）。

改完任何东西——代码、卡、剧本、文档——跑这一条就知道有没有把什么弄坏：

    python pipeline/check_all.py            # 全部
    python pipeline/check_all.py --quick    # 跳过 47 集的 s14（最慢的一段）

五道闸门：

    selftest      离线自测，190+ 条断言，管道逻辑
    s3            1200 章体检：臆造 = 0、逐字率、台词覆盖、审查分是否过合格线（T3 只提示不拦）
    s14 × 47      每集旁白承载账：关键旁白全部上账、现身 ≤ 6 次……
    s15           画风金标准：无载体冲突、渲染锁与同源锁逐字一致
    s17           原文引用：每处 【原】「…」 都逐字落在所标段号

任一失败退出码 1，并把失败项列在最后。每道闸门各自的输出在上面，翻回去看细节。
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import paths  # noqa: E402
from common.jsonio import read_json  # noqa: E402

PY = sys.executable
PIPE = paths.PIPELINE_DIR


def run(label: str, cmd: list[str], quiet: bool) -> tuple[str, bool, float, str]:
    t0 = time.time()
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=str(paths.ROOT))
    out = (r.stdout or "") + (r.stderr or "")
    ok = r.returncode == 0
    if not quiet or not ok:
        print(f"\n{'=' * 62}\n▶ {label}\n{'=' * 62}")
        print(out.rstrip())
    return label, ok, time.time() - t0, out


def main() -> int:
    ap = argparse.ArgumentParser(description="一键跑全部闸门")
    ap.add_argument("--quick", action="store_true", help="跳过逐集 s14（最慢）")
    ap.add_argument("--season", default="s01", help="跑哪一季的剧本（默认 s01）")
    args = ap.parse_args()

    results = []
    results.append(run("selftest", [PY, str(PIPE / "selftest.py")], quiet=True))
    results.append(run("s3 章节体检", [PY, str(PIPE / "s3_validate_chapters.py")], quiet=False))
    results.append(run("s15 画风闸门", [PY, str(PIPE / "s15_style_guard.py")], quiet=False))
    results.append(run("s17 引用闸门", [PY, str(PIPE / "s17_citation_check.py")], quiet=False))

    if not args.quick:
        eps = read_json(paths.EPISODES_JSON)["episodes"]
        season_no = int(args.season[1:])
        codes = [e["code"] for e in eps if e["season"] == season_no and paths.script_path(e["code"]).exists()]
        print(f"\n{'=' * 62}\n▶ s14 旁白承载账 × {len(codes)} 集\n{'=' * 62}")
        for code in codes:
            label, ok, sec, out = run(f"s14 {code}",
                                      [PY, str(PIPE / "s14_narration_ledger.py"), "--episode", code],
                                      quiet=True)
            gates = [ln.strip() for ln in out.splitlines() if ln.strip().startswith(("✓", "✗"))]
            bad = [g for g in gates if g.startswith("✗")]
            print(f"  {'✓' if ok else '✗'} {code}  {sec:4.1f}s" + (f"  {'；'.join(bad)}" if bad else ""))
            results.append((label, ok, sec, out))

    print(f"\n{'=' * 62}\n汇总\n{'=' * 62}")
    failed = [r for r in results if not r[1]]
    total = sum(r[2] for r in results)
    for label, ok, sec, _ in results:
        if not label.startswith("s14 S") or not ok:
            print(f"  {'✓' if ok else '✗'} {label}  {sec:.1f}s")
    n14 = sum(1 for r in results if r[0].startswith("s14 S"))
    if n14:
        print(f"  {'✓' if not any(r[0].startswith('s14 S') and not r[1] for r in results) else '✗'} "
              f"s14 × {n14} 集")
    print(f"\n共 {total:.0f}s。" + (f"✗ {len(failed)} 项未通过：{[r[0] for r in failed]}" if failed else "✓ 全绿"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
