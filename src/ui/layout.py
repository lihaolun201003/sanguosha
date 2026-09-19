"""Single source of truth for table geometry.

The drawn rect and the hit-test rect are always the same object, so a panel
that moves can never leave a stale click area behind.
"""

import pygame

WIDTH = 1000
HEIGHT = 700

# 顶部 AI 座位带
SEAT_TOP_Y = 30
SEAT_TOP_HEIGHT = 132
SEAT_TOP_WIDTH_SOLO = 236
SEAT_TOP_WIDTH_DUO = 214
SEAT_TOP_WIDTH_TRIO = 194
SEAT_TOP_GAP = 22

# 两侧 AI 座位
SEAT_SIDE_WIDTH = 178
SEAT_SIDE_HEIGHT = 132
SEAT_SIDE_X = 10
SEAT_SIDE_Y = 210
SEAT_SIDE_STEP = 142

# 中央牌桌
CENTRAL_RECT = pygame.Rect(188, 166, 624, 288)

# 牌堆 / 弃牌堆
CARD_PILE_SIZE = (86, 122)
DRAW_PILE_RECT = pygame.Rect(226, 258, *CARD_PILE_SIZE)
DISCARD_PILE_RECT = pygame.Rect(688, 258, *CARD_PILE_SIZE)

# 中央出牌展示（放在公共牌区下方，避免与五谷翻牌重叠）
TABLE_CARD_RECT = pygame.Rect(438, 300, 90, 124)
RESPONSE_CARD_RECT = pygame.Rect(534, 300, 90, 124)

# 公共牌区（五谷 / 需要从别处选牌时）
PUBLIC_POOL_Y = 178
PUBLIC_POOL_SIZE = (66, 94)
PUBLIC_POOL_GAP = 8

# 真人区域
PROMPT_RECT = pygame.Rect(188, 462, 624, 60)
PLAYER_STATUS_RECT = pygame.Rect(188, 528, 624, 46)
HAND_TOP = 578
HAND_CARD_SIZE = (88, 122)
HAND_AREA = pygame.Rect(188, HAND_TOP, 624, 126)

# 操作按钮
PRIMARY_BUTTON_RECT = pygame.Rect(820, 578, 170, 56)
SECONDARY_BUTTON_RECT = pygame.Rect(820, 640, 170, 44)

# 动画起点（供引擎选择飞牌起始位置）
PLAYER_HAND_SOURCE_RECT = (438, HAND_TOP, *HAND_CARD_SIZE)
ENEMY_HAND_SOURCE_RECT = (440, 150, *HAND_CARD_SIZE)

# 手牌悬停 / 选中位移
HOVER_LIFT = 22
SELECTED_LIFT = 34


def _hit_test(rects, position, *, lift_rects=None):
    """Topmost-first hit test; ``lift_rects`` extends each card's grab area."""

    for index in range(len(rects) - 1, -1, -1):
        rect = rects[index]
        if rect.collidepoint(position):
            return index
        if lift_rects is not None and lift_rects[index].collidepoint(position):
            return index
    return None


def public_rect_list(cards):
    """Public-pool card rects; usable without a Game instance."""

    count = len(cards)
    if count == 0:
        return []
    width, height = PUBLIC_POOL_SIZE
    total = count * width + max(0, count - 1) * PUBLIC_POOL_GAP
    start = WIDTH // 2 - total // 2
    return [
        pygame.Rect(start + index * (width + PUBLIC_POOL_GAP), PUBLIC_POOL_Y, width, height)
        for index in range(count)
    ]


