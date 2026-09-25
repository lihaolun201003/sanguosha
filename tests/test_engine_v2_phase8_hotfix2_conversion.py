"""Phase 8 Hotfix 2 tests: 统一 Card Action Discovery / Conversion 管线。

覆盖：六种场合（NORMAL / CONVERSION × PLAY / RESPONSE / RESCUE）、
source candidate 与完整 action、多 source、VirtualCard、provenance、
实体牌移动一次、武圣 / 龙胆、probe 扩展性、去重、ownership 与二次校验。
"""

import os
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame

from src.card_catalog import create_development_deck
from src.game import Game
from src.game.card_actions import ActionKind
from src.game.conversion import (
    EQUIPMENT_ZONE,
    PLAY_CONTEXT,
    RESCUE_CONTEXT,
    RESPONSE_CONTEXT,
    CardConversion,
)
from src.game.engine import PassPendingAction, RespondCardAction, UseCardAction
from src.game.flows import DamageContext, DamageFlow
from src.game.skills.conversion_probes import (
    GEAR_PROBE_ID,
    PAIR_PROBE_ID,
    TAO_PROBE_ID,
    WUXIE_PROBE_ID,
    bind_conversion_probes,
)
from src.card_catalog import create_equipment_cards
from tests.legacy_helpers import canonical_card, equipment, normal_sha, shan, tao


def spade(rank="7"):
    return next(
        card for card in create_development_deck()
        if card.suit == "spade" and card.rank == rank and card.name != "SHANDIAN"
    )


def heart(rank=None):
    for card in create_development_deck():
        if card.suit != "heart":
            continue
        if rank is not None and card.rank != rank:
            continue
        return card
    raise AssertionError("no heart card")


def trick(name):
    return canonical_card(name)


class ConversionTestCase(unittest.TestCase):

    def setUp(self):
        pygame.init()

    def make_game(self, ai_count=3, general=None):
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
        return game

    def play_ctx(self, game, player=None):
        return game.card_actions.play_context(player or game.player)

    def response_ctx(self, game, names, player=None):
        return game.card_actions.response_context(
            player or game.player, allowed_names=tuple(names))

    def rescue_ctx(self, game, names=("TAO",), player=None):
        return game.card_actions.rescue_context(
            player or game.player, allowed_names=tuple(names))

    def settle(self, game, frames=300):
        """把动画队列跑完，让实体牌真正落到最终区域。"""

        for _ in range(frames):
            if not game.busy:
                break
            game.update(1 / 60)
        return not game.busy

    def view_as(self, game, skill_id, *cards, rect=(0, 0, 80, 120)):
        """按 Hotfix 3 的交互使用视为技：先点技能，再选择 source 牌。"""

        self.assertTrue(game.begin_view_as(skill_id),
                        "无法进入视为技：" + skill_id)
        for card in cards:
            self.assertTrue(game.toggle_view_as_source(card, rect),
                            "这张牌不能作为 source")
        return game.pending_target_selection

    def options(self, game, card, context):
        actor = context.actor
        return game.card_actions.actions_for_card(actor, card, context)

    def conversions(self, game, card, context):
        return [item for item in self.options(game, card, context) if item.is_conversion]

    def drain(self, game, human_pass=True, max_steps=400):
        from src.game.engine import SelectCardsAction
        from src.game.engine.pending import PendingRequestType

        for _ in range(max_steps):
            if game.busy:
                game.update(1 / 60)
                continue
            request = game.pending_request
            if request is None:
                return True
            if getattr(request.target, "is_human", False):
                if (
                    human_pass
                    and request.request_type is PendingRequestType.RESPOND_CARD
                ):
                    game.submit_action(PassPendingAction(
                        request.target, request.request_id))
                    continue
                if request.request_type is PendingRequestType.SELECT_CARDS:
                    game.submit_action(SelectCardsAction(
                        request.target, request.request_id,
                        list(request.context.get("candidates", ()))[:max(1, request.min_cards)],
                    ))
                    continue
            game.engine.present_or_auto_resolve(request)
        return False


# ==================================================
# 1. Context / Option 基础
# ==================================================


