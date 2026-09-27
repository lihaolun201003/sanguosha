"""F1 演出调试面板（Presentation System 2.0）。

默认关闭。开关只影响本机画面，**不改变任何规则、不产生任何网络流量**。

它存在的理由是这一轮反复出现的三类问题都"看不见"：

* 演出排队对不对（``queue`` / ``current``）——判定还在演时，后面的演出排到了哪；
* 输入为什么点不动（``input``）——是被判定锁住、被模态占住，还是演出在让路；
* 两端表现是否一致（``speed`` / ``pending``）——客户端与房主的动画各自多快。

面板只读这些状态，不提供任何"直接推进 / 跳过规则"的操作。
"""

import pygame

from . import theme
from .input_lock import resolve as resolve_input
from .widgets import draw_panel

#: 开关（F1 切换；模块级是给不带 Renderer 的工具用）。
ENABLED = False

LINE_HEIGHT = 20
PANEL_WIDTH = 430
PANEL_HEIGHT = 232


def toggle():
    global ENABLED

    ENABLED = not ENABLED
    return ENABLED


def _player_label(player):
    if player is None:
        return "-"
    name = getattr(player, "name", "") or getattr(player, "player_id", "")
    return str(name or "?")


def snapshot(game, renderer=None):
    """采集一帧的诊断数据（供面板与试玩报告共用）。"""

    effects = getattr(renderer, "effects", None) if renderer is not None else None
    storyboard = getattr(effects, "storyboard", None)
    rows = []

    # ---- 演出队列 ----
    if storyboard is not None:
        current = storyboard.current
        pending = len(storyboard.pending)
        rows.append(("queue", "queue=%d current=%s pending=%d played=%d dropped=%d" % (
            1 if current is not None else 0,
            current.describe() if current is not None else "-",
            pending, storyboard.played, storyboard.dropped)))
        rows.append(("present", "presenting=%s holds_ui=%s" % (
            bool(getattr(getattr(game, "presentation_gate", None), "presenting", False)),
            bool(storyboard.holding))))
    else:
        rows.append(("queue", "queue=-"))

    # ---- 输入锁 ----
    lock = resolve_input(game, effects)
    rows.append(("input", "input=%s blocking=%s" % (lock.describe(),
                                                    "yes" if lock.blocks_play() else "no")))

    # ---- 速度 ----
    speed = getattr(game, "speed", None)
    from . import anim_config

    factor = anim_config.speed_factor(speed) if speed else 1.0
    rows.append(("speed", "speed=%s x%.2f storyboard x%.2f" % (
        getattr(game, "speed_label", "-"), factor,
        getattr(storyboard, "speed_factor", 1.0) if storyboard is not None else 1.0)))

    # ---- 请求 / 响应 ----
    request = getattr(game, "pending_request", None)
    request_text = "-"
    if request is not None:
        request_type = getattr(request, "request_type", "?")
        request_text = "%s→%s" % (
            getattr(request_type, "value", request_type) or "?",
            _player_label(getattr(request, "target", None)))
    rows.append(("pending", "pending=%s" % request_text))
    responding = None
    if renderer is not None and hasattr(renderer, "responding_player"):
        try:
            responding = renderer.responding_player(game)
        except Exception:                              # noqa: BLE001 - 诊断绝不能抛
            responding = None
    rows.append(("responding", "responding=%s" % _player_label(responding)))

    # ---- 表现层其它计数 ----
    if effects is not None:
        flights = getattr(getattr(effects, "transfers", None), "flights", ()) or ()
        active_flights = sum(1 for flight in flights if not flight.arrived)
        rows.append(("fx", "flights=%d arrows=%d floats=%d toasts=%d" % (
            active_flights, len(getattr(effects, "arrows", ()) or ()),
            len(getattr(effects, "floats", ()) or ()),
            len(getattr(getattr(renderer, "toasts", None), "items", ()) or ()))))
    else:
        rows.append(("fx", "flights=-"))

    if getattr(game, "local_interaction", True):
        rows.append(("turn", "phase=%s current=%s me=%s" % (
            getattr(game, "phase", "-"), _player_label(
                getattr(game, "current_turn_player", None)),
            _player_label(getattr(game, "player", None)))))
    else:
        rows.append(("turn", "view-only phase=%s" % getattr(game, "phase", "-")))

    errors = getattr(effects, "story_errors", None) if effects is not None else None
    rows.append(("errors", "story_errors=%d" % len(errors or ())))
    return rows


def draw(surface, game, metrics, renderer=None):
    """画面板（默认关闭时什么都不做）。"""

    if not ENABLED:
        return None
    rows = snapshot(game, renderer)
    fonts = metrics.fonts
    label_font = fonts.get("micro")
    value_font = fonts.get("micro")

    rect = pygame.Rect(
        metrics.px(16), metrics.px(108),
        min(metrics.px(PANEL_WIDTH), int(metrics.screen_w * 0.44)),
        metrics.px(24 + LINE_HEIGHT * len(rows)),
    )
    draw_panel(surface, rect, fill=theme.PANEL_DEEP, border=theme.HEAL,
               border_width=2, radius=metrics.px(8))
    title = fonts.get("small").render("F1 演出调试", True, theme.HEAL)
    surface.blit(title, (rect.x + metrics.px(10), rect.y + metrics.px(6)))

    y = rect.y + metrics.px(26)
    for name, text in rows:
        label = label_font.render(str(name), True, theme.TEXT_MUTED)
        surface.blit(label, (rect.x + metrics.px(10), y))
        value = value_font.render(str(text), True, theme.TEXT)
        surface.blit(value, (rect.x + metrics.px(86), y))
        y += metrics.px(LINE_HEIGHT)
    return rect
