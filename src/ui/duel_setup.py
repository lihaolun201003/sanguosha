"""1v1 测试的**开战前设置页**：分别指定双方武将、先手、操作方式与随机种子。

整屏数据驱动：包名、势力、版本标签、"能不能开局"全部从 ``GeneralRegistry``
推导，界面不认识任何具体武将——新增风 / 火 / 林 / 山 / 神将 / 一将成名 / SP
时只要写进注册表，这里会自动出现（含筛选按钮与"同名不同版本"的标签）。

与选将页（``general_select``）的分工：

* 选将页：从**本次候选**里挑一个，其余角色由系统分配（身份局 / 自由混战）；
* 本页：从**完整武将池**里分别挑两个人，不受三选一限制，也不许任何随机
  分配插手（对手是谁必须是我说了算）。
"""

import pygame

from src.game.modes.duel import (
    CONTROL_LABELS,
    ENEMY_SEAT,
    FIRST_LABELS,
    MY_SEAT,
    config_of,
)

from . import assets as assets_module
from . import layout, theme
from .text_input import TextField, accepts_digits_only
from .widgets import Button, draw_panel, ellipsize_text

KINGDOM_ORDER = ("wei", "shu", "wu", "qun", "god")
KINGDOM_COLORS = {
    "wei": (86, 116, 178),
    "shu": (176, 84, 72),
    "wu": (72, 152, 118),
    "qun": (140, 132, 108),
    # 神势力：金色（与神将卡的印玺 / 卡框一致）。
    "god": (198, 166, 74),
}
KINGDOM_LABELS = {"wei": "魏", "shu": "蜀", "wu": "吴", "qun": "群", "god": "神"}

DESIGN_WIDTH = 1600
DESIGN_HEIGHT = 900
MARGIN = 20
RIGHT_WIDTH = 440
GRID_COLUMNS = 6
GRID_ROWS = 2
GRID_GAP = 14
CARD_ASPECT = 420.0 / 572.0
CARD_TEXT_HEIGHT = 30
#: 武将名文字区在字体行高之外额外留的设计高度（上下各一半）。
CARD_TEXT_PAD = 10
#: 武将名字号的**屏幕像素下限**。
#:
#: 名字是卡框底部唯一的一行字，按 seat_name（24）原样缩放在小窗口里会小到
#: 看不清（1024×768 只有 15px、800×600 只有 12px）。低于下限就抬到下限；
#: 分辨率够高时仍按设计稿比例走——那里卡本来就宽，再放大只会把"同名不同
#: 版本"的后缀先截掉。
NAME_MIN_PIXELS = 18
#: 右栏技能说明 / 势力体力那两行小字的**屏幕像素下限**。
#:
#: micro（15）在 1024×768 上只有 10px，技能说明读起来费劲。抬到下限之后，
#: 面板里各行之间的间距不再写死（见 _draw_side_panel / _draw_detail），
#: 否则名字、meta、技能行会互相压上。
NOTE_MIN_PIXELS = 14
#: 每张武将牌的**屏幕像素**宽度下限。低于它宁可少排一列：1024×768 上排满
#: 6 列时每张只有 107px，"司马懿（标准版）"这类名字会被截到只剩两三个字。
MIN_CARD_PIXELS = 120
MIN_GRID_COLUMNS = 3
ALL_LABEL = "全部"

# 右栏垂直流：``(键, 设计高度, 与下一块的间距, 可伸展权重)``。
#
# 右栏所有纵坐标都从这里派生，不散落成一堆 px(86) / px(712)：屏幕比 16:9
# 设计稿高时（4:3 的 1024×768、5:4 的 1280×1024…），多出来的高度按权重分给
# 面板与段间距。原来是把设计稿的 y 直接缩放，多出来的高度全堆在右下角，
# 右栏挤成上半截、底下空一大片。
RIGHT_TOP = 86
RIGHT_BOTTOM_MARGIN = 24
RIGHT_FLOW = (
    ("side_my", 136, 6, 2.0),
    ("side_enemy", 136, 6, 2.0),
    ("detail", 186, 6, 2.0),
    ("settings", 142, 8, 0.0),
    ("random", 46, 6, 0.0),
    ("start", 58, 6, 0.0),
    ("back", 48, 0, 0.0),
)

# 设置面板内部（相对面板左上角的设计坐标）：左边是标签列，按钮与输入框
# 一律从标签列右侧开始排——标签贴在面板外面会直接压到武将网格上。
SETTINGS_LABEL_WIDTH = 112
SETTINGS_ROW_HEIGHT = 38
SETTINGS_ROW_TOPS = (12, 54, 96)


