"""山包武将技能：姜维 / 孙策 / 张昭&张紘 / 蔡文姬 / 邓艾（+ 刘禅 / 左慈 / 张郃 的部分能力）。

未实现完整的武将在 ``generals/expansions.py`` 里标了 ``implemented=False``，
原因写在各自的 ``unavailable_reason``——不在这里放半截实现。
"""

from src.game.atoms_v2 import DrawCardsAtom, MoveCardAtom, UnequipAtom
from src.game.engine import EventType, Flow
from src.game.engine.skills import Skill, SkillBinding
from src.game.rules import TurnPhase

from ..definitions import (
    ActiveSkillSpec,
    ModifierSpec,
    SkillDef,
    SkillKind,
    active,
    triggered,
)
from ..mechanics import (
    optional_trigger,
    add_mark,
    ask_cards,
    ask_confirm,
    ask_option,
    ask_targets,
    attack_range_targets,
    awaken,
    consume_limited,
    flip_player,
    gain_skill,
    hand_cards,
    judge,
    limited_used,
    lose_hp,
    other_alive_players,
    set_kingdom,
    start_pindian,
    use_virtual,
)
from ..modifiers import ModifierKind
from ..state import ResetScope


def _cards_of(player):
    cards = list(getattr(player, "hand", ()) or ())
    for card in (getattr(player, "equipment", None) or {}).values():
        if card is not None:
            cards.append(card)
    cards.extend(list(getattr(player, "judgement_zone", ()) or ()))
    return cards


# ==================================================
# 姜维 · 挑衅 / 志继
# ==================================================


def _tiaoxin_targets(game, player):
    """使用【杀】能攻击到你的其他角色。"""

    from src.game.rules import DistanceRule

    return [
        other for other in other_alive_players(game, player)
        if DistanceRule.in_attack_range(game, other, player)
    ]


def _can_tiaoxin(game, player):
    if game.game_over or not player.alive:
        return False, "无法发动"
    if game.current_turn_player is not player or game.phase != "play":
        return False, "只能在你的出牌阶段发动"
    if player.skill_state.get("tiaoxin", "used", 0):
        return False, "本回合已经发动过"
    if not _tiaoxin_targets(game, player):
        return False, "没有能攻击到你的角色"
    return True, ""


def _activate_tiaoxin(game, player, target=None, cards=None):
    if target is None:
        return False
    player.skill_state.set("tiaoxin", "used", 1, ResetScope.TURN)
    TiaoxinFlow(game.engine, player, target).start()
    game.add_log(player.name + " 发动【挑衅】→ " + target.name)
    return True


class TiaoxinFlow(Flow):
    """挑衅：目标须对你使用一张【杀】，否则你弃置其一张牌。"""

    def __init__(self, engine, owner, target):
        super().__init__(engine.context)
        self.engine = engine
        self.game = engine.game
        self.owner = owner
        self.target = target
        self.stage = "confirm"

    def begin(self):
        ask_confirm(self.engine, self, source=self.owner, target=self.target,
                    prompt="【挑衅】：%s 要求你对%s使用一张【杀】。是否使用？"
                           % (self.owner.name, self.owner.name),
                    reason="tiaoxin")
        return self.current_result()

    def advance(self, response=None):
        if self.stage == "punish":
            return self._after_punish(response)
        sha = next((card for card in getattr(self.target, "hand", ())
                    if getattr(card, "name", None) == "SHA"), None)
        if response is not None and response.confirmed and sha is not None:
            from src.game.engine import UseCardAction

            self.game.add_log("%s 响应【挑衅】，对 %s 使用【杀】"
                              % (self.target.name, self.owner.name))
            self.engine.submit(UseCardAction(
                self.target, sha, [self.owner], ignore_usage_limit=True))
            return self.complete({"applied": True})
        return self._punish()

    def _punish(self):
        candidates = _cards_of(self.target)
        if not candidates:
            return self.complete({"applied": True})
        self.stage = "punish"
        ask_cards(self.engine, self, source=self.owner, target=self.owner,
                  prompt="【挑衅】：%s 没有使用【杀】，请选择弃置其一张牌"
                         % self.target.name,
                  reason="tiaoxin", candidates=candidates,
                  min_cards=1, max_cards=1, zone="public_pool",
                  context={"zone_owner": self.target})
        return self.current_result()

    def _after_punish(self, response):
        cards = list(getattr(response, "cards", ()) or ())
        if cards:
            card = cards[0]
            if any(item is card for item in self.target.hand):
                self.context.apply(MoveCardAtom(
                    card, source=self.target.hand,
                    destination=self.game.deck.discard_pile))
            else:
                for slot, equipped in (self.target.equipment or {}).items():
                    if equipped is card:
                        self.context.apply(UnequipAtom(
                            self.target, slot, self.game.deck.discard_pile))
                        break
            self.game.add_log("%s 的【挑衅】弃置了 %s 的一张牌"
                              % (self.owner.name, self.target.name))
        return self.complete({"applied": True})


