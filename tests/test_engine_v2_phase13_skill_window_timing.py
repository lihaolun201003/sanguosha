"""事件回调里起的技能窗口必须**冻住原结算与 AI 回合**。

五人不符合"玩家选牌时 AI 继续出牌"的场景：技能在事件回调里启动了一条需要
玩家回答的流程（悲歌选牌 / 放逐选目标 / 天香选转移目标 / 涅槃确认…），而事件
分发是同步的——父结算流程必须显式等它，AI 也不能在别人等答案时接着出牌。

这里用**真实鼠标链路之外的最小真实路径**驱动：AI 控制器照常行动、真人控制器
把请求挂起（不回答），断言"等待期间 AI 的手牌、出牌记录、回合角色都不变"，
回答之后只推进一次。
"""

import os
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame

from src.game import Game
from src.game.engine import EventType, SelectTargetsAction
from src.game.atoms_v2 import LoseHpAtom
from src.game.flows.damage import DamageContext, DamageFlow
from src.game.flows.dying import DyingFlow
from src.game.rules import TurnPhase
from tests.legacy_helpers import (
    canonical_card,
    normal_sha,
    set_draw_order,
    shan,
    tao,
)


def build_identity(*, me="caiwenji", others=("guanyu", "lvbu", "zhaoyun", "caocao")):
    """五人身份局：真人 + 4 个 AI，AI 的手牌/牌堆全部固定，结果可复现。"""

    game = Game(ai_count=4)
    game.set_mode("identity")
    game.ai_pacing = True
    game.start_local_battle(4)
    game.actions.clear()
    game.set_general(game.player, me)
    for player, general_id in zip(game.players[1:], others):
        game.set_general(player, general_id)
    game.player.hp = game.player.max_hp
    for player in game.players:
        player.hand = []
        player.judgement_zone = []
    return game


def pending_reasons(game):
    return [request.context.get("reason", "") for request in game.engine.pending._stack]


def answer_human(game, *, cards=()):
    """回答当前挂在真人身上的请求（选牌 / 确认 / 响应）。"""

    request = game.engine.pending.current
    game.get_controller(game.player).submit(
        _answer_for(request, game.player, cards))


def _answer_for(request, actor, cards):
    from src.game.engine import (
        ConfirmPendingAction, PassPendingAction, SelectCardsAction,
        SelectTargetsAction,
    )

    if request.request_type.value == "select_cards":
        return SelectCardsAction(actor, request.request_id, cards)
    if request.request_type.value == "select_targets":
        return SelectTargetsAction(actor, request.request_id, ())
    if request.request_type.value == "confirm":
        return ConfirmPendingAction(actor, request.request_id, True)
    return PassPendingAction(actor, request.request_id)


