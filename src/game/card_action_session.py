"""真人 / AI 共用的 Card Action 会话。

一次"用牌"在提交之前可能经历：发现动作 → 选动作 → 收 source → 选目标。
这些状态集中放在 Game 上（``pending_card_action``），不塞进 Renderer，
也不按武将在 UI 里开分叉：赵云、关羽、未来的丈八类技能都走同一套。
"""

from .conversion import PLAY_CONTEXT, RESCUE_CONTEXT, RESPONSE_CONTEXT
from .engine import Event, EventType, RespondCardAction, UseCardAction
from .view_as import STAGE_READY, STAGE_SELECT_SOURCE, STAGE_SELECT_TARGET


def _display_name(name):
    from src.card import display_name_for

    return display_name_for(name)


class CardActionSessionMixin:

    # ==================================================
    # 入口：点击一张实体牌
    # ==================================================

    def begin_card_action(self, card, source_rect=None, index=None):
        """UI / AI 点击一张实体牌时的统一入口。

        返回本次点击被判定成的结果字符串：
        ``"disabled"`` / ``"picker"`` / ``"direct"`` / ``"collect"``。
        """

        context = self.current_card_action_context()
        if context is None:
            return "disabled"

        options = self.card_action_options(card, context)
        return self._begin_with_options(
            card, tuple(options), context, source_rect, index)

    def card_action_options(self, card, context=None):
        """普通点击一张实体牌时的动作。

        **只包含正常使用**：技能转化（View-As）必须先点「发动技能」，
        不允许点一张牌就自动当别的牌用。
        """

        context = context or self.current_card_action_context()
        if context is None:
            return []
        options = []
        for option in self.card_actions.actions_for_card(context.actor, card, context):
            if option.is_conversion:
                continue
            if not context.allows(option.result_name):
                continue
            if option.enabled:
                options.append(option)
        return options

    def conversion_hint(self, card, context=None):
        """这张牌虽然普通点击不能用，但能通过某个视为技使用时的提示。"""

        context = context or self.current_card_action_context()
        if context is None:
            return ""
        for option in self.card_actions.actions_for_card(context.actor, card, context):
            if not option.is_conversion or not option.enabled:
                continue
            if not context.allows(option.result_name):
                continue
            return "【%s】需要在「发动技能」里选择后才能使用。" % (
                option.skill_name or option.skill_id)
        return ""

    def _response_answerer(self, request):
        """这条请求里的"我"是谁。

        单人请求的回答者就是 ``target``；共享响应阶段（无懈）里一条请求同时
        问好几个人，``target`` 只是"第一个有资格的人"，所以要用本机玩家自己。
        """

        if getattr(request, "is_group", False):
            return self.player
        return request.target

    def current_card_action_context(self):
        """当前 UI 状态对应的 Action 场合（PLAY / RESPONSE / RESCUE）。"""

        if self.game_over or self.busy:
            return None
        request = self.pending_request
        if request is not None:
            actor = self._response_answerer(request)
            if actor is not self.player:
                return None
            allowed = tuple(getattr(request, "allowed_cards", ()) or ())
            if request.context.get("reason") == "dying_rescue":
                return self.card_actions.rescue_context(
                    actor, request=request,
                    dying_player=request.context.get("dying_player"),
                    allowed_names=allowed,
                )
            return self.card_actions.response_context(
                actor, request=request, allowed_names=allowed)
        # legacy 响应（1v1 濒死自救等）没有 Engine Pending，但有 ResponseSystem 请求。
        if self.response.active:
            current = self.response.current
            allowed = tuple(getattr(current, "allowed_cards", ()) or ())
            if self.phase == "dying":
                return self.card_actions.rescue_context(
                    self.player, allowed_names=allowed)
            return self.card_actions.response_context(
                self.player, allowed_names=allowed)
        if self.current_turn_player is not self.player:
            return None
        if self.phase != "play":
            return None
        return self.card_actions.play_context(self.player)

    # ==================================================
    # 内部：动作选择
    # ==================================================

    def _begin_with_options(self, card, options, context, source_rect, index=None):
        if not options:
            hint = self.conversion_hint(card, context)
            if hint:
                self.message = hint
                return "disabled"
            reason = ""
            for option in self.card_actions.actions_for_card(context.actor, card, context):
                if option.disabled_reason:
                    reason = option.disabled_reason
                    break
            self.message = reason or "当前不能使用这张牌。"
            return "disabled"

        complete = [option for option in options if option.complete]
        if len(options) == 1:
            option = options[0]
            if option.complete:
                return self._resolve_card_action(option, source_rect, index)
            # 多 source 的第一张：进入收集状态
            return self._begin_source_collection(option, context, source_rect, index)

        if len(complete) == 1 and len(options) == 1:
            return self._resolve_card_action(complete[0], source_rect, index)

        self.pending_card_action = {
            "context": context,
            "picker": list(options),
            "selected": [],
            "option": None,
            "source_rect": source_rect,
            "index": index,
        }
        self.message = "请选择这张牌的使用方式。"
        return "picker"

    def _begin_source_collection(self, option, context, source_rect, index=None):
        self.pending_card_action = {
            "context": context,
            "picker": None,
            "selected": list(option.source_cards),
            "option": option,
            "source_rect": source_rect,
            "index": index,
        }
        self._refresh_source_collection_message()
        return "collect"

    def _refresh_source_collection_message(self):
        state = self.pending_card_action
        if state is None or state["option"] is None:
            return
        option = state["option"]
        need = option.min_sources - len(state["selected"])
        self.message = "【%s】：已选择 %d/%d 张牌，还需 %d 张。" % (
            option.skill_name or option.skill_id,
            len(state["selected"]), option.min_sources, need,
        )

    # ==================================================
    # Picker / 收集阶段的操作
    # ==================================================

    def card_action_picker(self):
        state = self.pending_card_action
        if state is None:
            return []
        return list(state["picker"] or ())

    def choose_card_action(self, action_id):
        state = self.pending_card_action
        if state is None or not state["picker"]:
            return False
        option = next(
            (item for item in state["picker"] if item.action_id == action_id), None)
        if option is None:
            return False
        state["picker"] = None

        if option.complete:
            rect = state["source_rect"]
            self.pending_card_action = None
            self._resolve_card_action(option, rect)
            return True

        # 多 source：把这批候选收进收集状态
        state["option"] = option
        state["selected"] = list(option.source_cards)
        self._refresh_source_collection_message()
        return True

    def toggle_card_action_source(self, card, source_rect=None):
        """多 source 转换：再点一张牌作为 source。"""

        state = self.pending_card_action
        if state is None or state["picker"]:
            return False
        option = state["option"]
        if option is None or not option.is_conversion:
            return False

        selected = state["selected"]
        if any(card is item for item in selected):
            selected[:] = [item for item in selected if item is not card]
            self._refresh_source_collection_message()
            return True

        context = state["context"]
        conversion = self.card_actions.conversion_of(option)
        if conversion is None or not conversion.matches(card):
            return False
        if not self.card_actions.zone_of(context.actor, card):
            return False

        selected.append(card)
        state["source_rect"] = source_rect or state["source_rect"]

        if len(selected) >= option.min_sources:
            options = [
                item for item in self.card_actions.actions_for_sources(
                    context.actor, selected, context)
                if item.complete and item.enabled and context.allows(item.result_name)
            ]
            if len(options) == 1:
                rect = state["source_rect"]
                self.pending_card_action = None
                self._resolve_card_action(options[0], rect)
                return True
            if len(options) > 1:
                state["picker"] = list(options)
                self.message = "请选择使用方式。"
                return True
            self.message = "这些牌不能再转换了。"
            return False

        self._refresh_source_collection_message()
        return True

    def confirm_card_action(self):
        """多 source 收齐后由「确认使用」提交。"""

        state = self.pending_card_action
        if state is None or state["picker"]:
            return False
        context = state["context"]
        selected = list(state["selected"])
        options = [
            item for item in self.card_actions.actions_for_sources(
                context.actor, selected, context)
            if item.complete and item.enabled and context.allows(item.result_name)
        ]
        if not options:
            self.message = "这些牌现在不能使用。"
            return False
        option = options[0]
        rect = state["source_rect"]
        self.pending_card_action = None
        self._resolve_card_action(option, rect)
        return True

    def cancel_card_action(self):
        """取消一次尚未提交的用牌：不弃牌、不写技能状态、不清空手牌选择以外的状态。"""

        if self.pending_card_action is None:
            return False
        had_picker = bool(self.pending_card_action.get("picker"))
        self.pending_card_action = None
        self.message = "已取消。" if had_picker else "已取消选择。"
        return True

    def card_action_ready(self):
        state = self.pending_card_action
        if state is None or state["picker"]:
            return False
        option = state["option"]
        if option is None:
            return False
        return len(state["selected"]) >= option.min_sources

    def card_action_source_ids(self):
        """当前被选中作为 source 的实体牌 id（UI 高亮用）。"""

        state = self.pending_card_action
        if state is None:
            return set()
        return {id(card) for card in state["selected"]}

    # ==================================================
    # 执行
    # ==================================================

    def _resolve_card_action(self, option, source_rect=None, index=None):
        context = self.pending_card_action["context"] if self.pending_card_action else None
        context = context or self.current_card_action_context()
        if context is None:
            return "disabled"

        ok, reason = self.card_actions.validate(
            option, sources=option.source_cards, context=context)
        if not ok:
            self.message = reason
            self.pending_card_action = None
            return "disabled"

        card = self.card_actions.effective_card(option)
        if card is None:
            self.message = "无法构造这次要使用的牌。"
            self.pending_card_action = None
            return "disabled"

        state_index = index
        if state_index is None and self.pending_card_action is not None:
            state_index = self.pending_card_action.get("index")
        self.pending_card_action = None

        if context.context == PLAY_CONTEXT:
            return self._execute_play_action(option, card, source_rect)
        return self._execute_pending_action(option, card, source_rect, state_index)

    def _execute_play_action(self, option, card, source_rect):
        metadata = {"card_action": option}
        if option.is_conversion:
            metadata["conversion"] = {
                "skill_id": option.skill_id,
                "skill_name": option.skill_name,
                "source_cards": option.source_cards,
                "result_name": option.result_name,
                "log": option.log_text,
            }
        self._begin_v2_card_targeting(card, source_rect, metadata=metadata)
        return "direct"

    def _execute_pending_action(self, option, card, source_rect, index=None):
        request = self.pending_request
        if request is None:
            # legacy 响应路径（1v1 濒死自救）：由 ResponseSystem 接手，
            # 实体牌通过原始手牌下标移除，因此这里提交的是 source card。
            if self.response.active:
                source = option.source_cards[0] if option.source_cards else card
                if not self.response.play_action(
                    index, source, source_rect, effective_card=card
                ):
                    self.message = "这次响应已经不合法了。"
                    return "disabled"
                self._announce_card_action(option)
                return "direct"
            self.message = "这次请求已经结束了。"
            return "disabled"

        try:
            result = self.submit_action(RespondCardAction(
                self._response_answerer(request), request.request_id, card, source_rect))
        except ValueError as error:
            # 提交前状态变化（牌已不在手牌 / 请求已被回答）→ 拒绝并说明原因
            self.message = "这次响应已经不合法了：" + str(error)
            return "disabled"
        if getattr(result.status, "value", None) == "cancelled":
            self.message = "这次响应已经不合法了。"
            return "disabled"

        self._announce_card_action(option)
        return "direct"

    def _announce_card_action(self, option):
        """转换真正提交后才记录日志与技能浮字。"""

        if not option.is_conversion:
            return
        log = option.log_text
        if log:
            self.add_log(log)
        self.context.emit(Event(
            EventType.SKILL_TRIGGERED,
            source=option.owner,
            payload={
                "skill_id": option.skill_id,
                "skill_name": option.skill_name,
                "card_action": option,
            },
        ))

    # ==================================================
    # View-As：先点技能，再选实体牌
    #
    # 龙胆 / 武圣 / 未来的倾国、急救都走同一条入口：
    # 技能只声明 conversion，交互由这里统一处理，UI 不认识任何具体技能。
    # ==================================================

    def view_as_options(self, context=None):
        """当前场合下可以进入的视为技：[(skill_id, allowed, reason)]。"""

        context = context or self.current_card_action_context()
        if context is None:
            return []
        actor = context.actor
        result = []
        for skill_id in self.skills.view_as_skill_ids(actor):
            options = self.card_actions.view_as_options(actor, skill_id, context)
            if options:
                result.append((skill_id, True, ""))
                continue
            definition = self.skill_registry.get(skill_id)
            name = getattr(definition, "name", skill_id)
            result.append((skill_id, False, "现在没有可以作为【%s】来源的牌" % name))
        return result

    def begin_view_as(self, skill_id):
        """点完技能后进入选牌模式（此时才高亮合法素材牌）。"""

        context = self.current_card_action_context()
        if context is None:
            return False
        definition = self.skill_registry.get(skill_id)
        if definition is None or not definition.is_view_as:
            return False
        if not self.skills.has(context.actor, skill_id):
            return False

        options = self.card_actions.view_as_options(context.actor, skill_id, context)
        if not options:
            self.message = "现在不能发动【" + definition.name + "】。"
            return False
        if not self.card_actions.assemblable(context.actor, options):
            # 素材凑不齐（例如只剩一张手牌时的丈八蛇矛）：不进入选牌模式，
            # 否则玩家会停在一个永远选不满的界面里。
            required = max(option.min_sources for option in options)
            self.message = "现在不能发动【%s】：至少需要 %d 张可以转化的牌。" % (
                definition.name, required)
            return False

        from .view_as import ViewAsSession

        self.pending_view_as = ViewAsSession(
            skill_id=skill_id,
            skill_name=definition.name,
            context=context,
            required_source_count=max(option.min_sources for option in options),
            result_name=options[0].result_name,
            candidates=tuple(options),
        )
        self.pending_skill_picker = None
        self.pending_card_action = None
        self._update_view_as_message()
        return True

    def toggle_view_as_source(self, card, source_rect=None):
        """选择 / 取消一张 source 牌；收齐后自动进入目标选择或响应提交。"""

        session = self.pending_view_as
        if session is None or session.stage != STAGE_SELECT_SOURCE:
            return False
        if any(card is item for item in session.selected_source_cards):
            session.selected_source_cards[:] = [
                item for item in session.selected_source_cards if item is not card]
            self._update_view_as_message()
            return True
        if not session.accepts(card):
            return False
        if not self.card_actions.zone_of(session.owner, card):
            return False

        session.selected_source_cards.append(card)
        if source_rect is not None:
            session.source_rect = source_rect
        if session.needs_more_sources:
            self._update_view_as_message()
            return True
        return self._commit_view_as()

    def cancel_view_as(self):
        """取消 View-As：不弃牌、不写 used、不播放技能 FX。

        取消必须覆盖**整条技能发动链**：素材选齐之后流程已经进入目标选择
        （``_commit_view_as``），那时 ``pending_view_as`` 已经被收掉，只剩
        一条挂在目标选择上的半途动作。玩家按"取消"就是要放弃这次发动，
        所以这里把由 View-As 建立的那次目标选择一并收回——否则他会留在
        一次从未提交过的攻击里，而那个状态谁也不会再清。
        """

        session = self.pending_view_as
        if session is not None:
            name = session.skill_name
            self.pending_view_as = None
            self.message = "已取消发动【" + name + "】。"
            return True
        selection = self.pending_target_selection
        metadata = (selection or {}).get("metadata") or {}
        if metadata.get("view_as") is not None:
            return self.cancel_target_selection()
        return False

    def view_as_ready(self):
        session = self.pending_view_as
        return bool(session is not None and not session.needs_more_sources)

    def view_as_source_ids(self):
        session = self.pending_view_as
        if session is None:
            return set()
        return {id(card) for card in session.selected_source_cards}

    def view_as_candidate_ids(self):
        """当前可以点选为 source 的实体牌 id（UI 高亮 / 灰化用）。"""

        session = self.pending_view_as
        if session is None:
            return set()
        ids = set()
        for option in session.candidates:
            for card in option.source_cards:
                ids.add(id(card))
        return ids

    def _update_view_as_message(self):
        session = self.pending_view_as
        if session is None:
            return
        if session.needs_more_sources:
            self.message = "【%s】：请选择 %d 张牌，将其当【%s】%s。" % (
                session.skill_name,
                session.required_source_count,
                _display_name(session.result_name),
                "打出" if session.context.is_response else "使用",
            )
            return
        self.message = "【%s】：已选择 %s。" % (
            session.skill_name, session.describe_selected())

    def _commit_view_as(self):
        session = self.pending_view_as
        context = session.context
        candidates = self.card_actions.actions_for_sources(
            context.actor, session.selected_source_cards, context)
        options = [
            option for option in candidates
            if option.skill_id == session.skill_id and option.complete and option.enabled
        ]
        if not options:
            # 说明具体原因（本回合已经出过【杀】/ 攻击距离不足…）：这些原因是
            # 结果牌的 CardEffect 给的，UI 不自己判断。
            reason = next(
                (option.disabled_reason for option in candidates
                 if option.skill_id == session.skill_id
                 and option.complete and option.disabled_reason),
                "",
            )
            self.message = reason or "这些牌现在不能使用。"
            return False

        option = options[0].with_sources(session.selected_source_cards)
        card = self.card_actions.effective_card(option)
        if card is None:
            self.message = "无法构造这次要使用的牌。"
            return False

        session.effective_card = card
        session.selected_targets = []
        rect = session.source_rect

        if context.context == "play":
            session.stage = STAGE_SELECT_TARGET
            self.pending_view_as = None
            metadata = {
                "card_action": option,
                "view_as": option,
                "conversion": {
                    "skill_id": option.skill_id,
                    "skill_name": option.skill_name,
                    "source_cards": option.source_cards,
                    "result_name": option.result_name,
                    "log": option.log_text,
                },
            }
            self._begin_v2_card_targeting(card, rect, metadata=metadata)
            return True

        session.stage = STAGE_READY
        self.pending_view_as = None
        return self._submit_view_as_response(option, card, rect)

    def _submit_view_as_response(self, option, card, source_rect):
        """响应 / 救援：把虚拟牌按原 ResponseSystem / Pending 提交。"""

        request = self.pending_request
        if request is None:
            if self.response.active:
                index = None
                for position, item in enumerate(self.player.hand):
                    if item is option.source_cards[0]:
                        index = position
                        break
                if not self.response.play_action(
                    index, option.source_cards[0], source_rect, effective_card=card
                ):
                    self.message = "这次响应已经不合法了。"
                    return False
                self._announce_card_action(option)
                return True
            self.message = "这次请求已经结束了。"
            return False

        ok, reason = self.card_actions.validate(
            option, sources=option.source_cards, context=option.context and None)
        if not ok:
            self.message = reason
            return False
        try:
            result = self.submit_action(RespondCardAction(
                self._response_answerer(request), request.request_id, card, source_rect))
        except ValueError as error:
            self.message = "这次响应已经不合法了：" + str(error)
            return False
        if getattr(result.status, "value", None) == "cancelled":
            self.message = "这次响应已经不合法了。"
            return False
        self._announce_card_action(option)
        return True

    # ==================================================
    # 供 UI 复用的小工具
    # ==================================================

    def card_action_for_ids(self, action_id):
        state = self.pending_card_action
        if state is None:
            return None
        pool = list(state["picker"] or ())
        if state["option"] is not None:
            pool.append(state["option"])
        for option in pool:
            if option.action_id == action_id:
                return option
        return None