class Zhiji(Skill):
    """觉醒技：回合开始阶段若你没有手牌，回复 1 点体力或摸两张牌，
    然后减 1 点体力上限并永久获得【观星】。"""

    id = "zhiji"
    name = "志继"

    def bindings(self):
        return (SkillBinding(EventType.PHASE_START, priority=55),)

    def can_trigger(self, context, event):
        if event.source is not self.owner or not self.owner.alive:
            return False
        if event.payload.get("phase") is not TurnPhase.PREPARE:
            return False
        if limited_used(self.owner, self.id):
            return False
        return not self.owner.hand

    def resolve(self, context, event):
        ZhijiFlow(context.services["engine"], self.owner).start()


class ZhijiFlow(Flow):
    def __init__(self, engine, owner):
        super().__init__(engine.context)
        self.engine = engine
        self.game = engine.game
        self.owner = owner
        self.stage = "choose"

    def begin(self):
        ask_option(self.engine, self, source=self.owner, target=self.owner,
                   prompt="【志继】：请选择一项",
                   reason="zhiji",
                   options=(("heal", "回复 1 点体力"), ("draw", "摸两张牌")))
        return self.current_result()

    def advance(self, response=None):
        option = str(getattr(response, "option", "") or "heal")
        if option == "draw":
            awaken(self.game, self.owner, "zhiji", max_hp_delta=-1,
                   draw=2, gain=("guanxing",), name="志继")
        else:
            awaken(self.game, self.owner, "zhiji", max_hp_delta=-1,
                   recover=1, gain=("guanxing",), name="志继")
        return self.complete({"applied": True})


# ==================================================
# 孙策 · 激昂 / 魂姿 / 制霸
# ==================================================


class Jiang(Skill):
    """使用或被使用【决斗】/ 红色【杀】时摸一张牌。"""

    id = "jiang"
    name = "激昂"

    def bindings(self):
        return (
            SkillBinding(EventType.CARD_USED, priority=10),
            SkillBinding(EventType.BECOME_TARGET, priority=10),
        )

    def can_trigger(self, context, event):
        if not self.owner.alive:
            return False
        card = event.payload.get("card")
        if card is None:
            return False
        name = getattr(card, "name", None)
        if name == "JUEDOU":
            pass
        elif name == "SHA" and getattr(card, "card_color", None) == "red":
            pass
        else:
            return False
        if event.name is EventType.CARD_USED:
            return event.source is self.owner
        return event.target is self.owner and event.source is not self.owner

    def resolve(self, context, event):
        optional_trigger(
            context, self.owner,
            prompt="【激昂】：是否摸一张牌？", reason="jiang", label="激昂",
            effect=self._draw).start()

    def _draw(self, flow):
        flow.context.apply(DrawCardsAtom(self.owner, 1))
        flow.game.add_log("%s 的【激昂】摸一张牌" % self.owner.name)
        return True


class Hunzi(Skill):
    """觉醒技：回合开始阶段若你的体力为 1，减 1 点体力上限并永久获得
    【英姿】与【英魂】。"""

    id = "hunzi"
    name = "魂姿"

    def bindings(self):
        return (SkillBinding(EventType.PHASE_START, priority=55),)

    def can_trigger(self, context, event):
        if event.source is not self.owner or not self.owner.alive:
            return False
        if event.payload.get("phase") is not TurnPhase.PREPARE:
            return False
        if limited_used(self.owner, self.id):
            return False
        return int(self.owner.hp) == 1

    def resolve(self, context, event):
        awaken(context.state, self.owner, self.id, max_hp_delta=-1,
               gain=("yingzi", "yinghun"), name="魂姿",
               note="体力为 1，减 1 点体力上限并获得【英姿】【英魂】。")


def _zhiba_targets(game, player):
    """可以与你拼点的其他吴势力角色。"""

    return [
        other for other in other_alive_players(game, player)
        if getattr(other, "kingdom", None) == "wu"
    ]


