from src.actions import (
    CallbackAction,
    MoveCardAction,
    WaitAction,
)

from src.constants import (
    ENEMY_HAND_RECT,
    RESPONSE_CARD_RECT,
)

from .equipment_skills import (
    has_zhuge_crossbow,
)


class DyingMixin:

    # ==================================================
    # 玩家死亡
    # ==================================================

    def player_dies(self):

        return self.engine.run_death(
            self.player,
            source=self.enemy,
        )

    # ==================================================
    # 电脑死亡
    # ==================================================

    def enemy_dies(self):

        return self.engine.run_death(
            self.enemy,
            source=self.player,
        )

    # ==================================================
    # 电脑受到伤害以后
    # ==================================================

    def after_enemy_took_damage(self):

        # ==================================================
        # 还有体力
        # ==================================================

        if self.enemy.hp > 0:

            return

        # ==================================================
        # 进入濒死
        # ==================================================

        self.enemy_try_rescue()

    # ==================================================
    # 电脑尝试自救
    # ==================================================

    def enemy_try_rescue(self):

        if self.enemy.hp > 0:

            self.message = (
                "电脑脱离濒死状态。"
            )

            return

        rescue_card = None

        # ==================================================
        # AI 优先桃
        # ==================================================

        if self.enemy.has_card(
            "TAO"
        ):

            rescue_card = (
                self.enemy.remove_first(
                    "TAO"
                )
            )

        # ==================================================
        # 没桃再酒
        # ==================================================

        elif self.enemy.has_card(
            "JIU"
        ):

            rescue_card = (
                self.enemy.remove_first(
                    "JIU"
                )
            )

        # ==================================================
        # 无法自救
        # ==================================================

        if rescue_card is None:

            self.enemy_dies()

            return

        self.message = (
            "电脑濒死，使用【"
            + rescue_card.display_name
            + "】自救。"
        )

        self.actions.add(

            MoveCardAction(
                rescue_card,
                ENEMY_HAND_RECT,
                RESPONSE_CARD_RECT,
                duration=0.28,

                on_finish=(

                    lambda c=rescue_card:
                    self.add_table_card(
                        c,
                        RESPONSE_CARD_RECT
                    )
                )
            )
        )

        self.actions.add(

            CallbackAction(

                lambda c=rescue_card:
                self.resolve_enemy_rescue(
                    c
                )
            )
        )

    # ==================================================
    # 电脑自救结算
    # ==================================================

    def resolve_enemy_rescue(
        self,
        card
    ):

        self.enemy.hp += 1

        self.message = (
            "电脑使用【"
            + card.display_name
            + "】，回复 1 点体力。"
        )

        self.actions.add(
            WaitAction(0.40)
        )

        self.queue_to_discard(
            card,
            RESPONSE_CARD_RECT,
            after=self.enemy_try_rescue
        )

    # ==================================================
    # 玩家受到伤害以后
    # ==================================================

    def after_player_took_damage(self):

        # ==================================================
        # 没有进入濒死
        # ==================================================

        if self.player.hp > 0:

            self.after_enemy_sha_finished()

            return

        self.request_player_rescue()


    # ==================================================
    # 电脑的一次杀结算完成
    # ==================================================

    def after_enemy_sha_finished(self):

        if self.game_over:
            return

        # 装备诸葛连弩时，电脑可以继续使用手中的杀。
        if (
            has_zhuge_crossbow(self.enemy)
            and self.enemy.has_card("SHA")
            and self.can_attack(
                self.enemy,
                self.player
            )
        ):

            self.message = (
                "电脑发动【诸葛连弩】，继续出杀。"
            )

            self.actions.add(
                WaitAction(0.35)
            )

            self.actions.add(
                CallbackAction(
                    self.enemy_attack
                )
            )

            return

        self.enemy_discard_next()

    # ==================================================
    # 玩家进入濒死
    # ==================================================

    def request_player_rescue(self):

        if self.player.hp > 0:

            self.phase = "enemy"

            self.after_enemy_sha_finished()

            return

        has_tao = self.player.has_card(
            "TAO"
        )

        has_jiu = self.player.has_card(
            "JIU"
        )

        # ==================================================
        # 什么救命牌都没有
        # ==================================================

        if not has_tao and not has_jiu:

            self.player_dies()

            return

        self.phase = "dying"

        self.message = (
            "你进入濒死状态，"
            "可以使用【桃】或【酒】自救。"
        )

        self.response.request(

            prompt=(
                "濒死：使用【桃】或【酒】自救"
            ),

            allowed_cards={
                "TAO",
                "JIU",
            },

            on_card=(

                lambda index, card, rect:
                self.player_respond_rescue(
                    index,
                    card,
                    rect
                )
            ),

            on_pass=self.player_pass_rescue
        )

    # ==================================================
    # 玩家选择桃或酒
    # ==================================================

    def player_respond_rescue(
        self,
        index,
        card,
        source_rect
    ):

        rescue_card = (
            self.player.remove_card(
                index
            )
        )

        if rescue_card is None:
            return

        self.message = (
            "你使用【"
            + rescue_card.display_name
            + "】自救。"
        )

        self.actions.add(

            MoveCardAction(
                rescue_card,
                source_rect,
                RESPONSE_CARD_RECT,
                duration=0.28,

                on_finish=(

                    lambda c=rescue_card:
                    self.add_table_card(
                        c,
                        RESPONSE_CARD_RECT
                    )
                )
            )
        )

        self.actions.add(

            CallbackAction(

                lambda c=rescue_card:
                self.resolve_player_rescue(
                    c
                )
            )
        )

    # ==================================================
    # 自救牌结算
    # ==================================================

    def resolve_player_rescue(
        self,
        card
    ):

        self.player.hp += 1

        self.message = (
            "你回复了 1 点体力。"
        )

        self.actions.add(
            WaitAction(0.40)
        )

        self.queue_to_discard(
            card,
            RESPONSE_CARD_RECT,
            after=self.continue_player_dying
        )

    # ==================================================
    # 检查是否脱离濒死
    # ==================================================

    def continue_player_dying(self):

        # ==================================================
        # 比如原来 -1，
        # 一张桃只能救到 0，
        # 仍然需要继续救
        # ==================================================

        if self.player.hp <= 0:

            self.request_player_rescue()

            return

        self.phase = "enemy"

        self.message = (
            "你脱离了濒死状态。"
        )

        self.actions.add(
            WaitAction(0.40)
        )

        self.actions.add(

            CallbackAction(
                self.after_enemy_sha_finished
            )
        )

    # ==================================================
    # 玩家放弃自救
    # ==================================================

    def player_pass_rescue(self):

        self.player_dies()
