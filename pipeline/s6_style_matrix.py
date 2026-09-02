#!/usr/bin/env python3
"""
步骤 6：画风选型矩阵批量出图。

读 `production/style_test/matrix.json`，把「画面目标 × 画风」两两组合成完整
提示词，调 OpenAI Images API 兼容接口（模型由 .env 的 IMAGE_MODEL 指定）出图，每格 N 张，
落到 `production/style_test/out/`，文件名 `T03_S6_2.png` = 目标 T03 × 画风 S6 的第 2 张。
规模以 matrix.json 为准（当前 10 个画面目标 × 11 个画风 × 3 张）。

**选型已结束**（结论国风三维，见 doc/04 3.1）。本脚本与 matrix.json 保留作留痕，
以后要复盘或重跑对比时不用重建。

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
    python pipeline/s6_style_matrix.py --dump            # 只导出全部提示词到 prompts.md，不出图
    python pipeline/s6_style_matrix.py --dry-run         # 只算要发多少请求
    python pipeline/s6_style_matrix.py --targets T03 --styles S1,S6   # 先小范围试水
    python pipeline/s6_style_matrix.py                   # 全量
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

MATRIX_JSON = paths.STYLE_TEST_DIR / "matrix.json"
OUT_DIR = paths.STYLE_TEST_DIR / "out"
PROMPTS_MD = paths.STYLE_TEST_DIR / "prompts.md"
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


def build_payload(prompt: str, n: int, model: str, size: str,
                  extras: bool = True) -> dict:
    """
    按 OpenAI Images API 组请求体。

    可选参数只在配置里填了才发：网关不认某个参数会直接 400，
    能不发就不发。extras=False 用于 400 之后的降级重试。
    """
    p = {"model": model, "prompt": prompt, "n": n, "size": size}
    if extras:
        for key, val in (("quality", config.IMAGE_QUALITY),
                         ("output_format", config.IMAGE_OUTPUT_FORMAT),
                         ("background", config.IMAGE_BACKGROUND),
                         ("moderation", config.IMAGE_MODERATION)):
            if val:
                p[key] = val
    return p


def crop_169(blob: bytes) -> bytes:
    """
    居中裁成精确 16:9。接口给不出 16:9 的档位（gpt-image 最接近的是 3:2），
    与其迁就，不如出完自己裁——全片画幅已定 16:9，测试图就该按成片画幅看。
    """
    from io import BytesIO

    from PIL import Image
    im = Image.open(BytesIO(blob))
    w, h = im.size
    want = w * 9 / 16
    if abs(h - want) < 2:                     # 本来就是 16:9
        return blob
    if h > want:                              # 偏方，切上下
        top = int((h - want) / 2)
        im = im.crop((0, top, w, top + int(want)))
    else:                                     # 偏宽，切左右
        want_w = int(h * 16 / 9)
        left = int((w - want_w) / 2)
        im = im.crop((left, 0, left + want_w, h))
    out = BytesIO()
    im.save(out, "PNG")
    return out.getvalue()


def generate(prompt: str, n: int, key: str, model: str, size: str,
             abort: threading.Event, report=None) -> list[bytes]:
    """发一次请求，返回若干张图的字节。失败按状态码分三档处理。"""
    # 参数自查放在发请求之前：能在本地说清楚的问题，不该花一次请求去换一个 502
    bad = config.image_size_check(model, size)
    if bad:
        raise ImageError(bad)

    url = f"{config.IMAGE_BASE_URL}/images/generations"
    payload = build_payload(prompt, n, model, size)
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
                    if config.IMAGE_CROP_169:
                        imgs = [crop_169(b) for b in imgs]
                    append_jsonl(CALL_LOG, {
                        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                        "model": model, "size": size, "n_req": n, "n_got": len(imgs),
                        "attempt": attempt, "sec": round(time.time() - t0, 1),
                        "bytes": sum(len(b) for b in imgs),
                        "extras": sorted(set(payload) - {"model", "prompt", "n", "size"}),
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
                detail = _err_detail(r.text)
                # 400 多半是参数问题，原样重试无意义——但可选参数（quality /
                # moderation / output_format / background）本就是「有则更好」，
                # 网关不认就摘掉再来，不该让整格因此报废。
                extras = sorted(set(payload) - {"model", "prompt", "n", "size"})
                if r.status_code == 400 and extras:
                    payload = build_payload(prompt, n, model, size, extras=False)
                    last = f"HTTP 400：{detail}"
                    if report:
                        report(state="降级重试", attempt=attempt,
                               detail=f"摘掉可选参数 {'/'.join(extras)}")
                    continue
                raise ImageError(f"HTTP {r.status_code}：{detail}")

        if attempt < config.IMAGE_MAX_RETRIES:
            delay = config.IMAGE_RETRY_BASE * (2 ** (attempt - 1)) + random.uniform(0, 1)
            if report:
                report(state="退避重试", attempt=attempt, detail=f"{last[:40]} · {delay:.0f}s")
            time.sleep(delay)

    hint = ""
    # 体检模式（只试一次）不加这段——那边有更精确的阶梯结论，别用泛泛的猜测盖过去
    if config.IMAGE_MAX_RETRIES > 1 and any(c in last for c in ("502", "503", "504", "500")):
        hint = ("\n  这是上游的错，按官方文档不扣费。多半是服务端此刻不可用，"
                "过几分钟重跑原命令即可（已出的图不会重复出）。"
                "\n  若一直如此，跑 --doctor 逐级定位。")
    raise ImageError(f"重试 {config.IMAGE_MAX_RETRIES} 次仍失败：{last}{hint}")


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
def doctor(matrix: dict, args) -> int:
    """
    阶梯式体检。一次只动一个变量，第一个失败的台阶就是病因所在。

    502「Upstream network error」是个筐——上游真抽风是它，模型名不认、
    尺寸不支持、提示词太长、n=3 要不起，也可能被网关一并包成它。
    逐级加压才能把变量分开，否则只能靠猜着重跑。
    """
    tiny = "一只碗。"
    real = compose_prompt(matrix, matrix["targets"][0], matrix["styles"][0])
    steps = [
        ("接口连通 + 密钥 + 模型名", dict(prompt=tiny, n=1, size="1024x1024"),
         f"这一步就挂 → 检查 IMAGE_BASE_URL（当前 {config.IMAGE_BASE_URL}）、"
         f"IMAGE_API_KEY、IMAGE_MODEL（当前 {args.model}）"),
        (f"目标尺寸 {args.size}", dict(prompt=tiny, n=1, size=args.size),
         f"只有这一步挂 → 上游不认 {args.size}。gpt-image 官方只支持 "
         f"1024x1024 / 1536x1024 / 1024x1536 / auto；1792x1024 是 dall-e-3 的档位。"
         f"改 .env 里的 IMAGE_SIZE"),
        (f"真实长度提示词（{len(real)} 字）", dict(prompt=real, n=1, size=args.size),
         "只有这一步挂 → 提示词太长或触发了内容审核，"
         "把 matrix.json 里的描述写短些"),
        ("单次出 3 张（n=3）", dict(prompt=real, n=3, size=args.size),
         "只有这一步挂 → 上游要不起一次三张，用 --batch 1 一张张出"),
    ]

    print(f"接口 {config.IMAGE_BASE_URL}/images/generations")
    extras = build_payload("x", 1, args.model, args.size)
    extras = {k: v for k, v in extras.items() if k not in ("model", "prompt", "n", "size")}
    print(f"模型 {args.model} | 尺寸 {args.size} | "
          f"裁 16:9 {'开' if config.IMAGE_CROP_169 else '关'}")
    print(f"可选参数 {extras or '（未设置）'}"
          f"　←　网关不认会 400，脚本会自动摘掉重试\n")
    bad = config.image_size_check(args.model, args.size)
    if bad:
        print(f"⚠ 请求还没发就能判死：\n  {bad}\n")
        return 1
    try:
        key = config.image_api_key()
    except SystemExit as e:
        print(e)
        return 1
    print(f"密钥  已读到，{key[:6]}...{key[-4:]}（长度 {len(key)}）\n")

    abort = threading.Event()
    saved, config.IMAGE_MAX_RETRIES = config.IMAGE_MAX_RETRIES, 1   # 体检不重试，要看真实反应
    try:
        for i, (name, kw, hint) in enumerate(steps, 1):
            print(f"[{i}/{len(steps)}] {name} …… ", end="", flush=True)
            t0 = time.time()
            try:
                imgs = generate(key=key, model=args.model, abort=abort, **kw)
            except (ImageError, FatalError) as e:
                print(f"✗  {time.time() - t0:.0f}s")
                print(f"\n    {e}\n")
                print(f"    {hint}")
                # 只有第一级就挂时，「上游整体不可用」和「配置不对」才分不开。
                # 第二级往后挂，说明前面的请求成功过，上游显然是活的——
                # 这时再提「可能是上游抽风」只会把已经定位到的结论搅浑。
                if i == 1:
                    print("\n    也可能上游此刻整体不可用。这类错误按官方文档不扣费，")
                    print("    先隔几分钟重跑一次 --doctor；仍是同样结果再按上面查配置。")
                return 1
            print(f"✓  {len(imgs)} 张 · "
                  f"{sum(len(b) for b in imgs) / 1024:.1f}KB · {time.time() - t0:.0f}s")
    finally:
        config.IMAGE_MAX_RETRIES = saved

    out = args.out / "_doctor"
    out.mkdir(parents=True, exist_ok=True)
    for j, b in enumerate(imgs, 1):
        (out / f"doctor_{j}.png").write_bytes(b)
    print(f"\n四级全通过。样图在 {out}，看一眼再跑全量。")
    print("注意：体检出的图不计入矩阵，全量跑的时候会重新出。")
    return 0


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
    ap.add_argument("--doctor", action="store_true",
                    help="阶梯式体检：连通→尺寸→提示词长度→n=3，定位失败原因")
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

    if args.doctor:
        paths.LOG_DIR.mkdir(parents=True, exist_ok=True)
        return doctor(matrix, args)

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
