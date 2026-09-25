"""轻量静态审计：找出"写了但没人读"的规则接线问题。

Phase 10.4.2 / 10.4.3 里真正抓到的 bug 都是同一类形状——**一边写了，
另一边从来没读**。这个脚本只做三条静态检查，不做 AST 框架、不做规则推导：

1. 死事件      技能的 SkillBinding 监听的事件名，全项目没有任何 emit
2. 死标记      ``x._private = ...`` 形式的实体牌 / 玩家标记没有读取者
3. 死 scope    技能用 ResetScope 声明了生命周期，但该 scope 没有消费点

用法：``python -m tools.skill_static_audit``（纯静态，不需要 pygame 窗口）。
退出码非 0 表示发现了可疑项。
"""

import io
import os
import pathlib
import re
import sys

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "src"


def _iter_sources():
    for path in sorted(SRC.rglob("*.py")):
        yield path, io.open(path, encoding="utf-8").read()


def _rel(path):
    return path.relative_to(ROOT).as_posix()


# ==================================================
# 1. 死事件
# ==================================================

def check_dead_events():
    """监听方只有一种写法：``SkillBinding(EventType.X)``。

    所以"监听但没人发" = 某个 EventType 只出现在 SkillBinding 里，
    在其它任何地方（emit、其它订阅、payload 比较）都没出现过。
    """

    from src.game.engine.events import EventType

    all_uses, listening = set(), set()
    pattern = re.compile(r"EventType\.([A-Z_]+)")

    for _path, text in _iter_sources():
        for match in pattern.finditer(text):
            all_uses.add(match.group(1))
        for match in re.finditer(r"SkillBinding\(([^)]*)\)", text, re.S):
            for name in pattern.findall(match.group(1)):
                listening.add(name)

    never = sorted(listening - all_uses)
    unknown = sorted(name for name in all_uses if not hasattr(EventType, name))
    return never, unknown


# ==================================================
# 2. 死标记
# ==================================================

MARKER_WRITE = re.compile(r"(\w+)\.(_[a-z][a-z0-9_]*)\s*=")
MARKER_ANY = re.compile(r"_([a-z][a-z0-9_]*)")


def check_dead_markers():
    writes, reads = {}, {}
    for path, text in _iter_sources():
        for index, line in enumerate(text.splitlines(), 1):
            for match in MARKER_WRITE.finditer(line):
                writes.setdefault(match.group(2), []).append("%s:%d" % (_rel(path), index))
            stripped = MARKER_WRITE.sub("", line)
            for match in MARKER_ANY.finditer(stripped):
                reads.setdefault("_" + match.group(1), []).append("%s:%d" % (_rel(path), index))

    dead = {}
    for name, sites in sorted(writes.items()):
        if reads.get(name):
            continue
        # UI 层自己的按下反馈状态不影响规则，单独归类，不当作规则接线问题。
        kind = "ui" if all("/ui/" in site or "renderer.py" in site for site in sites) else "rule"
        dead[name] = (kind, sites)
    return dead


# ==================================================
# 3. 未消费的 scope
# ==================================================

def check_reset_scopes():
    """ResetScope 值必须至少有一个 clear_scope 消费点，否则状态永不清理。"""

    used = set()
    for _path, text in _iter_sources():
        for match in re.finditer(r"ResetScope\.([A-Z_]+)", text):
            used.add(match.group(1))

    consumed = set()
    for _path, text in _iter_sources():
        for match in re.finditer(r"clear_scope\(\s*ResetScope\.([A-Z_]+)", text):
            consumed.add(match.group(1))

    # PHASE / TURN / ROUND 需要真实消费点；PERSISTENT 由解绑 / 重置负责。
    required = {name for name in used if name != "PERSISTENT"}
    return sorted(required - consumed), sorted(used)


def main():
    problems = []

    never, unknown = check_dead_events()
    print("== 1. 死事件（技能监听但全项目没有发出者）==")
    if never:
        for name in never:
            print("   [x] %s" % name)
        problems.append("dead events: " + ", ".join(never))
    else:
        print("   [ok] 没有死事件")
    if unknown:
        print("   [?] 未在 EventType 中声明的事件名: %s" % ", ".join(unknown))

    dead_markers = check_dead_markers()
    print()
    print("== 2. 死标记（只写不读的私有字段）==")
    rule_dead = {n: v for n, v in dead_markers.items() if v[0] == "rule"}
    ui_dead = {n: v for n, v in dead_markers.items() if v[0] == "ui"}
    for name, (_kind, sites) in sorted(rule_dead.items()):
        print("   [x] %s  写入: %s" % (name, ", ".join(sites)))
    for name, (_kind, sites) in sorted(ui_dead.items()):
        print("   [-] %s（UI 状态，不影响规则）写入: %s" % (name, ", ".join(sites)))
    if rule_dead:
        problems.append("dead markers: " + ", ".join(sorted(rule_dead)))
    else:
        print("   [ok] 规则层没有只写不读的标记")

    missing, all_scopes = check_reset_scopes()
    print()
    print("== 3. 未消费的 ResetScope（状态永不清理）==")
    print("   已声明的 scope: %s" % ", ".join(all_scopes))
    if missing:
        for name in missing:
            print("   [x] ResetScope.%s 没有 clear_scope 消费点" % name)
        problems.append("unconsumed scopes: " + ", ".join(missing))
    else:
        print("   [ok] 每个 scope 都有消费点")

    print()
    if problems:
        print("静态审计：发现 %d 类问题" % len(problems))
        return 1
    print("静态审计：通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
