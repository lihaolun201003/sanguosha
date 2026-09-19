"""Phase 6 UI smoke: layout scenarios plus a full dummy-driven interaction flow.

Run with ``python tools/ui_smoke.py`` — SDL uses the dummy driver, no window
opens.  Screenshots for the layout scenarios are written next to this file.
"""

import os
import sys

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame

from src.constants import AI_PLUS_RECT, PLAYER_EQUIPMENT_RECTS, SINGLE_PLAYER_RECT
from src.game import Game
from src.game.engine import PassPendingAction, RespondCardAction
from src.renderer import Renderer
from src.start_menu import StartMenu
from src.ui import layout
from tests.legacy_helpers import canonical_card, equipment, normal_sha, shan, tao


def trick(name):
    return canonical_card(name)


def build_game(ai_count, hand_size=4):
    game = Game(ai_count=ai_count)
    game.start_single_player()
    game.actions.clear()
    for player in game.players:
        player.hand = []
    hand = [normal_sha(), normal_sha(), tao(), shan(), trick("GUOHE"), trick("WUXIE")]
    while len(hand) < hand_size:
        hand.append(trick("NANMAN"))
    game.player.hand = hand[:hand_size]
    game.player.hp = 3
    game.current_turn_player = game.player
    game.phase = "play"
    return game


def dress_up_opponents(game):
    """多个 AI 同时拥有武器 / 防具 / 坐骑 / 判定牌 / 横置。"""

    for index, player in enumerate(game.players[1:]):
        player.hp = max(1, player.max_hp - index % 3)
        player.hand = [shan() for _ in range(2 + index)]
        if index % 2 == 0:
            player.set_equipment(equipment("QINGLONG"))
        else:
            player.set_equipment(equipment("ZHUGE"))
        player.set_equipment(equipment("BAGUA"))
        player.set_equipment(equipment("CHITU"))
        player.set_equipment(equipment("JUEYING"))
        if index % 3 == 0:
            player.judgement_zone.append(trick("LEBU"))
        if index % 3 == 1:
            player.judgement_zone.append(trick("SHANDIAN"))
        player.chained = index % 2 == 0
    if len(game.players) > 3:
        game.players[3].alive = False
        game.players[3].hp = 0


def scenario_two_players(renderer, outdir):
    game = build_game(1, hand_size=5)
    dress_up_opponents(game)
    return game


def scenario_five_players(renderer, outdir):
    game = build_game(4, hand_size=8)
    dress_up_opponents(game)
    return game


def scenario_eight_players(renderer, outdir):
    game = build_game(7, hand_size=6)
    dress_up_opponents(game)
    return game


def scenario_many_cards(renderer, outdir):
    game = build_game(4, hand_size=18)
    dress_up_opponents(game)
    return game


def scenario_response(renderer, outdir):
    game = build_game(3, hand_size=4)
    dress_up_opponents(game)
    game.response.request(
        prompt="【杀】：请打出一张【闪】",
        allowed_cards={"SHAN"},
        on_card=lambda *args, **kwargs: None,
        on_pass=lambda: None,
    )
    return game


def scenario_wuxie(renderer, outdir):
    game = build_game(3, hand_size=4)
    dress_up_opponents(game)
    game.player.hand = [trick("WUXIE"), normal_sha()]
    from src.game.flows.wuxie import WuxieResponseChain

    chain = WuxieResponseChain(
        game.engine, game.players[1], trick("GUOHE"), [game.player], lambda _nullified: None
    )
    chain.start()
    return game


def scenario_dying(renderer, outdir):
    game = build_game(3, hand_size=4)
    dress_up_opponents(game)
    game.player.hand = [tao(), normal_sha()]
    from src.game.flows import DyingFlow

    victim = game.players[3]
    victim.hp = 0
    DyingFlow(game.engine, dying_player=victim).start()
    return game


