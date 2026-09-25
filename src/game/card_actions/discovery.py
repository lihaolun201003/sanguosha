"""Card Action Discovery：实体牌"现在究竟能做什么"的唯一查询层。

    实体牌 → Action Discovery → Normal / Converted / Response / Rescue
          → VirtualCard → Target Selection → CardEffect → source_cards 移动

UI、AI、响应系统、救援流程都只消费这里返回的 ``CardActionOption``；
没有任何一处再根据 card.name 自行判断能否操作。

性能约定（Hotfix 2 第 71 条）：灰化走轻量的 ``is_operable``（单卡谓词，
不做组合枚举），只有真正点击 / 决策时才做完整 ``actions_for_card``；
多 source 先返回"候选"，选齐后再收窄。

实体牌一律按 **身份**（``is``）比较，绝不使用 ``==`` / ``in``：
``Card`` 是 dataclass，值相等会让两张同名同花色的不同实体牌互相冒充。
"""

from src.game.conversion import (
    EQUIPMENT_ZONE,
    HAND_ZONE,
    PLAY_CONTEXT,
    RESCUE_CONTEXT,
    RESPONSE_CONTEXT,
)

from .context import (
    ActionKind,
    CardActionContext,
    conversion_action_id,
    normal_action_id,
    source_uid,
)
from .option import CardActionOption

EQUIPMENT_SLOTS = ("weapon", "armor", "offensive_horse", "defensive_horse")


def _identity_index(cards):
    return {id(card): card for card in cards}


