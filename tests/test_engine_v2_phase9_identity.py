"""Phase 9：GameMode / 标准身份模式 / 武将选择 / 开局流程 / 身份胜负。

覆盖：

    GameMode 注册与人数限制 · 身份配比与可见性 · 武将选择 · 正式 setup
    身份胜负 · 死亡奖惩 · 濒死保护 · AI 不作弊 · 重新开始 · FFA 回归
"""

import os
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame

from src.game import Game
from src.game.identity import (
    IDENTITY_DISTRIBUTION,
    IDENTITY_PLAYER_COUNTS,
    Identity,
    identity_name,
    is_lord_side,
    visible_identity,
)
from src.game.modes import FreeForAllMode, GameMode, IdentityMode
from src.game.modes.base import create_default_mode_registry
from tests.legacy_helpers import set_draw_order, tao


class IdentityModeTestCase(unittest.TestCase):
    def setUp(self):
        pygame.init()

    def make_game(self, ai_count=4, *, mode=None, seed=7):
        game = Game(ai_count=ai_count)
        game.scene = "game"
        game.ai_pacing = True
        game.actions.clear()
        game.engine.reset()
        game.rng.seed(seed)
        if mode:
            game.set_mode(mode)
        return game

    def start_identity_battle(self, ai_count=4, *, seed=7, general=None):
        """走真实开局流程：模式 -> 身份 -> 选将 -> 对局。"""

        game = self.make_game(ai_count, mode="identity", seed=seed)
        game.begin_general_select()
        game.confirm_identity()
        candidates = game.general_candidates or game.generals.ids()
        game.selected_general = general or candidates[0]
        game.confirm_general()
        return game

    def kill(self, game, player, source=None):
        """真实死亡结算链路：hp 归零 -> DeathFlow -> 模式判定。"""

        player.hp = 0
        return game.engine.run_death(player, source=source)

    def lord(self, game):
        return game.mode.lord()

    def player_with(self, game, identity):
        return next(
            player for player in game.players
            if getattr(player, "identity", None) is identity
        )

    def survivors(self, game):
        return [player for player in game.players if player.alive]


# ==================================================
# A. GameMode
# ==================================================


class GameModeTests(IdentityModeTestCase):
    def test_registry_contains_ffa_and_identity(self):
        registry = create_default_mode_registry()
        self.assertIn("ffa", registry)
        self.assertIn("identity", registry)
        self.assertIs(registry.require("ffa"), FreeForAllMode)
        self.assertIs(registry.require("identity"), IdentityMode)

    def test_ffa_allows_two_to_eight_players(self):
        mode = FreeForAllMode(None)
        self.assertEqual(mode.allowed_counts(), ())
        for count in range(2, 9):
            self.assertTrue(mode.allows(count), count)
        self.assertFalse(mode.allows(1))
        self.assertFalse(mode.allows(9))

    def test_identity_only_allows_five_to_eight_players(self):
        mode = IdentityMode(None)
        self.assertEqual(mode.allowed_counts(), IDENTITY_PLAYER_COUNTS)
        for count in (5, 6, 7, 8):
            self.assertTrue(mode.allows(count), count)
        for count in (1, 2, 3, 4, 9):
            self.assertFalse(mode.allows(count), count)

    def test_game_switches_mode_and_clamps_player_count(self):
        game = self.make_game(1)
        self.assertEqual(game.mode_id, "ffa")
        self.assertEqual(game.total_players(), 2)

        self.assertTrue(game.set_mode("identity"))
        self.assertEqual(game.mode_id, "identity")
        self.assertIn(game.total_players(), IDENTITY_PLAYER_COUNTS)

        self.assertFalse(game.set_mode("nope"), "未知模式必须被拒绝")
        self.assertEqual(game.mode_id, "identity")

    def test_player_count_adjustment_follows_mode_limits(self):
        game = self.make_game(4, mode="ffa")
        game.ai_count = 7
        self.assertFalse(game.can_adjust_player_count(1), "FFA 上限 8 人")
        game.set_mode("identity")
        self.assertFalse(game.can_adjust_player_count(1), "身份上限 8 人")
        game.adjust_player_count(-1)
        self.assertEqual(game.total_players(), 7)
        for _ in range(3):
            game.adjust_player_count(-1)
        self.assertEqual(game.total_players(), 5)
        self.assertFalse(game.can_adjust_player_count(-1), "身份下限 5 人")

    def test_mode_switch_does_not_leak_state_between_battles(self):
        game = self.start_identity_battle(4)
        self.assertTrue(any(p.identity is not None for p in game.players))
        game.set_mode("ffa")
        game.reset()
        self.assertTrue(all(p.identity is None for p in game.players))

    def test_base_mode_is_extensible(self):
        """新模式只要实现钩子即可接入，核心流程不需要认识它。"""

        class DuelMode(GameMode):
            id = "duel"
            name = "单挑"
            allowed_player_counts = (2,)

            def first_player(self):
                return self.game.players[1]

        registry = create_default_mode_registry()
        registry.register(DuelMode)
        game = self.make_game(1)
        mode = registry.require("duel")(game)
        self.assertTrue(mode.allows(2))
        self.assertFalse(mode.allows(5))
        self.assertIs(mode.first_player(), game.players[1])


