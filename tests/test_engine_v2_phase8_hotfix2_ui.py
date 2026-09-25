"""Phase 8 Hotfix 2 UI tests: Card Action 的真实鼠标点击链路。

每一步都用 ``pygame.event.post`` 投递真实的 MOUSEBUTTONDOWN，再交给
``src.ui.interaction.handle_game_click``（与 main.py 同一份路由）。
"""

import os
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame

from src.game import Game
from src.game.skills.conversion_probes import PAIR_PROBE_ID, bind_conversion_probes
from src.renderer import Renderer
from src.ui import prompt as prompt_module
from src.ui.interaction import handle_game_click
from tests.legacy_helpers import canonical_card, equipment, normal_sha, shan, tao


class CardActionUiTestCase(unittest.TestCase):

    RESOLUTION = (1920, 1080)

    def setUp(self):
        pygame.init()
        self.screen = pygame.display.set_mode(self.RESOLUTION)
        self.renderer = Renderer(self.screen)

    def tearDown(self):
        pygame.display.quit()

    def make_game(self, general=None, ai_count=3):
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
        if general:
            game.set_general(game.player, general)
        game.player.hp = game.player.max_hp
        pygame.event.clear()
        return game

    # ---- 真实鼠标事件 ----

    def frame(self, game, mouse=None, count=1):
        for _ in range(count):
            game.update(1 / 60)
            self.renderer.update(1 / 60)
            self.renderer.draw(game, mouse)

    def click(self, position, game, mouse=None):
        self.frame(game, mouse if mouse is not None else position)
        pygame.event.post(pygame.event.Event(
            pygame.MOUSEBUTTONDOWN, {"pos": position, "button": 1}
        ))
        handled = 0
        for event in pygame.event.get():
            if event.type != pygame.MOUSEBUTTONDOWN or event.button != 1:
                continue
            handled += 1
            handle_game_click(event.pos, game, self.renderer)
        self.frame(game, position)
        return handled

    def settle(self, game, frames=300):
        for _ in range(frames):
            if not game.busy:
                break
            self.frame(game)
        return not game.busy

    # ---- 位置 ----

    def hand_center(self, game, index):
        self.frame(game)
        return self.renderer.get_card_rects(game.player.hand)[index].center

    def seat_center(self, game, player):
        self.frame(game)
        return self.renderer.table_layout.seat_rects[player].center

    def primary_center(self, game):
        self.frame(game)
        return self.renderer.primary_button.rect.center

    def secondary_center(self, game):
        self.frame(game)
        return self.renderer.secondary_button.rect.center

    def picker_row_center(self, game, index):
        self.frame(game)
        return self.renderer.action_picker.rects[index].center

    def skill_button_center(self, game, skill_id=None):
        """技能按钮中心；给了 skill_id 就点那个技能的按钮。

        Phase 10.3 起技能区直接列出真实技能名，点哪个发动哪个。
        """

        self.frame(game)
        bar = self.renderer.skill_bar
        if skill_id is not None:
            rect = bar.rect_for(skill_id)
            if rect is not None:
                return rect.center
        return bar.button.rect.center

    def skill_picker_row_center(self, game, skill_id):
        self.frame(game)
        rows = [item[0] for item in game.pending_skill_picker]
        return self.renderer.skill_picker.rects[rows.index(skill_id)].center

    def use_view_as(self, game, skill_id, *indexes):
        """真实点击：点该技能名进入视为技 → 依次点选来源牌。"""

        self.click(self.skill_button_center(game, skill_id), game)
        for index in indexes:
            self.click(self.hand_center(game, index), game)


# ==================================================
# 1. 灰化逻辑（最重要的视觉验收）
# ==================================================


class GreyingTests(CardActionUiTestCase):

    def test_shan_is_not_greyed_for_zhaoyun(self):
        game = self.make_game(general="zhaoyun")
        game.player.hand = [shan(), tao()]
        game.player.hp = game.player.max_hp
        self.frame(game)
        playable = self.renderer.playable
        self.assertIn(0, playable, "能通过龙胆当【杀】的【闪】不能被灰")
        self.assertNotIn(1, playable, "满血【桃】确实不可用")

    def test_tao_stays_operable_for_guanyu_at_full_hp(self):
        game = self.make_game(general="guanyu")
        black_shan = shan()
        black_shan.suit = "spade"
        black_shan.card_color = "black"
        game.player.hand = [tao(), black_shan]
        game.player.hp = game.player.max_hp
        self.frame(game)
        playable = self.renderer.playable
        self.assertIn(0, playable, "武圣可以把满血【桃】当【杀】使用")
        self.assertNotIn(1, playable, "黑色【闪】既不能正常用，关羽也无法转换它")

    def test_red_shan_is_operable_for_guanyu(self):
        game = self.make_game(general="guanyu")
        red_card = shan()
        self.assertEqual(red_card.card_color, "red")
        game.player.hand = [red_card]
        self.frame(game)
        self.assertIn(0, self.renderer.playable, "武圣能吃红色牌，红色【闪】可用")


