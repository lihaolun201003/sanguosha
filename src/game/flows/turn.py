"""Authoritative turn/phase flow with delayed-trick judgment and phase skips."""

from src.game.atoms_v2 import DrawCardsAtom, MoveCardAtom
from src.game.engine import Event, EventType, Flow, FlowStatus
from src.game.engine.pending import PendingRequestType
from src.game.rules import PHASE_NAMES, PhaseControl, TURN_PHASE_ORDER, TurnPhase
from src.game.skills.state import ResetScope

from .damage import DamageContext, DamageFlow
from .judge import JudgeFlow


class TurnFlow(Flow):
    """One character's turn.

    The flow is resumable in the interactive mode: a judgement-phase Lightning
    hit may drop the active character into dying, and the remaining phases only
    continue once that child flow is finished.
    """

    PHASES_BEFORE_PLAY = (TurnPhase.PREPARE, TurnPhase.JUDGE, TurnPhase.DRAW)

    def __init__(self, engine, player, on_complete=None, on_play_phase=None):
        super().__init__(engine.context)
        self.engine = engine
        self.game = engine.game
        self.player = player
        self.on_complete = on_complete
        self.on_play_phase = on_play_phase
        self.phase_control = PhaseControl()
        self.phase_history = []
        self._phase_index = 0
        self._paused_child = None
        self._judge_list = []
        self._judge_index = 0
        self.stage = "running"
        self._pending_replacement = None
        #: 交互式回合的阶段推进位置：begin（阶段已宣布）/ body（阶段内容）/
        #: end（阶段收尾）。事件里起的技能窗口结束后据此精确续跑，不重来一遍。
        self._phase_stage = "begin"
        self._current_phase = None
        self._notify_no_play_phase = False

    # ==================================================
    # 交互式回合
    # ==================================================

    def begin_interactive(self):
        self.status = FlowStatus.RUNNING
        self.game.current_turn_player = self.player
        self._reset_turn_scopes()
        self.context.emit(Event(EventType.TURN_START, source=self.player, payload={"flow": self}))
        self._phase_index = 0
        self._paused_child = None
        self._phase_stage = "begin"
        self._current_phase = None
        self._judge_list = []
        self._judge_index = 0
        # 走与 start / resume 同一条"当前流程"包装：阶段事件里技能开的窗口
        # 才能认领本回合当父流程（否则据守 / 崩坏 / 琴音的窗口没人等）。
        with self._entered():
            return self._advance_interactive()

    def _advance_interactive(self):
        phases = self.PHASES_BEFORE_PLAY
        while self._phase_index < len(phases):
            if not self.player.is_alive:
                # 当前行动角色在自己的回合中阵亡：回合立即结束。
                break
            if self._phase_stage == "begin":
                phase = phases[self._phase_index]
                self._phase_index += 1
                self._current_phase = phase
                self.game.turn_phase = phase
                self.game.phase = phase.value
                self._reset_phase_scopes()
                self.context.emit(Event(EventType.PHASE_START, source=self.player, payload={"phase": phase, "flow": self, "skipped": self.phase_control.is_skipped(phase)}))
                self.phase_history.append(phase)
                self._phase_stage = "body"
                if self._guard_phase_children():
                    return False
            if self._phase_stage == "body":
                self._phase_stage = "end"
                if not self.phase_control.is_skipped(self._current_phase):
                    if self._execute_phase(self._current_phase):
                        # 阶段内容（判定 / 闪电伤害）自己挂起了：恢复走
                        # _resume_after_child，这里不再往下推。
                        return False
            if self._phase_stage == "end":
                self.context.emit(Event(EventType.PHASE_END, source=self.player, payload={"phase": self._current_phase, "flow": self, "skipped": self.phase_control.is_skipped(self._current_phase)}))
                self._phase_stage = "begin"
                self._current_phase = None
                if self._guard_phase_children():
                    return False
        self.game.skipped_phases = set(self.phase_control._skipped)
        if not self.player.is_alive:
            self._finish_dead_turn()
            return False
        return self._enter_play_phase()

    def _guard_phase_children(self):
        """阶段事件里起的技能窗口（据守 / 崩坏 / 神速 / 琴音…）还没答完就先停。

        返回 True 表示已挂起；恢复由 ``resume_from_child`` 接着本阶段的
        正确位置继续（阶段开始处挂起就先把阶段跑完，阶段结束处挂起就直接
        进下一个阶段）。
        """

        if self.guard_child_flows() is None:
            return False
        self._paused_child = self.pending_request
        return True

    def resume_from_child(self, result):
        """技能窗口答完了：回到交互式回合继续推进阶段。"""

        self._resume_after_child()
        return self.current_result()

    def _enter_play_phase(self):
        if self.phase_control.is_skipped(TurnPhase.PLAY):
            self.game.turn_phase = TurnPhase.DISCARD
            self.game.phase = "discard"
            return False
        self.game.turn_phase = TurnPhase.PLAY
        self.game.phase = "play"
        self._reset_phase_scopes()
        self.context.emit(Event(EventType.PHASE_START, source=self.player, payload={"phase": TurnPhase.PLAY, "flow": self, "skipped": False}))
        self.phase_history.append(TurnPhase.PLAY)
        return True

    def _resume_after_child(self):
        """Called when a paused judgement-phase child flow has finished."""

        if self.status is FlowStatus.COMPLETED or self._paused_child is None:
            return
        self._paused_child = None
        self.pending_request = None
        self.status = FlowStatus.RUNNING
        if self._judge_index < len(self._judge_list):
            # 判定区还没走完（例如闪电伤害的濒死处理好之后），接着判定。
            if self._continue_judgement_zone():
                return
        ready = self._advance_interactive()
        if self.status is FlowStatus.WAITING:
            return
        if self.on_play_phase is not None:
            self.on_play_phase(ready)

    def _pause_for(self, child):
        self._paused_child = child
        self.wait(child)
        return True

    # ==================================================
    # 技能状态的 scope 生命周期
    # ==================================================
    #
    # 技能用 ``ResetScope`` 声明自己存的状态活多久（TURN / PHASE / ROUND）。
    # 这里在回合与阶段的真实边界上消费这些 scope —— 少一处调用，所有
    # "限一次 / 本回合生效"的技能都会永久残留（反间一辈子只能发动一次、
    # 裸衣加成永远生效）。清理发生在事件**之前**，这样技能在 TURN_START /
    # PHASE_START 里写下的新状态不会被清掉。

    def _reset_turn_scopes(self):
        state = getattr(self.player, "skill_state", None)
        if state is not None:
            state.clear_scope(ResetScope.TURN)

    def _reset_phase_scopes(self):
        state = getattr(self.player, "skill_state", None)
        if state is not None:
            state.clear_scope(ResetScope.PHASE)

    def _finish_dead_turn(self):
        self.context.emit(Event(EventType.TURN_END, source=self.player, payload={"flow": self}))
        self._reset_turn_scopes()
        self.game.skipped_phases = set(self.phase_control._skipped)
        self._notify_no_play_phase = True
        self.complete({"player": self.player, "phases": tuple(self.phase_history), "skipped": set(self.phase_control._skipped)})

    def on_settled(self, result):
        # 完成收尾统一走这里：被子流程挡住的那些 complete 只是挂起，真正的
        # 回调必须等到完成时做一次、且只做一次。
        if self._notify_no_play_phase:
            self._notify_no_play_phase = False
            if self.on_play_phase is not None:
                # 让回合驱动方知道本回合不会再有出牌阶段，需要直接收尾。
                self.on_play_phase(False)
        self.notify_on_complete(result)

    # ==================================================
    # 全自动回合
    # ==================================================

    def finish_interactive(self):
        with self._entered():
            return self._finish_interactive()

    def _finish_interactive(self):
        if self.status is FlowStatus.COMPLETED:
            return self.current_result()
        if self.game.turn_phase is TurnPhase.PLAY:
            self.context.emit(Event(EventType.PHASE_END, source=self.player, payload={"phase": TurnPhase.PLAY, "flow": self, "skipped": False}))
        # 弃牌阶段与结束阶段：真实对局里弃牌由控制器完成，但**阶段事件必须发**，
        # 否则琴音（弃牌阶段）/ 据守 / 崩坏 / 狂风 / 大雾（结束阶段）这类技能
        # 在单人交互流程里永远不会被触发。
        self._emit_discard_phase()
        self._emit_phase(TurnPhase.FINISH)
        self.context.emit(Event(EventType.TURN_END, source=self.player, payload={"flow": self}))
        self._reset_turn_scopes()
        return self.complete({"player": self.player, "phases": tuple(self.phase_history), "skipped": set(self.phase_control._skipped)})

    def _emit_discard_phase(self):
        """补发弃牌阶段的阶段事件（结算路径与全自动回合保持一致）。"""

        self.game.turn_phase = TurnPhase.DISCARD
        self.game.phase = "discard"
        self._reset_phase_scopes()
        if TurnPhase.DISCARD not in self.phase_history:
            self.phase_history.append(TurnPhase.DISCARD)
        self.context.emit(Event(EventType.PHASE_START, source=self.player, payload={"phase": TurnPhase.DISCARD, "flow": self, "skipped": False}))
        self.context.emit(Event(EventType.PHASE_END, source=self.player, payload={"phase": TurnPhase.DISCARD, "flow": self, "skipped": False}))

    def _emit_phase(self, phase):
        self.game.turn_phase = phase
        self.game.phase = phase.value
        self._reset_phase_scopes()
        if phase not in self.phase_history:
            self.phase_history.append(phase)
        skipped = self.phase_control.is_skipped(phase)
        self.context.emit(Event(EventType.PHASE_START, source=self.player, payload={"phase": phase, "flow": self, "skipped": skipped}))
        self.context.emit(Event(EventType.PHASE_END, source=self.player, payload={"phase": phase, "flow": self, "skipped": skipped}))
        return skipped

    def advance(self, response=None):
        if self.stage == "phase_replacement":
            return self._finish_phase_replacement(response)
        self.game.current_turn_player = self.player
        self._reset_turn_scopes()
        self.context.emit(Event(EventType.TURN_START, source=self.player, payload={"flow": self}))
        for phase in TURN_PHASE_ORDER:
            if not self.player.is_alive or self.game.game_over:
                break
            self.game.turn_phase = phase
            self.game.phase = phase.value
            self._reset_phase_scopes()
            self.context.emit(Event(EventType.PHASE_START, source=self.player, payload={"phase": phase, "flow": self, "skipped": self.phase_control.is_skipped(phase)}))
            self.phase_history.append(phase)
            if not self.phase_control.is_skipped(phase):
                self._execute_phase(phase)
            self.context.emit(Event(EventType.PHASE_END, source=self.player, payload={"phase": phase, "flow": self, "skipped": self.phase_control.is_skipped(phase)}))
            if self.game.game_over:
                break
        self.context.emit(Event(EventType.TURN_END, source=self.player, payload={"flow": self}))
        self._reset_turn_scopes()
        self.game.skipped_phases = set(self.phase_control._skipped)
        return self.complete({"player": self.player, "phases": tuple(self.phase_history), "skipped": set(self.phase_control._skipped)})

    # ==================================================
    # 阶段执行
    # ==================================================

    def _execute_phase(self, phase):
        """Run one phase.  Returns True when the flow must wait for a child."""

        if phase is TurnPhase.JUDGE:
            return self._resolve_judgement_zone()
        if phase is TurnPhase.DRAW:
            if self._offer_phase_replacement(phase):
                return True
            self._draw_phase()
            return False
        elif phase is TurnPhase.DISCARD:
            # 手牌上限同样走统一查询（技能可以修改）。
            while len(self.player.hand) > self.game.hand_limit(self.player):
                card = self._discardable_card()
                if card is None:
                    break
                self.context.apply(MoveCardAtom(
                    card, source=self.player.hand,
                    destination=self.game.deck.discard_pile))
        return False

    def _discardable_card(self):
        """超限弃牌时优先丢没被锁住的牌（鸡肋）。全被锁住时按原规则丢最后一张。"""

        hand = self.player.hand
        if not hand:
            return None
        for card in reversed(hand):
            if not self.game.category_forbidden(self.player, card):
                return card
        return hand[-1]

    def _draw_phase(self):
        """摸牌数走统一查询：技能可以 +1 / -1（英姿、裸衣一类）。"""

        if self.phase_control.is_skipped(TurnPhase.DRAW):
            return
        self.context.apply(DrawCardsAtom(self.player, self.game.draw_count(self.player)))

    def _offer_phase_replacement(self, phase):
        """在自己某个阶段开始前询问是否改用技能结算（突袭一类）。"""

        for player, definition in self.game.skills.phase_offers(self.player, phase):
            replacement = definition.phase_replacement
            if not replacement.can_offer(self.game, player):
                continue
            request = self.engine.pending.create(
                PendingRequestType.CONFIRM,
                source=player,
                target=player,
                prompt=replacement.prompt,
                owner_flow=self,
                request_context={
                    "reason": "phase_replacement",
                    "phase": phase.value,
                    "skill_id": definition.id,
                },
            )
            self._pending_replacement = (definition, phase)
            self.stage = "phase_replacement"
            self.wait(request)
            self.engine.present_or_auto_resolve(request)
            return True
        return False

    def _finish_phase_replacement(self, response):
        definition, phase = self._pending_replacement
        self._pending_replacement = None
        self.stage = "running"
        self.status = FlowStatus.RUNNING
        self.pending_request = None

        replacement = definition.phase_replacement
        applied = False
        child = None
        if response is not None and response.confirmed:
            if replacement.flow is not None:
                # 需要玩家补全输入的替代（突袭）：是否跳过阶段由子流程的结果决定。
                child = replacement.flow(self.game, self.player)
                applied = child is None
            elif replacement.apply is not None:
                applied = bool(replacement.apply(self.game, self.player))

        if applied:
            self.phase_control.skip(phase)

        self.context.emit(Event(EventType.PHASE_END, source=self.player,
                                payload={"phase": phase, "flow": self, "skipped": applied}))

        if child is not None:
            # 先挂起本回合，等技能流程结束后再决定是否跳过这一阶段。
            child.on_complete = (
                lambda result, phase=phase: self._finish_replacement_child(phase, result)
            )
            self._pause_for(child)
            child.start()
            return self.current_result()

        if not applied and phase is TurnPhase.DRAW:
            self._draw_phase()

        ready = self._advance_interactive()
        if self.status is FlowStatus.WAITING:
            return self.current_result()
        if self.on_play_phase is not None:
            self.on_play_phase(ready)
        return self.current_result()

    def _finish_replacement_child(self, phase, result):
        """阶段替代的子流程结束：按它的结果决定跳过阶段还是照常结算。

        回调拿到的是 ``FlowResult``（流程的完成值在它的 ``value`` 里），不是
        子流程返回的那个字典——必须顺着 ``value`` 取，否则"是否替代成功"
        永远读成一个不存在的属性，阶段替代会整个失效。
        """

        value = getattr(result, "value", result)
        applied = bool(isinstance(value, dict) and value.get("applied"))
        if applied:
            self.phase_control.skip(phase)
        elif phase is TurnPhase.DRAW:
            self._draw_phase()
        self._resume_after_child()

    def _resolve_judgement_zone(self):
        # Later delayed tricks resolve first; the order is explicit, not an
        # accidental forward-list iteration.  改判窗口可能让某次判定暂停，
        # 这里用索引记录进度，恢复后从下一张继续。
        self._judge_list = [
            delayed for delayed in reversed(self.player.judgement_zone)
            if delayed in self.player.judgement_zone
        ]
        self._judge_index = 0
        return self._continue_judgement_zone()

    def _continue_judgement_zone(self):
        while self._judge_index < len(self._judge_list):
            delayed = self._judge_list[self._judge_index]
            self._judge_index += 1
            if delayed not in self.player.judgement_zone or self.player.hp <= 0:
                continue
            judge = JudgeFlow(self.engine, self.player, delayed.name.lower())
            outcome = judge.start()
            if outcome.status is FlowStatus.WAITING:
                # 判定进入改判窗口：等它结束后再结算这张延时锦囊。
                judge.on_complete = (
                    lambda result, card=delayed: self._resume_judgement(card, result)
                )
                return self._pause_for(judge)
            if self._apply_delayed_result(delayed, outcome.value):
                return True
        return False

    def _resume_judgement(self, delayed, result):
        if self._apply_delayed_result(delayed, result):
            return
        if self._continue_judgement_zone():
            return
        self._resume_after_child()

    def _apply_delayed_result(self, delayed, result):
        """结算一张延时锦囊；返回 True 表示本次结算触发了新的等待。"""

        if delayed.name == "LEBU":
            if result is None or result.suit != "heart":
                self.phase_control.skip(TurnPhase.PLAY)
                self._announce_phase_skip(TurnPhase.PLAY, delayed, result)
            self.engine.discard_zone_card(delayed, self.player.judgement_zone)
        elif delayed.name == "BINGLIANG":
            if result is None or result.suit != "club":
                self.phase_control.skip(TurnPhase.DRAW)
                self._announce_phase_skip(TurnPhase.DRAW, delayed, result)
            self.engine.discard_zone_card(delayed, self.player.judgement_zone)
        elif delayed.name == "SHANDIAN":
            rank_value = {"A": 1, "J": 11, "Q": 12, "K": 13}.get(result.rank, int(result.rank) if result and str(result.rank).isdigit() else 0) if result else 0
            hit = result is not None and result.suit == "spade" and 2 <= rank_value <= 9
            if hit:
                self.engine.discard_zone_card(delayed, self.player.judgement_zone)
                damage = DamageFlow(
                    self.engine,
                    DamageContext(None, self.player, 3, nature="thunder", card=delayed),
                    on_complete=lambda _result: self._resume_after_child(),
                )
                outcome = damage.start()
                if outcome.status is FlowStatus.WAITING:
                    # 闪电造成濒死：先处理求桃流程，再继续本回合。
                    return self._pause_for(damage)
            else:
                next_player = self._next_alive_player(self.player)
                destination = next_player.judgement_zone if next_player and not any(card.name == "SHANDIAN" for card in next_player.judgement_zone) else self.game.deck.discard_pile
                if destination is self.game.deck.discard_pile:
                    self.engine.discard_zone_card(delayed, self.player.judgement_zone)
                else:
                    self.context.apply(MoveCardAtom(delayed, source=self.player.judgement_zone, destination=destination))
        return False

    def _announce_phase_skip(self, phase, delayed, result):
        """延时锦囊让某个阶段不能进行：发一条**纯表现**的结论事件。

        规则上"跳过"已经由 ``phase_control`` 决定，这条事件只负责让界面把
        "判定成功了，所以跳过出牌阶段"说清楚——它不参与任何判定，也不改变
        阶段推进（见 ui.storyboard 的演出队列）。
        """

        name = getattr(delayed, "display_name", "") or "延时锦囊"
        detail = ""
        if result is not None:
            mark = (getattr(result, "suit_name", "") or "") + str(
                getattr(result, "rank", "") or "")
            detail = "判定牌：%s" % mark if mark else ""
        self.context.emit(Event(
            EventType.PHASE_SKIPPED,
            source=self.player,
            target=self.player,
            payload={
                "player": self.player,
                "phase": TurnPhase(phase).value,
                "text": "%s 判定成功 · 跳过%s阶段" % (name, PHASE_NAMES[TurnPhase(phase)]),
                "detail": detail,
                "tone": "phase",
            },
        ))

    def _next_alive_player(self, player):
        candidate = self.game.seats.next_alive_player(player)
        if candidate is None:
            return None
        visited = set()
        while candidate not in visited:
            visited.add(candidate)
            if not any(card.name == "SHANDIAN" for card in candidate.judgement_zone):
                return candidate
            candidate = self.game.seats.next_alive_player(candidate)
            if candidate is player:
                break
        return None
