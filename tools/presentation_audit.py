"""Presentation System 2.0 自检：补间 / 输入锁 / 牌移动 / 手牌平滑 / 跳过演出。

固化的判据都是"这一轮真出现过、且肉眼很难每次都验一遍"的行为：改动演出层
之后跑一遍，比重新手玩一局快得多。

    python tools/presentation_audit.py
"""

import os
import sys

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame

pygame.init()
screen = pygame.display.set_mode((1280, 800))

from src.game import Game
from src.renderer import Renderer
from src.ui import anim_config, card_transfer, input_lock, tween

FAILURES = []
CHECKS = [0]


def check(name, ok, detail=""):
    CHECKS[0] += 1
    if ok:
        print("[通过] %s%s" % (name, ("  —— " + str(detail)) if detail else ""))
    else:
        print("[失败] %s%s" % (name, ("  —— " + str(detail)) if detail else ""))
        FAILURES.append(name)


def make_game(seed=1, ai_count=2):
    game = Game(ai_count=ai_count)
    game.rng.seed(seed)
    game.scene = "general_select"
    game.general_candidates = game.roll_general_candidates()
    game.confirm_general(game.general_candidates[0])
    game.scene = "game"
    return game


# ==================================================
# 1. 补间
# ==================================================

def audit_tween():
    print("== 【补间】==")
    value = tween.Tween(0.0, 10.0, 0.4, "linear")
    steps = []
    for _ in range(4):
        value.update(0.1)
        steps.append(value.value)
    check("1.1 linear 补间按时间线性推进",
          all(abs(steps[i] - (i + 1) * 2.5) < 0.01 for i in range(4)), steps)
    check("1.2 走完时长后到达终点", value.done and abs(value.value - 10.0) < 0.001)

    for name in ("linear", "ease_in_quad", "ease_out_quad", "ease_in_out_quad",
                 "ease_out_cubic", "ease_in_cubic", "ease_out_back"):
        easing = tween.easing(name)
        check("1.3 缓动 %s 两端归位" % name,
              abs(easing(0.0)) < 1e-6 and abs(easing(1.0) - 1.0) < 1e-6)

    mid = tween.Tween(0.0, 1.0, 1.0, "ease_out_cubic")
    mid.update(0.5)
    check("1.4 ease_out_cubic 前半程走得更多（缓出）", mid.value > 0.5, mid.value)

    track = tween.TweenTrack(0.2)
    track.set("a", 0.0)         # 就位到 0，再从 0 补间到 5
    track.to("a", 5.0)
    track.update(0.1)
    half = track.value("a")
    track.to("a", 5.0)          # 目标不变
    track.update(0.1)
    check("1.5 目标不变时不重新计时（否则永远走不到终点）",
          track.value("a") > half, (half, track.value("a")))

    track.to("a", 9.0)
    check("1.6 改目标后从当前位置出发（不跳变）",
          track.value("a") > half and track.value("a") < 9.0)

    points = tween.PointTrack(0.2)
    points.to("p", (10, 20))
    check("1.7 首次出现直接就位（不做位移动画）", points.value("p") == (10, 20))
    points.update(0.1)
    points.to("p", (100, 20))
    mid_point = points.value("p")[0]
    check("1.8 已存在的点平滑移动", 10 <= mid_point < 100, mid_point)


# ==================================================
# 2. 统一时长与速度
# ==================================================

def audit_anim_config():
    print("== 【统一动画时长】==")
    anim_config.set_factor(1.0)
    check("2.1 默认档时长与配置表一致",
          abs(anim_config.duration("hover") - 0.12) < 1e-6,
          anim_config.duration("hover"))
    anim_config.set_speed(2.0)
    fast = anim_config.duration("hover")
    check("2.2 快档时长更短", fast < 0.12, fast)
    anim_config.set_speed(0.4)
    slow = anim_config.duration("hover")
    check("2.3 慢档时长更长", slow > 0.12, slow)
    anim_config.set_speed(0.75)
    check("2.4 默认速度档回到 1.0 倍",
          abs(anim_config.current().factor - 1.0) < 1e-6)
    check("2.5 规则 timeout 不在表里（只是界面时长）",
          "timeout" not in anim_config.BASE_DURATIONS)


# ==================================================
# 3. 输入优先级
# ==================================================

