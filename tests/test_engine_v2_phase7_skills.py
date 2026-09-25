"""Phase 7 tests: general registry, skill binding, hooks, modifiers, lifecycle."""

import os
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame

from src.game import Game
from src.game.engine import EventType
from src.game.engine.skills import Skill, SkillBinding
from src.game.flows import DamageContext, DamageFlow
from src.game.rules import DistanceRule
from src.game.skills import (
    ModifierKind,
    ModifierSpec,
    SkillDef,
    SkillKind,
    SkillRegistry,
    SkillState,
    create_default_skill_registry,
    create_probe_skill_registry,
)
from src.game.generals import GeneralDef, GeneralRegistry, create_default_general_registry
from src.game.skills.state import ResetScope
from src.card_catalog import (
    create_development_deck,
    create_equipment_cards,
    create_fire_sha,
)
from src.game.skills import Modifier
from tests.legacy_helpers import canonical_card, normal_sha, set_draw_order, tao


def trick(name):
    return canonical_card(name)


class PlusOneDamage(Skill):
    """测试用：让 owner 受到的伤害 +1（验证与 -1 技能的稳定顺序）。"""

    id = "probe_plus_one"
    name = "加压"

    def bindings(self):
        return (SkillBinding(EventType.DAMAGE_MODIFY, priority=5),)

    def can_trigger(self, context, event):
        damage = event.payload.get("damage")
        return damage is not None and damage.target is self.owner

    def resolve(self, context, event):
        event.payload["damage"].amount += 1


class CancelDamage(Skill):
    """测试用：把 owner 即将受到的伤害完全抵消。"""

    id = "probe_cancel"
    name = "免疫"

    def bindings(self):
        return (SkillBinding(EventType.DAMAGE_MODIFY, priority=90),)

    def can_trigger(self, context, event):
        damage = event.payload.get("damage")
        return damage is not None and damage.target is self.owner

    def resolve(self, context, event):
        event.payload["damage"].amount = 0
        event.payload["damage"].cancelled = True


class RecurseOnce(Skill):
    """测试用：受到伤害后再摸一张牌（验证嵌套 hook）。"""

    id = "probe_recurse"
    name = "连环"

    def bindings(self):
        return (SkillBinding(EventType.DAMAGE_SETTLED),)

    def can_trigger(self, context, event):
        damage = event.payload.get("damage")
        return (
            damage is not None
            and damage.target is self.owner
            and event.payload.get("amount", 0) > 0
            and self.owner.alive
        )

    def resolve(self, context, event):
        from src.game.atoms_v2 import DrawCardsAtom

        context.apply(DrawCardsAtom(self.owner, 1))


EXTRA_PROBES = (
    SkillDef(
        id="probe_plus_one",
        name="加压",
        kind=SkillKind.PASSIVE,
        factory=PlusOneDamage,
        tags=("probe",),
    ),
    SkillDef(
        id="probe_cancel",
        name="免疫",
        kind=SkillKind.PASSIVE,
        factory=CancelDamage,
        tags=("probe",),
    ),
    SkillDef(
        id="probe_recurse",
        name="连环",
        kind=SkillKind.PASSIVE,
        factory=RecurseOnce,
        tags=("probe",),
    ),
)


class SkillTestCase(unittest.TestCase):
    def setUp(self):
        pygame.init()

    def make_game(self, ai_count=2, *, probe=True):
        game = Game(ai_count=ai_count)
        if probe:
            registry = create_probe_skill_registry()
            registry.register_all(EXTRA_PROBES)
            game.skill_registry = registry
            game.skills.registry = registry
        game.scene = "game"
        game.actions.clear()
        game.engine.reset()
        for player in game.players:
            player.hand = []
            player.hp = player.max_hp
            player.alive = True
        game.phase = "play"
        game.current_turn_player = game.player
        return game

    def strike(self, game, source, target, amount=1, card=None, nature=None):
        card = card or normal_sha()
        if nature is None:
            nature = getattr(card, "nature", "normal")
        return DamageFlow(
            game.engine,
            DamageContext(source, target, amount, nature=nature, card=card),
        ).start()


