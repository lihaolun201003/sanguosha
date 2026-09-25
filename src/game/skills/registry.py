"""Skill registry (definitions) and skill manager (per-player binding).

Definitions are shared and owner-free; binding creates one runtime ``Skill``
instance per (player, skill) pair, so two players holding the same skill never
share state or subscriptions.
"""

import copy
from typing import Any, Dict, List, Optional, Tuple

from ..engine.skills import Skill
from .definitions import SkillDef, SkillKind
from .modifiers import Modifier, ModifierRegistry
from .state import ResetScope, SkillState


class SkillRegistry:
    """技能定义表：只描述技能是什么，不持有任何拥有者。"""

    def __init__(self):
        self._defs: Dict[str, SkillDef] = {}

    def register(self, definition):
        if not isinstance(definition, SkillDef):
            raise TypeError("SkillRegistry.register expects a SkillDef")
        if definition.id in self._defs:
            raise ValueError("skill already registered: " + definition.id)
        self._defs[definition.id] = definition
        return definition

    def register_all(self, definitions):
        for definition in definitions:
            self.register(definition)
        return self

    def get(self, skill_id):
        return self._defs.get(skill_id)

    def require(self, skill_id):
        definition = self.get(skill_id)
        if definition is None:
            raise KeyError("unknown skill: " + str(skill_id))
        return definition

    def list_skills(self):
        return tuple(self._defs.values())

    def ids(self):
        return tuple(self._defs)

    def for_general(self, general_id):
        return tuple(
            definition
            for definition in self._defs.values()
            if definition.general_id == general_id
        )

    def __contains__(self, skill_id):
        return skill_id in self._defs

    def __len__(self):
        return len(self._defs)


