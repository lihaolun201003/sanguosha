"""Phase 11.7：丈八蛇矛作为"装备赋予的视为技"的端到端回归。

背景：丈八曾经是单机专用的一条遗留通道（``game.zhangba_selecting``）：

* 单机点武器进入的是那条遗留状态，选牌接口又没接上 → 点了手牌没反应；
* 联机根本没有把它注册成转化 → 房主下发的候选里没有"两张手牌当【杀】"，
  客户端选多少张都发不动它；
* 那条老入口还要求"必须在出牌阶段"，所以【决斗】【南蛮】里也打不出杀。

现在它和武将视为技（龙胆 / 武圣）走**完全同一条规则路径**，区别只在绑定
时机：装备进入装备区时绑定、离开时解绑（``equipment_skills.granted``）。

本文件只验证真实效果，全部走真实点击：

* 单机：``pygame.event.post`` + ``src.ui.interaction.handle_game_click``；
* 联机：``RemoteTableScene.handle_event``（tools.lan_playability_sync.UiClient）
  + 房主用引擎自己的规则复核。

运行：``SDL_VIDEODRIVER=dummy .venv/Scripts/python.exe -m unittest \\
        tests.test_engine_v2_phase11_7_zhangba_view_as``
"""

import os
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame

from src.game import Game
from src.game.atoms_v2 import EquipCardAtom, UnequipAtom
from src.network.decisions import DecisionKind
from src.renderer import Renderer
from src.ui.interaction import handle_game_click
from tests.legacy_helpers import equipment, normal_sha, tao

from tools.lan_playability_sync import UiClient
from tools.lan_view_harness import (
    RECT,
    MatchSession,
    find_card,
    force_hand,
    option_targets,
    start_remote_turn,
)

ZHANGBA = "equipment.zhangba"


def pump_until(session, predicate, timeout=8.0):
    """推进对局直到条件成立（房主那侧也一起走）。"""

    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        session.pump(0.1)
    return bool(predicate())


def decision_of(client, kind=None):
    match = client.match
    if match is None or match.decision is None or match.answered:
        return None
    request = match.decision
    if kind is not None and request.get("kind") != kind:
        return None
    return request


# ==================================================
# 1. 单机：点武器 → 选两张手牌 → 真的结算
# ==================================================


