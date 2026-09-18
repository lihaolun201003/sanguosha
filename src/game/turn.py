from src.actions import (
    CallbackAction,
    WaitAction,
)

from src.constants import (
    ENEMY_HAND_RECT,
    PLAYER_HAND_SOURCE_RECT,
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

        if self.phase != "play":
            return

        if self.game_over:
            return


        # 使用酒以后必须先出杀
        if self.wine_sha_required:

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
                self.start_enemy_turn
            )
        )


    # ==================================================
    # 开始电脑回合
    # ==================================================

    def start_enemy_turn(self):

        self.start_next_turn(self.player)

    def start_next_turn(self, previous=None):

        if self.game_over:
            return
        previous = previous or self.current_turn_player
        next_player = self.seats.next_alive_player(previous)
        if next_player is None:
            return
        self.start_turn(next_player)

    def start_turn(self, player):

        from .flows.turn import TurnFlow
        from .controllers import AIController

        if self.game_over or not player.alive:
            return


        # 玩家酒效果不能跨回合
        self.player_wine_buff = False
        self.wine_sha_required = False


        # 初始化电脑本回合状态
        self.enemy_wine_buff = False
        self.enemy_jiu_used = False


        if getattr(self, "active_turn_flow", None) is not None:
            self.active_turn_flow.finish_interactive()
        self.active_turn_flow = TurnFlow(self.engine, player)
        can_play = self.active_turn_flow.begin_interactive()

        self.message = player.name + " 的回合。"
        self.add_log("当前回合：" + player.name)


        self.actions.add(
            WaitAction(0.50)
        )


        if player is self.player:
            self.sha_used = False
            self.jiu_used = False
            self.message = "出牌阶段。" if can_play else "出牌阶段被跳过，请进入弃牌阶段。"
            return
        controller = AIController(self, player)
        self.actions.add(CallbackAction(
            lambda p=player, c=controller: c.take_turn(lambda: self._finish_ai_turn(p))
            if can_play else self._finish_ai_turn(p)
        ))

    def _finish_ai_turn(self, player):
        from .atoms_v2 import MoveCardAtom
        while len(player.hand) > max(0, player.hp):
            self.context.apply(MoveCardAtom(player.hand[-1], source=player.hand, destination=self.deck.discard_pile))
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
