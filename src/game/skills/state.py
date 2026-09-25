"""Skill state storage: one namespace per skill on each player.

Skills must not keep adding ad-hoc fields to ``Player``.  Everything a skill
needs to remember (本回合是否发动过 / 使用次数 / 标记 / 限定技状态) lives in
``player.skill_state`` under the skill's own id, with explicit reset scopes.
"""

from enum import Enum


class ResetScope(str, Enum):
    """When a stored value is cleared."""

    TURN = "turn"        # 每回合开始
    PHASE = "phase"      # 每个阶段开始
    ROUND = "round"      # 每一轮（所有角色各行动一次）
    PERSISTENT = "persistent"   # 直到技能解绑 / 对局重置


class SkillState:
    """Per-player, per-skill state bag with scope-aware clearing."""

    def __init__(self):
        self._values = {}
        self._scopes = {}

    # ==================================================
    # 读写
    # ==================================================

    def get(self, skill_id, key, default=None):
        return self._values.get((skill_id, key), default)

    def set(self, skill_id, key, value, scope=ResetScope.PERSISTENT):
        self._values[(skill_id, key)] = value
        self._scopes[(skill_id, key)] = ResetScope(scope)

    def add(self, skill_id, key, amount=1, scope=ResetScope.PERSISTENT):
        current = self.get(skill_id, key, 0) or 0
        self.set(skill_id, key, current + amount, scope)
        return self.get(skill_id, key)

    def clear(self, skill_id, key=None):
        if key is None:
            for item in [item for item in self._values if item[0] == skill_id]:
                self._values.pop(item, None)
                self._scopes.pop(item, None)
            return
        self._values.pop((skill_id, key), None)
        self._scopes.pop((skill_id, key), None)

    def drop_skill(self, skill_id):
        """Called when a skill is unbound: its state disappears with it."""

        self.clear(skill_id)

    # ==================================================
    # 生命周期
    # ==================================================

    def clear_scope(self, scope, skill_ids=None):
        scope = ResetScope(scope)
        for item, item_scope in list(self._scopes.items()):
            if item_scope is not scope:
                continue
            if skill_ids is not None and item[0] not in skill_ids:
                continue
            self._values.pop(item, None)
            self._scopes.pop(item, None)

    def clear_all(self):
        self._values.clear()
        self._scopes.clear()

    # ==================================================
    # 查询
    # ==================================================

    def snapshot(self, skill_id=None):
        if skill_id is None:
            return dict(self._values)
        return {key: value for (owner, key), value in self._values.items() if owner == skill_id}

    def __len__(self):
        return len(self._values)