class CardActionDiscovery:

    def __init__(self, game):
        self.game = game

    # ==================================================
    # Context 构造
    # ==================================================

    def play_context(self, actor):
        return CardActionContext(
            actor=actor,
            context=PLAY_CONTEXT,
            phase=getattr(self.game, "phase", ""),
        )

    def response_context(self, actor, request=None, requirement=None, allowed_names=None):
        if allowed_names is None:
            allowed_names = tuple(getattr(request, "allowed_cards", ()) or ())
        return CardActionContext(
            actor=actor,
            context=RESPONSE_CONTEXT,
            requirement=requirement,
            allowed_names=tuple(allowed_names),
            pending_request=request,
            phase=getattr(self.game, "phase", ""),
        )

    def rescue_context(self, actor, request=None, dying_player=None, allowed_names=("TAO",)):
        return CardActionContext(
            actor=actor,
            context=RESCUE_CONTEXT,
            requirement=(getattr(dying_player, "name", None)),
            allowed_names=tuple(allowed_names),
            pending_request=request,
            phase=getattr(self.game, "phase", ""),
        )

    # ==================================================
    # 区域
    # ==================================================

    def _zone_cards(self, actor, zone):
        if zone == HAND_ZONE:
            return list(getattr(actor, "hand", ()))
        if zone == EQUIPMENT_ZONE:
            equipment = getattr(actor, "equipment", None) or {}
            return [card for card in equipment.values() if card is not None]
        return []

    def source_zones_in_use(self, actor):
        """actor 的转换声明涉及哪些区域（UI 据此决定装备牌能否被点击）。"""

        zones = set()
        for item in self.game.conversions.sorted_items():
            if item.owner is not actor:
                continue
            zones.update(item.conversion.source_zones)
        return zones

    def zone_of(self, actor, card):
        if any(card is item for item in getattr(actor, "hand", ())):
            return HAND_ZONE
        for slot in EQUIPMENT_SLOTS:
            if actor.get_equipment(slot) is card:
                return EQUIPMENT_ZONE
        return None

    # ==================================================
    # 单卡
    # ==================================================

    def actions_for_card(self, actor, card, context):
        """一张实体牌在当前场合下的全部动作（normal 在前，conversion 在后）。"""

        if card is None or getattr(card, "_virtual", False):
            return []

        options = []
        normal = self._normal_option(actor, card, context)
        if normal is not None:
            options.append(normal)
        options.extend(self._conversion_options(actor, (card,), context))
        return self._dedupe(options)

    def view_as_options(self, actor, skill_id, context):
        """某个视为技在当前场合能吃到哪些实体牌（source candidate）。

        View-As 必须先由玩家点技能再选牌，这里是"点完技能之后"的查询：
        返回的选项可能还差 source（多张牌转化），UI 据此高亮合法素材牌。
        """

        result = []
        seen = set()
        for item in self.game.conversions.sorted_items():
            if item.owner is not actor or item.conversion.skill_id != skill_id:
                continue
            conversion = item.conversion
            if context.context not in conversion.contexts:
                continue
            for zone in conversion.source_zones:
                for card in self._zone_cards(actor, zone):
                    if id(card) in seen:
                        continue
                    if not conversion.matches(card):
                        continue
                    if not _conversion_available(conversion, self.game, actor):
                        continue
                    seen.add(id(card))
                    # 正常使用也放进同一个池子：去重的判据是"这次转化的
                    # 结果与正常使用是否完全一样"，池子里少了正常使用那一条，
                    # 判据就永远不成立——【武圣】把一张红色【杀】当【杀】
                    # 会被列成合法素材，玩家点得中、却提交不了。
                    normal = self._normal_option(actor, card, context)
                    if normal is not None:
                        result.append(normal)
                    result.extend(self._conversion_options(actor, (card,), context))
        return [
            option for option in self._dedupe(result)
            if option.skill_id == skill_id and context.allows(option.result_name)
        ]

    def is_operable(self, actor, card, context):
        """灰化判定：还有可达成动作，或能作为某个多 source 转换的候选。"""

        for option in self.actions_for_card(actor, card, context):
            if option.enabled:
                return True
            if option.needs_more_sources and self._has_partner(actor, card, context):
                return True
        return False

    def _has_partner(self, actor, card, context):
        for item in self.game.conversions.sorted_items():
            if item.owner is not actor:
                continue
            conversion = item.conversion
            if conversion.bounds(self.game, actor)[1] < 2:
                continue
            if not conversion.accepts(card, context.context):
                continue
            for zone in conversion.source_zones:
                for other in self._zone_cards(actor, zone):
                    if other is card or not conversion.matches(other):
                        continue
                    if not _conversion_available(conversion, self.game, actor):
                        continue
                    return True
        return False

    # ==================================================
    # 集合
    # ==================================================

    def actions_for_sources(self, actor, cards, context):
        """已选好一组实体牌时的完整动作（多 source 在这里收窄）。"""

        cards = tuple(cards)
        if not cards:
            return []
        options = list(self._conversion_options(actor, cards, context))
        if len(cards) == 1:
            normal = self._normal_option(actor, cards[0], context)
            if normal is not None:
                options.insert(0, normal)
        return self._dedupe(options)

    def actions_in(self, actor, context):
        """当前场合下 actor 全部可操作的实体牌及其动作。"""

        options = []
        seen = set()
        for item in self.game.conversions.sorted_items():
            if item.owner is not actor:
                continue
            for zone in item.conversion.source_zones:
                for card in self._zone_cards(actor, zone):
                    if id(card) in seen:
                        continue
                    seen.add(id(card))
                    options.extend(self._conversion_options(actor, (card,), context))
        # 手牌的正常使用与转换是两件事：转换扫描不会替手牌做 normal 判定。
        for card in self._zone_cards(actor, HAND_ZONE):
            normal = self._normal_option(actor, card, context)
            if normal is not None:
                options.append(normal)
        return self._dedupe(options)

    def usable_options(self, actor, context):
        """enabled 且满足当前 requirement 的动作（响应 / 救援的筛选入口）。"""

        return [
            option for option in self.actions_in(actor, context)
            if option.enabled and context.allows(option.result_name)
        ]

    def respondable_options(self, actor, context):
        """现在**真的能支付并打出**的响应候选。

        两种可用方式：

        * 单个 source 的（实体牌本身、龙胆把【杀】当【闪】）：``enabled`` 就算；
        * 多个 source 的（两张手牌当【闪】这类）：还没凑齐时给的是"候选"，
          只有当这个人**凑得出**那么多张匹配的牌时才算能响应——凑不出来
          （手上只剩一张）就是"实际无法支付"，不能算。

        共享无懈阶段（谁会收到询问）与三种控制器的自动放弃都用这一条判据，
        避免出现"问了一个其实无牌可打的人"。
        """

        result = []
        for option in self.usable_options(actor, context):
            if not option.needs_more_sources:
                result.append(option)
                continue
            if self._sources_assemblable(actor, option):
                result.append(option)
        return result

    def assemblable(self, actor, options):
        """这些候选中至少有一种**现在真的凑得齐**来源。

        视为技入口（``begin_view_as``）用它挡掉"进得去、却永远收不齐"的情况：
        手里只剩一张牌时点丈八蛇矛，不该把玩家送进一个选不满的选牌模式。
        单 source 的候选一律算凑得齐。
        """

        for option in options:
            if not option.needs_more_sources:
                return True
            if self._sources_assemblable(actor, option):
                return True
        return False

    def _sources_assemblable(self, actor, option):
        """这个多 source 方式能不能在这个人的区域里凑齐（数量 + 匹配）。"""

        conversion = self.conversion_of(option)
        if conversion is None:
            return False
        if not _conversion_available(conversion, self.game, actor):
            return False
        need = max(1, int(conversion.bounds(self.game, actor)[0]))
        pool = []
        for zone in conversion.source_zones:
            for card in self._zone_cards(actor, zone):
                if any(card is item for item in pool):
                    continue
                if conversion.matches(card):
                    pool.append(card)
        return len(pool) >= need

    def can_respond(self, actor, *, allowed_names=(), request=None):
        """这个人现在有没有任何合法响应（实体牌或技能转化，判据同上）。"""

        context = self.response_context(
            actor, request=request, allowed_names=tuple(allowed_names or ()))
        return bool(self.respondable_options(actor, context))

    # ==================================================
    # Normal
    # ==================================================

    def _normal_option(self, actor, card, context):
        if context.is_play:
            enabled, reason = self.effect_usable(actor, card)
        else:
            enabled = context.allows(getattr(card, "name", None))
            reason = "" if enabled else "这张牌不能用于当前响应"
        return CardActionOption(
            action_id=normal_action_id(card, context.context),
            kind=ActionKind.NORMAL,
            context=context.context,
            owner=actor,
            source_cards=(card,),
            result_name=getattr(card, "name", ""),
            result_category=getattr(card, "category", "basic"),
            result_subtype=getattr(card, "subtype", None),
            result_nature=getattr(card, "nature", "normal"),
            enabled=enabled,
            disabled_reason=reason,
            requires_targets=(context.is_play
                              and self.effect_requires_targets(card, actor)),
        )

    # ==================================================
    # Conversion
    # ==================================================

    def _conversion_options(self, actor, cards, context):
        options = []
        if not cards or any(getattr(card, "_virtual", False) for card in cards):
            return options

        for item in self.game.conversions.sorted_items():
            if item.owner is not actor:
                continue
            conversion = item.conversion
            if context.context not in conversion.contexts:
                continue
            if not all(conversion.matches(card) for card in cards):
                continue
            if not _conversion_available(conversion, self.game, actor):
                continue

            count = len(cards)
            low, high = conversion.bounds(self.game, actor)
            if count < low:
                options.append(self._build_conversion_option(
                    actor, conversion, cards, context, complete=False))
                continue
            if count > high:
                continue

            chosen = self._choose_sources(actor, conversion, cards)
            if chosen is None:
                continue
            options.append(self._build_conversion_option(
                actor, conversion, chosen, context, complete=True))
        return options

    def _choose_sources(self, actor, conversion, cards):
        """取出正好 low 张 source（不足时从同区域补齐）。"""

        need = conversion.bounds(self.game, actor)[0]
        pool = []
        for card in cards:
            # 同一张实体牌不能重复充当两个 source（必须按身份判断）
            if any(card is item for item in pool):
                continue
            pool.append(card)
        if len(pool) < need:
            for zone in conversion.source_zones:
                for card in self._zone_cards(actor, zone):
                    if len(pool) >= need:
                        break
                    if any(card is item for item in pool):
                        continue
                    if conversion.matches(card):
                        pool.append(card)
        if len(pool) < need:
            return None
        return tuple(pool[:need])

    def _build_conversion_option(self, actor, conversion, cards, context, complete):
        skill_id = conversion.skill_id
        enabled = True
        reason = ""
        low, high = conversion.bounds(self.game, actor)

        if complete:
            if context.is_play:
                virtual = conversion.build(actor, cards)
                enabled, reason = self.effect_usable(actor, virtual)
            elif not context.allows(conversion.name):
                enabled = False
                reason = "这次响应不需要【" + _display(conversion.name) + "】"

        return CardActionOption(
            action_id=conversion_action_id(
                skill_id, cards if complete else (), conversion.name, context.context),
            kind=ActionKind.CONVERSION,
            context=context.context,
            owner=actor,
            source_cards=tuple(cards),
            result_name=conversion.name,
            result_category=conversion.category,
            result_subtype=conversion.subtype,
            result_nature=conversion.nature,
            skill_id=skill_id,
            skill_name=self._skill_name(skill_id),
            enabled=enabled,
            disabled_reason=reason,
            min_sources=low,
            max_sources=high,
            requires_targets=(
                context.is_play and complete
                and self.effect_requires_targets(
                    conversion.build(actor, cards), actor)
            ),
        )

    def _skill_name(self, skill_id):
        definition = self.game.skill_registry.get(skill_id)
        return getattr(definition, "name", "") or skill_id

    # ==================================================
    # 去重（第 42 条）
    # ==================================================

    def _dedupe(self, options):
        """稳定去重：候选动作不去重；同结果 + 同 source 的重复项按语义丢弃。"""

        kept = []
        seen = set()
        for option in options:
            if option is None:
                continue
            if not option.complete:
                kept.append(option)          # 候选动作留给 UI 继续收集 source
                continue
            token = (self._semantic_key(option), option.kind, option.action_id)
            if token in seen:
                continue
            seen.add(token)
            kept.append(option)

        normal_keys = {
            self._semantic_key(option) for option in kept if not option.is_conversion
        }
        pruned = []
        for option in kept:
            if (
                option.is_conversion
                and option.complete
                and self._semantic_key(option) in normal_keys
                and not self._keeps_with_normal(option)
            ):
                continue
            pruned.append(option)
        return pruned

    def _semantic_key(self, option):
        return (
            option.context,
            option.result_name,
            option.result_nature,
            source_uid(option.source_cards),
        )

    def _keeps_with_normal(self, option):
        for item in self.game.conversions.sorted_items():
            conversion = item.conversion
            if conversion.skill_id != option.skill_id or conversion.name != option.result_name:
                continue
            return bool(conversion.keep_with_normal)
        return False

    # ==================================================
    # 结果牌 / 目标 / 校验
    # ==================================================

    def conversion_of(self, option):
        """option 对应的转换声明（找不到返回 None）。"""

        return self._conversion_for(option)

    def _conversion_for(self, option):
        for item in self.game.conversions.sorted_items():
            conversion = item.conversion
            if conversion.skill_id != option.skill_id or conversion.name != option.result_name:
                continue
            return conversion
        return None

    def effective_card(self, option):
        if not option.is_conversion:
            return option.source_cards[0] if option.source_cards else None
        conversion = self._conversion_for(option)
        if conversion is None:
            return None
        return conversion.build(option.owner, option.source_cards)

    def effect_for(self, option):
        card = self.effective_card(option)
        return None if card is None else self.game.engine.card_effects.get(card)

    def effect_requires_targets(self, card, actor=None):
        """这个效果是否需要玩家挑目标。

        ``actor`` 给定时走**动态**规则查询（技能可以改写目标规则）；不给时
        （例如蛊惑拿一张临时探针牌问"这个牌名要不要选目标"）退回静态声明。
        """

        from src.game.rules import TargetRule

        effect = self.game.engine.card_effects.get(card)
        if effect is None:
            return False
        if actor is None:
            rule = getattr(effect, "target_rule", TargetRule.NO_TARGET)
        else:
            rule = self._dynamic_rule(effect, actor, card)
        return rule is not TargetRule.NO_TARGET

    def _dynamic_rule(self, effect, actor, card):
        """目标规则的动态查询入口（与 AvailableActions 同一处口径）。"""

        query = getattr(effect, "target_rule_for", None)
        if callable(query):
            return query(self.game, actor, card)
        from src.game.rules import TargetRule

        return getattr(effect, "target_rule", TargetRule.NO_TARGET)

    def _dynamic_bounds(self, effect, actor, card):
        query = getattr(effect, "target_bounds_for", None)
        if callable(query):
            minimum, maximum = query(self.game, actor, card)
            return int(minimum), int(maximum)
        return (int(getattr(effect, "min_targets", 0) or 0),
                int(getattr(effect, "max_targets", 0) or 0))

    def effect_usable(self, actor, card):
        """结果牌现在是否真的能用：目标 / 次数 / 距离 / 状态全部交给 CardEffect。"""

        from src.game.engine import UseCardAction
        from src.game.rules import TargetRule

        # 酒锁定：喝完酒之后这一张必须是【杀】，否则其它牌整体不可用。
        if self.game.wine_requires_sha(actor) and getattr(card, "name", None) != "SHA":
            return False, "你已经使用【酒】，现在必须使用一张【杀】。"

        if self.game.category_forbidden(actor, card):
            return False, "本回合你不能使用这个类别的牌。"
        effect = self.game.engine.card_effects.get(card)
        if effect is None:
            return False, "这张牌不能主动使用。"

        # 目标规则与数量**一律**走动态查询（技能可以多指定目标）：直接读
        # 静态 target_rule / max_targets 会让"技能放开了上限、发现层却还按
        # 旧值收目标"的半截能力复活。
        rule = self._dynamic_rule(effect, actor, card)
        _minimum, maximum = self._dynamic_bounds(effect, actor, card)
        if rule is TargetRule.NO_TARGET:
            return effect.can_use(self.game, UseCardAction(actor, card, []))
        if rule is TargetRule.SELF:
            targets = [actor] if maximum >= 1 else []
            return effect.can_use(self.game, UseCardAction(actor, card, targets))

        candidates = self.target_candidates(actor, rule)
        if rule in (TargetRule.ALL_PLAYERS, TargetRule.ALL_OTHERS):
            return effect.can_use(self.game, UseCardAction(actor, card, list(candidates)))

        last_reason = ""
        for target in candidates:
            ok, reason = effect.can_use(self.game, UseCardAction(actor, card, [target]))
            if ok:
                return True, ""
            last_reason = reason
        return False, (last_reason or "没有合法目标")

    def target_candidates(self, actor, rule, card=None):
        from src.game.rules import target_candidates

        return list(target_candidates(self.game, actor, rule, card=card))

    def legal_targets(self, actor, option):
        """这个 Action 现在可以选哪些目标（UI 高亮 / AI 决策共用）。"""

        from src.game.engine import UseCardAction
        from src.game.rules import TargetRule

        card = self.effective_card(option)
        if card is None:
            return []
        effect = self.game.engine.card_effects.get(card)
        if effect is None:
            return []
        rule = self._dynamic_rule(effect, actor, card)
        if rule is TargetRule.NO_TARGET:
            return []
        if rule is TargetRule.SELF:
            return [actor]
        candidates = self.target_candidates(actor, rule, card=card)
        if rule in (TargetRule.ALL_PLAYERS, TargetRule.ALL_OTHERS):
            return candidates
        result = []
        for target in candidates:
            ok, _reason = effect.can_use(self.game, UseCardAction(actor, card, [target]))
            if ok:
                result.append(target)
        return result

    def validate(self, option, sources=None, targets=None, context=None):
        """提交前二次校验（第 37 条）：任何一条不成立就拒绝并给出原因。"""

        actor = option.owner
        cards = tuple(sources if sources is not None else option.source_cards)

        if not cards:
            return False, "还没有选择要使用的牌。"
        if not getattr(actor, "alive", True):
            return False, "你已经不能行动了。"

        # 1) source 仍属于该玩家，且区域被这个技能允许
        for card in cards:
            zone = self.zone_of(actor, card)
            if zone is None:
                return False, "这张牌已经不在你的手牌或装备区。"
            if not self._zone_allowed(option, zone):
                return False, "这张牌所在区域不能用于此技能。"

        # 2) 技能仍在 + 3) 转换谓词仍成立
        if option.is_conversion:
            if self.game.skill_registry.get(option.skill_id) is None:
                return False, "技能已经不存在。"
            if not self.game.skills.has(actor, option.skill_id):
                return False, "技能已经不存在。"
            if not self._conversion_accepts(option, cards):
                return False, "这些牌已经不能转换了。"

        # 4) 场合仍然接受这个结果牌
        if option.context == PLAY_CONTEXT:
            if getattr(self.game, "game_over", False):
                return False, "游戏已经结束。"
        else:
            allowed = None
            if context is not None:
                allowed = context.allowed_names
            if allowed is None:
                request = getattr(self.game, "pending_request", None)
                allowed = tuple(getattr(request, "allowed_cards", ()) or ())
            if allowed and option.result_name not in allowed:
                return False, "这次响应已经不需要这张牌了。"

        # 5) 结果牌自身仍然合法
        card = self.effective_card(option)
        if card is None:
            return False, "无法构造这次要使用的牌。"

        if option.context == PLAY_CONTEXT:
            ok, reason = self.effect_usable(actor, card)
            if not ok:
                return False, reason
            # 6) 目标仍然合法
            if targets is not None:
                effect = self.game.engine.card_effects.get(card)
                if effect is not None:
                    from src.game.engine import UseCardAction

                    ok, reason = effect.can_use(
                        self.game, UseCardAction(actor, card, list(targets)))
                    if not ok:
                        return False, reason
        return True, ""

    def _zone_allowed(self, option, zone):
        if not option.is_conversion:
            return zone == HAND_ZONE
        conversion = self._conversion_for(option)
        if conversion is None:
            return zone == HAND_ZONE
        return conversion.uses_zone(zone)

    def _conversion_accepts(self, option, cards):
        conversion = self._conversion_for(option)
        if conversion is None:
            return False
        return conversion.accepts_many(
            cards, option.context, game=self.game, owner=option.owner)


def _display(name):
    from src.card import display_name_for

    return display_name_for(name)


def _conversion_available(conversion, game, actor):
    """转换的时机条件（急救：回合外才可把红色牌当【桃】）。"""

    condition = getattr(conversion, "available", None)
    if condition is None:
        return True
    return bool(condition(game, actor))
