"""标准身份局：身份配比、开局规则、死亡奖惩与阵营胜负。"""

from src.game.atoms_v2 import DrawCardsAtom, MoveCardAtom, UnequipAtom
from src.game.engine import EventType
from src.game.engine.state import GameOutcome, GameResult

from ..identity import (
    IDENTITY_PLAYER_COUNTS,
    Identity,
    distribution_for,
    identity_name,
)
from .base import DeathResolution, GameMode


# 明确"针对某个人"的敌意牌：只有这些牌才算一次公开出手。
# 群体牌（南蛮 / 万箭 / 五谷）与自用牌（桃 / 无中）不指向个人，
# 记进来会把"打了全场"误读成"针对某人"。
HOSTILE_CARDS = frozenset({
    "SHA", "JUEDOU", "GUOHE", "SHUNSHOU", "HUOGONG", "JIEDAO",
    "LEBU", "BINGLIANG",
})


class IdentityMode(GameMode):
    """5～8 人标准身份局。

    规则口径见 ``docs/rules/phase_9_identity_mode_rules.md``：
    主公体力上限 +1、主公先行动、击杀反贼摸三张、主公误杀忠臣弃光牌。

    除了规则，本模式还维护一份**公开出手记录**（谁对谁出过手）：这些事
    本来就发生在桌面上，人人可见，AI 用它推断立场（谁像反贼、谁像忠臣），
    因此不会泄露任何隐藏身份。
    """

    id = "identity"
    name = "标准身份"
    description = "5～8 人，主公 / 忠臣 / 反贼 / 内奸各怀心思。"
    allowed_player_counts = IDENTITY_PLAYER_COUNTS
    uses_identities = True
    general_choice_count = 3

    LORD_BONUS_HP = 1
    REBEL_KILL_REWARD = 3

    def __init__(self, game):
        super().__init__(game)
        # 攻击者 -> {目标: 次数}；只记录公开的、指向个人的出手。
        self._hostility = {}
        self._tokens = []
        self._subscribe()

    def _subscribe(self):
        events = getattr(getattr(self.game, "context", None), "events", None)
        if events is None:
            return
        self._tokens.append(
            events.subscribe(EventType.CARD_USED, self._note_card_used, owner=self)
        )

    def _note_card_used(self, _context, event):
        # 模式已切换：旧订阅者自动失效（不清理也不会误记下一次对局）。
        if self.game.mode is not self:
            return
        card = event.payload.get("card")
        targets = event.payload.get("targets") or ()
        source = event.source
        if card is None or source is None or not targets:
            return
        if getattr(card, "name", None) not in HOSTILE_CARDS:
            return
        record = self._hostility.setdefault(source, {})
        for target in targets:
            if target is source:
                continue
            record[target] = record.get(target, 0) + 1

    # ---- 公开行为查询（AI 立场推断的唯一依据）----

    def hostility(self, actor, target):
        """actor 对 target 公开出过几次手。"""

        if actor is None or target is None:
            return 0
        return (self._hostility.get(actor) or {}).get(target, 0)

    def has_struck(self, actor, target):
        return self.hostility(actor, target) > 0

    def ever_struck_anyone(self, actor):
        """这个角色是否公开出手过（用于判断"是否已经表明立场"）。"""

        return bool(self._hostility.get(actor))

    def lord_pressure(self):
        """主公的健康度（0~1）；没有主公时返回 1.0。"""

        lord = self.lord()
        if lord is None:
            return 1.0
        return max(0.0, lord.hp) / float(max(1, lord.max_hp))

    # ==================================================
    # 身份
    # ==================================================

    def lord(self):
        for player in self.game.players:
            if getattr(player, "identity", None) is Identity.LORD:
                return player
        return None

    def players_by_identity(self, identity):
        return tuple(
            player for player in self.game.players
            if getattr(player, "identity", None) is identity
        )

    def roll_identities(self):
        """按人数配比生成并打乱身份牌（不写入玩家，供展示流程使用）。"""

        players = sorted(self.game.players, key=lambda player: player.seat)
        identities = list(distribution_for(len(players)) or ())
        if not identities:
            return ()
        self.game.rng.shuffle(identities)
        return tuple(identities)

    def apply_identities(self, identities):
        """把身份按座次写入玩家；主公身份开局公开。"""

        players = sorted(self.game.players, key=lambda player: player.seat)
        identities = tuple(identities)
        if len(identities) != len(players):
            raise ValueError("identity count does not match player count")
        for player, identity in zip(players, identities):
            player.identity = identity
            player.identity_revealed = identity is Identity.LORD
        return identities

    def assign_identities(self):
        """生成 + 写入（一次性完成，主要供测试与直接调用方使用）。"""

        return self.apply_identities(self.roll_identities())

    def prepare_setup(self):
        return self.assign_identities()

    # ==================================================
    # 开局规则
    # ==================================================

    def setup_battle(self):
        lord = self.lord()
        if lord is None:
            return None
        # 主公体力上限 +1 并被视为满体力：必须在武将绑定之后执行，
        # 因为绑定武将会把体力重置为武将上限。
        lord.max_hp += self.LORD_BONUS_HP
        lord.hp = min(lord.max_hp, lord.hp + self.LORD_BONUS_HP)
        return lord

    def first_player(self):
        return self.lord() or self.game.player

    def validate(self):
        ok, message = super().validate()
        if not ok:
            return ok, message
        if len(self.players_by_identity(Identity.LORD)) != 1:
            return False, "身份尚未分配"
        return True, ""

    # ==================================================
    # 信息
    # ==================================================

    def reveal_all(self):
        for player in self.game.players:
            if getattr(player, "identity", None) is not None:
                player.identity_revealed = True

    def result_headline(self):
        """结算面板文案：告诉真人哪一方赢了。"""

        reason = getattr(self.game.result, "reason", "")
        return {
            "LORD_SIDE_WIN": "主公与忠臣获胜",
            "REBEL_WIN": "反贼获胜",
            "RENEGADE_WIN": "内奸获胜",
        }.get(reason, "对局结束")

    def result_lines(self):
        """结算面板要展示的每名玩家身份 / 武将 / 存活情况。"""

        lines = []
        for player in sorted(self.game.players, key=lambda item: item.seat):
            identity = getattr(player, "identity", None)
            lines.append({
                "name": player.name,
                "identity": identity_name(identity),
                # 稳定 ID：只供 UI 定位武将牌 / 身份牌素材，不参与任何规则判断。
                "identity_id": getattr(identity, "value", ""),
                "general_id": player.general_id or "",
                "general": self.game.generals.get(player.general_id).name
                if self.game.generals.get(player.general_id) else "",
                "alive": bool(player.alive),
                "is_human": player is self.game.player,
            })
        return tuple(lines)

    # ==================================================
    # 死亡奖惩
    # ==================================================

    def _killer_of(self, source, dead_player):
        """击杀者：造成致命伤害的角色；无来源或自伤时为 None。"""

        if source is None or source is dead_player:
            return None
        if not hasattr(source, "identity"):
            return None
        return source

    def on_death(self, dead_player, source):
        # 阵亡即揭示身份（UI 立刻更新，不等对局结束）。
        if getattr(dead_player, "identity", None) is not None:
            dead_player.identity_revealed = True

        killer = self._killer_of(source, dead_player)
        if killer is None:
            return None

        identity = getattr(dead_player, "identity", None)
        if identity is Identity.REBEL:
            self._reward_rebel_kill(killer)
        elif identity is Identity.LOYALIST and getattr(killer, "identity", None) is Identity.LORD:
            self._punish_lord_for_loyalist(killer)
        return None

    def _reward_rebel_kill(self, killer):
        self.game.engine.context.apply(
            DrawCardsAtom(killer, self.REBEL_KILL_REWARD))
        self.game.add_log(
            killer.name + " 击杀反贼，摸 " + str(self.REBEL_KILL_REWARD) + " 张牌"
        )

    def _punish_lord_for_loyalist(self, lord):
        for card in list(lord.hand):
            self.game.engine.context.apply(
                MoveCardAtom(card, source=lord.hand, destination=self.game.deck.discard_pile))
        for slot, card in list(lord.equipment.items()):
            if card is not None:
                self.game.engine.context.apply(
                    UnequipAtom(lord, slot, self.game.deck.discard_pile))
        self.game.add_log(lord.name + " 误杀忠臣，弃置所有手牌与装备")

    # ==================================================
    # 胜负
    # ==================================================

    def resolve_death(self, flow):
        game = self.game
        dead_player = flow.dead_player
        already_over = game.game_over
        alive = [player for player in game.players if player.alive]
        human = game.player

        lord = self.lord()
        lord_alive = lord is not None and lord.alive
        rebels_alive = any(player.alive for player in self.players_by_identity(Identity.REBEL))
        renegade_alive = any(player.alive for player in self.players_by_identity(Identity.RENEGADE))

        # 内奸胜：主公已死，且全场只剩内奸一人。
        renegade_solo = (
            not lord_alive
            and not rebels_alive
            and renegade_alive
            and len(alive) == 1
            and getattr(alive[0], "identity", None) is Identity.RENEGADE
        )
        # 主忠胜：反贼与内奸全部出局，主公仍在。
        lord_side_win = lord_alive and not rebels_alive and not renegade_alive
        # 反贼胜：主公阵亡，且场上还有反贼（内奸抢不到天下）。
        rebel_win = not lord_alive and (rebels_alive or not renegade_solo)
        renegade_win = renegade_solo

        finished = already_over or lord_side_win or rebel_win or renegade_win
        if finished:
            game.game_over = True
            game.phase = "over"
            # 对局结束：身份全部公开，供结果界面展示。
            self.reveal_all()

        if already_over:
            return DeathResolution(
                finished=True, outcome=None, winner=None,
                reason="ALREADY_OVER", message="",
            )

        if not finished:
            game.message = dead_player.name + " 阵亡"
            return DeathResolution(
                finished=False, reason="ELIMINATED", message=game.message)

        if lord_side_win:
            reason, message = "LORD_SIDE_WIN", "主公与忠臣获胜"
            winner_identity = Identity.LORD
        elif renegade_win:
            reason, message = "RENEGADE_WIN", "内奸获胜"
            winner_identity = Identity.RENEGADE
        else:
            reason, message = "REBEL_WIN", "反贼获胜"
            winner_identity = Identity.REBEL

        human_identity = getattr(human, "identity", None)
        if winner_identity is Identity.LORD:
            human_won = human_identity in (Identity.LORD, Identity.LOYALIST)
        else:
            human_won = human_identity is winner_identity

        winner = human if human_won else None
        game.message = ("你获胜了！" if human_won else "你阵亡了 / 游戏失败") + "（" + message + "）"
        game.result = GameResult(
            outcome=GameOutcome.PLAYER_WIN if human_won else GameOutcome.AI_WIN,
            winner=winner,
            loser=dead_player,
            reason=reason,
        )
        game.winner = winner
        game.add_log(message)
        return DeathResolution(
            finished=True,
            outcome=game.result.outcome,
            winner=winner,
            reason=reason,
            message=game.message,
            extra={
                "winner_identity": winner_identity,
                "winner_identity_name": identity_name(winner_identity),
                "human_identity": human_identity,
                "human_won": human_won,
            },
        )