class DuelSetupScreen:
    """1v1 测试的设置页（只负责界面与交互，规则判定全在 ``DuelTestMode``）。"""

    def __init__(self, screen):
        self.screen = screen
        self.metrics = None
        #: 当前正在为哪一方选将（0 = 我方，1 = 对手）。
        self.active_seat = MY_SEAT
        self.page = 0
        self.kingdom_filter = ""
        self.pack_filter = ""
        self.hover_index = None
        self.notice = ""
        self.notice_error = False
        self.search_field = TextField(
            label="搜索", placeholder="武将名 / 技能名",
            max_length=16)
        self.seed_field = TextField(
            label="随机种子", placeholder="留空 = 每次随机",
            max_length=12, accepts=accepts_digits_only)
        self.start_button = Button(pygame.Rect(0, 0, 10, 10), "开始对战", kind="primary", font="large")
        self.back_button = Button(pygame.Rect(0, 0, 10, 10), "返回", kind="ghost", font="normal")
        self.random_buttons = {}
        self.swap_button = Button(pygame.Rect(0, 0, 10, 10), "交换双方", kind="secondary", font="small")
        self.prev_button = Button(pygame.Rect(0, 0, 10, 10), "上一页", kind="secondary", font="small")
        self.next_button = Button(pygame.Rect(0, 0, 10, 10), "下一页", kind="secondary", font="small")
        self.kingdom_buttons = {}
        self.pack_buttons = {}
        self.side_panels = {}
        self.card_rects = []
        self.card_hit_rects = []
        #: 每张牌**实际**画名字的位置、字形墨迹范围、名字所在的文字带与卡框
        #: （绘制时记录，供测试核对边界与居中）。
        self.card_name_rects = []
        self.card_name_inks = []
        self.card_name_areas = []
        self.card_frames = []
        self.cards = []
        self.matched = []
        #: 当前网格能排多少列（屏幕像素太窄时会少于 GRID_COLUMNS）× 每张牌的
        #: 文字区高度；分页大小与卡面绘制都读它们，不能各算各的。
        self.grid_columns = GRID_COLUMNS
        self.card_text_h = 0
        self.detail_rect = pygame.Rect(0, 0, 10, 10)
        self.settings_rect = pygame.Rect(0, 0, 10, 10)
        #: 设置面板内标签列的右边界（标签右对齐到这里，在面板里面）。
        self.settings_label_x = 0
        #: 每帧绘制的标签 ``(标题, 文字矩形)``（供测试核对标签确实在面板里）。
        self.settings_labels = []
        self.first_buttons = {}
        self.control_buttons = {}
        self.grid_rect = pygame.Rect(0, 0, 10, 10)
        self.filter_rect = pygame.Rect(0, 0, 10, 10)
        #: 技能详情面板的滚动位置（内容比区域高时用滚轮翻看，不截断）。
        self.detail_scroll = 0
        self._detail_key = None
        self._last_scene = ""
        self._mouse_pos = None
        self.sync_layout(None, None)

    # ==================================================
    # 布局
    # ==================================================

    def sync_layout(self, metrics=None, game=None):
        metrics = metrics or layout.LayoutMetrics(DESIGN_WIDTH, DESIGN_HEIGHT)
        self.metrics = metrics

        left_x = metrics.px(MARGIN)
        left_w = metrics.screen_w - metrics.px(RIGHT_WIDTH + MARGIN * 3)
        right_x = metrics.screen_w - metrics.px(RIGHT_WIDTH + MARGIN)
        right_w = metrics.px(RIGHT_WIDTH)

        # ---- 左半：搜索 / 筛选 / 武将网格 / 翻页 ----
        top = metrics.px(146)
        self.filter_rect = pygame.Rect(left_x, top, left_w, metrics.px(96))
        self.search_field.set_rect(
            pygame.Rect(left_x, top, metrics.px(340) + metrics.px(112), metrics.px(46)))

        kingdom_y = top + metrics.px(56)
        kingdom_bottom = self._layout_filter_row(
            self.kingdom_buttons, self._kingdom_options(game),
            pygame.Rect(left_x + metrics.px(112), kingdom_y,
                        left_w - metrics.px(112), metrics.px(40)))

        pack_y = kingdom_bottom + metrics.px(6)
        pack_bottom = self._layout_filter_row(
            self.pack_buttons, self._pack_options(game),
            pygame.Rect(left_x + metrics.px(112), pack_y,
                        left_w - metrics.px(112), metrics.px(40)))

        # 筛选按钮换行（扩展包变多时必然发生）会把这一块撑高；网格的起点
        # 必须跟着往下挪，否则筛选按钮会直接压在武将牌上面。
        self.filter_rect = pygame.Rect(
            left_x, top, left_w, max(metrics.px(96), pack_bottom - top + metrics.px(8)))

        grid_top = max(top + metrics.px(156), pack_bottom + metrics.px(16))
        grid_bottom = metrics.screen_h - metrics.px(150)
        self.grid_rect = pygame.Rect(left_x, grid_top, left_w, max(metrics.px(80), grid_bottom - grid_top))
        self._layout_cards(metrics)

        pager_y = self.grid_rect.bottom + metrics.px(10)
        pager_h = metrics.px(46)
        self.prev_button.rect = pygame.Rect(left_x, pager_y, metrics.px(150), pager_h)
        self.next_button.rect = pygame.Rect(
            left_x + left_w - metrics.px(150), pager_y, metrics.px(150), pager_h)

        # ---- 右栏：我方 / 对手 / 详情 / 设置 / 按钮 ----
        #
        # 位置全部来自 _right_rows：屏幕越高，右栏越舒展（设计稿高度下与
        # 原来的写死坐标逐像素一致）。
        rows = self._right_rows(metrics)
        for key, seat in (("side_my", MY_SEAT), ("side_enemy", ENEMY_SEAT)):
            y, height = rows[key]
            self.side_panels[seat] = pygame.Rect(right_x, y, right_w, height)
        y, height = rows["detail"]
        self.detail_rect = pygame.Rect(right_x, y, right_w, height)
        y, height = rows["settings"]
        self.settings_rect = pygame.Rect(right_x, y, right_w, height)

        # 设置面板内部分成"标签列 + 控件列"：标签留在面板里，按钮 / 输入框
        # 从标签列右侧开始。种子输入框的标签由 TextField 画在框内左侧，这里
        # 用同一列宽与同一间隙换算，四种标签才会左对齐成一列。
        label_w = metrics.px(SETTINGS_LABEL_WIDTH)
        row_h = metrics.px(SETTINGS_ROW_HEIGHT)
        column_gap = max(4, int(round(row_h * 0.22)))
        column_x = right_x + label_w + column_gap
        column_w = max(metrics.px(120), right_x + right_w - column_x)
        self.settings_label_x = right_x + label_w
        first_y, control_y, seed_y = (
            y + metrics.px(offset) for offset in SETTINGS_ROW_TOPS)

        self._layout_choice_row(
            self.first_buttons, FIRST_LABELS,
            pygame.Rect(column_x, first_y, column_w, row_h))
        self._layout_choice_row(
            self.control_buttons, CONTROL_LABELS,
            pygame.Rect(column_x, control_y, column_w, row_h))
        self.seed_field.set_rect(
            pygame.Rect(right_x, seed_y, right_w, row_h),
            label_width=label_w)

        y, height = rows["random"]
        button_w = (right_w - metrics.px(16)) // 3
        for index, seat in enumerate((MY_SEAT, ENEMY_SEAT)):
            self.random_buttons[seat] = Button(
                pygame.Rect(right_x + index * (button_w + metrics.px(8)), y,
                            button_w, height),
                ("我方随机" if seat == MY_SEAT else "对手随机"),
                kind="secondary", font="small")
        self.swap_button.rect = pygame.Rect(
            right_x + 2 * (button_w + metrics.px(8)), y, button_w, height)

        y, height = rows["start"]
        self.start_button.rect = pygame.Rect(right_x, y, right_w, height)
        y, height = rows["back"]
        self.back_button.rect = pygame.Rect(right_x, y, right_w, height)
        return self

    def _right_rows(self, metrics):
        """右栏各块的 ``(顶边 y, 高度)``（屏幕坐标）。

        设计稿（1600×900）下与原来的写死坐标相同；屏幕比设计稿高时，多出来
        的高度按 RIGHT_FLOW 的权重分给可伸展的面板与各段间距——右栏因此始终
        贴着屏幕底部，不会把控件全挤在上半截。**只有这里**决定右栏的纵向
        位置，绘制与命中测试取的都是同一份结果。
        """

        top = metrics.px(RIGHT_TOP)
        bottom = metrics.screen_h - metrics.px(RIGHT_BOTTOM_MARGIN)
        fixed = sum(item[1] for item in RIGHT_FLOW)
        gaps = sum(item[2] for item in RIGHT_FLOW)
        extra = max(0, bottom - top - metrics.px(fixed + gaps))
        weight = sum(item[3] for item in RIGHT_FLOW) + len(RIGHT_FLOW) - 1
        unit = extra / float(weight or 1)

        rows = {}
        cursor = top
        for index, (key, height, gap, stretch) in enumerate(RIGHT_FLOW):
            block = max(1, metrics.px(height) + int(round(unit * stretch)))
            rows[key] = (cursor, block)
            cursor += block
            if index < len(RIGHT_FLOW) - 1:
                cursor += max(0, metrics.px(gap) + int(round(unit)))
        # 最后一块贴住屏幕底边：每块各自取整会有一两像素的累积误差，不能让
        # 它落在右栏底部（那里紧挨着屏幕底部的状态行）。
        last = RIGHT_FLOW[-1][0]
        y, height = rows[last]
        rows[last] = (y, max(1, bottom - y))
        return rows

    def _layout_filter_row(self, buttons, labels, rect):
        """筛选按钮：按文字宽度排一行，放不下就换行（包变多也不会挤爆）。

        返回这一行的**底边 y**，调用方据此把下面的网格挪开——换行之后
        按钮块会变高，不挪就会压在武将牌上。
        """

        buttons.clear()
        font = self.metrics.fonts.get("small")
        x, y = rect.x, rect.y
        gap = self.metrics.px(8)
        max_x = rect.right
        bottom = rect.bottom
        for value, label in labels:
            width = font.size(label)[0] + self.metrics.px(26)
            if x + width > max_x and x > rect.x:
                x = rect.x
                y += rect.height + self.metrics.px(6)
            buttons[value] = Button(
                pygame.Rect(x, y, max(self.metrics.px(56), width), rect.height),
                label, kind="secondary", font="small")
            x += buttons[value].rect.width + gap
            bottom = max(bottom, buttons[value].rect.bottom)
        return bottom

    def _layout_choice_row(self, buttons, options, rect):
        buttons.clear()
        count = max(1, len(options))
        gap = self.metrics.px(8)
        width = (rect.width - gap * (count - 1)) // count
        for index, (value, label) in enumerate(options):
            buttons[value] = Button(
                pygame.Rect(rect.x + index * (width + gap), rect.y, width, rect.height),
                label, kind="secondary", font="small")
        return buttons

    def _name_font(self, metrics):
        """武将名用的字体：小窗口里抬到可读下限，其余按设计稿比例。"""

        size = max(NAME_MIN_PIXELS,
                   int(round(theme.FONT_SIZES["seat_name"] * metrics.scale)))
        return theme.load_font(size)

    def _note_font(self, metrics):
        """右栏技能说明 / 势力体力用的字体：同样是可读下限。"""

        size = max(NOTE_MIN_PIXELS,
                   int(round(theme.FONT_SIZES["micro"] * metrics.scale)))
        return theme.load_font(size)

    def _grid_columns(self, metrics):
        """网格列数：设计稿的 6 列，但**屏幕像素**太窄时宁可少排一列。

        列数是分页大小的来源，所以它只看网格区域的宽度（不看这一页有几张
        牌），翻页 / 筛选时不会忽多变列。
        """

        gap = metrics.px(GRID_GAP)
        fits = (self.grid_rect.width + gap) // max(1, MIN_CARD_PIXELS + gap)
        return max(MIN_GRID_COLUMNS, min(GRID_COLUMNS, int(fits)))

    def _layout_cards(self, metrics):
        count = max(1, len(self.cards))
        self.grid_columns = self._grid_columns(metrics)
        columns = min(self.grid_columns, count) or 1
        rows = min(GRID_ROWS, max(1, (count + columns - 1) // columns))
        gap = metrics.px(GRID_GAP)
        width_limit = max(metrics.px(40), (self.grid_rect.width - gap * (columns - 1)) // columns)
        height_limit = max(metrics.px(60), (self.grid_rect.height - gap * (rows - 1)) // rows)
        # 文字区按**实际字体行高**算：写死 30 设计像素时，字号一缩放（例如
        # 1024×768 下 24 → 18）名字就会顶到、甚至越过卡框底边。
        name_font = self._name_font(metrics)
        text_h = max(metrics.px(CARD_TEXT_HEIGHT),
                     name_font.get_linesize() + metrics.px(CARD_TEXT_PAD))
        if text_h > height_limit - metrics.px(40):
            # 卡与文字都在很矮的网格里时，先保证名字放得下，再让卡面变小。
            text_h = max(name_font.get_linesize(),
                         min(text_h, height_limit - metrics.px(40)))
        self.card_text_h = text_h
        width = min(width_limit, int(max(1, height_limit - text_h) * CARD_ASPECT))
        width = max(min(metrics.px(52), width_limit), width)
        art_h = int(round(width / CARD_ASPECT))
        if art_h + text_h > height_limit:
            art_h = max(1, height_limit - text_h)
        height = art_h + text_h

        self.card_rects = []
        self.card_hit_rects = []
        lift = metrics.px(8)
        grid_height = rows * height + max(0, rows - 1) * gap
        # 网格在自己的区域里垂直居中：只有一两行时贴着底边会显得像被截断。
        grid_top = self.grid_rect.y + max(0, (self.grid_rect.height - grid_height) // 2)
        start_x = self.grid_rect.x + max(
            0, (self.grid_rect.width - (columns * width + (columns - 1) * gap)) // 2)
        for index in range(len(self.cards)):
            column, row = index % columns, index // columns
            rect = pygame.Rect(
                start_x + column * (width + gap),
                grid_top + row * (height + gap),
                width, height)
            self.card_rects.append(rect)
            self.card_hit_rects.append(rect.union(rect.move(0, -lift)))

    # ==================================================
    # 过滤 / 分页
    # ==================================================

    def _all_generals(self, game):
        if game is None:
            return []
        return list(game.generals.list_generals())

    def _kingdom_options(self, game):
        present = []
        for general in self._all_generals(game):
            if general.kingdom not in present:
                present.append(general.kingdom)
        ordered = [k for k in KINGDOM_ORDER if k in present]
        ordered += [k for k in present if k not in KINGDOM_ORDER]
        return [("", ALL_LABEL)] + [
            (k, KINGDOM_LABELS.get(k, k)) for k in ordered]

    def _pack_options(self, game):
        if game is None:
            return [("", ALL_LABEL)]
        return [("", ALL_LABEL)] + [
            (pack, pack) for pack in game.generals.packs()]

    def matches(self, game, general):
        """一名武将是否通过当前的搜索与筛选（搜索覆盖名字 / 版本 / 技能名）。"""

        if self.kingdom_filter and general.kingdom != self.kingdom_filter:
            return False
        if self.pack_filter and general.pack != self.pack_filter:
            return False
        keyword = self.search_field.value()
        if not keyword:
            return True
        haystack = [general.name, general.version, general.pack, general.id]
        for skill_id in general.skill_ids:
            definition = game.skill_registry.get(skill_id)
            if definition is not None:
                haystack.append(definition.name)
                haystack.append(skill_id)
        return any(keyword in str(item) for item in haystack if item)

    def filtered(self, game):
        return [general for general in self._all_generals(game) if self.matches(game, general)]

    def page_count(self):
        return max(1, (len(self.matched) + self._page_size() - 1) // self._page_size())

    def _page_size(self):
        return max(1, self.grid_columns) * GRID_ROWS

    def sync_cards(self, game):
        """重算当前页的武将列表与网格（筛选 / 搜索 / 翻页 / 分辨率变化后调用）。

        ``matched`` 是通过筛选的全部武将，``cards`` 只是当前这一页——分页信息
        与总数必须看前者，否则每页都只有一页。
        """

        self.matched = self.filtered(game)
        self.page = max(0, min(self.page, self.page_count() - 1))
        start = self.page * self._page_size()
        self.cards = self.matched[start:start + self._page_size()]
        self._layout_cards(self.metrics or layout.LayoutMetrics(DESIGN_WIDTH, DESIGN_HEIGHT))
        return self.cards

    def page_label(self, game):
        return "第 %d / %d 页　共 %d 名" % (
            self.page + 1, self.page_count(), len(self.matched))

    def change_page(self, delta, game):
        pages = self.page_count()
        target = max(0, min(pages - 1, self.page + delta))
        if target == self.page:
            return False
        self.page = target
        self.sync_cards(game)
        return True

    # ==================================================
    # 交互
    # ==================================================

    def handle_event(self, event, game):
        """主循环入口；返回 "start" / "back" / "handled" / None。

        返回 None 表示这个事件与设置页无关（位置相关的窗口事件继续走主循环）。
        """

        if event.type == pygame.MOUSEBUTTONDOWN and getattr(event, "button", 1) == 1:
            # 先让输入框处理（点框内 = 聚焦，点别处 = 失焦）。
            consumed = self.search_field.handle_event(event)
            self.seed_field.handle_event(event)
            action = self.handle_click(event.pos, game)
            return action or "handled"

        if event.type == pygame.MOUSEWHEEL:
            position = getattr(event, "pos", None) or self.mouse_pos()
            if self.detail_rect.collidepoint(position):
                self.scroll_detail(-1 if event.y > 0 else 1)
                return "handled"
            if self._page_area_hit(position):
                self.change_page(-1 if event.y > 0 else 1, game)
            return "handled"

        if event.type in (pygame.KEYDOWN, pygame.TEXTINPUT):
            return self.handle_key_event(event, game)

        return None

    def handle_key_event(self, event, game):
        """键盘：输入框优先，聚焦期间其余按键一律吞掉（不改游戏节奏/全屏）。"""

        field = self.focused_field()
        if event.type == pygame.TEXTINPUT:
            if field is None:
                return None
            field.handle_event(event)
            self.on_filter_changed(game)
            return "handled"

        if event.key == pygame.K_F11:
            # 全屏切换交给主循环；正在输入搜索词时不抢它。
            return None if field is None else "handled"
        if event.key == pygame.K_ESCAPE:
            if field is not None:
                field.blur()
                return "handled"
            return None
        if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
            if field is self.search_field:
                self.search_field.blur()
                self.on_filter_changed(game)
                return "handled"
            if field is None:
                return self.handle_click(self.start_button.rect.center, game) or "handled"
            field.blur()
            return "handled"
        if event.key in (pygame.K_PAGEUP, pygame.K_LEFT):
            if field is None and self.change_page(-1, game):
                return "handled"
            return None if field is None else "handled"
        if event.key in (pygame.K_PAGEDOWN, pygame.K_RIGHT):
            if field is None and self.change_page(1, game):
                return "handled"
            return None if field is None else "handled"
        if event.key == pygame.K_BACKSPACE and field is not None:
            field.handle_event(event)
            self.on_filter_changed(game)
            return "handled"
        # 正在输入时，其余按键全部归输入框（不能顺手改对局速度一类的快捷键）。
        return "handled" if field is not None else None

    def focused_field(self):
        for field in (self.search_field, self.seed_field):
            if field.focused:
                return field
        return None

    def on_filter_changed(self, game):
        self.page = 0
        self.sync_cards(game)
        return self.cards

    def _page_area_hit(self, position):
        return self.grid_rect.collidepoint(position)

    def mouse_pos(self):
        """当前鼠标位置（滚轮事件里没有坐标，用最近一帧画出来的位置）。"""

        return self._mouse_pos or pygame.mouse.get_pos()

    def general_at(self, position):
        for index, rect in enumerate(self.card_hit_rects):
            if rect.collidepoint(position):
                return self.cards[index] if index < len(self.cards) else None
        return None

    def handle_click(self, position, game):
        """返回 "start" / "back" / None（None 表示"设置页吃掉了这一下"）。"""

        config = config_of(game)

        card = self.general_at(position)
        if card is not None:
            self.choose_general(game, card)
            return None

        for seat, rect in self.side_panels.items():
            if rect.collidepoint(position):
                self.set_active_seat(seat)
                return None

        if self.prev_button.contains(position):
            self.change_page(-1, game)
            return None
        if self.next_button.contains(position):
            self.change_page(1, game)
            return None

        for value, button in self.kingdom_buttons.items():
            if button.contains(position):
                self.kingdom_filter = value
                self.on_filter_changed(game)
                return None
        for value, button in self.pack_buttons.items():
            if button.contains(position):
                self.pack_filter = value
                self.on_filter_changed(game)
                return None

        for value, button in self.first_buttons.items():
            if button.contains(position):
                config.first = value
                self.set_notice("")
                return None
        for value, button in self.control_buttons.items():
            if button.contains(position):
                config.control = value
                self.set_notice("")
                return None

        for seat, button in self.random_buttons.items():
            if button.contains(position):
                self.random_for(game, seat)
                return None

        if self.swap_button.contains(position):
            config.swap()
            self.set_notice("已交换双方武将。")
            return None

        if self.start_button.contains(position):
            return "start"

        if self.back_button.contains(position):
            return "back"

        return None

    # ==================================================
    # 状态变更
    # ==================================================

    def reset(self):
        self.active_seat = MY_SEAT
        self.page = 0
        self.kingdom_filter = ""
        self.pack_filter = ""
        self.notice = ""
        self.notice_error = False
        self.search_field.set_text("")
        self.search_field.blur()
        self.seed_field.blur()
        return self

    def set_active_seat(self, seat):
        self.active_seat = int(seat)
        return self.active_seat

    def set_notice(self, message, *, error=False):
        self.notice = str(message or "")
        self.notice_error = bool(error)
        return self.notice

    def choose_general(self, game, general):
        """给当前选将方指定一名武将。

        **界面可浏览**与**对局可使用**是两件事：未实现、或只允许在别的
        模式里用的条目照样看得见（能读技能说明），但点选会被明确拒绝并
        写出原因，不会被悄悄换成别的武将。
        """

        ok, reason = general.availability_for(getattr(game, "mode_id", None))
        if not ok:
            self.set_notice(general.display_name + " 不能开局：" + reason, error=True)
            return False
        config = config_of(game)
        config.set_general(self.active_seat, general.id)
        self.set_notice("已为「" + self._seat_label(self.active_seat) + "」选择 "
                        + general.display_name + "。")
        return True

    def random_for(self, game, seat):
        """单方随机：只从**能进随机池**的武将里抽。

        「测试用配置」（同一编号的多个形态、跨势力变体之类）不在随机池里
        ——它们要么由玩家在网格里指名，要么根本不该出现在这一局。
        """

        mode_id = getattr(game, "mode_id", None)
        pool = [general for general in self._all_generals(game)
                if general.random_eligible_for(mode_id)]
        if not pool:
            self.set_notice("没有可随机分配的武将。", error=True)
            return False
        general = game.rng.choice(pool)
        config = config_of(game)
        config.set_general(seat, general.id)
        self.set_active_seat(seat)
        self.set_notice("「" + self._seat_label(seat) + "」随机到 " + general.display_name
                        + "。（测试用配置不在随机池里，可点击指名）")
        return True

    def on_enter(self, game):
        """进入设置页：把配置写回输入框、重算列表。

        "换将"与"返回菜单后再进来"都不会重建这个界面，所以进入时必须自己
        同步一次——否则种子输入框会留着上一次的文本（或者干脆是空的）。
        """

        self.sync_fields(game)
        self.set_notice("")
        self.sync_cards(game)
        return self

    def sync_fields(self, game):
        """把 game 里的配置写回输入框（换将回来后输入框不能是空的）。"""

        config = config_of(game)
        if self.seed_field.text != config.seed_text:
            self.seed_field.set_text(config.seed_text)
        return self

    def apply_seed(self, game):
        """把种子输入框的内容写进配置（开战前调用）。"""

        config = config_of(game)
        config.seed = self.seed_field.value()
        return config.seed

    def start_battle(self, game):
        """校验 → 开局；返回 (ok, message)。"""

        self.apply_seed(game)
        starter = getattr(game.mode, "start_battle", None)
        if callable(starter):
            ok, message = starter()
        else:                                      # pragma: no cover - 兜底
            ok, message = True, ""
            game.start_local_battle(1)
        if not ok:
            self.set_notice(message, error=True)
        else:
            self.set_notice("")
        return ok, message

    @staticmethod
    def _seat_label(seat):
        return "我方" if int(seat) == MY_SEAT else "对手"

    def status_line(self, game):
        """按钮上方那行说明：现在能不能开局、为什么。"""

        config = config_of(game)
        ok, message = game.mode.validate_setup()
        if not ok:
            return message, True
        mine = game.generals.get(config.my_general)
        theirs = game.generals.get(config.enemy_general)
        text = "我方 %s 　vs　 对手 %s" % (mine.display_name, theirs.display_name)
        if message:
            return text + "　" + message, True
        return text, False

    # ==================================================
    # 绘制
    # ==================================================

    def draw(self, game, metrics=None):
        metrics = metrics or self.metrics
        # 刚进入设置页（含"换将"这条没有重建界面的路径）：先把配置写回输入框。
        scene = str(getattr(game, "scene", ""))
        if scene == "duel_setup" and self._last_scene != scene:
            self.on_enter(game)
        self._last_scene = scene
        if metrics is None or self.metrics is not metrics or not self.card_rects:
            self.sync_layout(metrics, game)
            self.sync_cards(game)
            metrics = self.metrics

        fonts = metrics.fonts
        mouse = pygame.mouse.get_pos()
        self._mouse_pos = mouse
        self._update_hover(mouse)

        self.screen.blit(theme.table_surface(metrics.screen_w, metrics.screen_h), (0, 0))

        title_font = fonts.get("title")
        title = title_font.render("1v1 测试 · 开战前设置", True, theme.GOLD_BRIGHT)
        self.screen.blit(title, (metrics.px(MARGIN), metrics.px(22)))

        hint_font = fonts.get("small")
        hint_text = (
            "点击我方 / 对手面板切换选将目标，再点武将牌指定；双方都选好才能开战。"
            "（当前选将目标：" + self._seat_label(self.active_seat) + "）")
        hint = hint_font.render(hint_text, True, theme.TEXT_DIM)
        self.screen.blit(hint, (metrics.px(MARGIN), metrics.px(104)))

        self._draw_filters(game, mouse)
        mode_id = getattr(game, "mode_id", None)
        self.card_name_rects = []
        self.card_name_inks = []
        self.card_name_areas = []
        self.card_frames = []
        self.settings_labels = []
        for index, (general, rect) in enumerate(zip(self.cards, self.card_rects)):
            self._draw_general_card(
                game, general, rect, metrics,
                chosen=self._is_chosen(game, general),
                hovered=index == self.hover_index,
                unavailable=not general.availability_for(mode_id)[0])
        self._draw_pager(game, fonts, mouse)

        for seat in (MY_SEAT, ENEMY_SEAT):
            self._draw_side_panel(game, seat, metrics)
        self._draw_detail(game, metrics)
        self._draw_settings(game, metrics, mouse)

        for seat, button in self.random_buttons.items():
            button.enabled = True
            button.draw(self.screen, fonts, mouse)
        self.swap_button.draw(self.screen, fonts, mouse)

        self.start_button.enabled = game.mode.validate_setup()[0]
        self.start_button.draw(self.screen, fonts, mouse)
        self.back_button.draw(self.screen, fonts, mouse)

        text, error = self.status_line(game)
        if self.notice:
            text, error = self.notice, self.notice_error
        color = theme.DANGER if error else theme.TEXT_DIM
        rendered = hint_font.render(
            ellipsize_text(text, hint_font, metrics.screen_w - metrics.px(MARGIN * 2)),
            True, color)
        self.screen.blit(rendered, rendered.get_rect(
            midbottom=(metrics.screen_w // 2, metrics.screen_h - metrics.px(8))))

    def _update_hover(self, position):
        self.hover_index = None
        for index, rect in enumerate(self.card_hit_rects):
            if rect.collidepoint(position) and index < len(self.cards):
                self.hover_index = index
                break
        # 详情面板换了主人就把滚动位置归零，否则翻到下一名武将时
        # 会停在上一条说明的中间，看起来像内容缺了一段。
        return self.hover_index

    def _is_chosen(self, game, general):
        config = config_of(game)
        return general.id in (config.my_general, config.enemy_general)

    def _draw_filters(self, game, mouse):
        fonts = self.metrics.fonts
        self.search_field.draw(self.screen, fonts, self.metrics)
        label_font = fonts.get("small")
        for title, buttons in (("势力", self.kingdom_buttons), ("扩展包", self.pack_buttons)):
            button = next(iter(buttons.values()), None)
            if button is None:
                continue
            text = label_font.render(title, True, theme.TEXT_DIM)
            self.screen.blit(text, text.get_rect(
                midright=(button.rect.x - self.metrics.px(10), button.rect.centery)))
        for buttons, current in ((self.kingdom_buttons, self.kingdom_filter),
                                 (self.pack_buttons, self.pack_filter)):
            for value, button in buttons.items():
                button.kind = "primary" if value == current else "secondary"
                button.draw(self.screen, fonts, mouse)

    def _draw_pager(self, game, fonts, mouse):
        self.prev_button.enabled = self.page > 0
        self.next_button.enabled = self.page + 1 < self.page_count()
        self.prev_button.draw(self.screen, fonts, mouse)
        self.next_button.draw(self.screen, fonts, mouse)
        label = fonts.get("small").render(self.page_label(game), True, theme.TEXT)
        self.screen.blit(label, label.get_rect(
            center=((self.prev_button.rect.right + self.next_button.rect.x) // 2,
                    self.prev_button.rect.centery)))

    def _draw_general_card(self, game, general, rect, metrics, *,
                           chosen, hovered, unavailable):
        fonts = metrics.fonts
        kingdom_color = KINGDOM_COLORS.get(general.kingdom, theme.GOLD_DIM)
        lift = metrics.px(8) if hovered else 0
        card = rect.move(0, -lift)

        fill = theme.PANEL_ALT if chosen else theme.PANEL
        if chosen:
            border, width = theme.TARGET_YELLOW, theme.BORDER_THICK
        elif hovered:
            border, width = theme.GOLD_BRIGHT, theme.BORDER
        elif unavailable:
            border, width = theme.DISABLED_TEXT, theme.BORDER_THIN
        else:
            border, width = theme.GOLD_DIM, theme.BORDER
        draw_panel(self.screen, card, fill=fill, border=border,
                   border_width=width, radius=metrics.px(12), shadow=False)

        stripe = pygame.Rect(card.x, card.y, metrics.px(6), card.height)
        pygame.draw.rect(
            self.screen, theme.DISABLED_TEXT if unavailable else kingdom_color, stripe,
            border_top_left_radius=metrics.px(12),
            border_bottom_left_radius=metrics.px(12))

        pad = metrics.px(8)
        text_h = self.card_text_h
        art_rect = pygame.Rect(
            card.x + pad, card.y + pad,
            max(1, card.width - pad * 2),
            max(1, card.height - pad - text_h))
        content = self._draw_art(general, art_rect, metrics, kingdom_color, unavailable)

        name_font = self._name_font(metrics)
        label = self._display_label(game, general)
        rendered = name_font.render(ellipsize_text(label, name_font, card.width - pad * 2),
                                    True, theme.TEXT_DIM if unavailable else theme.TEXT)
        # 名字在卡框底部的文字带里**上下居中**：既不能像原来那样贴顶画
        # （行高一超过文字带就压到卡框底边），也不能把带上下留白的整个
        # surface 居中——中文字形的墨迹在行高里本来就偏下，那样看着还是偏。
        # 所以按**墨迹包围盒**对齐，文字带的顶边取卡面实际画到哪。
        name_top = content.bottom if content is not None else art_rect.bottom
        name_area = pygame.Rect(
            card.x + pad, name_top,
            max(1, card.width - pad * 2),
            max(1, card.bottom - name_top))
        ink = rendered.get_bounding_rect()
        name_rect = rendered.get_rect()
        name_rect.centerx = name_area.centerx
        name_rect.y = name_area.centery - ink.centery
        self.screen.blit(rendered, name_rect)
        self.card_name_rects.append(name_rect)
        self.card_name_inks.append(pygame.Rect(
            name_rect.x + ink.x, name_rect.y + ink.y, ink.width, ink.height))
        self.card_name_areas.append(pygame.Rect(name_area))
        self.card_frames.append(pygame.Rect(card))

        if unavailable:
            self._draw_tag(card, "不可开局", theme.DANGER, metrics, top=True)
        elif self._chosen_seat(game, general) is not None:
            seat = self._chosen_seat(game, general)
            self._draw_tag(card, self._seat_label(seat), theme.TARGET_YELLOW, metrics, top=True)

    def _display_label(self, game, general):
        """同名不同版本必须带标签（标签由注册表推导，界面不写死名单）。"""

        labels = game.generals.labelled_names()
        return labels.get(general.id, general.display_name)

    def _chosen_seat(self, game, general):
        """这个武将被指定给了哪一方（没被选中时返回 None）。

        比较的是"配置里存的 id"与"这张牌的 id"，不涉及任何具体武将。
        """

        config = config_of(game)
        for seat, chosen in ((MY_SEAT, config.my_general),
                             (ENEMY_SEAT, config.enemy_general)):
            if chosen == general.id:
                return seat
        return None

    def _draw_tag(self, card, text, color, metrics, *, top=False):
        font = metrics.fonts.get("micro")
        label = font.render(text, True, theme.INK)
        badge = pygame.Rect(0, 0, label.get_width() + metrics.px(12),
                            label.get_height() + metrics.px(6))
        if top:
            badge.topleft = (card.x + metrics.px(10), card.y + metrics.px(8))
        else:
            badge.bottomright = (card.right - metrics.px(8), card.bottom - metrics.px(6))
        pygame.draw.rect(self.screen, color, badge, border_radius=metrics.px(6))
        self.screen.blit(label, label.get_rect(center=badge.center))

    def _draw_art(self, general, art_rect, metrics, kingdom_color, unavailable):
        """画卡面；返回**实际画出来**的内容矩形（没有内容时返回 None）。

        卡面按比例缩放（fit_contain）后不会填满 art_rect，底部那截留白要算进
        名字的位置——名字以"卡面底边 → 卡框底边"居中，视觉上才是正的。
        """

        registry = assets_module.get_registry()
        asset_id = assets_module.general_asset_id(general.id)
        source = registry.surface(asset_id)
        scaled = None
        target = None
        if source is not None:
            target = assets_module.fit_contain(art_rect, source.get_size(), align="midtop")
            scaled = registry.scaled(asset_id, target.size)
        if scaled is not None:
            pygame.draw.rect(self.screen, theme.PANEL_SUNKEN,
                             target.inflate(metrics.px(4), metrics.px(4)),
                             border_radius=metrics.px(6))
            pygame.draw.rect(self.screen, theme.GOLD_DIM, target.inflate(metrics.px(4), metrics.px(4)),
                             1, border_radius=metrics.px(6))
            self.screen.blit(scaled, target.topleft)
            if unavailable:
                veil = pygame.Surface(target.size, pygame.SRCALPHA)
                veil.fill((18, 22, 28, 170))
                self.screen.blit(veil, target.topleft)
            return target
        # 没有卡面素材时的兜底：姓氏首字 + 势力色圆环。
        fonts = metrics.fonts
        size = max(metrics.px(28), min(art_rect.width, art_rect.height) - metrics.px(16))
        center = (art_rect.centerx, art_rect.y + size // 2 + metrics.px(8))
        pygame.draw.circle(self.screen, (30, 42, 54), center, size // 2)
        pygame.draw.circle(self.screen, kingdom_color, center, size // 2, max(2, metrics.px(2)))
        initial = fonts.get("large").render(general.name[:1], True, theme.TEXT)
        self.screen.blit(initial, initial.get_rect(center=center))
        return None

    # ==================================================
    # 右栏
    # ==================================================

    def _draw_side_panel(self, game, seat, metrics):
        """我方 / 对手：一张紧凑的结果卡（卡面 + 名字 + 势力体力 + 技能名）。"""

        rect = self.side_panels[seat]
        config = config_of(game)
        active = self.active_seat == seat
        border = theme.TARGET_YELLOW if active else theme.GOLD_DIM
        draw_panel(self.screen, rect, fill=theme.PANEL_DEEP, border=border,
                   border_width=theme.BORDER_THICK if active else theme.BORDER,
                   radius=metrics.px(12), shadow=False)
        fonts = metrics.fonts
        pad = metrics.px(12)
        tag = fonts.get("small").render(
            self._seat_label(seat) + ("（正在选将）" if active else ""), True,
            theme.TARGET_YELLOW if active else theme.TEXT_DIM)
        self.screen.blit(tag, (rect.x + pad, rect.y + metrics.px(8)))

        general = game.generals.get(config.general_id_of(seat))
        art = pygame.Rect(rect.x + pad, rect.y + metrics.px(34),
                          metrics.px(62), metrics.px(84))
        text_x = art.right + metrics.px(12)
        width = rect.right - pad - text_x
        if general is None:
            empty = fonts.get("normal").render("未选择", True, theme.TEXT_MUTED)
            self.screen.blit(empty, empty.get_rect(midleft=(text_x, art.centery)))
            return
        kingdom_color = KINGDOM_COLORS.get(general.kingdom, theme.GOLD_DIM)
        registry = assets_module.get_registry()
        asset_id = assets_module.general_asset_id(general.id)
        source = registry.surface(asset_id)
        if source is not None:
            target = assets_module.fit_contain(art, source.get_size(), align="midleft")
            scaled = registry.scaled(asset_id, target.size)
            if scaled is not None:
                self.screen.blit(scaled, target.topleft)
                pygame.draw.rect(self.screen, theme.GOLD_DIM, target, 1)
        else:
            pygame.draw.rect(self.screen, theme.PANEL_SUNKEN, art, border_radius=metrics.px(6))
            initial = fonts.get("large").render(general.name[:1], True, kingdom_color)
            self.screen.blit(initial, initial.get_rect(center=art.center))

        name_font = fonts.get("normal")
        meta_font = self._note_font(metrics)
        # 名字 / 势力体力 / 技能行按**实际字体行高**往下排：字号一抬高（小窗口
        # 有可读下限），写死的 px(34) / px(66) / px(86) 就会让三行叠在一起。
        name_top = rect.y + metrics.px(34)
        meta_top = name_top + name_font.get_linesize() + metrics.px(2)
        name = name_font.render(
            ellipsize_text(self._display_label(game, general), name_font, width),
            True, theme.GOLD_BRIGHT)
        self.screen.blit(name, (text_x, name_top))
        meta = "%s · %s · 体力 %d/%d · %s" % (
            general.kingdom_name,
            "男" if general.gender == "male" else "女",
            general.max_hp, general.max_hp, general.pack)
        self.screen.blit(meta_font.render(
            ellipsize_text(meta, meta_font, width), True, theme.TEXT_DIM),
            (text_x, meta_top))

        line_font = meta_font
        top = meta_top + meta_font.get_linesize() + metrics.px(4)
        limit = rect.bottom - metrics.px(6)
        for skill_id in general.skill_ids:
            definition = game.skill_registry.get(skill_id)
            if definition is None or top + line_font.get_linesize() > limit:
                continue
            label = "【%s】%s" % (definition.name, definition.description)
            self.screen.blit(line_font.render(
                ellipsize_text(label, line_font, width), True, theme.TEXT),
                (text_x, top))
            top += line_font.get_linesize()
        note = game.mode.general_note(general) if hasattr(game.mode, "general_note") else ""
        if note and top + line_font.get_linesize() <= limit:
            self.screen.blit(line_font.render(
                ellipsize_text(note, line_font, width), True, theme.TARGET_YELLOW),
                (text_x, top))

    def _detail_general(self, game):
        """详情面板显示谁：鼠标悬停优先，其次当前选将方的选择，最后列表第一名。"""

        if self.hover_index is not None and self.hover_index < len(self.cards):
            return self.cards[self.hover_index]
        config = config_of(game)
        chosen = game.generals.get(config.general_id_of(self.active_seat))
        if chosen is not None:
            return chosen
        return self.cards[0] if self.cards else None

    def _draw_detail(self, game, metrics):
        """技能详情：内容比区域高时**滚动查看**，绝不直接截断。

        扩展包的技能说明动辄四五条、每条两三行，塞不进就用鼠标滚轮翻。
        """
        rect = self.detail_rect
        draw_panel(self.screen, rect, fill=theme.PANEL, border=theme.GOLD_DIM,
                   border_width=theme.BORDER, radius=metrics.px(12), shadow=False)
        fonts = metrics.fonts
        pad = metrics.px(12)
        general = self._detail_general(game)
        if general is None:
            empty = fonts.get("small").render("没有符合筛选条件的武将。", True, theme.TEXT_DIM)
            self.screen.blit(empty, empty.get_rect(center=rect.center))
            return

        name_font = fonts.get("normal")
        meta_font = self._note_font(metrics)
        # 标题 / 势力体力 / 正文按实际字体行高往下排（字号在小窗口里被抬高，
        # 写死的 px(34) / px(54) 会重叠）。
        head_top = rect.y + metrics.px(8)
        meta_top = head_top + name_font.get_linesize() + metrics.px(2)
        head = name_font.render(self._display_label(game, general), True, theme.GOLD_BRIGHT)
        self.screen.blit(head, (rect.x + pad, head_top))
        tags = [general.kingdom_name, general.pack]
        if general.version:
            tags.append(general.version)
        meta = " · ".join(tags) + " · 体力 %d · %s" % (
            general.max_hp, "男" if general.gender == "male" else "女")
        self.screen.blit(meta_font.render(
            ellipsize_text(meta, meta_font, rect.width - pad * 2), True, theme.TEXT_DIM),
            (rect.x + pad, meta_top))

        line_font = meta_font
        body_top = meta_top + meta_font.get_linesize() + metrics.px(6)
        body_width = rect.width - pad * 2
        entries = []
        for skill_id in general.skill_ids:
            definition = game.skill_registry.get(skill_id)
            if definition is None:
                body = "【" + skill_id + "】未注册的技能定义。"
            else:
                body = "【%s】%s" % (definition.name, definition.description)
            entries.extend((line, theme.TEXT) for line in _wrap_lines(body, line_font, body_width))

        ok, reason = general.availability_for(getattr(game, "mode_id", None))
        note = game.mode.general_note(general) if hasattr(game.mode, "general_note") else ""
        for text, color in ((note, theme.TARGET_YELLOW), ("" if ok else reason, theme.DANGER)):
            if not text:
                continue
            entries.extend((line, color) for line in _wrap_lines(text, line_font, body_width))
        if general.test_only and ok:
            entries.append(("测试用配置：只能在本模式显式选择，不参与随机分配。", theme.TARGET_YELLOW))

        line_h = line_font.get_linesize()
        top = body_top
        limit = rect.bottom - metrics.px(8)
        visible = max(1, (limit - top) // line_h)
        max_scroll = max(0, len(entries) - visible)
        if max_scroll:
            # 要滚动就一定会画右下角的提示条，正文得先给它让出一行，否则
            # 最后一行会被提示条盖住。
            limit -= line_h
            visible = max(1, (limit - top) // line_h)
            max_scroll = max(0, len(entries) - visible)
        detail_key = (general.id, len(entries))
        if detail_key != self._detail_key:
            self._detail_key = detail_key
            self.detail_scroll = 0
        self.detail_scroll = max(0, min(self.detail_scroll, max_scroll))
        if max_scroll and self.detail_scroll > 0:
            # 滚动时名字 / 势力那两行固定，正文自己往上走。
            top -= min(self.detail_scroll, max_scroll) * line_h

        for line, color in entries:
            if top + line_h > limit:
                break
            if top >= body_top:
                self.screen.blit(line_font.render(line, True, color), (rect.x + pad, top))
            top += line_h

        if max_scroll:
            hint = "滚轮翻看（%d/%d）" % (self.detail_scroll + 1, max_scroll + 1)
            rendered = line_font.render(hint, True, theme.GOLD_BRIGHT)
            badge = rendered.get_rect(
                bottomright=(rect.right - metrics.px(8), rect.bottom - metrics.px(4)))
            self.screen.blit(rendered, badge)

    def scroll_detail(self, delta):
        """详情面板滚动；返回是否真的动了（0 内容时不产生变化）。"""

        before = self.detail_scroll
        self.detail_scroll = max(0, self.detail_scroll + int(delta))
        return self.detail_scroll != before

    def _draw_settings(self, game, metrics, mouse):
        rect = self.settings_rect
        draw_panel(self.screen, rect, fill=theme.PANEL_DEEP, border=theme.GOLD_DIM,
                   border_width=theme.BORDER, radius=metrics.px(12), shadow=False)
        fonts = metrics.fonts
        config = config_of(game)
        for buttons, current in ((self.first_buttons, config.first),
                                 (self.control_buttons, config.control)):
            for value, button in buttons.items():
                button.kind = "primary" if value == current else "secondary"
                button.draw(self.screen, fonts, mouse)
        label_font = fonts.get("normal")
        for title, buttons in (("先手", self.first_buttons),
                               ("操作方式", self.control_buttons)):
            anchor = next(iter(buttons.values()), None)
            if anchor is None:
                continue
            text = label_font.render(title, True, theme.TEXT_DIM)
            # 标签右对齐到面板内的标签列（与种子输入框的标签同一列）；原来
            # 是贴着按钮左边界往外画，而面板左边就是武将网格，标签会直接压
            # 在武将牌上。
            bounds = text.get_rect(
                midright=(self.settings_label_x, anchor.rect.centery))
            self.screen.blit(text, bounds)
            self.settings_labels.append((title, bounds))
        self.seed_field.draw(self.screen, fonts, metrics)


def _wrap_lines(text, font, max_width):
    """按像素宽度折行（中文按字断行，够用且不引第三方库）。"""

    text = str(text or "")
    if not text:
        return []
    lines = []
    current = ""
    for char in text:
        candidate = current + char
        if current and font.size(candidate)[0] > max_width:
            lines.append(current)
            current = char
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines
