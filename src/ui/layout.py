"""Resolution-aware table geometry.

Layout is authored in a fixed **design space** (1600×900, 16:9) and mapped onto
whatever the real screen is.  Every component asks this module for its rects, so
a drawn rect and its hit-test rect are always the same object — including after
a fullscreen toggle or a window resize.

    real screen  →  LayoutMetrics  →  design rect  →  screen rect

Nothing here scales a rendered surface: components are re-drawn at the real
resolution, so text stays crisp and hit testing stays exact.
"""

import pygame

from . import theme

# ==================================================
# 设计坐标（16:9）
# ==================================================

DESIGN_WIDTH = 1600
DESIGN_HEIGHT = 900

# 兼容别名：旧调用点用 WIDTH / HEIGHT 表示"当前设计画布"。
WIDTH = DESIGN_WIDTH
HEIGHT = DESIGN_HEIGHT

MARGIN = 16

# 顶部 AI 座位带
SEAT_TOP_Y = 16
SEAT_TOP_HEIGHT = 158
SEAT_TOP_WIDTH_SOLO = 330
SEAT_TOP_WIDTH_DUO = 310
SEAT_TOP_WIDTH_TRIO = 286
SEAT_TOP_GAP = 26

# 两侧 AI 座位
#
# step 比卡高多留 42：两张侧座之间要看得清是两个人（原来只差 16px，
# 视觉上挤成一块）。侧座列在提示条以左（x 16..280），所以可以比中央
# 战场再往下伸一点，不与提示条 / 手牌冲突。
SEAT_SIDE_WIDTH = 264
SEAT_SIDE_HEIGHT = 158
SEAT_SIDE_X = MARGIN
SEAT_SIDE_Y = 176
SEAT_SIDE_STEP = 200

# 中央牌桌
#
# 高度按"放得下放大后的动作卡与牌堆"给（300），剩下的高度全部分给下方三段
# 与手牌：手牌从 106×148 放大到 124×173，是这一轮"看着大气"的主要来源。
CENTRAL_RECT = pygame.Rect(288, 176, 1024, 300)

# 牌堆 / 弃牌堆 / 出牌位
#
# 牌堆与弃牌堆是桌面上仅有的两个"实物"标识，原来 50×70 太小、几乎看不见；
# 放大到 64×90 之后才有牌堆的体量感。
CARD_PILE_SIZE = (64, 90)
# 与动作卡同一条水平带（垂直居中于出牌位），三者在中央形成一条主轴。
DRAW_PILE_RECT = pygame.Rect(342, 339, *CARD_PILE_SIZE)
DISCARD_PILE_RECT = pygame.Rect(1182, 339, *CARD_PILE_SIZE)

# 中央出牌位（Action Card Anchor）
#
# 当前动作主体卡与响应牌在中央战场的**唯一权威落位**：引擎动画、桌面静态
# 展示、tooltip 避让都从这里取坐标（屏幕坐标一律由 LayoutMetrics 换算），
# 不存在第二份写死的坐标。位置要求：落在中央战场内，不压底部手牌 / 提示条，
# 与上方公共牌池（PUBLIC_POOL_Y..+height）不重叠。
ACTION_CARD_RECT = pygame.Rect(716, 292, 132, 184)
RESPONSE_CARD_RECT = pygame.Rect(860, 292, 132, 184)

# 兼容旧名：TABLE_CARD_RECT 指的就是当前动作主体卡。
TABLE_CARD_RECT = ACTION_CARD_RECT

