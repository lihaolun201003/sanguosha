from src.actions import (
    CallbackAction,
    WaitAction,
)
from src.game.engine.flows import FlowStatus


class TurnMixin:

    # ==================================================
    # 玩家结束出牌阶段
    # ==================================================

    def end_phase_blockers(self, actor):
        """此刻**不能**结束出牌阶段的硬门控（空列表 = 通过）。

        与 ``can_end_play_phase`` 共用同一份条件：可用性查询与真正的执行不会
        各写一套"现在能不能结束"。这里只放"静默返回"的那几条；需要给玩家
        提示的（酒锁定、手牌超限）由 ``can_end_play_phase`` 与
        ``end_player_turn`` 分别带原因处理。
        """

        if self.busy:
            return ["结算动画还没结束"]
        if self.response.active:
            return ["还有响应窗口在等你回答"]
        # 判定优先：规则上判定还没结束时不能结束出牌阶段——否则回合会在判定
        # 结算到一半时推进到下一个人。这里只拦"规则上没走完"：判定牌仍在
        # 屏幕上展示时规则早已结束，AI 的回合必须能继续推进（拦了会把它卡死）。
        gate = getattr(self, "judge_gate", None)
        if gate is not None and gate.blocks:
            return [gate.blocks_message() or "判定尚未结束"]
        # 还有请求在等答案（共享无懈阶段 / 别人的响应 / 锦囊仍在结算）：
        # 现在结束回合会把结算打断，必须等它结束。
        if self.engine.pending.active:
            return ["还有请求在等回答（结算尚未结束）"]
        if self.current_turn_player is not actor:
            return ["现在不是你的回合"]
        if self.game_over:
            return ["对局已经结束"]
        return []

    def can_end_play_phase(self, actor=None):
        """现在允许结束出牌阶段吗：``(是否允许, 原因)``（纯查询）。

        AvailableActions 用它描述"结束出牌阶段"这一动作；``end_player_turn``
        用 ``end_phase_blockers`` 做同一层门控，所以两者不会分叉。

        **手牌超上限不算"不能结束"**：结束出牌阶段这一步本身就是切到弃牌阶段
        （``end_player_turn`` 会改 ``phase`` 并要求弃牌）。把它算成阻止条件会
        让玩家卡在出牌阶段——界面会灰掉"结束回合"，而唯一能进入弃牌阶段的
        入口正是那个按钮。
        """

        actor = actor or self.player
        blockers = self.end_phase_blockers(actor)
        if blockers:
            return False, blockers[0]
        if self.phase != "play":
            return False, "现在不是出牌阶段。"
        # 使用酒以后必须先出杀；但如果没有能打到的目标（例如唯一目标已经
        # 阵亡），酒的效果作废，不能把玩家永久锁在出牌阶段。
        if self.wine_requires_sha(actor):
            return False, "你已经使用【酒】，必须先使用一张【杀】。"
        return True, ""

    def end_player_turn(self, actor=None):
        """结束出牌阶段。

        ``actor`` 默认是本地真人（原来只支持真人自己）；远程真人的出牌阶段
        也走同一条规则路径，只是"结束"这个决定来自网络而不是鼠标。
        """

        actor = actor or self.player

        # 硬门控与 can_end_play_phase 同源（同一份 end_phase_blockers），
        # 不在这里另写一遍"能不能结束"。
        if self.end_phase_blockers(actor):
            return

        if self.phase == "discard":
            # 出牌阶段被跳过（乐不思蜀 / 兵粮寸断）时，回合停在弃牌阶段等玩家
            # 收尾。此时手牌不超上限就等于弃牌完成，允许直接结束回合；超过
            # 上限则必须先弃牌。缺了这一段，本地真人会永远停在这里——AI 与
            # 远程真人都已经由各自的控制器收尾，只有本地这条路径漏了。
            limit = self.hand_limit(actor)
            if len(actor.hand) > limit:
                self.message = "请弃置 " + str(len(actor.hand) - limit) + " 张牌。"
                return
            if getattr(self, "active_turn_flow", None) is not None:
                self.active_turn_flow.finish_interactive()
            self.start_next_turn(actor)
            return

        if self.phase != "play":
            return

        # 使用酒以后必须先出杀；但如果没有能打到的目标（例如唯一目标已经
        # 阵亡），酒的效果作废，不能把玩家永久锁在出牌阶段。
        if self.wine_requires_sha(actor):

            self.message = (
                "你已经使用【酒】，"
                "必须先使用一张【杀】。"
            )

            return


        # 手牌超过当前上限（技能可以修改上限）
        limit = self.hand_limit(actor)
        if (
            len(actor.hand)
            > limit
        ):

            self.phase = "discard"

            need = (
                len(actor.hand)
                - limit
            )

            self.message = (
                "请弃置 "
                + str(need)
                + " 张牌。"
            )

            return


        phase_flow = getattr(self, "active_turn_flow", None)
        if phase_flow is not None:
            result = phase_flow.finish_interactive()
            if result is not None and result.status is FlowStatus.WAITING:
                # 结束阶段技能（据守 / 崩坏 / 琴音）开了一个要回答的窗口：
                # 等它答完再交给下一个角色，否则"技能还在问、回合已经换人"。
                self.engine.defer_turn_resume(
                    lambda a=actor: self.start_next_turn(a))
                return
        self.start_next_turn(actor)


    # ==================================================
    # 玩家弃牌
    # ==================================================

    def player_discard(
        self,
        index,
        source_rect
    ):

        if self.busy:
            return

        if self.current_turn_player is not self.player:
            return

        if self.phase != "discard":
            return


        card = self.player.remove_card(
            index
        )

        if card is None:
            return


        self.message = (
            "弃置【"
            + card.display_name
            + "】。"
        )


        self.queue_to_discard(
            card,
            source_rect,
            after=self.after_player_discard
        )


    # ==================================================
    # 每弃一张以后重新检查
    # ==================================================

    def after_player_discard(self):

        remaining = (
            len(self.player.hand)
            - self.hand_limit(self.player)
        )


        if remaining > 0:

            self.message = (
                "还需要弃置 "
                + str(remaining)
                + " 张牌。"
            )

            return


        self.message = (
            "弃牌阶段结束。"
        )


        self.actions.add(
            WaitAction(0.35)
        )


        self.actions.add(

            CallbackAction(
                lambda: self.start_next_turn(self.player)
            )
        )


    # ==================================================
    # 开始下一个存活角色的回合
    # ==================================================

    def start_next_turn(self, previous=None):

        if self.game_over:
            return
        # 额外回合优先（放权 / 连破）：队列里还有就先把回合交给它，
        # 而不是按座次继续——否则"额外回合"会变成"下一轮才生效"。
        extra = None
        popper = getattr(self, "pop_extra_turn", None)
        if callable(popper):
            extra = popper()
        if extra is not None:
            self.start_turn(extra)
            return
        previous = previous or self.current_turn_player
        next_player = self.seats.next_alive_player(previous)
        if next_player is None:
            return
        self.start_turn(next_player)

    def start_turn(self, player):

        from .engine import FlowStatus
        from .flows.turn import TurnFlow

        if self.game_over or not player.alive:
            return

        # 翻面：武将牌背面朝上时，这个回合被整段跳过（翻回正面即结束）。
        # 这是通用规则，任何角色都可能被翻面，不是某一名武将的特例。
        if not getattr(player, "face_up", True):
            player.face_up = True
            self.add_log("%s 的武将牌翻回正面，跳过本回合。" % player.name)
            self.message = player.name + " 处于翻面状态，跳过本回合。"
            self.actions.add(CallbackAction(lambda p=player: self.start_next_turn(p)))
            return

        # 回合状态属于角色自己：出杀次数、酒效果都按角色清理。
        player.clear_turn_state()

        if getattr(self, "active_turn_flow", None) is not None:
            self.active_turn_flow.finish_interactive()
        flow = TurnFlow(
            self.engine,
            player,
            on_play_phase=lambda ready, p=player: self._enter_play_phase(p, ready),
        )
        self.active_turn_flow = flow
        ready = flow.begin_interactive()
        if flow.status is FlowStatus.WAITING:
            # 判定阶段触发了需要等待的流程（例如闪电造成濒死求桃）：
            # 出牌阶段会在该流程结束后自动开始。
            return
        self._enter_play_phase(player, ready)

    def _enter_play_phase(self, player, can_play):

        self.message = player.name + " 的回合。"
        self.add_log("当前回合：" + player.name)


        self.actions.add(
            WaitAction(0.50)
        )


        if player.is_human:
            self.message = "出牌阶段。" if can_play else "出牌阶段被跳过，请进入弃牌阶段。"
            return
        # AI 与真人共用同一套回合流程，只是 Action 来自 AIController。
        controller = self.get_controller(player)
        self.actions.add(CallbackAction(
            lambda p=player, c=controller: c.take_turn(lambda: self._finish_ai_turn(p))
            if can_play else self._finish_ai_turn(p)
        ))

    def _finish_ai_turn(self, player):
        # 还有请求没答完（别人的技能窗口 / 自己这边的响应）：现在收尾会把结算
        # 打断，甚至把回合交给下一个角色——别人还在等着回答，回合已经换了人。
        # 登记成回调，等请求解决后再收尾（引擎在请求栈清空时唤醒）。
        if self.engine.pending.active:
            self.engine.defer_turn_resume(lambda p=player: self._finish_ai_turn(p))
            return
        self.get_controller(player).discard_to_hand_limit()
        phase_flow = self.active_turn_flow
        if phase_flow is not None:
            result = phase_flow.finish_interactive()
            if result is not None and result.status is FlowStatus.WAITING:
                # 结束阶段技能开了窗口：这一回合先不收尾，等它答完。
                self.engine.defer_turn_resume(lambda p=player: self._finish_ai_turn(p))
                return
        if not self.game_over:
            self.start_next_turn(player)


    # ==================================================
    # 开始玩家新回合
    # ==================================================

    def start_player_turn(self):
        self.start_turn(self.player)


    # ==================================================
    # 玩家摸牌完成
    # ==================================================

    def finish_player_draw(self):

        self.phase = "play"

        self.message = (
            "出牌阶段。"
        )