def scenario_wugu(renderer, outdir):
    game = build_game(4, hand_size=5)
    dress_up_opponents(game)
    game.player.hand = [trick("WUGU")]
    from src.game.engine import UseCardAction

    targets = game.seats.alive_players_in_order(start_after=game.player, include_start=True)
    game.submit_action(UseCardAction(game.player, game.player.hand[0], targets))
    return game


def scenario_tiesuo(renderer, outdir):
    game = build_game(3, hand_size=3)
    dress_up_opponents(game)
    game.player.hand = [trick("TIESUO")]
    rects = renderer.get_card_rects(game.player.hand)
    game.player_use_card(0, tuple(rects[0]))
    game.choice.choose_yes()
    selection = game.pending_target_selection
    if selection is not None:
        for target in selection["candidates"][:1]:
            game.toggle_target_selection(target)
    return game


def scenario_shunshou_equipment(renderer, outdir):
    """顺手牵羊指向一个只剩装备的角色：公共区必须能选中那件装备。"""

    game = build_game(3, hand_size=2)
    dress_up_opponents(game)
    victim = game.players[1]
    victim.hand = []
    # 只留下武器：+1 马会把距离推到 2，顺手牵羊（距离 1）就无法指定目标。
    for slot in ("armor", "defensive_horse", "offensive_horse"):
        victim.remove_equipment(slot)
    game.player.hand = [trick("SHUNSHOU")]
    from src.game.engine import UseCardAction

    game.submit_action(UseCardAction(game.player, game.player.hand[0], [victim]))
    return game


SCENARIOS = [
    ("A_2players", scenario_two_players),
    ("B_5players", scenario_five_players),
    ("C_8players", scenario_eight_players),
    ("D_18_cards", scenario_many_cards),
    ("E_full_equipment", scenario_five_players),
    ("F_pending_shan", scenario_response),
    ("F_pending_wuxie", scenario_wuxie),
    ("F_pending_dying", scenario_dying),
    ("F_pending_wugu", scenario_wugu),
    ("F_pending_tiesuo", scenario_tiesuo),
    ("F_pending_shunshou_equipment", scenario_shunshou_equipment),
]


def run_scenarios(renderer, outdir):
    results = []
    for name, builder in SCENARIOS:
        game = builder(renderer, outdir)
        # 多跑几帧，让出牌 / 摸牌动画落到中央展示位。
        for _ in range(30):
            game.update(1 / 60)
            renderer.update(1 / 60)
            renderer.draw(game)
        pygame.display.flip()
        path = os.path.join(outdir, name + ".png")
        pygame.image.save(renderer.screen, path)
        rects = renderer.get_player_panel_rects(game)
        bounds = renderer.screen.get_rect()
        inside = all(bounds.contains(rect) for rect in rects.values())
        items = list(rects.values())
        overlap = any(
            items[i].colliderect(items[j])
            for i in range(len(items))
            for j in range(i + 1, len(items))
        )
        results.append((name, len(game.players), inside, overlap, path))
    return results


