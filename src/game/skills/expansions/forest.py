"""林包武将技能：孙坚 / 孟获 / 徐晃 / 曹丕 / 祝融 / 董卓 / 贾诩 / 鲁肃。"""

from src.game.atoms_v2 import DrawCardsAtom, MoveCardAtom, UnequipAtom
from src.game.conversion import PLAY_CONTEXT, CardConversion
from src.game.engine import EventType, Flow
from src.game.engine.skills import Skill, SkillBinding
from src.game.rules import TurnPhase

from ..definitions import (
    ActiveSkillSpec,
    ModifierSpec,
    PhaseReplacement,
    SkillDef,
    SkillKind,
    active,
    triggered,
)
from ..mechanics import (
    ask_cards,
    ask_confirm,
    ask_option,
    ask_targets,
    consume_limited,
    flip_player,
    hand_cards,
    judge,
    limited_used,
    lose_hp,
    lost_hp,
    other_alive_players,
    start_pindian,
    use_virtual,
)
from ..modifiers import ModifierKind
from ..state import ResetScope


def _is_black(card):
    if getattr(card, "is_virtual", False):
        return False
    return getattr(card, "card_color", None) == "black"


def _is_black_basic_or_equipment(card):
    if getattr(card, "is_virtual", False):
        return False
    if getattr(card, "card_color", None) != "black":
        return False
    return getattr(card, "category", None) in ("basic", "equipment")


def _all_cards_of(player):
    cards = list(getattr(player, "hand", ()) or ())
    for card in (getattr(player, "equipment", None) or {}).values():
        if card is not None:
            cards.append(card)
    cards.extend(list(getattr(player, "judgement_zone", ()) or ()))
    return cards


# ==================================================
# 孙坚 · 英魂
# ==================================================


def _can_yinghun(game, player):
    if game.game_over or not player.alive:
        return False, "无法发动"
    if game.current_turn_player is not player or game.phase != "prepare":
        return False, "只能在你的回合开始阶段发动"
    if player.skill_state.get("yinghun", "used", 0):
        return False, "本回合已经发动过"
    if lost_hp(player) <= 0:
        return False, "你未受伤，不能发动"
    if not other_alive_players(game, player):
        return False, "没有其他角色"
    return True, ""


def _activate_yinghun(game, player, target=None, cards=None):
    if target is None:
        return False
    player.skill_state.set("yinghun", "used", 1, ResetScope.TURN)
    YinghunFlow(game.engine, player, target).start()
    return True


class YinghunFlow(Flow):
    """英魂：两项选一 —— 摸 X 弃 1，或摸 1 弃 X（X = 孙坚已损失体力）。"""

    def __init__(self, engine, owner, target):
        super().__init__(engine.context)
        self.engine = engine
        self.game = engine.game
        self.owner = owner
        self.target = target
        self.x = max(1, lost_hp(owner))
        self.mode = ""
        self.stage = "choose"

    def begin(self):
        ask_option(self.engine, self, source=self.owner, target=self.owner,
                   prompt="【英魂】：X = %d，请选择一项" % self.x,
                   reason="yinghun",
                   options=(("draw_x", "令其摸 %d 张牌，然后弃一张牌" % self.x),
                            ("discard_x", "令其摸一张牌，然后弃 %d 张牌" % self.x)))
        return self.current_result()

    def advance(self, response=None):
        if self.stage == "choose":
            return self._after_choice(response)
        return self._after_discard(response)

    def _after_choice(self, response):
        option = str(getattr(response, "option", "") or "")
        if option == "discard_x":
            self.mode = "discard_x"
            self.context.apply(DrawCardsAtom(self.target, 1))
            return self._ask_discard(self.x)
        self.mode = "draw_x"
        self.context.apply(DrawCardsAtom(self.target, self.x))
        return self._ask_discard(1)

    def _ask_discard(self, count):
        candidates = list(getattr(self.target, "hand", ()) or ())
        if not candidates:
            return self._finish()
        count = min(count, len(candidates))
        self.stage = "discard"
        ask_cards(self.engine, self, source=self.owner, target=self.target,
                  prompt="【英魂】：请选择弃置 %d 张手牌" % count,
                  reason="yinghun", candidates=candidates,
                  min_cards=count, max_cards=count)
        return self.current_result()

    def _after_discard(self, response):
        for card in list(getattr(response, "cards", ()) or ()):
            if any(item is card for item in self.target.hand):
                self.context.apply(MoveCardAtom(
                    card, source=self.target.hand,
                    destination=self.game.deck.discard_pile))
        return self._finish()

    def _finish(self):
        self.game.add_log("%s 发动【英魂】→ %s（X = %d，%s）"
                          % (self.owner.name, self.target.name, self.x, self.mode))
        return self.complete({"applied": True})


# ==================================================
# 孟获 · 祸首 / 再起
# ==================================================


def _nanman_ignore(game, query):
    """祸首 / 巨象的【南蛮入侵】免疫（TARGET_FORBIDDEN 的判定体）。"""

    card = query.get("card")
    if card is None or getattr(card, "name", None) != "NANMAN":
        return 0
    return 1