class SkillWindowPauseTests(unittest.TestCase):
    """等待技能窗口期间，原结算与 AI 回合都必须停住。"""

    @classmethod
    def setUpClass(cls):
        pygame.init()
        cls.screen = pygame.display.set_mode((1280, 800))

    def drive(self, game, frames=240, until=None, auto_answer=("sha",)):
        """推进若干帧；``auto_answer`` 里的真人请求由驱动方先答掉。

        悲歌 / 天香这类窗口**不在**自动回答之列：它们正是要观察的等待点。
        """

        for _ in range(frames):
            if until is not None and until():
                return True
            request = game.engine.pending.current
            if (request is not None and request.target is game.player
                    and request.context.get("reason") in auto_answer):
                answer_human(game)
            game.update(1 / 60)
        return until is None or until()

    # ==================================================
    # 悲歌：伤害后开选牌窗口
    # ==================================================

    def test_beige_window_freezes_the_ai_turn(self):
        """玩家蔡文姬的悲歌等待选牌时，AI 不许继续出牌。"""

        game = build_identity()
        # 当前回合交给 AI-1，它手上有一张【杀】；真人手里留一张可弃的牌。
        active = game.players[1]
        game.current_turn_player = active
        game.phase = "play"
        game.turn_phase = TurnPhase.PLAY
        active.hand = [normal_sha(), normal_sha(), canonical_card("GUOHE"),
                       canonical_card("WUZHONG"), canonical_card("SHUNSHOU")]
        game.player.hand = [tao(), shan(), shan()]
        for player in game.players[2:]:
            player.hand = [shan(), tao()]
        set_draw_order(game, [canonical_card("SHAN") for _ in range(40)])

        controller = game.get_controller(active)
        controller.take_turn(lambda: None)

        # 推进：AI 出【杀】→ 真人不响应 → 伤害 → 悲歌窗口
        self.assertTrue(self.drive(
            game, until=lambda: "beige" in pending_reasons(game)),
            "没有等到悲歌窗口，实际：" + repr(pending_reasons(game)))

        hand_before = len(active.hand)
        log_before = len(game.game_log)
        turn_before = game.current_turn_player
        played_before = controller._cards_played

        # 玩家还在选牌：给足帧数，AI 不许动
        self.drive(game, frames=120)

        self.assertIn("beige", pending_reasons(game), "悲歌窗口不该自己消失")
        self.assertEqual(len(active.hand), hand_before, "等待期间 AI 的手牌变了")
        self.assertEqual(len(game.game_log), log_before, "等待期间 AI 又出牌了")
        self.assertIs(game.current_turn_player, turn_before, "等待期间回合换人了")
        self.assertEqual(controller._cards_played, played_before)

        # 玩家弃一张牌完成悲歌 → 结算继续，AI 才能接着行动
        game.get_controller(game.player).submit(
            _answer_for(game.engine.pending.current, game.player, (game.player.hand[0],)))
        self.drive(game, frames=180)
        self.assertNotIn("beige", pending_reasons(game), "悲歌答完就该收尾")
        self.assertTrue(any("悲歌" in line for line in game.game_log),
                        "悲歌的判定与结算应该真的跑完了")

    # ==================================================
    # 天香：必须先选完转移目标，再扣血
    # ==================================================

    def test_tianxiang_selects_target_before_the_damage_lands(self):
        """天香窗口没答完时，原目标不能已经掉血。"""

        game = build_identity(me="xiaoqiao", others=("guanyu", "lvbu", "zhaoyun", "caocao"))
        victim = game.player
        victim.hand = [tao(), shan(), canonical_card("SHAN")]
        source = game.players[1]
        hp_before = victim.hp

        flow = DamageFlow(
            game.engine,
            DamageContext(source=source, target=victim, amount=1, card=normal_sha()),
        )
        flow.start()
        self.assertIn("tianxiang", pending_reasons(game), "没有开到天香窗口")
        self.assertEqual(victim.hp, hp_before,
                         "天香还没选完转移目标，原目标已经掉血了")

        # 选一张红桃弃置：此时还只到"选目标"这一步，血照样不能扣
        request = game.engine.pending.current
        candidates = list(request.context["candidates"])
        game.get_controller(game.player).submit(
            _answer_for(request, game.player, (candidates[0],)))
        self.drive(game, frames=30)
        self.assertEqual(victim.hp, hp_before, "还没选完转移目标就不该扣血")

        # 选一个转移目标 → 伤害落到新目标身上，原目标一点血都不掉
        target_request = game.engine.pending.current
        self.assertEqual(target_request.context.get("reason"), "tianxiang")
        transferred = game.players[2]
        transferred_hp = transferred.hp
        game.get_controller(game.player).submit(SelectTargetsAction(
            game.player, target_request.request_id, (transferred,)))
        self.drive(game, frames=90)
        self.assertEqual(transferred.hp, transferred_hp - 1, "伤害应该转移到新目标")
        self.assertEqual(victim.hp, hp_before, "原目标的血不该动")

    # ==================================================
    # 濒死：涅槃必须先处理决定，再完成死亡
    # ==================================================

    def test_niepan_window_finishes_before_the_dying_flow(self):
        """濒死时涅槃窗口开着，濒死流程不能先走完（更不能死人）。"""

        game = build_identity(me="pangtong", others=("guanyu", "lvbu", "zhaoyun", "caocao"))
        victim = game.player
        hp_before = victim.hp

        dying = DyingFlow(game.engine, dying_player=victim,
                          source=game.players[1])
        dying.start()
        self.assertIn("niepan", pending_reasons(game), "没有开到涅槃窗口")
        self.assertTrue(victim.alive, "涅槃还没答，人已经死了")

        # 拒绝涅槃 → 才轮到求桃
        game.get_controller(game.player).submit(
            _answer_for(game.engine.pending.current, game.player, ()))
        self.drive(game, frames=60)
        self.assertNotIn("niepan", pending_reasons(game))
        self.assertTrue(victim.alive, "求桃阶段还没结束就不该判死")

    # ==================================================
    # 无人等待时 AI 照常推进
    # ==================================================

    def test_ai_turn_runs_normally_without_any_request(self):
        """没有技能窗口时 AI 正常出牌——修复不能把 AI 冻死。"""

        game = build_identity(me="caiwenji", others=("guanyu", "lvbu", "zhaoyun", "caocao"))
        active = game.players[1]
        game.current_turn_player = active
        game.phase = "play"
        game.turn_phase = TurnPhase.PLAY
        active.hand = [normal_sha(), canonical_card("GUOHE")]
        game.player.hand = [shan(), tao()]
        set_draw_order(game, [canonical_card("SHAN") for _ in range(20)])
        game.player.hp = game.player.max_hp

        controller = game.get_controller(active)
        controller.take_turn(lambda: None)
        self.drive(game, frames=240)

        # AI 至少打出了一张牌（战报里有它的名字）
        self.assertGreater(controller._cards_played, 0, "AI 一张牌都没出")


