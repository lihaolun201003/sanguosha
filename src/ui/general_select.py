"""General selection screen: pick a general before the battle starts.

武将牌直接用 ``assets/generals/cards`` 里的真实卡面（按比例 fit，不拉伸）；
没有素材的武将自动退回原来的程序绘制头像，因此新增第二批武将时即使图还没到
也能正常开局。

整屏数据驱动：只读 ``GeneralDef`` / ``SkillDef``，不认识任何具体武将。所有
rect 都来自 LayoutMetrics，候选数量 3～11 都能自动排版。
"""

import pygame

from . import assets as assets_module
from . import layout, theme
from .widgets import Button, draw_panel, ellipsize_text

KINGDOM_COLORS = {
    "wei": (86, 116, 178),
    "shu": (176, 84, 72),
    "wu": (72, 152, 118),
    "qun": (140, 132, 108),
}

CARD_COLUMNS = 4
CARD_GAP = 22
# 武将牌素材比例（420:572）；卡片高度按它反推，保证卡面不被压扁。
CARD_ASPECT = 420.0 / 572.0
TEXT_AREA = 88
HOVER_LIFT = 10


class GeneralSelectScreen:
    """一屏展示可选武将卡，支持选择 / 随机 / 确认 / 返回。

    ``allow_random`` / ``allow_back`` 默认开启（单机开局的两条退路）；联机的
    候选由房主给定，"随机"会选出候选之外的武将、"返回"也没有可回的地方，
    所以那两个按钮在联机里关掉，只留「确认出战」。
    """

    def __init__(self, screen, *, allow_random=True, allow_back=True):
        self.screen = screen
        self.metrics = None
        self.card_rects = []
        self.card_hit_rects = []
        self.hover_index = None
        self.allow_random = bool(allow_random)
        self.allow_back = bool(allow_back)
        #: 可选的一行提示（联机开局用它显示"等待其他玩家选将"）。为空时不画。
        self.notice = ""
        self.confirm_button = Button(pygame.Rect(0, 0, 10, 10), "确认出战", kind="primary", font="large")
        self.random_button = Button(pygame.Rect(0, 0, 10, 10), "随机", kind="secondary", font="normal")
        self.back_button = Button(pygame.Rect(0, 0, 10, 10), "返回", kind="ghost", font="normal")
        self.sync_layout(None, ())

    # ==================================================
    # 布局
    # ==================================================

    def sync_layout(self, metrics=None, generals=()):
        metrics = metrics or layout.LayoutMetrics(layout.DESIGN_WIDTH, layout.DESIGN_HEIGHT)
        self.metrics = metrics
        self.generals = list(generals)
        #: 当前这一屏放不下、因此没有画出卡片的武将数量（见 sync_layout）。
        self.hidden_count = 0

        count = max(1, len(self.generals))
        # 候选少时大字排开；展示全量武将池（25 名）时自动增加列数，
        # 保证网格始终落在可用区域内，不会压到按钮。
        if count <= 8:
            columns = min(CARD_COLUMNS, count)
        elif count <= 16:
            columns = 5
        else:
            columns = 6
        columns = min(columns, count)
        gap = metrics.px(CARD_GAP)

        header = metrics.px(150)
        footer = metrics.px(126)
        available_h = max(metrics.px(160), metrics.screen_h - header - footer)
        available_w = max(metrics.px(320), metrics.screen_w - metrics.px(72))

        # 单张卡片的最小可用高度（武将牌 + 文字区）。任何情况下都不能为了
        # "把所有人塞进一屏"而把卡片压到看不见——扩展包全部加载后注册表有
        # 91 名武将，硬塞只会让网格冲出屏幕、并且压到确认按钮上。
        min_card_h = metrics.px(TEXT_AREA) + metrics.px(72)
        max_rows = max(1, (available_h + gap) // (min_card_h + gap))
        visible = min(count, columns * max_rows)
        rows = max(1, (visible + columns - 1) // columns)
        #: 这一屏放不下的武将数量。真实开局只传候选（3 名），不会溢出；
        #: 展示全量注册表（工具 / 测试 / 图鉴）时它会大于 0。
        self.hidden_count = count - visible

        width_limit = max(metrics.px(64), (available_w - gap * (columns - 1)) // columns)
        height_limit = max(min_card_h, (available_h - gap * (rows - 1)) // rows)

        # 卡片 = 武将牌（按素材比例）+ 下方文字区。空间紧张时先压缩文字区，
        # 保证武将牌本身完整、网格不越出可用区域——否则确认按钮会被压住。
        text_area = metrics.px(TEXT_AREA)
        min_art = metrics.px(72)
        if height_limit - text_area < min_art:
            text_area = max(metrics.px(14), height_limit - min_art)

        width = min(width_limit, int(max(1, height_limit - text_area) * CARD_ASPECT))
        width = max(min(metrics.px(52), width_limit), width)
        art_height = int(round(width / CARD_ASPECT))
        # 兜底：最小宽度可能让卡片略高，这里再压一次高度上限。
        if art_height + text_area > height_limit:
            art_height = max(1, height_limit - text_area)
        height = art_height + text_area

        grid_height = rows * height + max(0, rows - 1) * gap
        top = header + max(0, (available_h - grid_height) // 2)
        start_x = (metrics.screen_w - (columns * width + (columns - 1) * gap)) // 2

        self.card_rects = []
        self.card_hit_rects = []
        lift = metrics.px(HOVER_LIFT)
        for index in range(min(len(self.generals), visible)):
            column, row = index % columns, index // columns
            rect = pygame.Rect(
                start_x + column * (width + gap),
                top + row * (height + gap),
                width,
                height,
            )
            self.card_rects.append(rect)
            # 悬停时卡片上浮，点击区域必须跟着一起抬高，否则"看得见点不到"。
            self.card_hit_rects.append(rect.union(rect.move(0, -lift)))

        button_y = min(
            metrics.screen_h - metrics.px(62) - metrics.px(20),
            max(top + grid_height + metrics.px(18), metrics.screen_h - footer + metrics.px(6)),
        )
        button_h = metrics.px(62)
        self.confirm_button.rect = pygame.Rect(
            metrics.screen_w // 2 - metrics.px(120), button_y, metrics.px(240), button_h
        )
        self.random_button.rect = pygame.Rect(
            self.confirm_button.rect.left - metrics.px(210), button_y, metrics.px(190), button_h
        )
        self.back_button.rect = pygame.Rect(
            self.confirm_button.rect.right + metrics.px(20), button_y, metrics.px(190), button_h
        )
        return self

    # ==================================================
    # 交互
    # ==================================================

    def card_index_at(self, position):
        for index, rect in enumerate(self.card_hit_rects):
            if rect.collidepoint(position):
                return index
        return None

    def general_at(self, position):
        index = self.card_index_at(position)
        if index is None or index >= len(self.generals):
            return None
        return self.generals[index]

    def set_hover(self, position):
        """只更新悬停高亮；不影响 game.selected_general。"""

        self.hover_index = self.card_index_at(position)
        return self.hover_index

    def handle_click(self, position, game):
        """返回 "select" / "random" / "confirm" / "back" / None。"""

        general = self.general_at(position)
        if general is not None:
            game.selected_general = general.id
            return "select"

        if self.allow_random and self.random_button.contains(position):
            pool = list(game.generals.ids())
            if pool:
                game.selected_general = game.rng.choice(pool)
            return "random"

        if self.confirm_button.contains(position) and game.selected_general:
            return "confirm"

        if self.allow_back and self.back_button.contains(position):
            return "back"

        return None

    # ==================================================
    # 绘制
    # ==================================================

    def draw(self, game, metrics=None):
        metrics = metrics or self.metrics
        # metrics 变了（全屏启动 / 缩放窗口）就重算布局，否则第一帧会沿用
        # 旧尺寸，卡片挤在一起。武将列表沿用当前显示的那批；从未设置过时
        # 退回全量，避免出现空白界面。
        if metrics is None or self.metrics is not metrics or not self.card_rects:
            self.sync_layout(metrics, self.generals or game.generals.list_generals())
            metrics = self.metrics

        fonts = metrics.fonts
        mouse = pygame.mouse.get_pos()
        self.set_hover(mouse)

        self.screen.blit(theme.table_surface(metrics.screen_w, metrics.screen_h), (0, 0))

        title = fonts.get("title").render("选择你的武将", True, theme.GOLD_BRIGHT)
        self.screen.blit(title, title.get_rect(center=(metrics.screen_w // 2, metrics.px(64))))

        selected = game.generals.get(game.selected_general)
        hint_text = (
            "已选择：%s    ·    确认后其余角色会自动分配不同武将" % selected.name
            if selected is not None
            else "点击武将牌选择你的武将，确认后其余角色会自动分配不同武将"
        )
        hint_color = theme.GOLD_BRIGHT if selected is not None else theme.TEXT_DIM
        hint = fonts.get("small").render(hint_text, True, hint_color)
        self.screen.blit(hint, hint.get_rect(center=(metrics.screen_w // 2, metrics.px(108))))

        for index, (general, rect) in enumerate(zip(self.generals, self.card_rects)):
            self._draw_general_card(
                game, general, rect, metrics,
                chosen=game.selected_general == general.id,
                hovered=index == self.hover_index,
            )

        self.confirm_button.enabled = bool(game.selected_general)
        if self.allow_random:
            self.random_button.draw(self.screen, fonts, mouse)
        self.confirm_button.draw(self.screen, fonts, mouse)
        if self.allow_back:
            self.back_button.draw(self.screen, fonts, mouse)

        if self.notice:
            note_font = fonts.get("small")
            note = note_font.render(self.notice, True, theme.TEXT_DIM)
            self.screen.blit(note, note.get_rect(
                center=(metrics.screen_w // 2, metrics.screen_h - metrics.px(28))))

    def _draw_general_card(self, game, general, rect, metrics, *, chosen, hovered):
        fonts = metrics.fonts
        kingdom_color = KINGDOM_COLORS.get(general.kingdom, theme.GOLD_DIM)

        lift = metrics.px(HOVER_LIFT) if hovered and not chosen else 0
        card = rect.move(0, -lift)

        # 悬停时先画一层阴影，制造"抬起来"的层次。
        if lift:
            shadow = pygame.Surface((card.width, card.height), pygame.SRCALPHA)
            pygame.draw.rect(shadow, (0, 0, 0, 120), shadow.get_rect(),
                             border_radius=metrics.px(14))
            self.screen.blit(shadow, (card.x, card.y + metrics.px(8)))

        fill = theme.PANEL_ALT if chosen else theme.PANEL
        if chosen:
            border, border_width = theme.TARGET_YELLOW, theme.BORDER_THICK
        elif hovered:
            border, border_width = theme.GOLD_BRIGHT, theme.BORDER
        else:
            border, border_width = theme.GOLD_DIM, theme.BORDER
        draw_panel(
            surface=self.screen, rect=card, fill=fill, border=border,
            border_width=border_width, radius=metrics.px(14), shadow=False,
        )

        # 势力色条
        stripe = pygame.Rect(card.x, card.y, metrics.px(7), card.height)
        pygame.draw.rect(self.screen, kingdom_color, stripe,
                         border_top_left_radius=metrics.px(14),
                         border_bottom_left_radius=metrics.px(14))

        pad = metrics.px(12)
        art_rect = pygame.Rect(
            card.x + pad, card.y + pad,
            max(1, card.width - pad * 2),
            max(1, card.height - pad - metrics.px(TEXT_AREA)),
        )
        self._draw_general_art(general, art_rect, metrics, kingdom_color)

        # 选中角标
        if chosen:
            badge_font = fonts.get("micro")
            label = badge_font.render("已选择", True, theme.INK)
            badge = pygame.Rect(0, 0, label.get_width() + metrics.px(14), label.get_height() + metrics.px(8))
            badge.topright = (card.right - pad, card.y + pad)
            pygame.draw.rect(self.screen, theme.TARGET_YELLOW, badge, border_radius=metrics.px(8))
            self.screen.blit(label, label.get_rect(center=badge.center))

        self._draw_general_text(game, general, card, art_rect, metrics, kingdom_color)

    def _draw_general_art(self, general, art_rect, metrics, kingdom_color):
        """卡面优先用真实武将牌；没有素材时退回程序绘制的头像圆。"""

        art = None
        asset_id = assets_module.general_asset_id(general.id)
        registry = assets_module.get_registry()
        source = registry.surface(asset_id)
        if source is not None:
            target = assets_module.fit_contain(art_rect, source.get_size(), align="midtop")
            scaled = registry.scaled(asset_id, target.size)
            if scaled is not None:
                art = (scaled, target)

        if art is not None:
            scaled, target = art
            frame = target.inflate(metrics.px(6), metrics.px(6))
            pygame.draw.rect(self.screen, theme.PANEL_SUNKEN, frame, border_radius=metrics.px(8))
            pygame.draw.rect(self.screen, theme.GOLD_DIM, frame, 1, border_radius=metrics.px(8))
            self.screen.blit(scaled, target.topleft)
            return

        # Fallback：头像圆 + 姓氏首字
        fonts = metrics.fonts
        size = max(metrics.px(34), min(art_rect.width, art_rect.height) - metrics.px(20))
        center = (art_rect.centerx, art_rect.y + size // 2 + metrics.px(10))
        pygame.draw.circle(self.screen, (30, 42, 54), center, size // 2)
        pygame.draw.circle(self.screen, kingdom_color, center, size // 2, max(2, metrics.px(3)))
        initial = fonts.get("large").render(general.name[:1], True, theme.TEXT)
        self.screen.blit(initial, initial.get_rect(center=center))

    def _draw_general_text(self, game, general, card, art_rect, metrics, kingdom_color):
        """武将名 / 势力 / 体力 / 技能，作为卡面之外的程序化补充信息。"""

        fonts = metrics.fonts
        pad = metrics.px(12)
        top = art_rect.bottom + metrics.px(6)
        bottom_limit = card.bottom - metrics.px(6)
        available = card.width - pad * 2

        name_font = fonts.get("seat_name")
        name = ellipsize_text(general.name, name_font, available)
        rendered = name_font.render(name, True, theme.TEXT)
        self.screen.blit(rendered, rendered.get_rect(midtop=(card.centerx, top)))
        top += rendered.get_height() + metrics.px(1)

        meta_font = fonts.get("micro")
        meta_text = "%s · %s · %d/%d" % (
            general.kingdom_name,
            "男" if general.gender == "male" else "女",
            general.max_hp,
            general.max_hp,
        )
        meta = meta_font.render(ellipsize_text(meta_text, meta_font, available), True, theme.TEXT_DIM)
        self.screen.blit(meta, meta.get_rect(midtop=(card.centerx, top)))
        top += meta.get_height() + metrics.px(2)

        line_font = fonts.get("micro")
        line_height = line_font.get_linesize()
        for skill_id in general.skill_ids:
            definition = game.skill_registry.get(skill_id)
            if definition is None:
                continue
            if top + line_height > bottom_limit:
                break
            label = "【%s】" % definition.name
            if definition.description:
                label += definition.description
            text = ellipsize_text(label, line_font, available)
            color = theme.GOLD_BRIGHT if "】" in text else theme.TEXT_DIM
            self.screen.blit(line_font.render(text, True, color), (card.x + pad, top))
            top += line_height