def _can_zhiba(game, player):
    if game.game_over or not player.alive:
        return False, "无法发动"
    if game.current_turn_player is not player or game.phase != "play":
        return False, "只能在你的出牌阶段发动"
    if not player.hand:
        return False, "需要一张手牌拼点"
    if not _zhiba_targets(game, player):
        return False, "没有其他吴势力角色"
    return True, ""


def _activate_zhiba(game, player, target=None, cards=None):
    if target is None:
        return False
    ZhibaFlow(game.engine, player, target).start()
    return True


class ZhibaFlow(Flow):
    """制霸：其他吴势力角色与你拼点；该角色没赢时，你可以获得双方的拼点牌。"""

    def __init__(self, engine, owner, target):
        super().__init__(engine.context)
        self.engine = engine
        self.game = engine.game
        self.owner = owner
        self.target = target
        self.stage = "confirm"

    def begin(self):
        start_pindian(self.engine, self.owner, self.target, reason="zhiba",
                      on_complete=self._after_pindian)
        return self.current_result()

    def advance(self, response=None):
        return self.complete({"applied": True})

    def _after_pindian(self, result):
        game = self.game
        if result is None or result.cancelled:
            return
        if result.initiator_wins:
            game.add_log("【制霸】：%s 没赢" % self.target.name)
            return
        game.add_log("【制霸】：%s 获得双方拼点的牌" % self.owner.name)
        for card in (result.initiator_card, result.target_card):
            if card is None:
                continue
            if any(item is card for item in game.deck.discard_pile):
                game.deck.discard_pile.remove(card)
                self.owner.hand.append(card)
        return self.complete({"applied": True})


# ==================================================
# 张昭&张紘 · 直谏 / 固政
# ==================================================


def _zhijian_equipment(player):
    return hand_cards(
        player, lambda card: getattr(card, "category", None) == "equipment")


def _zhijian_targets(game, player):
    """装备区还有空位的其他角色（不得替换原装备）。"""

    from src.player import Player

    result = []
    for other in other_alive_players(game, player):
        slots = getattr(other, "equipment", None) or {}
        if any(card is None for card in slots.values()):
            result.append(other)
    return result


def _can_zhijian(game, player):
    if game.game_over or not player.alive:
        return False, "无法发动"
    if game.current_turn_player is not player or game.phase != "play":
        return False, "只能在你的出牌阶段发动"
    if not _zhijian_equipment(player):
        return False, "手里没有装备牌"
    if not _zhijian_targets(game, player):
        return False, "没有装备区有空位的角色"
    return True, ""


def _is_zhijian_source(game, player, card):
    """直谏的素材：一张装备手牌。"""

    return getattr(card, "category", None) == "equipment"


def _activate_zhijian(game, player, target=None, cards=None):
    if target is None:
        return False
    # 装哪一张由玩家自己挑；没有素材就不发动，绝不替他挑。
    chosen = list(cards or ())
    if not chosen:
        game.message = "【直谏】：请先选择一张装备牌。"
        return False
    card = chosen[0]
    if not any(item is card for item in player.hand):
        return False
    slot = getattr(card, "subtype", None)
    if slot not in (getattr(target, "equipment", None) or {}):
        return False
    if target.get_equipment(slot) is not None:
        game.message = "【直谏】：该角色的这个装备位已被占用。"
        return False
    from src.game.atoms_v2 import EquipCardAtom

    # 先把牌移出手牌，再装进对方的装备区：两步都走原子，装备区的进出事件
    # （失去装备 / 获得装备、装备赋予技能）全部照常发出。
    for index, item in enumerate(player.hand):
        if item is card:
            player.hand.pop(index)
            break
    game.engine.context.apply(EquipCardAtom(target, card))
    game.engine.context.apply(DrawCardsAtom(player, 1))
    game.add_log("%s 发动【直谏】，将【%s】置于 %s 的装备区并摸一张牌"
                 % (player.name, getattr(card, "display_name", "?"), target.name))
    return True