def audit_input_lock():
    print("== 【输入优先级】==")
    priorities = input_lock.InputPriority
    check("3.1 判定高于强制响应",
          priorities.JUDGE > priorities.MANDATORY_RESPONSE)
    check("3.2 强制响应 > 模态 > 选目标 > 选牌 > 普通",
          priorities.MANDATORY_RESPONSE > priorities.MODAL
          > priorities.TARGET_SELECTION > priorities.CARD_SELECTION
          > priorities.NORMAL_PLAY)

    lock = input_lock.InputLock("normal")
    check("3.3 普通层允许出牌", lock.allows("normal"))
    judge = input_lock.InputLock("judge")
    check("3.4 判定层挡住普通出牌", not judge.allows("normal"))
    check("3.5 判定层仍允许本地设置（速度 / 跳过）",
          judge.allows(input_lock.ALWAYS))
    modal = input_lock.InputLock("modal")
    check("3.6 模态层挡住选目标与出牌",
          not modal.allows("target") and not modal.allows("normal"))
    check("3.7 模态层允许模态自己", modal.allows("modal"))

    game = make_game()
    renderer = Renderer(screen)
    renderer.effects.attach(game)
    resolved = input_lock.resolve(game, renderer.effects)
    check("3.8 正常出牌阶段解析成 normal 层", resolved.layer == "normal",
          resolved.describe())

    game.pending_selection = {"zone": "hand", "selected": [], "minimum": 1,
                              "maximum": 1, "candidates": []}
    resolved = input_lock.resolve(game, renderer.effects)
    check("3.9 选牌窗口解析成 selection 层", resolved.layer == "selection",
          resolved.describe())
    game.pending_selection = None
    resolved = input_lock.resolve(game, renderer.effects)
    check("3.10 清掉窗口后回到 normal 层", resolved.layer == "normal")


# ==================================================
# 4. 卡牌移动（统一表现）
# ==================================================

def audit_card_transfer():
    print("== 【实体牌移动】==")
    game = make_game(seed=3)
    renderer = Renderer(screen)
    renderer.begin_frame(game, (500, 400))
    effects = renderer.effects
    card = game.player.hand[0]

    flight = effects.move_card(
        card, card_transfer.ZONE_DECK, card_transfer.ZONE_HAND,
        owner=game.player, visibility=card_transfer.HIDDEN)
    check("4.1 登记的移动会产生一次飞行", flight is not None)
    check("4.2 飞行中的牌被表现层独占（静态区域不再画）",
          id(card) in effects.owned_card_ids())
    check("4.3 手牌区暂时不画它（避免同一张牌两处出现）",
          id(card) in effects.dealing_card_ids(game.player))
    start = flight.position
    for _ in range(30):
        effects.update(1 / 60.0)
    check("4.4 飞行推进后位置改变", flight.position != start or flight.done,
          (start, flight.position))
    effects.transfers.finish_all()
    check("4.5 跳过演出让所有牌立刻落位",
          not effects.transfers.busy() and id(card) not in effects.owned_card_ids())

    zones = {card_transfer.ZONE_DECK, card_transfer.ZONE_HAND,
             card_transfer.ZONE_DISCARD, card_transfer.ZONE_EQUIPMENT,
             card_transfer.ZONE_JUDGE, card_transfer.ZONE_PUBLIC_POOL,
             card_transfer.ZONE_TABLE, card_transfer.ZONE_SEAT}
    check("4.6 八个牌区都有名字与落点（五谷/判定/装备/弃牌共用一套）",
          set(card_transfer.ZONES) == zones and
          all(zone in card_transfer.ZONE_LABELS for zone in zones))

    resolved = effects.zone_of(game.deck.discard_pile)
    check("4.7 弃牌堆能被认出来", resolved[0] == card_transfer.ZONE_DISCARD)
    resolved = effects.zone_of(game.player.hand)
    check("4.8 手牌能被认出来并带上归属",
          resolved[0] == card_transfer.ZONE_HAND and resolved[1] is game.player)


# ==================================================
# 5. 手牌平滑与命中
# ==================================================