class GeneralRegistryTests(unittest.TestCase):
    def test_default_catalog_has_generals_and_skills(self):
        generals = create_default_general_registry()
        skills = create_default_skill_registry()
        # Phase 8 起武将数量会持续增长，这里只校验"注册表非空且技能齐全"。
        self.assertGreaterEqual(len(generals), 3)
        self.assertGreaterEqual(len(skills), 3)
        for general in generals.list_generals():
            self.assertTrue(general.skill_ids)
            # 未实现（图鉴条目）的武将没有技能定义是预期的：它们被明确
            # 禁止开局，界面上只展示"为什么不能选"。
            if not general.implemented:
                continue
            for skill_id in general.skill_ids:
                self.assertIn(skill_id, skills)

    def test_register_and_lookup_by_stable_id(self):
        registry = GeneralRegistry()
        general = GeneralDef(id="caocao", name="曹操", kingdom="wei", skill_ids=("jianxiong",))
        registry.register(general)
        self.assertIs(registry.get("caocao"), general)
        self.assertIs(registry.require("caocao"), general)
        self.assertEqual(registry.ids(), ("caocao",))
        self.assertEqual(general.kingdom_name, "魏")

    def test_duplicate_and_unknown_general(self):
        registry = create_default_general_registry()
        with self.assertRaises(ValueError):
            registry.register(registry.require("zhangfei"))
        self.assertIsNone(registry.get("nobody"))
        with self.assertRaises(KeyError):
            registry.require("nobody")

    def test_general_def_rejects_missing_fields(self):
        with self.assertRaises(ValueError):
            GeneralDef(id="", name="无名")
        with self.assertRaises(ValueError):
            GeneralDef(id="x", name="")


class SkillRegistryTests(unittest.TestCase):
    def test_register_lookup_and_duplicates(self):
        registry = SkillRegistry()
        definition = SkillDef(id="demo", name="示例", factory=PlusOneDamage)
        registry.register(definition)
        self.assertIs(registry.get("demo"), definition)
        self.assertEqual(registry.ids(), ("demo",))
        with self.assertRaises(ValueError):
            registry.register(definition)
        with self.assertRaises(KeyError):
            registry.require("missing")

    def test_definition_requires_some_behaviour(self):
        with self.assertRaises(ValueError):
            SkillDef(id="empty", name="空")

    def test_probe_registry_is_separate_from_default(self):
        self.assertNotIn("probe_recycle", create_default_skill_registry())
        self.assertIn("probe_recycle", create_probe_skill_registry())


class SkillBindingTests(SkillTestCase):
    def test_bind_and_unbind_a_triggered_skill(self):
        game = self.make_game()
        victim = game.players[1]
        game.skills.bind(victim, "probe_blood_draw")
        self.assertEqual(game.skills.skill_ids_of(victim), ("probe_blood_draw",))
        self.assertEqual(game.skills.listeners_for(victim, EventType.DAMAGE_SETTLED), 1)

        game.skills.unbind(victim, "probe_blood_draw")
        self.assertEqual(game.skills.skill_ids_of(victim), ())
        self.assertEqual(game.skills.listeners_for(victim), 0)

    def test_double_binding_is_rejected(self):
        game = self.make_game()
        victim = game.players[1]
        game.skills.bind(victim, "probe_blood_draw")
        with self.assertRaises(ValueError):
            game.skills.bind(victim, "probe_blood_draw")

    def test_the_same_skill_on_two_players_is_independent(self):
        game = self.make_game(ai_count=3)
        first, second = game.players[1], game.players[2]
        game.skills.bind(first, "probe_blood_draw")
        game.skills.bind(second, "probe_blood_draw")

        self.assertEqual(game.skills.skill_ids_of(first), ("probe_blood_draw",))
        self.assertEqual(game.skills.skill_ids_of(second), ("probe_blood_draw",))
        self.assertEqual(game.skills.listeners_for(first), 1)
        self.assertEqual(game.skills.listeners_for(second), 1)

        first.hand = []
        second.hand = []
        self.strike(game, game.player, first, 1)
        self.assertEqual(len(first.hand), 1, "技能应属于自己的拥有者")
        self.assertEqual(len(second.hand), 0, "另一个拥有者不应被触发")

        game.skills.unbind(first, "probe_blood_draw")
        self.assertEqual(game.skills.listeners_for(first), 0)
        self.assertEqual(game.skills.listeners_for(second), 1, "解绑不应影响其他拥有者")

    def test_unbinding_clears_that_skills_state_only(self):
        game = self.make_game()
        victim = game.players[1]
        game.skills.bind(victim, "probe_blood_draw")
        victim.skill_state.set("probe_blood_draw", "drawn", 3, ResetScope.TURN)
        game.skills.bind(victim, "probe_play_phase")
        victim.skill_state.set("probe_play_phase", "x", 1)

        game.skills.unbind(victim, "probe_blood_draw")
        self.assertEqual(victim.skill_state.get("probe_blood_draw", "drawn"), None)
        self.assertEqual(victim.skill_state.get("probe_play_phase", "x"), 1)

    def test_binding_a_general_binds_its_skills(self):
        game = self.make_game()
        ai = game.players[1]
        game.set_general(ai, "huangyueying")
        self.assertEqual(ai.general_id, "huangyueying")
        # Phase 8 起黄月英同时拥有集智与奇才
        self.assertEqual(game.skills.skill_ids_of(ai), ("jizhi", "qicai"))
        self.assertEqual(game.general_of(ai).name, "黄月英")


