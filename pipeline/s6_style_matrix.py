#!/usr/bin/env python3
"""
步骤 6：画风选型矩阵批量出图。

读 `production/style_test/matrix.json`，把「画面目标 × 画风」两两组合成完整
提示词，调 image-2.0（兼容 OpenAI Images API）出图，每格 N 张，落到
`production/style_test/out/`，文件名 `T03_S6_2.png` = 目标 T03 × 画风 S6 的第 2 张。

出完图跑 `s7_contact_sheet.py` 拼成大图对比。

## 三条设计约束

1. **单一变量**。提示词 = 公共前缀 + 目标描述 + 画风段 + 公共后缀 + 避免句。
   目标描述只写「画面里有什么」，画风段只写「怎么渲染」，两者互不侵入。
   所以本脚本**不允许手改单格提示词**——要改就改 matrix.json 里的目标描述
   或画风段。逐格微调会让差异来源无法归因，对比就废了。

2. **反面词折进正文**。这个接口兼容的是 OpenAI Images API，
   **没有 negative_prompt 字段**。所以 shared.negative / target.negative_extra /
   style.negative_extra 会被去重合并，以自然语言「务必避免出现以下要素：…」
   追加在提示词末尾。这是接口限制下唯一可行的做法。

3. **按格断点续传**。已经存在的图不会重出（除非 --force）。
   一格要 3 张、只出来 2 张，下次跑只补缺的那 1 张。

## 计费与容错

按官方文档：请求成功才扣费，上游报错（5xx/401 等）不扣费。据此分三档处理：

  401 / 402   密钥无效 / 余额不足 → **立刻中止全部**，再跑也只是白撞
  429 / 5xx   限流 / 上游故障     → 指数退避重试（不扣费，可放心重试）
  400         参数或提示词被拒     → 只废掉这一格，其余继续，并打印出提示词

用法：
    python pipeline/s6_style_matrix.py --dump            # 只导出 80 条提示词，不出图
    python pipeline/s6_style_matrix.py --dry-run         # 只算要发多少请求
    python pipeline/s6_style_matrix.py --targets T03 --styles S1,S6   # 先小范围试水
    python pipeline/s6_style_matrix.py                   # 全量 10×8×3 = 240 张
"""
import argparse
import base64
import json
import random
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config
from common import paths
from common.jsonio import append_jsonl, write_json
from common.progress import Heartbeat, Progress, fmt_dur

MATRIX_JSON = paths.PRODUCTION_DIR / "style_test" / "matrix.json"
OUT_DIR = paths.PRODUCTION_DIR / "style_test" / "out"
PROMPTS_MD = paths.PRODUCTION_DIR / "style_test" / "prompts.md"
CALL_LOG = paths.LOG_DIR / "s6_images.jsonl"

# 可重试：限流与上游故障，按文档这些都不扣费
RETRYABLE = {408, 409, 425, 429, 500, 502, 503, 504, 520, 522, 524}
# 致命：再跑也只是白撞，直接中止全部
FATAL = {401, 402, 403}


class ImageError(RuntimeError):
    """单格失败，其余格继续。"""


class FatalError(RuntimeError):
    """全局失败，立即中止所有并发。"""


# ------------------------------------------------------------------ 提示词
def _split_terms(s: str) -> list[str]:
    """反面词允许用中英文逗号、顿号、分号分隔，统一切开。"""
    return [t.strip() for t in re.split(r"[,，、;；]", s or "") if t.strip()]


def compose_prompt(matrix: dict, target: dict, style: dict) -> str:
    """
    拼一格的完整提示词。顺序是有讲究的：

      公共前缀（题材+画幅） → 目标描述（画面里有什么） →
      画风段（怎么渲染） → 公共后缀（全局质感规则） → 避免句

    画风段放在目标描述之后，是因为多数出图模型对靠后的指令权重更高，
    而我们这次要比的正是画风——得让它压得住。
    """
    sh = matrix["shared"]
    body = " ".join(p.strip() for p in (
        sh.get("prefix", ""),
        target["desc"],
        style["suffix"],
        sh.get("suffix", ""),
    ) if p and p.strip())

    seen, terms = set(), []
    for src in (sh.get("negative"), target.get("negative_extra"), style.get("negative_extra")):
        for t in _split_terms(src):
            if t not in seen:
                seen.add(t)
                terms.append(t)
    if terms:
        body += "\n\n务必避免出现以下要素：" + "、".join(terms) + "。"
    return body