def run_dummy_flow():
    """完整交互流程：菜单 → 5 人局 → 出牌 → 结束回合 → AI 行动 → 结算。"""

    pygame.init()
    screen = pygame.display.set_mode((layout.WIDTH, layout.HEIGHT))
    game = Game()
    renderer = Renderer(screen)
    menu = StartMenu(screen, renderer.big_font, renderer.small_font, renderer.tiny_font)
    steps = []

    def frame(count=1, mouse=None):
        for _ in range(count):
            game.update(1 / 60)
            renderer.update(1 / 60)
            renderer.draw(game, mouse)
            pygame.display.flip()

    def wait_idle(max_frames=240):
        """等动画队列播完，就像真实玩家等界面稳定后再点击。"""

        for _ in range(max_frames):
            if not game.busy:
                return True
            frame(1)
        return False

    # 1) 选择 4 AI
    for _ in range(3):
        menu.handle_click(pygame.Rect(*AI_PLUS_RECT).center, game)
    steps.append(("菜单选择 AI 数量 = 4", game.ai_count == 4))

    # 2) 开始 5 人局
    menu.handle_click(pygame.Rect(*SINGLE_PLAYER_RECT).center, game)
    frame(2)
    steps.append(("进入 5 人局", game.scene == "game" and len(game.players) == 5))
    steps.append(("开局动画播完", wait_idle()))

    # 3) 鼠标移动（悬停手牌）
    hand = list(game.player.hand)
    if hand:
        rects = renderer.get_card_rects(game.player.hand)
        frame(2, rects[0].center)
        hovered = renderer.table_layout.hand_hover
        steps.append(("鼠标移动到第一张手牌触发悬停", hovered == 0))
        lifted = renderer.get_card_rects(game.player.hand)[0].top
        steps.append(("悬停卡牌上浮", lifted < layout.HAND_TOP))

    # 4) 出杀并选择目标，然后取消
    game.player.hand = [normal_sha()]
    wait_idle()
    frame(1)
    rects = renderer.get_card_rects(game.player.hand)
    game.player_use_card(0, tuple(rects[0]))
    steps.append(("点击手牌开始选择目标", game.pending_target_selection is not None))
    if game.pending_target_selection is not None:
        cancelled = game.cancel_target_selection()
        steps.append(("取消选择目标成功", bool(cancelled) and game.pending_target_selection is None))

    # 5) 结束回合按钮
    game.player.hand = []
    wait_idle()
    frame(1)
    action = renderer.hit_action(renderer.primary_button.rect.center, game)
    steps.append(("主按钮动作 = 结束回合", action == "end_turn"))
    game.end_player_turn()

    # 6) AI 自动行动
    turns_before = game.current_player_id
    human_turns = 0
    for _ in range(600):
        frame(1)
        if game.game_over and not game.busy:
            break
        if game.busy:
            continue
        if game.pending_request is not None:
            request = game.pending_request
            if getattr(request.target, "is_human", False):
                game.submit_action(PassPendingAction(request.target, request.request_id))
            else:
                game.engine.present_or_auto_resolve(request)
            continue
        if (
            game.current_turn_player is game.player
            and game.phase == "play"
            and not game.busy
        ):
            human_turns += 1
            if human_turns > 3:
                break
            game.end_player_turn()
    steps.append(("AI 自动接管回合", game.current_player_id != turns_before or game.game_over))

    # 7) 响应一个 Pending
    if not game.game_over:
        game.response.request(
            prompt="【杀】：请打出一张【闪】",
            allowed_cards={"SHAN"},
            on_card=lambda *args, **kwargs: None,
            on_pass=lambda: None,
        )
        frame(1)
        actions = renderer.actions_for(game)
        click = renderer.hit_action(actions["secondary"].rect.center, game)
        steps.append(("Pending 响应按钮 = 不出", click == "pass_response"))
        game.pass_response()
        frame(1)

    # 8) 重新开始
    renderer.reset_effects()
    game.reset()
    frame(2)
    steps.append(("重新开始重建牌桌", len(game.players) == 5 and len(game.player.hand) == 4))

    # 9) 返回主菜单
    game.return_to_menu()
    menu.draw(game)
    pygame.display.flip()
    steps.append(("返回主菜单", game.scene == "menu"))

    pygame.display.quit()
    return steps


def main():
    outdir = os.path.join(os.path.dirname(__file__), "ui_snapshots")
    os.makedirs(outdir, exist_ok=True)

    pygame.init()
    screen = pygame.display.set_mode((layout.WIDTH, layout.HEIGHT))
    renderer = Renderer(screen)

    ok = True
    print("=== 布局场景 ===")
    for name, players, inside, overlap, path in run_scenarios(renderer, outdir):
        flag = inside and not overlap
        ok = ok and flag
        print("%-4s %-20s 人数=%-2d 面板内界=%s 重叠=%s" % (
            "PASS" if flag else "FAIL", name, players, inside, overlap))

    print("=== dummy 完整流程 ===")
    for label, passed in run_dummy_flow():
        ok = ok and passed
        print("%-4s %s" % ("PASS" if passed else "FAIL", label))

    print("UI 烟雾总体：", "通过" if ok else "失败")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
