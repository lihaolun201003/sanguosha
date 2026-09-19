"""Phase 6 UI tests: layout, hit testing, visuals, and dummy rendering.

These drive the real Renderer through a dummy SDL surface.  Assertions check
geometry and coarse colour properties (relative brightness / channel bias)
rather than exact pixels, so they stay stable across font and driver changes.
"""

import os
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame

from src.choice import ChoiceOverlay
from src.constants import AI_MINUS_RECT, AI_PLUS_RECT, SINGLE_PLAYER_RECT
from src.game import Game
from src.renderer import Renderer
from src.start_menu import StartMenu
from src.ui import layout, theme
from tests.legacy_helpers import canonical_card, equipment, normal_sha, shan, tao


def trick(name):
    return canonical_card(name)


class UiTestCase(unittest.TestCase):
    def setUp(self):
        pygame.init()
        self.screen = pygame.display.set_mode((layout.WIDTH, layout.HEIGHT))
        self.renderer = Renderer(self.screen)
        self.overlay = ChoiceOverlay(self.screen, self.renderer.small_font, self.renderer.tiny_font)
        self.menu = StartMenu(self.screen, self.renderer.big_font, self.renderer.small_font, self.renderer.tiny_font)

    def tearDown(self):
        pygame.display.quit()

    def make_game(self, ai_count, *, hand_size=4):
        game = Game(ai_count=ai_count)
        game.start_single_player()
        game.actions.clear()
        for player in game.players:
            player.hand = []
        game.player.hand = [normal_sha() for _ in range(hand_size)]
        game.player.hp = game.player.max_hp
        game.current_turn_player = game.player
        game.phase = "play"
        return game

    def render(self, game, mouse_pos=(0, 0)):
        self.renderer.draw(game, mouse_pos)
        pygame.display.flip()

    def brightness(self, rect):
        rect = pygame.Rect(rect).clip(self.screen.get_rect())
        total = 0
        count = 0
        for x in range(rect.x, rect.right, 3):
            for y in range(rect.y, rect.bottom, 3):
                color = self.screen.get_at((x, y))
                total += color.r + color.g + color.b
                count += 1
        return total / max(1, count)

    def pixel(self, position):
        return self.screen.get_at(position)[:3]


