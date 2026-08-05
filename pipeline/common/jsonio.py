"""JSON 读写：统一 UTF-8、不转义中文、原子写（避免中断写坏文件）。"""
import json
import os
import tempfile
from pathlib import Path
from typing import Any


def read_json(path: Path) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, obj: Any, indent: int = 2) -> None:
    """原子写：先写临时文件再 rename，中途崩溃不会留下半个文件。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=indent)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def append_jsonl(path: Path, obj: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def extract_json_block(text: str) -> Any:
    """从模型回复里抠出 JSON。小模型常见毛病：包 ```json 围栏、前后带解释文字。"""
    s = text.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else s
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3]
    s = s.strip()
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    # 退路：截取第一个 { 到最后一个 }
    i, j = s.find("{"), s.rfind("}")
    if i != -1 and j > i:
        return json.loads(s[i:j + 1])
    raise ValueError(f"回复中找不到合法 JSON，前 200 字：{text[:200]}")