# 公共牌区（五谷 / 从别处选牌）
#
# Phase 10.4：公共牌是**公开信息**，必须画真实卡面，所以尺寸始终保持在卡面
# 素材的最小绘制线（cards.ART_MIN_WIDTH / ART_MIN_HEIGHT）之上。牌少时用完整
# 竖版尺寸，牌多时按可用宽度自动缩小并收紧间距，始终保持与手牌一致的竖版比例。
#
# Phase 10.5：整条带子上移并略降高度，让出中央的出牌展示位——五谷公共牌池与
# 当前动作卡不再水平重叠。
PUBLIC_POOL_Y = 168
# 高度上限回到 120：公共牌池（五谷一类）与当前动作卡必须垂直错开，
# 中央战场只有 300 高，塞不下"132 + 184"。
PUBLIC_POOL_MAX_HEIGHT = 120
PUBLIC_POOL_MIN_HEIGHT = 112
PUBLIC_POOL_ASPECT = 124 / 173.0     # 宽 / 高（与手牌同一竖版比例）
PUBLIC_POOL_GAP = 12
PUBLIC_POOL_MAX_SPAN = 780           # 单行允许占用的设计宽度
PUBLIC_POOL_SIZE = (
    int(round(PUBLIC_POOL_MAX_HEIGHT * PUBLIC_POOL_ASPECT)),
    PUBLIC_POOL_MAX_HEIGHT,
)
PUBLIC_POOL_HOVER_SCALE = 1.18       # 悬停放大倍数
PUBLIC_POOL_HOVER_LIFT = 12          # 悬停上浮（设计坐标）

# 真人区域
#
# 间距是算过的：中央战场 → 提示条 → 座位状态条 → 手牌，四段之间都要留出
# 呼吸空间。提示条要放得下"标题 + 正文"两行，所以高度按两行 + 内边距给。
PROMPT_RECT = pygame.Rect(288, 482, 1024, 88)
# 状态条高度 74：名字行、装备槽与血点行要各自占一行，原来是 64 挤在一起。
PLAYER_STATUS_RECT = pygame.Rect(288, 580, 1024, 74)

# 手牌：与状态条之间留出足够空间，悬停 / 选中上浮都不会压到状态信息。
# 56 是设计值：选中上浮 40 之后仍留 16px，小窗口（1366×768）缩放后
# 也有 12px——原来 40 的间距在选中时会正好顶到状态条底边。
HAND_TOP = 710
HAND_CARD_SIZE = (124, 173)
HAND_AREA = pygame.Rect(288, HAND_TOP, 1024, HAND_CARD_SIZE[1])

# 操作按钮（固定位置，不随 Pending 移动）
PRIMARY_BUTTON_RECT = pygame.Rect(1336, 742, 248, 60)
SECONDARY_BUTTON_RECT = pygame.Rect(1336, 812, 248, 48)
# 投降：右上角的小方块（顶部座位带最右端之外的空位）。
# 误触要二次确认（见 Renderer.request_surrender）。
SURRENDER_BUTTON_RECT = pygame.Rect(1512, 18, 74, 44)

# 节奏控件
SPEED_CONTROL_RECT = pygame.Rect(16, 16, 240, 84)

# 战报（左下角）
LOG_WIDTH = 268
LOG_HEIGHT = 124
LOG_BOTTOM_MARGIN = 14

# 手牌位移（跟着放大后的手牌一起放大，否则"抬起来"看不出来）
HOVER_LIFT = 28
SELECTED_LIFT = 40