def _nanman_source(game, query):
    """祸首：这名角色是任何【南蛮入侵】造成伤害的来源。"""

    card = query.get("card")
    return bool(card is not None and getattr(card, "name", None) == "NANMAN")


def _can_zaiqi(game, player):
    if game.game_over or not player.alive:
        return False, "无法发动"
    if lost_hp(player) <= 0:
        return False, "你未受伤，不能发动"
    if not game.deck.draw_pile:
        return False, "牌堆没有牌"
    return True, ""


def _apply_zaiqi(game, player):
    """再起：放弃摸牌，亮出牌堆顶 X 张，红桃回复体力后弃置，其余收进手牌。"""

    x = min(lost_hp(player), len(game.deck.draw_pile))
    if x <= 0:
        return False
    revealed = []
    for _ in range(x):
        card = game.deck.draw()
        if card is None:
            break
        revealed.append(card)
    hearts = [card for card in revealed if getattr(card, "suit", None) == "heart"]
    others = [card for card in revealed if card not in hearts]
    names = "、".join((getattr(card, "identity_label", "") or "?") for card in revealed)
    game.add_log("%s 发动【再起】，亮出 %s" % (player.name, names))
    if hearts:
        from src.game.atoms_v2 import RecoverHpAtom

        before = player.hp
        game.engine.context.apply(RecoverHpAtom(player, len(hearts)))
        game.add_log("%s 因【再起】回复 %d 点体力" % (player.name, player.hp - before))
    for card in hearts:
        game.deck.discard(card)
    for card in others:
        player.hand.append(card)
    if others:
        game.message = player.name + " 将其余 %d 张牌收入手牌。" % len(others)
    return True


# ==================================================
# 徐晃 · 断粮
# ==================================================


def _duanliang_distance(game, query):
    card = query.get("card")
    if card is None or getattr(card, "name", None) != "BINGLIANG":
        return None
    return 2


# ==================================================
# 曹丕 · 行殇 / 放逐 / 颂威
# ==================================================


class Xingshang(Skill):
    """你可以立即获得死亡角色的所有牌。"""

    id = "xingshang"
    name = "行殇"

    def bindings(self):
        return (SkillBinding(EventType.DEATH, priority=20),)

    def can_trigger(self, context, event):
        dead = event.target
        if dead is None or dead is self.owner or not self.owner.alive:
            return False
        return bool(_all_cards_of(dead))

    def resolve(self, context, event):
        XingshangFlow(context.services["engine"], self.owner, event.target).start()


class XingshangFlow(Flow):
    def __init__(self, engine, owner, dead):
        super().__init__(engine.context)
        self.engine = engine
        self.game = engine.game
        self.owner = owner
        self.dead = dead
        self.stage = "confirm"

    def begin(self):
        ask_confirm(self.engine, self, source=self.owner, target=self.owner,
                    prompt="【行殇】：是否获得 %s 的所有牌？" % self.dead.name,
                    reason="xingshang")
        return self.current_result()

    def advance(self, response=None):
        if response is None or not response.confirmed:
            return self.complete({"applied": False})
        count = 0
        for card in list(getattr(self.dead, "hand", ()) or ()):
            self.context.apply(MoveCardAtom(
                card, source=self.dead.hand, destination=self.owner.hand))
            count += 1
        for slot in list(self.dead.equipment):
            if self.dead.get_equipment(slot) is not None:
                self.context.apply(UnequipAtom(
                    self.dead, slot, self.owner.hand))
                count += 1
        for card in list(getattr(self.dead, "judgement_zone", ()) or ()):
            self.context.apply(MoveCardAtom(
                card, source=self.dead.judgement_zone, destination=self.owner.hand))
            count += 1
        self.game.add_log("%s 的【行殇】获得 %s 的 %d 张牌"
                          % (self.owner.name, self.dead.name, count))
        return self.complete({"applied": True, "count": count})


class Fangzhu(Skill):
    """你每受到一次伤害，可令一名其他角色摸 X 张牌（X = 已损失体力）然后翻面。"""

    id = "fangzhu"
    name = "放逐"

    def bindings(self):
        return (SkillBinding(EventType.DAMAGE_TARGET_AFTER, priority=20),)

    def can_trigger(self, context, event):
        damage = event.payload.get("damage")
        if damage is None or damage.target is not self.owner:
            return False
        if not self.owner.alive:
            return False
        return bool(other_alive_players(context.state, self.owner))

    def resolve(self, context, event):
        FangzhuFlow(context.services["engine"], self.owner).start()


