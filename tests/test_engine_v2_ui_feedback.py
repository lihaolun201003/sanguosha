"""桌面反馈：响应高亮、响应停顿、被别人拿走的牌要展示出来。

这三条都是"让真人看得清"的表现层行为，规则判定不受影响。
"""

import os
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame

from src.game import Game
from src.game.engine import RespondCardAction, UseCardAction
from src.game.engine.pending import PendingRequestType
from src.renderer import Renderer
from src.ui import seats, theme
from tests.legacy_helpers import canonical_card, equipment, set_draw_order, shan, tao


class FeedbackUiTestCase(unittest.TestCase):
    RESOLUTION = (1600, 900)

    def setUp(self):
        pygame.init()
        self.screen = pygame.display.set_mode(self.RESOLUTION)
        self.renderer = Renderer(self.screen)

    def tearDown(self):
        pygame.display.quit()

    def make_game(self, ai_count=2):
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
        return game

    def frame(self, game, count=1):
        for _ in range(count):
            game.update(1 / 60)
            self.renderer.update(1 / 60)
            self.renderer.draw(game)


# ==================================================
# 响应高亮
# ==================================================


class RespondingHighlightTests(FeedbackUiTestCase):
    def test_nobody_is_responding_without_a_request(self):
        game = self.make_game()
        self.frame(game)
        self.assertIsNone(self.renderer.responding_player(game))

    def test_the_player_who_owes_a_response_is_highlighted(self):
        game = self.make_game()
        attacker = game.player
        attack = canonical_card("SHA")
        attacker.hand = [attack]
        game.current_turn_player = attacker
        game.phase = "play"
        self.frame(game)

        game.submit_action(UseCardAction(attacker, attack, [game.players[1]]))
        for _ in range(200):
            self.frame(game)
            if game.pending_request is not None:
                break

        request = game.pending_request
        self.assertIsNotNone(request)
        self.assertIs(self.renderer.responding_player(game), request.target,
                      "被要求响应的角色必须是高亮对象")
        self.assertIs(self.renderer.responding_player(game), game.players[1])

    def test_highlight_clears_after_the_response(self):
        game = self.make_game()
        attacker = game.player
        attack = canonical_card("SHA")
        attacker.hand = [attack]
        game.current_turn_player = attacker
        game.phase = "play"
        game.players[1].hand = [shan()]
        self.frame(game)
        game.submit_action(UseCardAction(attacker, attack, [game.players[1]]))
        for _ in range(200):
            self.frame(game)
            if game.pending_request is not None:
                break

        request = game.pending_request
        game.submit_action(PassPendingAction := __import__(
            "src.game.engine", fromlist=["PassPendingAction"]).PassPendingAction(
            request.target, request.request_id))
        for _ in range(200):
            self.frame(game)
            if game.pending_request is None and not game.busy:
                break
        self.assertIsNone(self.renderer.responding_player(game))

    def test_human_response_uses_the_same_highlight(self):
        game = self.make_game()
        # 让 AI 对真人出杀：等待响应的是真人。
        ai = game.players[1]
        attack = canonical_card("SHA")
        ai.hand = [attack]
        game.current_turn_player = game.player
        game.phase = "play"
        self.frame(game)
        game.submit_action(UseCardAction(ai, attack, [game.player]))
        for _ in range(200):
            self.frame(game)
            if game.pending_request is not None:
                break
        self.assertIs(self.renderer.responding_player(game), game.player)

    def test_seat_panel_draws_with_the_responding_flag(self):
        game = self.make_game()
        self.frame(game)
        rect = self.renderer.get_player_panel_rects(game)[game.players[1]]
        # 直接调用绘制：既不能抛异常，也要接受新参数。
        seats.draw_seat(
            self.screen, game.players[1], rect, metrics=self.renderer.metrics,
            is_responding=True)
        seats.draw_seat(
            self.screen, game.players[1], rect, metrics=self.renderer.metrics,
            is_responding=False)
        self.assertTrue(seats.STATUS_RESPONDING)
        self.assertNotEqual(theme.RESPONDING, theme.GOLD_BRIGHT)


