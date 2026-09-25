from enum import Enum


class ControllerType(str, Enum):
    HUMAN = "human"
    AI = "ai"

    # Phase 11.1 预留：局域网里的真人。本轮只登记这个类型，不产生这种角色；
    # 正式的对局同步在 Phase 11.2 接入。届时的规则是：流程只向
    # PlayerController 要动作，绝不写 "if remote_player: ..." 这类特判，
    # is_human / is_ai 只用于表现层（谁的面板要等输入）。
    REMOTE_HUMAN = "remote_human"


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
        # Phase 11.1 预留：REMOTE_HUMAN 角色对应的网络连接 id（本地 / AI 为 None）。
        # 它是"这条角色归哪个客户端"的唯一凭据，绝不是 socket 对象本身。
        self.connection_id = None
        self.alive = True

        self.gender = gender

        self.max_hp = max_hp

        self.hp = max_hp

        self.hand = []
        self.judgement_zone = []
        self.chained = False

        #: 武将牌是否正面朝上（翻面机制）：背面朝上时跳过自己的回合。
        self.face_up = True
        #: 技能的具名标记计数（暴怒 / 梦魇 / 忍 / 星 …）：
        #: ``{(skill_id, mark_name): count}``。技能自己的状态仍然优先放
        #: ``skill_state``；这里只放**需要被别人查询 / 需要展示**的计数。
        self.marks = {}
        #: 武将牌上的牌区（邓艾的「田」、神诸葛的「星」…）：
        #: ``{zone_name: [card, ...]}``。它不是手牌、不是判定区、也不是装备区。
        self.placed_cards = {}

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

        # ==================================================
        # 武将 / 技能
        #
        # 只保存武将的稳定 id；GeneralDef 与技能实例由注册表持有，
        # 技能状态保存在 SkillManager 注入的 SkillState 里（按技能命名空间），
        # 避免每个技能往 Player 上塞自己的字段。
        # ==================================================

        self.general_id = None
        self.skill_state = None
        # 势力：由绑定的武将决定（【激将】【救援】要问"是不是蜀 / 吴"）。
        self.kingdom = None

        # ==================================================
        # 身份
        #
        # 自由混战没有身份（保持 None）；身份模式由 GameMode 在开局写入。
        # 面向其他玩家 / AI 的判断一律走 src/game/identity.py 的可见性查询，
        # 不要直接读下面的真身字段。
        # ==================================================

        self.identity = None
        self.identity_revealed = False


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
    # 武将绑定
    # ==================================================

    def set_general(self, general):
        """绑定武将：只改身份与体力上限，技能由 SkillManager 负责。"""

        self.general_id = None if general is None else general.id
        if general is None:
            return self
        self.max_hp = general.max_hp
        self.hp = general.max_hp
        self.gender = general.gender
        # 势力也是武将定义的一部分。以前没有任何地方写过它，【激将】【救援】
        # 于是"绑得上但永远发动不了"（它们要问别人是不是蜀 / 吴势力）。
        self.kingdom = general.kingdom
        return self

    def has_general(self):
        return self.general_id is not None


    # ==================================================
    # 重置
    # ==================================================

    def reset(self):

        self.hp = self.max_hp
        self.alive = True

        self.identity = None
        self.identity_revealed = False

        self.hand = []
        self.judgement_zone = []
        self.chained = False
        self.face_up = True
        self.marks = {}
        self.placed_cards = {}

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

    # ==================================================
    # 技能标记（暴怒 / 梦魇 / 忍 / 权 …）
    #
    # 放在 Player 上而不是 skill_state 里，是因为这些计数**别人也要读**：
    # 武魂要看"谁持有最多的梦魇标记"、伪帝要看主公、庸肆要看势力数。
    # 键是技能的稳定 id，值是 {标记名: 数量}。
    # ==================================================

    def mark(self, skill_id, name="default"):
        return int((self.marks.get(skill_id) or {}).get(name, 0) or 0)

    def add_mark(self, skill_id, name="default", amount=1):
        bucket = self.marks.setdefault(skill_id, {})
        bucket[name] = int(bucket.get(name, 0) or 0) + int(amount)
        return bucket[name]

    def set_mark(self, skill_id, name, value):
        self.marks.setdefault(skill_id, {})[name] = int(value)
        return self.marks[skill_id][name]

    def clear_marks(self, skill_id=None):
        if skill_id is None:
            self.marks = {}
            return
        self.marks.pop(skill_id, None)

    def total_marks(self):
        return sum(int(value or 0) for bucket in self.marks.values() for value in bucket.values())

    # ==================================================
    # 武将牌上的牌区（田 / 星 …）
    #
    # 这些牌既不在手牌、也不在判定区与装备区，但**要能被别人看见**，
    # 因此不能塞进 hand。区域名由技能自己声明（"tian" / "star"）。
    # ==================================================

    def placed_zone(self, zone):
        return self.placed_cards.setdefault(zone, [])

    def placed_count(self, zone):
        return len(self.placed_cards.get(zone) or ())

    def place_card(self, zone, card):
        self.placed_zone(zone).append(card)
        return card

    def take_placed_card(self, zone, card=None):
        """从牌区取走一张（不传 card 时取最后一张）；没有则返回 None。"""

        pile = self.placed_cards.get(zone) or []
        if not pile:
            return None
        if card is None:
            return pile.pop()
        for index, item in enumerate(pile):
            if item is card:
                pile.pop(index)
                return item
        return None

    # ==================================================
    # 翻面
    # ==================================================

    @property
    def flipped(self):
        """武将牌是否背面朝上（背面朝上 = 跳过自己的下一个回合）。"""

        return not self.face_up