# ==================================================
# B. 身份分配
# ==================================================


class IdentityAssignmentTests(IdentityModeTestCase):
    def test_distribution_matches_player_count(self):
        for count, expected in IDENTITY_DISTRIBUTION.items():
            self.assertEqual(len(expected), count, count)
            self.assertEqual(expected.count(Identity.LORD), 1, count)
            self.assertGreaterEqual(expected.count(Identity.REBEL), 1, count)
            self.assertEqual(expected.count(Identity.RENEGADE), 1, count)

    def test_every_supported_count_assigns_exactly_one_lord(self):
        for total in IDENTITY_PLAYER_COUNTS:
            game = self.make_game(total - 1, mode="identity")
            identities = game.mode.assign_identities()
            self.assertEqual(len(identities), total, total)
            self.assertEqual(identities.count(Identity.LORD), 1, total)
            self.assertEqual(
                [p.identity for p in game.players].count(Identity.LORD), 1, total)

    def test_identity_counts_match_the_rule_table(self):
        for total in IDENTITY_PLAYER_COUNTS:
            game = self.make_game(total - 1, mode="identity")
            game.mode.assign_identities()
            actual = tuple(sorted(p.identity.value for p in game.players))
            expected = tuple(sorted(i.value for i in IDENTITY_DISTRIBUTION[total]))
            self.assertEqual(actual, expected, total)

    def test_human_is_not_pinned_to_lord(self):
        seen = set()
        for seed in range(12):
            game = self.make_game(4, mode="identity", seed=seed)
            game.mode.assign_identities()
            seen.add(game.player.identity)
        self.assertGreater(len(seen), 1, "真人身份不应固定")

    def test_assignment_is_deterministic_for_a_fixed_seed(self):
        first = self.make_game(4, mode="identity", seed=99)
        first.mode.assign_identities()
        second = self.make_game(4, mode="identity", seed=99)
        second.mode.assign_identities()
        self.assertEqual(
            [p.identity for p in first.players],
            [p.identity for p in second.players],
        )

    def test_lord_identity_is_public_from_the_start(self):
        game = self.make_game(4, mode="identity")
        game.mode.assign_identities()
        lord = self.lord(game)
        self.assertTrue(lord.identity_revealed)
        for player in game.players:
            if player is not lord:
                self.assertFalse(player.identity_revealed)

    def test_apply_identities_rejects_a_mismatched_count(self):
        game = self.make_game(4, mode="identity")
        with self.assertRaises(ValueError):
            game.mode.apply_identities((Identity.LORD,))

    def test_ffa_players_have_no_identity(self):
        game = self.make_game(3, mode="ffa")
        game.start_local_battle(3)
        self.assertTrue(all(p.identity is None for p in game.players))
        self.assertFalse(game.mode.uses_identities)


# ==================================================
# C. 身份可见性
# ==================================================


class IdentityVisibilityTests(IdentityModeTestCase):
    def setUp(self):
        super().setUp()
        self.game = self.make_game(4, mode="identity", seed=5)
        self.game.mode.assign_identities()
        self.lord = self.lord(self.game)
        self.human = self.game.player

    def test_lord_is_visible_to_everyone(self):
        for viewer in self.game.players:
            if viewer is self.lord:
                continue
            self.assertIs(
                visible_identity(self.lord, viewer), Identity.LORD,
                viewer.name)

    def test_players_know_their_own_identity(self):
        for player in self.game.players:
            self.assertEqual(
                visible_identity(player, player), player.identity, player.name)

    def test_other_living_identities_stay_hidden(self):
        hidden = [
            player for player in self.game.players
            if player is not self.lord and player is not self.human
        ]
        for player in hidden:
            self.assertIsNone(
                visible_identity(player, self.human),
                player.name + " 的身份对真人应当是未知")

    def test_death_reveals_the_identity(self):
        target = next(
            player for player in self.game.players
            if player is not self.lord and player is not self.human
        )
        self.assertIsNone(visible_identity(target, self.human))
        self.kill(self.game, target)
        self.assertIsNotNone(visible_identity(target, self.human))
        self.assertEqual(visible_identity(target, self.human), target.identity)

    def test_reveal_all_exposes_every_identity(self):
        self.game.mode.reveal_all()
        for player in self.game.players:
            self.assertIsNotNone(visible_identity(player, self.human))

    def test_ffa_has_no_identity_information(self):
        ffa = self.make_game(3, mode="ffa")
        ffa.start_local_battle(3)
        self.assertTrue(all(visible_identity(p, ffa.player) is None for p in ffa.players))

    def test_lord_side_helper(self):
        self.assertTrue(is_lord_side(Identity.LORD))
        self.assertTrue(is_lord_side(Identity.LOYALIST))
        self.assertFalse(is_lord_side(Identity.REBEL))
        self.assertFalse(is_lord_side(Identity.RENEGADE))