class FangzhuFlow(Flow):
    def __init__(self, engine, owner):
        super().__init__(engine.context)
        self.engine = engine
        self.game = engine.game
        self.owner = owner
        self.stage = "target"

    def begin(self):
        ask_targets(self.engine, self, source=self.owner, target=self.owner,
                    prompt="【放逐】：请选择摸牌并翻面的角色",
                    reason="fangzhu",
                    candidates=other_alive_players(self.game, self.owner),
                    min_targets=1, max_targets=1)
        return self.current_result()

    def advance(self, response=None):
        targets = list(getattr(response, "targets", ()) or ())
        if not targets:
            return self.complete({"applied": False})
        target = targets[0]
        x = max(1, lost_hp(self.owner))
        self.context.apply(DrawCardsAtom(target, x))
        flip_player(self.game, target, reason="放逐")
        self.game.add_log("%s 的【放逐】令 %s 摸 %d 张牌并翻面"
                          % (self.owner.name, target.name, x))
        return self.complete({"applied": True})


class Songwei(Skill):
    """主公技：其他魏势力角色的判定牌为黑色且生效后，可以让你摸一张牌。"""

    id = "songwei"
    name = "颂威"

    def bindings(self):
        return (SkillBinding(EventType.JUDGE_FINISHED, priority=10),)

    def can_trigger(self, context, event):
        if not self.owner.alive:
            return False
        result = event.payload.get("result")
        judged = getattr(result, "source", None)
        if result is None or judged is None or judged is self.owner:
            return False
        if getattr(judged, "kingdom", None) != "wei":
            return False
        return getattr(result, "color", None) == "black"

    def resolve(self, context, event):
        result = event.payload.get("result")
        judged = getattr(result, "source", None)
        SongweiFlow(context.services["engine"], self.owner, judged,
                    getattr(result, "identity_label", "") or "?").start()


class SongweiFlow(Flow):
    def __init__(self, engine, owner, judged, label):
        super().__init__(engine.context)
        self.engine = engine
        self.game = engine.game
        self.owner = owner
        self.judged = judged
        self.label = label
        self.stage = "confirm"

    def begin(self):
        ask_confirm(self.engine, self, source=self.owner, target=self.owner,
                    prompt="【颂威】：%s 的判定为黑色（%s），是否摸一张牌？"
                           % (self.judged.name, self.label),
                    reason="songwei")
        return self.current_result()

    def advance(self, response=None):
        if response is None or not response.confirmed:
            return self.complete({"applied": False})
        self.context.apply(DrawCardsAtom(self.owner, 1))
        self.game.add_log("%s 的【颂威】摸一张牌" % self.owner.name)
        return self.complete({"applied": True})


# ==================================================
# 祝融 · 巨象 / 烈刃
# ==================================================


class Juxiang(Skill):
    """锁定技：【南蛮入侵】对你无效；**其他角色使用**的【南蛮入侵】结算完毕
    进入弃牌堆时你立即获得它。"""

    id = "juxiang"
    name = "巨象"

    def bindings(self):
        # 时机必须是"这张牌作为锦囊结算完毕"，不能用通用的「进弃牌堆」：
        # 那条通知对**一切**弃牌途径都发（自己用的南蛮结算、别人弃牌阶段
        # 把南蛮弃掉、被拆顺拆掉……），挂上去就等于南蛮全归祝融，白送一大截强度。
        return (SkillBinding(EventType.CARD_USE_FINISHED, priority=10),)

    def can_trigger(self, context, event):
        if not self.owner.alive:
            return False
        card = event.payload.get("card")
        if card is None or getattr(card, "name", None) != "NANMAN":
            return False
        if event.source is self.owner:
            return False
        # 牌可能已经不在弃牌堆（被【奸雄】一类技能取走），那就不再拿。
        return any(item is card for item in context.state.deck.discard_pile)

    def resolve(self, context, event):
        card = event.payload["card"]
        game = context.state
        if not any(item is card for item in game.deck.discard_pile):
            return
        game.deck.discard_pile.remove(card)
        self.owner.hand.append(card)
        game.add_log("%s 的【巨象】获得了【南蛮入侵】" % self.owner.name)


class Lieren(Skill):
    """你使用【杀】造成一次伤害后，可与受伤角色拼点，赢了获得其一张牌。"""

    id = "lieren"
    name = "烈刃"

    def bindings(self):
        return (SkillBinding(EventType.DAMAGE_SETTLED, priority=-20),)

    def can_trigger(self, context, event):
        if event.source is not self.owner or not self.owner.alive:
            return False
        damage = event.payload.get("damage")
        if damage is None or int(event.payload.get("amount", 0) or 0) <= 0:
            return False
        card = getattr(damage, "card", None)
        if card is None or getattr(card, "name", None) != "SHA":
            return False
        target = getattr(damage, "target", None)
        if target is None or target is self.owner or not target.alive:
            return False
        return bool(getattr(self.owner, "hand", ())) and bool(getattr(target, "hand", ()))

    def resolve(self, context, event):
        damage = event.payload["damage"]
        start_pindian(
            context.services["engine"], self.owner, damage.target, reason="lieren",
            on_complete=lambda result: self._after(context, damage.target, result))

    def _after(self, context, target, result):
        if result is None or result.cancelled or not result.initiator_wins:
            return
        game = context.state
        LierenFlow(context.services["engine"], self.owner, target).start()


