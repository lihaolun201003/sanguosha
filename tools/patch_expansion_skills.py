"""一次性补丁：修正神将/一将成名技能的工厂类 id 与新增的【杀】距离查询。

运行方式（在项目根目录）：
    .venv/Scripts/python.exe tools/patch_expansion_skills.py
"""

import io
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def patch(relative_path, pairs):
    path = os.path.join(ROOT, *relative_path.split("/"))
    text = io.open(path, encoding="utf-8").read()
    for old, new in pairs:
        if old not in text:
            print("  跳过（未命中）：" + relative_path + " :: " + old.strip().splitlines()[0][:60])
            continue
        text = text.replace(old, new, 1)
    io.open(path, "w", encoding="utf-8").write(text)
    print("已更新 " + relative_path)


# 1) 新的 ModifierKind：杀无距离限制
patch("src/game/skills/modifiers.py", [(
    '    SLASH_FORBIDDEN = "slash_forbidden"\n',
    '    SLASH_FORBIDDEN = "slash_forbidden"\n'
    '    # 【杀】无距离限制：归属使用者（武神）。condition 判断具体是哪张【杀】。\n'
    '    SLASH_DISTANCE_IGNORE = "slash_distance_ignore"\n',
)])

# 2) Game 查询
patch("src/game/core.py", [(
    "    def slash_forbidden(self, player, card=None):",
    '''    def slash_ignores_distance(self, player, card=None):
        """这张【杀】是否无视距离限制（武神）。"""

        if not hasattr(self, "modifiers"):
            return False
        query = {"player": player, "card": card}
        for modifier in self.modifiers.sorted_for(ModifierKind.SLASH_DISTANCE_IGNORE):
            if not modifier.matches(self, query):
                continue
            value = modifier.value
            if callable(value):
                if value(self, query):
                    return True
            elif value:
                return True
        return False

    def slash_forbidden(self, player, card=None):''',
)])

# 3) ShaEffect 尊重它
patch("src/game/card_effects/sha.py", [(
    """        # 多目标时逐个校验距离：只有第一个目标在范围内是不够的。
        for target in action.targets:
            if not DistanceRule.in_attack_range(game, actor, target):
                return False, "攻击距离不足。"
        return True, \"\"""",
    """        # 多目标时逐个校验距离：只有第一个目标在范围内是不够的。
        # 「无距离限制」走规则层查询（武神），不在这里认具体技能。
        if not game.slash_ignores_distance(actor, action.card):
            for target in action.targets:
                if not DistanceRule.in_attack_range(game, actor, target):
                    return False, "攻击距离不足。"
        return True, \"\"""",
)])