# ==================================================
# 响应停顿
# ==================================================


class ResponsePauseTests(FeedbackUiTestCase):
    def test_ai_response_is_queued_with_a_pause_when_pacing(self):
        game = self.make_game()
        attacker = game.player
        attack = canonical_card("SHA")
        attacker.hand = [attack]
        game.current_turn_player = attacker
        game.phase = "play"
        self.frame(game)
        game.submit_action(UseCardAction(attacker, attack, [game.players[1]]))

        # 节奏模式：响应不是立刻发生，而是排队等一会。
        self.assertIsNotNone(game.pending_request)
        self.assertTrue(game.busy, "AI 响应应当排进动作队列并产生停顿")
        queued = len(game.actions.queue)
        self.assertGreaterEqual(queued, 1)

    def test_key_responses_pause_longer_than_ordinary_ones(self):
        game = self.make_game()
        engine = game.engine
        self.assertGreater(engine.KEY_RESPONSE_PAUSE, engine.AI_RESPONSE_PAUSE)

        class FakeRequest:
            def __init__(self, reason, request_type=PendingRequestType.CONFIRM):
                self.context = {"reason": reason}
                self.request_type = request_type

        key = engine.response_pause(FakeRequest("sha", PendingRequestType.RESPOND_CARD))
        ordinary = engine.response_pause(FakeRequest("tuxi"))
        self.assertEqual(key, engine.KEY_RESPONSE_PAUSE)
        self.assertEqual(ordinary, engine.AI_RESPONSE_PAUSE)
        self.assertGreater(key, ordinary)

    def test_pause_is_scaled_by_the_speed_setting(self):
        game = self.make_game()
        game.set_speed(0.5)
        attacker = game.player
        attack = canonical_card("SHA")
        attacker.hand = [attack]
        game.current_turn_player = attacker
        game.phase = "play"
        self.frame(game)
        game.submit_action(UseCardAction(attacker, attack, [game.players[1]]))
        # 慢速档：同样的等待时间内不应该完成响应。
        for _ in range(20):
            game.update(1 / 60)
        self.assertIsNotNone(game.pending_request)
        self.assertTrue(game.busy)


# ==================================================
# 被别人拿走的牌要展示
# ==================================================


class TakenCardRevealTests(FeedbackUiTestCase):
    def _run_guohe(self, game, card_in_hand):
        victim = game.player
        victim.hand = [card_in_hand]
        guohe = canonical_card("GUOHE")
        actor = game.players[1]
        actor.hand = [guohe]
        game.current_turn_player = actor
        game.phase = "play"
        self.frame(game)

        seen_on_table = []
        game.submit_action(UseCardAction(actor, guohe, [victim]))
        for _ in range(600):
            self.frame(game)
            if any(card is card_in_hand for card, _rect in game.table_cards):
                seen_on_table.append(True)
            if not game.busy and game.pending_request is None:
                break
        return seen_on_table

    def test_discarded_card_is_shown_on_the_table(self):
        game = self.make_game()
        target_card = tao()
        seen = self._run_guohe(game, target_card)
        self.assertTrue(seen, "被拆掉的牌应当亮在桌面上")
        self.assertIn(target_card, game.deck.discard_pile, "过河拆桥：牌进弃牌堆")
        self.assertNotIn(target_card, game.player.hand)

    def test_stolen_card_is_shown_before_it_reaches_the_thief(self):
        game = self.make_game()
        target_card = tao()
        victim = game.player
        victim.hand = [target_card]
        shunshou = canonical_card("SHUNSHOU")
        actor = game.players[1]
        actor.hand = [shunshou]
        game.current_turn_player = actor
        game.phase = "play"
        self.frame(game)

        seen = False
        game.submit_action(UseCardAction(actor, shunshou, [victim]))
        for _ in range(600):
            self.frame(game)
            if any(card is target_card for card, _rect in game.table_cards):
                seen = True
            if not game.busy and game.pending_request is None:
                break
        self.assertTrue(seen, "被顺走的牌应当亮在桌面上")
        self.assertIn(target_card, actor.hand, "顺手牵羊：牌到使用者手里")

    def test_reveal_is_skipped_without_a_ui(self):
        """无头环境（没有 UI 矩形）不能因为要做动画而报错。"""

        game = self.make_game()
        game.ui_rects = {}
        card = tao()
        owner = game.player
        game.engine.show_taken_card(card, owner, game.players[1], to_hand=True)
        self.assertEqual(len(game.actions.queue), 0)

    def test_reveal_queues_three_steps_with_a_hold(self):
        game = self.make_game()
        self.frame(game)          # 建立 ui_rects
        card = tao()
        game.engine.show_taken_card(card, game.player, game.players[1], to_hand=True)
        self.assertEqual(len(game.actions.queue), 3, "飞入桌面 + 停留 + 飞向目的地")
        self.assertGreater(game.engine.TAKEN_CARD_HOLD, 0.4)


