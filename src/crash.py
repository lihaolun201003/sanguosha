"""崩溃日志：让"双击没反应 / 一闪就没了"变成一份可读的日志。

release 版是 ``--windowed``（没有控制台），未捕获异常默认**什么都不显示**，
玩家只会看到游戏窗口消失。这里做最小的处理：

* 把未捕获异常（主线程 + 网络等子线程）写进
  ``%LOCALAPPDATA%\\Sanguosha\\logs\\crash.log``；
* 再弹一个**原生**中文对话框告诉玩家日志在哪、让 TA 把文件发回来；
* 绝不吞掉异常：写日志失败也要把 traceback 打到 stderr（有控制台的 debug
  构建还能看到完整栈）。

它**只**接管 ``sys.excepthook`` / ``threading.excepthook``——不改变任何既有
异常处理路径（业务代码里正常的 try/except 完全不受影响）。
"""

import datetime
import os
import sys
import traceback

from src import paths

#: 版本号写进日志抬头；没有就用 "dev"（源码运行）。
VERSION = "dev"

#: 日志文件名。
LOG_NAME = "crash.log"

#: 日志最多留几条（避免无限增长：每次崩溃追加一段，超过就整文件重开）。
MAX_ENTRIES = 20

_installed = False


def log_path():
    return os.path.join(paths.log_dir(), LOG_NAME)


def _version():
    """尽量给出可辨认的版本：打包时由 spec 写入 ``_version.txt``。"""

    try:
        marker = os.path.join(paths.bundle_root(), "_version.txt")
        if os.path.exists(marker):
            with open(marker, "r", encoding="utf-8") as handle:
                return handle.read().strip() or VERSION
    except OSError:
        pass
    return VERSION


def _trim(text):
    lines = text.splitlines()
    if len(lines) <= MAX_ENTRIES * 60:
        return text
    return "\n".join(lines[-MAX_ENTRIES * 60:])


def write(header, exc_type, exc_value, tb):
    """把一次异常追加进崩溃日志；返回日志路径（失败返回空串）。"""

    try:
        path = log_path()
        stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        body = "".join(traceback.format_exception(exc_type, exc_value, tb))
        block = (
            "==========================================================\n"
            "[%s] %s\n"
            "版本: %s\n"
            "运行: %s\n"
            "异常: %s: %s\n"
            "----------------------------------------------------------\n"
            "%s" % (stamp, header, _version(), paths.describe(),
                    getattr(exc_type, "__name__", exc_type), exc_value, body)
        )
        existing = ""
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as handle:
                    existing = handle.read()
            except OSError:
                existing = ""
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(_trim(existing + block))
        return path
    except Exception:                                    # noqa: BLE001 - 兜底
        return ""


def _message_box(text):
    """原生中文提示框（拿不到就什么都不做，退回日志）。"""

    if os.name != "nt":
        return False
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, text, "三国杀 · 出错了", 0x40010)
        return True
    except Exception:                                    # noqa: BLE001
        return False


def _report(header, exc_type, exc_value, tb, *, notify):
    path = write(header, exc_type, exc_value, tb)
    traceback.print_exception(exc_type, exc_value, tb)
    if notify:
        where = ("已写入日志：\n" + path) if path else "（日志写入失败）"
        _message_box(
            "游戏遇到了一个未预期的错误，已经停止。\n\n"
            + where
            + "\n\n请把这个 crash.log 文件发回给作者，谢谢！"
        )
    return path


def ensure_streams():
    """保证 ``sys.stdout`` / ``sys.stderr`` 是可写的流。

    ``--windowed`` 的发布版**双击**启动时这两个是 ``None``（没有控制台），
    任何一句 ``print`` 都会抛 ``AttributeError: 'NoneType' object has no
    attribute 'write'`` ——而它可能出现在很深的地方（工具脚本、诊断输出），
    表现就是"双击闪一下就没了"。有管道时（从命令行重定向运行）没这个问题，
    所以只在真正为 None 时才接管，并把输出丢给 devnull。
    """

    import io
    import os as _os

    for name in ("stdout", "stderr"):
        if getattr(sys, name, None) is not None:
            continue
        try:
            setattr(sys, name, open(_os.devnull, "w", encoding="utf-8"))
        except OSError:
            try:
                setattr(sys, name, io.StringIO())
            except Exception:                            # noqa: BLE001
                pass


def install():
    """接管未捕获异常（主线程 + 子线程）。重复调用无副作用。"""

    global _installed

    if _installed:
        return False

    def hook(exc_type, exc_value, tb):
        _report("主线程未捕获异常", exc_type, exc_value, tb, notify=True)

    sys.excepthook = hook

    # 网络线程（LAN 的收包 / 心跳跑在后台线程）里漏出来的异常同样要留痕：
    # 否则表现只是"联机卡住不动"，玩家拿不到任何线索。
    try:
        import threading

        def thread_hook(args):
            _report("子线程未捕获异常（%s）" % getattr(args.thread, "name", "?"),
                    args.exc_type, args.exc_value, args.exc_traceback,
                    notify=False)

        threading.excepthook = thread_hook
    except Exception:                                    # noqa: BLE001
        pass

    _installed = True
    return True