class SkillManager:
    """把技能绑定到具体玩家，并统一管理生命周期与主动技能入口。"""

    # 死亡后默认不再触发的技能标签（用于未来保留"死亡时结算"类技能）
    KEEP_ON_DEATH = ("death_settlement",)

    def __init__(self, game, registry=None):
        self.game = game
        self.registry = registry if registry is not None else SkillRegistry()
        self._instances: Dict[str, List[Skill]] = {}
        self._bound: Dict[str, List[str]] = {}
        self._active: Dict[str, Dict[str, SkillDef]] = {}

    # ==================================================
    # 绑定 / 解绑
    # ==================================================

    def bind(self, player, skill_id, *, definition=None):
        """给玩家绑定一个技能；重复绑定会被拒绝（防止 listener 叠加）。"""

        definition = definition or self.registry.require(skill_id)
        instances = self._instances.setdefault(player.player_id, [])
        if any(instance.id == definition.id for instance in instances):
            raise ValueError(
                "skill already bound to " + player.name + ": " + definition.id
            )
        if definition.is_active and definition.id in self._active.get(player.player_id, {}):
            raise ValueError(
                "active skill already bound to " + player.name + ": " + definition.id
            )

        if definition.factory is not None:
            instance = definition.factory(player)
            instance.owner = player
            instance.install(self.game.context)
            instances.append(instance)

        for spec in definition.modifiers:
            self.game.modifiers.register(
                Modifier(
                    kind=spec.kind,
                    value=spec.value,
                    owner=player,
                    skill_id=definition.id,
                    priority=spec.priority,
                    roles=spec.roles,
                    condition=spec.condition,
                )
            )

        for conversion in definition.conversions:
            # 绑定时刻把"需要 owner 的谓词"折进 matches（见
            # CardConversion.for_owner）：绑定之后的每一次查询都自带这名
            # 角色的状态，UI 与引擎不会再各判一次。
            self.game.conversions.register(
                conversion.for_owner(player, self.game), player)

        if definition.is_active:
            self._active.setdefault(player.player_id, {})[definition.id] = definition

        # 记录全部已绑定技能（含只有 modifier 的锁定技），供 UI 与查询使用。
        self._bound.setdefault(player.player_id, []).append(definition.id)
        return definition

    def bind_general(self, player):
        """按武将定义绑定它的全部技能（按 general.skill_ids 的顺序）。

        主公技只在"当前角色是主公"时才会绑定：FFA 没有主公，即使选了刘备 /
        孙权也不会获得主公技；身份模式下也只有主公本人获得。
        """

        general = self.game.generals.get(player.general_id)
        if general is None:
            return ()
        bound = []
        for skill_id in general.skill_ids:
            definition = self.registry.get(skill_id)
            if (definition is not None and definition.is_lord_skill
                    and not self.lord_skills_enabled(player)):
                continue
            self.bind(player, skill_id)
            bound.append(skill_id)
        return tuple(bound)

    def lord_skills_enabled(self, player):
        """主公技的启用条件：身份模式 + 该角色是主公。"""

        mode = getattr(self.game, "mode", None)
        if mode is None or not getattr(mode, "uses_identities", False):
            return False
        from ..identity import Identity

        return getattr(player, "identity", None) is Identity.LORD

    @staticmethod
    def _state_of(player):
        """Player 的状态容器由 Game 注入；手工构造的 Player 可能没有它。"""

        return getattr(player, "skill_state", None)

    def unbind(self, player, skill_id):
        instances = self._instances.get(player.player_id, [])
        for instance in list(instances):
            if instance.id != skill_id:
                continue
            instance.uninstall(self.game.context)
            instances.remove(instance)
        self._active.get(player.player_id, {}).pop(skill_id, None)
        bound = self._bound.get(player.player_id)
        if bound and skill_id in bound:
            bound.remove(skill_id)
        self.game.modifiers.unregister_owner_skill(player, skill_id)
        self.game.conversions.unregister_owner_skill(player, skill_id)
        state = self._state_of(player)
        if state is not None:
            state.drop_skill(skill_id)
        return True

    def unbind_all(self, player):
        instances = self._instances.pop(player.player_id, [])
        for instance in instances:
            instance.uninstall(self.game.context)
        self._active.pop(player.player_id, None)
        self._bound.pop(player.player_id, None)
        self.game.modifiers.unregister_owner(player)
        self.game.conversions.unregister_owner(player)
        state = self._state_of(player)
        if state is not None:
            state.clear_all()
        return len(instances)

    def clear(self):
        """卸载全部技能（reset / 返回主菜单 / 重新开局）。"""

        for player_id, instances in list(self._instances.items()):
            for instance in instances:
                instance.uninstall(self.game.context)
            instances.clear()
        self._instances.clear()
        self._bound.clear()
        self._active.clear()
        self.game.modifiers.clear()
        self.game.conversions.clear()
        Skill.reset_depth()
        for player in self.game.players:
            state = self._state_of(player)
            if state is not None:
                state.clear_all()

    def on_player_death(self, player):
        """死亡结算之后调用：默认卸载该玩家的技能。

        DeathFlow 会在发出 DEATH 事件之后才调用这里，因此"死亡时触发"
        类技能仍然有机会响应；带 KEEP_ON_DEATH 标签的技能会被保留。
        """

        instances = self._instances.get(player.player_id, [])
        kept = []
        for instance in instances:
            definition = self.registry.get(instance.id)
            tags = definition.tags if definition is not None else ()
            if any(tag in tags for tag in self.KEEP_ON_DEATH):
                kept.append(instance)
                continue
            instance.uninstall(self.game.context)
        if kept:
            self._instances[player.player_id] = kept
        else:
            self._instances.pop(player.player_id, None)

        kept_ids = [instance.id for instance in kept]
        self._bound[player.player_id] = kept_ids
        if not kept_ids:
            self._bound.pop(player.player_id, None)

        for skill_id in list(self._active.get(player.player_id, {})):
            definition = self.registry.get(skill_id)
            if definition is None or not any(tag in definition.tags for tag in self.KEEP_ON_DEATH):
                self._active[player.player_id].pop(skill_id, None)
        if not self._active.get(player.player_id):
            self._active.pop(player.player_id, None)

        self.game.modifiers.unregister_owner(player)
        self.game.conversions.unregister_owner(player)
        state = self._state_of(player)
        if state is not None:
            state.clear_all()
        return len(kept)

    # ==================================================
    # 查询
    # ==================================================

    def skills_of(self, player):
        return tuple(self._instances.get(player.player_id, ()))

    def skill_ids_of(self, player):
        """该角色当前拥有的全部技能（触发式 / 锁定技 / 主动技）。"""

        return tuple(self._bound.get(player.player_id, ()))

    def has(self, player, skill_id):
        return skill_id in self._bound.get(player.player_id, ())

    def judge_replacers(self, judged_player, reason=None):
        """能替换这次判定的角色，按座次（从判定角色开始）排序。

        顺序只取决于座次与技能注册顺序，不依赖任何字典遍历顺序。
        """

        result = []
        for player in self.game.seats.alive_players_in_order(
            start_after=judged_player, include_start=True
        ):
            for skill_id in self.skill_ids_of(player):
                definition = self.registry.get(skill_id)
                if definition is None or definition.judge_replacement is None:
                    continue
                result.append((player, definition))
        return result

    def phase_offers(self, player, phase):
        """该角色在某个阶段可以发动替代的技能：[(player, definition)]。"""

        offers = []
        for skill_id in self.skill_ids_of(player):
            definition = self.registry.get(skill_id)
            if definition is None or definition.phase_replacement is None:
                continue
            if definition.phase_replacement.phase is not phase:
                continue
            offers.append((player, definition))
        return offers

    def active_skill_ids(self, player):
        return tuple(self._active.get(player.player_id, ()))

    def view_as_skill_ids(self, player):
        """该玩家拥有的视为技 id（龙胆 / 武圣一类）。"""

        result = []
        for skill_id in self._bound.get(player.player_id, ()):
            definition = self.registry.get(skill_id)
            if definition is not None and definition.is_view_as:
                result.append(skill_id)
        return tuple(result)

    def listeners_for(self, player, event_name=None):
        """测试与诊断用：该玩家当前挂在事件总线上的监听数量。"""

        count = 0
        for instance in self._instances.get(player.player_id, ()):
            if event_name is None:
                count += len(instance._subscription_tokens)
                continue
            for binding in instance.bindings():
                if binding.event_name == event_name:
                    count += 1
        return count

    def total_listeners(self):
        return sum(len(instance._subscription_tokens) for instances in self._instances.values() for instance in instances)

    # ==================================================
    # 主动技能
    # ==================================================

    def can_activate(self, player, skill_id):
        definition = self.registry.get(skill_id)
        if definition is None or definition.activate is None:
            return False, "技能不存在或不能主动发动"
        if not self.has(player, skill_id):
            return False, "该角色没有这个技能"
        if definition.can_activate is None:
            return True, ""
        result = definition.can_activate(self.game, player)
        if isinstance(result, tuple):
            return bool(result[0]), str(result[1])
        return bool(result), ""

    def activatable_skills(self, player):
        return tuple(
            skill_id
            for skill_id in self.active_skill_ids(player)
            if self.can_activate(player, skill_id)[0]
        )

    def activate(self, player, skill_id, **params):
        """直接发动（AI 或已收集好参数的调用方）。

        与 UI 的 ActivateSkillAction 走完全相同的校验 / 支付 / 结算路径，
        因此两边的规则行为不会分叉。
        """

        from ..engine.domain_actions import ActivateSkillAction
        from .activation import resolve_activation

        cards = list(params.get("cards") or ())
        if params.get("card") is not None:
            cards.append(params["card"])
        action = ActivateSkillAction(
            player,
            skill_id,
            target=params.get("target"),
            cards=cards,
        )
        ok, message = resolve_activation(self.game.engine, action)
        return (True, None) if ok else (False, message)
