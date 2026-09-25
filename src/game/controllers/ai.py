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

        action = UseCardAction(self.player, card, targets)
        return action if self._can_use(card, action) else None

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
        for skill_id in game.skills.activatable_skills(self.player):
            allowed, _reason = game.skills.can_activate(self.player, skill_id)
            if not allowed:
                continue
            if skill_id in self._used_skills_this_turn:
                continue
            target = self._active_skill_target(skill_id)
            if target is None and skill_id != "tuxi":
                continue
            if skill_id == "jieyin" and self.player.hp > 1:
                # 只在体力偏低时用结姻，避免浪费两张手牌。
                continue
            self._used_skills_this_turn.add(skill_id)
            from src.game.engine import ActivateSkillAction

            cards = self._active_skill_cards(skill_id)
            ok, _message = self.submit(
                ActivateSkillAction(self.player, skill_id, target=target, cards=cards)
            )
            if ok:
                return True
            self._used_skills_this_turn.discard(skill_id)
        return False

    def _active_skill_cards(self, skill_id):
        """主动技能需要的牌：弃掉手上最没价值的那几张。

        需要几张、**能用哪些**都来自共同查询（``activation_inputs``）；选哪
        几张才是 AI 的策略。候选这一层不能省：牌不是想给就能给的（眩惑只能
        交红桃手牌、明策只能给装备或【杀】、直谏只能给装备），AI 必须按同一
        份名单挑，否则房主会拒绝它提交的牌。
        """

        inputs = self._available_actions().skill_inputs(self.player, skill_id)
        candidates = list(inputs.get("cost_candidates") or ())
        if not candidates:
            return []
        if inputs.get("variable_cost"):
            cap = int(inputs.get("max_cost_cards") or 0)
            need = min(cap, len(candidates)) if cap else len(candidates)
            need = max(1, need)
        else:
            need = int(inputs.get("cost_cards") or 0)
        if need <= 0:
            return []
        ranked = sorted(candidates, key=self.card_value)
        return ranked[:need]

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
        want = max(1, min(int(request.max_cards or 1), len(candidates)))
        self.submit(
            SelectTargetsAction(request.target, request.request_id, candidates[:want])
        )

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
        """没有真实响应牌时，用技能转化出来的虚拟牌（武圣 / 龙胆）。

        只取**已经凑齐来源**的转化：多来源转化（丈八蛇矛：两张手牌当【杀】）
        在收齐两张之前只是个候选，把它当成一张牌的转化交出去等于凭空少付
        一张牌。AI 现版本不做多来源收集，所以这里直接跳过候选态。
        """

        options = [
            option for option in self.response_options(request)
            if option.is_conversion and not option.needs_more_sources
        ]
        if not options:
            return None
        options.sort(key=lambda option: self.card_value(option.source_cards[0]))
        return self.game.card_actions.effective_card(options[0])

    def converted_play_card(self, card_name):
        """出牌阶段：用技能把某张实体牌当 card_name 使用（优先用最没用的牌）。

        与响应一样只看已凑齐来源的转化（见 ``converted_response``）。
        """

        context = self.game.card_actions.play_context(self.player)
        options = [
            option for option in self.game.card_actions.usable_options(self.player, context)
            if option.is_conversion and option.result_name == card_name
            and not option.needs_more_sources
        ]
        if not options:
            return None
        options.sort(key=lambda option: self.card_value(option.source_cards[0]))
        return self.game.card_actions.effective_card(options[0])

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
            self._play_or_pass(request, self._pick_card(responder, SELF_RESCUE_NAMES))
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
        self._play_or_pass(request, card)

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

        if reason == "wugu":
            picked = sorted(candidates, key=self.card_value, reverse=True)[:count]
        elif reason in ("guohe", "shunshou"):
            picked = self._pick_from_opponent(request, candidates)[:count]
        else:
            picked = sorted(candidates, key=self.card_value)[:count]

        self.submit(
            SelectCardsAction(request.target, request.request_id, picked)
        )

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