class ContextTests(ConversionTestCase):

    def test_six_kind_context_labels(self):
        game = self.make_game(general="zhaoyun")
        shan_card = shan()
        game.player.hand = [shan_card]

        play = self.options(game, shan_card, self.play_ctx(game))
        response = self.options(
            game, shan_card, self.response_ctx(game, ("SHA",)))
        self.assertEqual(
            {item.kind_context for item in play if item.is_conversion},
            {"conversion_play"})
        self.assertEqual(
            {item.kind_context for item in response if item.is_conversion},
            {"conversion_response"})

    def test_context_allows_matches_allowed_names(self):
        game = self.make_game(general="zhaoyun")
        context = self.response_ctx(game, ("SHAN",))
        self.assertTrue(context.allows("SHAN"))
        self.assertFalse(context.allows("SHA"))
        play = self.play_ctx(game)
        self.assertTrue(play.allows("SHA"), "PLAY 场合不限制结果牌名")

    def test_action_ids_are_stable_and_address_free(self):
        game = self.make_game(general="zhaoyun")
        shan_card = shan()
        game.player.hand = [shan_card]
        first = self.options(game, shan_card, self.play_ctx(game))
        second = self.options(game, shan_card, self.play_ctx(game))
        self.assertEqual(
            [item.action_id for item in first],
            [item.action_id for item in second])
        for item in first:
            self.assertNotIn("0x", item.action_id)
            self.assertNotIn("object at", item.action_id)
        self.assertTrue(first[0].action_id.startswith("normal:"))
        self.assertIn("convert:longdan:", first[1].action_id)

    def test_option_labels_and_details(self):
        game = self.make_game(general="zhaoyun")
        shan_card = shan()
        game.player.hand = [shan_card]
        option = self.conversions(game, shan_card, self.play_ctx(game))[0]
        self.assertIn("龙胆", option.label)
        self.assertIn("杀", option.label)
        self.assertIn("闪", option.detail)
        self.assertEqual(option.result_name, "SHA")
        self.assertEqual(option.skill_id, "longdan")


# ==================================================
# 2. 武圣（关羽）
# ==================================================


class WushengTests(ConversionTestCase):

    def setUp(self):
        super().setUp()
        self.game = self.make_game(general="guanyu")
        game = self.game
        self.target = game.players[1]
        self.target.hand = []
        game.player.hp = game.player.max_hp

    def assert_converts_to_sha(self, card):
        options = self.conversions(self.game, card, self.play_ctx(self.game))
        self.assertTrue(options, "%s 应该能通过武圣当【杀】" % card.display_name)
        self.assertTrue(options[0].enabled)
        self.assertEqual(options[0].skill_id, "wusheng")

    def test_red_tao_converts(self):
        card = tao()
        self.game.player.hand = [card]
        self.assert_converts_to_sha(card)

    def test_red_shan_converts(self):
        card = shan()
        self.game.player.hand = [card]
        self.assert_converts_to_sha(card)

    def test_red_trick_converts(self):
        card = trick("WUZHONG")
        self.game.player.hand = [card]
        self.assertTrue(getattr(card, "card_color", None), "需要一张红色锦囊")
        self.assert_converts_to_sha(card)

    def test_red_equipment_in_hand_converts(self):
        card = next(
            item for item in create_equipment_cards()
            if item.card_color == "red")
        self.game.player.hand = [card]
        self.assert_converts_to_sha(card)

    def test_black_card_does_not_convert(self):
        card = spade("7")
        self.game.player.hand = [card]
        self.assertEqual(
            self.conversions(self.game, card, self.play_ctx(self.game)), [])

    def test_red_card_converts_in_response(self):
        card = heart("3")
        self.game.player.hand = [card]
        context = self.response_ctx(self.game, ("SHA",))
        options = self.conversions(self.game, card, context)
        self.assertTrue(options and options[0].enabled)

    def test_full_hp_tao_stays_operable(self):
        card = tao()
        self.game.player.hand = [card]
        options = self.options(self.game, card, self.play_ctx(self.game))
        normal = [item for item in options if not item.is_conversion]
        self.assertTrue(normal and not normal[0].enabled, "满血时正常桃不可用")
        self.assertIn("体力", normal[0].disabled_reason)
        self.assertTrue(self.game.card_actions.is_operable(
            self.game.player, card, self.play_ctx(self.game)))


# ==================================================
# 3. 龙胆（赵云）
# ==================================================