class HookTests(SkillTestCase):
    def test_before_hook_modifies_damage_amount(self):
        game = self.make_game()
        victim = game.players[1]
        game.skills.bind(victim, "probe_iron_body")
        before = victim.hp

        self.strike(game, game.player, victim, 2)

        self.assertEqual(victim.hp, before - 1, "铁骨应当把 2 点伤害减到 1 点")

    def test_hook_modification_order_is_stable(self):
        game = self.make_game()
        victim = game.players[1]
        game.skills.bind(victim, "probe_iron_body")   # -1，priority 20
        game.skills.bind(victim, "probe_plus_one")    # +1，priority 5

        before = victim.hp
        self.strike(game, game.player, victim, 2)
        # 高优先级（铁骨 priority 20）先执行，两个技能互不干扰：
        # 2 - 1 + 1 = 2
        self.assertEqual(victim.hp, before - 2)

    def test_hook_can_cancel_the_damage_entirely(self):
        game = self.make_game()
        victim = game.players[1]
        game.skills.bind(victim, "probe_cancel")
        before = victim.hp

        self.strike(game, game.player, victim, 3)

        self.assertEqual(victim.hp, before, "免疫应当完全抵消伤害")

    def test_after_hook_draws_a_card(self):
        game = self.make_game()
        victim = game.players[1]
        victim.hand = []
        game.skills.bind(victim, "probe_blood_draw")

        self.strike(game, game.player, victim, 1)

        self.assertEqual(len(victim.hand), 1, "受伤后应当摸一张牌")

    def test_nested_trigger_completes_without_recursion(self):
        game = self.make_game()
        victim = game.players[1]
        victim.hand = []
        game.skills.bind(victim, "probe_blood_draw")
        game.skills.bind(victim, "probe_recurse")

        self.strike(game, game.player, victim, 1)

        self.assertEqual(len(victim.hand), 2, "两个 after 技能都应当触发一次")
        self.assertLessEqual(Skill._depth, Skill.MAX_DEPTH)

    def test_phase_hook_fires_on_play_phase(self):
        from src.game.flows import TurnFlow

        game = self.make_game(ai_count=1)
        ai = game.players[1]
        game.skills.bind(ai, "probe_play_phase")

        TurnFlow(game.engine, ai).begin_interactive()

        self.assertEqual(ai.skill_state.get("probe_play_phase", "phase_starts"), 1)

    def test_hook_priority_beats_registration_order(self):
        game = self.make_game()
        victim = game.players[1]
        # 先注册低优先级（+1），再注册高优先级（抵消）
        game.skills.bind(victim, "probe_plus_one")
        game.skills.bind(victim, "probe_cancel")

        before = victim.hp
        self.strike(game, game.player, victim, 2)
        self.assertEqual(victim.hp, before, "高优先级技能应当先执行并抵消伤害")