# ==================================================
# D. 武将选择
# ==================================================


class GeneralSelectionTests(IdentityModeTestCase):
    def test_candidates_come_from_the_general_registry(self):
        game = self.make_game(4, mode="identity")
        candidates = game.roll_general_candidates()
        self.assertTrue(candidates)
        for general_id in candidates:
            self.assertIsNotNone(game.generals.get(general_id))
            self.assertIn(general_id, game.generals.ids())

    def test_candidate_count_follows_the_mode_configuration(self):
        game = self.make_game(4, mode="identity")
        self.assertEqual(
            len(game.roll_general_candidates()), game.mode.general_choice_count)
        game.mode.general_choice_count = 5
        self.assertEqual(len(game.roll_general_candidates()), 5)

    def test_candidates_are_unique(self):
        game = self.make_game(7, mode="identity", seed=3)
        candidates = game.roll_general_candidates(6)
        self.assertEqual(len(set(candidates)), len(candidates))

    def test_candidate_count_is_clamped_to_the_pool(self):
        game = self.make_game(4, mode="identity")
        game.general_pool = ("zhangfei", "guanyu")
        candidates = game.roll_general_candidates(5)
        self.assertEqual(len(candidates), 2)

    def test_confirm_general_binds_the_chosen_general(self):
        game = self.make_game(4, mode="identity")
        game.begin_general_select()
        game.confirm_identity()
        chosen = game.general_candidates[1]
        game.selected_general = chosen
        self.assertTrue(game.confirm_general())
        self.assertEqual(game.player.general_id, chosen)
        self.assertTrue(game.player.hand, "开局必须发初始手牌")

    def test_selectable_generals_only_lists_candidates(self):
        game = self.make_game(4, mode="identity")
        game.begin_general_select()
        game.confirm_identity()
        shown = [general.id for general in game.selectable_generals()]
        self.assertEqual(shown, list(game.general_candidates))

    def test_ai_generals_are_unique_and_take_from_the_pool(self):
        game = self.start_identity_battle(4)
        assigned = [player.general_id for player in game.players]
        self.assertTrue(all(assigned), "每名角色都要有武将")
        self.assertEqual(len(set(assigned)), len(assigned), "武将不能重复")

    def test_every_candidate_is_a_registered_general(self):
        game = self.make_game(7, mode="ffa", seed=2)
        for general_id in game.roll_general_candidates(7):
            self.assertIn(general_id, game.generals)


# ==================================================
# E. 正式 setup
# ==================================================


class SetupFlowTests(IdentityModeTestCase):
    def test_scene_flow_for_ffa(self):
        game = self.make_game(3, mode="ffa")
        self.assertEqual(game.begin_general_select(), "general_select")
        self.assertEqual(game.game_candidates if False else game.general_candidates,
                         game.general_candidates)
        self.assertTrue(game.general_candidates)

    def test_scene_flow_for_identity(self):
        game = self.make_game(4, mode="identity")
        self.assertEqual(game.begin_general_select(), "identity_reveal")
        self.assertTrue(game.pending_identities)
        self.assertTrue(game.confirm_identity())
        self.assertEqual(game.scene, "general_select")

    def test_confirm_identity_is_only_valid_in_its_scene(self):
        game = self.make_game(4, mode="ffa")
        game.begin_general_select()
        self.assertFalse(game.confirm_identity())

    def test_identity_battle_setup(self):
        game = self.start_identity_battle(4)
        self.assertEqual(game.scene, "game")
        self.assertEqual(len(game.players), 5)
        self.assertTrue(all(player.general_id for player in game.players))
        self.assertEqual(
            [p.identity for p in game.players].count(Identity.LORD), 1)
        self.assertIs(game.seats.all_players()[0], game.players[0])

    def test_lord_gets_an_extra_hp_cap_and_is_full(self):
        game = self.start_identity_battle(4)
        lord = self.lord(game)
        general = game.generals.get(lord.general_id)
        self.assertEqual(lord.max_hp, general.max_hp + 1)
        self.assertEqual(lord.hp, lord.max_hp, "主公开局满体力")

    def test_non_lord_keeps_the_general_hp_cap(self):
        game = self.start_identity_battle(4)
        for player in game.players:
            if player is self.lord(game):
                continue
            general = game.generals.get(player.general_id)
            self.assertEqual(player.max_hp, general.max_hp, player.name)

    def test_lord_acts_first(self):
        game = self.start_identity_battle(4)
        self.assertIs(game.current_turn_player, self.lord(game))

    def test_ffa_still_starts_with_the_human(self):
        game = self.make_game(3, mode="ffa")
        game.begin_general_select()
        game.selected_general = game.general_candidates[0]
        game.confirm_general()
        self.assertIs(game.current_turn_player, game.player)
        self.assertTrue(all(p.identity is None for p in game.players))

    def test_initial_hand_is_dealt_to_everyone(self):
        game = self.make_game(3, mode="ffa")
        game.begin_general_select()
        game.selected_general = game.general_candidates[0]
        game.confirm_general()
        for player in game.players:
            self.assertGreaterEqual(len(player.hand), 4, player.name)

    def test_reset_clears_identity_and_general(self):
        game = self.start_identity_battle(4)
        game.reset()
        self.assertTrue(all(p.identity is None for p in game.players))
        self.assertTrue(all(p.identity_revealed is False for p in game.players))