class LongdanTests(ConversionTestCase):

    def setUp(self):
        super().setUp()
        self.game = self.make_game(general="zhaoyun")
        self.target = self.game.players[1]
        self.target.hand = []

    def test_shan_to_sha_in_play(self):
        card = shan()
        self.game.player.hand = [card]
        option = self.conversions(self.game, card, self.play_ctx(self.game))[0]
        self.assertEqual(option.result_name, "SHA")
        self.assertTrue(option.enabled)
        # 但普通点击不会自动转换：必须先进视为技
        self.assertEqual(
            self.game.begin_card_action(card, (0, 0, 80, 120)), "disabled")
        self.assertIsNone(self.game.pending_target_selection)
        self.assertIn("发动技能", self.game.message)

    def test_sha_highlights_as_shan_response(self):
        card = normal_sha()
        self.game.player.hand = [card]
        context = self.response_ctx(self.game, ("SHAN",))
        options = self.conversions(self.game, card, context)
        self.assertTrue(options, "手里只有杀时，龙胆必须让它成为合法响应")
        self.assertTrue(options[0].enabled)
        self.assertEqual(options[0].result_name, "SHAN")

    def test_shan_is_operable_in_play_phase(self):
        card = shan()
        self.game.player.hand = [card]
        self.assertTrue(self.game.card_actions.is_operable(
            self.game.player, card, self.play_ctx(self.game)),
            "能通过龙胆当【杀】使用的【闪】不能被灰")

    def test_converted_sha_uses_sha_rules(self):
        card = shan()
        self.game.player.hand = [card]
        self.assertIsNotNone(self.view_as(self.game, "longdan", card))
        self.game.toggle_target_selection(self.target)
        self.assertTrue(self.game.player.sha_used, "转换出来的杀同样计入杀次数")
        self.drain(self.game)
        self.assertLess(self.target.hp, self.target.max_hp)

    def test_sha_quota_blocks_converted_sha(self):
        card = shan()
        self.game.player.hand = [card]
        self.game.player.sha_used = True
        option = self.conversions(self.game, card, self.play_ctx(self.game))[0]
        self.assertFalse(option.enabled)
        self.assertIn("杀", option.disabled_reason)

    def test_distance_blocks_converted_sha(self):
        card = shan()
        self.game.player.hand = [card]
        far = self.game.players[2]
        far.set_equipment(equipment("JUEYING"))   # +1 防御马：距离 2 > 攻击范围 1
        for player in self.game.players[1:]:
            if player is not far:
                player.alive = False
                player.hp = 0
        option = self.conversions(self.game, card, self.play_ctx(self.game))[0]
        self.assertFalse(option.enabled, "距离不足时转换的杀同样不可用")
        self.assertTrue(option.disabled_reason)


# ==================================================
# 4. 实体牌移动 / provenance
# ==================================================