class LierenFlow(Flow):
    """烈刃赢了：获得受伤角色的一张牌。"""

    def __init__(self, engine, owner, target):
        super().__init__(engine.context)
        self.engine = engine
        self.game = engine.game
        self.owner = owner
        self.target = target
        self.stage = "select"

    def begin(self):
        candidates = _all_cards_of(self.target)
        if not candidates:
            return self.complete({"applied": False})
        ask_cards(self.engine, self, source=self.owner, target=self.owner,
                  prompt="【烈刃】：请选择获得 %s 的一张牌" % self.target.name,
                  reason="lieren", candidates=candidates,
                  min_cards=1, max_cards=1, zone="public_pool",
                  context={"zone_owner": self.target})
        return self.current_result()

    def advance(self, response=None):
        cards = list(getattr(response, "cards", ()) or ())
        if not cards:
            return self.complete({"applied": False})
        card = cards[0]
        if any(item is card for item in self.target.hand):
            self.context.apply(MoveCardAtom(
                card, source=self.target.hand, destination=self.owner.hand))
        else:
            for slot, equipped in (self.target.equipment or {}).items():
                if equipped is card:
                    self.context.apply(UnequipAtom(self.target, slot))
                    self.owner.hand.append(card)
                    break
            else:
                return self.complete({"applied": False})
        self.game.add_log("%s 的【烈刃】获得 %s 的一张牌"
                          % (self.owner.name, self.target.name))
        return self.complete({"applied": True})


# ==================================================
# 董卓 · 酒池 / 肉林 / 崩坏 / 暴虐
# ==================================================


def _roulin_from_source(game, query):
    """肉林：董卓对女性角色使用【杀】时，对方需连续两张【闪】。"""

    card = query.get("card")
    target = query.get("target")
    if card is None or getattr(card, "name", None) != "SHA" or target is None:
        return 0
    return 1 if getattr(target, "gender", None) == "female" else 0


def _roulin_from_target(game, query):
    """肉林：女性角色对董卓使用【杀】时，同样需要连续两张【闪】。"""

    card = query.get("card")
    source = query.get("source")
    if card is None or getattr(card, "name", None) != "SHA" or source is None:
        return 0
    return 1 if getattr(source, "gender", None) == "female" else 0


class Benguai(Skill):
    """锁定技：结束阶段若你的体力不是全场最少（或之一），须减 1 点体力或上限。"""

    id = "benguai"
    name = "崩坏"

    def bindings(self):
        return (SkillBinding(EventType.PHASE_START, priority=15),)

    def can_trigger(self, context, event):
        if event.source is not self.owner or not self.owner.alive:
            return False
        if event.payload.get("phase") is not TurnPhase.FINISH:
            return False
        return self._above_lowest(context.state)

    def _above_lowest(self, game):
        """你的体力**不是**全场最少的（或之一）时必须发动。"""

        alive = list(game.get_alive_players())
        if not alive:
            return False
        lowest = min(int(player.hp) for player in alive)
        return int(self.owner.hp) > lowest

    def resolve(self, context, event):
        BenguaiFlow(context.services["engine"], self.owner).start()


class BenguaiFlow(Flow):
    def __init__(self, engine, owner):
        super().__init__(engine.context)
        self.engine = engine
        self.game = engine.game
        self.owner = owner
        self.stage = "choose"

    def begin(self):
        options = []
        if int(self.owner.hp) > 1:
            options.append(("hp", "减 1 点体力"))
        if int(self.owner.max_hp) > 1:
            options.append(("max_hp", "减 1 点体力上限"))
        if not options:
            return self.complete({"applied": False})
        ask_option(self.engine, self, source=self.owner, target=self.owner,
                   prompt="【崩坏】：你的体力不是全场最少，请选择一项",
                   reason="benguai", options=tuple(options))
        return self.current_result()

    def advance(self, response=None):
        option = str(getattr(response, "option", "") or "")
        if option == "max_hp":
            self.owner.max_hp = max(1, int(self.owner.max_hp) - 1)
            if self.owner.hp > self.owner.max_hp:
                self.owner.hp = self.owner.max_hp
            self.game.add_log("%s 的【崩坏】减 1 点体力上限" % self.owner.name)
        else:
            lose_hp(self.game, self.owner, 1, reason="崩坏")
        return self.complete({"applied": True})


class Baonue(Skill):
    """主公技：其他群势力角色每造成一次伤害，可判定，黑色则董卓回复 1 点体力。"""

    id = "baonue"
    name = "暴虐"

    def bindings(self):
        return (SkillBinding(EventType.DAMAGE_SETTLED, priority=5),)

    def can_trigger(self, context, event):
        if not self.owner.alive:
            return False
        source = event.source
        if source is None or source is self.owner:
            return False
        if getattr(source, "kingdom", None) != "qun":
            return False
        damage = event.payload.get("damage")
        if damage is None or int(event.payload.get("amount", 0) or 0) <= 0:
            return False
        return self.owner.hp < self.owner.max_hp

    def resolve(self, context, event):
        BaonueFlow(context.services["engine"], self.owner, event.source).start()