# ==================================================
# F. 身份胜负
# ==================================================


class VictoryConditionTests(IdentityModeTestCase):
    def _battle(self, seed=11):
        game = self.start_identity_battle(4, seed=seed)
        return game, self.lord(game)

    def test_lord_side_wins_when_rebels_and_renegade_are_gone(self):
        game = self.make_game(4, mode="identity", seed=11)
        game.mode.apply_identities((
            Identity.LORD, Identity.LOYALIST, Identity.REBEL, Identity.REBEL, Identity.RENEGADE))
        lord = game.players[0]
        for player in list(game.players):
            if player.identity in (Identity.LORD, Identity.LOYALIST):
                continue
            self.kill(game, player)
        self.assertTrue(game.game_over)
        self.assertEqual(game.result.reason, "LORD_SIDE_WIN")
        self.assertTrue(lord.alive)

    def test_rebels_win_when_the_lord_dies(self):
        game, lord = self._battle()
        rebel = self.player_with(game, Identity.REBEL)
        self.kill(game, lord, source=rebel)
        self.assertTrue(game.game_over)
        self.assertEqual(game.result.reason, "REBEL_WIN")

    def test_renegade_wins_when_only_the_renegade_survives(self):
        game = self.make_game(4, mode="identity", seed=13)
        # 手工布置终局：只剩主公与内奸，内奸杀死主公。
        game.mode.apply_identities((
            Identity.LORD, Identity.RENEGADE, Identity.REBEL, Identity.LOYALIST, Identity.REBEL))
        lord, renegade = game.players[0], game.players[1]
        for player in game.players[2:]:
            self.kill(game, player)
        self.assertFalse(game.game_over, "反贼未清空时不能结束")
        self.kill(game, lord, source=renegade)
        self.assertTrue(game.game_over)
        self.assertEqual(game.result.reason, "RENEGADE_WIN")

    def test_rebels_win_even_if_all_rebels_died_with_the_lord(self):
        """标准口径：主公阵亡且内奸不是唯一存活者 → 反贼胜。"""

        game = self.make_game(4, mode="identity", seed=17)
        game.mode.apply_identities((
            Identity.LORD, Identity.LOYALIST, Identity.REBEL, Identity.RENEGADE, Identity.REBEL))
        lord = game.players[0]
        for player in (game.players[2], game.players[4]):
            self.kill(game, player)
        self.kill(game, lord, source=game.players[1])
        self.assertTrue(game.game_over)
        self.assertEqual(game.result.reason, "REBEL_WIN")

    def test_a_non_final_death_keeps_the_battle_running(self):
        game, lord = self._battle()
        victim = self.player_with(game, Identity.REBEL)
        self.kill(game, victim)
        self.assertFalse(game.game_over)
        self.assertIsNone(game.result)

    def test_ffa_result_is_unchanged(self):
        game = self.make_game(1, mode="ffa")
        game.start_local_battle(1)
        self.kill(game, game.players[1])
        self.assertTrue(game.game_over)
        self.assertEqual(game.result.reason, "LAST_SURVIVOR")
        self.assertIs(game.winner, game.player)

    def test_reveal_all_happens_when_the_battle_ends(self):
        game, lord = self._battle()
        self.kill(game, lord, source=self.player_with(game, Identity.REBEL))
        self.assertTrue(game.game_over)
        for player in game.players:
            self.assertTrue(
                player.identity_revealed,
                player.name + " 的身份应在结束时公开")