class MovementProvenanceTests(ConversionTestCase):

    def test_converted_play_moves_physical_card_once(self):
        game = self.make_game(general="zhaoyun")
        card = shan()
        game.player.hand = [card]
        self.view_as(game, "longdan", card)
        game.toggle_target_selection(game.players[1])
        self.settle(game)

        positions = [
            card in game.player.hand,
            card in game.processing_zone,
            card in game.deck.discard_pile,
        ]
        self.assertEqual(sum(bool(item) for item in positions), 1,
                         "实体牌只能出现在一个区域")
        self.assertIn(card, game.deck.discard_pile)

    def test_virtual_card_carries_sources(self):
        game = self.make_game(general="zhaoyun")
        card = shan()
        game.player.hand = [card]
        option = self.conversions(game, card, self.play_ctx(game))[0]
        virtual = game.card_actions.effective_card(option)
        self.assertTrue(virtual.is_virtual)
        self.assertEqual(virtual.name, "SHA")
        self.assertEqual(len(virtual.source_cards), 1)
        self.assertIs(virtual.source_cards[0], card)
        self.assertEqual(virtual.skill_id, "longdan")
        self.assertEqual(virtual.nature, "normal")
        self.assertEqual(virtual.suit, card.suit, "花色/点数继承自实体牌")

    def test_metadata_records_provenance(self):
        game = self.make_game(general="guanyu")
        card = tao()
        game.player.hand = [card]
        self.view_as(game, "wusheng", card)
        selection = game.pending_target_selection
        action = selection["metadata"].get("card_action")
        self.assertIsNotNone(action, "目标选择必须带上来源 Action")
        self.assertEqual(action.skill_id, "wusheng")
        self.assertTrue(action.is_conversion)
        self.assertIn("wusheng", selection["metadata"]["conversion"]["skill_id"])

    def test_conversion_log_names_skill_and_source(self):
        game = self.make_game(general="guanyu")
        card = tao()
        game.player.hand = [card]
        self.view_as(game, "wusheng", card)
        game.toggle_target_selection(game.players[1])
        logs = " ".join(game.game_log)
        self.assertIn("武圣", logs)
        self.assertIn("桃", logs)
        self.assertIn("杀", logs)

    def test_skill_triggered_emitted_only_on_commit(self):
        game = self.make_game(general="zhaoyun")
        card = shan()
        game.player.hand = [card]
        fired = []
        from src.game.engine import EventType

        game.context.events.subscribe(
            EventType.SKILL_TRIGGERED,
            lambda context, event: fired.append(event.payload.get("skill_id")))

        game.begin_view_as("longdan")
        self.assertEqual(fired, [], "进入视为技不应触发技能浮字")
        game.toggle_view_as_source(card, (0, 0, 80, 120))
        self.assertEqual(fired, [], "只选好 source 也还没有真正发动")
        game.toggle_target_selection(game.players[1])
        self.assertIn("longdan", fired, "确认目标后才是真正的发动")

    def test_multi_source_moves_both_cards(self):
        game = self.make_game()
        first, second = normal_sha(), shan()
        game.player.hand = [first, second, tao()]
        bind_conversion_probes(game, game.player, PAIR_PROBE_ID)

        game.begin_view_as(PAIR_PROBE_ID)
        game.toggle_view_as_source(first, (0, 0, 80, 120))
        self.assertIsNotNone(game.pending_view_as, "还差一张，必须留在选牌阶段")
        game.toggle_view_as_source(second, (0, 0, 80, 120))
        self.assertIsNotNone(game.pending_target_selection)
        game.toggle_target_selection(game.players[1])

        self.settle(game)
        for card in (first, second):
            self.assertIn(card, game.deck.discard_pile)
            self.assertNotIn(card, game.player.hand)
            self.assertNotIn(card, game.processing_zone)

    def test_multi_source_virtual_card_has_two_sources(self):
        game = self.make_game()
        first, second = normal_sha(), shan()
        game.player.hand = [first, second]
        bind_conversion_probes(game, game.player, PAIR_PROBE_ID)
        context = self.play_ctx(game)
        options = game.card_actions.actions_for_sources(
            game.player, (first, second), context)
        conversion = [item for item in options if item.is_conversion][0]
        virtual = game.card_actions.effective_card(conversion)
        self.assertEqual(len(virtual.source_cards), 2)
        self.assertIs(virtual.source_cards[0], first)
        self.assertIs(virtual.source_cards[1], second)


# ==================================================
# 5. Normal + Conversion 并存 / 去重
# ==================================================


