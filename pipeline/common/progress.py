"""
跑批进度显示。

并发跑 1200 章要跑很久，得能一眼看出：整体到哪了、还要多久、
每个并发槽当前在处理哪一章、卡在哪个环节。

终端是 TTY 时画一块原地刷新的状态板；被重定向到文件时自动退化成
逐行日志（不吐 ANSI 转义符污染日志）。
"""
import shutil
import sys
import threading
import time


def fmt_dur(sec: float) -> str:
    sec = int(sec)
    h, m, s = sec // 3600, (sec % 3600) // 60, sec % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _dw(s: str) -> int:
    """显示宽度：中日韩字符占 2 列。"""
    return sum(2 if ord(c) > 0x2E80 else 1 for c in s)


def _clip(s: str, width: int) -> str:
    """按显示宽度截断，避免换行打乱光标计算。"""
    out, w = [], 0
    for c in s:
        cw = 2 if ord(c) > 0x2E80 else 1
        if w + cw > width:
            break
        out.append(c)
        w += cw
    return "".join(out)


def _pad(s: str, width: int) -> str:
    """按显示宽度左对齐补齐，中文列才对得整齐。"""
    s = _clip(s, width)
    return s + " " * max(0, width - _dw(s))


class Progress:
    """线程安全的进度看板。"""

    def __init__(self, total: int, workers: int, done_already: int = 0,
                 force_plain: bool = False):
        self.total = total
        self.workers = workers
        self.done_already = done_already      # 断点续传时已完成的章节数
        self.done = 0                          # 本次处理完成数
        self.ok = 0
        self.failed = 0
        self.t0 = time.time()
        self.active: dict[int, dict] = {}      # slot -> {seq,label,stage,step,steps,t0}
        # 槽位显式借还：线程 ident 会在线程销毁后被复用，按 ident 直接映射会串行、
        # 且槽位永不回收会让看板行数无限增长。
        self._free: list[int] = list(range(max(1, workers)))
        self._held: dict[int, int] = {}        # 线程 ident -> 当前持有的 slot
        self._lock = threading.RLock()
        self._painted = 0                      # 上次画了几行，用于原地刷新
        self._last_paint = 0.0
        self.tty = sys.stdout.isatty() and not force_plain

    # ---------------------------------------------------------- 槽位借还
    def _acquire(self) -> int:
        ident = threading.get_ident()
        with self._lock:
            slot = self._free.pop(0) if self._free else len(self.active)
            self._held[ident] = slot
            return slot

    def _current(self) -> int | None:
        return self._held.get(threading.get_ident())

    def _release(self) -> int | None:
        ident = threading.get_ident()
        with self._lock:
            slot = self._held.pop(ident, None)
            if slot is not None and slot not in self._free:
                self._free.append(slot)
                self._free.sort()
            return slot

    # ---------------------------------------------------------- 事件
    def begin(self, seq: int, label: str, steps: int) -> None:
        with self._lock:
            self.active[self._acquire()] = {"seq": seq, "label": label, "stage": "准备",
                                            "step": 0, "steps": steps, "t0": time.time()}
        self._paint()

    def stage(self, name: str, steps: int | None = None) -> None:
        """steps 用于中途上调总步数：触发修订轮时章内步骤会变多。"""
        with self._lock:
            a = self.active.get(self._current())
            if a:
                a["stage"] = name
                a["step"] += 1
                if steps:
                    a["steps"] = steps
        self._paint()

    def end(self, seq: int, status: str, msg: str) -> None:
        """status: ok | fail | skip"""
        with self._lock:
            a = self.active.pop(self._release(), None)
            elapsed = time.time() - a["t0"] if a else 0.0
            if status != "skip":
                self.done += 1
                self.ok += status == "ok"
                self.failed += status == "fail"
            line = (f"[{self.done}/{self.total}] seq{seq:<5d} "
                    f"{'✓' if status == 'ok' else '✗' if status == 'fail' else '-'} "
                    f"{msg}  {elapsed:.0f}s")
        if self.tty:
            self._paint(log=line)
        else:
            print(line, flush=True)
            if self.done % 25 == 0:
                print("    " + self._headline(plain=True), flush=True)

    # ---------------------------------------------------------- 渲染
    def _headline(self, plain: bool = False) -> str:
        finished = self.done_already + self.done
        grand = self.done_already + self.total
        pct = finished / grand * 100 if grand else 100.0
        elapsed = time.time() - self.t0
        eta = (elapsed / self.done * (self.total - self.done)) if self.done else 0
        core = (f"{finished}/{grand} {pct:5.1f}%  ✓{self.ok} ✗{self.failed}  "
                f"用时 {fmt_dur(elapsed)}  剩余 ~{fmt_dur(eta) if self.done else '--:--'}")
        if plain:
            return f"总进度 {core}"
        width = max(10, min(30, shutil.get_terminal_size((100, 24)).columns - 70))
        filled = int(width * finished / grand) if grand else width
        return f"总进度 [{'█' * filled}{'░' * (width - filled)}] {core}"

    def _rows(self) -> list[str]:
        cols = shutil.get_terminal_size((100, 24)).columns
        rows = []
        for slot in sorted(self.active):
            a = self.active[slot]
            rows.append(_clip(
                f"  seq{a['seq']:<5d} {_pad(a['label'], 34)} "
                f"[{a['step']}/{a['steps']}] {_pad(a['stage'], 12)} "
                f"{time.time() - a['t0']:4.0f}s", cols - 1))
        return rows

    def _paint(self, log: str | None = None, force: bool = False) -> None:
        if not self.tty:
            return
        now = time.time()
        if not force and log is None and now - self._last_paint < 0.15:
            return                              # 限流，避免频繁重绘闪烁
        with self._lock:
            self._last_paint = now
            buf = []
            if self._painted:
                buf.append(f"\033[{self._painted}A")   # 光标回到看板顶部
            if log:                                     # 完成日志滚在看板上方
                buf.append(f"\033[2K{log}\n")
            lines = [self._headline()] + self._rows()
            for ln in lines:
                buf.append(f"\033[2K{ln}\n")
            # 上一轮比这轮长时，清掉多余的残留行
            for _ in range(max(0, self._painted - len(lines))):
                buf.append("\033[2K\n")
            extra = max(0, self._painted - len(lines))
            if extra:
                buf.append(f"\033[{extra}A")
            self._painted = len(lines)
            sys.stdout.write("".join(buf))
            sys.stdout.flush()

    def refresh(self) -> None:
        """给心跳线程用：即使没有事件也刷新一下计时。"""
        self._paint(force=True)

    def close(self) -> None:
        with self._lock:
            self.active.clear()
        if self.tty and self._painted:
            sys.stdout.write(f"\033[{self._painted}A")
            for _ in range(self._painted):
                sys.stdout.write("\033[2K\n")
            sys.stdout.write(f"\033[{self._painted}A")
            self._painted = 0
            sys.stdout.flush()
        print(self._headline(plain=True), flush=True)


class Heartbeat:
    """后台线程定时刷新看板，让耗时长的章节也能看到计时在走。"""

    def __init__(self, progress: Progress, interval: float = 1.0):
        self.p, self.interval = progress, interval
        self._stop = threading.Event()
        self._t: threading.Thread | None = None

    def __enter__(self):
        if self.p.tty:
            self._t = threading.Thread(target=self._run, daemon=True)
            self._t.start()
        return self

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            self.p.refresh()

    def __exit__(self, *exc) -> None:
        self._stop.set()
        if self._t:
            self._t.join(timeout=2)
