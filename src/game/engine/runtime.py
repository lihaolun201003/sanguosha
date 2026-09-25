"""Engine V2 action dispatcher and Legacy UI/animation adapters."""

from src.actions import MoveCardAction, WaitAction
from src.constants import (
    ENEMY_HAND_RECT,
    PLAYER_HAND_SOURCE_RECT,
    RESPONSE_CARD_RECT,
    TABLE_CARD_RECT,
    DISCARD_PILE_RECT,
    DRAW_PILE_RECT,
)

from src.game.atoms_v2 import MoveCardAtom
from src.game.card_effects import create_default_registry
from src.game.compat import LegacyEquipmentCompatibility
from src.game.equipment_skills.system import EquipmentSkillController

from .domain_actions import (
    ActivateSkillAction,
    ChooseOptionAction,
    ConfirmPendingAction,
    PassPendingAction,
    RespondCardAction,
    SelectCardsAction,
    SelectTargetsAction,
    UseCardAction,
)
from .events import Event, EventType
from .flows import FlowResult, FlowStatus
from .pending import PendingManager, PendingRequestType, PendingResolution


# 命名落位的设计默认值：只有完全没有 UI（无头测试）时才会用到。
# 真实对局里每个落位都由 UI 按当前分辨率解析（见 GameEngine.placement）。
PLACEMENT_FALLBACKS = {
    "table_card": TABLE_CARD_RECT,
    "response_card": RESPONSE_CARD_RECT,
    "discard_pile": DISCARD_PILE_RECT,
    "draw_pile": DRAW_PILE_RECT,
    "player_hand": PLAYER_HAND_SOURCE_RECT,
    "opponent_hand": ENEMY_HAND_RECT,
}


