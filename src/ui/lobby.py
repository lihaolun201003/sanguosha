"""房间大厅：房间信息 + 玩家列表 + 准备 / 开始游戏。

界面对「房主」与「客户端」是同一套代码：数据全部来自 ``session.lobby``
（房主是权威对象，客户端是房主广播的镜像），按钮的可用性也由大厅状态决定，
不在这里另立一套判断。
"""

import pygame

from . import layout, theme
from .widgets import Button, draw_panel, ellipsize_text

# 设计坐标（相对面板）。垂直节奏是算过的：标题 → 地址 → 房间信息 → 分隔线
# → 表头 → 8 行玩家 → 提示 / 按钮 / 说明，任何一段都不得压到下一段。
PANEL_DESIGN = (940, 800)
CONTENT_MARGIN = 56
TITLE_Y = 52
ADDRESS_Y = 108
INFO_OFFSET_Y = 34          # 房间号 / 模式 / 人数：紧跟在地址下面
DIVIDER_Y = 176
COLUMN_Y = 202
ROW_TOP = 224
ROW_HEIGHT = 44
ROW_GAP = 6                 # 8 行正好落在 224～614
MAX_ROWS = 8
NOTICE_Y = 634              # 提示行；开局后这里换成「房主已开始游戏」横幅
BUTTON_Y = 658
BUTTON_HEIGHT = 62
HINT_Y = 744
BUTTON_WIDTH = 300
BUTTON_GAP = 24


