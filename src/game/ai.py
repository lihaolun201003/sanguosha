import random

from src.actions import (
    CallbackAction,
    MoveCardAction,
    WaitAction,
)

from src.card import Card
from .engine import UseCardAction

from src.constants import (
    ENEMY_EQUIPMENT_RECTS,
    ENEMY_HAND_RECT,
    TABLE_CARD_RECT,
)

from .equipment_skills import (
    has_zhuge_crossbow,
)

from .equipment_skills.weapons import (
    has_fangtian_halberd,
    has_zhangba_spear,
    has_zhuque_fan,
)


class AIMixin:

    # ==================================================
    # AI 尝试装备
    # ==================================================

    def enemy_equip_next(self):

        if self.game_over:
            return

        equipment_index = None

        for i, card in enumerate(
            self.enemy.hand
        ):

            if (
                card.category
                == "equipment"
            ):

                equipment_index = i

                break

        # ==================================================
        # 没装备可以出了
        # ==================================================

        if equipment_index is None:

            self.enemy_use_tao()

            return

        card = (
            self.enemy.remove_card(
                equipment_index
            )
        )

        slot = card.subtype

        target_rect = (
            ENEMY_EQUIPMENT_RECTS[
                slot
            ]
        )

        # ==================================================
        # 旧装备
        # ==================================================

        old_card, healed = (
            self.remove_equipment_with_effects(
                self.enemy,
                slot
            )
        )

        if old_card is not None:
            self.queue_to_discard(
                old_card,
                target_rect
            )

        self.message = (
            "电脑装备了【"
            + card.display_name
            + "】。"
        )

        if healed:

            self.message += (
                "失去【白银狮子】，"
                "回复 1 点体力。"
            )

        self.actions.add(

            MoveCardAction(
                card,
                ENEMY_HAND_RECT,
                target_rect,
                duration=0.30,

                on_finish=(

                    lambda c=card:
                    self.enemy.set_equipment(
                        c
                    )
                )
            )
        )

        self.actions.add(
            WaitAction(0.25)
        )

        # ==================================================
        # 继续检查还有没有装备
        # ==================================================

        self.actions.add(

            CallbackAction(
                self.enemy_equip_next
            )
        )

    # ==================================================
    # AI 使用桃
    # ==================================================

    def enemy_use_tao(self):

        if (
            self.enemy.hp
            < self.enemy.max_hp

            and

            self.enemy.has_card("TAO")
        ):

            tao = (
                self.enemy.remove_first(
                    "TAO"
                )
            )

            self.message = (
                "电脑使用了【桃】。"
            )

            self.actions.add(

                MoveCardAction(
                    tao,
                    ENEMY_HAND_RECT,
                    TABLE_CARD_RECT,
                    duration=0.27,

                    on_finish=(

                        lambda c=tao:
                        self.add_table_card(
                            c,
                            TABLE_CARD_RECT
                        )
                    )
                )
            )

            self.actions.add(
                WaitAction(0.40)
            )

            self.actions.add(

                CallbackAction(

                    lambda c=tao:
                    self.resolve_enemy_tao(
                        c
                    )
                )
            )

            return

        self.enemy_use_jiu()

    # ==================================================
    # AI 桃结算
    # ==================================================

    def resolve_enemy_tao(
        self,
        tao
    ):

        self.enemy.hp += 1

        if self.enemy.hp > self.enemy.max_hp:
            self.enemy.hp = self.enemy.max_hp

        self.message = (
            "电脑回复了 1 点体力。"
        )

        self.actions.add(
            WaitAction(0.35)
        )

        self.queue_to_discard(
            tao,
            TABLE_CARD_RECT,
            after=self.enemy_use_jiu
        )

    # ==================================================
    # AI 使用酒
    # ==================================================

    def enemy_use_jiu(self):

        if self.enemy_jiu_used:

            self.enemy_attack()

            return

        # ==================================================
        # 必须：
        # 有酒
        # 有杀
        # 能打到玩家
        # ==================================================

        if (
            self.enemy.has_card("JIU")
            and
            self.enemy.has_card("SHA")
            and
            self.can_attack(
                self.enemy,
                self.player
            )
        ):

            jiu = (
                self.enemy.remove_first(
                    "JIU"
                )
            )

            self.enemy_jiu_used = True

            self.enemy_wine_buff = True

            self.message = (
                "电脑使用了【酒】。"
            )

            self.actions.add(

                MoveCardAction(
                    jiu,
                    ENEMY_HAND_RECT,
                    TABLE_CARD_RECT,
                    duration=0.27,

                    on_finish=(

                        lambda c=jiu:
                        self.add_table_card(
                            c,
                            TABLE_CARD_RECT
                        )
                    )
                )
            )

            self.actions.add(
                WaitAction(0.40)
            )

            self.queue_to_discard(
                jiu,
                TABLE_CARD_RECT,
                after=self.enemy_attack
            )

            return

        self.enemy_attack()

    # ==================================================
    # AI 出杀
    # ==================================================

    def enemy_attack(self):

        # Basic trick AI: decisions only submit the same domain action used by
        # the human façade; all movement, requests and damage stay in V2.
        trick = next((card for card in self.enemy.hand if card.category == "trick"), None)
        if trick is not None:
            if trick.name in ("WUZHONG", "SHANDIAN"):
                targets = [self.enemy]
            elif trick.name == "TAOYUAN":
                targets = [player for player in (self.player, self.enemy) if player.hp > 0]
            elif trick.name == "WUGU":
                targets = [player for player in (self.enemy, self.player) if player.hp > 0]
            elif trick.name in ("NANMAN", "WANJIAN"):
                targets = [self.player] if self.player.hp > 0 else []
            elif trick.name == "TIESUO":
                recast = self.player.chained and self.enemy.chained
                targets = [] if recast else [player for player in (self.enemy, self.player) if player.hp > 0][:2]
            else:
                targets = [self.player]
            try:
                result = self.submit_action(
                    UseCardAction(
                        actor=self.enemy,
                        card=trick,
                        targets=targets,
                        source_rect=ENEMY_HAND_RECT,
                        metadata={"recast": bool(trick.name == "TIESUO" and not targets), "skip_wuxie": bool(trick.name == "TIESUO" and not targets)},
                        on_complete=lambda _result: self.enemy_attack(),
                    )
                )
                if result.status.value != "cancelled":
                    return
            except ValueError:
                pass

        # ==================================================
        # 没有实体杀时，丈八蛇矛可以用两张手牌转化
        # ==================================================

        has_real_sha = self.enemy.has_card("SHA")

        can_use_zhangba = (
            has_zhangba_spear(self.enemy)
            and len(self.enemy.hand) >= 2
        )

        if not has_real_sha and not can_use_zhangba:

            self.message = (
                "电脑结束出牌阶段。"
            )

            self.actions.add(
                WaitAction(0.40)
            )

            self.actions.add(

                CallbackAction(
                    self.enemy_discard_next
                )
            )

            return

        # ==================================================
        # 距离不够
        # ==================================================

        if not self.can_attack(
            self.enemy,
            self.player
        ):

            self.message = (
                "电脑的攻击距离不足。"
            )

            self.actions.add(
                WaitAction(0.50)
            )

            self.actions.add(

                CallbackAction(
                    self.enemy_discard_next
                )
            )

            return

        if not has_real_sha and can_use_zhangba:
            materials = list(self.enemy.hand[:2])
            virtual_sha = Card(
                name="SHA",
                category="basic",
                color=(245, 205, 195),
            )
            virtual_sha._virtual = True
            base_damage = 2 if self.enemy_wine_buff else 1
            self.enemy_wine_buff = False
            self.submit_action(
                UseCardAction(
                    actor=self.enemy,
                    card=virtual_sha,
                    targets=[self.player],
                    source_rect=ENEMY_HAND_RECT,
                    ignore_usage_limit=True,
                    metadata={
                        "skill": "ZHANGBA",
                        "materials": materials,
                        "base_damage": base_damage,
                    },
                    on_complete=lambda _result: self.after_enemy_sha_finished(),
                )
            )
            return

        # Real cards without complex Legacy equipment now use the same domain
        # action and Engine V2 flow as the human player.
        if has_real_sha:
            sha = next(
                card
                for card in self.enemy.hand
                if card.name == "SHA"
            )
            if self.engine.compatibility.supports_v2_sha(
                self.enemy,
                self.player,
                sha,
            ):
                self.submit_action(
                    UseCardAction(
                        actor=self.enemy,
                        card=sha,
                        targets=[self.player],
                        source_rect=ENEMY_HAND_RECT,
                        metadata={
                            "zhuque_fire": bool(
                                has_zhuque_fan(self.enemy)
                                and getattr(sha, "nature", "normal") == "normal"
                            )
                        },
                        on_complete=(
                            lambda _result:
                            self.after_enemy_sha_finished()
                        ),
                    )
                )
                return

        was_last_hand_card = (
            has_real_sha
            and len(self.enemy.hand) == 1
        )

        if has_real_sha:

            sha = self.enemy.remove_first(
                "SHA"
            )

        else:

            materials = [
                (
                    self.enemy.remove_card(0),
                    ENEMY_HAND_RECT,
                ),
                (
                    self.enemy.remove_card(0),
                    ENEMY_HAND_RECT,
                ),
            ]

            sha = Card(
                name="SHA",
                category="basic",
                color=(245, 205, 195),
            )
            sha._virtual = True

            self.queue_weapon_discards(
                materials
            )

        damage = 1

        # ==================================================
        # 酒杀
        # ==================================================

        if self.enemy_wine_buff:

            damage += 1

            self.enemy_wine_buff = False

        if has_real_sha:

            self.message = (
                "电脑使用了【"
                + sha.display_name
                + "】。"
            )

        else:

            self.message = (
                "电脑发动【丈八蛇矛】，"
                "将两张手牌当【杀】使用。"
            )

        if (
            has_fangtian_halberd(self.enemy)
            and was_last_hand_card
        ):

            self.message += (
                "【方天画戟】已触发，"
                "当前1v1没有额外目标。"
            )

        self.actions.add(

            MoveCardAction(
                sha,
                ENEMY_HAND_RECT,
                TABLE_CARD_RECT,
                duration=0.30,

                on_finish=(

                    lambda c=sha:
                    self.add_table_card(
                        c,
                        TABLE_CARD_RECT
                    )
                )
            )
        )

        self.actions.add(
            WaitAction(0.50)
        )

        self.actions.add(

            CallbackAction(

                lambda c=sha, d=damage:
                self.request_player_shan(
                    c,
                    d
                )
            )
        )

    # ==================================================
    # AI 弃牌
    # ==================================================

    def enemy_discard_next(self):

        if self.game_over:
            return

        # ==================================================
        # 手牌上限 = 当前体力
        # ==================================================

        if (
            len(self.enemy.hand)
            > self.enemy.hp
        ):

            index = random.randrange(
                len(self.enemy.hand)
            )

            card = (
                self.enemy.remove_card(
                    index
                )
            )

            self.message = (
                "电脑弃置【"
                + card.display_name
                + "】。"
            )

            self.queue_to_discard(
                card,
                ENEMY_HAND_RECT,
                after=self.enemy_discard_next
            )

            return

        # ==================================================
        # 电脑回合彻底结束
        # ==================================================

        self.start_player_turn()
