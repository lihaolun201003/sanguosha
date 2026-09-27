"""Information-safe free-for-all AI controller.

The AI submits GameActions exactly like the human seat does, so every rule
stays in the engine.  It may read other characters' HP, hand *count*,
equipment and judgement zone, but never the contents of another player's hand.
"""

import random

from src.actions import CallbackAction, WaitAction

from src.game.engine import (
    ChooseOptionAction,
    ConfirmPendingAction,
    PassPendingAction,
    RespondCardAction,
    SelectCardsAction,
    SelectTargetsAction,
    UseCardAction,
)
from src.game.engine.pending import PendingRequestType
from src.game.rules import TargetRule

from .base import PlayerController


# 自由混战没有队友：AI 只救自己。
SELF_RESCUE_NAMES = ("TAO", "JIU")
# 对全场（含自己）有利的牌，AI 不会用无懈可击去抵消。
BENEFICIAL_TRICKS = {"WUZHONG", "TAOYUAN", "WUGU"}
# 「选一个人给他好处」的请求（节命补牌一类）：目标必须挑自己或自己人，
# 通用分支取候选前 N 个会直接把牌补给对手。
BENEFIT_REASONS = frozenset({"jieming"})


def _cost_combo_ok(validator, game, player, cards):
    """技能声明的费用组合校验（``cost_validator``）的一个答案。

    与引擎侧（``activation._as_result``）读同一个钩子、同一个契约：
    ``(ok, reason)`` 或裸 ``bool`` 两种返回形态都接受。AI 不在这里重复任何
    规则——"两张必须同花色"这类判断只存在于技能自己的声明里。
    """

    result = validator(game, player, list(cards))
    if isinstance(result, tuple):
        return bool(result[0])
    return bool(result)


def _predicate_ok(value):
    """技能谓词的两种返回形态：``bool`` 或 ``(bool, reason)``。"""

    if isinstance(value, tuple):
        return bool(value[0])
    return bool(value)


class _PendingNoRequest:
    """空的请求栈（谓词只读 ``current`` / ``active`` 也是安全的）。"""

    current = None
    active = False


class _EngineNoRequest:
    def __init__(self, engine):
        self._engine = engine
        self.pending = _PendingNoRequest()

    def __getattr__(self, name):
        return getattr(self._engine, name)


class _NoRequestView:
    """反事实对照用的**只读**视图：把"待回答的请求"报告成"没有请求"。

    **不改动任何真实状态**（不摘请求栈、不发事件、不移动牌），只把 ``game``
    包一层，让技能的可用性判据看到"这条响应请求不存在"的世界。技能的
    ``can_activate(game, player)`` 契约本来就是"只读传进来的 game 与 player"，
    所以这份视图对谓词是合法输入。
    """

    def __init__(self, game):
        self._game = game
        self.engine = _EngineNoRequest(getattr(game, "engine", None))

    def __getattr__(self, name):
        return getattr(self._game, name)


