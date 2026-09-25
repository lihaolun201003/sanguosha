"""Phase 6 UI smoke: layout scenarios plus a full dummy-driven interaction flow.

Run with ``python tools/ui_smoke.py`` — SDL uses the dummy driver, no window
opens.  Screenshots for the layout scenarios are written next to this file.
"""

import os
import sys

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame

from src.game import Game
from src.actions import CallbackAction
from src.game.engine import PassPendingAction, RespondCardAction
from tools.multiplayer_smoke import human_respond
from src.renderer import Renderer
from src.start_menu import StartMenu
from src.ui.general_select import GeneralSelectScreen
from src.ui.interaction import handle_game_click
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
    screen = pygame.display.set_mode((1920, 1080))
    game = Game()
    game.ai_pacing = True
    renderer = Renderer(screen)
    menu = StartMenu(screen)
    menu.sync_layout(renderer.metrics)
    picker = GeneralSelectScreen(screen)
    picker.sync_layout(renderer.metrics, game.generals.list_generals())
    steps = []

    def frame(count=1, mouse=None):
        for _ in range(count):
            game.update(1 / 60)
            renderer.update(1 / 60)
            renderer.draw(game, mouse)
            pygame.display.flip()

    def click(position, mouse=None):
        """投递真实鼠标左键事件，并交给 main.py 使用的那一份点击路由。"""

        frame(1, mouse if mouse is not None else position)
        pygame.event.post(pygame.event.Event(
            pygame.MOUSEBUTTONDOWN, {"pos": position, "button": 1}
        ))
        handled = 0
        for event in pygame.event.get():
            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                handled += 1
                handle_game_click(event.pos, game, renderer)
        frame(1, position)
        return handled

    def source_landed(card, g=None):
        """实体 source 只移动一次：落到弃牌堆，或被技能（奸雄一类）取走。"""

        g = g or game
        if card in g.deck.discard_pile:
            return True
        return any(
            any(item is card for item in owner.hand)
            for owner in g.players if owner is not g.player
        )

    def settle(g, frame_fn, max_frames=900):
        """把动画与响应队列都跑完（AI 可能出闪 / 触发防具）。"""

        for _ in range(max_frames):
            frame_fn(1)
            if not g.busy and g.pending_request is None:
                return True
        return False

    def wait_idle(max_frames=240):
        """等动画队列播完，就像真实玩家等界面稳定后再点击。"""

        for _ in range(max_frames):
            if not game.busy:
                return True
            frame(1)
        return False

    # 1) 选择 4 AI（按钮位置现在由 LayoutMetrics 决定）
    menu.sync_layout(renderer.metrics)
    for _ in range(3):
        menu.handle_click(menu.plus_button.rect.center, game)
    steps.append(("菜单选择 AI 数量 = 4", game.ai_count == 4))

    # 2) 开始 → 进入选将 → 选择赵云 → 确认 → 5 人局
    menu.handle_click(menu.start_button.rect.center, game)
    steps.append(("进入选将界面", game.scene == "general_select"))

    picker.sync_layout(renderer.metrics, game.generals.list_generals())
    zhaoyun_rect = next(
        (rect for general, rect in zip(picker.generals, picker.card_rects) if general.id == "zhaoyun"),
        None,
    )
    if zhaoyun_rect is not None:
        picker.handle_click(zhaoyun_rect.center, game)
    steps.append(("选择赵云", game.selected_general == "zhaoyun"))

    confirm_action = picker.handle_click(picker.confirm_button.rect.center, game)
    if confirm_action == "confirm":
        game.confirm_general()
    frame(2)
    steps.append(("进入 5 人局",
                  game.scene == "game" and len(game.players) == 5))
    steps.append(("真人武将为赵云", game.players[0].general_id == "zhaoyun"))
    assigned = [player.general_id for player in game.players]
    steps.append(("AI 也分到武将且不重复", all(assigned) and len(set(assigned)) == len(assigned)))
    steps.append(("开局动画播完", wait_idle()))

    # 3) 鼠标移动（悬停手牌）
    hand = list(game.player.hand)
    if hand:
        rects = renderer.get_card_rects(game.player.hand)
        frame(2, rects[0].center)
        hovered = renderer.table_layout.hand_hover
        steps.append(("鼠标移动到第一张手牌触发悬停", hovered == 0))
        lifted = renderer.get_card_rects(game.player.hand)[0].top
        steps.append(("悬停卡牌上浮", lifted < renderer.metrics.hand_top()))

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

    # 4.5) 主动技能全链路：真实鼠标点击「发动技能」→ 选目标 → 确认发动
    game.set_general(game.player, "zhouyu")
    game.player.hp = game.player.max_hp
    game.player.hand = [tao()]
    skill_target = game.players[1]
    skill_target.hand = []
    skill_target.hp = skill_target.max_hp
    wait_idle()
    frame(1)

    click(renderer.skill_bar.button.rect.center)
    steps.append(("点击「发动技能」进入技能输入", game.pending_skill_input is not None))

    frame(1)
    click(renderer.table_layout.seat_rects[skill_target].center)
    steps.append(("点击角色选择技能目标",
                  bool(game.pending_skill_input)
                  and game.pending_skill_input["target"] is skill_target))

    frame(1)
    confirm_click = renderer.hit_action(renderer.primary_button.rect.center, game)
    click(renderer.primary_button.rect.center)
    steps.append(("主按钮在技能输入时变为确认发动", confirm_click == "confirm_skill"))
    steps.append(("点击「确认发动」提交技能", game.pending_skill_input is None))

    for _ in range(240):
        frame(1)

        if game.pending_selection is not None:
            # 反间要求周瑜交出一张手牌：用真实鼠标点击手牌回答。
            index = next(
                (i for i, card in enumerate(game.player.hand)
                 if game.is_selection_candidate(card, None)),
                None,
            )
            if index is not None:
                click(renderer.get_card_rects(game.player.hand)[index].center)
                continue

        request = game.pending_request
        if request is None:
            if not game.busy:
                break
            continue
        if getattr(request.target, "is_human", False):
            human_respond(game, request)
        else:
            game.engine.present_or_auto_resolve(request)

    steps.append(("反间结算完成并写入 used 标记",
                  game.player.skill_state.get("fanjian", "used", 0) == 1))
    frame(1)
    steps.append(("发动后技能按钮不再可点",
                  not renderer.skill_bar.button.enabled))

    # 技能段落结束：等动画播完并交回原武将，后续步骤按原流程继续。
    wait_idle()
    game.set_general(game.player, "zhaoyun")

    # 4.6) Card Action / Conversion：真实鼠标点击的四种场合
    #      (a) 赵云出牌阶段：【闪】→【龙胆】→【杀】
    game.set_general(game.player, "zhaoyun")
    game.player.hp = game.player.max_hp
    flash = shan()
    game.player.hand = [flash]
    victim = next((item for item in game.players[1:] if item.alive), game.players[1])
    victim.alive = True
    victim.hp = victim.max_hp
    victim.hand = []
    wait_idle()
    frame(1)

    steps.append(("出牌阶段的【闪】没有被灰",
                  renderer.playable is not None and 0 in renderer.playable))
    click(renderer.get_card_rects(game.player.hand)[0].center)
    steps.append(("未点【龙胆】时点击【闪】不会自动转换",
                  game.pending_target_selection is None
                  and game.pending_view_as is None
                  and flash in game.player.hand))
    click(renderer.skill_bar.button.rect.center)
    steps.append(("点击「发动技能」进入【龙胆】选牌模式",
                  game.pending_view_as is not None
                  and game.pending_view_as.skill_id == "longdan"))
    click(renderer.get_card_rects(game.player.hand)[0].center)
    steps.append(("选择【闪】后进入【龙胆】的目标选择",
                  game.pending_target_selection is not None
                  and getattr(game.pending_target_selection["card"], "_virtual", False)))
    frame(1)
    frame(1)
    victim = next((item for item in game.players[1:] if item.alive), victim)
    click(renderer.table_layout.seat_rects[victim].center)
    settle(game, frame)
    steps.append(("实体【闪】只移动一次（弃牌堆或已被技能取走）",
                  source_landed(flash)
                  and flash not in game.player.hand
                  and flash not in game.processing_zone))
    steps.append(("转换被写进战报",
                  any("龙胆" in line for line in game.game_log)))

    #      (b) 关羽满血：【桃】同时有正常使用与【武圣】→ 弹出选择面板
    game.set_general(game.player, "guanyu")
    game.player.hp = game.player.max_hp - 1     # 受伤：正常使用【桃】也合法
    game.player.sha_used = False      # 上一段用过【杀】，这里重置本回合的杀次数
    peach = tao()
    game.player.hand = [peach]
    victim = next((item for item in game.players[1:] if item.alive), victim)
    victim.alive = True
    victim.hp = victim.max_hp
    wait_idle()
    frame(1)

    steps.append(("满血的【桃】因为【武圣】没有被灰",
                  renderer.playable is not None and 0 in renderer.playable))
    click(renderer.skill_bar.button.rect.center)
    steps.append(("点击「发动技能」进入【武圣】选牌模式",
                  game.pending_view_as is not None
                  and game.pending_view_as.skill_id == "wusheng"))
    click(renderer.get_card_rects(game.player.hand)[0].center)
    steps.append(("选择红桃【桃】后进入目标选择",
                  game.pending_target_selection is not None
                  and game.pending_target_selection["card"].name == "SHA"))
    frame(1)
    victim = next((item for item in game.players[1:] if item.alive), victim)
    click(renderer.table_layout.seat_rects[victim].center)
    settle(game, frame)
    steps.append(("【武圣】把【桃】当【杀】结算成功",
                  source_landed(peach)
                  and peach not in game.player.hand
                  and any("武圣" in line for line in game.game_log)))

    #      (c) 响应：【杀】通过【龙胆】当【闪】打出
    game.set_general(game.player, "zhaoyun")
    killer = normal_sha()
    game.player.hand = [killer]
    wait_idle()
    game.response.request(
        prompt="【杀】：请打出一张【闪】",
        allowed_cards={"SHAN"},
        on_card=lambda index, card, rect: game.actions.add(
            CallbackAction(lambda: None)),
        on_pass=lambda: None,
    )
    wait_idle()
    frame(1)
    _log_before = game.game_log[-1] if game.game_log else ""
    click(renderer.get_card_rects(game.player.hand)[0].center)
    steps.append(("响应阶段未点技能时【杀】不能当【闪】打出",
                  (game.game_log[-1] if game.game_log else "") == _log_before))
    wait_idle()
    frame(1)
    click(renderer.skill_bar.button.rect.center)
    steps.append(("响应阶段也能进入【龙胆】", game.pending_view_as is not None))
    wait_idle()
    frame(2)                       # 选牌模式会改变手牌绘制，位置必须重新计算
    click(renderer.get_card_rects(game.player.hand)[0].center)
    last_log = game.game_log[-1] if game.game_log else ""
    steps.append(("响应阶段【杀】通过【龙胆】当【闪】打出",
                  "龙胆" in last_log and "闪" in last_log))
    game.response.clear()

    # 技能段落结束：等动画播完并交回原武将，后续步骤按原流程继续。
    wait_idle()
    game.set_general(game.player, "zhaoyun")

    # 5) 结束回合按钮
    game.player.hand = []
    wait_idle()
    frame(1)
    action = renderer.hit_action(renderer.primary_button.rect.center, game)
    steps.append(("主按钮动作 = 结束回合", action == "end_turn"))
    game.end_player_turn()

    # 6) AI 自动行动
    turns_before = game.current_player_id
    log_before = len(game.game_log)
    human_turns = 0
    for _ in range(2000):
        frame(1)
        if game.game_over and not game.busy:
            break
        if len(game.game_log) > log_before and not game.busy:
            break                       # AI 已经行动过，本步验证完成
        if game.busy:
            continue
        if game.pending_request is not None:
            request = game.pending_request
            if getattr(request.target, "is_human", False):
                # 真人请求必须给出合法内容（选牌 / 选项都不能用 Pass 敷衍）。
                human_respond(game, request)
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
    steps.append(("AI 自动接管回合",
                  game.current_player_id != turns_before
                  or game.game_over
                  or len(game.game_log) > log_before))

    # 7) 响应一个 Pending
    # 清掉可能残留的 UI 选择状态，保证下面是一次干净的响应验证
    game.cancel_card_action()
    game.cancel_skill_input()
    game.cancel_target_selection()
    game.response.clear()

    for _ in range(120):
        request = game.pending_request
        if request is None:
            break
        if getattr(request.target, "is_human", False):
            human_respond(game, request)
        else:
            game.engine.present_or_auto_resolve(request)
        frame(1)
    wait_idle()
    if not game.game_over and game.pending_request is None:
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
    # 视觉验收尺寸：1080p
    screen = pygame.display.set_mode((1920, 1080))
    renderer = Renderer(screen)
    menu.sync_layout(renderer.metrics) if False else None

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