class Guzheng(Skill):
    """固政：记录每个弃牌阶段的弃牌，阶段结束时把其中一张交还、其余据为己有。

    记账与结算必须在**同一个技能实例**上：记录用的临时状态不能靠第二个技能
    去维护，否则解绑、技能栏显示与"谁拥有它"全部会对不上。
    """

    id = "guzheng"
    name = "固政"

    def bindings(self):
        return (
            SkillBinding(EventType.PHASE_START, priority=-10),
            SkillBinding(EventType.CARD_DISCARDED, priority=-10),
            SkillBinding(EventType.PHASE_END, priority=5),
        )

    def can_trigger(self, context, event):
        if event.name is EventType.PHASE_START:
            return event.payload.get("phase") is TurnPhase.DISCARD
        if event.name is EventType.CARD_DISCARDED:
            return getattr(context.state, "turn_phase", None) is TurnPhase.DISCARD
        if not self.owner.alive:
            return False
        if event.payload.get("phase") is not TurnPhase.DISCARD:
            return False
        loser = event.source
        return loser is not None and loser is not self.owner and bool(self._pile(context.state, loser))

    @staticmethod
    def _pile(game, loser):
        """该角色在这个弃牌阶段里弃掉、且还在弃牌堆里的牌。

        记账放在 ``skill_state`` 里而不是玩家对象的私有属性上：重开一局时
        ``skills.clear()`` 会连技能状态一起清干净，不会留下上一局的牌引用
        （私有属性不会随重开消失，是真实的残留）。
        """

        state = getattr(loser, "skill_state", None)
        if state is None:
            return []
        return [
            card for card in state.get("guzheng", "pile", ()) or ()
            if any(item is card for item in game.deck.discard_pile)
        ]

    def resolve(self, context, event):
        if event.name is EventType.PHASE_START:
            player = event.source
            state = getattr(player, "skill_state", None)
            if state is not None:
                state.set("guzheng", "pile", (), ResetScope.TURN)
            return
        if event.name is EventType.CARD_DISCARDED:
            owner = event.payload.get("owner")
            card = event.payload.get("card")
            state = getattr(owner, "skill_state", None) if owner is not None else None
            if state is None or card is None:
                return
            pile = list(state.get("guzheng", "pile", ()) or ())
            pile.append(card)
            state.set("guzheng", "pile", tuple(pile), ResetScope.TURN)
            return
        loser = event.source
        pile = self._pile(context.state, loser)
        if pile:
            GuzhengFlow(context.services["engine"], self.owner, loser, pile).start()


class GuzhengFlow(Flow):
    """固政：获得该角色于此阶段弃置的牌，然后把其中的一张交还给他。

    官方结算顺序是"**先返还其中一张**，才可拿其余牌"，所以：

    * 放弃 = 一张都不动。以前请求写 ``min_cards=0``，空回答会被当成"一张都
      不还"，紧接着却把整堆牌塞进固政拥有者手里（实测点放弃后二张拿到两张、
      弃牌者一张未得）；
    * 返还的那张必须此刻**还在弃牌堆里**，否则这次发动整体作废（不吞牌）；
    * 单张牌的场景只能返还，不能自己留下。
    """

    def __init__(self, engine, owner, loser, pile):
        super().__init__(engine.context)
        self.engine = engine
        self.game = engine.game
        self.owner = owner
        self.loser = loser
        self.pile = list(pile)
        self.stage = "select"

    def begin(self):
        candidates = [card for card in self.pile if self._in_pile(card)]
        if not candidates:
            return self.complete({"applied": False})
        ask_cards(self.engine, self, source=self.owner, target=self.owner,
                  prompt="【固政】：请选择交还给 %s 的一张牌（其余归你）" % self.loser.name,
                  reason="guzheng", candidates=candidates,
                  min_cards=1, max_cards=1, zone="public_pool",
                  # 可以放弃：放弃就是这次不发动，任何牌都不移动。
                  context={"cancellable": True})
        return self.current_result()

    def advance(self, response=None):
        cards = list(getattr(response, "cards", ()) or ())
        returned = cards[0] if cards else None
        if returned is None or not self._in_pile(returned):
            self.game.add_log("%s 放弃发动【固政】，这些弃牌留在弃牌堆"
                              % self.owner.name)
            return self.complete({"applied": False})
        # 先返还：这一步不成立就没有"获得其余牌"这回事。
        self._move(returned, self.loser)
        taken = 0
        for card in list(self.pile):
            if card is returned or not self._in_pile(card):
                continue
            self._move(card, self.owner)
            taken += 1
        self.game.add_log("%s 的【固政】交还 %s 一张牌，并获得其余 %d 张"
                          % (self.owner.name, self.loser.name, taken))
        return self.complete({"applied": True, "taken": taken})

    def _in_pile(self, card):
        return any(item is card for item in self.game.deck.discard_pile)

    def _move(self, card, player):
        # 弃牌堆里的牌换主人：走统一原子（raw list 操作不发任何事件）。
        self.context.apply(MoveCardAtom(
            card, source=self.game.deck.discard_pile,
            destination=player.hand, reason="guzheng"))