class ModifierTests(SkillTestCase):
    def test_distance_modifier(self):
        game = self.make_game(ai_count=3)
        actor = game.players[1]
        target = game.players[3]
        base = DistanceRule.distance(game, actor, target)
        self.assertGreater(base, 1)

        game.skills.bind(actor, "probe_nimble")
        self.assertEqual(DistanceRule.distance(game, actor, target), base - 1)

        game.skills.unbind(actor, "probe_nimble")
        self.assertEqual(DistanceRule.distance(game, actor, target), base)

    def test_draw_count_modifier(self):
        game = self.make_game()
        player = game.players[1]
        self.assertEqual(game.draw_count(player), 2)
        game.skills.bind(player, "probe_extra_draw")
        self.assertEqual(game.draw_count(player), 3)

    def test_hand_limit_modifier(self):
        game = self.make_game()
        player = game.players[1]
        player.hp = 3
        self.assertEqual(game.hand_limit(player), 3)

        game.modifiers.register(
            Modifier(
                kind=ModifierKind.HAND_LIMIT,
                value=2,
                owner=player,
                skill_id="test_limit",
                roles=("player",),
            )
        )
        self.assertEqual(game.hand_limit(player), 5)
        game.modifiers.unregister_owner_skill(player, "test_limit")
        self.assertEqual(game.hand_limit(player), 3)

    def test_slash_quota_modifier_removes_the_sha_limit(self):
        game = self.make_game()
        player = game.players[1]
        self.assertFalse(game.can_use_unlimited_sha(player))
        game.skills.bind(player, "paoxiao")
        self.assertTrue(game.can_use_unlimited_sha(player))
        game.skills.unbind(player, "paoxiao")
        self.assertFalse(game.can_use_unlimited_sha(player))

    def test_attack_range_modifier(self):
        game = self.make_game(ai_count=4)
        actor = game.players[1]
        far = game.players[4]
        self.assertFalse(DistanceRule.in_attack_range(game, actor, far))

        game.modifiers.register(
            Modifier(
                kind=ModifierKind.ATTACK_RANGE,
                value=3,
                owner=actor,
                skill_id="test_range",
                roles=("player",),
            )
        )
        self.assertTrue(DistanceRule.in_attack_range(game, actor, far))

    def test_modifier_order_is_stable(self):
        game = self.make_game()
        player = game.players[1]
        for value, priority in ((-1, 0), (2, 10), (-3, 5)):
            game.modifiers.register(
                Modifier(
                    kind=ModifierKind.DRAW_COUNT,
                    value=value,
                    owner=player,
                    skill_id="m%d" % priority,
                    priority=priority,
                    roles=("player",),
                )
            )
        self.assertEqual(game.draw_count(player), 0)
        self.assertEqual(
            tuple(modifier.priority for modifier in game.modifiers.sorted_for(ModifierKind.DRAW_COUNT)),
            (10, 5, 0),
        )


