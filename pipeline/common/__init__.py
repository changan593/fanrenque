"""公共模块。导入即生效的一件事：把标准输出统一成 UTF-8。

Windows 下 Python 的 stdout 被管道接走时按系统代码页（中文系统是 GBK）编码，
脚本收尾打印的 ✓ ✗ 会直接抛 UnicodeEncodeError——check_all 捕获子进程输出时
五道闸门全部在最后一行炸掉，看着像全红。所有脚本都 import common，
在这里 reconfigure 一次，比在每个脚本里各写一遍稳。
直连控制台时 Windows 走的是 Unicode 控制台接口，这一句是空操作。
"""
import sys as _sys

for _stream in (_sys.stdout, _sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8")
        except (ValueError, OSError):
            pass