class BaonueFlow(Flow):
    def __init__(self, engine, owner, source):
        super().__init__(engine.context)
        self.engine = engine
        self.game = engine.game
        self.owner = owner
        self.source = source
        self.stage = "judge"

    def begin(self):
        return self._begin_judge()

    def _begin_judge(self):
        flow, result = judge(self.engine, self.owner, "baonue")
        if result is None:
            flow.on_complete = self._after_judge
            self.wait(flow)
            return self.current_result()
        return self._after_judge(result)

    def advance(self, response=None):
        return self.complete({"applied": True})

    def _after_judge(self, result):
        game = self.game
        if result is not None and getattr(result, "color", None) == "black":
            from src.game.atoms_v2 import RecoverHpAtom

            before = self.owner.hp
            self.context.apply(RecoverHpAtom(self.owner, 1))
            game.add_log("%s 的【暴虐】判定为黑色，回复 %d 点体力"
                         % (self.owner.name, self.owner.hp - before))
        else:
            game.add_log("%s 的【暴虐】判定不为黑色" % self.owner.name)
        return self.complete({"applied": True})


# ==================================================
# 贾诩 · 完杀 / 乱武 / 帷幕
# ==================================================


def _wansha_forbidden(game, query):
    """完杀：在自己（= modifier 的拥有者）的回合内，除自己以外只有濒死角色
    才能使用【桃】。施加限制的人由 ``Game.rescue_forbidden`` 从 modifier 上
    带进来（query["owner"]），不写死在技能里。"""

    owner = query.get("owner")
    rescuer = query.get("rescuer")
    dying = query.get("dying")
    if owner is None or rescuer is None or dying is None:
        return False
    if game.current_turn_player is not owner:
        return False
    if rescuer is owner:
        return False
    return rescuer is not dying


def _weimu_forbidden(game, query):
    """帷幕：不能成为黑色锦囊牌的目标。"""

    card = query.get("card")
    if card is None:
        return 0
    if getattr(card, "category", None) != "trick":
        return 0
    if getattr(card, "card_color", None) != "black":
        return 0
    return 1


def _can_luanwu(game, player):
    if game.game_over or not player.alive:
        return False, "无法发动"
    if game.current_turn_player is not player or game.phase != "play":
        return False, "只能在你的出牌阶段发动"
    if limited_used(player, "luanwu"):
        return False, "限定技已经发动过"
    if not other_alive_players(game, player):
        return False, "没有其他角色"
    return True, ""


def _activate_luanwu(game, player, target=None, cards=None):
    consume_limited(game, player, "luanwu", note="乱武")
    LuanwuFlow(game.engine, player).start()
    game.add_log(player.name + " 发动限定技【乱武】")
    return True


class LuanwuFlow(Flow):
    """乱武：除自己以外的所有角色依次对最近的角色使用【杀】，不行则失去 1 点体力。"""

    def __init__(self, engine, owner):
        super().__init__(engine.context)
        self.engine = engine
        self.game = engine.game
        self.owner = owner
        self.order = []
        self.index = 0
        self.stage = "run"

    def begin(self):
        self.order = [
            player for player in self.game.seats.alive_players_in_order(
                start_after=self.owner)
            if player is not self.owner
        ]
        self.index = 0
        return self._next()

    def advance(self, response=None):
        return self._next()

    # ---- 逐个结算 ----

    def _next(self):
        if self.game.game_over:
            return self.complete({"applied": True})
        while self.index < len(self.order):
            player = self.order[self.index]
            self.index += 1
            if not getattr(player, "alive", True) or int(player.hp) <= 0:
                continue
            if self._make_them_sha(player):
                return self._wait_for_child()
            self._punish(player)
        return self.complete({"applied": True})

    def _wait_for_child(self):
        """把本流程挂起，等这次出牌的流程跑完再继续问下一个人。"""

        from src.game.engine import FlowStatus

        self.status = FlowStatus.WAITING
        self.pending_request = {"reason": "luanwu_child"}
        return self.current_result()

    def _resume_child(self):
        from src.game.engine import FlowStatus

        self.pending_request = None
        self.status = FlowStatus.RUNNING
        return self._next()

    def _nearest_targets(self, player):
        """与这名角色距离最近的其他存活角色（可能并列）。"""

        from src.game.rules import DistanceRule

        others = [other for other in self.game.get_alive_players() if other is not player]
        if not others:
            return []
        distances = [(DistanceRule.distance(self.game, player, other), other)
                     for other in others]
        best = min(item[0] for item in distances)
        return [other for value, other in distances if value == best]

    def _sha_for(self, player):
        card = next(
            (item for item in getattr(player, "hand", ())
             if getattr(item, "name", None) == "SHA"), None)
        return card

    def _make_them_sha(self, player):
        """让这名角色对最近的角色使用一张【杀】；做不到返回 False。"""

        candidates = self._nearest_targets(player)
        if not candidates:
            return False
        card = self._sha_for(player)
        if card is None:
            return False
        from src.game.engine import UseCardAction

        self.game.add_log("【乱武】：%s 对 %s 使用【杀】" % (player.name, candidates[0].name))
        self.engine.submit(UseCardAction(
            player, card, [candidates[0]], ignore_usage_limit=True,
            on_complete=lambda _result: self._resume_child()))

    def _punish(self, player):
        self.game.add_log("【乱武】：%s 无法使用【杀】，失去 1 点体力" % player.name)
        lose_hp(self.game, player, 1, source=self.owner, reason="乱武")