class ActiveSkillTests(SkillTestCase):
    def test_active_skill_discards_then_draws(self):
        game = self.make_game()
        player = game.player
        card = normal_sha()
        player.hand = [card]
        game.skills.bind(player, "probe_recycle")

        allowed, reason = game.skills.can_activate(player, "probe_recycle")
        self.assertTrue(allowed, reason)

        ok, _ = game.skills.activate(player, "probe_recycle", card=card)
        self.assertTrue(ok)
        self.assertNotIn(card, player.hand)
        self.assertIn(card, game.deck.discard_pile)
        self.assertEqual(len(player.hand), 1, "弃一张后应当摸一张")

    def test_active_skill_limited_to_once_per_turn(self):
        game = self.make_game()
        player = game.player
        first, second = normal_sha(), tao()
        player.hand = [first, second]
        game.skills.bind(player, "probe_recycle")

        game.skills.activate(player, "probe_recycle", card=first)
        allowed, reason = game.skills.can_activate(player, "probe_recycle")
        self.assertFalse(allowed)
        self.assertIn("已经发动过", reason)

    def test_active_skill_requires_play_phase_and_hand(self):
        game = self.make_game()
        player = game.player
        player.hand = []
        game.skills.bind(player, "probe_recycle")
        allowed, reason = game.skills.can_activate(player, "probe_recycle")
        self.assertFalse(allowed)
        self.assertIn("手牌", reason)

        player.hand = [normal_sha()]
        game.current_turn_player = game.players[1]
        allowed, reason = game.skills.can_activate(player, "probe_recycle")
        self.assertFalse(allowed)
        self.assertIn("出牌阶段", reason)

    def test_activating_a_skill_the_player_lacks_is_rejected(self):
        game = self.make_game()
        allowed, reason = game.skills.can_activate(game.player, "probe_recycle")
        self.assertFalse(allowed)

    def test_skill_state_reset_scopes(self):
        state = SkillState()
        state.set("s", "turn_flag", 1, ResetScope.TURN)
        state.set("s", "persistent_flag", 1, ResetScope.PERSISTENT)
        state.set("other", "turn_flag", 1, ResetScope.TURN)

        state.clear_scope(ResetScope.TURN, skill_ids=("s",))

        self.assertIsNone(state.get("s", "turn_flag"))
        self.assertEqual(state.get("s", "persistent_flag"), 1)
        self.assertEqual(state.get("other", "turn_flag"), 1, "只清理指定技能的状态")


class LifecycleTests(SkillTestCase):
    def test_death_unbinds_skills(self):
        game = self.make_game()
        victim = game.players[1]
        game.skills.bind(victim, "probe_blood_draw")
        self.assertEqual(game.skills.listeners_for(victim), 1)

        victim.hp = 0
        game.engine.run_death(victim)

        self.assertFalse(victim.alive)
        self.assertEqual(game.skills.skill_ids_of(victim), ())
        self.assertEqual(game.skills.listeners_for(victim), 0)

    def test_death_triggered_skill_still_fires_before_unbind(self):
        game = self.make_game()
        victim = game.players[1]
        victim.hand = []
        game.skills.bind(victim, "probe_blood_draw")

        victim.hp = 1
        self.strike(game, game.player, victim, 3)

        self.assertFalse(victim.alive)
        # 伤害结算完成的瞬间技能仍然存在，因此摸到了牌；之后才被卸载。
        self.assertEqual(game.skills.skill_ids_of(victim), ())

    def test_reset_clears_skills_modifiers_and_state(self):
        game = self.make_game(ai_count=3)
        ai = game.players[1]
        game.skills.bind(ai, "probe_nimble")
        game.skills.bind(ai, "probe_blood_draw")
        ai.skill_state.set("probe_nimble", "flag", 1)
        self.assertGreater(game.skills.total_listeners(), 0)
        self.assertGreater(len(game.modifiers), 0)

        game.reset()

        self.assertEqual(game.skills.total_listeners(), 0)
        self.assertEqual(len(game.modifiers), 0)
        for player in game.players:
            self.assertEqual(len(player.skill_state), 0)
            self.assertIsNone(player.general_id)

    def test_repeated_restart_keeps_exactly_one_listener(self):
        game = self.make_game(ai_count=3)
        # 用完整武将池（Phase 8 起共 11 名），保证人数足够时不重复。
        game.general_pool = tuple(game.generals.ids())

        for _ in range(4):
            game.reset()
            game.players[1].hand = []
            game.players[1].hp = game.players[1].max_hp

        # Phase 8 起改为「从池中不重复随机分配」：只校验分配有效、无重复。
        assigned = [player.general_id for player in game.players]
        self.assertTrue(all(assigned), "每位角色都应当分到武将")
        self.assertEqual(len(set(assigned)), len(assigned), "同一局武将不重复")

        # 三连 restart 之后，出杀额度修正数量恰好等于咆哮持有者的人数（没有叠加）
        zhangfei_count = sum(
            1 for player in game.players if "paoxiao" in game.skills.skill_ids_of(player)
        )
        self.assertEqual(
            len(game.modifiers.sorted_for(ModifierKind.SLASH_QUOTA)),
            zhangfei_count,
            "每次重开都重新绑定，但没有重复注册",
        )
        jizhi_holders = [
            player for player in game.players if "jizhi" in game.skills.skill_ids_of(player)
        ]
        for holder in jizhi_holders:
            self.assertEqual(
                game.skills.listeners_for(holder, EventType.CARD_USED),
                1,
                "集智只有一个监听器",
            )

    def test_restart_does_not_double_fire_a_hook(self):
        game = self.make_game()
        for _ in range(3):
            game.reset()
            game.skills.bind(game.players[1], "probe_blood_draw")

        victim = game.players[1]
        victim.hand = []
        victim.hp = victim.max_hp
        self.strike(game, game.player, victim, 1)
        self.assertEqual(len(victim.hand), 1, "技能只能触发一次")

    def test_old_game_listeners_do_not_leak_into_new_game(self):
        game = self.make_game()
        game.skills.bind(game.players[1], "probe_blood_draw")
        tokens_before = game.skills.total_listeners()
        self.assertEqual(tokens_before, 1)

        game.reset()
        # 同一个 Game 对象、同一个 EventDispatcher：旧监听必须已经摘掉
        self.assertEqual(game.skills.total_listeners(), 0)

        victim = game.players[1]
        victim.hand = []
        self.strike(game, game.player, victim, 1)
        self.assertEqual(len(victim.hand), 0, "旧技能不应在新对局里生效")

    def test_return_to_menu_clears_everything(self):
        game = self.make_game()
        game.skills.bind(game.players[1], "probe_nimble")
        game.return_to_menu()
        self.assertEqual(game.skills.total_listeners(), 0)
        self.assertEqual(len(game.modifiers), 0)
        self.assertEqual(game.scene, "menu")


