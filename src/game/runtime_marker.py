"""开发期 Runtime Marker：开局时把"这一局真的是什么"打在控制台上。

动机（Phase 11.4.3）：上一轮的"联机已接回身份局"结论来自工具，而工具自己
调了 ``set_mode("identity")`` 与 ``apply_identities``，绕过了真实开局路径；
真人运行的 ``HostMatch.start()`` 走的是另一条代码路径，于是屏幕上是一局
二人裸局而报告全绿。

这里换一种做法：开局结束后把**真实结论**直接打到控制台——人数、真人 / AI
构成、身份配比、武将、控制器，以及创建这一局的代码位置。它与界面无关，
只在控制台输出，所以既不会污染正式 UI，也能在真人运行时被看到。

``SGS_RUNTIME_MARKER=0`` 可关闭；测试与工具默认也会用它作为断言来源。
"""

import os
import sys

#: 关掉 Marker 的环境变量（"0" / "off" / "false" / "no" 视为关闭）。
MARKER_ENV = "SGS_RUNTIME_MARKER"

_OFF_VALUES = ("0", "off", "false", "no")


def marker_enabled(environ=None):
    """Marker 是否开启（默认开启：它只往控制台打印，不影响界面）。"""

    value = ((environ or os.environ).get(MARKER_ENV) or "").strip().lower()
    return value not in _OFF_VALUES


def creation_origin(depth=1):
    """创建这一局的代码位置（``相对路径:函数名``），供报告与测试核对。

    只认项目内的帧：跳过本模块与标准库，找不到时返回空串。用 ``sys._getframe``
    而不是 ``inspect.stack()``：后者会读取源码行，每局开局都调用太贵。
    """

    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    frame = sys._getframe(depth)
    while frame is not None:
        filename = str(frame.f_code.co_filename)
        if os.path.basename(filename) != "runtime_marker.py" and filename.startswith(root):
            relative = os.path.relpath(filename, root).replace("\\", "/")
            if relative.startswith(("src/", "tools/", "tests/")):
                return "%s:%s()" % (relative, frame.f_code.co_name)
        frame = frame.f_back
    return ""


def _controller_label(game, player):
    """这个座位由谁控制：已建好的控制器类名，或声明的控制器类型。

    这里**不触发**控制器创建（那会在 Marker 里就跑起 AI 回合）。
    """

    kind = _controller_type_name(player)
    controller = (getattr(game, "controllers", None) or {}).get(player.player_id)
    if controller is not None:
        return "%s(%s)" % (kind, type(controller).__name__)
    return "%s(待建)" % kind


def _controller_type_name(player):
    value = getattr(player, "controller_type", None)
    return getattr(value, "value", None) or str(value or "ai")


def _identity_counts(game):
    from src.game.identity import identity_name

    counts = {}
    for player in getattr(game, "players", ()) or ():
        identity = getattr(player, "identity", None)
        if identity is None:
            continue
        name = identity_name(identity)
        counts[name] = counts.get(name, 0) + 1
    return counts


def describe_battle(game, *, origin=None, tag="IDENTITY RUNTIME"):
    """把当前这一局的关键事实写成多行文本（纯读，不改任何状态）。"""

    players = list(getattr(game, "players", ()) or ())
    local_human = [item for item in players if item.is_human]
    remote = [
        item for item in players
        if _controller_type_name(item) == "remote_human"
    ]
    ai = [item for item in players if item.is_ai]

    identities = _identity_counts(game)
    assigned = bool(identities)
    with_general = [item for item in players if getattr(item, "general_id", None)]

    lines = ["=== LAN %s ===" % tag]
    lines.append("created_by = " + (origin if origin is not None
                                    else creation_origin(2) or "unknown"))
    lines.append("game_mode = " + str(getattr(game, "mode_id", "") or ""))
    lines.append("players = " + str(len(players)))
    # "真人"= 本地真人 + 远程真人（身份局的真人总数），AI 是补位的那部分。
    lines.append("human = " + str(len(local_human) + len(remote)))
    lines.append("local_human = " + str(len(local_human)))
    lines.append("remote_human = " + str(len(remote)))
    lines.append("ai = " + str(len(ai)))
    lines.append("controllers = [" + ", ".join(
        "%s:%s=%s" % (player.player_id, player.name, _controller_label(game, player))
        for player in players
    ) + "]")
    lines.append("identities = " + ("、".join(
        "%s x%d" % (name, count) for name, count in identities.items()
    ) if identities else "（未分配）"))
    lines.append("identities_assigned = " + str(assigned))
    lines.append("generals_assigned = " + str(len(with_general) == len(players)))
    lines.append("generals = [" + ", ".join(
        "%s=%s" % (player.player_id, player.general_id or "-")
        for player in players
    ) + "]")
    return "\n".join(lines)


def print_battle_marker(game, *, origin=None, tag="IDENTITY RUNTIME", stream=None):
    """按需打印 Marker；返回打印出来的文本（关闭时返回空串）。"""

    if not marker_enabled():
        return ""
    text = describe_battle(game, origin=origin, tag=tag)
    print(text, file=stream or sys.stdout, flush=True)
    return text