def build_cells(matrix: dict, targets: list[str] | None,
                styles: list[str] | None) -> list[dict]:
    ts = [t for t in matrix["targets"] if not targets or t["id"] in targets]
    ss = [s for s in matrix["styles"] if not styles or s["id"] in styles]
    unknown = (set(targets or []) - {t["id"] for t in matrix["targets"]}) | \
              (set(styles or []) - {s["id"] for s in matrix["styles"]})
    if unknown:
        raise SystemExit(f"matrix.json 里没有这些 id：{', '.join(sorted(unknown))}")
    return [{"target": t, "style": s, "prompt": compose_prompt(matrix, t, s)}
            for t in ts for s in ss]


# ------------------------------------------------------------------ 出图
def _decode(item: dict) -> bytes:
    """
    正常走 b64_json。少数网关会退化成返回 url——一并兜住，
    否则一次成功的付费请求会因为字段名不同而白扔。
    """
    if item.get("b64_json"):
        return base64.b64decode(item["b64_json"])
    if item.get("url"):
        r = requests.get(item["url"], timeout=config.IMAGE_TIMEOUT)
        r.raise_for_status()
        return r.content
    raise ImageError(f"返回里既没有 b64_json 也没有 url，字段：{list(item)}")


def _err_detail(body: str) -> str:
    try:
        j = json.loads(body)
        e = j.get("error") or j
        return str(e.get("message") or e)[:300]
    except Exception:
        return body[:300]


def generate(prompt: str, n: int, key: str, model: str, size: str,
             abort: threading.Event, report=None) -> list[bytes]:
    """发一次请求，返回若干张图的字节。失败按状态码分三档处理。"""
    url = f"{config.IMAGE_BASE_URL}/images/generations"
    payload = {"model": model, "prompt": prompt, "n": n, "size": size}
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    last = ""

    for attempt in range(1, config.IMAGE_MAX_RETRIES + 1):
        if abort.is_set():
            raise FatalError("已中止")
        if report:
            report(state="出图中", attempt=attempt, detail=f"请求{n}张")
        t0 = time.time()
        try:
            r = requests.post(url, headers=headers, json=payload,
                              timeout=(config.CONNECT_TIMEOUT, config.IMAGE_TIMEOUT))
        except requests.RequestException as e:
            last = f"网络异常：{type(e).__name__} {e}"
        else:
            if r.status_code == 200:
                try:
                    data = r.json().get("data") or []
                except ValueError:
                    raise ImageError(f"200 但不是 JSON，前 200 字：{r.text[:200]}")
                if not data:
                    last = "200 但 data 为空"
                else:
                    imgs = [_decode(d) for d in data]
                    append_jsonl(CALL_LOG, {
                        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                        "model": model, "size": size, "n_req": n, "n_got": len(imgs),
                        "attempt": attempt, "sec": round(time.time() - t0, 1),
                        "bytes": sum(len(b) for b in imgs),
                        "prompt_head": prompt[:80],
                    })
                    return imgs
            elif r.status_code in FATAL:
                abort.set()
                raise FatalError(f"HTTP {r.status_code}：{_err_detail(r.text)}\n"
                                 f"（401=密钥无效 402=余额不足 403=无权限，已中止全部任务）")
            elif r.status_code in RETRYABLE:
                last = f"HTTP {r.status_code}：{_err_detail(r.text)}"
                ra = r.headers.get("Retry-After")
                if ra and ra.isdigit():
                    time.sleep(min(int(ra), 60))
            else:
                # 400 之类：参数或提示词本身有问题，重试无意义
                raise ImageError(f"HTTP {r.status_code}：{_err_detail(r.text)}")

        if attempt < config.IMAGE_MAX_RETRIES:
            delay = config.RETRY_BASE_DELAY * (2 ** (attempt - 1)) + random.uniform(0, 1)
            if report:
                report(state="退避重试", attempt=attempt, detail=f"{last[:40]} · {delay:.0f}s")
            time.sleep(delay)

    raise ImageError(f"重试 {config.IMAGE_MAX_RETRIES} 次仍失败：{last}")


def cell_files(out_dir: Path, tid: str, sid: str, repeats: int) -> list[Path]:
    return [out_dir / f"{tid}_{sid}_{i}.png" for i in range(1, repeats + 1)]