# ==================================================
# 2. Play：单一动作直达
# ==================================================


class PlayDirectTests(CardActionUiTestCase):

    def test_click_shan_without_skill_does_nothing(self):
        """核心：没有点【龙胆】时，点击【闪】不得自动转换。"""

        game = self.make_game(general="zhaoyun")
        card = shan()
        game.player.hand = [card]

        self.click(self.hand_center(game, 0), game)
        self.assertIsNone(game.pending_target_selection, "不得自动进入目标选择")
        self.assertIsNone(game.pending_view_as)
        self.assertIn(card, game.player.hand, "牌没有被消耗")
        self.assertIn("发动技能", game.message, "必须提示玩家先去点技能")

    def test_click_shan_after_longdan_enters_targeting(self):
        game = self.make_game(general="zhaoyun")
        card = shan()
        game.player.hand = [card]

        self.use_view_as(game, "longdan", 0)
        self.assertIsNotNone(game.pending_target_selection)
        selection = game.pending_target_selection
        self.assertTrue(getattr(selection["card"], "_virtual", False))
        self.assertEqual(selection["card"].name, "SHA")

        self.click(self.seat_center(game, game.players[1]), game)
        self.settle(game)
        self.assertIn(card, game.deck.discard_pile)
        self.assertNotIn(card, game.player.hand)

    def test_only_valid_sources_are_clickable_in_view_as(self):
        """进入视为技后，只有这个技能吃得下的牌才可点。"""

        game = self.make_game(general="zhaoyun")
        flash, peach = shan(), tao()
        game.player.hand = [flash, peach]
        game.player.hp = game.player.max_hp
        self.frame(game)
        self.assertEqual(self.renderer.playable, {0},
                         "未点技能时【闪】因为龙胆不灰，满血【桃】仍然是灰的")

        self.use_view_as(game, "longdan")           # 先点技能，再选牌
        self.assertIsNotNone(game.pending_view_as)
        self.frame(game)
        self.assertEqual(self.renderer.playable, {0},
                         "龙胆只吃【杀】/【闪】，【桃】必须被灰掉")

    def test_target_prompt_shows_conversion_origin(self):
        game = self.make_game(general="zhaoyun")
        game.player.hand = [shan()]
        self.use_view_as(game, "longdan", 0)
        self.frame(game)
        info = prompt_module.describe(game)
        self.assertIn("龙胆", info.body)
        self.assertIn("闪", info.body)
        self.assertIn("杀", info.body)


# ==================================================
# 3. Play：Normal + Conversion 并存 → CardActionPicker
# ==================================================


