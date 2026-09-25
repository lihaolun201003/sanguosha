"""Reusable judgment flow with a generic replacement window.

Sequence:

    draw the judge card
      → JUDGE_REVEALED
      → replacement window: every character with a replacement skill gets one
        chance, in seat order starting from the judged character
      → lock the final card, compute the result
      → JUDGE_FINISHED  （天妒一类技能在这里取走判定牌）
      → whatever is still in the processing zone goes to the discard pile

When nobody can replace, the whole flow completes synchronously, so ordinary
judgements keep their old behaviour.
"""

from dataclasses import dataclass, field

from src.game.atoms_v2 import MoveCardAtom
from src.game.engine import Event, EventType, Flow, FlowStatus
from src.game.engine.pending import PendingRequestType
from src.game.judge_presentation import JudgeOutcomeTone, judge_source


@dataclass(frozen=True)
class JudgeResult:
    card: object
    suit: str
    color: str
    rank: str
    source: object = None
    target: object = None
    reason: str = "judge"
    replaced: bool = False
    original_card: object = None
    replacement_history: tuple = ()
    # 展示语义：来源声明与结果语义由规则层（JUDGE_SOURCES）给出，
    # UI 只消费这里的结果，不自己按卡名猜规则。
    source_spec: object = None
    outcome: object = None

    @property
    def tone(self):
        return getattr(self.outcome, "tone", JudgeOutcomeTone.NEUTRAL)


@dataclass
class JudgeContext:
    """Live state of one judgement while the replacement window is open."""

    judged_player: object
    reason: str
    original_card: object
    current_card: object
    replacements: list = field(default_factory=list)   # [(player, skill_id, old, new)]
    locked: bool = False

    @property
    def replaced(self):
        return bool(self.replacements)

    @property
    def replacement_history(self):
        return tuple(self.replacements)