class Beige(Skill):
    """一名角色受到【杀】造成的伤害后，弃一张牌令其判定，按花色结算。"""

    id = "beige"
    name = "悲歌"

    def bindings(self):
        return (SkillBinding(EventType.DAMAGE_SETTLED, priority=15),)

    def can_trigger(self, context, event):
        if not self.owner.alive:
            return False
        damage = event.payload.get("damage")
        if damage is None or int(event.payload.get("amount", 0) or 0) <= 0:
            return False
        card = getattr(damage, "card", None)
        if card is None or getattr(card, "name", None) != "SHA":
            return False
        target = getattr(damage, "target", None)
        return target is not None and bool(_cards_of(self.owner))

    def resolve(self, context, event):
        BeigeFlow(context.services["engine"], self.owner,
                  event.payload["damage"]).start()


class BeigeFlow(Flow):
    """悲歌：弃一张牌 → 判定 → 按花色让受害者/伤害来源各受其果。"""

    def __init__(self, engine, owner, damage):
        super().__init__(engine.context)
        self.engine = engine
        self.game = engine.game
        self.owner = owner
        self.damage = damage
        self.stage = "select"

    def begin(self):
        candidates = _cards_of(self.owner)
        if not candidates:
            return self.complete({"applied": False})
        ask_cards(self.engine, self, source=self.owner, target=self.owner,
                  prompt="【悲歌】：请弃置一张牌为受伤角色判定",
                  reason="beige", candidates=candidates, min_cards=1, max_cards=1)
        return self.current_result()

    def advance(self, response=None):
        if self.stage == "club":
            return self._after_club(response)
        return self._after_cost(response)

    def _after_cost(self, response):
        cards = list(getattr(response, "cards", ()) or ())
        if not cards:
            return self.complete({"applied": False})
        card = cards[0]
        if any(item is card for item in self.owner.hand):
            self.context.apply(MoveCardAtom(
                card, source=self.owner.hand,
                destination=self.game.deck.discard_pile))
        else:
            for slot, equipped in (self.owner.equipment or {}).items():
                if equipped is card:
                    self.context.apply(UnequipAtom(
                        self.owner, slot, self.game.deck.discard_pile))
                    break
        flow, result = judge(self.engine, self.damage.target, "beige")
        if result is None:
            flow.on_complete = self._after_judge
            self.wait(flow)
            return self.current_result()
        return self._after_judge(result)

    def _after_judge(self, result):
        game = self.game
        if result is None:
            return self.complete({"applied": False})
        victim = getattr(self.damage, "target", None)
        source = getattr(self.damage, "source", None)
        suit = getattr(result, "suit", None)
        if suit == "heart" and victim is not None:
            from src.game.atoms_v2 import RecoverHpAtom

            self.context.apply(RecoverHpAtom(victim, 1))
            game.add_log("【悲歌】红桃：%s 回复 1 点体力" % victim.name)
        elif suit == "diamond" and victim is not None:
            self.context.apply(DrawCardsAtom(victim, 2))
            game.add_log("【悲歌】方块：%s 摸两张牌" % victim.name)
        elif suit == "club" and source is not None:
            return self._ask_club_discard(source)
        elif suit == "spade" and source is not None:
            flip_player(game, source, reason="悲歌")
            game.add_log("【悲歌】黑桃：%s 的武将牌翻面" % source.name)
        return self.complete({"applied": True})

    # ---- 梅花：弃哪两张由**伤害来源自己**挑 ----

    def _ask_club_discard(self, source):
        """判定完成后向伤害来源询问——以前是代码反复取他手牌的最后一张。

        候选是手牌与装备区的全部可弃牌；不足两张时按实际数量处理。
        """

        candidates = _cards_of(source)
        if not candidates:
            self.game.add_log("【悲歌】梅花：%s 没有牌可弃" % source.name)
            return self.complete({"applied": True})
        count = min(2, len(candidates))
        self.stage = "club"
        ask_cards(self.engine, self, source=self.owner, target=source,
                  prompt="【悲歌】梅花：请选择要弃置的 %d 张牌" % count,
                  reason="beige", candidates=candidates,
                  min_cards=count, max_cards=count)
        return self.current_result()

    def _after_club(self, response):
        game = self.game
        source = getattr(self.damage, "source", None)
        discarded = 0
        for card in list(getattr(response, "cards", ()) or ()):
            if source is None:
                break
            if any(item is card for item in source.hand):
                self.context.apply(MoveCardAtom(
                    card, source=source.hand,
                    destination=game.deck.discard_pile))
                discarded += 1
                continue
            for slot, equipped in (source.equipment or {}).items():
                if equipped is card:
                    self.context.apply(UnequipAtom(
                        source, slot, game.deck.discard_pile))
                    discarded += 1
                    break
        name = getattr(source, "name", "伤害来源")
        game.add_log("【悲歌】梅花：%s 弃置 %d 张牌" % (name, discarded))
        return self.complete({"applied": True})