class LocalZhangbaTests(unittest.TestCase):

    RESOLUTION = (1920, 1080)

    def setUp(self):
        pygame.init()
        self.screen = pygame.display.set_mode(self.RESOLUTION)
        self.renderer = Renderer(self.screen)

    def tearDown(self):
        pygame.display.quit()

    # ---- 装置 ----

    def make_game(self, hands=("SHA", "TAO"), *, spear=True, ai_count=2):
        game = Game(ai_count=ai_count)
        game.scene = "game"
        game.ai_pacing = True
        game.actions.clear()
        game.engine.reset()
        for player in game.players:
            player.hand = []
            player.hp = player.max_hp
            player.alive = True
        game.phase = "play"
        game.current_turn_player = game.player
        game.player.hand = [self.card(name) for name in hands]
        if spear:
            game.context.apply(EquipCardAtom(game.player, equipment("ZHANGBA")))
        pygame.event.clear()
        return game

    @staticmethod
    def card(name):
        if name == "SHA":
            return normal_sha()
        if name == "TAO":
            return tao()
        raise ValueError(name)

    def frame(self, game, mouse=None, count=1):
        for _ in range(count):
            game.update(1 / 60)
            self.renderer.update(1 / 60)
            self.renderer.draw(game, mouse)

    def click(self, position, game):
        self.frame(game, position)
        pygame.event.post(pygame.event.Event(
            pygame.MOUSEBUTTONDOWN, {"pos": position, "button": 1}))
        for event in pygame.event.get():
            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                handle_game_click(event.pos, game, self.renderer)
        self.frame(game, position)

    def hand_center(self, game, index):
        self.frame(game)
        return self.renderer.get_card_rects(game.player.hand)[index].center

    def weapon_center(self, game):
        self.frame(game)
        rects = self.renderer.player_equipment_slot_rects(game)
        return rects["weapon"].center

    def seat_center(self, game, player):
        self.frame(game)
        return self.renderer.table_layout.seat_rects[player].center

    def settle(self, game, frames=400):
        for _ in range(frames):
            if not game.busy:
                return True
            self.frame(game)
        return False

    # ---- 用例 ----

    def test_equipping_binds_the_skill_and_unequipping_removes_it(self):
        game = self.make_game(spear=False)
        self.assertFalse(game.skills.has(game.player, ZHANGBA))

        game.context.apply(EquipCardAtom(game.player, equipment("ZHANGBA")))
        self.assertTrue(game.skills.has(game.player, ZHANGBA),
                        "装备丈八蛇矛之后应当获得它赋予的视为技")
        self.assertIn(ZHANGBA, game.skills.view_as_skill_ids(game.player))

        game.context.apply(UnequipAtom(game.player, "weapon"))
        self.assertFalse(game.skills.has(game.player, ZHANGBA),
                         "卸下之后视为技必须失效")
        self.assertFalse(game.skills.view_as_skill_ids(game.player))

    def test_weapon_click_enters_the_unified_view_as(self):
        game = self.make_game()
        self.click(self.weapon_center(game), game)

        session = game.pending_view_as
        self.assertIsNotNone(session, "点武器应当进入视为技选牌")
        self.assertEqual(session.skill_id, ZHANGBA)
        self.assertEqual(session.required_source_count, 2)
        self.assertEqual(game.view_as_candidate_ids(),
                         {id(card) for card in game.player.hand},
                         "丈八可以把任意两张手牌当【杀】，全部手牌都是候选")
        self.assertIn("丈八蛇矛", game.message)

    def test_without_spear_the_weapon_slot_click_does_nothing(self):
        game = self.make_game(spear=False)
        self.click(self.weapon_center(game), game)
        self.assertIsNone(game.pending_view_as)
        self.assertIsNone(game.pending_target_selection)

    def test_one_card_hand_cannot_enter(self):
        game = self.make_game(hands=("TAO",))
        self.click(self.weapon_center(game), game)
        self.assertIsNone(game.pending_view_as, "凑不出两张牌就不该进入选牌")
        self.assertIn("丈八蛇矛", game.message)

    def test_two_cards_are_played_as_a_sha(self):
        game = self.make_game()
        first, second = game.player.hand
        target = game.players[1]

        self.click(self.weapon_center(game), game)
        self.click(self.hand_center(game, 0), game)
        self.assertIsNotNone(game.pending_view_as, "还差一张，不能提前结算")
        self.assertEqual(len(game.pending_view_as.selected_source_cards), 1)

        self.click(self.hand_center(game, 1), game)
        selection = game.pending_target_selection
        self.assertIsNotNone(selection, "收齐两张应当进入目标选择")
        virtual = selection["card"]
        self.assertTrue(virtual.is_virtual)
        self.assertEqual(virtual.name, "SHA")
        self.assertEqual(len(virtual.source_cards), 2)
        self.assertIs(virtual.source_cards[0], first)
        self.assertIs(virtual.source_cards[1], second)

        self.click(self.seat_center(game, target), game)
        self.settle(game)

        self.assertIn(first, game.deck.discard_pile, "第一张手牌没有进入弃牌堆")
        self.assertIn(second, game.deck.discard_pile, "第二张手牌没有进入弃牌堆")
        self.assertTrue(game.player.sha_used)
        self.assertTrue(any("丈八蛇矛" in line for line in game.game_log),
                        "战报里必须能看出这次转化")

    def test_second_weapon_click_cancels(self):
        game = self.make_game()
        first = game.player.hand[0]

        self.click(self.weapon_center(game), game)
        self.click(self.hand_center(game, 0), game)
        self.assertEqual(len(game.pending_view_as.selected_source_cards), 1)

        self.click(self.weapon_center(game), game)
        self.assertIsNone(game.pending_view_as, "再点一次武器应当取消发动")
        self.assertIn(first, game.player.hand, "取消不弃牌")
        self.assertFalse(game.player.sha_used, "取消不消耗出杀次数")

    def test_response_window_can_use_it(self):
        """【南蛮入侵】/【决斗】里可以被要求打出【杀】：同一条路径。"""

        game = self.make_game()
        first, second = game.player.hand
        submitted = []
        game.response.request(
            prompt="【南蛮入侵】：请打出一张【杀】",
            allowed_cards={"SHA"},
            on_card=lambda index, card, rect: submitted.append((index, card)),
            on_pass=lambda: submitted.append(None),
        )

        self.click(self.weapon_center(game), game)
        session = game.pending_view_as
        self.assertIsNotNone(session, "响应窗口里也要能进入丈八")
        self.assertEqual(session.context.context, "response")

        self.click(self.hand_center(game, 0), game)
        self.assertEqual(submitted, [], "只选一张不算打出")
        self.click(self.hand_center(game, 1), game)

        self.assertEqual(len(submitted), 1, "收齐两张应当打出这张杀")
        index, card = submitted[0]
        self.assertEqual(index, 0)
        # 响应系统收到的实体牌是**第一张来源牌**，虚拟【杀】通过 effective_card
        # 交给结算（与【龙胆】把【闪】当【杀】打出是同一条路径）。
        self.assertIs(card, first)
        self.assertEqual(second.name, "TAO")

    def test_engine_pending_response_can_use_it(self):
        """真实【南蛮入侵】响应窗口：引擎 Pending（不是 legacy ResponseSystem）。

        用户报告的正是这一条："【决斗】【南蛮入侵】里打不出【杀】"。
        """

        from src.game.engine import UseCardAction
        from tests.legacy_helpers import canonical_card

        game = self.make_game()
        first, second = game.player.hand
        attacker = game.players[1]
        attacker.hand = [canonical_card("NANMAN", card_color="black")]
        # 【南蛮入侵】的目标由规则决定（其他所有存活角色），不是玩家挑的。
        targets = [player for player in game.players
                   if player is not attacker and player.alive]

        game.engine.submit(UseCardAction(attacker, attacker.hand[0], targets))
        for _ in range(1200):
            self.frame(game)
            current = game.engine.pending.current
            if (current is not None and current.target is game.player
                    and not game.busy):
                break

        context = game.current_card_action_context()
        self.assertIsNotNone(context, "响应窗口里本机真人应当有一个可用场合")
        self.assertEqual(context.context, "response")
        self.assertIn("SHA", context.allowed_names)

        self.click(self.weapon_center(game), game)
        self.assertIsNotNone(game.pending_view_as, "南蛮响应里也要能发动丈八")
        self.click(self.hand_center(game, 0), game)
        self.assertIsNotNone(game.pending_view_as, "只选一张不算打出")
        self.click(self.hand_center(game, 1), game)

        self.assertNotIn(first, game.player.hand)
        self.assertNotIn(second, game.player.hand)
        self.assertTrue(
            any("丈八蛇矛" in line and "打出" in line for line in game.game_log),
            "战报里应当显示这是打出（不是使用）")

    def test_skill_bar_offers_the_equipment_skill(self):
        """技能区也应当列出装备赋予的视为技（点它 = 同一个入口）。"""

        game = self.make_game()
        self.frame(game)
        rows = dict((item[0], item) for item in game.skill_picker_rows())
        self.assertIn(ZHANGBA, rows)
        self.assertTrue(rows[ZHANGBA][1], "两张手牌在手：现在应当可以发动")

        rect = self.renderer.skill_bar.rect_for(ZHANGBA)
        self.assertIsNotNone(rect, "技能区没有画出丈八蛇矛")
        self.click(rect.center, game)
        self.assertIsNotNone(game.pending_view_as)
        self.assertEqual(game.pending_view_as.skill_id, ZHANGBA)

    def test_normal_use_is_not_hijacked(self):
        """装备丈八之后，普通用牌仍是普通用牌（点【桃】直接回血）。"""

        game = self.make_game(hands=("TAO", "SHA"))
        game.player.hp = game.player.max_hp - 1

        self.click(self.hand_center(game, 0), game)

        self.assertEqual(game.player.hp, game.player.max_hp, "【桃】应当正常结算")
        self.assertIsNone(game.pending_view_as)
        self.assertIsNone(game.pending_card_action,
                          "普通用牌不该弹转化面板")