class JudgeFlow(Flow):
    def __init__(self, engine, owner, reason="judge", on_complete=None, spec=None,
                 card_recipient=None):
        super().__init__(engine.context)
        self.engine = engine
        self.game = engine.game
        self.owner = owner
        self.reason = reason
        # 展示声明：默认按 reason 查注册表，特殊来源可以显式传入。
        self.spec = spec or judge_source(reason)
        # 改判窗口会让判定变成可恢复流程；调用方用 on_complete 接回自己的结算。
        self.on_complete = on_complete
        self.judge_context = None
        self.replacers = []
        self.replacer_index = 0
        self.stage = "draw"
        self.result = None
        #: 判定牌最终归谁：``callable(JudgeResult) -> player | None``。
        #: 非 None 表示"这张判定牌由这名角色取走"，收尾时就不进弃牌堆。
        #: 【双雄】"获得此判定牌"、天妒一类都走这条通道；改判后拿到的是
        #: 最终生效的那张（result.card 就是锁定后的判定牌）。
        self.card_recipient = card_recipient
        #: 判定是否已经在规则上走完（闸门据此解除"判定优先"）。
        self.settled = False

    # ==================================================
    # 主流程
    # ==================================================

    def advance(self, response=None):
        if self.status is FlowStatus.COMPLETED:
            return self.current_result()
        if self.stage == "draw":
            return self._draw()
        if self.stage == "replacement":
            return self._handle_replacement(response)
        if self.stage == "finalized":
            # 判定结束后技能（颂威）的确认窗口答完了：补最后的收尾。
            return self._settle_judgement()
        raise RuntimeError("JudgeFlow cannot advance from stage " + self.stage)

    def _draw(self):
        card = self.game.deck.draw()
        if card is None:
            return self._complete_with(None)

        self.judge_context = JudgeContext(self.owner, self.reason, card, card)
        self.game.judge_context = self.judge_context
        self.game.judge_card = card
        # 判定真正开始：登记到闸门，从这一刻到收尾为止全场只允许判定输入
        # （见 src/game/judge_gate.py）。
        gate = getattr(self.game, "judge_gate", None)
        if gate is not None:
            gate.register_judge(self)
        # 判定牌进入处理区：替换与最终结算都按真实区域移动。
        if not any(item is card for item in self.game.processing_zone):
            self.game.processing_zone.append(card)

        self._emit_revealed(card)
        self.replacers = self.game.skills.judge_replacers(self.owner, self.reason)
        self.replacer_index = 0
        self.stage = "replacement"
        return self._next_replacer()

    def _emit_revealed(self, card):
        result = self._build_result(card)
        self.context.emit(Event(EventType.JUDGE_STARTED, source=self.owner,
                                payload={"reason": self.reason, "flow": self}))
        self.context.emit(Event(EventType.JUDGE_REVEALED, source=self.owner,
                                payload={"result": result, "flow": self, "context": self.judge_context}))

    def _build_result(self, card, *, final=False):
        return JudgeResult(
            card=card,
            suit=card.suit,
            color=card.card_color,
            rank=card.rank,
            source=self.owner,
            target=self.owner,
            reason=self.reason,
            replaced=self.judge_context.replaced if self.judge_context else False,
            original_card=self.judge_context.original_card if self.judge_context else card,
            replacement_history=self.judge_context.replacement_history if self.judge_context else (),
            source_spec=self.spec,
            # 翻开时不剧透结果：只有最终判定牌锁定后才计算 outcome。
            outcome=self.spec.outcome(card, self.owner) if final else None,
        )

    # ---- 替换窗口 ----

    def _next_replacer(self):
        while self.replacer_index < len(self.replacers):
            player, definition = self.replacers[self.replacer_index]
            self.replacer_index += 1
            if not player.alive or player.hp <= 0:
                continue
            replacement = definition.judge_replacement
            if replacement is None:
                continue
            candidates = list(replacement.candidates(self.game, player, self.judge_context))
            if not candidates:
                continue
            request = self.engine.pending.create(
                PendingRequestType.SELECT_CARDS,
                source=self.owner,
                target=player,
                prompt=self._replacement_prompt(player, definition),
                owner_flow=self,
                min_cards=0,
                max_cards=1,
                request_context={
                    "reason": "judge_replacement",
                    "skill_id": definition.id,
                    "candidates": candidates,
                    "zone": "hand",
                    "zone_owner": player,
                    "judged_player": self.owner,
                    "current_card": self.judge_context.current_card,
                },
            )
            self.wait(request)
            self.engine.present_or_auto_resolve(request)
            return self.current_result()
        return self._finalize()

    def _replacement_prompt(self, player, definition):
        card = self.judge_context.current_card
        identity = getattr(card, "identity_label", "") or ""
        return "【%s】是否替换判定牌？（当前判定：%s）" % (definition.name, identity)

    def _handle_replacement(self, response):
        if response is None:
            return self._next_replacer()

        player = response.actor
        skill_id = response.request.context.get("skill_id", "")
        context = self.context
        chosen = list(response.cards or ())

        if chosen:
            new_card = chosen[0]
            # 引擎不信任 UI / AI 传来的结果：窗口状态与实体牌归属都要复核。
            if not self._replacement_is_legal(player, new_card, skill_id, response.request):
                self.game.add_log("%s 的改判请求已失效，本次视为不替换" % player.name)
                return self._next_replacer()
            old_card = self.judge_context.current_card
            if any(item is old_card for item in self.game.processing_zone):
                context.apply(MoveCardAtom(
                    old_card,
                    source=self.game.processing_zone,
                    destination=self.game.deck.discard_pile,
                ))
            context.apply(MoveCardAtom(
                new_card,
                source=player.hand,
                destination=self.game.processing_zone,
            ))
            self.judge_context.current_card = new_card
            self.judge_context.replacements.append((player, skill_id, old_card, new_card))
            self.game.add_log(
                "%s 发动技能，将判定牌 %s 替换为 %s" % (
                    player.name,
                    getattr(old_card, "identity_label", "") or getattr(old_card, "display_name", "?"),
                    getattr(new_card, "identity_label", "") or getattr(new_card, "display_name", "?"),
                )
            )
            self.game.judge_card = new_card
            # 表现层据此把 Panel 里的判定牌换成新的，并标出"被改过"。
            self.context.emit(Event(
                EventType.JUDGE_REPLACED, source=self.owner, target=player,
                payload={
                    "flow": self,
                    "context": self.judge_context,
                    "reason": self.reason,
                    "player": player,
                    "skill_id": skill_id,
                    "old_card": old_card,
                    "new_card": new_card,
                    "history": self.judge_context.replacement_history,
                },
            ))

        return self._next_replacer()

    def _replacement_is_legal(self, player, card, skill_id, request):
        """改判的引擎侧校验：窗口、技能归属、存活、实体牌位置。"""

        if self.stage != "replacement" or self.judge_context is None:
            return False
        if self.judge_context.locked:
            return False
        if request is None or player is not request.target:
            return False
        if request.context.get("skill_id") != skill_id:
            return False
        if not player.alive or player.hp <= 0:
            return False
        # 替换牌必须是该角色手上真实存在的实体牌，且不能就是当前判定牌。
        if not any(item is card for item in player.hand):
            return False
        return card is not self.judge_context.current_card

    # ---- 收尾 ----

    def _finalize(self):
        self.judge_context.locked = True
        card = self.judge_context.current_card
        result = self._build_result(card, final=True)
        self.context.emit(Event(EventType.JUDGE_BEFORE_RESULT, source=self.owner, target=self.owner,
                                payload={"result": result, "flow": self, "context": self.judge_context}))
        self.context.emit(Event(EventType.JUDGE_RESULT, source=self.owner, target=self.owner,
                                payload={"result": result, "flow": self, "context": self.judge_context}))
        self.result = result
        # JUDGE_FINISHED 期间技能可以取走判定牌（天妒），也可能开一个窗口
        # （颂威的"是否让判定生效"）：窗口没答完，判定牌与判定状态都不能收。
        self.context.emit(Event(EventType.JUDGE_FINISHED, source=self.owner,
                                payload={"result": result, "flow": self, "context": self.judge_context}))
        self.stage = "finalized"
        guard = self.guard_child_flows()
        if guard is not None:
            return guard
        return self._settle_judgement()

    def _settle_judgement(self):
        """判定收尾：判定牌的最终去向，以及清理判定状态。

        判定牌默认进弃牌堆；声明了 ``card_recipient`` 的判定由那名角色取走
        （【双雄】"获得此判定牌"）。取走的一定是**最终生效**的那张：
        ``result.card`` 在改判窗口锁定时就定下来了，旧判定牌早已离开处理区。
        """

        if self.status is FlowStatus.COMPLETED:
            return self.current_result()
        result = self.result
        context = self.judge_context
        card = context.current_card if context is not None else None
        if card is not None and any(item is card for item in self.game.processing_zone):
            taker = self._card_taker(result)
            if taker is not None:
                self.context.apply(MoveCardAtom(
                    card,
                    source=self.game.processing_zone,
                    destination=taker.hand,
                ))
                self.game.add_log("%s 获得判定牌 %s" % (
                    taker.name,
                    self._card_identity(card),
                ))
            else:
                self.context.apply(MoveCardAtom(
                    card,
                    source=self.game.processing_zone,
                    destination=self.game.deck.discard_pile,
                ))
        self.game.judge_context = None
        self.game.judge_card = None
        self._release_gate()
        self.stage = "completed"
        return self._complete_with(result)

    # ---- 判定牌归属 ----

    def _card_taker(self, result):
        recipient = self.card_recipient
        if recipient is None:
            return None
        try:
            taker = recipient(result)
        except Exception as error:                      # noqa: BLE001
            # 声明方（技能）自己出错不能把判定卡在收尾这一步：判定照常收尾，
            # 判定牌按默认去向进弃牌堆，错误只记一条日志。
            self.game.add_log("判定牌归属声明出错：%s" % (error,))
            return None
        if taker is None or not getattr(taker, "alive", True):
            return None
        return taker

    @staticmethod
    def _card_identity(card):
        return (getattr(card, "identity_label", "")
                or getattr(card, "display_name", "?"))

    def _release_gate(self):
        """判定已经在规则上走完：解除"判定优先"。"""

        self.settled = True
        gate = getattr(self.game, "judge_gate", None)
        if gate is not None:
            gate.unregister_judge(self)

    def _complete_with(self, result):
        return self.complete(result)

    def on_settled(self, result):
        # 兜底：牌堆抽空一类"没有判定牌"的路径直接完成，没有走
        # ``_settle_judgement``，闸门也必须在这里解除。
        self._release_gate()
        # 判定流程的调用方接的是 **JudgeResult**（不是 FlowResult）：保持这个
        # 约定，否则每个调用点都要跟着改。
        if self.on_complete is not None:
            self.on_complete(self.result)

