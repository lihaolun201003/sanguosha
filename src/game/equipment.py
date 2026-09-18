from src.actions import (
    CallbackAction,
    MoveCardAction,
    WaitAction,
)

from src.constants import (
    DRAW_PILE_RECT,
    PLAYER_EQUIPMENT_RECTS,
    RESPONSE_CARD_RECT,
)

from .equipment_skills.armors import (
    blocking_armor_for_sha,
    has_bagua,
    is_red_card,
    modify_sha_damage,
)
from .engine import Event, EventType

from .equipment_skills.weapons import (
    has_guding_blade,
    has_qinggang_sword,
    has_zhuge_crossbow,
)
from .rules import DistanceRule


class EquipmentMixin:

    # ==================================================
    # 计算两人的距离
    # ==================================================

    def get_distance(
        self,
        attacker,
        defender
    ):

        return DistanceRule.distance(self, attacker, defender)


    # ==================================================
    # 是否在攻击范围内
    # ==================================================

    def can_attack(
        self,
        attacker,
        defender
    ):

        return DistanceRule.in_attack_range(self, attacker, defender)


    # ==================================================
    # 诸葛连弩
    # 是否忽略每回合一次杀限制
    # ==================================================

    def can_use_unlimited_sha(
        self,
        player
    ):

        return has_zhuge_crossbow(
            player
        )


    # ==================================================
    # 青釭剑
    # 本次杀是否无视防具
    # ==================================================

    def sha_ignores_armor(
        self,
        attacker
    ):

        return has_qinggang_sword(
            attacker
        )


    # ==================================================
    # 杀是否被防具直接无效
    # ==================================================

    def get_sha_blocking_armor(
        self,
        attacker,
        defender,
        sha
    ):

        # 青釭剑：
        # 整张杀无视目标防具。
        if self.sha_ignores_armor(
            attacker
        ):

            return None

        return blocking_armor_for_sha(
            defender,
            sha
        )


    # ==================================================
    # 修正杀造成的最终伤害
    # ==================================================

    def apply_sha_damage_modifiers(
        self,
        attacker,
        defender,
        sha,
        damage
    ):

        damage = max(
            0,
            int(damage)
        )

        effects = []

        # ==================================================
        # 古锭刀
        #
        # 使用杀造成伤害时，
        # 如果目标此时没有手牌，
        # 伤害 +1。
        #
        # 在防具修正之前计算。
        # ==================================================

        if (
            damage > 0
            and
            has_guding_blade(
                attacker
            )
            and
            len(defender.hand) == 0
        ):

            damage += 1

            effects.append(
                "【古锭刀】使伤害 +1"
            )

        # ==================================================
        # 青釭剑
        #
        # 无视所有防具效果：
        #
        # - 仁王盾
        # - 藤甲
        # - 白银狮子
        #
        # 八卦阵在进入伤害步骤以前
        # 已经由 has_bagua_armor 跳过。
        # ==================================================

        if self.sha_ignores_armor(
            attacker
        ):

            if (
                defender.get_equipment(
                    "armor"
                )
                is not None
            ):

                effects.append(
                    "【青釭剑】无视防具"
                )

            return (
                damage,
                effects
            )

        # ==================================================
        # 正常防具伤害修正
        # ==================================================

        damage, armor_effects = (
            modify_sha_damage(
                defender,
                sha,
                damage
            )
        )

        effects.extend(
            armor_effects
        )

        return (
            damage,
            effects
        )


    # ==================================================
    # 是否装备八卦阵
    # ==================================================

    def has_bagua_armor(
        self,
        player,
        attacker=None
    ):

        # 青釭剑攻击时，
        # 八卦阵也被无视。
        if (
            attacker is not None
            and
            self.sha_ignores_armor(
                attacker
            )
        ):

            return False

        return has_bagua(
            player
        )


    # ==================================================
    # 八卦阵判定
    # ==================================================

    def queue_bagua_judgment(
        self,
        owner,
        on_success,
        on_failure
    ):

        judgment = self.deck.draw()

        # 正常牌堆逻辑下几乎不会发生。
        # 如果确实无牌可判定，就视为判定失败。
        if judgment is None:

            self.message = (
                owner.name
                + "的【八卦阵】无法进行判定。"
            )

            on_failure()

            return

        self.message = (
            owner.name
            + "发动【八卦阵】，进行判定。"
        )

        self.actions.add(

            MoveCardAction(
                judgment,
                DRAW_PILE_RECT,
                RESPONSE_CARD_RECT,
                duration=0.28,

                on_finish=(

                    lambda c=judgment:
                        self.add_table_card(
                            c,
                            RESPONSE_CARD_RECT
                        )
                )
            )
        )

        self.actions.add(
            WaitAction(0.45)
        )

        self.actions.add(

            CallbackAction(

                lambda c=judgment:
                    self.resolve_bagua_judgment(
                        owner,
                        c,
                        on_success,
                        on_failure
                    )
            )
        )


    def resolve_bagua_judgment(
        self,
        owner,
        judgment,
        on_success,
        on_failure
    ):

        success = is_red_card(
            judgment
        )

        if success:

            self.message = (
                owner.name
                + "的【八卦阵】判定为红色，"
                "判定成功。"
            )

            after = on_success

        else:

            self.message = (
                owner.name
                + "的【八卦阵】判定不是红色，"
                "判定失败。"
            )

            after = on_failure

        self.actions.add(
            WaitAction(0.35)
        )

        self.queue_to_discard(
            judgment,
            RESPONSE_CARD_RECT,
            after=after
        )


    # ==================================================
    # 取下装备，并处理“失去装备”效果
    # ==================================================

    def remove_equipment_with_effects(
        self,
        player,
        slot
    ):

        old_card = player.remove_equipment(
            slot
        )

        event = self.context.emit(
            Event(
                EventType.EQUIPMENT_LOST,
                source=old_card,
                target=player,
                payload={"card": old_card, "slot": slot, "healed": False},
            )
        ) if old_card is not None else None
        healed = bool(event and event.payload.get("healed"))

        return (
            old_card,
            healed
        )


    # ==================================================
    # 玩家装备一张牌
    # ==================================================

    def player_equip(
        self,
        index,
        source_rect
    ):

        card = self.player.remove_card(
            index
        )

        if card is None:
            return

        slot = card.subtype

        target_rect = (
            PLAYER_EQUIPMENT_RECTS[
                slot
            ]
        )

        old_card, healed = (
            self.remove_equipment_with_effects(
                self.player,
                slot
            )
        )

        # ==================================================
        # 旧装备进入弃牌堆
        # ==================================================

        if old_card is not None:

            self.queue_to_discard(
                old_card,
                target_rect
            )

        self.message = (
            "你装备了【"
            + card.display_name
            + "】。"
        )

        if healed:

            self.message += (
                "失去【白银狮子】，"
                "回复 1 点体力。"
            )

        # ==================================================
        # 新装备飞入装备槽
        # ==================================================

        self.actions.add(

            MoveCardAction(
                card,
                source_rect,
                target_rect,
                duration=0.32,

                on_finish=(

                    lambda c=card:
                        self.player.set_equipment(
                            c
                        )
                )
            )
        )

        self.actions.add(
            WaitAction(0.25)
        )