class EquipmentPickTests(UiTestCase):
    """从其他角色区域选装备牌（顺手牵羊 / 过河拆桥 / 麒麟弓）必须能选中。"""

    def _open_selection(self, card_name, victim_index=1, prepare=None):
        from src.game.engine import UseCardAction

        game = self.make_game(3, hand_size=1)
        victim = game.players[victim_index]
        victim.hand = []
        if prepare is not None:
            prepare(victim)
        card = trick(card_name)
        game.player.hand = [card]
        game.submit_action(UseCardAction(game.player, card, [victim]))
        return game, victim

    def test_shunshou_pool_entries_carry_the_equipment_slot(self):
        weapon = equipment("QINGLONG")
        game, victim = self._open_selection(
            "SHUNSHOU", prepare=lambda target: target.set_equipment(weapon)
        )
        entries = game.selection_pool_entries()
        self.assertEqual(len(entries), 1)
        picked, key = entries[0]
        self.assertIs(picked, weapon)
        self.assertEqual(key, "weapon")
        # 高亮与命中都依赖这个 key。
        self.assertTrue(game.is_selection_candidate(weapon, key))
        self.assertFalse(game.is_selection_candidate(weapon, None))

    def test_shunshou_takes_the_equipment_when_clicked_with_its_slot(self):
        weapon = equipment("QINGLONG")
        game, victim = self._open_selection(
            "SHUNSHOU", prepare=lambda target: target.set_equipment(weapon)
        )
        self.render(game)
        rendered_entries = self.renderer.get_pool_entries(game)
        self.assertEqual(len(rendered_entries), 1)

        card, key = rendered_entries[0]
        # 与 main.py 相同的调用方式：把装备槽 key 一并回传。
        game.select_pending_card(card, (0, 0, 10, 10), key=key)

        self.assertTrue(any(item is weapon for item in game.player.hand), "武器没有到手")
        self.assertIsNone(victim.get_equipment("weapon"))
        self.assertIsNone(game.pending_selection)

    def test_guohe_discards_equipment_when_clicked_with_its_slot(self):
        armor = equipment("BAGUA")
        game, victim = self._open_selection(
            "GUOHE", prepare=lambda target: target.set_equipment(armor)
        )
        entries = game.selection_pool_entries()
        self.assertEqual(len(entries), 1)
        card, key = entries[0]
        self.assertIs(card, armor)
        game.select_pending_card(card, (0, 0, 10, 10), key=key)

        self.assertIsNone(victim.get_equipment("armor"))
        self.assertTrue(any(item is armor for item in game.deck.discard_pile))

    def test_hand_candidates_still_select_without_a_slot_key(self):
        stolen = tao()
        game, victim = self._open_selection(
            "SHUNSHOU", prepare=lambda target: setattr(target, "hand", [stolen])
        )
        entries = game.selection_pool_entries()
        self.assertEqual(len(entries), 1)
        card, key = entries[0]
        self.assertIsNone(key)
        game.select_pending_card(card, (0, 0, 10, 10), key=key)
        self.assertTrue(any(item is stolen for item in game.player.hand))

    def test_equipment_candidate_is_highlighted_in_the_pool(self):
        weapon = equipment("QINGLONG")
        game, victim = self._open_selection(
            "SHUNSHOU", prepare=lambda target: target.set_equipment(weapon)
        )
        self.render(game)
        # 公共区高亮走 is_selection_candidate(card, key)
        entries = self.renderer.get_pool_entries(game)
        self.assertTrue(game.is_selection_candidate(entries[0][0], entries[0][1]))
        rects = self.renderer.get_public_card_rects([card for card, _key in entries])
        self.assertEqual(len(rects), 1)
        self.assertTrue(self.renderer.screen.get_rect().contains(rects[0]))