class Duanchang(Skill):
    """锁定技：杀死你的角色失去当前的所有武将技能。"""

    id = "duanchang"
    name = "断肠"

    def bindings(self):
        return (SkillBinding(EventType.DEATH, priority=-50),)

    def can_trigger(self, context, event):
        if event.target is not self.owner:
            return False
        killer = event.source
        return killer is not None and killer is not self.owner and killer.alive

    def resolve(self, context, event):
        game = context.state
        killer = event.source
        from ..mechanics import lose_all_skills

        lose_all_skills(game, killer, reason="断肠")
        game.add_log("%s 被【断肠】，失去所有武将技能" % killer.name)


# ==================================================
# 邓艾 · 屯田 / 凿险 / 急袭
# ==================================================

TIAN_ZONE = "tian"


def _is_not_heart(card):
    return getattr(card, "suit", None) != "heart"


class Tuntian(Skill):
    """回合外失去牌时判定，非红桃的判定牌置于武将牌上，称为「田」。"""

    id = "tuntian"
    name = "屯田"

    def bindings(self):
        return (
            SkillBinding(EventType.CARD_DISCARDED, priority=5),
            SkillBinding(EventType.CARD_LOST, priority=5),
        )

    def can_trigger(self, context, event):
        if not self.owner.alive:
            return False
        if event.payload.get("owner") is not self.owner:
            return False
        # 「田」本身离开武将牌不算"失去牌"（那是技能自己的结算）。
        if event.payload.get("from") is self.owner.placed_zone(TIAN_ZONE):
            return False
        return context.state.current_turn_player is not self.owner

    def resolve(self, context, event):
        TuntianFlow(context.services["engine"], self.owner).start()


class TuntianFlow(Flow):
    def __init__(self, engine, owner):
        super().__init__(engine.context)
        self.engine = engine
        self.game = engine.game
        self.owner = owner
        self.stage = "judge"

    def begin(self):
        return self._begin_judge()

    def _begin_judge(self):
        flow, result = judge(self.engine, self.owner, "tuntian")
        if result is None:
            flow.on_complete = self._after_judge
            self.wait(flow)
            return self.current_result()
        return self._after_judge(result)

    def advance(self, response=None):
        return self.complete({"applied": True})

    def _after_judge(self, result):
        game = self.game
        card = getattr(result, "card", None)
        if card is not None and _is_not_heart(card):
            # 判定牌可能已经被别的技能取走（【天妒】【巨象】……）。那种情况下
            # 必须**整个跳过**：牌已经名花有主，再放一次会让同一张牌同时挂在
            # 两个牌区（归属不变量会直接报重复）。以前的注释写的就是"取走就跳过"，
            # 但代码只跳过了 remove、照样 place_card——注释和实现不一致。
            if not any(item is card for item in game.deck.discard_pile):
                return self.complete({"applied": True})
            game.deck.discard_pile.remove(card)
            self.owner.place_card(TIAN_ZONE, card)
            game.add_log("%s 的【屯田】将 %s 置于武将牌上（共 %d 张「田」）"
                         % (self.owner.name,
                            getattr(card, "identity_label", "") or "?",
                            self.owner.placed_count(TIAN_ZONE)))
        else:
            game.add_log("%s 的【屯田】判定为红桃，不获得「田」" % self.owner.name)
        return self.complete({"applied": True})


def _tuntian_distance(game, query):
    """每有一张「田」，计算与其他角色的距离 -1。"""

    player = query.get("player")
    if player is None:
        return 0
    return -player.placed_count(TIAN_ZONE)


class Zaoxian(Skill):
    """觉醒技：「田」达到 3 张时减 1 点体力上限并永久获得【急袭】。"""

    id = "zaoxian"
    name = "凿险"

    def bindings(self):
        return (SkillBinding(EventType.PHASE_START, priority=55),)

    def can_trigger(self, context, event):
        if event.source is not self.owner or not self.owner.alive:
            return False
        if event.payload.get("phase") is not TurnPhase.PREPARE:
            return False
        if limited_used(self.owner, self.id):
            return False
        return self.owner.placed_count(TIAN_ZONE) >= 3

    def resolve(self, context, event):
        awaken(context.state, self.owner, self.id, max_hp_delta=-1,
               gain=("jixi",), name="凿险")