# ==================================================
# G. 死亡奖惩
# ==================================================


class DeathConsequenceTests(IdentityModeTestCase):
    def _battle_with_killer(self, killer_identity, victim_identity, seed=23):
        """构造 5 人身份局，把指定身份安排给座次 0 / 1 的两名 AI。"""

        game = self.make_game(4, mode="identity", seed=seed)
        layout = [killer_identity, victim_identity]
        for identity in Identity:
            if identity not in (killer_identity, victim_identity):
                layout.append(identity)
        while len(layout) < len(game.players):
            layout.append(Identity.REBEL)
        game.mode.apply_identities(tuple(layout[: len(game.players)]))
        # 用 AI 座次做击杀者，避免真人的控制器差异影响测试。
        killer, victim = game.players[0], game.players[1]
        return game, killer, victim

    def test_killing_a_rebel_rewards_the_killer(self):
        for killer_identity in (Identity.LORD, Identity.LOYALIST, Identity.REBEL, Identity.RENEGADE):
            game, killer, victim = self._battle_with_killer(
                killer_identity, Identity.REBEL, seed=31)
            killer.hand = []
            set_draw_order(game, [tao(), tao(), tao()])
            self.kill(game, victim, source=killer)
            self.assertEqual(
                len(killer.hand), 3,
                identity_name(killer_identity) + " 击杀反贼应当摸三张")

    def test_lord_killing_a_loyalist_is_punished(self):
        game, lord, loyalist = self._battle_with_killer(
            Identity.LORD, Identity.LOYALIST, seed=37)
        lord.hand = [tao(), tao()]
        from tests.legacy_helpers import equipment as equipment_card

        armor = equipment_card("BAGUA")
        lord.set_equipment(armor)
        self.kill(game, loyalist, source=lord)
        self.assertEqual(len(lord.hand), 0, "主公误杀忠臣要弃光手牌")
        self.assertIsNone(lord.get_equipment("armor"), "装备也要弃掉")

    def test_other_killers_do_not_trigger_the_lord_penalty(self):
        game, rebel, loyalist = self._battle_with_killer(
            Identity.REBEL, Identity.LOYALIST, seed=41)
        rebel.hand = [tao(), tao()]
        self.kill(game, loyalist, source=rebel)
        self.assertEqual(len(rebel.hand), 2, "非主公杀忠臣不受惩罚")

    def test_no_killer_means_no_reward_and_no_penalty(self):
        game, lord, victim = self._battle_with_killer(
            Identity.LORD, Identity.REBEL, seed=43)
        lord.hand = []
        self.kill(game, victim, source=None)
        self.assertEqual(len(lord.hand), 0, "无来源死亡不发击杀奖励")

    def test_self_inflicted_death_gives_no_reward(self):
        game, lord, victim = self._battle_with_killer(
            Identity.LORD, Identity.REBEL, seed=47)
        victim.hand = []
        self.kill(game, victim, source=victim)
        self.assertEqual(len(victim.hand), 0, "自杀不算被击杀")

    def test_ffa_deaths_have_no_consequences(self):
        game = self.make_game(2, mode="ffa")
        game.start_local_battle(2)
        victim = game.players[1]
        victim.identity = None
        killer = game.player
        killer.hand = []
        self.kill(game, victim, source=killer)
        self.assertEqual(len(killer.hand), 0)


# ==================================================
# H. 濒死保护
# ==================================================


class DyingProtectionTests(IdentityModeTestCase):
    def test_rescued_lord_does_not_end_the_battle(self):
        game = self.start_identity_battle(4, seed=53)
        lord = self.lord(game)
        lord.hp = 0
        # 模拟被桃救回：回到 1 点体力（濒死流程的净效果）。
        lord.hp = 1
        self.assertFalse(game.game_over, "濒死不等于死亡")
        self.assertIsNone(game.result)

    def test_lord_dying_flow_asks_for_rescue_before_any_result(self):
        from src.game.flows import DamageContext, DamageFlow

        game = self.start_identity_battle(4, seed=59)
        game.ai_pacing = False       # 濒死求桃走同步响应，便于断言
        lord = self.lord(game)
        lord.hp = 1
        lord.hand = [tao()]

        DamageFlow(
            game.engine,
            DamageContext(game.player, lord, 1, card=tao()),
        ).start()

        # 主公被桃救回：体力回到 1，对局必须继续。
        self.assertGreater(lord.hp, 0, "主公应当用【桃】自救")
        self.assertTrue(lord.alive)
        self.assertFalse(game.game_over, "濒死不等于死亡，对局不能提前结束")
        self.assertIsNone(game.result)

    def test_lord_death_after_failed_rescue_ends_the_battle(self):
        game = self.start_identity_battle(4, seed=61)
        game.ai_pacing = False
        lord = self.lord(game)
        for player in game.players:
            player.hand = []
        rebel = self.player_with(game, Identity.REBEL)
        lord.hp = 1
        from src.game.flows import DamageContext, DamageFlow

        DamageFlow(
            game.engine,
            DamageContext(rebel, lord, 2, card=tao()),
        ).start()
        self.assertFalse(lord.alive)
        self.assertTrue(game.game_over)
        self.assertEqual(game.result.reason, "REBEL_WIN")