# ==================================================
# 桌面展示副本不能残留
# ==================================================


class TableCardCleanupTests(FeedbackUiTestCase):
    def _settle(self, game, steps=600):
        for step in range(steps):
            self.frame(game)
            if not game.busy and game.pending_request is None and step > 30:
                return
        raise AssertionError("对局没有在 %d 帧内稳定" % steps)

    def test_equipping_a_card_leaves_no_copy_on_the_table(self):
        game = self.make_game()
        armor = equipment("BAGUA")
        game.player.hand = [armor]
        game.current_turn_player = game.player
        game.phase = "play"
        self.frame(game)

        game.submit_action(UseCardAction(game.player, armor, []))
        self._settle(game)

        self.assertIs(game.player.get_equipment("armor"), armor, "牌要进装备区")
        self.assertEqual(list(game.table_cards), [], "桌面不能留下装备牌的副本")

    def test_stolen_equipment_leaves_no_copy_on_the_table(self):
        game = self.make_game()
        victim, actor = game.player, game.players[1]
        armor = equipment("RENWANG")
        victim.set_equipment(armor)
        shunshou = canonical_card("SHUNSHOU")
        actor.hand = [shunshou]
        game.current_turn_player = actor
        game.phase = "play"
        self.frame(game)

        game.submit_action(UseCardAction(actor, shunshou, [victim]))
        self._settle(game)

        self.assertIn(armor, actor.hand, "顺手牵羊把装备给到使用者")
        self.assertIsNone(victim.get_equipment("armor"))
        self.assertEqual(list(game.table_cards), [], "被偷的装备不能在桌面停留")

    def test_converted_card_taken_by_a_skill_leaves_no_virtual_copy(self):
        from src.game.conversion import PLAY_CONTEXT
        from tests.test_engine_v2_phase8_generals import heart

        game = self.make_game()
        guanyu, caocao = game.player, game.players[1]
        game.set_general(guanyu, "guanyu")
        game.set_general(caocao, "caocao")
        red = heart("K")
        guanyu.hand = [red]
        caocao.hand = []
        game.current_turn_player = guanyu
        game.phase = "play"
        self.frame(game)

        virtual = game.conversions.options_for(game, guanyu, red, PLAY_CONTEXT)[0][1]
        game.submit_action(UseCardAction(guanyu, virtual, [caocao]))
        self._settle(game)

        self.assertIn(red, caocao.hand, "奸雄拿到原始实体牌")
        self.assertEqual(list(game.table_cards), [], "虚拟牌副本也要收掉")

    def test_plain_sha_still_ends_in_the_discard_pile(self):
        game = self.make_game()
        attack = canonical_card("SHA")
        game.player.hand = [attack]
        game.players[1].hp = game.players[1].max_hp
        game.current_turn_player = game.player
        game.phase = "play"
        self.frame(game)

        game.submit_action(UseCardAction(game.player, attack, [game.players[1]]))
        self._settle(game)

        self.assertIn(attack, game.deck.discard_pile)
        self.assertEqual(list(game.table_cards), [])