def _tian_cards(player):
    """邓艾武将牌上的「田」（实体牌）。"""

    return list(player.placed_zone(TIAN_ZONE))


def _jixi_targets(game, player):
    """【急袭】的合法目标 = 【顺手牵羊】的合法目标（距离 1 以内、区域里有牌）。

    以前候选是"全部其他存活角色"，距离 2 以上或没有牌可拿的人也会被列出来，
    玩家点得中、提交却被引擎拒——目标合法性必须与【顺手牵羊】本身同一判据。
    """

    from src.game.engine import UseCardAction

    from ..mechanics import virtual_card

    probe = virtual_card("SHUNSHOU", player, (), category="trick")
    effect = game.engine.card_effects.get(probe)
    if effect is None:                                    # pragma: no cover
        return []
    result = []
    for other in other_alive_players(game, player):
        valid, _reason = effect.can_use(
            game, UseCardAction(player, probe, [other], ignore_usage_limit=True))
        if valid:
            result.append(other)
    return result


def _can_jixi(game, player):
    if game.game_over or not player.alive:
        return False, "无法发动"
    if game.current_turn_player is not player or game.phase != "play":
        return False, "只能在你的出牌阶段发动"
    if not game.skills.has(player, "jixi"):
        return False, "还没有获得【急袭】"
    if not _tian_cards(player):
        return False, "没有「田」"
    if not _jixi_targets(game, player):
        return False, "没有符合【顺手牵羊】条件的目标"
    return True, ""


def _activate_jixi(game, player, target=None, cards=None):
    """急袭：把一张「田」当【顺手牵羊】使用。

    用哪一张「田」由邓艾自己选（以前默认拿第一张）：这里开一个选牌窗口，
    选完才真正把「田」移出武将牌区。取消 = 什么都不发生，那张「田」留在
    原区域（旧实现会先把它塞进手牌，用不出去也回不去）。
    """

    if target is None or not any(
            other is target for other in _jixi_targets(game, player)):
        return False
    if not _tian_cards(player):
        return False
    JixiFlow(game.engine, player, target).start()
    return True


class JixiFlow(Flow):
    """急袭：先选一张「田」，再当作【顺手牵羊】使用。"""

    def __init__(self, engine, owner, target):
        super().__init__(engine.context)
        self.engine = engine
        self.game = engine.game
        self.owner = owner
        self.target = target

    def begin(self):
        candidates = _tian_cards(self.owner)
        if not candidates:
            return self.complete({"applied": False})
        ask_cards(self.engine, self, source=self.owner, target=self.owner,
                  prompt="【急袭】：请选择要当【顺手牵羊】使用的一张「田」",
                  reason="jixi", candidates=candidates,
                  min_cards=0, max_cards=1,
                  context={"cancellable": True})
        return self.current_result()

    def advance(self, response=None):
        cards = list(getattr(response, "cards", ()) or ())
        card = cards[0] if cards else None
        if card is None:
            self.game.add_log("%s 放弃发动【急袭】" % self.owner.name)
            return self.complete({"applied": False})
        pile = _tian_cards(self.owner)
        if not any(item is card for item in pile):
            # 选牌期间这张「田」已经不在武将牌上：这次不发动，不动任何牌。
            return self.complete({"applied": False})
        if not any(other is self.target for other in _jixi_targets(self.game, self.owner)):
            self.game.add_log("【急袭】的目标已经不再合法，本次不发动")
            return self.complete({"applied": False})
        from src.game.engine import UseCardAction

        from ..mechanics import virtual_card

        # 「田」先回到手牌，再作为【顺手牵羊】的实体来源牌进入处理区；
        # 出牌的来源牌移动、结算、进弃牌堆全部交给通用流程（只移动这一次）。
        self.owner.take_placed_card(TIAN_ZONE, card)
        self.owner.hand.append(card)
        virtual = virtual_card("SHUNSHOU", self.owner, (card,), category="trick")
        self.engine.submit(UseCardAction(
            self.owner, virtual, [self.target], ignore_usage_limit=True))
        self.game.add_log("%s 发动【急袭】，将一张「田」当【顺手牵羊】使用"
                          % self.owner.name)
        return self.complete({"applied": True})


# ==================================================
# 技能表
# ==================================================