class CoexistenceTests(ConversionTestCase):

    def test_normal_and_conversion_both_offered(self):
        game = self.make_game(general="guanyu")
        game.player.hp = game.player.max_hp - 1
        card = tao()
        game.player.hand = [card]
        options = self.options(game, card, self.play_ctx(game))
        self.assertEqual(len(options), 2, "桃：正常使用 + 武圣当杀")
        kinds = {item.kind for item in options}
        self.assertEqual(kinds, {ActionKind.NORMAL, ActionKind.CONVERSION})

    def test_single_source_goes_straight_to_targeting(self):
        game = self.make_game(general="zhaoyun")
        card = shan()
        game.player.hand = [card]
        game.begin_view_as("longdan")
        game.toggle_view_as_source(card, (0, 0, 80, 120))
        self.assertFalse(game.card_action_picker(), "不必再弹一次面板")
        self.assertIsNotNone(game.pending_target_selection)

    def test_same_result_conversion_is_deduped(self):
        game = self.make_game(general="guanyu")
        card = next(
            item for item in create_development_deck()
            if item.name == "SHA" and item.card_color == "red")
        game.player.hand = [card]
        options = self.options(game, card, self.play_ctx(game))
        self.assertEqual(len(options), 1, "红色【杀】的正常使用与武圣同结果，只留正常")
        self.assertFalse(options[0].is_conversion)

    def test_keep_with_normal_flag_retains_both(self):
        game = self.make_game()
        card = normal_sha()
        game.player.hand = [card]

        from src.game.skills.definitions import SkillDef, SkillKind

        definition = SkillDef(
            id="probe_keep_normal",
            name="并存",
            description="同结果也保留转化项。",
            kind=SkillKind.PASSIVE,
            conversions=(
                CardConversion(
                    skill_id="probe_keep_normal",
                    matches=lambda item: True,
                    name="SHA",
                    contexts=(PLAY_CONTEXT,),
                    keep_with_normal=True,
                ),
            ),
        )
        game.skill_registry.register(definition)
        game.skills.bind(game.player, definition.id, definition=definition)

        options = self.options(game, card, self.play_ctx(game))
        self.assertEqual(len(options), 2)
        self.assertTrue(any(item.is_conversion for item in options))

    def test_no_options_gives_disabled_reason(self):
        game = self.make_game(general="guanyu")
        game.player.hp = game.player.max_hp
        card = tao()
        game.player.hand = [card]
        for player in game.players[1:]:
            player.alive = False
            player.hp = 0
        self.assertEqual(game.begin_card_action(card, (0, 0, 80, 120)), "disabled",
                         "满血且没有合法目标时这张桃整体不可用")
        self.assertTrue(game.message)
        self.assertFalse(game.card_actions.is_operable(
            game.player, card, self.play_ctx(game)))


# ==================================================
# 6. Response / Rescue Context
# ==================================================


class ResponseRescueTests(ConversionTestCase):

    def test_probe_wuxie_only_in_response(self):
        game = self.make_game()
        card = spade("3")
        game.player.hand = [card]
        bind_conversion_probes(game, game.player, WUXIE_PROBE_ID)

        self.assertEqual(
            self.conversions(game, card, self.play_ctx(game)), [],
            "Probe A 只在 RESPONSE 场合声明")
        response = self.conversions(
            game, card, self.response_ctx(game, ("WUXIE",)))
        self.assertTrue(response and response[0].enabled)

    def test_probe_tao_only_in_rescue(self):
        game = self.make_game()
        card = heart("4")
        game.player.hand = [card]
        bind_conversion_probes(game, game.player, TAO_PROBE_ID)

        self.assertEqual(
            self.conversions(game, card, self.play_ctx(game)), [],
            "Probe B 只在 RESCUE 场合声明")
        rescue = self.conversions(game, card, self.rescue_ctx(game, ("TAO",)))
        self.assertTrue(rescue and rescue[0].enabled)

    def test_rescue_context_accepts_wine_for_self(self):
        game = self.make_game()
        card = tao()
        game.player.hand = [card]
        context = self.rescue_ctx(game, ("TAO", "JIU"))
        self.assertTrue(context.is_rescue)
        self.assertTrue(context.allows("JIU"))

    def test_dying_flow_finds_conversion_rescuer(self):
        game = self.make_game(general="zhaoyun")
        card = heart("5")
        game.player.hand = [card]
        bind_conversion_probes(game, game.player, TAO_PROBE_ID)
        game.player.hp = 0

        from src.game.flows import DyingFlow

        flow = DyingFlow(game.engine, dying_player=game.player)
        flow.start()
        request = game.pending_request
        self.assertIsNotNone(request, "红色牌在手时救援请求必须建立")
        self.assertIn("TAO", request.allowed_cards)
        game.submit_action(PassPendingAction(request.target, request.request_id))
        self.drain(game)

    def test_dying_flow_skips_player_without_any_rescue_option(self):
        game = self.make_game()
        game.player.hand = [spade("9")]
        game.player.hp = 0

        from src.game.flows import DyingFlow

        flow = DyingFlow(game.engine, dying_player=game.player)
        flow.start()
        self.assertIsNone(game.pending_request, "黑色牌无法救人，不该建立真人请求")

    def test_wuxie_chain_auto_passes_without_legal_action(self):
        game = self.make_game()
        game.player.hand = [spade("9")]

        from src.game.engine.pending import PendingRequestType

        request = game.engine.pending.create(
            PendingRequestType.RESPOND_CARD,
            source=game.players[1],
            target=game.player,
            prompt="使用【无懈可击】？",
            owner_flow=None,
            allowed_cards={"WUXIE"},
            min_cards=0,
            max_cards=1,
            request_context={"reason": "wuxie_chain"},
        )
        # owner_flow 为 None 时 present 会走自动放弃分支：这里只验证判定本身
        context = game.card_actions.response_context(
            game.player, request=request, allowed_names=request.allowed_cards)
        self.assertEqual(game.card_actions.usable_options(game.player, context), [])