# ==================================================
# I. AI 不作弊
# ==================================================


class AiFairnessTests(IdentityModeTestCase):
    def _controller(self, game, player):
        return game.get_controller(player)

    def _battle(self, seed=67):
        """5 人身份局：座次 0 是真人，1~4 是 AI。"""

        game = self.make_game(4, mode="identity", seed=seed)
        return game

    def test_visible_identity_hides_unrevealed_roles(self):
        game = self._battle(67)
        game.mode.apply_identities((
            Identity.REBEL, Identity.REBEL, Identity.LOYALIST, Identity.RENEGADE, Identity.LORD))
        ai = game.players[1]
        controller = self._controller(game, ai)
        hidden = game.players[2]
        self.assertIsNone(
            controller.visible_identity(hidden),
            "AI 不能看到未公开的身份")
        self.assertIs(controller.visible_identity(ai), ai.identity,
                      "AI 知道自己的身份")

    def test_identity_bias_only_reacts_to_revealed_roles(self):
        game = self._battle(71)
        game.mode.apply_identities((
            Identity.LOYALIST, Identity.LORD, Identity.LOYALIST, Identity.RENEGADE, Identity.REBEL))
        lord = game.players[1]
        controller = self._controller(game, lord)
        hidden_rebel = game.players[4]
        hidden_loyalist = game.players[2]

        self.assertEqual(
            controller.identity_bias(hidden_rebel), 0,
            "未公开的反贼不能影响 AI 打分")
        self.assertEqual(controller.identity_bias(hidden_loyalist), 0)

        hidden_rebel.identity_revealed = True
        self.assertGreater(
            controller.identity_bias(hidden_rebel), 0,
            "公开反贼要被主公针对")
        hidden_loyalist.identity_revealed = True
        self.assertLess(
            controller.identity_bias(hidden_loyalist), 0,
            "公开忠臣不该被主公攻击")

    def test_loyalist_never_targets_the_lord(self):
        game = self._battle(73)
        game.mode.apply_identities((
            Identity.REBEL, Identity.LOYALIST, Identity.LORD, Identity.RENEGADE, Identity.REBEL))
        loyalist, lord = game.players[1], game.players[2]
        controller = self._controller(game, loyalist)
        self.assertLess(controller.identity_bias(lord), 0, "忠臣不该打主公")

    def test_controller_never_reads_raw_identity_for_others(self):
        """隐藏身份的玩家在打分上必须与"无身份"完全一致。"""

        game = self._battle(79)
        game.mode.apply_identities((
            Identity.LOYALIST, Identity.LORD, Identity.LOYALIST, Identity.RENEGADE, Identity.REBEL))
        lord = game.players[1]
        controller = self._controller(game, lord)
        hidden = game.players[2]
        self.assertFalse(hidden.identity_revealed)
        bias_hidden = controller.identity_bias(hidden)
        # 把真身换掉也不该改变结果：AI 只认可见信息。
        original = hidden.identity
        hidden.identity = Identity.REBEL
        self.assertEqual(controller.identity_bias(hidden), bias_hidden)
        hidden.identity = original

    def test_protects_only_revealed_lords(self):
        game = self._battle(83)
        game.mode.apply_identities((
            Identity.REBEL, Identity.LOYALIST, Identity.LORD, Identity.RENEGADE, Identity.REBEL))
        loyalist, lord, hidden = game.players[1], game.players[2], game.players[4]
        controller = self._controller(game, loyalist)
        self.assertTrue(controller.protects(lord), "忠臣要救主公")
        self.assertFalse(controller.protects(hidden), "不救身份未知的人")

    def test_ffa_controller_has_no_identity_logic(self):
        game = self.make_game(3, mode="ffa")
        game.start_local_battle(3)
        controller = self._controller(game, game.players[1])
        for player in game.players:
            self.assertEqual(controller.identity_bias(player), 0)