def run_cell(cell: dict, idx: int, args, key: str, abort: threading.Event,
             prog: Progress, lock: threading.Lock, manifest: dict) -> tuple[str, str]:
    tid, sid = cell["target"]["id"], cell["style"]["id"]
    label = f"{tid}×{sid} {cell['target']['name']}／{cell['style']['name']}"
    files = cell_files(args.out, tid, sid, args.repeats)
    missing = [f for f in files if not f.exists()]

    if not missing:
        prog.end(idx, "skip", f"{label} 已完整跳过")
        return "skip", ""

    prog.begin(idx, label, len(missing))
    got = 0
    try:
        # 上游若只认 n=1，就按实际返回张数一轮轮补，不会漏图也不会死循环
        for _ in range(args.repeats + 2):
            missing = [f for f in files if not f.exists()]
            if not missing:
                break
            prog.stage(f"补{len(missing)}张")
            imgs = generate(cell["prompt"], min(args.batch, len(missing)), key,
                            args.model, args.size, abort,
                            report=lambda **kw: prog.note(**kw))
            for blob, path in zip(imgs, missing):
                path.write_bytes(blob)
                got += 1
            if len(imgs) < min(args.batch, len(missing)):
                prog.note(state="上游少给了图，继续补")
        left = [f for f in files if not f.exists()]
        if left:
            raise ImageError(f"还差 {len(left)} 张没出来")
    except FatalError as e:
        prog.end(idx, "fail", f"{label} 中止：{e}")
        return "fatal", str(e)
    except ImageError as e:
        prog.end(idx, "fail", f"{label} 失败：{e}")
        return "fail", str(e)

    with lock:
        manifest["cells"][f"{tid}_{sid}"] = {
            "target": tid, "target_name": cell["target"]["name"],
            "style": sid, "style_name": cell["style"]["name"],
            "model": args.model, "size": args.size,
            "files": [f.name for f in files],
            "prompt": cell["prompt"],
        }
        write_json(args.out / "manifest.json", manifest)
    prog.end(idx, "ok", f"{label} 新出 {got} 张")
    return "ok", ""