# ==================================================
# 7. Ownership / 二次校验
# ==================================================


class ValidationTests(ConversionTestCase):

    def test_source_leaving_hand_invalidates(self):
        game = self.make_game(general="zhaoyun")
        card = shan()
        game.player.hand = [card]
        option = self.conversions(game, card, self.play_ctx(game))[0]
        game.player.hand = []
        ok, reason = game.card_actions.validate(option, sources=(card,))
        self.assertFalse(ok)
        self.assertTrue(reason)

    def test_foreign_card_is_not_a_source(self):
        game = self.make_game(general="guanyu")
        other = game.players[1]
        card = shan()
        other.hand = [card]
        game.player.hand = []
        self.assertIsNone(game.card_actions.zone_of(game.player, card),
                          "别人的牌不在自己的任何区域")
        option = self.conversions(game, card, self.play_ctx(game))
        if option:
            ok, reason = game.card_actions.validate(option[0], sources=(card,))
            self.assertFalse(ok, "不属于该玩家的牌必须被二次校验拒绝")
            self.assertTrue(reason)

    def test_zone_of_reports_equipment(self):
        game = self.make_game()
        card = equipment("QINGLONG")
        game.player.set_equipment(card)
        self.assertEqual(
            game.card_actions.zone_of(game.player, card), EQUIPMENT_ZONE)

    def test_duplicate_source_rejected(self):
        game = self.make_game()
        card = normal_sha()
        game.player.hand = [card]
        bind_conversion_probes(game, game.player, PAIR_PROBE_ID)
        options = [
            item for item in game.card_actions.actions_for_sources(
                game.player, (card, card), self.play_ctx(game))
            if item.is_conversion
        ]
        for option in options:
            unique = {id(item) for item in option.source_cards}
            self.assertEqual(len(unique), len(option.source_cards),
                             "同一张实体牌不能当两个 source")
        self.assertFalse(
            [item for item in options if item.complete],
            "手上只有一张牌时双刃不成立")

    def test_validate_rejects_when_quota_used(self):
        game = self.make_game(general="zhaoyun")
        card = shan()
        game.player.hand = [card]
        option = self.conversions(game, card, self.play_ctx(game))[0]
        game.player.sha_used = True
        ok, reason = game.card_actions.validate(option, sources=(card,))
        self.assertFalse(ok)
        self.assertIn("杀", reason)

    def test_confirm_after_state_change_is_rejected(self):
        game = self.make_game()
        first, second = normal_sha(), shan()
        game.player.hand = [first, second]
        bind_conversion_probes(game, game.player, PAIR_PROBE_ID)
        game.begin_view_as(PAIR_PROBE_ID)
        game.toggle_view_as_source(first, (0, 0, 80, 120))
        game.toggle_view_as_source(second, (0, 0, 80, 120))
        # 收齐后已经自动进入目标选择；此时把手牌拿走再确认
        self.assertIsNotNone(game.pending_target_selection)
        game.player.hand = []
        self.assertFalse(game.confirm_target_selection(),
                         "source 已经离手，确认必须被拒绝")
        self.assertFalse(game.player.sha_used)


# ==================================================
# 8. 装备区 source（Probe D）
# ==================================================