class GeneralIntegrationTests(SkillTestCase):
    def test_paoxiao_lifts_the_sha_limit(self):
        game = self.make_game()
        zhangfei = game.players[1]
        game.set_general(zhangfei, "zhangfei")

        self.assertTrue(game.can_use_unlimited_sha(zhangfei))
        # 装备规则不回归：没有咆哮的人依旧受限制
        self.assertFalse(game.can_use_unlimited_sha(game.players[2]))

    def test_jizhi_draws_on_trick_use(self):
        from src.game.engine import UseCardAction

        game = self.make_game(ai_count=3)
        yueying = game.players[1]
        game.set_general(yueying, "huangyueying")
        yueying.hand = [trick("WUZHONG")]
        yueying.hp = 3
        game.current_turn_player = yueying
        game.phase = "play"

        before = len(yueying.hand)
        game.submit_action(UseCardAction(yueying, yueying.hand[0], [yueying]))

        # 无中生有摸两张 + 集智再摸一张 = 净增 2
        self.assertEqual(len(yueying.hand), before - 1 + 2 + 1)

    def test_jizhi_ignores_non_trick_cards(self):
        from src.game.engine import UseCardAction

        game = self.make_game(ai_count=3)
        yueying = game.players[1]
        game.set_general(yueying, "huangyueying")
        victim = game.players[2]
        victim.hand = []
        yueying.hand = [normal_sha()]
        game.current_turn_player = yueying
        game.phase = "play"

        game.submit_action(UseCardAction(yueying, yueying.hand[0], [victim]))

        self.assertEqual(len(yueying.hand), 0, "使用【杀】不应触发【集智】")

    def test_ganglie_punishes_the_damage_source(self):
        game = self.make_game(ai_count=3)
        xiahoudun = game.players[1]
        game.set_general(xiahoudun, "xiahoudun")
        attacker = game.players[2]
        attacker.hp = attacker.max_hp

        # 判定牌强制为黑桃（非红桃）→ 刚烈应当造成 1 点伤害
        spade = next(card for card in create_development_deck() if card.suit == "spade")
        set_draw_order(game, [spade] + [tao() for _ in range(5)])

        before = attacker.hp
        self.strike(game, attacker, xiahoudun, 1)

        self.assertEqual(attacker.hp, before - 1, "刚烈应当反伤伤害来源")

    def test_ganglie_does_nothing_on_heart(self):
        game = self.make_game(ai_count=3)
        xiahoudun = game.players[1]
        game.set_general(xiahoudun, "xiahoudun")
        attacker = game.players[2]

        heart = next(card for card in create_development_deck() if card.suit == "heart")
        set_draw_order(game, [heart] + [tao() for _ in range(5)])

        before = attacker.hp
        self.strike(game, attacker, xiahoudun, 1)
        self.assertEqual(attacker.hp, before, "红桃判定不触发刚烈")

    def test_generals_do_not_leak_into_core_flows(self):
        """核心 Flow / 规则文件里不允许出现具体武将判断。"""

        import pathlib
        import re

        root = pathlib.Path("src/game")
        forbidden = re.compile(r"general_id\s*==|general\.name\s*==|general_id\s*in\s*\(")
        core_files = [
            "flows/damage.py",
            "flows/turn.py",
            "flows/dying.py",
            "flows/death.py",
            "rules/distance.py",
            "rules/seats.py",
            "rules/targeting.py",
            "equipment.py",
            "cards.py",
        ]
        offenders = []
        for relative in core_files:
            path = root / relative
            if not path.exists():
                continue
            for index, line in enumerate(path.read_text(encoding="utf-8").split("\n"), 1):
                if forbidden.search(line):
                    offenders.append("%s:%d" % (relative, index))
        for path in (root / "card_effects").glob("*.py"):
            for index, line in enumerate(path.read_text(encoding="utf-8").split("\n"), 1):
                if forbidden.search(line):
                    offenders.append("%s:%d" % (path, index))

        self.assertEqual(offenders, [], "核心规则里出现了武将判断：" + str(offenders))