# ==================================================
# 1b. AI：多来源候选不能被当成单张转化
# ==================================================


class AiZhangbaTests(unittest.TestCase):
    """AI 不做多来源收集，就绝不能把"两张手牌当【杀】"的候选当成单张打出。

    候选态（``needs_more_sources``）只是"还差一张"的提示，把它交给响应 /
    出牌结算等于凭空少付一张牌。
    """

    def make_game(self, hands, *, spear=True):
        from src.game.controllers.ai import AIController

        game = Game(ai_count=1)
        actor = game.enemy
        for player in game.players:
            player.hand = []
        actor.hand = [self.card(name) for name in hands]
        if spear:
            game.context.apply(EquipCardAtom(actor, equipment("ZHANGBA")))
        return game, actor, AIController(game, actor)

    @staticmethod
    def card(name):
        if name == "SHA":
            return normal_sha()
        if name == "TAO":
            return tao()
        if name == "SHAN":
            from tests.legacy_helpers import shan

            return shan()
        raise ValueError(name)

    def test_spear_is_bound_to_the_ai(self):
        game, actor, _controller = self.make_game(["TAO", "TAO"])
        self.assertTrue(game.skills.has(actor, ZHANGBA))

    def test_ai_never_turns_one_card_into_a_sha(self):
        for hands in (["TAO"], ["SHA", "TAO"], ["TAO", "TAO", "SHAN"]):
            _game, _actor, controller = self.make_game(hands)
            self.assertIsNone(
                controller.converted_play_card("SHA"),
                "AI 把不足两张的丈八候选当成了【杀】：" + repr(hands))

    def test_ai_does_not_answer_a_sha_request_with_one_card(self):
        from src.game.engine.pending import PendingRequest, PendingRequestType

        game, actor, controller = self.make_game(["TAO", "TAO"])
        request = PendingRequest(
            request_id=1,
            request_type=PendingRequestType.RESPOND_CARD,
            source=game.player,
            target=actor,
            prompt="请打出一张【杀】",
            allowed_cards=frozenset({"SHA"}),
        )
        self.assertIsNone(
            controller.converted_response(request),
            "AI 用一张手牌冒充了两张手牌转化的【杀】")


