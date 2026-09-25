"""Phase 10.5 smoke：通用判定面板 / 改判 / 技能栏 / Tooltip / 分辨率。

用法：``python -m tools.phase10_5_smoke``（SDL 走 dummy 驱动，不弹窗口）。
截图写到 ``tools/ui_snapshots/``，每项打印 PASS / FAIL。

只跑**少量**对局（4 人 / 8 人各一局 + 5 人身份模式的基础渲染），
不做大规模随机跑局。
"""

import os
import sys

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame

from src.game import Game
from src.game.engine import EventType, SelectCardsAction
from src.game.flows import JudgeFlow
from src.renderer import Renderer
from src.ui import layout as layout_module
from src.ui import theme
from src.ui.judge import JudgeStage
from src.ui.skill_bar import SkillBar
from src.ui.widgets import place_tooltip
from tests.legacy_helpers import canonical_card, normal_sha, set_draw_order, shan, tao


OUTDIR = os.path.join(os.path.dirname(__file__), "ui_snapshots")
RESOLUTION = (1920, 1080)
RESOLUTIONS = ((1280, 720), (1366, 768), (1600, 900), (1920, 1080), (2560, 1440))

RESULTS = []


def check(label, passed, detail=""):
    RESULTS.append((label, bool(passed), detail))
    print("%-4s %s%s" % ("PASS" if passed else "FAIL", label,
                         ("  — " + str(detail)) if detail else ""))
    return passed


def save(renderer, name):
    path = os.path.join(OUTDIR, name + ".png")
    pygame.image.save(renderer.screen, path)
    return path


def make_game(ai_count):
    game = Game(ai_count=ai_count)
    game.scene = "game"
    game.actions.clear()
    game.engine.reset()
    for player in game.players:
        player.hand = []
        player.hp = player.max_hp
        player.alive = True
    game.phase = "play"
    return game


def watch(game):
    revealed, results, replaced = [], [], []
    game.context.events.subscribe(
        EventType.JUDGE_REVEALED, lambda _c, e: revealed.append(e.payload["result"]))
    game.context.events.subscribe(
        EventType.JUDGE_RESULT, lambda _c, e: results.append(e.payload["result"]))
    game.context.events.subscribe(
        EventType.JUDGE_REPLACED, lambda _c, e: replaced.append(e.payload))
    return revealed, results, replaced


def render(renderer, game, frames=1, mouse=None):
    for _ in range(frames):
        game.update(1 / 60)
        renderer.update(1 / 60)
        renderer.draw(game, mouse)
    pygame.display.flip()


# ==================================================
# 1 判定面板生命周期
# ==================================================

def smoke_judge_lifecycle(renderer):
    game = make_game(3)
    victim = game.players[1]
    victim.judgement_zone.append(canonical_card("LEBU"))
    _revealed, results, _replaced = watch(game)
    set_draw_order(game, [normal_sha()])
    render(renderer, game)
    JudgeFlow(game.engine, victim, "lebu").start()

    panel = renderer.effects.judge_panel
    check("判定：翻开后立刻展示面板", panel.active)
    check("判定：面板带着来源与规则文本",
          panel.spec is not None and panel.spec.display_name == "乐不思蜀"
          and bool(panel.spec.rule_text),
          panel.spec.rule_text if panel.spec else "")
    check("判定：判定牌就是引擎抽到的那张",
          panel.shown_card is not None and panel.shown_card.suit == "spade")

    panel.finish(results[-1])
    order, shots = [], {}
    for _ in range(3000):
        renderer.effects.judge_panel.update(1 / 60, game)
        renderer.draw(game)
        stage = renderer.effects.judge_panel.stage
        if not order or order[-1] is not stage:
            order.append(stage)
        if stage is JudgeStage.OUTCOME_HOLD and "outcome" not in shots:
            shots["outcome"] = True
            save(renderer, "phase10_5_judge_negative")
        if not renderer.effects.judge_panel.active:
            break

    check("判定：生命周期按固定顺序推进",
          order[0] is JudgeStage.OPEN and order[-1] is JudgeStage.DONE,
          " → ".join(stage.value for stage in order))
    check("判定：结果语义是 NEGATIVE（非红桃跳过出牌阶段）",
          panel.outcome is not None and panel.tone.value == "negative",
          panel.outcome.title if panel.outcome else "")
    check("判定：面板结束后自动收起", not panel.active)

    # 红色结果（八卦阵红判定）用同一块面板，颜色不同。
    game2 = make_game(3)
    board = game2.players[1]
    _r2, results2, _x2 = watch(game2)
    set_draw_order(game2, [tao()])
    JudgeFlow(game2.engine, board, "bagua").start()
    renderer.effects.judge_panel.finish(results2[-1])
    positive = renderer.effects.judge_panel
    check("判定：同一面板支持 POSITIVE 结果", positive.tone.value == "positive",
          positive.outcome.title if positive.outcome else "")
    check("判定：两种结果的语义色不同",
          theme.judge_tone_color("positive") != theme.judge_tone_color("negative"))