class LobbyScreen:
    """一屏展示房间里的所有人，并提供准备 / 开始 / 离开。"""

    def __init__(self, screen):
        self.screen = screen
        self.metrics = None
        self.panel_rect = pygame.Rect(0, 0, 10, 10)
        self.row_rects = []
        self.ready_button = Button(pygame.Rect(0, 0, 10, 10), "准备",
                                   kind="primary", font="large")
        self.start_button = Button(pygame.Rect(0, 0, 10, 10), "开始游戏",
                                   kind="primary", font="large")
        self.leave_button = Button(pygame.Rect(0, 0, 10, 10), "离开房间",
                                   kind="ghost", font="large")
        self.hint = ""
        # 已经点过、但还没被房主确认的准备请求 (player_id, 值)。
        # 连点「准备 / 取消 / 准备」时，按钮按本地意图翻面，最终仍以房主
        # 广播回来的权威状态为准。
        self._pending_ready = None
        self.sync_layout(None)

    # ==================================================
    # 布局
    # ==================================================

    def sync_layout(self, metrics=None):
        metrics = metrics or layout.LayoutMetrics(
            layout.DESIGN_WIDTH, layout.DESIGN_HEIGHT)
        self.metrics = metrics

        width = min(metrics.px(PANEL_DESIGN[0]), int(metrics.screen_w * 0.88))
        height = min(metrics.px(PANEL_DESIGN[1]), int(metrics.screen_h * 0.94))
        self.panel_rect = pygame.Rect(0, 0, width, height)
        self.panel_rect.center = (metrics.screen_w // 2, metrics.screen_h // 2)

        panel = self.panel_rect
        content_x = panel.x + metrics.px(CONTENT_MARGIN)
        content_w = panel.width - metrics.px(CONTENT_MARGIN) * 2

        self.title_y = panel.y + metrics.px(TITLE_Y)
        self.address_y = panel.y + metrics.px(ADDRESS_Y)
        self.divider_y = panel.y + metrics.px(DIVIDER_Y)
        self.info_y = self.address_y + metrics.px(INFO_OFFSET_Y)
        self.column_y = panel.y + metrics.px(COLUMN_Y)
        self.notice_y = panel.y + metrics.px(NOTICE_Y)
        self.hint_y = panel.y + metrics.px(HINT_Y)
        self.content_x = content_x
        self.content_w = content_w

        row_h = metrics.px(ROW_HEIGHT)
        step = row_h + metrics.px(ROW_GAP)
        top = panel.y + metrics.px(ROW_TOP)
        self.row_rects = [
            pygame.Rect(content_x, top + index * step, content_w, row_h)
            for index in range(MAX_ROWS)
        ]

        button_w = metrics.px(BUTTON_WIDTH)
        button_h = metrics.px(BUTTON_HEIGHT)
        gap = metrics.px(BUTTON_GAP)
        button_y = panel.y + metrics.px(BUTTON_Y)
        left_x = panel.centerx - button_w - gap // 2
        right_x = panel.centerx + gap // 2
        self.ready_button.rect = pygame.Rect(left_x, button_y, button_w, button_h)
        self.start_button.rect = pygame.Rect(left_x, button_y, button_w, button_h)
        self.leave_button.rect = pygame.Rect(right_x, button_y, button_w, button_h)
        self.single_leave_rect = pygame.Rect(
            panel.centerx - button_w // 2, button_y, button_w, button_h)
        return self

    # ==================================================
    # 交互
    # ==================================================

    def handle_event(self, event, session, game):
        """返回 ``"back"``（回多人菜单）/ ``"handled"`` / ``None``。"""

        if event.type != pygame.MOUSEBUTTONDOWN:
            if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                return "back"
            return None
        if getattr(event, "button", 1) != 1:
            return "handled"

        lobby = session.lobby
        started = bool(lobby is not None and lobby.started)
        if self.leave_button.contains(event.pos):
            return "back"
        if started:
            return "handled"

        if session.is_host:
            if self.start_button.contains(event.pos):
                ok, reason = session.start_match()
                self.hint = "" if ok else reason
            return "handled"

        if self.ready_button.contains(event.pos):
            target = not self.ready_intent(session)
            self._pending_ready = (session.local_player_id, target)
            session.set_ready(target)
        return "handled"

    def ready_intent(self, session):
        """按钮此刻应当表达的准备状态：优先用还没被房主确认的本地点击。"""

        local = session.local_player
        authoritative = bool(local.ready) if local is not None else False
        pending = self._pending_ready
        if pending is not None:
            player_id, value = pending
            if player_id == session.local_player_id and value != authoritative:
                return value
            self._pending_ready = None
        return authoritative

    # ==================================================
    # 绘制
    # ==================================================

    def draw(self, session, metrics=None, game=None):
        metrics = metrics or self.metrics
        if metrics is None or self.metrics is not metrics or self.panel_rect.width <= 10:
            self.sync_layout(metrics)
            metrics = self.metrics
        fonts = metrics.fonts
        mouse = pygame.mouse.get_pos()
        lobby = session.lobby

        self.screen.blit(theme.table_surface(metrics.screen_w, metrics.screen_h), (0, 0))
        panel = self.panel_rect
        draw_panel(self.screen, panel, fill=theme.PANEL_DEEP, border=theme.GOLD,
                   border_width=3, radius=metrics.px(22))

        title = fonts.get("hero").render("房 间 大 厅", True, theme.GOLD_BRIGHT)
        self.screen.blit(title, title.get_rect(center=(panel.centerx, self.title_y)))

        if lobby is not None:
            address_font = fonts.get("large")
            address = address_font.render(
                lobby.host_address or session.address_text, True, theme.GOLD_BRIGHT)
            self.screen.blit(address, address.get_rect(
                center=(panel.centerx, self.address_y)))

            # 人数行：真人数、AI 补位数、本局人数（开局前就显示，与真正
            # 开局的人数出自同一份推算，见 lobby.planned_battle_size）。
            info = "房间号 %s ｜ 模式 %s ｜ 真人 %d ｜ AI 补位 %d ｜ 本局 %d 人" % (
                lobby.room_id, self.mode_name(game, lobby),
                lobby.count, session.planned_ai_count,
                session.planned_battle_size)
            rendered = fonts.get("normal").render(info, True, theme.TEXT_DIM)
            self.screen.blit(rendered, rendered.get_rect(
                center=(panel.centerx, self.info_y)))

        pygame.draw.line(
            self.screen, theme.GOLD_DIM,
            (panel.x + metrics.px(60), self.divider_y),
            (panel.right - metrics.px(60), self.divider_y), 2)

        self._draw_columns(fonts, metrics)
        self._draw_players(session, fonts, metrics)
        self._draw_footer(session, fonts, metrics, mouse)

    def mode_name(self, game, lobby):
        """模式显示名：只问模式注册表（UI 不维护第二份模式名单）。"""

        mode_id = getattr(lobby, "game_mode", "") or "ffa"
        mode = game.modes.get(mode_id) if game is not None else None
        return mode.name if mode is not None else mode_id

    def _draw_columns(self, fonts, metrics):
        font = fonts.get("small")
        left = self.content_x + metrics.px(24)
        seat = font.render("座位", True, theme.TEXT_MUTED)
        self.screen.blit(seat, seat.get_rect(midleft=(left, self.column_y)))
        name = font.render("玩家", True, theme.TEXT_MUTED)
        self.screen.blit(name, name.get_rect(
            midleft=(self.content_x + metrics.px(170), self.column_y)))
        status = font.render("状态", True, theme.TEXT_MUTED)
        self.screen.blit(status, status.get_rect(
            midright=(self.content_x + self.content_w - metrics.px(24), self.column_y)))

    def _draw_players(self, session, fonts, metrics):
        lobby = session.lobby
        if lobby is None:
            return
        players = lobby.ordered()[:MAX_ROWS]
        for index, player in enumerate(players):
            rect = self.row_rects[index]
            is_local = player.player_id == session.local_player_id
            fill = theme.PANEL_ALT if is_local else (
                theme.PANEL if index % 2 == 0 else theme.PANEL_DEEP)
            border = theme.GOLD_BRIGHT if is_local else theme.GOLD_DIM
            draw_panel(self.screen, rect, fill=fill, border=border,
                       border_width=2 if is_local else 1,
                       radius=metrics.px(10), shadow=False)

            seat_font = fonts.get("small")
            seat = seat_font.render("座位 %d" % (player.seat + 1), True, theme.TEXT_DIM)
            self.screen.blit(seat, seat.get_rect(
                midleft=(rect.x + metrics.px(24), rect.centery)))

            name_font = fonts.get("large")
            name_x = rect.x + metrics.px(170)
            name = name_font.render(
                ellipsize_text(player.nickname, name_font,
                               rect.width - metrics.px(420)), True, theme.TEXT)
            self.screen.blit(name, name.get_rect(midleft=(name_x, rect.centery)))

            if is_local:
                tag_font = fonts.get("micro")
                tag = tag_font.render("你", True, theme.INK)
                badge = pygame.Rect(0, 0, tag.get_width() + metrics.px(14),
                                    tag.get_height() + metrics.px(8))
                badge.midleft = (name_x + name.get_width() + metrics.px(12), rect.centery)
                pygame.draw.rect(self.screen, theme.GOLD_BRIGHT, badge,
                                 border_radius=metrics.px(7))
                self.screen.blit(tag, tag.get_rect(center=badge.center))

            status_font = fonts.get("normal")
            label = player.status_label
            color = theme.lobby_status_color(label)
            status = status_font.render(label, True, color)
            self.screen.blit(status, status.get_rect(
                midright=(rect.right - metrics.px(24), rect.centery)))

    def _draw_footer(self, session, fonts, metrics, mouse):
        lobby = session.lobby
        started = bool(lobby is not None and lobby.started)

        if started:
            # 开局后：横幅占提示行的位置，按钮行只留「离开房间」。
            self.leave_button.rect = self.single_leave_rect
            banner_font = fonts.get("large")
            banner = banner_font.render("房主已开始游戏", True, theme.GOLD_BRIGHT)
            self.screen.blit(banner, banner.get_rect(
                center=(self.panel_rect.centerx, self.notice_y)))
            self.leave_button.draw(self.screen, fonts, mouse)
            note = fonts.get("small").render(
                "对局已经开始，正在进入牌桌", True, theme.TEXT_DIM)
            self.screen.blit(note, note.get_rect(
                center=(self.panel_rect.centerx, self.hint_y)))
            return

        if session.is_host:
            blocker = lobby.start_blocker() if lobby is not None else "房间未就绪"
            self.start_button.enabled = not blocker
            self.start_button.draw(self.screen, fonts, mouse)
            if blocker:
                hint = blocker
            else:
                hint = "可以开始：真人 %d 人 + AI 补位 %d 人（本局 %d 人）" % (
                    lobby.count, session.planned_ai_count,
                    session.planned_battle_size)
        else:
            ready = self.ready_intent(session)
            self.ready_button.set_label("取消准备" if ready else "准备")
            self.ready_button.kind = "secondary" if ready else "primary"
            self.ready_button.enabled = not session.connecting
            self.ready_button.draw(self.screen, fonts, mouse)
            if not ready:
                hint = "点击「准备」，房主即可开始游戏"
            elif lobby is not None:
                hint = lobby.start_blocker() or "全员已准备，等待房主开始游戏"
            else:
                hint = "等待房主开始游戏"

        self.leave_button.draw(self.screen, fonts, mouse)

        if hint:
            font = fonts.get("small")
            rendered = font.render(
                ellipsize_text(hint, font, self.content_w), True, theme.TEXT_DIM)
            self.screen.blit(rendered, rendered.get_rect(
                center=(self.panel_rect.centerx, self.hint_y)))

        notices = list(session.notices)[-2:]
        if notices:
            font = fonts.get("small")
            text = " ｜ ".join(notices)
            rendered = font.render(
                ellipsize_text(text, font, self.content_w), True, theme.TEXT_MUTED)
            self.screen.blit(rendered, rendered.get_rect(
                center=(self.panel_rect.centerx, self.notice_y)))