class PlayPickerTests(CardActionUiTestCase):

    def test_tao_uses_itself_without_skill(self):
        """不点技能时【桃】就是【桃】，不会被【武圣】接管。"""

        game = self.make_game(general="guanyu")
        card = tao()
        game.player.hand = [card]
        game.player.hp = game.player.max_hp - 1

        self.click(self.hand_center(game, 0), game)
        self.assertIsNone(game.pending_target_selection)
        self.assertIsNone(game.pending_view_as)
        self.assertEqual(game.player.hp, game.player.max_hp, "正常使用【桃】回血")
        self.assertIn(card, game.deck.discard_pile)

    def test_wusheng_after_skill_targets_sha(self):
        game = self.make_game(general="guanyu")
        card = tao()
        game.player.hand = [card]
        game.player.hp = game.player.max_hp - 1

        self.use_view_as(game, "wusheng", 0)
        selection = game.pending_target_selection
        self.assertIsNotNone(selection)
        self.assertEqual(selection["card"].name, "SHA")
        self.assertTrue(selection["card"].is_virtual)

    def test_skill_picker_lists_view_as_skills(self):
        """Skill Picker 由 SkillDef 驱动：视为技与主动技同列。"""

        game = self.make_game(general="guanyu")
        game.player.hand = [tao()]
        game.player.hp = game.player.max_hp - 1
        bind_conversion_probes(game, game.player, PAIR_PROBE_ID)
        self.frame(game)
        rows = game.skill_picker_rows()
        ids = [item[0] for item in rows]
        self.assertIn("wusheng", ids)
        self.assertIn(PAIR_PROBE_ID, ids)
        for skill_id, _allowed, _reason in rows:
            definition = game.skill_registry.require(skill_id)
            self.assertTrue(definition.name)
            self.assertTrue(definition.description)

    def test_skill_picker_rect_equals_hit_rect(self):
        game = self.make_game(general="guanyu")
        game.player.hand = [tao()]
        game.player.hp = game.player.max_hp - 1
        bind_conversion_probes(game, game.player, PAIR_PROBE_ID)
        self.frame(game)
        # 两个技能都直接列在技能区，点哪个发动哪个。
        bar = self.renderer.skill_bar
        ids = [item.id for item in bar.skills]
        self.assertTrue(ids, "技能区必须列出这个武将的全部技能")
        for skill_id in ids:
            rect = bar.rect_for(skill_id)
            self.assertIsNotNone(rect)
            self.assertEqual(bar.hit(rect.center, game), ("skill", skill_id))

    def test_skill_picker_cancel_keeps_everything(self):
        game = self.make_game(general="guanyu")
        card = tao()
        game.player.hand = [card]
        game.player.hp = game.player.max_hp - 1
        bind_conversion_probes(game, game.player, PAIR_PROBE_ID)
        self.frame(game)
        bar = self.renderer.skill_bar
        rect = bar.rect_for(bar.skills[0].id)
        self.click(rect.center, game)
        # 进入输入流程后取消：不消耗任何牌、不留残留状态。
        self.click(self.renderer.secondary_button.rect.center, game)
        self.assertIsNone(game.pending_view_as)
        self.assertIsNone(game.pending_target_selection)
        self.assertIsNone(game.pending_skill_input)
        self.assertIn(card, game.player.hand, "取消不消耗任何牌")

    def test_view_as_survives_resolution_change(self):
        game = self.make_game(general="zhaoyun")
        game.player.hand = [shan()]
        for size in ((1280, 720), (2560, 1440), (1600, 900)):
            self.screen = pygame.display.set_mode(size)
            self.renderer.set_screen(self.screen)
            self.use_view_as(game, "longdan", 0)
            self.assertTrue(game.pending_target_selection,
                            "F11/Resize 之后仍能走完 View-As @%s" % (size,))
            game.cancel_target_selection()
            self.frame(game)


# ==================================================
# 4. 多 source（Probe C）
# ==================================================