# ==================================================
# 鲁肃 · 好施 / 缔盟
# ==================================================


def _can_haoshi(game, player):
    if game.game_over or not player.alive:
        return False, "无法发动"
    if game.current_turn_player is not player or game.phase != "draw":
        return False, "只能在你的摸牌阶段发动"
    if not game.deck.draw_pile:
        return False, "牌堆没有牌"
    return True, ""


def _haoshi_flow(game, player):
    return HaoshiFlow(game.engine, player)


class HaoshiFlow(Flow):
    """好施：额外摸两张；若手牌多于五张，须把一半（向下取整）交给手牌最少的人。"""

    def __init__(self, engine, owner):
        super().__init__(engine.context)
        self.engine = engine
        self.game = engine.game
        self.owner = owner
        self.give = 0
        self.stage = "confirm"

    def begin(self):
        ask_confirm(self.engine, self, source=self.owner, target=self.owner,
                    prompt="【好施】：是否发动？额外摸两张牌。",
                    reason="haoshi")
        return self.current_result()

    def advance(self, response=None):
        if self.stage == "confirm":
            return self._after_confirm(response)
        if self.stage == "target":
            return self._after_target(response)
        return self._after_cards(response)

    def _after_confirm(self, response):
        if response is None or not response.confirmed:
            return self.complete({"applied": False})
        # 摸牌阶段的基础摸牌由本流程自己完成（阶段替代语义）。
        base = int(self.game.draw_count(self.owner))
        self.context.apply(DrawCardsAtom(self.owner, base + 2))
        self.game.add_log("%s 发动【好施】，摸了 %d 张牌" % (self.owner.name, base + 2))
        if len(self.owner.hand) <= 5:
            return self.complete({"applied": True})
        self.give = len(self.owner.hand) // 2
        others = other_alive_players(self.game, self.owner)
        if not others or self.give <= 0:
            return self.complete({"applied": True})
        fewest = min(len(other.hand) for other in others)
        candidates = [other for other in others if len(other.hand) == fewest]
        self.stage = "target"
        ask_targets(self.engine, self, source=self.owner, target=self.owner,
                    prompt="【好施】：请选择手牌最少的角色，把 %d 张手牌交给他" % self.give,
                    reason="haoshi", candidates=candidates,
                    min_targets=1, max_targets=1)
        return self.current_result()

    def _after_target(self, response):
        targets = list(getattr(response, "targets", ()) or ())
        if not targets:
            return self.complete({"applied": True})
        self.target = targets[0]
        candidates = list(self.owner.hand)
        count = min(self.give, len(candidates))
        self.stage = "cards"
        ask_cards(self.engine, self, source=self.owner, target=self.owner,
                  prompt="【好施】：请选择交给 %s 的 %d 张手牌" % (self.target.name, count),
                  reason="haoshi", candidates=candidates,
                  min_cards=count, max_cards=count)
        return self.current_result()

    def _after_cards(self, response):
        cards = list(getattr(response, "cards", ()) or ())
        for card in cards:
            if any(item is card for item in self.owner.hand):
                self.context.apply(MoveCardAtom(
                    card, source=self.owner.hand, destination=self.target.hand))
        self.game.add_log("%s 的【好施】把 %d 张手牌交给了 %s"
                          % (self.owner.name, len(cards), self.target.name))
        return self.complete({"applied": True})


def _can_dimeng(game, player):
    if game.game_over or not player.alive:
        return False, "无法发动"
    if game.current_turn_player is not player or game.phase != "play":
        return False, "只能在你的出牌阶段发动"
    if player.skill_state.get("dimeng", "used", 0):
        return False, "本回合已经发动过"
    if len(other_alive_players(game, player)) < 2:
        return False, "需要两名其他角色"
    return True, ""


def _activate_dimeng(game, player, target=None, cards=None):
    player.skill_state.set("dimeng", "used", 1, ResetScope.TURN)
    DimengFlow(game.engine, player).start()
    return True