# ==================================================
# 悬停查看武将技能
# ==================================================


class GeneralTooltipTests(FeedbackUiTestCase):
    def _dressed_game(self, seed=7):
        game = self.make_game(ai_count=3)
        game.general_pool = tuple(game.generals.ids())
        game.rng.seed(seed)
        game.set_general(game.player, "huangyueying")
        game.set_general(game.players[1], "zhangfei")
        game.set_general(game.players[2], "sunshangxiang")
        game.phase = "play"
        game.current_turn_player = game.player
        self.frame(game)
        return game

    def test_hovering_an_opponent_shows_their_skills(self):
        from src.ui import tooltip

        game = self._dressed_game()
        target = game.players[2]
        panel = self.renderer.get_player_panel_rects(game)[target]

        lines, header = tooltip.build_lines(
            game, target, self.renderer.metrics.fonts.get("small"), 300)
        self.assertIn("孙尚香", header)
        text = " ".join(text for text, _f, _c, _i in lines)
        self.assertIn("结姻", text)
        self.assertIn("枭姬", text)
        self.assertTrue(
            tooltip.draw_general_tooltip(
                self.screen, game, target, panel.center, self.renderer.metrics))

    def test_hovering_yourself_shows_your_own_skills(self):
        from src.ui import tooltip

        game = self._dressed_game()
        lines, header = tooltip.build_lines(
            game, game.player, self.renderer.metrics.fonts.get("small"), 300)
        self.assertIn("黄月英", header)
        text = " ".join(text for text, _f, _c, _i in lines)
        self.assertIn("集智", text)
        self.assertIn("奇才", text)

    def test_hovering_empty_space_draws_nothing(self):
        game = self._dressed_game()
        self.assertIsNone(self.renderer.player_at_position((4, 4), game))
        self.assertFalse(
            self.renderer._draw_general_tooltip(game, self.renderer.metrics))

    def test_tooltip_is_not_drawn_over_your_own_hand(self):
        game = self._dressed_game()
        game.player.hand = [tao(), tao()]
        self.frame(game)
        rects = self.renderer.get_card_rects(game.player.hand)
        self.renderer.draw(game, rects[0].center)
        self.assertIsNotNone(self.renderer.player_hand_hover(game),
                             "鼠标应当命中了手牌")
        self.assertFalse(
            self.renderer._draw_general_tooltip(game, self.renderer.metrics),
            "手牌上不弹武将提示，避免挡住出牌")

    def test_tooltip_survives_a_player_without_a_general(self):
        from src.ui import tooltip

        game = self._dressed_game()
        game.clear_general(game.players[1])
        lines, _header = tooltip.build_lines(
            game, game.players[1], self.renderer.metrics.fonts.get("small"), 300)
        self.assertTrue(lines)
        self.assertTrue(
            tooltip.draw_general_tooltip(
                self.screen, game, game.players[1], (400, 300), self.renderer.metrics))

    def test_skill_rows_match_the_registry(self):
        from src.ui import tooltip

        game = self._dressed_game()
        for player in game.players:
            general = game.generals.get(player.general_id)
            if general is None:
                continue
            rows = tooltip.skill_rows(game, player)
            self.assertEqual(
                [name for name, _desc in rows],
                [game.skill_registry.get(sid).name for sid in general.skill_ids],
                player.name)
            for _name, description in rows:
                self.assertTrue(description, "技能必须有说明文案")

    def test_tooltip_stays_inside_the_screen(self):
        from src.ui import tooltip

        game = self._dressed_game()
        for position in ((8, 8), (1590, 890), (1590, 8), (8, 890)):
            self.assertTrue(
                tooltip.draw_general_tooltip(
                    self.screen, game, game.players[2], position, self.renderer.metrics),
                str(position))


if __name__ == "__main__":
    unittest.main()