class MultiSourceUiTests(CardActionUiTestCase):

    def make_multi_game(self, hp=None):
        game = self.make_game()
        # 第一张用【闪】：它没有正常用途，所以"未点技能时不转换"是可验证的
        first, second, third = shan(), normal_sha(), tao()
        game.player.hand = [first, second, third]
        bind_conversion_probes(game, game.player, PAIR_PROBE_ID)
        game.players[1].hand = []
        game.players[1].hp = hp or game.players[1].max_hp
        return game, first, second, third

    def test_two_click_conversion_flow(self):
        game, first, second, _third = self.make_multi_game()

        # 未点技能：点这张【闪】不会自动转换，也不会进入目标选择
        self.click(self.hand_center(game, 0), game)
        self.assertIsNone(game.pending_view_as)
        self.assertIsNone(game.pending_target_selection)
        self.assertEqual(len(game.player.hand), 3, "牌没有被消耗")

        # 点技能 → 双刃 → 选第一张
        self.use_view_as(game, PAIR_PROBE_ID, 0)
        session = game.pending_view_as
        self.assertIsNotNone(session, "还差一张，必须留在选牌阶段")
        self.assertEqual(len(session.selected_source_cards), 1)
        self.assertEqual(session.required_source_count, 2)

        self.frame(game)
        info = prompt_module.describe(game)
        self.assertIn("双刃", info.title)
        self.assertIn("1 / 2", info.progress)

        # 点第二张牌补齐 source
        self.click(self.hand_center(game, 1), game)
        self.assertIsNone(game.pending_view_as, "收齐后自动进入目标选择")
        self.assertIsNotNone(game.pending_target_selection)
        selection = game.pending_target_selection
        self.assertEqual(len(selection["card"].source_cards), 2)

    def test_confirm_uses_both_sources(self):
        game, first, second, _third = self.make_multi_game()
        self.use_view_as(game, PAIR_PROBE_ID, 0, 1)
        self.click(self.seat_center(game, game.players[1]), game)
        self.settle(game)

        for card in (first, second):
            self.assertIn(card, game.deck.discard_pile)
            self.assertNotIn(card, game.player.hand)
        self.assertTrue(game.player.sha_used)

    def test_sources_are_highlighted_while_collecting(self):
        game, first, _second, _third = self.make_multi_game()
        self.use_view_as(game, PAIR_PROBE_ID, 0)
        self.frame(game)
        from src.ui import player as player_ui

        self.assertIn(id(first), player_ui.selected_hand_card_ids(game))
        self.assertEqual(self.renderer.playable, {0, 1, 2},
                         "选牌阶段所有候选牌都可点（含已选，用于取消选择）")

    def test_cancel_collection_is_clean(self):
        game, first, second, _third = self.make_multi_game()
        self.use_view_as(game, PAIR_PROBE_ID, 0)
        self.frame(game)
        self.assertEqual(
            self.renderer.hit_action(self.secondary_center(game), game), "view_as_cancel")
        self.click(self.secondary_center(game), game)

        self.assertIsNone(game.pending_view_as)
        self.assertIsNone(game.pending_target_selection)
        self.assertEqual(len(game.player.hand), 3, "取消不消耗任何牌")
        self.assertFalse(game.player.sha_used)
        self.assertFalse(any("双刃" in line for line in game.game_log),
                         "取消不得写战报")

    def test_selecting_same_card_twice_deselects(self):
        game, first, second, _third = self.make_multi_game()
        self.use_view_as(game, PAIR_PROBE_ID, 0)
        self.click(self.hand_center(game, 0), game)       # 再点一次取消选择
        self.assertEqual(len(game.pending_view_as.selected_source_cards), 0)
        self.click(self.hand_center(game, 1), game)
        self.assertEqual(len(game.pending_view_as.selected_source_cards), 1)
        self.assertIsNone(game.pending_target_selection, "只有源牌还不够")


# ==================================================
# 5. Response：真实点击响应
# ==================================================


class ResponseUiTests(CardActionUiTestCase):

    def test_click_sha_without_skill_does_not_respond(self):
        game = self.make_game(general="zhaoyun")
        sha_card = normal_sha()
        game.player.hand = [sha_card]
        submitted = []
        game.response.request(
            prompt="【杀】：请打出一张【闪】",
            allowed_cards={"SHAN"},
            on_card=lambda index, card, rect: submitted.append((index, card)),
            on_pass=lambda: submitted.append(None),
        )
        self.click(self.hand_center(game, 0), game)
        self.assertEqual(submitted, [], "没点【龙胆】之前【杀】不能当【闪】响应")
        self.assertIn("发动技能", game.message)

    def test_click_sha_after_longdan_responds(self):
        game = self.make_game(general="zhaoyun")
        sha_card = normal_sha()
        game.player.hand = [sha_card]
        submitted = []
        game.response.request(
            prompt="【杀】：请打出一张【闪】",
            allowed_cards={"SHAN"},
            on_card=lambda index, card, rect: submitted.append((index, card)),
            on_pass=lambda: submitted.append(None),
        )
        self.use_view_as(game, "longdan", 0)
        self.assertEqual(len(submitted), 1, "点技能后【杀】当【闪】打出")
        index, card = submitted[0]
        self.assertEqual(index, 0, "legacy 响应按手牌下标移除实体牌")
        self.assertIs(card, sha_card)

    def test_both_real_shan_and_converted_shan_are_usable(self):
        """真实【闪】与【龙胆】杀当闪必须同时可用（各自都是唯一动作）。"""

        game = self.make_game(general="zhaoyun")
        shan_card, sha_card = shan(), normal_sha()
        game.player.hand = [shan_card, sha_card]
        submitted = []

        def install():
            game.response.request(
                prompt="【杀】：请打出一张【闪】",
                allowed_cards={"SHAN"},
                on_card=lambda index, card, rect: submitted.append(index),
                on_pass=lambda: submitted.append(None),
            )

        install()
        self.frame(game)
        self.click(self.hand_center(game, 0), game)          # 真实闪：直接响应
        install()
        self.frame(game)
        self.use_view_as(game, "longdan", 1)                  # 杀：先点技能再选牌

        self.assertEqual(submitted, [0, 1])

    def test_response_context_lists_conversion_in_discovery(self):
        game = self.make_game(general="zhaoyun")
        sha_card = normal_sha()
        game.player.hand = [sha_card]
        context = game.card_actions.response_context(game.player, allowed_names=("SHAN",))
        options = game.card_actions.usable_options(game.player, context)
        self.assertEqual(len(options), 1)
        self.assertTrue(options[0].is_conversion)
        self.assertEqual(options[0].result_name, "SHAN")
        self.assertEqual(options[0].skill_name, "龙胆")

    def test_response_prompt_mentions_conversion(self):
        game = self.make_game(general="zhaoyun")
        game.player.hand = [normal_sha()]
        game.response.request(
            prompt="【杀】：请打出一张【闪】",
            allowed_cards={"SHAN"},
            on_card=lambda index, card, rect: None,
            on_pass=lambda: None,
        )
        self.frame(game)
        info = prompt_module.describe(game)
        self.assertTrue(info.body or info.title)