class EquipmentSourceTests(ConversionTestCase):

    def test_equipment_source_declared(self):
        game = self.make_game()
        weapon = equipment("QINGLONG")
        game.player.set_equipment(weapon)
        bind_conversion_probes(game, game.player, GEAR_PROBE_ID)

        context = self.play_ctx(game)
        self.assertIn(EQUIPMENT_ZONE,
                      game.card_actions.source_zones_in_use(game.player))
        options = self.conversions(game, weapon, context)
        self.assertTrue(options, "声明了装备区的转换应能发现装备牌")
        self.assertTrue(options[0].enabled)

    def test_equipment_source_moves_card(self):
        game = self.make_game()
        weapon = equipment("QINGLONG")
        game.player.set_equipment(weapon)
        bind_conversion_probes(game, game.player, GEAR_PROBE_ID)
        game.players[1].hand = []

        game.begin_view_as(GEAR_PROBE_ID)
        game.toggle_view_as_source(weapon, (0, 0, 80, 120))
        self.assertIsNotNone(game.pending_target_selection)
        game.toggle_target_selection(game.players[1])
        self.settle(game)
        self.assertIn(weapon, game.deck.discard_pile)
        self.assertIsNone(game.player.get_equipment("weapon"))

    def test_hand_only_conversion_rejects_equipment(self):
        game = self.make_game(general="zhaoyun")
        weapon = equipment("QINGLONG")
        weapon.card_color = "red"
        game.player.set_equipment(weapon)
        self.assertNotIn(EQUIPMENT_ZONE,
                         game.card_actions.source_zones_in_use(game.player))
        self.assertEqual(
            self.conversions(game, weapon, self.play_ctx(game)), [],
            "龙胆只声明手牌，装备区不能被当成 source")


# ==================================================
# 9. 场合过滤 / 便捷查询
# ==================================================


class DiscoveryQueryTests(ConversionTestCase):

    def test_usable_options_respects_requirement(self):
        game = self.make_game(general="zhaoyun")
        sha_card, shan_card = normal_sha(), shan()
        game.player.hand = [sha_card, shan_card]
        context = self.response_ctx(game, ("SHAN",))
        usable = game.card_actions.usable_options(game.player, context)
        names = [(item.kind, item.result_name) for item in usable]
        self.assertIn((ActionKind.NORMAL, "SHAN"), names)
        self.assertIn((ActionKind.CONVERSION, "SHAN"), names)
        self.assertNotIn((ActionKind.NORMAL, "SHA"), names)

    def test_actions_in_covers_whole_hand(self):
        game = self.make_game(general="zhaoyun")
        game.player.hand = [normal_sha(), shan(), tao()]
        options = game.card_actions.actions_in(game.player, self.play_ctx(game))
        results = {(item.kind, item.result_name) for item in options}
        self.assertIn((ActionKind.CONVERSION, "SHA"), results)

    def test_legal_targets_uses_effective_card(self):
        game = self.make_game(general="zhaoyun")
        card = shan()
        game.player.hand = [card]
        option = self.conversions(game, card, self.play_ctx(game))[0]
        targets = game.card_actions.legal_targets(game.player, option)
        self.assertTrue(targets)
        self.assertIn(game.players[1], targets)

    def test_source_zones_default_to_hand(self):
        game = self.make_game(general="guanyu")
        zones = game.card_actions.source_zones_in_use(game.player)
        self.assertEqual(zones, {"hand"}, "正式武将当前只声明手牌")

    def test_probes_are_not_registered_by_default(self):
        game = self.make_game()
        for probe_id in (WUXIE_PROBE_ID, TAO_PROBE_ID, PAIR_PROBE_ID, GEAR_PROBE_ID):
            self.assertIsNone(game.skill_registry.get(probe_id))
        self.assertGreaterEqual(len(game.skill_registry), 11)


# ==================================================
# 10. AI 共用 Discovery
# ==================================================