MOUNTAIN_SKILLS = (
    active(
        "tiaoxin",
        "挑衅",
        "出牌阶段限一次，你可以指定一名使用【杀】能攻击到你的角色，"
        "该角色需对你使用一张【杀】，否则你弃置其一张牌。",
        can_activate=_can_tiaoxin,
        activate=_activate_tiaoxin,
        spec=ActiveSkillSpec(
            needs_target=True,
            target_candidates=_tiaoxin_targets,
            target_prompt="【挑衅】：请选择能攻击到你的角色",
        ),
        tags=("active",),
    ),
    triggered(
        "zhiji",
        "志继",
        "觉醒技，回合开始阶段，若你没有手牌，你回复 1 点体力或摸两张牌，"
        "然后减 1 点体力上限，并永久获得技能【观星】。",
        factory=Zhiji,
        tags=("awakening",),
    ),
    triggered(
        "jiang",
        "激昂",
        "每当你使用或被使用一张【决斗】或红色【杀】时，你可以摸一张牌。",
        factory=Jiang,
    ),
    triggered(
        "hunzi",
        "魂姿",
        "觉醒技，回合开始阶段，若你的体力为 1，你须减 1 点体力上限，"
        "并永久获得技能【英姿】和【英魂】。",
        factory=Hunzi,
        tags=("awakening",),
    ),
    active(
        "zhiba",
        "制霸",
        "主公技，其他吴势力角色可以在其出牌阶段与你进行一次拼点；"
        "若该角色没赢，你可以获得双方拼点的牌。",
        can_activate=_can_zhiba,
        activate=_activate_zhiba,
        spec=ActiveSkillSpec(
            needs_target=True,
            target_candidates=_zhiba_targets,
            target_prompt="【制霸】：请选择与你拼点的吴势力角色",
        ),
        tags=("active", "lord"),
        is_lord_skill=True,
    ),
    active(
        "zhijian",
        "直谏",
        "出牌阶段，你可以将一张装备牌置于一名其他角色的装备区里（不得替换原装备），"
        "然后摸一张牌。",
        can_activate=_can_zhijian,
        activate=_activate_zhijian,
        spec=ActiveSkillSpec(
            needs_target=True,
            target_candidates=_zhijian_targets,
            target_prompt="【直谏】：请选择获得装备的角色",
            # 装备牌的去向是"装进对方的装备区"，不是弃置、也不是交手牌，
            # 所以它按素材处理，由技能自己的结算负责移动。
            cost_cards=1,
            keep_cards=True,
            cost_prompt="【直谏】：请选择一张装备牌置入目标的装备区",
            cost_candidates=_is_zhijian_source,
        ),
        tags=("active", "card_transfer"),
    ),
    triggered(
        "guzheng",
        "固政",
        "其他角色的弃牌阶段结束时，你可以将其中一张弃牌交还该角色，"
        "并获得其余于此阶段中弃掉的牌。",
        factory=Guzheng,
    ),
    triggered(
        "beige",
        "悲歌",
        "一名角色受到【杀】造成的一次伤害后，你可以弃置一张牌并令其进行判定："
        "红桃则该角色回复 1 点体力；方块则该角色摸两张牌；"
        "梅花则伤害来源弃两张牌；黑桃则伤害来源将其武将牌翻面。",
        factory=Beige,
    ),
    triggered(
        "duanchang",
        "断肠",
        "锁定技，杀死你的角色失去当前的所有武将技能。",
        factory=Duanchang,
        kind=SkillKind.LOCKED,
    ),
    SkillDef(
        id="tuntian",
        name="屯田",
        description="每当你于回合外失去牌时，你可以进行一次判定，"
        "将非红桃的判定牌置于你的武将牌上，称为「田」；"
        "每有一张「田」，你计算与其他角色的距离减一。",
        kind=SkillKind.PASSIVE,
        factory=Tuntian,
        modifiers=(
            ModifierSpec(kind=ModifierKind.DISTANCE_OUTGOING,
                         value=_tuntian_distance, roles=("source",)),
        ),
    ),
    triggered(
        "zaoxian",
        "凿险",
        "觉醒技，回合开始阶段，若你的「田」数达到 3 张或更多，"
        "你须减 1 点体力上限，并永久获得技能【急袭】。",
        factory=Zaoxian,
        tags=("awakening",),
    ),
    active(
        "jixi",
        "急袭",
        "出牌阶段，你可以将一张「田」当【顺手牵羊】使用。",
        can_activate=_can_jixi,
        activate=_activate_jixi,
        spec=ActiveSkillSpec(
            needs_target=True,
            target_candidates=_jixi_targets,
            target_prompt="【急袭】：请选择【顺手牵羊】的目标",
        ),
        tags=("active", "granted"),
    ),
)