class DimengFlow(Flow):
    """缔盟：选两名其他角色 → 弃掉等于手牌数差的牌 → 交换他们的手牌。"""

    def __init__(self, engine, owner):
        super().__init__(engine.context)
        self.engine = engine
        self.game = engine.game
        self.owner = owner
        self.first = None
        self.second = None
        self.need = 0
        self.stage = "first"

    def begin(self):
        ask_targets(self.engine, self, source=self.owner, target=self.owner,
                    prompt="【缔盟】：请选择第一名其他角色",
                    reason="dimeng",
                    candidates=other_alive_players(self.game, self.owner),
                    min_targets=1, max_targets=1)
        return self.current_result()

    def advance(self, response=None):
        if self.stage == "first":
            return self._after_first(response)
        if self.stage == "second":
            return self._after_second(response)
        return self._after_discard(response)

    def _after_first(self, response):
        targets = list(getattr(response, "targets", ()) or ())
        if not targets:
            return self.complete({"applied": False})
        self.first = targets[0]
        self.stage = "second"
        ask_targets(self.engine, self, source=self.owner, target=self.owner,
                    prompt="【缔盟】：请选择第二名其他角色（与 %s 交换手牌）"
                           % self.first.name,
                    reason="dimeng",
                    candidates=[other for other in other_alive_players(self.game, self.owner)
                                if other is not self.first],
                    min_targets=1, max_targets=1)
        return self.current_result()

    def _after_second(self, response):
        targets = list(getattr(response, "targets", ()) or ())
        if not targets:
            return self.complete({"applied": False})
        self.second = targets[0]
        self.need = abs(len(self.first.hand) - len(self.second.hand))
        if self.need <= 0:
            return self._swap()
        candidates = list(self.owner.hand)
        if len(candidates) < self.need:
            self.game.message = "【缔盟】：手牌不足 %d 张，无法发动。" % self.need
            return self.complete({"applied": False})
        self.stage = "discard"
        ask_cards(self.engine, self, source=self.owner, target=self.owner,
                  prompt="【缔盟】：请弃置 %d 张牌（两名角色手牌数差）" % self.need,
                  reason="dimeng", candidates=candidates,
                  min_cards=self.need, max_cards=self.need)
        return self.current_result()

    def _after_discard(self, response):
        cards = list(getattr(response, "cards", ()) or ())
        for card in cards:
            if any(item is card for item in self.owner.hand):
                self.context.apply(MoveCardAtom(
                    card, source=self.owner.hand,
                    destination=self.game.deck.discard_pile))
        self.game.add_log("%s 的【缔盟】弃置 %d 张牌" % (self.owner.name, len(cards)))
        return self._swap()

    def _swap(self):
        first_cards = list(self.first.hand)
        second_cards = list(self.second.hand)
        for card in first_cards:
            self.context.apply(MoveCardAtom(
                card, source=self.first.hand, destination=self.second.hand))
        for card in second_cards:
            self.context.apply(MoveCardAtom(
                card, source=self.second.hand, destination=self.first.hand))
        self.game.add_log("%s 的【缔盟】交换了 %s 与 %s 的手牌"
                          % (self.owner.name, self.first.name, self.second.name))
        return self.complete({"applied": True})


# ==================================================
# 技能表
# ==================================================