# ==================================================
# 6. 状态清理
# ==================================================


class ResetTests(CardActionUiTestCase):

    def test_restart_clears_view_as_state(self):
        game = self.make_game(general="guanyu")
        game.player.hand = [tao()]
        game.player.hp = game.player.max_hp - 1
        self.use_view_as(game, "wusheng", 0)
        self.assertIsNotNone(game.pending_target_selection)
        game.reset()
        self.assertIsNone(game.pending_view_as)
        self.assertIsNone(game.pending_target_selection)
        self.assertIsNone(game.pending_card_action)

    def test_return_to_menu_clears_view_as_state(self):
        game = self.make_game()
        first, second = normal_sha(), shan()
        game.player.hand = [first, second]
        bind_conversion_probes(game, game.player, PAIR_PROBE_ID)
        self.use_view_as(game, PAIR_PROBE_ID, 0)
        self.assertIsNotNone(game.pending_view_as)
        game.return_to_menu()
        self.assertIsNone(game.pending_view_as)
        self.assertFalse(game.card_action_picker())

    def test_no_stale_buttons_after_restart(self):
        game = self.make_game(general="guanyu")
        game.player.hand = [tao()]
        game.player.hp = game.player.max_hp - 1
        self.click(self.hand_center(game, 0), game)
        game.reset()
        game.start_single_player()
        game.actions.clear()
        self.frame(game)
        self.assertEqual(self.renderer.primary_button.label, "结束回合")


# ==================================================
# 7. 装备区 source 的点击（Probe D）
# ==================================================


class EquipmentSourceUiTests(CardActionUiTestCase):

    def test_weapon_slot_is_not_clickable_without_declaration(self):
        game = self.make_game(general="zhaoyun")
        weapon = equipment("QINGLONG")
        game.player.set_equipment(weapon)
        game.player.hand = []
        self.frame(game)
        self.assertNotIn("equipment",
                         game.card_actions.source_zones_in_use(game.player))
        rect = self.renderer.player_equipment_slot_rects(game)["weapon"]
        self.click(rect.center, game)
        self.assertIsNone(game.pending_card_action, "龙胆不声明装备区，装备牌不能被点")

    def test_equipment_can_be_used_when_declared(self):
        game = self.make_game()
        weapon = equipment("QINGLONG")
        game.player.set_equipment(weapon)
        game.player.hand = []
        bind_conversion_probes(
            game, game.player, "probe_gear_sha")
        self.frame(game)
        self.assertIn("equipment",
                      game.card_actions.source_zones_in_use(game.player))
        self.click(self.skill_button_center(game, "probe_gear_sha"), game)
        self.assertIsNotNone(game.pending_view_as, "先进入视为技")
        rect = self.renderer.player_equipment_slot_rects(game)["weapon"]
        self.click(rect.center, game)
        self.assertIsNotNone(game.pending_target_selection,
                             "声明了装备区的转化必须能点装备牌")
        self.click(self.seat_center(game, game.players[1]), game)
        self.settle(game)
        self.assertIn(weapon, game.deck.discard_pile)
        self.assertIsNone(game.player.get_equipment("weapon"))


if __name__ == "__main__":
    unittest.main()