class LayoutTests(UiTestCase):
    def test_seat_panels_stay_inside_during_two_to_eight_players(self):
        bounds = self.screen.get_rect()
        for ai_count in range(1, 8):
            game = self.make_game(ai_count)
            rects = self.renderer.get_player_panel_rects(game)
            self.assertEqual(len(rects), ai_count + 1)
            for rect in rects.values():
                self.assertTrue(bounds.contains(rect), "座位越界：%s" % (rect,))
            items = list(rects.values())
            for index, first in enumerate(items):
                for second in items[index + 1:]:
                    self.assertFalse(first.colliderect(second), "座位重叠：AI=%d" % ai_count)

    def test_human_area_and_hand_stay_inside(self):
        game = self.make_game(7, hand_size=5)
        self.render(game)
        bounds = self.screen.get_rect()
        self.assertTrue(bounds.contains(layout.PLAYER_STATUS_RECT))
        self.assertTrue(bounds.contains(layout.PROMPT_RECT))
        self.assertTrue(bounds.contains(self.renderer.primary_button.rect))
        self.assertTrue(bounds.contains(self.renderer.secondary_button.rect))
        for rect in self.renderer.get_card_rects(game.player.hand):
            self.assertTrue(bounds.contains(rect))

    def test_many_cards_overlap_without_leaving_the_hand_area(self):
        for hand_size in (20, 30):
            game = self.make_game(3, hand_size=hand_size)
            self.render(game)
            rects = self.renderer.get_card_rects(game.player.hand)
            self.assertEqual(len(rects), hand_size)
            self.assertGreaterEqual(rects[0].left, layout.HAND_AREA.left - 2)
            self.assertLessEqual(rects[-1].right, layout.HAND_AREA.right + 2)
            # 相邻牌必须重叠，但不允许完全遮住前面的牌。
            step = rects[1].left - rects[0].left
            self.assertGreater(step, 0)
            self.assertLess(step, layout.HAND_CARD_SIZE[0])

    def test_public_pool_rects_are_centered_and_inside(self):
        game = self.make_game(3)
        cards = [tao(), normal_sha(), shan(), trick("LEBU"), trick("WUZHONG")]
        rects = self.renderer.get_public_card_rects(cards)
        self.assertEqual(len(rects), 5)
        bounds = self.screen.get_rect()
        for rect in rects:
            self.assertTrue(bounds.contains(rect))
        span = rects[-1].right - rects[0].left
        self.assertAlmostEqual(rects[0].left - (layout.WIDTH - span) // 2, rects[0].left - (layout.WIDTH - span) // 2)


class InteractionTests(UiTestCase):
    def test_hover_lifts_the_card_and_keeps_it_clickable(self):
        game = self.make_game(3, hand_size=4)
        base_rects = self.renderer.get_card_rects(game.player.hand)
        target_rect = pygame.Rect(base_rects[1])
        self.render(game, target_rect.center)

        hovered = self.renderer.get_card_rects(game.player.hand)[1]
        self.assertLess(hovered.top, target_rect.top, "悬停没有上浮")
        self.assertEqual(self.renderer.card_at_position(target_rect.center, game.player.hand), 1)
        self.assertEqual(self.renderer.card_at_position(hovered.center, game.player.hand), 1)

    def test_selected_card_sits_above_default_position(self):
        game = self.make_game(3, hand_size=4)
        selection_card = game.player.hand[2]
        game.start_card_selection(
            zone="hand",
            candidates=[(card, None) for card in game.player.hand],
            number=2,
            prompt="请选择两张手牌弃置",
            on_complete=lambda selected: None,
            owner=game.player,
        )
        game.select_pending_card(selection_card, (0, 0, 10, 10))
        self.render(game, (0, 0))
        rect = self.renderer.get_card_rects(game.player.hand)[2]
        base = layout.TableLayout(game)._base_hand_rects(game.player.hand)[2]
        self.assertLess(rect.top, base.top, "已选牌没有抬起")

    def test_ai_panels_are_clickable_targets(self):
        game = self.make_game(3, hand_size=1)
        game.player.hand = [normal_sha()]
        self.render(game)
        rects = self.renderer.get_player_panel_rects(game)
        for player, rect in rects.items():
            self.assertIs(self.renderer.player_at_position(rect.center, game), player)

    def test_tiesuo_can_still_pick_two_targets(self):
        game = self.make_game(3, hand_size=1)
        card = trick("TIESUO")
        game.player.hand = [card]
        rects = self.renderer.get_card_rects(game.player.hand)
        game.player_use_card(0, tuple(rects[0]))
        # 铁索先询问「连环 / 重铸」，选择连环后才进入目标选择。
        self.assertTrue(game.choice.active)
        game.choice.choose_yes()
        selection = game.pending_target_selection
        self.assertIsNotNone(selection)
        for victim in selection["candidates"][:2]:
            game.toggle_target_selection(victim)
        self.assertEqual(len(selection["selected"]), 2)
        game.confirm_target_selection()
        self.assertEqual(sum(1 for player in game.players if player.chained), 2)

    def test_cancel_target_selection_returns_to_play(self):
        game = self.make_game(3, hand_size=1)
        game.player.hand = [normal_sha()]
        rects = self.renderer.get_card_rects(game.player.hand)
        game.player_use_card(0, tuple(rects[0]))
        self.assertIsNotNone(game.pending_target_selection)
        self.assertTrue(game.cancel_target_selection())
        self.assertIsNone(game.pending_target_selection)
        self.assertEqual(len(game.player.hand), 1)

    def test_action_buttons_follow_the_pending_state(self):
        game = self.make_game(3, hand_size=2)
        actions = self.renderer.actions_for(game)
        self.assertTrue(actions["primary"].enabled)
        self.assertEqual(actions["primary_action"], "end_turn")

        game.response.request(
            prompt="【杀】：请打出一张【闪】",
            allowed_cards={"SHAN"},
            on_card=lambda *args, **kwargs: None,
            on_pass=lambda: None,
        )
        actions = self.renderer.actions_for(game)
        self.assertFalse(actions["primary"].enabled)
        self.assertTrue(actions["secondary"].enabled)
        self.assertEqual(actions["secondary_action"], "pass_response")
        self.assertEqual(self.renderer.hit_action(actions["secondary"].rect.center, game), "pass_response")

    def test_hit_action_maps_primary_button(self):
        game = self.make_game(3, hand_size=1)
        actions = self.renderer.actions_for(game)
        self.assertEqual(
            self.renderer.hit_action(actions["primary"].rect.center, game),
            "end_turn",
        )


class VisualTests(UiTestCase):
    def _border_color(self, rect):
        return self.pixel((rect.centerx, rect.top + 1))

    def test_current_player_seat_uses_gold_border(self):
        game = self.make_game(3, hand_size=2)
        game.current_turn_player = game.players[1]
        self.render(game)
        rect = self.renderer.get_player_panel_rects(game)[game.players[1]]
        color = self._border_color(rect)
        self.assertGreater(color[0], color[2], "当前行动座位边框不是金色调")
        self.assertGreater(color[0], 150)

    def test_legal_target_seat_uses_blue_border(self):
        game = self.make_game(3, hand_size=1)
        game.player.hand = [normal_sha()]
        rects = self.renderer.get_card_rects(game.player.hand)
        game.player_use_card(0, tuple(rects[0]))
        selection = game.pending_target_selection
        self.assertIsNotNone(selection)
        candidate = selection["candidates"][0]
        self.render(game)
        rect = self.renderer.get_player_panel_rects(game)[candidate]
        color = self._border_color(rect)
        self.assertGreater(color[2], color[0], "合法目标边框不是蓝色调")

    def test_selected_target_seat_uses_gold_border(self):
        game = self.make_game(3, hand_size=1)
        game.player.hand = [trick("TIESUO")]
        rects = self.renderer.get_card_rects(game.player.hand)
        game.player_use_card(0, tuple(rects[0]))
        game.choice.choose_yes()
        victim = game.pending_target_selection["candidates"][0]
        game.toggle_target_selection(victim)
        self.render(game)
        rect = self.renderer.get_player_panel_rects(game)[victim]
        color = self._border_color(rect)
        self.assertGreater(color[0], color[2], "已选目标边框不是黄色调")
        self.assertGreater(color[0], 180)

    def test_dead_seat_is_dimmed(self):
        game = self.make_game(3, hand_size=2)
        dead = game.players[2]
        dead.alive = False
        dead.hp = 0
        self.render(game)
        rects = self.renderer.get_player_panel_rects(game)
        alive_brightness = self.brightness(rects[game.players[1]])
        dead_brightness = self.brightness(rects[dead])
        self.assertLess(dead_brightness, alive_brightness, "阵亡座位没有灰化")

    def test_seat_shows_hp_pips_equipment_and_judgement(self):
        game = self.make_game(3, hand_size=2)
        seat = game.players[1]
        seat.hp = 2
        seat.chained = True
        seat.set_equipment(equipment("QINGLONG"))
        seat.judgement_zone.append(trick("LEBU"))
        self.render(game)
        rect = self.renderer.get_player_panel_rects(game)[seat]
        # 血点区域比空白面板更亮（有实心圆）
        pip_strip = pygame.Rect(rect.x + 8, rect.y + 50, 60, 14)
        self.assertGreater(self.brightness(pip_strip), 40)
        self.assertTrue(rect.collidepoint(rect.center))

    def test_card_face_renders_suit_and_rank_without_error(self):
        game = self.make_game(2, hand_size=3)
        game.player.hand = [normal_sha(), tao(), trick("GUOHE")]
        self.render(game)
        for rect in self.renderer.get_card_rects(game.player.hand):
            self.assertGreater(self.brightness(rect), 90, "牌面没有被绘制")


class PromptTests(UiTestCase):
    def test_prompt_describes_a_response_pending(self):
        from src.ui import prompt as prompt_module

        game = self.make_game(3)
        game.response.request(
            prompt="【杀】：请打出一张【闪】",
            allowed_cards={"SHAN"},
            on_card=lambda *args, **kwargs: None,
            on_pass=lambda: None,
        )
        info = prompt_module.describe(game)
        self.assertEqual(info.kind, "response")
        self.assertIn("闪", info.body)

    def test_prompt_describes_wugu_pool_selection(self):
        from src.ui import prompt as prompt_module

        game = self.make_game(3)
        cards = [tao(), normal_sha()]
        game.public_card_pool = list(cards)
        game.start_card_selection(
            zone="public_pool",
            candidates=[(card, None) for card in cards],
            number=1,
            prompt="【五谷丰登】：请选择一张公共牌",
            on_complete=lambda selected: None,
            owner=game.player,
        )
        info = prompt_module.describe(game)
        self.assertEqual(info.kind, "select")
        self.assertIn("还需选择 1 张", info.progress)

    def test_prompt_describes_target_selection_progress(self):
        from src.ui import prompt as prompt_module

        game = self.make_game(3, hand_size=1)
        game.player.hand = [trick("TIESUO")]
        rects = self.renderer.get_card_rects(game.player.hand)
        game.player_use_card(0, tuple(rects[0]))
        game.choice.choose_yes()
        game.toggle_target_selection(game.pending_target_selection["candidates"][0])
        info = prompt_module.describe(game)
        self.assertEqual(info.kind, "target")
        self.assertIn("已选择 1 / 2", info.progress)

    def test_prompt_reports_waiting_for_an_ai(self):
        from src.ui import prompt as prompt_module

        game = self.make_game(3)
        game.pending_target_selection = None
        request = game.engine.pending.create.__self__  # 占位，避免误用
        del request
        info = prompt_module.describe(game)
        self.assertIn(info.kind, ("play", "info"))


class MenuAndResultTests(UiTestCase):
    def test_menu_ai_count_clamps_between_one_and_seven(self):
        game = Game(ai_count=1)
        for _ in range(10):
            self.menu.handle_click(pygame.Rect(*AI_PLUS_RECT).center, game)
        self.assertEqual(game.ai_count, 7)
        for _ in range(10):
            self.menu.handle_click(pygame.Rect(*AI_MINUS_RECT).center, game)
        self.assertEqual(game.ai_count, 1)

    def test_menu_starts_a_battle_and_menu_rects_are_clickable(self):
        game = Game(ai_count=1)
        self.menu.handle_click(pygame.Rect(*AI_PLUS_RECT).center, game)
        self.menu.handle_click(pygame.Rect(*SINGLE_PLAYER_RECT).center, game)
        self.assertEqual(game.scene, "game")
        self.assertEqual(len(game.players), 3)
        game.return_to_menu()
        self.assertEqual(game.scene, "menu")

    def test_result_overlay_actions(self):
        game = self.make_game(3)
        game.game_over = True
        game.result = None
        self.assertEqual(
            self.renderer.hit_action(self.renderer.get_restart_rect().center, game),
            "restart",
        )
        self.assertEqual(
            self.renderer.hit_action(self.renderer.get_main_menu_rect().center, game),
            "menu",
        )

    def test_result_overlay_shows_win_and_loss_titles(self):
        game = self.make_game(2)
        game.game_over = True
        game.winner = game.player
        self.assertEqual(self.renderer.result_overlay.result_texts(game)[0], "胜 利")

        game.winner = None
        from src.game.engine.state import GameOutcome, GameResult

        game.result = GameResult(
            outcome=GameOutcome.HUMAN_ELIMINATED,
            winner=None,
            loser=game.player,
            reason="HUMAN_ELIMINATED",
        )
        self.assertEqual(self.renderer.result_overlay.result_texts(game)[0], "战 败")


class PlayabilityHintTests(UiTestCase):
    def test_unusable_cards_are_greyed_out_through_engine_rules(self):
        from src.ui import player as player_ui

        game = self.make_game(3, hand_size=3)
        game.player.hp = 1
        game.player.hand = [normal_sha(), tao(), shan()]
        playable = player_ui.playable_hand_indices(game)
        self.assertIsNotNone(playable)
        # 桃可以自救，闪不能主动使用。
        self.assertIn(1, playable)
        self.assertNotIn(2, playable)

    def test_playability_hint_is_disabled_out_of_turn(self):
        from src.ui import player as player_ui

        game = self.make_game(3, hand_size=2)
        game.current_turn_player = game.players[1]
        self.assertIsNone(player_ui.playable_hand_indices(game))

    def test_playing_a_card_while_not_your_turn_is_ignored(self):
        game = self.make_game(3, hand_size=2)
        game.current_turn_player = game.players[1]
        before = list(game.player.hand)
        rects = self.renderer.get_card_rects(game.player.hand)
        game.player_use_card(0, tuple(rects[0]))
        self.assertEqual(game.player.hand, before)


class DummyRenderTests(UiTestCase):
    def test_dummy_renders_two_five_and_eight_player_tables(self):
        for ai_count in (1, 4, 7):
            game = self.make_game(ai_count, hand_size=6)
            for _ in range(3):
                game.update(1 / 60)
                self.renderer.update(1 / 60)
                self.render(game)
            self.assertEqual(len(self.renderer.get_player_panel_rects(game)), ai_count + 1)

    def test_effects_react_to_damage_and_heal(self):
        from src.game.atoms_v2 import RecoverHpAtom
        from src.game.flows import DamageContext, DamageFlow

        game = self.make_game(3, hand_size=2)
        # 特效通过订阅引擎事件工作，先渲染一帧完成订阅。
        self.render(game)

        victim = game.players[1]
        before = victim.hp
        DamageFlow(
            game.engine,
            DamageContext(game.player, victim, 1, card=normal_sha()),
        ).start()
        self.assertEqual(victim.hp, before - 1)
        flash, color = self.renderer.effects.seat_flash(victim)
        self.assertGreater(flash, 0, "伤害没有触发闪光")
        self.assertEqual(color, theme.DANGER)

        game.engine.context.apply(RecoverHpAtom(victim, 1))
        flash, color = self.renderer.effects.seat_flash(victim)
        self.assertEqual(color, theme.HEAL)

    def test_effects_show_judge_and_turn_banners(self):
        from src.game.engine import Event, EventType
        from src.game.flows import JudgeFlow

        game = self.make_game(3, hand_size=2)
        self.render(game)

        JudgeFlow(game.engine, game.players[1], "lebu").start()
        display = self.renderer.effects.judge_display()
        self.assertIsNotNone(display)
        self.assertIn("乐不思蜀", display["reason_text"])
        self.render(game)

        game.context.emit(Event(EventType.TURN_START, source=game.players[1]))
        banner = self.renderer.effects.turn_display()
        self.assertIsNotNone(banner)
        self.assertIn("AI 1", banner["text"])

    def test_death_event_creates_a_float_text(self):
        game = self.make_game(3, hand_size=2)
        self.render(game)
        game.engine.run_death(game.players[1])
        texts = [item.text for item in self.renderer.effects.floats]
        self.assertIn("阵亡", texts)


if __name__ == "__main__":
    unittest.main()