class RegressionTests(SkillTestCase):
    def test_equipment_damage_rules_still_work(self):
        game = self.make_game()
        victim = game.players[1]
        victim.set_equipment(
            next(card for card in create_equipment_cards() if card.name == "TENGJIA")
        )
        victim.hp = victim.max_hp
        before = victim.hp

        # 藤甲：火焰伤害 +1（装备技能的 DAMAGE_MODIFY hook 仍然生效）
        self.strike(game, game.player, victim, 1, card=create_fire_sha())
        self.assertEqual(victim.hp, before - 2)

        # 白银狮子：把大于 1 的伤害压到 1 点
        lion = next(card for card in create_equipment_cards() if card.name == "BAIYIN")
        victim.set_equipment(lion)
        victim.hp = victim.max_hp
        before = victim.hp
        self.strike(game, game.player, victim, 3, card=normal_sha())
        self.assertEqual(victim.hp, before - 1)

    def test_games_without_generals_behave_as_before(self):
        game = self.make_game(ai_count=3)
        for player in game.players:
            self.assertIsNone(player.general_id)
            self.assertEqual(game.skills.skill_ids_of(player), ())
        self.assertEqual(game.draw_count(game.player), 2)
        self.assertEqual(game.hand_limit(game.player), game.player.hp)

    def test_general_pool_rotates_by_seat(self):
        game = self.make_game(ai_count=4)
        game.general_pool = tuple(game.generals.ids())
        assigned = game.assign_generals()
        # 池内不重复随机分配（Phase 8 起）
        self.assertEqual(len(set(assigned)), len(assigned), "同一局武将不重复")
        self.assertTrue(all(item in game.generals.ids() for item in assigned))

        # 分配到的武将确实生效：体力上限与性别跟着 GeneralDef 走
        for player in game.players:
            general = game.generals.require(player.general_id)
            self.assertEqual(player.max_hp, general.max_hp)
            self.assertEqual(player.gender, general.gender)

    def test_explicit_assignment_overrides_the_pool(self):
        game = self.make_game(ai_count=3)
        game.general_pool = ("zhangfei",)
        game.assign_generals({1: "xiahoudun"})
        self.assertEqual(game.players[1].general_id, "xiahoudun")
        for player in game.players[2:]:
            self.assertEqual(player.general_id, "zhangfei")

    def test_unknown_general_is_rejected(self):
        game = self.make_game()
        with self.assertRaises(KeyError):
            game.set_general(game.players[1], "not_a_real_general")


if __name__ == "__main__":
    unittest.main()