def audit_hand_motion():
    print("== 【手牌互动】==")
    game = make_game(seed=5)
    renderer = Renderer(screen)

    # 只用 ``begin_frame``（它带着鼠标位置建布局）：``draw`` 会用**真实**鼠标
    # 位置重建一次，在无头环境里那是 (0,0)，测不到悬停。
    rects = renderer.get_card_rects(game.player.hand)
    base_y = rects[0].y
    renderer.begin_frame(game, None)
    rects = [pygame.Rect(rect) for rect in renderer.table_layout.hand_rects]
    check("5.1 没有悬停时手牌在基础位置", rects[0].y == base_y, rects[0].y)

    # 鼠标停在第一张牌上：位置应逐帧上移（不是瞬间跳）
    center = rects[0].center
    renderer.begin_frame(game, center)
    first = renderer.table_layout.hand_rects[0].y
    check("5.2 悬停第一帧只走一小步（不是瞬移）",
          base_y - 40 < first <= base_y, first)
    for _ in range(30):
        renderer.update(1 / 60.0)
        renderer.begin_frame(game, center)
    settled = renderer.table_layout.hand_rects[0].y
    check("5.3 悬停稳定后抬起到固定高度", settled < first, (first, settled))
    check("5.4 悬停抬起量就是布局里的 hover_lift",
          base_y - settled == renderer.metrics.px(28), (base_y, settled))

    # 命中测试用的是**画出来的** rect
    rendered = pygame.Rect(renderer.table_layout.hand_rects[0])
    check("5.5 抬起后点新位置仍然命中第一张",
          renderer.card_at_position(rendered.center, game.player.hand) == 0)

    # 重新排序：去掉第一张后，其余牌平滑左移而不是瞬移
    renderer.update(1 / 60.0)
    renderer.begin_frame(game, None)
    before = [pygame.Rect(rect) for rect in renderer.table_layout.hand_rects]
    del game.player.hand[0]
    renderer.update(1 / 60.0)
    renderer.begin_frame(game, None)
    after = [pygame.Rect(rect) for rect in renderer.table_layout.hand_rects]
    moved = after[0].x != before[1].x
    check("5.6 手牌减少后其余牌是移动过去的（不在同一帧跳到终点）",
          moved or len(after) == 0, (before[1].x, after[0].x if after else None))


# ==================================================
# 6. 跳过演出只跳动画
# ==================================================

def audit_skip():
    print("== 【跳过演出】==")
    game = make_game(seed=7)
    renderer = Renderer(screen)
    renderer.begin_frame(game, None)
    effects = renderer.effects
    before_hp = game.player.hp
    before_hand = list(game.player.hand)
    before_phase = game.phase

    effects.present_result("这是一条很长的结算结论", detail="等待看完", min_duration=5.0)
    check("6.1 演出已排队", effects.storyboard.busy)
    effects.skip_presentation()
    for _ in range(40):
        effects.update(1 / 60.0)
    check("6.2 跳过之后队列很快排空", not effects.storyboard.busy,
          effects.storyboard.describe())
    check("6.3 跳过演出没有改动任何规则数据",
          game.player.hp == before_hp and game.player.hand == before_hand
          and game.phase == before_phase)


def audit_debug_overlay():
    print("== 【F1 演出调试】==")
    from src.ui import debug_overlay
    game = make_game(seed=9)
    renderer = Renderer(screen)
    renderer.begin_frame(game, (500, 400))
    renderer.draw(game)
    rows = debug_overlay.snapshot(game, renderer)
    keys = {name for name, _text in rows}
    check("7.1 调试面板列出队列 / 输入 / 速度 / 请求 / 响应者",
          {"queue", "input", "speed", "pending", "responding"} <= keys, sorted(keys))
    check("7.2 默认关闭（不影响正常画面）", debug_overlay.ENABLED is False)
    check("7.3 F1 可以切换", debug_overlay.toggle() is True
          and debug_overlay.toggle() is False)
    check("7.4 面板绘制不抛异常",
          debug_overlay.draw(screen, game, renderer.metrics, renderer) is None
          or True)


def main():
    audit_tween()
    audit_anim_config()
    audit_input_lock()
    audit_card_transfer()
    audit_hand_motion()
    audit_skip()
    audit_debug_overlay()
    print("=" * 68)
    print("共 %d 项，通过 %d，失败 %d" % (
        CHECKS[0], CHECKS[0] - len(FAILURES), len(FAILURES)))
    for name in FAILURES:
        print("  失败：", name)
    print("=" * 68)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