class GameEngine:
    def __init__(self, context):
        self.context = context
        self.game = context.state
        self.pending = PendingManager(context)
        self.compatibility = LegacyEquipmentCompatibility(self.game)
        self.card_effects = create_default_registry()
        self.equipment = EquipmentSkillController(self)
        self.active_flows = []
        #: 正在执行的流程栈（Flow.start / resume 期间压栈）：事件回调里启动的
        #: 技能流程靠它认领父流程，父流程据此等待它结束（见 Flow.guard_child_flows）。
        self._flow_stack = []
        #: "等结算清空后再继续"的回调（AI 的回合推进）。
        #:
        #: 为什么不用动作队列重排：``ActionQueue.update`` 会在同一帧里连续执行
        #: 立即完成的动作，反复排进去就是同一帧里空转；而"AI 对当前请求的回答"
        #: 也排在同一条队列里，把队列整体冻住会互相锁死（AI 等自己回答的请求）。
        #: 这里改成登记回调，等请求真正解决、栈清空时由引擎唤醒。
        self._deferred_resumes = []
        #: 共享响应阶段（无懈）的窗口计数：整个阶段稳定不变，轮次另编号。
        self._response_windows = 0
        context.services["engine"] = self

    def reset(self):
        self.pending.clear()
        self.active_flows.clear()
        self._flow_stack.clear()
        self._deferred_resumes.clear()

    def defer_turn_resume(self, callback):
        """登记一个"等没有待回答请求之后再继续"的回调。

        用于**推进回合**的动作（AI 接着出牌 / 收尾进入下一个角色）。它与
        "解决当前请求"的动作必须区分开：后者照样可以马上排进动作队列，
        否则等待中的请求永远拿不到答案。
        """

        if callback is not None:
            self._deferred_resumes.append(callback)

    def _flush_deferred_resumes(self):
        """没有任何待回答请求了：唤醒等在这条边界上的回合推进。"""

        if self.pending.active or not self._deferred_resumes:
            return
        callbacks, self._deferred_resumes = self._deferred_resumes, []
        for callback in callbacks:
            callback()

    def enter_flow(self, flow):
        """一条流程开始执行（或从等待里恢复）：把它挂到当前栈顶流程名下。

        栈顶就是"这条流程是在谁的结算过程中被启动的"——技能在事件回调里
        起的流程因此能自动找到该等它的父结算。
        """

        parent = self._flow_stack[-1] if self._flow_stack else None
        self._flow_stack.append(flow)
        # 只有**还在跑**的流程才当父：子流程结束时会回调父流程，那一刻栈顶
        # 可能是"正在收尾、已经完成"的那条流程，认它当父会造出反向引用。
        if (parent is not None and parent is not flow
                and parent.status in (FlowStatus.RUNNING, FlowStatus.WAITING)):
            parent.adopt_child(flow)

    def leave_flow(self, flow):
        if self._flow_stack and self._flow_stack[-1] is flow:
            self._flow_stack.pop()
            return
        # 异常路径下栈可能已经错位：按值兜底移除，别让栈留下幽灵节点。
        if flow in self._flow_stack:
            self._flow_stack.remove(flow)

    def open_response_window(self):
        """开一个新的共享响应阶段，返回它的稳定窗口号。"""

        self._response_windows += 1
        return self._response_windows

    def submit(self, action):
        # 判定优先：判定没走完之前只接受判定流程自己要的输入。
        # 这里是权威闸门——真人 UI、AI 控制器、LAN 客户端提交的动作
        # 全部经过它，所以远端不能靠绕过界面抢先出牌。
        gate = getattr(self.game, "judge_gate", None)
        if gate is not None:
            reason = gate.guard(action)
            if reason:
                # 拒绝就是拒绝：不改任何状态、不推进任何流程，只把原因写到
                # 提示里。返回"已取消"而不是抛异常——AI 的推进路径里有几条
                # 靠 status == cancelled 才能接回回合，抛异常会把它们打断。
                self.game.message = reason
                return FlowResult(FlowStatus.CANCELLED, None)
        if isinstance(action, UseCardAction):
            return self._use_card(action)
        if isinstance(action, RespondCardAction):
            return self._respond_card(action)
        if isinstance(action, PassPendingAction):
            return self._pass_pending(action)
        if isinstance(action, ConfirmPendingAction):
            return self._resolve_simple(action, confirmed=action.confirmed)
        if isinstance(action, SelectCardsAction):
            return self._resolve_simple(action, cards=tuple(action.cards))
        if isinstance(action, SelectTargetsAction):
            return self._resolve_simple(action, targets=tuple(action.targets))
        if isinstance(action, ChooseOptionAction):
            return self._resolve_simple(action, option=action.option)
        if isinstance(action, ActivateSkillAction):
            from src.game.skills.activation import resolve_activation

            return resolve_activation(self, action)
        raise TypeError("unsupported GameAction: " + type(action).__name__)

    def _use_card(self, action):
        from src.game.flows import UseCardFlow

        effect = self.card_effects.require(action.card)
        flow = UseCardFlow(self, action, effect)
        self.active_flows.append(flow)
        result = flow.start()
        self._prune_flows()
        return result

    def _respond_card(self, action):
        request = self.pending.require(action.request_id)
        if request.is_group:
            return self._group_respond_card(action, request)
        if request.request_type is not PendingRequestType.RESPOND_CARD:
            raise ValueError("PendingRequest does not accept a card response")
        if action.actor is not request.target:
            raise ValueError("action actor is not the requested responder")
        if action.card.name not in request.allowed_cards:
            raise ValueError("card is not allowed for this PendingRequest")

        # 技能转化出来的虚拟牌（武圣 / 龙胆）：校验与移动都针对真实源牌。
        source_cards = list(getattr(action.card, "source_cards", ()) or ())
        material_cards = source_cards if source_cards else [action.card]
        for card in material_cards:
            # 实体牌必须在响应者的合法区域里：手牌，或技能声明允许的装备区。
            if self.game.source_container(action.actor, card) is None:
                raise ValueError("response card is no longer in responder zones")

        self.pending.take(action.request_id)
        self.game.response.clear()

        for card in material_cards:
            self.game.move_source_card_to_processing(action.actor, card)
        for card in material_cards:
            self.context.apply(
                MoveCardAtom(
                    card,
                    source=self.game.processing_zone,
                    destination=self.game.deck.discard_pile,
                )
            )
        self.animate_response_card(action.card, action.source_rect, action.actor)
        resolution = PendingResolution(
            request=request,
            actor=action.actor,
            card=action.card,
            source_rect=action.source_rect,
        )
        self.pending.emit_resolved(resolution)
        self.context.emit(Event(
            EventType.CARD_RESPONDED, source=action.actor, target=request.source,
            payload={"actor": action.actor, "card": action.card, "request": request,
                     "reason": request.context.get("reason", "")},
        ))
        result = request.owner_flow.resume(resolution)
        self._prune_flows()
        self._drive_pending_front()
        return result

    # ==================================================
    # 群体等待（共享响应阶段）：谁先打出谁锁定本轮
    # ==================================================

    def _group_respond_card(self, action, request):
        """无懈阶段里的一次出牌：**本轮第一个合法的人**锁定本轮。

        锁定之后立刻消费他的牌、撤销其他人在**本轮**的询问，再把"这轮有人出牌"
        交给阶段自己去开下一轮。其他人在本轮的提交（含重复消息）到这里已经
        不是待回答状态，会在网络层被拒——绝不会再扣一张牌，也不会被算进下一轮。
        """

        if request.request_type is not PendingRequestType.RESPOND_CARD:
            raise ValueError("PendingRequest does not accept a card response")
        actor = action.actor
        if not request.is_member(actor):
            raise ValueError("action actor is not an eligible responder")
        if request.member_status(actor) != "pending":
            raise ValueError("this responder has already answered this round")
        if action.card.name not in request.allowed_cards:
            raise ValueError("card is not allowed for this PendingRequest")

        source_cards = list(getattr(action.card, "source_cards", ()) or ())
        material_cards = source_cards if source_cards else [action.card]
        for card in material_cards:
            if self.game.source_container(actor, card) is None:
                raise ValueError("response card is no longer in responder zones")

        # 先锁定本轮结果：别人手里的牌**一张都不动**。
        request.set_member_status(actor, "used")
        self._withdraw_group_asks(request, reason="本轮已经有人打出【无懈可击】")
        self.pending.take(request.request_id)
        self.game.response.clear()

        for card in material_cards:
            self.game.move_source_card_to_processing(actor, card)
        for card in material_cards:
            self.context.apply(
                MoveCardAtom(
                    card,
                    source=self.game.processing_zone,
                    destination=self.game.deck.discard_pile,
                )
            )
        self.animate_response_card(action.card, action.source_rect, actor)
        resolution = PendingResolution(
            request=request,
            actor=actor,
            card=action.card,
            source_rect=action.source_rect,
        )
        self.pending.emit_resolved(resolution)
        self.context.emit(Event(
            EventType.CARD_RESPONDED, source=actor, target=request.source,
            payload={"actor": actor, "card": action.card, "request": request,
                     "reason": request.context.get("reason", "")},
        ))
        result = request.owner_flow.resume(resolution)
        self._prune_flows()
        self._drive_pending_front()
        return result

    def _group_pass(self, request, actor):
        """群体等待里的一次放弃：只更新这名成员的状态。

        本轮还有别人没答 → 流程继续等（返回 None）；所有人都放弃了 →
        这一轮结束，把"无人出牌"交给阶段收尾。
        """

        actor = actor or request.target
        if not request.is_member(actor):
            raise ValueError("action actor is not an eligible responder")
        if request.member_status(actor) != "pending":
            raise ValueError("this responder has already answered this round")
        request.set_member_status(actor, "passed")
        current = self.game.response.current
        owner = getattr(current, "responder", None) if current is not None else None
        if actor is self.game.player or owner is actor:
            # 本机这块面板的主人放弃了：收掉面板，改显示等待文案。
            # 别人的面板（例如远程真人的）不受影响。
            self.game.response.clear()
        if request.pending_members:
            # 还有别人在决定：这一轮的请求保持等待，谁都不推进流程。
            return None

        self.pending.take(request.request_id)
        self._withdraw_group_asks(request, reason="本轮所有人都放弃了")
        self.game.response.clear()
        resolution = PendingResolution(request=request, actor=None, passed=True)
        self.pending.emit_resolved(resolution)
        result = request.owner_flow.resume(resolution)
        self._prune_flows()
        self._drive_pending_front()
        return result

    def _withdraw_group_asks(self, request, reason=""):
        """撤销所有还在等答案的成员的询问（界面 + 网络请求）。"""

        for member in request.group_members:
            if request.member_status(member) != "pending":
                continue
            request.set_member_status(member, "withdrawn")
            controller = self.game.get_controller(member)
            if controller is not None:
                controller.withdraw(request, reason)

    def present_group(self, request):
        """把一条群体请求（共享无懈阶段）交给有资格的人。

        **怎么问**由模式决定：默认实现是"同时问所有有资格的人"（见
        ``GameMode.present_group``）；1v1 测试的**双边手动**模式覆写成"按座次
        逐个问"——本地只有一个鼠标，同时开两块面板只会丢掉前一块。

        进门前先切一次当前操作者：无懈链之类的流程会直接调这里，而界面视角
        必须在那条请求被翻译成面板之前就切到回答者身上。
        """

        self.game.sync_operator()
        return self.game.mode.present_group(self, request)

    def _clear_request_ui(self, request):
        """请求一旦被引擎解决，就顺手清掉它建立的真人选择状态。

        真人 UI 回调自己也会清一次，这里兜底的是"用引擎接口直接提交"
        的调用方（脚本、测试、AI 探针），否则过期的选择状态会残留到
        下一个请求，届时提交会带着旧 request_id 撞上引擎校验。
        """

        game = self.game
        if request.request_type is PendingRequestType.SELECT_TARGETS:
            selection = game.pending_target_selection
            if selection is not None and selection.get("request_id") in (None, request.request_id):
                game.pending_target_selection = None
        elif request.request_type is PendingRequestType.SELECT_CARDS:
            selection = game.pending_selection
            if selection is not None and selection.get("request_id") in (None, request.request_id):
                game.pending_selection = None
        elif request.request_type in (PendingRequestType.CONFIRM,
                                      PendingRequestType.CHOOSE_OPTION):
            # 二选一面板（确认 / 选项）关掉时必须一起清掉：否则它会在请求
            # 早已解决之后still 显示在屏幕上，下一次"确认/取消"会带着一个
            # 过期的 request_id 提交，引擎直接拒绝（PendingRequest is missing）。
            # 结束阶段技能（据守 / 崩坏 / 琴音）会大量开这类窗口，这条清理
            # 是它们能安全收尾的前提。
            owner = getattr(self.game.choice.current, "responder", None)
            if owner is None or owner is request.target:
                self.game.choice.clear()

    def _pass_pending(self, action):
        request = self.pending.require(action.request_id)
        if request.is_group:
            return self._group_pass(request, action.actor)
        if action.actor is not request.target:
            raise ValueError("action actor is not the requested responder")
        if request.request_type is not PendingRequestType.RESPOND_CARD:
            # 选牌请求只有在「至少选 0 张」时才可以放弃（例如改判窗口里
            # 选择不替换）；其余选牌 / 选项 / 确认必须给出具体内容，
            # 放弃会让流程收到空结果（例如五谷拿不到牌），这里显式拒绝。
            # 选目标请求允许放弃：它的语义是"这次技能不发动"。
            if request.request_type is PendingRequestType.SELECT_CARDS and request.min_cards == 0:
                pass
            elif request.request_type is PendingRequestType.SELECT_TARGETS:
                pass
            else:
                raise ValueError(
                    "this PendingRequest cannot be answered with a pass: "
                    + request.request_type.value
                )
        self.pending.take(action.request_id)
        self.game.response.clear()
        resolution = PendingResolution(
            request=request,
            actor=action.actor,
            passed=True,
        )
        self.pending.emit_resolved(resolution)
        self._clear_request_ui(request)
        result = request.owner_flow.resume(resolution)
        self._prune_flows()
        self._drive_pending_front()
        return result

    def _resolve_simple(self, action, **values):
        request = self.pending.require(action.request_id)
        if action.actor is not request.target:
            raise ValueError("action actor is not the requested responder")
        if isinstance(action, ConfirmPendingAction):
            if request.request_type is not PendingRequestType.CONFIRM:
                raise ValueError("PendingRequest does not accept confirmation")
        elif isinstance(action, SelectCardsAction):
            if request.request_type is not PendingRequestType.SELECT_CARDS:
                raise ValueError("PendingRequest does not accept card selection")
            count = len(action.cards)
            if count < request.min_cards or count > request.max_cards:
                raise ValueError("selected card count is outside request bounds")
            # 同一张实体牌不能算作两个选择：网络层会把重复的 id 去重，
            # 但本地 / 脚本路径直接传对象，不查重就会让"选两张"用同一张牌
            # 凑数（弃牌时同一张牌被移动两次）。与选目标的查重对称。
            if len({id(card) for card in action.cards}) != count:
                raise ValueError("selected cards contain duplicates")
            candidates = request.context.get("candidates")
            if candidates is not None:
                for card in action.cards:
                    if not any(candidate is card for candidate in candidates):
                        raise ValueError("selected card is not a request candidate")
        elif isinstance(action, SelectTargetsAction):
            if request.request_type is not PendingRequestType.SELECT_TARGETS:
                raise ValueError("PendingRequest does not accept target selection")
            count = len(action.targets)
            if count < request.min_cards or count > request.max_cards:
                raise ValueError("selected target count is outside request bounds")
            if len({id(target) for target in action.targets}) != count:
                raise ValueError("selected targets contain duplicates")
            candidates = request.context.get("candidates")
            if candidates is not None:
                for target in action.targets:
                    if not any(candidate is target for candidate in candidates):
                        raise ValueError("selected target is not a request candidate")
        elif isinstance(action, ChooseOptionAction):
            if request.request_type is not PendingRequestType.CHOOSE_OPTION:
                raise ValueError("PendingRequest does not accept an option")
            if action.option not in request.options:
                raise ValueError("option is not allowed for this request")
        self.pending.take(action.request_id)
        resolution = PendingResolution(
            request=request,
            actor=action.actor,
            **values,
        )
        self.pending.emit_resolved(resolution)
        self._clear_request_ui(request)
        result = request.owner_flow.resume(resolution)
        self._prune_flows()
        self._drive_pending_front()
        return result

    # 每个 AI 响应之间的观察停顿：让玩家看清是谁在响应什么，
    # 而不是把整条结算链在一帧里跑完。只有开启 Game.ai_pacing 的
    # 真实对局才会走这条排队路径。停顿时间会被游戏速度档位缩放。
    AI_RESPONSE_PAUSE = 0.6
    # 需要看牌面再决定的关键响应（出闪 / 求桃 / 无懈 / 改判）多停一会。
    KEY_RESPONSE_PAUSE = 0.85
    KEY_RESPONSE_REASONS = frozenset({
        "sha", "dying_rescue", "wuxie_chain", "judge_replacement",
        "nanman", "wanjian", "juedou",
    })

    def response_pause(self, request):
        """这次响应要停顿多久：关键响应停久一点，其余按通用节奏。"""

        reason = request.context.get("reason", "")
        if reason in self.KEY_RESPONSE_REASONS:
            return self.KEY_RESPONSE_PAUSE
        if request.request_type is PendingRequestType.RESPOND_CARD:
            return self.KEY_RESPONSE_PAUSE
        return self.AI_RESPONSE_PAUSE

    def present_or_auto_resolve(self, request):
        """把决策请求交给这名角色的控制器。

        规则层只知道"要一个决策"，不知道回答来自本地鼠标、远程网络还是 AI：
        三种来源都通过 ``PlayerController.present`` 接入。同步控制器（本地真人
        / AI）会当场提交 Action 并恢复流程；异步控制器（远程真人）只是把请求
        发出去，流程停在原地等，主循环照常跑。

        群体请求（共享无懈阶段）交给 ``present_group``，由模式决定怎么问：默认
        "同时问所有有资格的人"；双边手动测试模式改成"按顺序逐个问"。

        问之前先切一次**当前操作者**：本地真人只有一位时它什么都不做；1v1 测试
        的双边手动模式里，视角必须在那条请求被翻译成界面状态之前就切到回答者
        身上——否则"这张牌在谁的手牌区里挑"这类判断会落到错误的一方。
        """

        if request.status != "pending" or self.pending.current is not request:
            return
        selection = self.game.pending_selection
        if selection is not None and selection.get("request_id") not in (None, request.request_id):
            # A covered card request is presented again when it reaches the top.
            self.game.pending_selection = None
        if request.is_group:
            return self.present_group(request)
        self.game.sync_operator()
        responder = request.target
        controller = self.game.get_controller(responder)
        if controller is None:
            return
        controller.present(request)

    def defer_respond(self, request, controller):
        """动作队列回调：停顿结束后真正让 AI 提交响应。"""

        if request.status != "pending" or self.pending.current is not request:
            # 请求在这段停顿里已经被死亡清理或别的流程解决掉了。
            return
        if request.is_group and request.member_status(controller.player) != "pending":
            # 群体等待：这段停顿里本轮已经被别人锁定（或这位自己已经答过）。
            # 迟到的回答在这里被丢掉——不扣牌、不结算、不算进下一轮。
            return
        if self.game.game_over:
            # 对局在停顿期间结束：不再保留等待中的请求。
            self.pending.clear()
            return
        owner_flow = getattr(request, "owner_flow", None)
        if owner_flow is not None and not self._flow_is_waiting(owner_flow):
            # 停顿期间流程已经收尾（目标阵亡 / 效果被取消）：这次响应作废，
            # 否则会在已经结束的流程上恢复。
            return
        controller.respond(request)

    @staticmethod
    def _flow_is_waiting(flow):
        from .flows import FlowStatus

        return getattr(flow, "status", None) is FlowStatus.WAITING

    def _drive_pending_front(self):
        """内层请求解决后，重新驱动暴露出来的外层请求。

        技能可以在事件回调里同步启动一个嵌套流程（铁骑在出牌时判定、八阵在
        求闪时判定）：那个判定窗口会压在父流程随后创建的请求下面。等父请求
        解决、窗口重新回到栈顶时，它的"请响应"早就过期了——没人再驱动它，
        整局就卡在判定面板上。这里补上这一步。
        """

        request = self.pending.current
        if request is None or request.status != "pending":
            # 栈已经空了：这时候才是"接着出牌 / 进入下一个回合"的安全时刻。
            self._flush_deferred_resumes()
            return
        self.present_or_auto_resolve(request)

    def zone_name(self, owner, request):
        """Pick the human input region for a card-selection request.

        Own cards are picked by clicking the hand; another character's cards are
        laid out in the public pool area so every candidate stays clickable
        even when eight panels share the table.
        """

        if owner is self.game.player:
            return "hand"
        return "public_pool"


    def animation_rect(self, key, fallback):
        """当前分辨率下的动画落点；没有 UI 时退回设计默认值。"""

        rects = getattr(self.game, "ui_rects", None)
        if rects:
            rect = rects.get(key)
            if rect is not None:
                return tuple(rect)
        return fallback

    def placement(self, key):
        """命名落位（"table_card" / "discard_pile" …）的当前屏幕坐标。

        动画落点与静态展示位都从这里取，且这是 MoveCardAction 的解析器：
        动画每帧重新问一次当前布局，所以 resize / F11 之后飞行终点立刻
        落到新的中央 anchor 上，不会停在旧分辨率的坐标。
        """

        return self.animation_rect(key, PLACEMENT_FALLBACKS.get(key, TABLE_CARD_RECT))

    def animate_card_use(self, action):
        start_rect = action.source_rect
        if start_rect is None:
            start_rect = (
                self.placement("player_hand")
                if action.actor is self.game.player
                else self.placement("opponent_hand")
            )
        self.game.actions.add(
            MoveCardAction(
                action.card,
                start_rect,
                "table_card",
                duration=0.30,
                on_finish=(
                    lambda card=action.card:
                    self.place_table_card(card)
                ),
                resolver=self.placement,
            )
        )

    def place_table_card(self, card, key="table_card"):
        """出牌动画到达中央：把牌停在桌面——前提是它还在结算中。

        桃 / 酒 / 铁索重铸这类**瞬间结算**的牌，流程在动画飞到中央之前就已经
        收尾（牌进了弃牌堆），这时再登记桌面副本只会让它永远挂在中央。
        判据是"牌还在处理区"：还在处理区 = 这次结算没结束 = 该显示。

        View-As（龙胆把【闪】当【杀】）时中央展示的是**虚拟牌本身**：虚拟牌
        不在处理区，进处理区的是它的实体来源牌，所以来源牌要一起算进判据——
        否则这次出牌在桌面上什么都不显示，而收尾逻辑仍会去移除一张从未登记
        过的虚拟牌（``remove_table_card(self.card)``）。
        """

        materials = list(getattr(card, "source_cards", ()) or ()) or [card]
        for item in self.game.processing_zone:
            if any(item is material for material in materials):
                self.game.add_table_card(card, key)
                return

    def animate_response_card(self, card, source_rect, actor):
        start_rect = source_rect
        if start_rect is None:
            start_rect = (
                self.placement("player_hand")
                if actor is self.game.player
                else self.placement("opponent_hand")
            )
        self.game.actions.add(
            MoveCardAction(
                card,
                start_rect,
                "response_card",
                duration=0.28,
                on_finish=(
                    lambda response=card:
                    self.game.add_table_card(response, "response_card")
                ),
                resolver=self.placement,
            )
        )
        self.game.actions.add(
            MoveCardAction(
                card,
                "response_card",
                "discard_pile",
                duration=0.30,
                on_finish=(
                    lambda response=card:
                    self.game.remove_table_card(response)
                ),
                resolver=self.placement,
            )
        )

    def discard_processing_card(self, card):
        if not any(item is card for item in self.game.processing_zone):
            # 已经被技能取走（奸雄 / 天妒一类），或者已经进了别的区域
            # （延时锦囊进了判定区）：不能再移动一次，否则同一张实体牌会
            # 同时出现在两个区域。但桌面上的展示副本必须收掉，否则它会
            # 一直停在中央——牌已经在别处了，视觉只能有一份。
            self.game.remove_table_card(card)
            return
        self.discard_zone_card(card, self.game.processing_zone)

    # 别人（通常是 AI）拿走 / 弃掉一张牌时，把这张牌亮在桌面停留的时间。
    TAKEN_CARD_HOLD = 0.75

    def show_taken_card(self, card, owner, actor, *, to_hand=True):
        """把一张被拿走 / 被弃置的牌亮在桌面，停一会再飞向目的地。

        纯表现层：规则上的移动由调用方完成，这里只负责让真人看清
        "谁拿走了什么牌"，避免过河拆桥 / 顺手牵羊这类操作一闪而过。
        """

        game = self.game
        if not getattr(game, "ui_rects", None):
            # 无 UI（无头测试 / 无渲染）时不做任何动画。
            return
        start = (
            self.placement("player_hand")
            if owner is game.player
            else self.placement("opponent_hand")
        )
        if to_hand:
            destination = (
                self.placement("player_hand")
                if actor is game.player
                else self.placement("opponent_hand")
            )
        else:
            destination = "discard_pile"

        game.actions.add(MoveCardAction(
            card, start, "table_card", duration=0.26,
            on_finish=(lambda shown=card: game.add_table_card(shown, "table_card")),
            resolver=self.placement,
        ))
        game.actions.add(WaitAction(self.TAKEN_CARD_HOLD))
        game.actions.add(MoveCardAction(
            card, "table_card", destination, duration=0.30,
            on_finish=(lambda shown=card: game.remove_table_card(shown)),
            resolver=self.placement,
        ))

    def discard_zone_card(self, card, source):
        """Move a visible table card from a logical zone to the discard pile.

        规则移动立即完成（不为了动画延迟任何状态）；桌面上的静态副本同时
        收掉，这张牌接下来的视觉全部交给移动动画——是**交接**，不是两个
        副本并存。
        """

        self.context.apply(
            MoveCardAtom(
                card,
                source=source,
                destination=self.game.deck.discard_pile,
            )
        )
        self.game.remove_table_card(card)
        self.game.actions.add(
            MoveCardAction(
                card,
                "table_card",
                "discard_pile",
                duration=0.30,
                resolver=self.placement,
            )
        )

    def clear_pending_ui(self):
        self.pending.clear()
        self.game.response.clear()
        # 请求栈被整体清空（对局结束 / 死亡清理）也算"没有待回答的请求"：
        # 等在这条边界上的回合推进要醒过来，否则 AI 的回合会停在半路。
        self._flush_deferred_resumes()

    def run_death(self, dead_player, source=None, cause=None):
        from src.game.flows import DeathFlow

        return DeathFlow(
            self,
            dead_player=dead_player,
            source=source,
            cause=cause,
        ).start()

    def run_turn(self, player, on_complete=None):
        from src.game.flows import TurnFlow
        return TurnFlow(self, player, on_complete=on_complete).start()

    def _prune_flows(self):
        self.active_flows = [
            flow
            for flow in self.active_flows
            if flow.status.value in {"running", "waiting"}
        ]