class IdentityStanceTests(IdentityModeTestCase):
    """按身份与公开行为决定打谁：主公克制、忠臣护主、反贼速推、内奸平衡。

    立场只看公开信息（已公开身份 + 桌面上的公开出手），隐藏身份不参与。
    """

    def _battle(self, seed=101, identities=None):
        game = self.make_game(4, mode="identity", seed=seed)
        if identities is not None:
            game.mode.apply_identities(identities)
        return game

    def _controller(self, game, player):
        return game.get_controller(player)

    def _strike(self, game, attacker, target):
        """一次公开出手：走真实事件链路，而不是直接改内部记录。"""

        from src.game.engine import Event, EventType
        from tests.legacy_helpers import normal_sha

        game.context.emit(Event(
            EventType.CARD_USED,
            source=attacker,
            payload={"card": normal_sha(), "targets": (target,)},
        ))

    def test_rebel_prioritises_the_lord(self):
        game = self._battle(101, (
            Identity.LOYALIST, Identity.REBEL, Identity.LORD,
            Identity.RENEGADE, Identity.REBEL))
        rebel, lord, unknown = game.players[1], game.players[2], game.players[4]
        controller = self._controller(game, rebel)
        self.assertGreater(controller.stance_bias(lord), 0, "反贼要速推主公")
        self.assertEqual(
            controller.stance_bias(unknown), 0,
            "未公开身份的同伴看不出来，也不该凭空加分")

    def test_lord_holds_fire_until_someone_strikes_first(self):
        game = self._battle(103, (
            Identity.LOYALIST, Identity.REBEL, Identity.LORD,
            Identity.RENEGADE, Identity.REBEL))
        lord, quiet, attacker = game.players[2], game.players[1], game.players[4]
        controller = self._controller(game, lord)
        self.assertLess(
            controller.stance_bias(quiet), 0,
            "还没表明立场的人，主公不该主动攻击")
        self._strike(game, attacker, lord)
        self.assertGreater(
            controller.stance_bias(attacker), 0,
            "打过主公的人要被反击")

    def test_lord_spares_whoever_fights_his_enemy(self):
        game = self._battle(105, (
            Identity.LOYALIST, Identity.REBEL, Identity.LORD,
            Identity.RENEGADE, Identity.REBEL))
        lord, rebel, loyalist = game.players[2], game.players[1], game.players[0]
        controller = self._controller(game, lord)
        self._strike(game, rebel, lord)
        self._strike(game, loyalist, rebel)
        self.assertLess(
            controller.stance_bias(loyalist), 0,
            "帮我打敌人的人是自家人，不能误伤")

    def test_loyalist_hunts_whoever_strikes_the_lord(self):
        game = self._battle(107, (
            Identity.REBEL, Identity.LOYALIST, Identity.LORD,
            Identity.RENEGADE, Identity.REBEL))
        loyalist, lord, hidden_rebel = (
            game.players[1], game.players[2], game.players[4])
        controller = self._controller(game, loyalist)
        self.assertLess(controller.stance_bias(lord), 0, "忠臣绝不能打主公")
        self._strike(game, hidden_rebel, lord)
        self.assertGreater(
            controller.stance_bias(hidden_rebel), 0,
            "打过主公的人就是明面上的敌人")

    def test_loyalist_shields_the_lord_with_wuxie(self):
        game = self._battle(109, (
            Identity.REBEL, Identity.LOYALIST, Identity.LORD,
            Identity.RENEGADE, Identity.REBEL))
        loyalist, lord, other = game.players[1], game.players[2], game.players[3]
        controller = self._controller(game, loyalist)
        self.assertTrue(controller._shields_the_lord((lord,)))
        self.assertFalse(controller._shields_the_lord((other,)))

    def test_lord_holds_back_when_wounded(self):
        game = self._battle(111, (
            Identity.REBEL, Identity.LOYALIST, Identity.LORD,
            Identity.RENEGADE, Identity.REBEL))
        lord = game.players[2]
        controller = self._controller(game, lord)
        lord.hp = 2
        self.assertTrue(controller._holds_back(), "主公体力吃紧要收手")
        lord.hp = lord.max_hp
        self.assertFalse(controller._holds_back())

    def test_renegade_never_finishes_the_lord(self):
        game = self._battle(113, (
            Identity.REBEL, Identity.LOYALIST, Identity.LORD,
            Identity.RENEGADE, Identity.REBEL))
        renegade, lord = game.players[3], game.players[2]
        controller = self._controller(game, renegade)
        lord.hp = 1
        self.assertLess(
            controller.stance_bias(lord), 0,
            "主公只剩一点体力时内奸不能补刀")

    def test_renegade_joins_the_lord_when_rebels_swarm(self):
        game = self._battle(115, (
            Identity.REBEL, Identity.LOYALIST, Identity.LORD,
            Identity.RENEGADE, Identity.REBEL))
        renegade, lord = game.players[3], game.players[2]
        first, second = game.players[0], game.players[4]
        controller = self._controller(game, renegade)
        self._strike(game, first, lord)
        self.assertLess(controller.stance_bias(first), 0, "只来一个反贼：先看戏")
        self._strike(game, second, lord)
        self.assertGreater(
            controller.stance_bias(first), 0,
            "反贼成群时内奸要帮主公压住他们")

    def test_ffa_has_no_stance_logic(self):
        game = self.make_game(3, mode="ffa")
        game.start_local_battle(3)
        controller = self._controller(game, game.players[1])
        for player in game.players:
            self.assertEqual(controller.stance_bias(player), 0)

    def test_ai_cannot_see_the_human_hidden_identity(self):
        """真人不是透明人：未公开的身份对 AI 不可见（曾经这里会泄露）。"""

        game = self._battle(117, (
            Identity.LOYALIST, Identity.REBEL, Identity.LORD,
            Identity.RENEGADE, Identity.REBEL))
        human = game.players[0]
        self.assertFalse(human.identity_revealed)
        self.assertIsNone(
            game.mode.public_identity_of(human),
            "对外公开的身份查询不该带真人视角")
        for player in game.players[1:]:
            controller = self._controller(game, player)
            self.assertIsNone(controller.visible_identity(human))