# ==================================================
# 2. 联机：房主下发候选 + 客户端点武器选中方式
# ==================================================


class LanZhangbaTests(unittest.TestCase):

    def test_host_offers_zhangba_as_a_play_option(self):
        with MatchSession(client_count=1) as session:
            game, pump = session.game, session.pump
            remote = session.remote()
            client = session.client_of(remote)

            session.fix_hands(["TAO"], keep=remote)
            force_hand(game, remote, ["SHA", "TAO"])
            game.context.apply(EquipCardAtom(remote, equipment("ZHANGBA")))
            start_remote_turn(session.host, pump)

            # 面板不是"发起请求就立刻到"（房主先等桌面静一下再发）：
            # 全量套件里机器更忙，必须等它真的到客户端。
            self.assertTrue(pump_until(
                session,
                lambda: decision_of(client, DecisionKind.PLAY_PHASE) is not None,
                timeout=10.0), "游客没有收到出牌阶段面板")
            request = decision_of(client, DecisionKind.PLAY_PHASE)
            card, option = find_card(request, skill_id=ZHANGBA)
            self.assertIsNotNone(option, "房主没有下发丈八的转化方式")
            self.assertEqual(int(option["min_sources"]), 2,
                             "丈八必须明确要求两张来源牌")
            self.assertEqual(int(option["max_sources"]), 2)
            self.assertEqual(option["result_name"], "SHA")

    def test_client_weapon_click_then_two_cards_then_target(self):
        with MatchSession(client_count=1) as session:
            game, pump = session.game, session.pump
            remote = session.remote()
            client = session.client_of(remote)
            ui = UiClient(client)

            session.fix_hands(["TAO"], keep=remote)
            force_hand(game, remote, ["SHA", "TAO"])
            game.context.apply(EquipCardAtom(remote, equipment("ZHANGBA")))
            start_remote_turn(session.host, pump)

            self.assertTrue(pump_until(
                session,
                lambda: decision_of(client, DecisionKind.PLAY_PHASE) is not None,
                timeout=10.0), "游客没有收到出牌阶段面板")
            request = decision_of(client, DecisionKind.PLAY_PHASE)
            card, option = find_card(request, skill_id=ZHANGBA)
            self.assertIsNotNone(option)

            # 点自己装备的丈八蛇矛：客户端应当选中"两张手牌当【杀】"这一种用法
            rects = ui.renderer.player_equipment_slot_rects(ui.view)
            ui.click(rects["weapon"].center)
            self.assertEqual(ui.scene.decision.action_id,
                             str(option.get("action_id") or ""),
                             "点武器没有选中房主下发的丈八方式")

            hand_ids = [str(item["card_id"]) for item in request["cards"]
                        if item.get("name") in ("SHA", "TAO")]
            self.assertEqual(len(hand_ids), 2)

            ui.click_hand_card(hand_ids[0])
            pump(0.3)
            self.assertFalse(client.match.answered, "只选一张就提交了")
            ui.click_hand_card(hand_ids[1])
            pump(0.4)

            targets = option_targets(card, option)
            self.assertTrue(targets, "下发的丈八方式没有合法目标（杀需要目标）")
            ui.click_seat(targets[0])

            self.assertTrue(pump_until(
                session,
                lambda: client.match.answered or client.match.decision is None,
                timeout=8.0), "选满两张 + 目标之后没有提交")
            answer = (client.match.last_answer or {}).get("result") or {}
            self.assertEqual(len(answer.get("card_ids") or ()), 2)
            self.assertEqual(answer.get("skill_id"), ZHANGBA)
            self.assertEqual(answer.get("result_name"), "SHA")

            self.assertTrue(pump_until(
                session,
                lambda: not session.host.match.registry.rejected, timeout=4.0),
                "房主拒绝了合法出牌：" + str(session.host.match.registry.rejected))
            pump(0.6)
            self.assertTrue(remote.sha_used, "房主没有把这次出杀记到出杀次数上")
            self.assertTrue(any("丈八蛇矛" in line for line in game.game_log),
                            "房主的战报里没有这次转化")

    def test_client_can_respond_with_two_cards(self):
        """【南蛮入侵】响应：客户端同样能用丈八打出【杀】。"""

        with MatchSession(client_count=1) as session:
            game, pump = session.game, session.pump
            remote = session.remote()
            client = session.client_of(remote)
            ui = UiClient(client)

            session.fix_hands(["TAO"], keep=remote)
            force_hand(game, remote, ["SHA", "TAO"])
            game.context.apply(EquipCardAtom(remote, equipment("ZHANGBA")))

            # 房主使用【南蛮入侵】：邻居（游客）必须打出一张【杀】
            from tests.legacy_helpers import canonical_card

            game.player.hand = [canonical_card("NANMAN", card_color="black")]
            for player in game.players:
                player.clear_turn_state()
            game.current_turn_player = game.player
            game.phase = "play"
            game.player_use_card(0, RECT)

            self.assertTrue(pump_until(
                session,
                lambda: decision_of(client, DecisionKind.RESPOND_CARD) is not None,
                timeout=10.0), "游客没有收到【杀】的响应请求")
            request = decision_of(client, DecisionKind.RESPOND_CARD)
            card, option = find_card(request, skill_id=ZHANGBA)
            self.assertIsNotNone(option, "响应窗口里房主没有下发丈八")

            rects = ui.renderer.player_equipment_slot_rects(ui.view)
            ui.click(rects["weapon"].center)
            hand_ids = [str(item["card_id"]) for item in request["cards"]
                        if item.get("name") in ("SHA", "TAO")]
            ui.click_hand_card(hand_ids[0])
            pump(0.3)
            self.assertFalse(client.match.answered, "响应里只选一张就提交了")
            ui.click_hand_card(hand_ids[1])
            self.assertTrue(pump_until(
                session,
                lambda: client.match.answered or client.match.decision is None,
                timeout=8.0), "响应里选满两张没有提交")
            answer = (client.match.last_answer or {}).get("result") or {}
            self.assertEqual(len(answer.get("card_ids") or ()), 2)
            self.assertEqual(answer.get("skill_id"), ZHANGBA)
            self.assertTrue(pump_until(
                session,
                lambda: not session.host.match.registry.rejected, timeout=4.0),
                "房主拒绝了这次响应：" + str(session.host.match.registry.rejected))
            pump(0.6)
            for card_id in hand_ids:
                self.assertNotIn(
                    card_id, [str(item.id) for item in remote.hand],
                    "响应牌没有离开手牌：" + card_id)


if __name__ == "__main__":
    unittest.main()