# ------------------------------------------------------------------ 入口
def dump_prompts(matrix: dict, cells: list[dict], path: Path) -> None:
    lines = [f"# 画风矩阵提示词全集（{len(cells)} 格）", "",
             f"由 `matrix.json` 自动拼装，**不要手改本文件**——改了也不会生效，"
             f"生成时是现拼的。要调就改 `matrix.json`。", "",
             f"- 公共前缀：{matrix['shared'].get('prefix','')}",
             f"- 公共后缀：{matrix['shared'].get('suffix','')}",
             f"- 尺寸：{config.IMAGE_SIZE}（1.75:1，最接近 16:9）", ""]
    last = None
    for c in cells:
        t, s = c["target"], c["style"]
        if t["id"] != last:
            last = t["id"]
            lines += ["---", "", f"## {t['id']}　{t['name']}", "",
                      f"**为什么测它**：{t.get('why','')}", "",
                      f"**看什么**：{t.get('key_check','')}", ""]
        lines += [f"### {t['id']} × {s['id']}　{s['name']}"
                  f"{'（' + s['ref'] + '）' if s.get('ref') else ''}", "",
                  "```text", c["prompt"], "```", ""]
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="画风选型矩阵批量出图")
    ap.add_argument("--matrix", type=Path, default=MATRIX_JSON)
    ap.add_argument("--out", type=Path, default=OUT_DIR)
    ap.add_argument("--targets", help="只跑这些目标，逗号分隔，如 T03,T06")
    ap.add_argument("--styles", help="只跑这些画风，逗号分隔，如 S1,S6")
    ap.add_argument("--repeats", type=int, default=None, help="每格几张，默认取 matrix.json")
    ap.add_argument("--batch", type=int, default=config.IMAGE_BATCH, help="单次请求出几张")
    ap.add_argument("--workers", type=int, default=config.IMAGE_WORKERS)
    ap.add_argument("--model", default=config.IMAGE_MODEL)
    ap.add_argument("--size", default=config.IMAGE_SIZE)
    ap.add_argument("--force", action="store_true", help="删掉已有图重出")
    ap.add_argument("--dump", action="store_true", help="只导出提示词，不出图")
    ap.add_argument("--dry-run", action="store_true", help="只报要发多少请求，不出图")
    ap.add_argument("--plain", action="store_true", help="不画看板，逐行日志")
    args = ap.parse_args()

    if not args.matrix.exists():
        raise SystemExit(f"找不到矩阵定义：{args.matrix}")
    matrix = json.loads(args.matrix.read_text(encoding="utf-8"))
    args.repeats = args.repeats or int(matrix["meta"].get("repeats_per_cell", 3))
    args.batch = max(1, min(args.batch, args.repeats))

    cells = build_cells(matrix,
                        [x.strip() for x in args.targets.split(",")] if args.targets else None,
                        [x.strip() for x in args.styles.split(",")] if args.styles else None)

    if args.dump:
        PROMPTS_MD.parent.mkdir(parents=True, exist_ok=True)
        dump_prompts(matrix, cells, PROMPTS_MD)
        print(f"已导出 {len(cells)} 条提示词 → {PROMPTS_MD}")
        return 0

    args.out.mkdir(parents=True, exist_ok=True)
    paths.LOG_DIR.mkdir(parents=True, exist_ok=True)

    if args.force:
        n = 0
        for c in cells:
            for f in cell_files(args.out, c["target"]["id"], c["style"]["id"], args.repeats):
                if f.exists():
                    f.unlink()
                    n += 1
        print(f"--force：已删除 {n} 张旧图")

    todo, done = [], 0
    for c in cells:
        miss = [f for f in cell_files(args.out, c["target"]["id"], c["style"]["id"], args.repeats)
                if not f.exists()]
        if miss:
            todo.append((c, len(miss)))
        done += args.repeats - len(miss)
    need = sum(m for _, m in todo)
    reqs = sum(-(-m // args.batch) for _, m in todo)   # 向上取整

    print(f"矩阵 {len({c['target']['id'] for c in cells})} 目标 × "
          f"{len({c['style']['id'] for c in cells})} 画风 × {args.repeats} 张 "
          f"= {len(cells) * args.repeats} 张")
    print(f"已有 {done} 张，待出 {need} 张，约 {reqs} 次请求 "
          f"（单次 n={args.batch}）")
    print(f"模型 {args.model} | 尺寸 {args.size} | 并发 {args.workers} | "
          f"接口 {config.IMAGE_BASE_URL}")
    print("计费按官方文档：请求成功才扣费，上游报错不扣费。具体单价看你的账户。")
    if args.dry_run:
        return 0
    if not todo:
        print("全部已完成，无需出图。接着跑 s7_contact_sheet.py 拼大图。")
        return 0

    key = config.image_api_key()
    mf_path = args.out / "manifest.json"
    manifest = json.loads(mf_path.read_text(encoding="utf-8")) if mf_path.exists() \
        else {"cells": {}}
    manifest.setdefault("cells", {})
    manifest["meta"] = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                        "model": args.model, "size": args.size,
                        "repeats": args.repeats, "matrix_version": matrix["meta"].get("version")}

    abort = threading.Event()
    lock = threading.Lock()
    prog = Progress(total=len(todo), workers=args.workers,
                    done_already=len(cells) - len(todo), force_plain=args.plain)
    t0 = time.time()
    stats = {"ok": 0, "fail": 0, "fatal": 0, "skip": 0}
    fails: list[str] = []

    # 单次出图动辄几十秒，没有心跳看板就会像死了一样
    with Heartbeat(prog), ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs = {pool.submit(run_cell, c, i, args, key, abort, prog, lock, manifest): c
                for i, (c, _) in enumerate(todo, 1)}
        for fut in as_completed(futs):
            status, msg = fut.result()
            stats[status] = stats.get(status, 0) + 1
            if status in ("fail", "fatal"):
                c = futs[fut]
                fails.append(f"{c['target']['id']}×{c['style']['id']}　{msg}")
    prog.close()

    with lock:
        write_json(mf_path, manifest)

    print(f"\n用时 {fmt_dur(time.time() - t0)}　成功 {stats['ok']} 格　"
          f"失败 {stats['fail']} 格　中止 {stats['fatal']} 格")
    if fails:
        print("\n未完成的格子（重跑本命令即可只补这些）：")
        for f in fails[:20]:
            print("  ✗ " + f)
        if len(fails) > 20:
            print(f"  …… 另有 {len(fails) - 20} 格")
    if stats["fatal"]:
        print("\n出现致命错误已中止。先确认密钥与余额，再重跑。")
        return 2
    print(f"\n图在 {args.out}　接着跑：python pipeline/s7_contact_sheet.py")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