class AIController(PlayerController):

    MAX_CARDS_PER_TURN = 10

    # 连续出牌之间的观察停顿（会被全局节奏倍率缩放）。
    CARD_PAUSE = 0.35

    CARD_VALUES = {
        "TAO": 10,
        "JIU": 6,
        "SHA": 6,
        "SHAN": 5,
        "WUXIE": 8,
        "WUZHONG": 7,
        "GUOHE": 6,
        "SHUNSHOU": 8,
        "JUEDOU": 5,
        "NANMAN": 6,
        "WANJIAN": 6,
        "TAOYUAN": 7,
        "WUGU": 7,
        "JIEDAO": 4,
        "LEBU": 7,
        "SHANDIAN": 5,
        "HUOGONG": 6,
        "TIESUO": 4,
        "BINGLIANG": 7,
    }

    def __init__(self, game, player, rng=None):
        super().__init__(game, player)
        self.rng = rng if rng is not None else random.Random()
        self._cards_played = 0
        self._failed_cards = set()
        self._used_skills_this_turn = set()
        #: 已经用技能试过的响应请求 id（一条请求只发动一次技能，见
        #: ``_respond_with_skill``）。
        self._skill_response_tried = set()

    # ==================================================
    # 估值与信息边界
    # ==================================================

    def card_value(self, card):
        """估值只用于自己的手牌与全场公开牌。"""

        if card.name in self.CARD_VALUES:
            return self.CARD_VALUES[card.name]
        if card.category == "equipment":
            if card.subtype == "weapon":
                return 4 + card.attack_range
            return 5
        return 4

    def score_target(self, target, card):
        score = (target.max_hp - target.hp) * 20 - target.hp * 3
        if card.name in {"GUOHE", "SHUNSHOU"}:
            score += len(target.hand) * 4
            score += sum(value is not None for value in target.equipment.values()) * 3
        if card.name == "HUOGONG" and target.hand:
            score += 10
        if card.name == "SHA":
            score += len(target.hand) * 1.5 - target.hp * 0.5
        score += self.identity_bias(target, card)
        # 立场：把"谁该打"从"谁血少"提升到身份层面（身份模式专用）。
        score += self.stance_bias(target, card)
        # 打破平局：否则所有 AI 永远围殴座次最前的那一个角色。
        score += self.rng.random() * 6
        return score

    # ==================================================
    # 立场：按身份与公开行为决定打谁
    # ==================================================

    def stance_bias(self, target, card=None):
        """由公开行为推断出的敌意倾向；只在身份模式生效。

        只用公开信息：已经公开的身份（主公 / 阵亡者）与桌面上人人可见的
        出手记录（谁打过谁）。隐藏身份永远不参与打分，所以 AI 依然不作弊。

        返回正数 = 更该打，负数 = 尽量别打；量级与 identity_bias 一致，
        足以压过"谁血少"这类局部判断，但不会大到无视眼前局面。
        """

        from src.game.identity import Identity

        mode = getattr(self.game, "mode", None)
        if mode is None or not getattr(mode, "uses_identities", False):
            return 0.0
        if target is self.player or not getattr(target, "alive", True):
            return 0.0
        own = getattr(self.player, "identity", None)
        if own is None:
            return 0.0
        visible = mode.public_identity_of(target)
        lord = mode.lord()

        if own is Identity.LORD:
            return self._lord_stance(mode, target, lord, visible)
        if own is Identity.LOYALIST:
            return self._loyalist_stance(mode, target, lord, visible)
        if own is Identity.REBEL:
            return self._rebel_stance(mode, target, lord, visible)
        return self._renegade_stance(mode, target, lord, visible)

    def _lord_stance(self, mode, target, lord, visible):
        """主公：生存优先、稳住盘面——只反击已经出手的人，不乱杀无辜。"""

        from src.game.identity import Identity

        if visible is Identity.LOYALIST:
            return -70.0
        if visible in (Identity.REBEL, Identity.RENEGADE):
            return 40.0
        if mode.has_struck(target, self.player):
            return 55.0                   # 打过我的人：果断反击
        # 忠臣的明跳信号：帮我打敌人的，就是自家人，绝不能误伤。
        enemies = [
            player for player in self.game.get_alive_players()
            if player is not self.player and self._is_known_enemy(mode, player)
        ]
        if any(mode.has_struck(target, enemy) for enemy in enemies):
            return -30.0
        if not mode.ever_struck_anyone(target):
            return -25.0                  # 还没表明立场的人：暂不动手
        return 0.0

    def _is_known_enemy(self, mode, player):
        """已知的敌人：公开的反贼 / 内奸，或已经打过主公的人。"""

        from src.game.identity import Identity

        visible = mode.public_identity_of(player)
        if visible in (Identity.REBEL, Identity.RENEGADE):
            return True
        lord = mode.lord()
        return lord is not None and mode.has_struck(player, lord)

    def _loyalist_stance(self, mode, target, lord, visible):
        """忠臣：明跳护主——谁打主公，我就打谁。"""

        from src.game.identity import Identity

        if target is lord:
            return -120.0
        if visible is Identity.LOYALIST:
            return -50.0
        if visible is Identity.REBEL:
            return 45.0
        if lord is not None and mode.has_struck(target, lord):
            return 55.0                   # 打过主公的人：明跳集火
        # 同类识别：和我打同一个敌人的，是自家人，别互相消耗。
        enemies = [
            player for player in self.game.get_alive_players()
            if player is not self.player and self._is_known_enemy(mode, player)
        ]
        if any(mode.has_struck(target, enemy) for enemy in enemies):
            return -25.0
        if mode.has_struck(target, self.player):
            return 18.0
        return 0.0

    def _rebel_stance(self, mode, target, lord, visible):
        """反贼：人数优势、速推主公。"""

        from src.game.identity import Identity

        if target is lord:
            return 65.0
        if visible is Identity.REBEL:
            return -70.0                  # 同伴
        if visible is Identity.LOYALIST:
            return 30.0                   # 护主的忠臣同样要清
        score = 0.0
        if lord is not None and mode.has_struck(target, lord):
            score += 25.0                 # 跟着打主公的人不是敌人
        return score

    def _renegade_stance(self, mode, target, lord, visible):
        """内奸：平衡局势——帮弱打强，但绝不让主公死在自己手上。"""

        from src.game.identity import Identity

        if lord is None:
            return 0.0
        pressure = mode.lord_pressure()
        if target is lord:
            if lord.hp <= 1:
                return -80.0              # 留到最后单挑，不能补刀
            # 只有主公满血、且反贼已经被压住时才去削弱主公方；
            # 否则先帮主公挡反贼——局势必须在自己的掌控里。
            if pressure >= 0.9 and lord.hp >= 3:
                return 10.0
            return -35.0
        if visible is Identity.REBEL:
            return 35.0 if pressure <= 0.55 else -8.0
        if visible is Identity.LOYALIST:
            return 28.0 if pressure >= 0.75 else -10.0
        # 行为信号：打主公的人（大概率是反贼）。反贼成群时必须先帮主公
        # 压住他们——否则主公一倒，内奸也失去最后的单挑机会。
        if lord is not None and mode.has_struck(target, lord):
            assailants = [
                player for player in self.game.get_alive_players()
                if player is not lord and mode.has_struck(player, lord)
            ]
            return 30.0 if len(assailants) >= 2 else -5.0
        return 0.0

    def _shields_the_lord(self, affected):
        """忠臣替主公挡锦囊（无懈可击）：这就是"替主公挡刀"。"""

        from src.game.identity import Identity

        if getattr(self.player, "identity", None) is not Identity.LOYALIST:
            return False
        mode = getattr(self.game, "mode", None)
        lord = mode.lord() if mode is not None else None
        if lord is None or not getattr(lord, "alive", True):
            return False
        return any(target is lord for target in affected)

    def _holds_back(self):
        """主公体力吃紧时收手：先活下来，再谈输出。"""

        from src.game.identity import Identity

        if getattr(self.player, "identity", None) is not Identity.LORD:
            return False
        return self.player.hp <= 2

    # ==================================================
    # 身份（只读公开信息）
    # ==================================================

    def visible_identity(self, target):
        """AI 能看到的身份：自己的身份，或已经公开的身份。

        绝不允许直接读 ``target.identity`` —— 隐藏身份对本 AI 是不可见的，
        否则就成了作弊。
        """

        if target is self.player:
            return getattr(self.player, "identity", None)
        mode = getattr(self.game, "mode", None)
        if mode is None or not getattr(mode, "uses_identities", False):
            return None
        return mode.public_identity_of(target)

    def identity_bias(self, target, card=None):
        """按公开身份给目标打分；隐藏身份不额外加权（沿用通用打分）。"""

        if target is self.player:
            return 0
        visible = self.visible_identity(target)
        if visible is None:
            return 0
        own = getattr(self.player, "identity", None)
        if own is None:
            return 0

        from src.game.identity import Identity

        if own is Identity.REBEL:
            if visible is Identity.LORD:
                return 40
            if visible is Identity.LOYALIST:
                return 15
            return 0
        if own is Identity.LORD:
            if visible in (Identity.REBEL, Identity.RENEGADE):
                return 30
            if visible is Identity.LOYALIST:
                return -60
            return 0
        if own is Identity.LOYALIST:
            if visible is Identity.LORD:
                return -100
            if visible in (Identity.REBEL, Identity.RENEGADE):
                return 30
            return 0
        if own is Identity.RENEGADE:
            # 内奸只需要温和的平衡：别把主公逼死，也别放过明面敌人。
            if visible is Identity.LORD:
                return -20
            return 5
        return 0

    def protects(self, target):
        """是否应该主动救这个角色（只救主公，且自己是主忠一方或内奸）。"""

        if target is self.player:
            return True
        visible = self.visible_identity(target)
        own = getattr(self.player, "identity", None)
        if visible is None or own is None:
            return False
        from src.game.identity import Identity

        if visible is not Identity.LORD:
            return False
        # 主公不能死：忠臣全力相救，内奸也需要主公活着来牵制反贼。
        return own in (Identity.LOYALIST, Identity.RENEGADE)


    # ==================================================
    # 合法目标
    # ==================================================

    def legal_targets(self, card):
        """合法目标（判定在基类，与远程真人共用）再按 AI 的价值排序。"""

        return sorted(
            super().legal_targets(card),
            key=lambda player: self.score_target(player, card),
            reverse=True,
        )

    def _can_use(self, card, action):
        return self.can_use(card, action)

    # ==================================================
    # 出牌决策
    # ==================================================

    def _wants_equipment(self, card):
        current = self.player.get_equipment(card.subtype)
        if current is None:
            return True
        if card.subtype == "weapon":
            return card.attack_range > current.attack_range
        return self.card_value(card) > self.card_value(current)

    def _available_actions(self):
        """共同动作查询（Phase 15A）：AI 从这里拿**合法候选**。"""

        from src.game.available_actions import AvailableActions

        return AvailableActions(self.game)

    def _build_action(self, card):
        """把一张牌变成 AI 想用的动作。

        "能不能这样用、能打谁"来自共同查询（动态目标规则）；"挑哪个目标、
        要不要留牌"仍然是 AI 的策略——本阶段只把候选生成收口，不改强度。
        """

        from src.game.available_actions import RECAST_METADATA, TargetMode

        effect = self.game.engine.card_effects.get(card)
        if effect is None:
            return None
        profile = self._available_actions().target_profile(self.player, card=card)
        candidates = list(profile.players)

        if card.name == "TIESUO":
            fresh = [target for target in candidates if not target.chained][:2]
            if fresh:
                action = UseCardAction(self.player, card, fresh)
                return action if self._can_use(card, action) else None
            recast = UseCardAction(
                self.player, card, [], metadata=dict(RECAST_METADATA))
            return recast if self._can_use(card, recast) else None

        if profile.mode == TargetMode.CHOOSE:
            if profile.rule == TargetRule.MULTIPLE.value:
                targets = [player for player in candidates if not player.chained][:2]
            else:
                targets = candidates[:1]
        elif profile.mode in (TargetMode.ALL, TargetMode.SELF):
            targets = candidates
        else:
            targets = []

        action = UseCardAction(
            self.player, card, targets,
            metadata=self._auxiliary_metadata(effect, targets, card))
        return action if self._can_use(card, action) else None

    def _auxiliary_metadata(self, effect, targets, card):
        """AI 对"使用这张牌还要补全的输入"的自动选择。

        候选来自**规则层声明的同一份查询**（借刀的第二目标就是按"持武器者
        真的能杀到谁"筛出来的），AI 只是在合法候选里挑一个——它不允许凭空
        造一个不在候选里的目标。
        """

        metadata = {}
        for spec in effect.required_inputs(
                self.game, self.player, list(targets), card=card):
            candidates = list(
                spec.candidates(self.game, self.player, list(targets)) or ())
            metadata[spec.key] = candidates[0] if candidates else None
        return metadata

    def choose_action(self):
        hand = [card for card in self.player.hand if card.id not in self._failed_cards]
        if not hand:
            return None

        # 1) 体力明显偏低时补充
        if self.player.hp <= self.player.max_hp - 2:
            tao = next((card for card in hand if card.name == "TAO"), None)
            if tao is not None:
                action = UseCardAction(self.player, tao, [self.player])
                if self._can_use(tao, action):
                    return action

        # 2) 装备：空槽直接装备，武器只换更长的
        for card in hand:
            if card.category != "equipment" or not self._wants_equipment(card):
                continue
            action = UseCardAction(self.player, card, [])
            if self._can_use(card, action):
                return action

        # 3) 酒 + 杀（没有真实【杀】时尝试技能转化：武圣 / 龙胆）
        sha = next((card for card in hand if card.name == "SHA"), None)
        if sha is None:
            converted = self.converted_play_card("SHA")
            if converted is not None and self.legal_targets(converted):
                sha = converted
        if sha is not None:
            sha_targets = self.legal_targets(sha)
            if self._holds_back():
                # 主公体力吃紧：只收已经打倒的残血，不再主动开辟战线。
                sha_targets = [target for target in sha_targets if target.hp <= 1]
            if sha_targets:
                jiu = next((card for card in hand if card.name == "JIU"), None)
                if (
                    jiu is not None
                    and not self.player.jiu_used
                    and not self.player.sha_used
                    and self.player.hp > 1
                ):
                    action = UseCardAction(self.player, jiu, [self.player])
                    if self._can_use(jiu, action):
                        return action
                action = UseCardAction(self.player, sha, sha_targets[:1])
                if self._can_use(sha, action):
                    return action

        # 4) 其余锦囊，按价值从高到低尝试
        ranked = sorted(hand, key=self.card_value, reverse=True)
        for card in ranked:
            if card.name in ("WUXIE", "TAO", "JIU", "SHA", "SHAN"):
                continue
            action = self._build_action(card)
            if action is not None:
                return action
        return None

    # ==================================================
    # 回合循环
    # ==================================================

    def take_turn(self, on_complete):
        self._cards_played = 0
        self._failed_cards = set()
        self._used_skills_this_turn = set()
        # 上一回合的响应请求都已经结束，重开记录（避免集合无限增长）。
        self._skill_response_tried = set()
        self._continue_turn(on_complete)

    def _continue_turn(self, on_complete):
        game = self.game
        if (
            self._cards_played >= self.MAX_CARDS_PER_TURN
            or game.game_over
            or not self.player.is_alive
        ):
            game.actions.add(CallbackAction(on_complete))
            return

        if self._settlement_in_progress():
            # 还有人在等答案（自己的技能窗口 / 别人的响应 / 别的结算）：现在
            # 出牌会插进别人的结算中间，出现"玩家还在选悲歌的牌，AI 又打了
            # 一张【杀】"。不排队空转（同一帧里转不完，还会和"AI 对当前请求
            # 的回答"互相锁死），登记成回调，等请求解决后由引擎唤醒。
            game.engine.defer_turn_resume(
                lambda: self._continue_turn(on_complete))
            return

        if self._try_active_skill():
            self._cards_played += 1
            # 技能可能引发一段需要结算的流程：离间让两个人决斗、反间让对方猜牌。
            # 必须等它跑完再继续出牌，否则会出现"决斗还没打完就接着出【万箭齐发】"。
            game.actions.add(CallbackAction(lambda: self._resume_after_skill(on_complete)))
            return

        action = self.choose_action()
        if action is None:
            game.actions.add(CallbackAction(on_complete))
            return

        self._cards_played += 1
        self._failed_cards.add(action.card.id)
        action.on_complete = lambda _result: game.actions.add(
            CallbackAction(lambda: self._continue_turn(on_complete))
        )
        # 每张牌之间留出观察时间，避免一个回合的牌在瞬间全部打完。
        game.actions.add(WaitAction(self.CARD_PAUSE))
        result = self.submit(action)
        if getattr(result.status, "value", None) == "cancelled":
            game.actions.add(CallbackAction(lambda: self._continue_turn(on_complete)))

    def _settlement_in_progress(self):
        """现在还不能接着出牌吗（有待回答的请求 / 回合已经不属于自己）。"""

        game = self.game
        engine = getattr(game, "engine", None)
        if engine is not None and engine.pending.active:
            return True
        # 别人的结算把回合推走了（自己阵亡、或回合已经交给下一个角色）：
        # 这一轮出牌到此为止，再出就是"越过回合"。
        if game.current_turn_player is not self.player:
            return True
        return False

    def _resume_after_skill(self, on_complete, waited=0):
        """技能引发流程时等它结束；等不到（引擎异常）也要有兜底。

        "还没结束"的判据是引擎状态，而不是技能名字：只要还有待响应请求、
        活动流程或动作队列在跑，就说明这段结算没走完。所有会引发流程的
        技能（离间 / 反间 / 结姻 / 突袭 / 未来的新技能）都走同一条路。
        """

        game = self.game
        pending = game.engine.pending.current
        running = bool(getattr(game.engine, "active_flows", None))
        if (pending is not None or game.busy or running) and waited < 600:
            game.actions.add(
                CallbackAction(lambda: self._resume_after_skill(on_complete, waited + 1)))
            return
        self._continue_turn(on_complete)

    def _try_active_skill(self):
        """出牌阶段尝试发动一次主动技能；成功返回 True。

        策略保持简单：反间与结姻在有明确收益时发动，突袭在摸牌阶段发动。
        """

        game = self.game
        if game.game_over or not self.player.is_alive:
            return False
        # 优先级：限定技 / 大招（big_play）先于普通主动技。否则神吕布会把
        # 标记零散花在【无前】上，永远攒不到 6 枚放【神愤】。
        def _priority(skill_id):
            definition = game.skill_registry.get(skill_id)
            tags = getattr(definition, "tags", ()) if definition is not None else ()
            if "limited" in tags:
                return 0
            if "big_play" in tags:
                return 1
            return 2

        # 技能清单与真人看到的**同一份**：自己拥有的主动技，加上别人拥有、
        # 现在由我发动的授予型技能（【黄天】）。AI 不为这两类各写一套判断。
        entries = []
        for skill_id in sorted(game.skills.activatable_skills(self.player), key=_priority):
            allowed, _reason = game.skills.can_activate(self.player, skill_id)
            if allowed:
                entries.append((skill_id, None))
        for definition, owners in game.skills.granted_offers(self.player):
            entries.append((definition.id, list(owners)))

        for skill_id, grant_owners in entries:
            if skill_id in self._used_skills_this_turn:
                continue
            definition = game.skill_registry.get(skill_id)
            inputs = self._available_actions().skill_inputs(
                self.player, skill_id, grant_owners=grant_owners)
            if definition is not None and getattr(definition, "needs_local_ui", False):
                # 这个技能的交互还挂在"只有本地真人界面才有"的选牌通道上
                # （心战 / 观星）：AI 发出去只会一直等一个永远不会出现的答案，
                # 整个回合就此卡住。可用性过滤在其实入口已经把它标成 disabled，
                # 这里走的是另一条路（skills.activatable_skills），必须同样跳过。
                continue
            if definition is not None and "granted" in getattr(definition, "tags", ()):
                # 「极略」这类"临时获得别的技能"的主动技：AI 没有能力判断
                # 鬼才 / 放逐的发动时机，盲目发动只会白扣「忍」标记。
                continue
            if grant_owners is not None:
                target = self._granted_skill_target(grant_owners)
            else:
                target = self._active_skill_target(skill_id)
            if inputs.get("needs_target") and target is None:
                # 需要目标却没找到合法目标 → 这条不发动。**不需要目标**的
                # 主动技（神愤 / 业炎）以前也被这里一刀切掉，AI 从来放不出
                # 神愤，等于把神吕布的大招删了。
                continue
            if skill_id == "jieyin" and self.player.hp > 1:
                # 只在体力偏低时用结姻，避免浪费两张手牌。
                continue
            if self._would_gift_to_enemy(skill_id, target):
                # 把牌送给对手是净亏（自由混战 / 1v1 里没有队友可言）：
                # 宁可这一手不用技能，也不给对面送资源。
                continue
            self._used_skills_this_turn.add(skill_id)
            from src.game.engine import ActivateSkillAction

            cards = self._active_skill_cards(skill_id)
            if cards is None:
                # 挑不出满足技能组合约束的组合（【乱击】手里没有两张同花色）：
                # 这一手不发动，也不把这次机会记成"用过了"。
                self._used_skills_this_turn.discard(skill_id)
                continue
            ok, _message = self.submit(
                ActivateSkillAction(self.player, skill_id, target=target, cards=cards)
            )
            if ok:
                return True
            self._used_skills_this_turn.discard(skill_id)
        return False

    def _granted_skill_target(self, owners):
        """授予型技能（【黄天】）把牌交给谁：只交给自己人。

        没有阵营概念的模式（自由混战 / 1v1）里谁都不是自己人，所以 AI 不交——
        把【闪】白送给别人不是"帮助"，是净亏。
        """

        for owner in owners:
            if self.is_ally(owner):
                return owner
        return None

    # ---- 赠予类技能（card_transfer）的自我伤害防护 ----

    def _would_gift_to_enemy(self, skill_id, target):
        """这次发动会不会把资源白送给对手。

        判据全部来自**数据声明**，不按技能名分支：
        ``SkillDef.tags`` 里带 ``card_transfer`` 的技能（仁德 / 举荐 / 眩惑 /
        明策 / 直谏）结算后会把牌或装备交给目标，所以只有在目标是自己人时
        才值得发动。没有队友的模式（自由混战 / 1v1 / 身份局里的内奸）一律
        不发——AI 之前正是靠"把整手牌交给对手"把刘备 / 徐庶 / 陈宫玩成
        0% 胜率的。
        """

        definition = self.game.skill_registry.get(skill_id)
        if definition is None or "card_transfer" not in getattr(definition, "tags", ()):
            return False
        if target is None or target is self.player:
            return False
        return not self.is_ally(target)

    def is_ally(self, other):
        """这名角色是不是"自己人"（只用公开信息判断，不做弊）。

        身份模式：主公与忠臣互为同伴；反贼之间互为同伴（这是自己的身份，
        本来就知道）；内奸没有同伴。其它模式没有阵营概念，统一返回 False。
        """

        from src.game.identity import Identity

        if other is None or other is self.player:
            return False
        mode = getattr(self.game, "mode", None)
        if mode is None or not getattr(mode, "uses_identities", False):
            return False
        own = getattr(self.player, "identity", None)
        theirs = getattr(other, "identity", None)
        if own is None or theirs is None:
            return False
        if own is Identity.RENEGADE:
            return False
        if own is Identity.REBEL:
            return theirs is Identity.REBEL
        # 主公 / 忠臣：彼此是同伴（反贼与内奸不是）。
        return theirs in (Identity.LORD, Identity.LOYALIST)

    def _active_skill_cards(self, skill_id):
        """主动技能需要的牌：弃掉手上最没价值的那几张。

        需要几张、**能用哪些**都来自共同查询（``activation_inputs``）；选哪
        几张才是 AI 的策略。候选这一层不能省：牌不是想给就能给的（眩惑只能
        交红桃手牌、明策只能给装备或【杀】、直谏只能给装备），AI 必须按同一
        份名单挑，否则房主会拒绝它提交的牌。

        组合约束（【乱击】的两张必须同花色）由技能在 ``spec.cost_validator``
        里声明，这里读**同一份**校验来挑组合：不合格就换组合。挑不到任何
        合法组合时返回 ``None``——提交一份注定被拒的牌等于白丢一次发动机会，
        不如这一手不发。

        返回：牌列表，或 ``None``（这次不发动）。
        """

        inputs = self._available_actions().skill_inputs(self.player, skill_id)
        candidates = list(inputs.get("cost_candidates") or ())
        if inputs.get("variable_cost"):
            cap = int(inputs.get("max_cost_cards") or 0)
            need = min(cap, len(candidates)) if cap else len(candidates)
            if self._skill_gives_cards_away(skill_id):
                # 白送出去的牌只给"最小可用量"：以前按上限给，仁德会把整手
                # 牌一次送光（连自己的防御都不留）。制衡这类"回炉自己的牌"
                # 不带 card_transfer，不受这条影响。
                need = min(need, 2)
            need = max(1, need)
        else:
            need = int(inputs.get("cost_cards") or 0)
        if need <= 0:
            return []
        if len(candidates) < need:
            # 连候选（逐张谓词那一层）都不够付：提交一份凑不满的牌只会被
            # 规则层拒绝。宁可这一手不发。
            return None
        ranked = sorted(candidates, key=self.card_value)
        return self._pick_cost_cards(skill_id, ranked, need)

    def _pick_cost_cards(self, skill_id, ranked, need):
        """从"最没价值优先"的候选里挑一组合法的费用牌（挑不到返回 ``None``）。

        合法性判据是技能自己声明的那一份（``ActiveSkillSpec.cost_validator``，
        引擎 ``plan_activation`` 校验阶段读的是同一个函数），所以 AI 不会再
        挑出"两张不同花色"这种会被规则层拒绝的组合。组合按价值从低到高枚举，
        第一个通过的即为本次选择——够用即可，不做全组合搜索。
        """

        from src.game.skills.activation import spec_of

        definition = self.game.skill_registry.get(skill_id)
        spec = spec_of(definition) if definition is not None else None
        validator = getattr(spec, "cost_validator", None) if spec is not None else None
        if not callable(validator):
            return ranked[:need]
        import itertools

        # 候选本身就是"手上能用的牌"（最多十几张），直接枚举组合即可：
        # 数量级完全可控，而且不会像"只看最便宜的几张"那样在组合边界上漏掉
        # 唯一合法的搭配。
        for combo in itertools.combinations(ranked, need):
            if _cost_combo_ok(validator, self.game, self.player, combo):
                return list(combo)
        return None

    def _skill_gives_cards_away(self, skill_id):
        definition = self.game.skill_registry.get(skill_id)
        return bool(definition is not None
                    and "card_transfer" in getattr(definition, "tags", ()))

    def _active_skill_target(self, skill_id):
        """主动技的目标候选来自共同查询；"挑谁"仍是 AI 的策略。"""

        inputs = self._available_actions().skill_inputs(self.player, skill_id)
        candidates = list(inputs.get("targets") or ()) if inputs.get("needs_target") else []
        if not candidates:
            return None
        if skill_id == "jieyin":
            wounded_males = [
                other for other in candidates
                if other.gender == "male" and other.hp < other.max_hp
            ]
            return wounded_males[0] if wounded_males else None
        if skill_id == "fanjian":
            candidates.sort(key=lambda other: len(other.hand))
            return candidates[0]
        candidates.sort(key=lambda other: len(other.hand), reverse=True)
        return candidates[0]

    def discard_to_hand_limit(self):
        """弃到体力上限：从价值最低的开始丢（移动由基类负责）。"""

        limit = self.game.hand_limit(self.player)
        surplus = len(self.player.hand) - limit
        if surplus <= 0:
            return []
        # 被锁住类别的牌（鸡肋）优先保留：不能用、不能打、也不能弃。
        free = [card for card in self.player.hand
                if not self.game.category_forbidden(self.player, card)]
        locked = [card for card in self.player.hand
                  if self.game.category_forbidden(self.player, card)]
        ordered = sorted(free, key=self.card_value) + sorted(locked, key=self.card_value)
        return self.discard_cards(ordered[:surplus])

    # ==================================================
    # Pending 响应
    # ==================================================

    def present(self, request):
        """AI 的决策入口：与真人共用同一个 PendingRequest，只是答案由 AI 给。

        节奏模式（真实对局）下响应会先排进动作队列，每次决策前停一段可见的
        时间；无头测试与批量演算直接同步回答。停顿不改变任何规则判定，只是
        把"谁回应、什么时候回应"交给动作队列驱动。
        """

        game = self.game
        if not getattr(game, "ai_pacing", False):
            return self.respond(request)
        engine = game.engine
        game.actions.add(WaitAction(engine.response_pause(request)))
        game.actions.add(CallbackAction(
            lambda: engine.defer_respond(request, self)))

    def respond(self, request):
        """当场给出决策（同步路径；节奏模式由动作队列在停顿后调用它）。"""

        request_type = request.request_type
        if request_type is PendingRequestType.RESPOND_CARD:
            return self._respond_with_card(request)
        if request_type is PendingRequestType.CONFIRM:
            return self._respond_confirm(request)
        if request_type is PendingRequestType.CHOOSE_OPTION:
            return self._respond_option(request)
        if request_type is PendingRequestType.SELECT_CARDS:
            return self._respond_select(request)
        if request_type is PendingRequestType.SELECT_TARGETS:
            return self._respond_select_targets(request)
        raise ValueError("unsupported AI pending request: " + str(request_type))

    def _respond_select_targets(self, request):
        """选目标请求：按候选顺序取满上限（突袭一类），合法性仍由引擎复核。"""

        candidates = list(request.context.get("candidates", ()))
        if not candidates:
            self._pass(request)
            return
        reason = str(request.context.get("reason") or "")
        if reason == "yeyan":
            picked = self._yeyan_targets(request, candidates)
            if not picked:
                self._pass(request)          # 分配到此为止（不再往同一个人身上堆）
                return
            self.submit(SelectTargetsAction(
                request.target, request.request_id, picked))
            return
        if reason in BENEFIT_REASONS:
            picked = self._beneficiary_target(candidates)
            if not picked:
                self._pass(request)
                return
            self.submit(SelectTargetsAction(
                request.target, request.request_id, picked))
            return
        want = max(1, min(int(request.max_cards or 1), len(candidates)))
        self.submit(
            SelectTargetsAction(request.target, request.request_id, candidates[:want])
        )

    def _yeyan_targets(self, request, candidates):
        """业炎的逐点分配：优先摊到"还没被打到"的人身上。

        对同一名角色分配 2 点及以上要弃四张不同花色的手牌并**失去 3 点体力**
        ——AI 算不清这笔账，一律避免；只在对手只剩 1 点体力、堆第二点能直接
        击杀时才考虑，而那需要先确认自己付得起代价。
        """

        allocation = request.context.get("allocation") or {}
        fresh = [player for player in candidates
                 if not int(allocation.get(str(player.player_id), 0) or 0)]
        if fresh:
            return [fresh[0]]
        # 每个人都至少吃到 1 点了：只有"再补一刀就能杀掉"才值得付重额代价。
        payable = self._can_pay_yeyan_heavy()
        if not payable:
            return []
        killable = [player for player in candidates if int(player.hp) <= 1]
        return [killable[0]] if killable else []

    def _beneficiary_target(self, candidates):
        """挑一个"给他好处"的目标：自己人优先，绝不补给已知的敌人。

        评分 = 阵营权重 × 100 + 还差几张补满，所以权重压倒补牌数——只要
        有自己人（或自己）可补，哪怕他只差 1 张，也不会去补一个缺 5 张的敌人。
        没有队友的模式（自由混战）里大家都不是敌人，按缺牌数挑最需要的。
        """

        mode = getattr(self.game, "mode", None)
        best = None
        best_score = None
        for player in candidates:
            limit = min(5, int(getattr(player, "max_hp", 0) or 0))
            lack = max(0, limit - len(getattr(player, "hand", ()) or ()))
            if lack <= 0:
                continue
            if player is self.player or self.is_ally(player):
                weight = 2
            elif (mode is not None and getattr(mode, "uses_identities", False)
                  and self._is_known_enemy(mode, player)):
                weight = 0
            else:
                weight = 1
            score = weight * 100 + lack
            if best_score is None or score > best_score:
                best, best_score = player, score
        return [best] if best is not None else []

    def _can_pay_yeyan_heavy(self):
        """重额业炎的代价：四张不同花色的手牌 + 3 点体力。"""

        suits = {getattr(card, "suit", None) for card in self.player.hand}
        suits.discard(None)
        return len(suits) >= 4 and int(self.player.hp) > 3

    def _pass(self, request):
        self.submit(PassPendingAction(self.answerer(request), request.request_id))

    def _play_or_pass(self, request, card):
        if card is None:
            self._pass(request)
            return
        self.submit(
            RespondCardAction(self.answerer(request), request.request_id, card, None)
        )

    def _pick_card(self, player, names):
        for name in names:
            card = next((item for item in player.hand if item.name == name), None)
            if card is not None:
                return card
        return None

    def response_options(self, request):
        """这个请求当前有哪些合法响应（真实牌与技能转化，统一来自 Discovery）。"""

        responder = self.answerer(request)
        context = self.game.card_actions.response_context(
            responder, request=request, allowed_names=request.allowed_cards)
        return self.game.card_actions.usable_options(responder, context)

    def converted_response(self, request):
        """没有真实响应牌时，用技能转化出来的虚拟牌（武圣 / 龙胆 / 龙魂）。

        多来源转化（龙魂要 X 张同花色、丈八蛇矛要两张手牌）在这里**把来源
        凑齐**后再交出去：以前只认"已经凑齐的"，于是神赵云在 2 体力时
        永远打不出龙魂的【闪】【桃】【无懈】——等于没有防御技。
        """

        options = self._assembled_options(
            self.response_context(request),
            lambda option: option.result_name in tuple(request.allowed_cards))
        if not options:
            return None
        options.sort(key=lambda option: self.card_value(option.source_cards[0]))
        return self.game.card_actions.effective_card(options[0])

    def converted_play_card(self, card_name):
        """出牌阶段：用技能把实体牌当 card_name 使用（优先用最没用的牌）。"""

        context = self.game.card_actions.play_context(self.player)
        options = self._assembled_options(
            context, lambda option: option.result_name == card_name)
        if not options:
            return None
        options.sort(key=lambda option: self.card_value(option.source_cards[0]))
        return self.game.card_actions.effective_card(options[0])

    # ---- 多来源转化的"凑牌" ----

    def _assembled_options(self, context, predicate):
        """按谓词挑转化候选，并把需要多张来源的那些**凑齐**（返回完整候选）。

        凑牌策略：只在候选中优先挑"手上最没用"的那几张，并且只在这个小集合
        里做组合——够用即可，不做全组合搜索。
        """

        discovery = self.game.card_actions
        options = [option for option in discovery.usable_options(context.actor, context)
                   if option.is_conversion and predicate(option)]
        ready = [option for option in options if option.complete and option.enabled]
        for option in options:
            if not option.enabled or not option.needs_more_sources:
                continue
            assembled = self._assemble_sources(option, context, discovery)
            if assembled is not None:
                ready.append(assembled)
        return ready

    def _assemble_sources(self, option, context, discovery):
        """把这条多来源转化凑齐：起始牌就是候选自带的 source_cards，
        再按转化自己的谓词挑够剩下的张数（优先挑手上最没用的）。"""

        import itertools

        conversion = discovery.conversion_of(option)
        if conversion is None:
            return None
        actor = context.actor
        starting = list(option.source_cards or ())
        need = max(1, int(option.min_sources))
        missing = need - len(starting)
        if missing < 0 or not starting:
            return None
        if missing == 0:
            return option if option.complete and option.enabled else None

        pool = [card for card in self._conversion_materials(actor, conversion, discovery)
                if not any(card is item for item in starting)]
        if len(pool) < missing:
            return None
        # 只在"最没用的几张"里组合：够用即可，不做全组合搜索。
        pool = sorted(pool, key=self.card_value)[:missing + 3]
        for combo in itertools.combinations(pool, missing):
            picked = starting + list(combo)
            for item in discovery.actions_for_sources(actor, picked, context):
                if (item.complete and item.enabled
                        and item.result_name == option.result_name):
                    return item
        return None

    def _conversion_materials(self, actor, conversion, discovery):
        """这名角色身上符合该转化谓词、且区域允许的实体牌。"""

        zones = tuple(getattr(conversion, "source_zones", ()) or ())
        cards = []
        if not zones or "hand" in zones:
            cards.extend(card for card in getattr(actor, "hand", ()) or ()
                         if card is not None)
        if not zones or "equipment" in zones:
            cards.extend(card for card in (getattr(actor, "equipment", None)
                                           or {}).values() if card is not None)
        matches = getattr(conversion, "matches", None)
        return [card for card in cards if matches is None or matches(card)]

    def response_context(self, request):
        """一次响应请求对应的 Action 上下文（转化候选都从它出）。"""

        responder = self.answerer(request)
        return self.game.card_actions.response_context(
            responder, request=request, allowed_names=request.allowed_cards)

    def _respond_with_card(self, request):
        reason = request.context.get("reason")
        # 共享响应阶段（无懈）里一条请求同时问好几个人：回答者是**我**，
        # 不是请求上的 target（那只是"第一个有资格的人"）。
        responder = self.answerer(request)

        if reason == "dying_rescue":
            dying = request.context.get("dying_player")
            if responder is not dying and not self.protects(dying):
                # 自由混战 / 隐藏身份：不使用【桃】救其他角色，但仍走规则层流程。
                self._pass(request)
                return
            # 救援牌名要以请求为准：救别人只允许【桃】，【完杀】还会在施加者的
            # 回合里把【桃】也禁掉。直接递 SELF_RESCUE_NAMES 会在这些情况下挑出
            # 一张请求不接受的牌，被引擎拒收（card is not allowed）。
            allowed = tuple(request.allowed_cards or ())
            names = tuple(name for name in SELF_RESCUE_NAMES if name in allowed)
            self._play_or_pass(request, self._pick_card(responder, names))
            return

        if reason == "wuxie_chain":
            self._play_or_pass(request, self._wuxie_choice(request))
            return

        # 统一入口：先看有没有真实牌，再看技能能不能把别的牌转化出来。
        options = self.response_options(request)
        real = [option for option in options if not option.is_conversion]
        if real:
            card = self._pick_card(responder, tuple(request.allowed_cards))
        else:
            card = None
        if card is None:
            card = self.converted_response(request)
        if card is None and self._respond_with_skill(request, responder):
            # 技能替我完成了这次响应（【激将】：蜀势力角色打出的【杀】由刘备
            # 打出）。请求已经由那条技能的流程回答，这里不再提交"放弃"。
            return
        self._play_or_pass(request, card)

    def _respond_with_skill(self, request, responder):
        """没有实体牌 / 转化牌可打时，看看**技能**能不能替我完成这次响应。

        判据全部来自规则层的同一份可用性查询（``game.skills.can_activate``）：
        【激将】在"被要求打出【杀】"的响应窗口里能不能发动，由技能自己回答
        （有其他存活的蜀势力角色即可）——AI 不认牌名，也不认技能 id。这条
        路与出牌阶段的 ``_try_active_skill`` 是同一个口径：规则层说"可以
        发动"的技能，真人界面上也亮着同一个按钮。

        返回 True 表示已经由技能的流程接手这次响应（可能还挂着等同伴回答）。

        **一条请求只发动一次技能**：技能没能满足这条请求时（【激将】问了
        一圈、没有一个蜀势力角色愿意提供【杀】），引擎会把这条请求重新驱动
        到最前面再问一次，没有这个记忆就会"发动 → 没人提供 → 再发动"地
        无限递归。标记必须在 ``submit`` **之前**落下——那条重新驱动就发生在
        ``submit`` 里面，等它返回再记就来不及了。
        """

        game = self.game
        if responder is not self.player or game.game_over:
            return False
        if request.request_id in self._skill_response_tried:
            return False
        for skill_id in game.skills.activatable_skills(responder):
            definition = game.skill_registry.get(skill_id)
            if definition is None:
                continue
            if getattr(definition, "needs_local_ui", False):
                continue
            if "granted" in getattr(definition, "tags", ()):
                continue
            if not self._skill_serves_this_request(definition, responder):
                # 它在这个窗口里"恰好也能发动"，但那次可发动跟这条请求无关
                # （【酒诗】在任意时刻都能翻面视为使用【酒】）：替玩家发动它
                # 只会白白花掉翻面 / 一张牌，还回答不了这条请求。
                continue
            from src.game.engine import ActivateSkillAction

            self._skill_response_tried.add(request.request_id)
            ok, _message = self.submit(
                ActivateSkillAction(responder, skill_id))
            if ok:
                return True
            # 被规则层拒绝（等于什么都没发生）：撤掉标记，继续试下一个。
            self._skill_response_tried.discard(request.request_id)
        return False

    def _skill_serves_this_request(self, definition, player):
        """这个技能此刻的"可发动"是**这条响应请求带来的**吗。

        反事实对照：把待回答的请求报告成不存在，再问一次技能自己的可用性
        判据（只读视图，不改任何真实状态）。摘掉请求之后仍然可发动 → 这个
        技能与"完成这次响应"无关，AI 不替玩家发动它；只有"没有这条请求就
        发不动"的技能（【激将】：没有被要求打出【杀】时不可发动）才是真正
        为这次响应而生的。

        没有可用性判据的技能（``can_activate is None``）随时都能发动，同样
        不算响应型技能——保守方向是"宁可不用"，不会出现"AI 替玩家白花一张
        牌或翻一次面"。
        """

        predicate = getattr(definition, "can_activate", None)
        if not callable(predicate):
            return False
        try:
            return not _predicate_ok(
                predicate(_NoRequestView(self.game), player))
        except Exception:                                              # noqa: BLE001
            # 谓词读到了这份视图给不出的东西：保守地当成"与本次响应无关"。
            return False

    def _wuxie_choice(self, request):
        card = self._pick_card(self.answerer(request), ("WUXIE",))
        if card is None:
            return None
        trick = request.context.get("card")
        if trick is not None and trick.name in BENEFICIAL_TRICKS:
            return None
        affected = request.context.get("targets", ())
        if not any(target is self.player for target in affected):
            # 自己不受影响：只有忠臣替主公挡牌这一种情况值得消耗无懈。
            if not self._shields_the_lord(affected):
                return None
        if request.context.get("nullified"):
            # 该效果已经被抵消，对当前的自己有利，不再反无懈。
            return None
        return card

    def _respond_confirm(self, request):
        reason = request.context.get("reason", "")
        actor = request.target
        confirmed = True
        if reason == "guanshi_confirm":
            confirmed = len(actor.hand) >= 4
        elif reason == "qinglong_confirm":
            confirmed = (
                not actor.sha_used
                and any(card.name == "SHA" for card in actor.hand)
            )
        self.submit(
            ConfirmPendingAction(request.target, request.request_id, confirmed)
        )

    def _respond_option(self, request):
        options = list(request.options)
        pick = "draw" if "draw" in options else (options[0] if options else None)
        self.submit(ChooseOptionAction(request.target, request.request_id, pick))

    # 判定 → 有利条件（AI 策略层的小表，不属于核心规则）
    JUDGE_FAVOURABLE = {
        "lebu": ("heart",),
        "bingliang": ("club",),
        "bagua": ("red",),
        "ganglie": ("heart",),
    }

    def _judge_replacement_choice(self, request):
        """只在"自己判定且当前结果不利"时考虑改判，并优先用最没用的牌。"""

        judged = request.context.get("judged_player")
        judge_context = self.game.judge_context
        if judged is not self.player or judge_context is None:
            return None
        card = judge_context.current_card
        if card is None:
            return None
        if self._judgement_is_favourable(judge_context.reason, card):
            return None
        candidates = list(request.context.get("candidates", ()))
        if not candidates:
            return None
        # 找一张能让判定变有利的牌；没有就挑最不重要的一张试试。
        for option in candidates:
            if self._judgement_is_favourable(judge_context.reason, option):
                return option
        candidates.sort(key=self.card_value)
        return candidates[0] if len(candidates) > 1 else None

    def _judgement_is_favourable(self, reason, card):
        if reason == "shandian":
            rank = str(getattr(card, "rank", ""))
            value = {"A": 1, "J": 11, "Q": 12, "K": 13}.get(
                rank, int(rank) if rank.isdigit() else 0
            )
            return not (getattr(card, "suit", None) == "spade" and 2 <= value <= 9)
        wanted = self.JUDGE_FAVOURABLE.get(reason)
        if wanted is None:
            return True          # 不认识的判定不参与改判
        for condition in wanted:
            if condition == "red" and getattr(card, "card_color", None) == "red":
                return True
            if condition in ("heart", "club") and getattr(card, "suit", None) == condition:
                return True
        return False

    def _respond_select(self, request):
        reason = request.context.get("reason")
        if reason == "judge_replacement":
            picked_card = self._judge_replacement_choice(request)
            if picked_card is None:
                self._pass(request)
            else:
                self.submit(SelectCardsAction(request.target, request.request_id, [picked_card]))
            return

        candidates = list(request.context.get("candidates", ()))
        if not candidates:
            self._pass(request)
            return
        count = max(1, request.min_cards)

        if reason == "gongxin":
            # 攻心：候选是目标的**全部手牌**（引擎只允许拥有者看到），
            # 但只有红桃能展示。挑对方最有用的红桃弃掉；没有红桃就放弃。
            hearts = [card for card in candidates
                      if getattr(card, "suit", None) == "heart"]
            if not hearts:
                self._pass(request)
                return
            picked = [max(hearts, key=self.card_value)]
            self.submit(SelectCardsAction(
                request.target, request.request_id, picked))
            return

        if reason == "xinzhan":
            # 心战：红桃是白拿的牌，候选全都是红桃（引擎只给红桃），全取。
            picked = sorted(candidates, key=self.card_value, reverse=True)
            self.submit(SelectCardsAction(
                request.target, request.request_id, picked))
            return

        if reason in ("wugu", "guanxing_order", "xinzhan_order", "shelie"):
            picked = sorted(candidates, key=self.card_value, reverse=True)[:count]
        elif reason in ("guohe", "shunshou"):
            picked = self._pick_from_opponent(request, candidates)[:count]
        elif reason == "pindian":
            picked = self._pindian_choice(candidates)[:count]
        else:
            picked = sorted(candidates, key=self.card_value)[:count]

        self.submit(
            SelectCardsAction(request.target, request.request_id, picked)
        )

    def _pindian_choice(self, candidates):
        """拼点按**点数**出牌，不按用途。

        拼点比的是点数（A=1 … K=13），而且这些技能都是"赢了大赚、没赢挨罚"，
        所以赢（或挡住对手赢）远比省一张好牌重要。以前这里走的是通用的
        "按用途价值升序"，而基本牌的价值一律是 4 分，等于照着手牌顺序甩牌
        ——手上明明有 K，照样能把一张 A 递出去。
        """

        from src.game.skills.mechanics import rank_value

        return sorted(candidates,
                      key=lambda card: (-rank_value(card), self.card_value(card)))

    def _pick_from_opponent(self, request, candidates):
        owner = request.context.get("zone_owner")
        if owner is not None and owner is not self.player:
            equipped = {id(card) for card in owner.equipment.values() if card is not None}
            exposed = [card for card in candidates if id(card) in equipped]
            if exposed:
                # 装备是公开信息；对方手牌内容不可读，只能随机选择。
                return [max(exposed, key=self.card_value)]
            return [self.rng.choice(candidates)]
        return sorted(candidates, key=self.card_value, reverse=True)