FOREST_SKILLS = (
    active(
        "yinghun",
        "英魂",
        "回合开始阶段，若你已受伤，你可以令一名其他角色执行一项："
        "摸 X 张牌然后弃一张牌；或摸一张牌然后弃 X 张牌（X 为你已损失的体力值）。",
        can_activate=_can_yinghun,
        activate=_activate_yinghun,
        spec=ActiveSkillSpec(
            needs_target=True,
            target_candidates=lambda game, player: other_alive_players(game, player),
            target_prompt="【英魂】：请选择目标角色",
        ),
        tags=("active",),
    ),
    SkillDef(
        id="huoshou",
        name="祸首",
        description="锁定技，【南蛮入侵】对你无效；你是任何【南蛮入侵】造成伤害的来源。",
        kind=SkillKind.LOCKED,
        modifiers=(
            ModifierSpec(kind=ModifierKind.TARGET_FORBIDDEN, value=_nanman_ignore,
                         roles=("target",)),
            ModifierSpec(kind=ModifierKind.TRICK_SOURCE, value=_nanman_source),
        ),
    ),
    SkillDef(
        id="zaiqi",
        name="再起",
        description="摸牌阶段，若你已受伤，你可以放弃摸牌并改为亮出牌堆顶的 X 张牌"
        "（X 为你已损失的体力值）：其中每有一张红桃，你回复 1 点体力并弃置该牌，"
        "其余的牌收入你的手牌。",
        kind=SkillKind.PASSIVE,
        phase_replacement=PhaseReplacement(
            phase=TurnPhase.DRAW,
            prompt="【再起】：是否放弃摸牌，改为亮出牌堆顶的若干张牌？",
            can_offer=_can_zaiqi,
            apply=_apply_zaiqi,
        ),
    ),
    SkillDef(
        id="duanliang",
        name="断粮",
        description="你可以将一张黑色基本牌或黑色装备牌当【兵粮寸断】使用；"
        "你可以对与你距离 2 以内的角色使用【兵粮寸断】。",
        kind=SkillKind.VIEW_AS,
        conversions=(
            CardConversion(skill_id="duanliang", matches=_is_black_basic_or_equipment,
                           name="BINGLIANG", category="trick",
                           contexts=(PLAY_CONTEXT,)),
        ),
        modifiers=(
            ModifierSpec(kind=ModifierKind.TRICK_DISTANCE,
                         value=_duanliang_distance, roles=("player",)),
        ),
        tags=("conversion",),
    ),
    triggered(
        "xingshang",
        "行殇",
        "你可以立即获得死亡角色的所有牌。",
        factory=Xingshang,
    ),
    triggered(
        "fangzhu",
        "放逐",
        "你每受到一次伤害，你可以令一名其他角色摸 X 张牌（X 为你已损失的体力值），"
        "然后该角色将其武将牌翻面。",
        factory=Fangzhu,
    ),
    triggered(
        "songwei",
        "颂威",
        "主公技，其他魏势力角色的判定牌为黑色且生效后，你可以摸一张牌。",
        factory=Songwei,
        is_lord_skill=True,
    ),
    SkillDef(
        id="juxiang",
        name="巨象",
        description="锁定技，【南蛮入侵】对你无效；其他角色使用的【南蛮入侵】"
        "结算完毕进入弃牌堆时，你立即获得它。",
        kind=SkillKind.LOCKED,
        factory=Juxiang,
        modifiers=(
            ModifierSpec(kind=ModifierKind.TARGET_FORBIDDEN, value=_nanman_ignore,
                         roles=("target",)),
        ),
    ),
    triggered(
        "lieren",
        "烈刃",
        "当你使用【杀】造成一次伤害后，你可以与受到该伤害的角色拼点："
        "若你赢，你获得该角色的一张牌。",
        factory=Lieren,
    ),
    SkillDef(
        id="jiuchi",
        name="酒池",
        description="你可以将一张黑色手牌当【酒】使用。",
        kind=SkillKind.VIEW_AS,
        conversions=(
            CardConversion(skill_id="jiuchi", matches=_is_black, name="JIU",
                           category="basic", contexts=(PLAY_CONTEXT,)),
        ),
        tags=("conversion",),
    ),
    SkillDef(
        id="roulin",
        name="肉林",
        description="锁定技，你对女性角色、女性角色对你使用【杀】时，"
        "都需连续使用两张【闪】才能抵消。",
        kind=SkillKind.LOCKED,
        modifiers=(
            ModifierSpec(kind=ModifierKind.RESPONSE_COUNT, value=_roulin_from_source,
                         roles=("source",)),
            ModifierSpec(kind=ModifierKind.RESPONSE_COUNT, value=_roulin_from_target,
                         roles=("target",)),
        ),
    ),
    triggered(
        "benguai",
        "崩坏",
        "锁定技，结束阶段，若你的体力不是全场最少的（或之一），"
        "你须减 1 点体力或 1 点体力上限。",
        factory=Benguai,
        kind=SkillKind.LOCKED,
    ),
    triggered(
        "baonue",
        "暴虐",
        "主公技，其他群势力角色每造成一次伤害，你可以进行一次判定："
        "若结果为黑色，你回复 1 点体力。",
        factory=Baonue,
        is_lord_skill=True,
    ),
    SkillDef(
        id="wansha",
        name="完杀",
        description="锁定技，你的回合内，除你以外，只有处于濒死状态的角色才能使用【桃】。",
        kind=SkillKind.LOCKED,
        modifiers=(
            ModifierSpec(kind=ModifierKind.RESCUE_FORBIDDEN, value=_wansha_forbidden),
        ),
    ),
    active(
        "luanwu",
        "乱武",
        "限定技，出牌阶段，你可以令所有其他角色依次对与其距离最近的一名角色使用一张【杀】，"
        "无法如此做者失去 1 点体力。",
        can_activate=_can_luanwu,
        activate=_activate_luanwu,
        spec=ActiveSkillSpec(),
        tags=("active", "limited"),
    ),
    SkillDef(
        id="weimu",
        name="帷幕",
        description="锁定技，你不能成为黑色锦囊牌的目标。",
        kind=SkillKind.LOCKED,
        modifiers=(
            ModifierSpec(kind=ModifierKind.TARGET_FORBIDDEN, value=_weimu_forbidden,
                         roles=("target",)),
        ),
    ),
    SkillDef(
        id="haoshi",
        name="好施",
        description="摸牌阶段，你可以额外摸两张牌；若此时你的手牌数多于五张，"
        "你必须将一半（向下取整）的手牌交给场上除你以外手牌数最少的一名角色。",
        kind=SkillKind.PASSIVE,
        phase_replacement=PhaseReplacement(
            phase=TurnPhase.DRAW,
            prompt="【好施】：是否额外摸两张牌？",
            can_offer=_can_haoshi,
            flow=_haoshi_flow,
        ),
    ),
    active(
        "dimeng",
        "缔盟",
        "出牌阶段限一次，你可以选择其他两名角色，弃掉等同于这两名角色手牌数差的牌，"
        "然后交换他们的手牌。",
        can_activate=_can_dimeng,
        activate=_activate_dimeng,
        spec=ActiveSkillSpec(),
        tags=("active",),
    ),
)