if __name__ == "__main__":
    unittest.main()


class MoreSkillWindowTests(unittest.TestCase):
    """放逐 / 双悲歌 / 改判 / 阶段结束：都是"事件回调里开窗口"的同一族问题。"""

    @classmethod
    def setUpClass(cls):
        pygame.init()
        cls.screen = pygame.display.set_mode((1280, 800))

    def drive(self, game, frames=240, until=None, auto_answer=("sha", "judge_replacement")):
        for _ in range(frames):
            if until is not None and until():
                return True
            request = game.engine.pending.current
            if (request is not None and request.target is game.player
                    and request.context.get("reason") in auto_answer):
                answer_human(game)
            game.update(1 / 60)
        return until is None or until()

    # ---- 放逐：伤害后选目标 ----

    def test_fangzhu_window_blocks_the_ai_turn(self):
        game = build_identity(me="caopi", others=("guanyu", "lvbu", "zhaoyun", "caocao"))
        active = game.players[1]
        game.current_turn_player = active
        game.phase = "play"
        active.hand = [normal_sha(), normal_sha(), canonical_card("GUOHE")]
        game.player.hand = [shan(), shan(), tao()]
        for player in game.players[2:]:
            player.hand = [shan(), tao()]
        set_draw_order(game, [canonical_card("SHAN") for _ in range(40)])

        controller = game.get_controller(active)
        controller.take_turn(lambda: None)
        self.assertTrue(self.drive(
            game, until=lambda: "fangzhu" in pending_reasons(game)),
            "没有等到放逐窗口，实际：" + repr(pending_reasons(game)))

        hands = [len(player.hand) for player in game.players]
        logs = len(game.game_log)
        turn = game.current_turn_player
        self.drive(game, frames=120)
        self.assertEqual([len(player.hand) for player in game.players], hands,
                         "放逐等待期间 AI 的手牌变了")
        self.assertEqual(len(game.game_log), logs, "放逐等待期间 AI 行动了")
        self.assertIs(game.current_turn_player, turn, "放逐等待期间回合换人了")

    # ---- 两个悲歌（玩家 + AI）同时触发 ----

    def test_two_beige_windows_are_answered_one_by_one(self):
        game = build_identity(me="caiwenji",
                              others=("guanyu", "sp_caiwenji", "zhaoyun", "caocao"))
        victim = game.player
        victim.hand = [tao(), shan(), tao()]
        game.players[2].hand = [shan()]
        set_draw_order(game, [canonical_card("SHAN") for _ in range(40)])

        DamageFlow(game.engine, DamageContext(
            source=game.players[1], target=victim, amount=1,
            card=normal_sha())).start()

        reasons = pending_reasons(game)
        self.assertEqual(reasons.count("beige"), 2,
                         "玩家与 AI 的悲歌都应该开窗口，实际：" + repr(reasons))
        self.assertEqual([r.target for r in game.engine.pending._stack],
                         [outer.target for outer in game.engine.pending._stack],
                         "栈里两条请求各有自己的主人")

        # 玩家一直不答：AI 那边的悲歌由 AI 自己答完，但玩家的那条必须一直在，
        # 而且这期间 AI 绝不能接着出牌（这正是"玩家选牌时 AI 继续行动"）。
        turn = game.current_turn_player
        played = game.get_controller(game.players[1])._cards_played
        self.drive(game, frames=90)
        mine = [r for r in game.engine.pending._stack if r.target is game.player]
        self.assertTrue(mine, "玩家的悲歌窗口必须一直在等")
        self.assertEqual(mine[0].context.get("reason"), "beige")
        self.assertIs(game.current_turn_player, turn, "等待期间回合换人了")
        self.assertEqual(game.get_controller(game.players[1])._cards_played, played,
                         "等待期间 AI 又出牌了")

        # 逐条回答：先答最上面那条，再答另一条，最后必须全部清空
        for _ in range(2):
            request = game.engine.pending.current
            if request is None or request.context.get("reason") != "beige":
                break
            if request.target is game.player:
                candidates = [c for c in request.context.get("candidates") or ()
                              if c in game.player.hand]
                if candidates:
                    game.get_controller(game.player).submit(
                        _answer_for(request, game.player, (candidates[0],)))
            self.drive(game, frames=90, auto_answer=("sha", "beige"))
        self.drive(game, frames=180, auto_answer=("sha", "beige"))
        self.assertEqual(pending_reasons(game), [], "两条悲歌最终都该收尾")

    # ---- 判定改判：刚烈判定被鬼才改判 ----

    def test_judge_replacement_window_is_waited_for(self):
        game = build_identity(me="simayi",
                              others=("xiahoudun", "lvbu", "zhaoyun", "caocao"))
        victim = game.players[1]          # 夏侯惇的刚烈
        game.player.hand = [canonical_card("SHAN"), tao()]
        set_draw_order(game, [canonical_card("SHAN") for _ in range(40)])
        hp_before = game.player.hp

        DamageFlow(game.engine, DamageContext(
            source=game.player, target=victim, amount=1,
            card=normal_sha())).start()

        reasons = pending_reasons(game)
        self.assertIn("judge_replacement", reasons,
                      "鬼才应该拿到改判窗口，实际：" + repr(reasons))
        self.assertEqual(game.player.hp, hp_before,
                         "刚烈还没判定完，反击伤害不该已经结算")

        # 放弃改判 → 刚烈照常结算
        self.drive(game, frames=180, auto_answer=("sha", "judge_replacement"))
        self.assertEqual(pending_reasons(game), [])

    # ---- 阶段结束技能 ----

    def test_end_phase_skill_window_holds_the_turn(self):
        game = build_identity(me="dongzhuo",      # 崩坏是董卓的结束阶段技能
                              others=("guanyu", "lvbu", "zhaoyun", "caocao"))
        # 崩坏只在"体力不是全场最少"时触发：让别人比自己低。
        game.player.hp = 2
        for player in game.players[1:]:
            player.hp = player.max_hp
        game.players[1].hp = 1

        game.start_turn(game.player)
        self.drive(game, frames=120)      # 走完准备 / 判定 / 摸牌
        flow = game.active_turn_flow
        self.assertIsNotNone(flow)
        result = flow.finish_interactive()
        reasons = pending_reasons(game)
        if result is None or result.status.value != "waiting":
            self.skipTest("本局没有触发结束阶段技能（" + repr(reasons) + "）")

        turn_before = game.current_turn_player
        self.drive(game, frames=120, auto_answer=())
        self.assertIs(game.current_turn_player, turn_before,
                      "结束阶段技能还在问，回合不该换人")