# 4) god.py：无前合并成一个技能；极略清理类 id 对齐
patch("src/game/skills/expansions/god.py", [
    (
        '''def _juejing_draw(game, query):
    """绝境：摸牌阶段摸已损失体力值 +2 张牌。"""

    player = query.get("player")
    if player is None:
        return 0
    return lost_hp_safe(player) + 2 - 2      # DRAW_COUNT 是"在基础 2 张之上"的增量


def lost_hp_safe(player):''',
        "def lost_hp_safe(player):",
    ),
    (
        '''    if not game.skills.has(player, "wuqian_cleanup"):
        # 清理由 SkillManager 的回合 scope 自动完成：这里只需要在回合结束时
        # 撤掉临时技能，见 WuqianCleanup。
        pass
    return True''',
        "    return True",
    ),
    (
        '''class WuqianCleanup(Skill):
    """回合结束时撤掉【无前】临时获得的【无双】。"""

    id = "wuqian_cleanup"
    name = "无前（清理）"''',
        '''class Wuqian(Skill):
    """无前：两件事绑在同一个技能实例上——临时【无双】的回收，以及
    "指定角色防具无效"的伤害修正。

    工厂类的 ``id`` 必须与 SkillDef 的 ``id`` 一致：``SkillManager.unbind``
    按 ``instance.id`` 匹配，对不上就永远卸载不掉（监听会留在事件总线上）。
    """

    id = "wuqian"
    name = "无前"''',
    ),
    (
        '''    def bindings(self):
        return (SkillBinding(EventType.TURN_END, priority=-80),)

    def can_trigger(self, context, event):
        return (event.source is self.owner
                and self.owner.skill_state.get("wuqian", "target_id", None) is not None)

    def resolve(self, context, event):
        game = context.state
        true_owner = getattr(self.owner, "general_id", None)
        if true_owner is None:
            return
        if game.skills.has(self.owner, "wushuang"):
            # 只撤掉"由无前临时给的那一份"：原武将本来就有的【无双】
            # （SP 吕布形态）不能被误删，因此按"是不是本体技能"判断。
            if "wushuang" not in (game.generals.get(true_owner).skill_ids or ()):
                game.skills.unbind(self.owner, "wushuang")
        self.owner.skill_state.clear("wuqian", "target_id")


class WuqianArmor(Skill):
    """无前：你指定的角色防具无效，直到回合结束。"""

    id = "wuqian_armor"
    name = "无前（无视防具）"

    def bindings(self):
        return (SkillBinding(EventType.DAMAGE_MODIFY, priority=60),)

    def can_trigger(self, context, event):
        damage = event.payload.get("damage")
        if damage is None or damage.source is not self.owner:
            return False
        if self.owner.skill_state.get("wuqian", "target_id", 0) != id(damage.target):
            return False
        return context.state.armor_card(damage.target) is not None

    def resolve(self, context, event):
        damage = event.payload["damage"]
        damage.ignore_armor = True
        damage.effects.append("【无前】防具无效")''',
        '''    def bindings(self):
        return (
            SkillBinding(EventType.TURN_END, priority=-80),
            SkillBinding(EventType.DAMAGE_MODIFY, priority=60),
        )

    def can_trigger(self, context, event):
        if event.name is EventType.TURN_END:
            return (event.source is self.owner
                    and self.owner.skill_state.get("wuqian", "target_id", None) is not None)
        damage = event.payload.get("damage")
        if damage is None or damage.source is not self.owner:
            return False
        if self.owner.skill_state.get("wuqian", "target_id", 0) != id(damage.target):
            return False
        return context.state.armor_card(damage.target) is not None

    def resolve(self, context, event):
        game = context.state
        if event.name is EventType.DAMAGE_MODIFY:
            damage = event.payload["damage"]
            damage.ignore_armor = True
            damage.effects.append("【无前】防具无效")
            return
        # 回合结束：撤掉【无前】临时给的【无双】。原本就会【无双】的形态
        # （SP008 吕布）不能被误删，按"是不是本体的武将技能"判断。
        general = game.generals.get(getattr(self.owner, "general_id", None))
        if (game.skills.has(self.owner, "wushuang")
                and "wushuang" not in (getattr(general, "skill_ids", ()) or ())):
            game.skills.unbind(self.owner, "wushuang")
        self.owner.skill_state.clear("wuqian", "target_id")''',
    ),
    ("        factory=WuqianCleanup,", "        factory=Wuqian,"),
    (
        '''    triggered(
        "wuqian_armor",
        "无前（防具无效）",
        "【无前】指定的角色在你本回合内防具无效。",
        factory=WuqianArmor,
        kind=SkillKind.LOCKED,
    ),
''',
        "",
    ),
    (
        '''class JilueCleanup(Skill):
    """回合结束时撤掉【极略】临时获得的技能。"""

    id = "jilue_cleanup"
    name = "极略（清理）"''',
        '''class JilueCleanup(Skill):
    """回合结束时撤掉【极略】临时获得的技能。

    ``id`` 与 SkillDef 的 ``jilue`` 一致——``SkillManager.unbind`` 按实例的
    ``id`` 匹配，不一致就卸载不掉。
    """

    id = "jilue"
    name = "极略"''',
    ),
])

# 5) yijiang 酒诗的工厂类 id 对齐
patch("src/game/skills/expansions/yijiang.py", [(
    '''class JiushiBack(Skill):
    """背面朝上时受到伤害，可在伤害结算后翻回正面。"""

    id = "jiushi_back"
    name = "酒诗（翻回）"''',
    '''class JiushiBack(Skill):
    """酒诗：背面朝上时受到伤害，可在伤害结算后翻回正面。

    工厂类的 ``id`` 必须与 SkillDef 的 ``jiushi`` 一致，否则技能卸载不掉。
    """

    id = "jiushi"
    name = "酒诗"''',
)])

print("补丁应用完成")
