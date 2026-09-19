from enum import Enum


class ControllerType(str, Enum):
    HUMAN = "human"
    AI = "ai"

    # A future LAN phase adds REMOTE_HUMAN here; the engine must keep
    # treating controller type as the only source of action authority.


HORSE_SLOTS = ("defensive_horse", "offensive_horse")


class Player:

    def __init__(
        self,
        name,
        max_hp=4,
        gender=None,
        player_id=None,
        seat=0,
        controller_type=ControllerType.HUMAN,
    ):

        self.name = name

        self.player_id = player_id or "P0"
        self.seat = int(seat)
        self.controller_type = ControllerType(controller_type)
        self.alive = True

        self.gender = gender

        self.max_hp = max_hp

        self.hp = max_hp

        self.hand = []
        self.judgement_zone = []
        self.chained = False

        # ==================================================
        # 装备区
        # ==================================================

        self.equipment = {

            "weapon": None,

            "armor": None,

            "defensive_horse": None,

            "offensive_horse": None,
        }

        # ==================================================
        # 本回合状态
        #
        # 这些标记必须按角色独立保存：多人局里每个 AI 都有自己的
        # 出杀次数与酒效果，不能共用一个全局布尔值。
        # ==================================================

        self.sha_used = False
        self.jiu_used = False
        self.wine_buff = False
        self.wine_sha_required = False


    @property
    def is_human(self):
        return self.controller_type is ControllerType.HUMAN

    @property
    def is_ai(self):
        return self.controller_type is ControllerType.AI

    @property
    def is_alive(self):
        return self.alive and self.hp > 0

    def clear_turn_state(self):
        self.sha_used = False
        self.jiu_used = False
        self.wine_buff = False
        self.wine_sha_required = False


    # ==================================================
    # 重置
    # ==================================================

    def reset(self):

        self.hp = self.max_hp
        self.alive = True

        self.hand = []
        self.judgement_zone = []
        self.chained = False

        self.equipment = {

            "weapon": None,

            "armor": None,

            "defensive_horse": None,

            "offensive_horse": None,
        }

        self.clear_turn_state()


    # ==================================================
    # 摸牌
    # ==================================================

    def draw_cards(
        self,
        deck,
        number
    ):

        for _ in range(number):

            card = deck.draw()

            if card is not None:

                self.hand.append(
                    card
                )


    # ==================================================
    # 手牌查询
    # ==================================================

    def has_card(
        self,
        name
    ):

        for card in self.hand:

            if card.name == name:
                return True

        return False


    def remove_first(
        self,
        name
    ):

        for i, card in enumerate(
            self.hand
        ):

            if card.name == name:

                return self.hand.pop(i)

        return None


    def remove_card(
        self,
        index
    ):

        if (
            0 <= index < len(self.hand)
        ):

            return self.hand.pop(index)

        return None


    # ==================================================
    # 装备
    # ==================================================

    def get_equipment(
        self,
        slot
    ):

        return self.equipment.get(
            slot
        )


    def remove_equipment(
        self,
        slot
    ):

        old_card = (
            self.equipment.get(slot)
        )

        self.equipment[slot] = None

        return old_card


    def set_equipment(
        self,
        card
    ):

        if card is None:
            return

        if card.subtype not in self.equipment:
            return

        self.equipment[
            card.subtype
        ] = card


    # ==================================================
    # 武器攻击范围
    # ==================================================

    @property
    def attack_range(self):

        weapon = self.equipment[
            "weapon"
        ]

        if weapon is None:
            return 1

        return weapon.attack_range


    # ==================================================
    # 是否有 +1 马
    # ==================================================

    @property
    def has_defensive_horse(self):

        return (
            self.equipment[
                "defensive_horse"
            ]
            is not None
        )


    # ==================================================
    # 是否有 -1 马
    # ==================================================

    @property
    def has_offensive_horse(self):

        return (
            self.equipment[
                "offensive_horse"
            ]
            is not None
        )