class TableLayout:
    """Geometry for one frame, derived from the live Game state."""

    def __init__(self, game, mouse_pos=None, selected_card_ids=()):
        self.game = game
        self.mouse_pos = mouse_pos
        self.selected_card_ids = set(selected_card_ids)
        self.seat_rects = self._build_seat_rects()
        self.hand_rects = []
        self.hand_base_rects = []
        self.hand_hit_rects = []
        self.hand_hover = None
        self.selected_hand_keys = {
            index
            for index, card in enumerate(game.player.hand)
            if id(card) in self.selected_card_ids
        }
        self._build_hand()

    # ==================================================
    # 座位
    # ==================================================

    def _opponents(self):
        ordered = sorted(self.game.players, key=lambda player: player.seat)
        return [player for player in ordered if player is not self.game.player]

    def _build_seat_rects(self):
        opponents = self._opponents()
        count = len(opponents)
        rects = {}
        if count == 0:
            return rects

        if count <= 3:
            width = (
                SEAT_TOP_WIDTH_SOLO if count == 1
                else SEAT_TOP_WIDTH_DUO if count == 2
                else SEAT_TOP_WIDTH_TRIO
            )
            total = count * width + (count - 1) * SEAT_TOP_GAP
            start = (WIDTH - total) // 2
            for index, player in enumerate(opponents):
                rects[player] = pygame.Rect(
                    start + index * (width + SEAT_TOP_GAP),
                    SEAT_TOP_Y,
                    width,
                    SEAT_TOP_HEIGHT,
                )
            return rects

        top = opponents[:3]
        side = opponents[3:]
        total = len(top) * SEAT_TOP_WIDTH_TRIO + (len(top) - 1) * SEAT_TOP_GAP
        start = (WIDTH - total) // 2
        for index, player in enumerate(top):
            rects[player] = pygame.Rect(
                start + index * (SEAT_TOP_WIDTH_TRIO + SEAT_TOP_GAP),
                SEAT_TOP_Y,
                SEAT_TOP_WIDTH_TRIO,
                SEAT_TOP_HEIGHT,
            )

        left = side[0::2]
        right = side[1::2]
        for index, player in enumerate(left):
            rects[player] = pygame.Rect(
                SEAT_SIDE_X,
                SEAT_SIDE_Y + index * SEAT_SIDE_STEP,
                SEAT_SIDE_WIDTH,
                SEAT_SIDE_HEIGHT,
            )
        for index, player in enumerate(right):
            rects[player] = pygame.Rect(
                WIDTH - SEAT_SIDE_X - SEAT_SIDE_WIDTH,
                SEAT_SIDE_Y + index * SEAT_SIDE_STEP,
                SEAT_SIDE_WIDTH,
                SEAT_SIDE_HEIGHT,
            )
        return rects

    def seat_rect(self, player):
        return self.seat_rects.get(player)

    def player_at(self, position):
        for player, rect in self.seat_rects.items():
            if rect.collidepoint(position):
                return player
        if PLAYER_STATUS_RECT.collidepoint(position):
            return self.game.player
        return None

    # ==================================================
    # 手牌
    # ==================================================

    def _base_hand_rects(self, hand):
        count = len(hand)
        if count == 0:
            return []

        width, height = HAND_CARD_SIZE
        area_width = HAND_AREA.width
        step = width
        if count > 1:
            natural = count * width + (count - 1) * 10
            if natural > area_width:
                step = (area_width - width) / (count - 1)
            else:
                step = width + min(10, (area_width - count * width) / max(1, count - 1))
        total = width + step * (count - 1)
        start = HAND_AREA.centerx - total / 2

        return [
            pygame.Rect(round(start + index * step), HAND_TOP, width, height)
            for index in range(count)
        ]

    def _build_hand(self):
        hand = list(self.game.player.hand)
        base = self._base_hand_rects(hand)
        self.hand_base_rects = base
        if not base:
            return

        lifted_hover = [rect.move(0, -HOVER_LIFT) for rect in base]
        self.hand_hit_rects = [
            base[index].union(lifted_hover[index]) for index in range(len(base))
        ]

        if self.mouse_pos is not None:
            self.hand_hover = _hit_test(base, self.mouse_pos, lift_rects=self.hand_hit_rects)

        rects = []
        for index, rect in enumerate(base):
            lift = 0
            if index in self.selected_hand_keys:
                lift = SELECTED_LIFT
            elif index == self.hand_hover:
                lift = HOVER_LIFT
            rects.append(rect.move(0, -lift))
        self.hand_rects = rects

    def hand_index_at(self, position):
        if not self.hand_rects:
            return None
        return _hit_test(self.hand_rects, position, lift_rects=self.hand_hit_rects)

    def hand_rect(self, index):
        if 0 <= index < len(self.hand_rects):
            return self.hand_rects[index]
        return None

    # ==================================================
    # 公共牌 / 中央
    # ==================================================

    def public_rects(self, cards):
        return public_rect_list(cards)

    def public_index_at(self, position, cards):
        for index, rect in enumerate(self.public_rects(cards)):
            if rect.collidepoint(position):
                return index
        return None

    # ==================================================
    # 真人装备槽
    # ==================================================

    def player_equipment_rects(self):
        rects = {}
        size = 56
        gap = 8
        start_x = PLAYER_STATUS_RECT.x + 192
        for index, slot in enumerate(("weapon", "armor", "defensive_horse", "offensive_horse")):
            rects[slot] = pygame.Rect(
                start_x + index * (size + gap),
                PLAYER_STATUS_RECT.y + 4,
                size,
                PLAYER_STATUS_RECT.height - 8,
            )
        return rects
