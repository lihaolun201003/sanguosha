from src.actions import (
    CallbackAction,
    WaitAction,
)


class TurnMixin:

    # ==================================================
    # 玩家结束出牌阶段
    # ==================================================

    def end_player_turn(self):

        if self.busy:
            return

        if self.response.active:
            return

        if self.current_turn_player is not self.player:
            return

        if self.phase != "play":
            return

        if self.game_over:
            return


        # 使用酒以后必须先出杀；但如果没有能打到的目标（例如唯一目标已经
        # 阵亡），酒的效果作废，不能把玩家永久锁在出牌阶段。
        if self.wine_blocks_other_cards():

            self.message = (
                "你已经使用【酒】，"
                "必须先使用一张【杀】。"
            )

            return


        # 手牌超过当前体力
        if (
            len(self.player.hand)
            > self.player.hp
        ):

            self.phase = "discard"

            need = (
                len(self.player.hand)
                - self.player.hp
            )

            self.message = (
                "请弃置 "
                + str(need)
                + " 张牌。"
            )

            return


        if getattr(self, "active_turn_flow", None) is not None:
            self.active_turn_flow.finish_interactive()
        self.start_next_turn(self.player)


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
            - self.player.hp
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
        self.get_controller(player).discard_to_hand_limit()
        if self.active_turn_flow is not None:
            self.active_turn_flow.finish_interactive()
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