# ==================================================
# 2 鬼才改判
# ==================================================

def smoke_guicai(renderer):
    game = make_game(3)
    victim = game.player
    game.set_general(victim, "simayi")
    replacement = tao()
    victim.hand = [replacement]
    victim.judgement_zone.append(canonical_card("LEBU"))
    _revealed, results, replaced = watch(game)
    set_draw_order(game, [normal_sha()])
    render(renderer, game)

    flow = JudgeFlow(game.engine, victim, "lebu")
    flow.start()
    panel = renderer.effects.judge_panel
    original = panel.shown_card
    check("鬼才：判定不利时拿到改判窗口", game.pending_request is not None)

    game.engine.submit(SelectCardsAction(
        victim, game.pending_request.request_id, [replacement]))
    panel.note_replacement(replaced[0], game)
    panel.finish(results[-1])
    renderer.draw(game)
    save(renderer, "phase10_5_judge_replaced")

    check("鬼才：面板没有重开，仍然是同一块",
          panel.active and panel.was_replaced)
    check("鬼才：展示的判定牌被换成新的",
          panel.shown_card is replacement and panel.previous_card is original,
          "%s → %s" % (original.suit_name, replacement.suit_name))
    check("鬼才：改判来源与次数可读",
          panel.replacement_skill_name == "鬼才" and len(panel.replacement_history) == 1)
    check("鬼才：最终结果按新判定牌计算", panel.tone.value == "positive",
          panel.outcome.title if panel.outcome else "")


# ==================================================
# 3 技能栏
# ==================================================

def smoke_skill_bar(renderer):
    game = make_game(3)
    metrics = renderer.metrics
    for general_id, expected in (("zhaoyun", ["龙胆"]),
                                 ("zhouyu", ["英姿", "反间"]),
                                 ("sunshangxiang", ["结姻", "枭姬"])):
        game.set_general(game.player, general_id)
        bar = SkillBar()
        bar.sync_layout(metrics, 0)
        bar.sync(game)
        names = [definition.name for definition in bar.skills]
        check("技能栏：%s 显示真实技能名" % general_id, names == expected, str(names))

    game.set_general(game.player, "zhouyu")
    bar = SkillBar()
    bar.sync_layout(metrics, 0)
    bar.sync(game)
    render(renderer, game)
    bar.draw(renderer.screen, game, None)
    pygame.display.flip()
    save(renderer, "phase10_5_skill_bar")
    check("技能栏：锁定技与主动技都占按钮位", len(bar.rects) == 2)
    bar.info_skill_id = "fanjian"
    info = bar.info_text(game) or ""
    check("技能栏：说明来自 SkillDef",
          game.skill_registry.get("fanjian").description in info
          and "反间" in info and "主动技" in info)


# ==================================================
# 4 Tooltip placement
# ==================================================