class AiDiscoveryTests(ConversionTestCase):

    def test_ai_response_uses_conversion(self):
        game = self.make_game()
        zhaoyun = game.players[1]
        game.set_general(zhaoyun, "zhaoyun")
        sha_card = normal_sha()
        zhaoyun.hand = [sha_card]

        from src.game.engine.pending import PendingRequestType

        request = game.engine.pending.create(
            PendingRequestType.RESPOND_CARD,
            source=game.player,
            target=zhaoyun,
            prompt="请打出一张【闪】",
            owner_flow=None,
            allowed_cards={"SHAN"},
            min_cards=0,
            max_cards=1,
            request_context={"reason": "sha_response"},
        )
        card = game.get_controller(zhaoyun).converted_response(request)
        self.assertIsNotNone(card, "AI 必须能用龙胆凑出【闪】")
        self.assertTrue(getattr(card, "_virtual", False))
        self.assertEqual(card.name, "SHAN")
        self.assertIs(card.source_cards[0], sha_card)

    def test_ai_play_conversion_uses_discovery(self):
        game = self.make_game()
        guanyu = game.players[1]
        game.set_general(guanyu, "guanyu")
        card = tao()
        guanyu.hand = [card]
        game.current_turn_player = guanyu
        controller = game.get_controller(guanyu)
        virtual = controller.converted_play_card("SHA")
        self.assertIsNotNone(virtual)
        self.assertEqual(virtual.name, "SHA")
        self.assertIs(virtual.source_cards[0], card)

    def test_ai_never_reads_general_id_in_discovery(self):
        game = self.make_game(general="zhaoyun")
        zhaoyun = game.players[1]
        game.set_general(zhaoyun, "zhaoyun")
        shan_card = shan()
        zhaoyun.hand = [shan_card]
        game.current_turn_player = zhaoyun
        options = game.card_actions.actions_in(
            zhaoyun, game.card_actions.play_context(zhaoyun))
        self.assertTrue(any(item.skill_id == "longdan" for item in options))


# ==================================================
# 11. 属性牌与核心扫描
# ==================================================


class ElementalAndScanTests(ConversionTestCase):

    def test_conversion_can_produce_elemental_sha(self):
        """结果牌不只存名字：属性（火杀 / 雷杀）同样能表达。"""

        game = self.make_game()
        card = normal_sha()
        game.player.hand = [card]

        from src.game.skills.definitions import SkillDef, SkillKind

        definition = SkillDef(
            id="probe_fire_sha",
            name="烈焰",
            description="把【杀】当火【杀】使用。",
            kind=SkillKind.PASSIVE,
            conversions=(
                CardConversion(
                    skill_id="probe_fire_sha",
                    matches=lambda item: True,
                    name="SHA",
                    nature="fire",
                    contexts=(PLAY_CONTEXT,),
                ),
            ),
        )
        game.skill_registry.register(definition)
        game.skills.bind(game.player, definition.id, definition=definition)

        option = [
            item for item in game.card_actions.actions_for_card(
                game.player, card, self.play_ctx(game))
            if item.is_conversion
        ][0]
        virtual = game.card_actions.effective_card(option)
        self.assertEqual(virtual.name, "SHA")
        self.assertEqual(virtual.nature, "fire")
        self.assertEqual(virtual.display_name, "火杀")
        self.assertEqual(option.result_nature, "fire")

    def test_core_has_no_general_specific_branches(self):
        """第六十九条：通用交互层不允许出现具体武将 / 技能的条件分支。"""

        import pathlib

        root = pathlib.Path(__file__).resolve().parents[1]
        watched = [
            root / "src" / "renderer.py",
            root / "src" / "response.py",
        ]
        watched += sorted((root / "src" / "ui").glob("*.py"))
        watched += sorted((root / "src" / "game" / "controllers").glob("*.py"))
        watched += sorted((root / "src" / "game" / "card_effects").glob("*.py"))
        watched += sorted((root / "src" / "game" / "engine").glob("*.py"))

        forbidden = (
            'general_id ==', 'skill_id == "longdan"', 'skill_id == "wusheng"',
            "skill_id == 'longdan'", "skill_id == 'wusheng'",
            '== "zhaoyun"', '== "guanyu"',
        )
        offenders = []
        for path in watched:
            text = path.read_text(encoding="utf-8")
            for token in forbidden:
                if token in text:
                    offenders.append("%s: %s" % (path.name, token))
        self.assertEqual(offenders, [], "通用层不允许按具体武将分支")

    def test_skill_modules_own_their_conversion_rules(self):
        """允许且鼓励：技能模块自己声明转换规则。"""

        game = self.make_game(general="zhaoyun")
        card = shan()
        game.player.hand = [card]
        option = self.conversions(game, card, self.play_ctx(game))[0]
        conversion = game.card_actions.conversion_of(option)
        self.assertIsNotNone(conversion)
        self.assertEqual(conversion.skill_id, "longdan")


if __name__ == "__main__":
    unittest.main()
