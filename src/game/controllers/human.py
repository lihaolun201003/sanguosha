"""本地真人控制器：把引擎的决策请求挂到 Pygame 交互状态上。

真人点击手牌 / 角色 / 按钮最终都会走回 ``engine.submit(...)``，所以这里的
职责只有一件：**把请求翻译成界面能显示的交互状态**。规则判定一条都不在这里。

（这段代码原本写在 ``GameEngine.present_or_auto_resolve`` 的"非 AI"分支里，
Phase 11.2 把它挪进控制器，好让远程真人复用同一个决策入口。）
"""

from src.game.engine import (
    ChooseOptionAction,
    ConfirmPendingAction,
    PassPendingAction,
    RespondCardAction,
    SelectCardsAction,
    SelectTargetsAction,
)
from src.game.engine.pending import PendingRequestType

from .base import PlayerController


class HumanController(PlayerController):
    """本地 Pygame 输入适配器：UI 直接提交 GameAction。"""

    @property
    def holds_panel(self):
        """我现在手里有没有一块等着点的人和面板（响应 / 二选一）。

        局部单机只有一位真人时它永远是"刚好有或没有"；双边手动测试里它是
        引擎判断"这块面板归谁、下一位该轮到谁"的依据（见 modes/duel.py）。
        """

        game = self.game
        for system in (game.response, game.choice):
            current = system.current
            if current is not None and getattr(current, "responder", None) is self.player:
                return True
        return False

    def present(self, request):
        game = self.game
        responder = self.player

        if request.request_type is PendingRequestType.RESPOND_CARD:
            if request.is_group:
                self._present_group(request)
                return
            # 无懈链是自动推进的：没有任何合法响应（真实牌或技能转化）
            # 就直接放弃，不必让玩家再点一次「不出」。
            if request.context.get("reason") == "wuxie_chain":
                if not self._can_respond(request):
                    self.submit(PassPendingAction(responder, request.request_id))
                    return
            game.response.request(
                prompt=request.prompt,
                allowed_cards=request.allowed_cards,
                reason=str(request.context.get("reason") or ""),
                responder=responder,
                on_card=(
                    lambda index, card, rect, request_id=request.request_id:
                    self.submit(RespondCardAction(responder, request_id, card, rect))
                ),
                on_pass=(
                    lambda request_id=request.request_id:
                    self.submit(PassPendingAction(responder, request_id))
                ),
            )
            return

        if request.request_type is PendingRequestType.CONFIRM:
            game.choice.request(
                title="装备技能", prompt=request.prompt,
                yes_label="发动", no_label="不发动",
                responder=responder,
                on_yes=lambda request_id=request.request_id: self.submit(
                    ConfirmPendingAction(responder, request_id, True)),
                on_no=lambda request_id=request.request_id: self.submit(
                    ConfirmPendingAction(responder, request_id, False)),
            )
            return

        if request.request_type is PendingRequestType.CHOOSE_OPTION:
            first = request.options[0]
            second = request.options[1] if len(request.options) > 1 else first
            from src.game.skills.mechanics import option_label

            game.choice.request(
                title="请选择", prompt=request.prompt,
                yes_label=option_label(request, first),
                no_label=option_label(request, second),
                responder=responder,
                on_yes=lambda value=first, request_id=request.request_id: self.submit(
                    ChooseOptionAction(responder, request_id, value)),
                on_no=lambda value=second, request_id=request.request_id: self.submit(
                    ChooseOptionAction(responder, request_id, value)),
            )
            return

        if request.request_type is PendingRequestType.SELECT_TARGETS:
            game.start_target_selection(
                candidates=list(request.context.get("candidates", ())),
                minimum=request.min_cards,
                maximum=request.max_cards,
                prompt=request.prompt,
                request_id=request.request_id,
                on_complete=lambda targets, request_id=request.request_id: self.submit(
                    SelectTargetsAction(responder, request_id, targets)),
                on_cancel=lambda request_id=request.request_id: self.submit(
                    PassPendingAction(responder, request_id)),
            )
            return

        if request.request_type is PendingRequestType.SELECT_CARDS:
            owner = request.context.get("zone_owner", responder)
            zone = request.context.get("zone")
            if zone is None:
                zone = game.engine.zone_name(owner, request)
            candidates = []
            for card in list(request.context.get("candidates", ())):
                key = None
                for slot, equipped in owner.equipment.items():
                    if equipped is card:
                        key = slot
                        break
                candidates.append((card, key))
            game.start_card_selection(
                zone=zone,
                owner=owner,
                candidates=candidates,
                number=request.min_cards,
                prompt=request.prompt,
                request_id=request.request_id,
                on_complete=lambda selected, request_id=request.request_id: self.submit(
                    SelectCardsAction(responder, request_id, [item[0] for item in selected])),
            )
            return

        raise ValueError(
            "unsupported pending request for a human: " + str(request.request_type))

    # ==================================================
    # 共享响应阶段（无懈）
    # ==================================================

    def _present_group(self, request):
        """共享无懈阶段：只有**还轮得到我**的时候才给我面板。

        我没有资格 / 本轮已经放弃 → 什么都不做，界面由提示层显示"等待其他玩家
        响应"（见 ui.prompt）。这样就不会再出现"手里没有【无懈可击】却停在
        询问"的空面板。
        """

        if not request.is_member(self.player):
            return
        if request.member_status(self.player) != "pending":
            return
        if not self._can_respond(request):
            # 局面在这条请求建好之后又变了（牌被拿走一类）：直接放弃，
            # 让本轮继续推进，而不是把玩家按在一个点不动的面板上。
            self.submit(PassPendingAction(self.player, request.request_id))
            return
        self.game.response.request(
            prompt=request.prompt,
            allowed_cards=request.allowed_cards,
            reason=str(request.context.get("reason") or ""),
            responder=self.player,
            on_card=(
                lambda index, card, rect, request_id=request.request_id:
                self.submit(RespondCardAction(
                    self.player, request_id, card, rect))
            ),
            on_pass=(
                lambda request_id=request.request_id:
                self.submit(PassPendingAction(self.player, request_id))
            ),
        )

    def _can_respond(self, request):
        """这条请求要求我打出的牌，我现在**真的**打得出吗（含技能转化）。"""

        return self.game.card_actions.can_respond(
            self.player, allowed_names=request.allowed_cards, request=request)

    def withdraw(self, request, reason=""):
        """这一轮不用我再回答了：把面板收掉，界面改显示等待文案。"""

        if not request.is_group or not request.is_member(self.player):
            return None
        if getattr(self.game.engine.pending, "current", None) is not request:
            return None               # 面板已经属于别的请求了，别误收
        self.game.response.clear()
        return None

    # ==================================================
    # 出牌阶段
    # ==================================================

    def take_turn(self, on_complete, can_play=True):
        """本地真人的出牌阶段由鼠标驱动：这里只给提示，不做任何推进。

        结束回合仍然走 ``game.end_player_turn()``（它负责弃牌阶段与下一个
        回合），所以 ``on_complete`` 在本地真人路径上不参与。
        """

        self.game.message = (
            "出牌阶段。" if can_play else "出牌阶段被跳过，请进入弃牌阶段。")