def smoke_tooltip(renderer):
    viewport = renderer.screen.get_rect()
    cases = (
        ("右侧够位", pygame.Rect(200, 200, 80, 60)),
        ("贴右边缘", pygame.Rect(viewport.right - 100, 200, 80, 60)),
        ("贴下边缘", pygame.Rect(700, viewport.bottom - 60, 80, 40)),
    )
    ok = True
    for label, anchor in cases:
        rect = place_tooltip(anchor, (340, 240), viewport)
        inside = viewport.contains(rect)
        ok = ok and inside
        print("     %s → %s" % (label, tuple(rect)))
    check("Tooltip：所有候选位置都不越出视口", ok)

    game = make_game(6)
    renderer.draw(game)
    metrics = renderer.metrics
    # 真实场景：鼠标停在某个 AI 的装备槽上，提示框要避开提示条与手牌区。
    seat = next(rect for player, rect in renderer.table_layout.seat_rects.items()
                if player is not game.player)
    anchor = pygame.Rect(seat.centerx, seat.centery, 24, 24)
    rect = place_tooltip(anchor, (320, 260), renderer.screen.get_rect(),
                         avoid=renderer._tooltip_avoid_rects(metrics))
    check("Tooltip：不压住提示条与手牌区",
          not rect.colliderect(metrics.prompt) and not rect.colliderect(metrics.hand_area),
          tuple(rect))
    check("Tooltip：不盖住鼠标指向的那一点",
          not rect.colliderect(anchor), tuple(rect))


# ==================================================
# 5 分辨率
# ==================================================

def smoke_resolutions():
    ok_panel = ok_skill = ok_tooltip = True
    for width, height in RESOLUTIONS:
        screen = pygame.display.set_mode((width, height))
        renderer = Renderer(screen)
        metrics = layout_module.LayoutMetrics(width, height)
        screen_rect = pygame.Rect(0, 0, width, height)

        from src.ui.judge import JudgePanel

        panel_rect = JudgePanel().rect(metrics)
        ok_panel = ok_panel and screen_rect.contains(panel_rect) \
            and not panel_rect.colliderect(metrics.prompt) \
            and panel_rect.bottom <= metrics.hand_area.top

        game = Game(ai_count=3)
        game.scene = "game"
        game.actions.clear()
        game.engine.reset()
        game.set_general(game.player, "zhouyu")
        bar = SkillBar()
        bar.sync_layout(metrics, 0)
        bar.sync(game)
        ok_skill = ok_skill and all(screen_rect.contains(rect) for rect in bar.rects)

        box = place_tooltip(pygame.Rect(width // 2, height // 2, 40, 40),
                            (320, 240), screen_rect)
        ok_tooltip = ok_tooltip and screen_rect.contains(box)

    pygame.display.set_mode(RESOLUTION)
    Renderer(pygame.display.get_surface())
    check("分辨率：判定面板 1280×720 ～ 2560×1440 都不越界、不压提示条", ok_panel)
    check("分辨率：技能按钮不越界", ok_skill)
    check("分辨率：Tooltip 不越界", ok_tooltip)


# ==================================================
# 6 少量对局
# ==================================================

def smoke_games():
    from tools.multiplayer_smoke import run_smoke

    for ai_count in (3, 7):
        _game, result = run_smoke(ai_count=ai_count, verbose=False)
        check("%d 人自由混战不卡死" % (ai_count + 1),
              result.stuck_reason is None and result.error is None,
              "回合=%s" % result.turns)

    # 5 人身份模式：只确认规则与 UI 都能正常构建渲染。
    game = Game(ai_count=4)
    game.set_mode("identity")
    game.ai_count = 4
    game.scene = "game"
    game.actions.clear()
    game.engine.reset()
    game.phase = "play"
    for player in game.players:
        player.hand = [shan(), tao()]
        player.hp = player.max_hp
        player.alive = True
    renderer = Renderer(pygame.display.get_surface())
    renderer.draw(game)
    check("5 人身份模式人数合法且能渲染", game.total_players() == 5,
          str(game.total_players()))


def main():
    os.makedirs(OUTDIR, exist_ok=True)
    pygame.init()
    screen = pygame.display.set_mode(RESOLUTION)
    renderer = Renderer(screen)

    print("=== 1 判定面板生命周期 ===")
    smoke_judge_lifecycle(renderer)
    print("=== 2 鬼才改判 ===")
    smoke_guicai(renderer)
    print("=== 3 技能栏 ===")
    smoke_skill_bar(renderer)
    print("=== 4 Tooltip placement ===")
    smoke_tooltip(renderer)
    print("=== 5 分辨率 ===")
    smoke_resolutions()
    print("=== 6 少量对局 ===")
    smoke_games()

    pygame.quit()
    failed = [label for label, passed, _detail in RESULTS if not passed]
    print("\nPhase 10.5 smoke：%d 项，%d 项通过" % (len(RESULTS), len(RESULTS) - len(failed)))
    if failed:
        print("未通过：")
        for label in failed:
            print("  - " + label)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