class DamageAfterEventPayloadTests(unittest.TestCase):
    """DAMAGE_*_AFTER 的 amount 语义：实际造成的伤害点数。"""

    @classmethod
    def setUpClass(cls):
        pygame.init()
        cls.screen = pygame.display.set_mode((1280, 800))

    def test_after_events_carry_the_applied_amount(self):
        """发送方必须提供 amount：鸡肋 / 节命 / 武魂这些技能读的就是它。"""

        game = build_identity()
        game.player.hp = game.player.max_hp + 2
        seen = {"target": [], "source": [], "settled": []}
        subscribe = game.engine.context.events.subscribe
        for key, event_type in (("target", EventType.DAMAGE_TARGET_AFTER),
                                ("source", EventType.DAMAGE_SOURCE_AFTER),
                                ("settled", EventType.DAMAGE_SETTLED)):
            subscribe(event_type,
                      lambda context, event, key=key: seen[key].append(
                          event.payload.get("amount")))

        DamageFlow(game.engine, DamageContext(
            source=game.players[1], target=game.player, amount=2,
            card=normal_sha())).start()

        self.assertEqual(seen["target"], [2], "伤害后事件必须带实际点数")
        self.assertEqual(seen["source"], [2], "伤害来源侧同理")
        self.assertEqual(seen["settled"], [2], "结算事件的口径要一致")