class LayoutMetrics:
    """Maps design coordinates onto the current screen."""

    def __init__(self, width, height):
        self.screen_w = max(640, int(width))
        self.screen_h = max(480, int(height))
        self.scale = min(
            self.screen_w / float(DESIGN_WIDTH),
            self.screen_h / float(DESIGN_HEIGHT),
        )
        self.content_w = DESIGN_WIDTH * self.scale
        self.content_h = DESIGN_HEIGHT * self.scale
        self.offset_x = (self.screen_w - self.content_w) / 2.0
        self.offset_y = (self.screen_h - self.content_h) / 2.0
        self.fonts = ScaledFonts(self.scale)

    # ---- 换算 ----

    def px(self, value):
        return int(round(value * self.scale))

    def point(self, x, y):
        return (
            int(round(self.offset_x + x * self.scale)),
            int(round(self.offset_y + y * self.scale)),
        )

    def rect(self, x, y, w, h):
        return pygame.Rect(
            int(round(self.offset_x + x * self.scale)),
            int(round(self.offset_y + y * self.scale)),
            max(1, int(round(w * self.scale))),
            max(1, int(round(h * self.scale))),
        )

    def to_screen(self, design_rect):
        return self.rect(design_rect.x, design_rect.y, design_rect.width, design_rect.height)

    # ---- 底部锚点 ----

    @property
    def bottom_extra(self):
        """设计内容带**下方的空余**（非 16:9 屏幕才会大于 0）。

        画布按设计比例居中，于是 4:3 之类的屏幕上会上下各留一条空带。上半部分
        （对手座位 / 中央出牌位）留空没问题，但**底部那一组**（提示 / 状态条 /
        手牌 / 固定按钮）必须贴着屏幕底边——否则手牌会浮在半空中，下面空出
        一大片，看起来像"牌没放到底"。
        """

        return max(0, int(round(
            self.screen_h - (self.offset_y + self.content_h))))

    def to_bottom_screen(self, design_rect):
        """把底部那一组的设计矩形贴到真正的屏幕底边（16:9 时与 to_screen 相同）。"""

        return self.to_screen(design_rect).move(0, self.bottom_extra)

    # ---- 命名区域 ----

    @property
    def central(self):
        return self.to_screen(CENTRAL_RECT)

    @property
    def prompt(self):
        return self.to_bottom_screen(PROMPT_RECT)

    @property
    def player_status(self):
        return self.to_bottom_screen(PLAYER_STATUS_RECT)

    @property
    def hand_area(self):
        return self.to_bottom_screen(HAND_AREA)

    @property
    def primary_button(self):
        return self.to_bottom_screen(PRIMARY_BUTTON_RECT)

    @property
    def secondary_button(self):
        return self.to_bottom_screen(SECONDARY_BUTTON_RECT)

    @property
    def surrender_button(self):
        return self.to_screen(SURRENDER_BUTTON_RECT)

    @property
    def speed_control(self):
        return self.to_screen(SPEED_CONTROL_RECT)

    @property
    def log_rect(self):
        height = self.px(LOG_HEIGHT)
        return pygame.Rect(
            self.px(MARGIN),
            self.screen_h - height - self.px(LOG_BOTTOM_MARGIN),
            self.px(LOG_WIDTH),
            height,
        )

    def hand_card_size(self):
        return (self.px(HAND_CARD_SIZE[0]), self.px(HAND_CARD_SIZE[1]))

    def hand_top(self):
        # 与 hand_area 用同一个锚点：手牌区贴屏幕底边，牌才会落在最下面。
        return self.to_bottom_screen(HAND_AREA).y

    # ---- 中央出牌位（Action Card Anchor）----
    #
    # 引擎与绘制都只认这几个属性：分辨率一变就重新换算，永远不会留下
    # 上一套屏幕坐标。

    @property
    def action_card_rect(self):
        """当前动作主体卡的屏幕矩形。"""

        return self.to_screen(ACTION_CARD_RECT)

    @property
    def action_card_center(self):
        return self.action_card_rect.center

    @property
    def response_card_rect(self):
        """响应牌（闪 / 无懈）的屏幕矩形。"""

        return self.to_screen(RESPONSE_CARD_RECT)

    @property
    def action_display_rect(self):
        """中央展示带：主体卡 + 响应牌共同占用的区域。"""

        return self.action_card_rect.union(self.response_card_rect)

    # ---- 动画落点 ----

    def placement_rect(self, key):
        """一个命名落位在当前分辨率下的屏幕矩形（未知键返回 None）。"""

        return self.animation_rects().get(key)

    def animation_rects(self):
        """Screen rects the engine animation should fly to/from."""

        width, height = HAND_CARD_SIZE
        return {
            "table_card": self.action_card_rect,
            "response_card": self.response_card_rect,
            "discard_pile": self.to_screen(DISCARD_PILE_RECT),
            "draw_pile": self.to_screen(DRAW_PILE_RECT),
            "player_hand": self.to_screen(
                pygame.Rect(HAND_AREA.centerx - width // 2, HAND_TOP, width, height)
            ),
            "opponent_hand": self.rect(
                DESIGN_WIDTH // 2 - width // 2, 152, width, height
            ),
        }


class ScaledFonts:
    """Font accessor bound to one metrics scale."""

    def __init__(self, scale):
        self.scale = scale

    def get(self, name):
        return theme.fonts().get(name, self.scale)

    def suit(self, size):
        return theme.fonts().suit(max(8, int(round(size * self.scale))))


def public_pool_card_size(count, gap=PUBLIC_POOL_GAP):
    """公共牌的设计尺寸：牌少用完整竖版，牌多按可用宽度缩小（仍是竖版比例）。

    返回 ``(width, height, gap)``；结果始终不低于 ``PUBLIC_POOL_MIN_HEIGHT``，
    因此永远不会小到让卡面退回纯文字排版。
    """

    if count <= 0:
        return PUBLIC_POOL_SIZE[0], PUBLIC_POOL_SIZE[1], gap
    span = PUBLIC_POOL_MAX_SPAN - max(0, count - 1) * gap
    height = int(round(span / (count * PUBLIC_POOL_ASPECT)))
    height = max(PUBLIC_POOL_MIN_HEIGHT, min(PUBLIC_POOL_MAX_HEIGHT, height))
    width = max(1, int(round(height * PUBLIC_POOL_ASPECT)))
    return width, height, gap


PLACEMENT_KEYS = (
    "table_card", "response_card", "discard_pile", "draw_pile",
    "player_hand", "opponent_hand",
)


def resolve_card_rect(metrics, rect_data):
    """一条桌面落位（``game.table_cards`` 的第二项）对应的屏幕矩形。

    落位可以是命名区域（"table_card" / "response_card"）：每次绘制都按**当前**
    布局重新解析，所以窗口 resize / F11 全屏切换之后主体卡自动落到新的中央
    位置，不会停留在旧分辨率的像素上。显式坐标原样返回（历史调用点与测试）。
    """

    if isinstance(rect_data, str):
        rect = metrics.placement_rect(rect_data)
        if rect is None:
            rect = metrics.action_card_rect
        return pygame.Rect(rect)
    return pygame.Rect(*rect_data)


def public_rect_list(cards, metrics=None):
    """Public-pool card rects; usable without a Game instance."""

    count = len(cards)
    if count == 0:
        return []
    width, height, gap = public_pool_card_size(count)
    total = count * width + max(0, count - 1) * gap
    start = DESIGN_WIDTH // 2 - total // 2
    design = [
        pygame.Rect(start + index * (width + gap), PUBLIC_POOL_Y, width, height)
        for index in range(count)
    ]
    if metrics is None:
        return design
    return [metrics.to_screen(rect) for rect in design]


def _hit_test(rects, position, *, lift_rects=None):
    """Topmost-first hit test; ``lift_rects`` extends each card's grab area."""

    for index in range(len(rects) - 1, -1, -1):
        if rects[index].collidepoint(position):
            return index
        if lift_rects is not None and lift_rects[index].collidepoint(position):
            return index
    return None


class TableLayout:
    """Geometry for one frame, derived from the live Game state + metrics."""

    def __init__(self, game, metrics=None, mouse_pos=None, selected_card_ids=()):
        self.game = game
        self.metrics = metrics if metrics is not None else LayoutMetrics(DESIGN_WIDTH, DESIGN_HEIGHT)
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
        """按**座次环**把对手摆到真人周围。

        规则距离只认 SeatManager；这里只决定"画在哪"。视觉规律：

        * 正对面（偶数人数的第 n/2 个）放正上方；
        * 顺时针前半放左侧、后半放右侧；
        * 每侧**离自己越近的越靠下**，越远越靠上。

        于是自己的两个距离 1 邻居永远落在自己左右下方，一眼能看出谁近谁远。
        7 人时（自己 seat 0）的实际效果：

                     seat 4
            seat 3            seat 5
            seat 2            seat 6
            seat 1            seat 7
                        真人
        """

        ordered = sorted(self.game.players, key=lambda player: player.seat)
        total = len(ordered)
        me = self.game.player
        rects = {}
        if total <= 1 or me not in ordered:
            return rects

        my_index = ordered.index(me)
        top = []
        left = []
        right = []
        for index, player in enumerate(ordered):
            if player is me:
                continue
            offset = (index - my_index) % total
            distance = min(offset, total - offset)
            if offset * 2 == total:
                top.append((player, distance))
            elif offset * 2 < total:
                left.append((player, distance))
            else:
                right.append((player, distance))

        metrics = self.metrics

        if top:
            width = min(SEAT_TOP_WIDTH_SOLO, metrics.px(DESIGN_WIDTH - 2 * MARGIN))
            span = len(top) * width + (len(top) - 1) * SEAT_TOP_GAP
            start = (DESIGN_WIDTH - span) // 2
            for index, (player, _distance) in enumerate(top):
                rects[player] = metrics.rect(
                    start + index * (width + SEAT_TOP_GAP),
                    SEAT_TOP_Y, width, SEAT_TOP_HEIGHT,
                )

        # 距离大的在上、距离小的（自己的近邻）在下。
        for side_players, x in (
            (sorted(left, key=lambda item: -item[1]), SEAT_SIDE_X),
            (sorted(right, key=lambda item: -item[1]),
             DESIGN_WIDTH - SEAT_SIDE_X - SEAT_SIDE_WIDTH),
        ):
            for index, (player, _distance) in enumerate(side_players):
                rects[player] = metrics.rect(
                    x,
                    SEAT_SIDE_Y + index * SEAT_SIDE_STEP,
                    SEAT_SIDE_WIDTH,
                    SEAT_SIDE_HEIGHT,
                )
        return rects

    def seat_rect(self, player):
        return self.seat_rects.get(player)

    def any_seat_rect(self, player):
        """任何角色的屏幕 rect（含真人底部的状态条）。

        只给表现层用（指向箭头 / 特效定位），不参与任何规则判定。
        """

        rect = self.seat_rects.get(player)
        if rect is not None:
            return rect
        if player is self.game.player:
            return self.metrics.player_status
        return None

    def player_at(self, position):
        for player, rect in self.seat_rects.items():
            if rect.collidepoint(position):
                return player
        if self.metrics.player_status.collidepoint(position):
            return self.game.player
        return None

    # ==================================================
    # 手牌
    # ==================================================

    def _base_hand_rects(self, hand):
        count = len(hand)
        if count == 0:
            return []

        metrics = self.metrics
        area = metrics.hand_area
        width, height = metrics.hand_card_size()
        top = metrics.hand_top()
        gap = metrics.px(12)

        if count <= 1:
            step = width
        else:
            natural = count * (width + gap) - gap
            if natural <= area.width:
                # 放得下就完整展开，牌与牌之间留出间隙，不再重叠。
                step = width + gap
            else:
                # 放不下才水平重叠，并保留足够宽度让花色与点数可见。
                step = (area.width - width) / float(count - 1)
        total = width + step * (count - 1)
        start = area.centerx - total / 2.0

        return [
            pygame.Rect(round(start + index * step), top, width, height)
            for index in range(count)
        ]

    def _build_hand(self):
        hand = list(self.game.player.hand)
        base = self._base_hand_rects(hand)
        self.hand_base_rects = base
        self.hand_rects = list(base)
        if not base:
            return

        hover_lift = self.metrics.px(HOVER_LIFT)
        selected_lift = self.metrics.px(SELECTED_LIFT)

        lifted_hover = [rect.move(0, -hover_lift) for rect in base]
        self.hand_hit_rects = [
            base[index].union(lifted_hover[index]) for index in range(len(base))
        ]

        if self.mouse_pos is not None:
            self.hand_hover = _hit_test(base, self.mouse_pos, lift_rects=self.hand_hit_rects)

        rects = []
        for index, rect in enumerate(base):
            lift = 0
            if index in self.selected_hand_keys:
                lift = selected_lift
            elif index == self.hand_hover:
                lift = hover_lift
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
    # 公共牌 / 真人装备 / 动画
    # ==================================================

    def public_rects(self, cards):
        return public_rect_list(cards, self.metrics)

    def public_index_at(self, position, cards):
        for index, rect in enumerate(self.public_rects(cards)):
            if rect.collidepoint(position):
                return index
        return None

    def player_equipment_rects(self):
        """四个装备槽；图标与文字在槽内分区，不再互相压盖。"""

        metrics = self.metrics
        status = metrics.player_status
        size = metrics.px(62)
        gap = metrics.px(12)
        # 214 → 232：左侧"武将缩略图 + 名字 + 座次"要放得下，
        # 否则昵称一长，座次就顶到第一个装备槽上。
        start_x = status.x + metrics.px(232)
        rects = {}
        for index, slot in enumerate(("weapon", "armor", "defensive_horse", "offensive_horse")):
            rects[slot] = pygame.Rect(
                start_x + index * (size + gap),
                status.y + metrics.px(6),
                size,
                status.height - metrics.px(12),
            )
        return rects

    def animation_rects(self):
        return self.metrics.animation_rects()