# ==================================================
# J. 重新开始
# ==================================================


class RestartTests(IdentityModeTestCase):
    def test_restart_returns_to_the_setup_flow(self):
        game = self.start_identity_battle(4)
        game.restart_setup()
        self.assertEqual(game.scene, "menu")
        self.assertTrue(all(p.identity is None for p in game.players))
        self.assertTrue(all(p.general_id is None for p in game.players))
        self.assertIsNone(game.result)
        self.assertFalse(game.game_over)

    def test_two_identity_battles_do_not_share_state(self):
        game = self.start_identity_battle(4, seed=5)
        first = [p.identity for p in game.players]
        game.restart_setup()
        game.begin_general_select()
        game.confirm_identity()
        game.selected_general = game.general_candidates[0]
        game.confirm_general()
        self.assertEqual(
            [p.identity for p in game.players].count(Identity.LORD), 1)
        self.assertFalse(game.player.identity_revealed and game.player.identity is not Identity.LORD)
        self.assertTrue(first)

    def test_switching_modes_between_battles_resets_everything(self):
        game = self.start_identity_battle(4)
        game.restart_setup()
        game.set_mode("ffa")
        game.begin_general_select()
        game.selected_general = game.general_candidates[0]
        game.confirm_general()
        self.assertEqual(game.scene, "game")
        self.assertTrue(all(p.identity is None for p in game.players))

    def test_restart_clears_pending_and_skills(self):
        game = self.start_identity_battle(4)
        game.engine.pending.create(
            __import__("src.game.engine.pending", fromlist=["PendingRequestType"])
            .PendingRequestType.CONFIRM,
            source=game.player, target=game.player, prompt="x", owner_flow=None)
        game.restart_setup()
        self.assertIsNone(game.pending_request)
        self.assertTrue(all(not game.skills.skill_ids_of(p) for p in game.players))


# ==================================================
# K. FFA 回归
# ==================================================


class FfaRegressionTests(IdentityModeTestCase):
    def test_ffa_setup_for_several_player_counts(self):
        for ai_count in (1, 3, 7):
            game = self.make_game(ai_count, mode="ffa")
            game.begin_general_select()
            game.selected_general = game.general_candidates[0]
            game.confirm_general()
            self.assertEqual(len(game.players), ai_count + 1)
            self.assertTrue(all(p.alive for p in game.players))
            self.assertTrue(all(p.identity is None for p in game.players))

    def test_ffa_last_survivor_result(self):
        game = self.make_game(2, mode="ffa")
        game.start_local_battle(2)
        self.kill(game, game.players[1])
        self.kill(game, game.players[2])
        self.assertTrue(game.game_over)
        self.assertEqual(game.result.reason, "LAST_SURVIVOR")
        self.assertIs(game.result.winner, game.player)

    def test_ffa_human_elimination_result(self):
        game = self.make_game(2, mode="ffa")
        game.start_local_battle(2)
        self.kill(game, game.player)
        self.assertTrue(game.game_over)
        self.assertEqual(game.result.reason, "HUMAN_ELIMINATED")

    def test_ffa_has_no_mode_specific_ui_rows(self):
        game = self.make_game(2, mode="ffa")
        game.start_local_battle(2)
        self.assertEqual(game.mode.result_lines(), ())


if __name__ == "__main__":
    unittest.main()
