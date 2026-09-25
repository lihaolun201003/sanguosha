"""多人对战入口：创建房间 / 加入房间（Phase 11.1）。

这一屏只做两件事，并把结果交给 ``LanSession``：

* 创建房间：昵称 + 最大人数 + 端口 → 房主会话（端口在本机绑定）；
* 加入房间：昵称 + 房主 IP + 端口 → 客户端会话（后台连接，界面不冻结）。

所有失败原因都在本屏用中文显示（连接超时 / 房间已满 / 协议不兼容……），
绝不把 traceback 丢给玩家。
"""

import getpass

import pygame

from src.network.lobby import planned_battle_size
from src.network.session import DEFAULT_GAME_MODE
from src.network.transport import local_ip_addresses
from src.start_menu import centered_text_origin

from . import layout, theme
from .text_input import TextField, accepts_digits_only, accepts_host
from .widgets import Button, draw_panel, ellipsize_text

# 设计坐标（相对面板，1600×900 设计空间）
PANEL_DESIGN = (900, 812)
BACK_BUTTON = (24, 20, 132, 46)
TITLE_Y = 58
SUBTITLE_Y = 110
DIVIDER_Y = 144
NICK_Y = 178
NICK_HEIGHT = 56
CREATE_TITLE_Y = 244
MODE_Y = 280
MODE_HEIGHT = 46
STEPPER_Y = 344
STEPPER_HEIGHT = 50
CREATE_BUTTON_Y = 406
BUTTON_HEIGHT = 58
JOIN_TITLE_Y = 490
IP_Y = 520
PORT_Y = 580
JOIN_BUTTON_Y = 650
HINT_Y = 738
STATUS_Y = 772
CONTENT_MARGIN = 60
STEPPER_GROUP_WIDTH = 372
STEPPER_LABEL_WIDTH = 150
STEPPER_BUTTON_WIDTH = 56
STEPPER_VALUE_WIDTH = 76
STEPPER_GAP = 12
MODE_BUTTON_GAP = 12
HINT_TEXT = "本机地址：%s ｜ 加入时输入房主 IP，按回车即可加入"

STATUS_INFO = "info"
STATUS_ERROR = "error"


def default_nickname():
    """默认昵称：用本机用户名（两台电脑天然不重名），拿不到就用「玩家」。"""

    try:
        name = getpass.getuser().strip()
    except Exception:  # pragma: no cover - 环境异常时退回默认值
        name = ""
    return name or "玩家"


class MultiplayerMenuScreen:
    """「多人对战」入口屏。"""

    def __init__(self, screen):
        self.screen = screen
        self.metrics = None
        self.panel_rect = pygame.Rect(0, 0, 10, 10)
        self.status = ""
        self.status_tone = STATUS_INFO
        self.connecting = False

        self.nickname_field = TextField(
            label="昵称", placeholder="玩家", initial=default_nickname(),
            max_length=12)
        self.ip_field = TextField(
            label="房主 IP", placeholder="192.168.1.23", max_length=21,
            accepts=accepts_host)
        self.port_field = TextField(
            label="端口", initial=str(9527), max_length=5,
            accepts=accepts_digits_only)

        self.minus_button = Button(pygame.Rect(0, 0, 10, 10), "−",
                                   kind="secondary", font="large")
        self.plus_button = Button(pygame.Rect(0, 0, 10, 10), "＋",
                                  kind="secondary", font="large")
        self.value_rect = pygame.Rect(0, 0, 10, 10)
        # 本局模式：联机不是新模式，它只是"身份局里某些座位由远程真人控制"，
        # 所以这里选的是**这一局用哪种模式**，默认标准身份局。
        self.mode_buttons = {}
        self.mode_order = ()
        self.create_button = Button(pygame.Rect(0, 0, 10, 10), "创建房间",
                                    kind="primary", font="large")
        self.join_button = Button(pygame.Rect(0, 0, 10, 10), "加入房间",
                                  kind="secondary", font="large")
        self.back_button = Button(pygame.Rect(0, 0, 10, 10), "← 返回",
                                  kind="ghost", font="normal")
        self.fields = (self.nickname_field, self.ip_field, self.port_field)
        self.sync_layout(None)

    # ==================================================
    # 布局
    # ==================================================

    def sync_layout(self, metrics=None):
        metrics = metrics or layout.LayoutMetrics(
            layout.DESIGN_WIDTH, layout.DESIGN_HEIGHT)
        self.metrics = metrics

        width = min(metrics.px(PANEL_DESIGN[0]), int(metrics.screen_w * 0.86))
        height = min(metrics.px(PANEL_DESIGN[1]), int(metrics.screen_h * 0.94))
        self.panel_rect = pygame.Rect(0, 0, width, height)
        self.panel_rect.center = (metrics.screen_w // 2, metrics.screen_h // 2)

        panel = self.panel_rect
        content_x = panel.x + metrics.px(CONTENT_MARGIN)
        content_w = panel.width - metrics.px(CONTENT_MARGIN) * 2
        center_x = panel.centerx

        self.back_button.rect = pygame.Rect(
            panel.x + metrics.px(BACK_BUTTON[0]), panel.y + metrics.px(BACK_BUTTON[1]),
            metrics.px(BACK_BUTTON[2]), metrics.px(BACK_BUTTON[3]))

        self.title_y = panel.y + metrics.px(TITLE_Y)
        self.subtitle_y = panel.y + metrics.px(SUBTITLE_Y)
        self.divider_y = panel.y + metrics.px(DIVIDER_Y)

        # 这里已经在屏幕坐标里算位置了（panel_rect 就是屏幕矩形），不能再走
        # metrics.rect（它会把设计坐标再换算一次）。
        field_h = metrics.px(NICK_HEIGHT)
        label_w = metrics.px(110)
        for field, design_y in ((self.nickname_field, NICK_Y),
                                (self.ip_field, IP_Y),
                                (self.port_field, PORT_Y)):
            field.set_rect(pygame.Rect(
                content_x, panel.y + metrics.px(design_y), content_w, field_h),
                label_width=label_w)

        self.create_title_y = panel.y + metrics.px(CREATE_TITLE_Y)
        self.join_title_y = panel.y + metrics.px(JOIN_TITLE_Y)

        # 模式按钮：横排，与房间人数共用一条视觉节奏。
        count = max(1, len(self.mode_order))
        mode_area = min(content_w, metrics.px(520))
        mode_gap = metrics.px(MODE_BUTTON_GAP)
        mode_w = max(metrics.px(140), (mode_area - mode_gap * (count - 1)) // count)
        mode_h = metrics.px(MODE_HEIGHT)
        mode_y = panel.y + metrics.px(MODE_Y)
        self.mode_panel_rect = pygame.Rect(
            center_x - (mode_w * count + mode_gap * (count - 1)) // 2,
            mode_y, mode_w * count + mode_gap * (count - 1), mode_h)
        for index, mode_id in enumerate(self.mode_order):
            button = self.mode_buttons.get(mode_id)
            if button is None:
                continue
            button.rect = pygame.Rect(
                self.mode_panel_rect.x + index * (mode_w + mode_gap),
                mode_y, mode_w, mode_h)
        self.mode_hint_y = self.mode_panel_rect.bottom + metrics.px(8)

        # 人数步进器：整组居中，与主菜单的人数控件同一套操作习惯。
        group_w = metrics.px(STEPPER_GROUP_WIDTH)
        group_x = center_x - group_w // 2
        stepper_y = panel.y + metrics.px(STEPPER_Y)
        stepper_h = metrics.px(STEPPER_HEIGHT)
        button_w = metrics.px(STEPPER_BUTTON_WIDTH)
        value_w = metrics.px(STEPPER_VALUE_WIDTH)
        gap = metrics.px(STEPPER_GAP)

        self.stepper_label_rect = pygame.Rect(
            group_x, stepper_y, metrics.px(STEPPER_LABEL_WIDTH), stepper_h)
        self.minus_button.rect = pygame.Rect(
            self.stepper_label_rect.right + gap, stepper_y, button_w, stepper_h)
        self.value_rect = pygame.Rect(
            self.minus_button.rect.right + gap, stepper_y, value_w, stepper_h)
        self.plus_button.rect = pygame.Rect(
            self.value_rect.right + gap, stepper_y, button_w, stepper_h)

        button_w = min(metrics.px(300), content_w - metrics.px(24))
        button_h = metrics.px(BUTTON_HEIGHT)
        self.create_button.rect = pygame.Rect(
            center_x - button_w // 2, panel.y + metrics.px(CREATE_BUTTON_Y),
            button_w, button_h)
        self.join_button.rect = pygame.Rect(
            center_x - button_w // 2, panel.y + metrics.px(JOIN_BUTTON_Y),
            button_w, button_h)

        self.hint_y = panel.y + metrics.px(HINT_Y)
        self.status_y = panel.y + metrics.px(STATUS_Y)
        return self

    def sync_modes(self, modes, session=None, game=None):
        """按模式注册表重建模式按钮；返回 [(mode_cls, Button)]。"""

        modes = tuple(modes)
        ids = tuple(mode.id for mode in modes)
        if ids != self.mode_order:
            self.mode_order = ids
            self.mode_buttons = {
                mode.id: Button(pygame.Rect(0, 0, 10, 10), mode.name,
                                kind="secondary", font="normal")
                for mode in modes
            }
            self.sync_layout(self.metrics)
        return [(mode, self.mode_buttons[mode.id]) for mode in modes]

    def match_mode(self, session, game):
        """本局模式 id：以会话上的选择为准，没有就退回游戏当前模式。"""

        mode_id = str(getattr(session, "game_mode", "") or "")
        if mode_id and (game is None or game.modes.get(mode_id) is not None):
            return mode_id
        if game is not None:
            return game.mode_id
        return DEFAULT_GAME_MODE

    def apply_match_mode(self, session, game):
        """把会话上选的模式真正应用到这个 Game（创建房间 / 进联机菜单时调用）。

        模式决定"允许人数""是否分配身份""AI 补位到几人"，所以它必须在
        建局之前就落到 Game 上——只显示在大厅里、Game 却还是自由混战，
        正是之前那局"二人裸局"的来源之一。
        """

        mode_id = self.match_mode(session, game)
        session.game_mode = mode_id
        if game is not None and game.modes.get(mode_id) is not None:
            game.set_mode(mode_id)
        return mode_id

    def set_match_mode(self, mode_id, session, game):
        session.game_mode = str(mode_id or DEFAULT_GAME_MODE)
        return self.apply_match_mode(session, game)

    def mode_object(self, session, game):
        """当前选中模式的模式对象（推算本局人数用）。"""

        if game is None:
            return None
        mode_id = self.match_mode(session, game)
        return game.modes.get(mode_id) or game.mode

    # ==================================================
    # 交互
    # ==================================================

    def set_status(self, text, tone=STATUS_INFO):
        self.status = str(text or "")
        self.status_tone = tone
        return self

    def clear_fields_focus(self):
        for field in self.fields:
            field.blur()

    def _focused_field(self):
        for field in self.fields:
            if field.focused:
                return field
        return None

    def handle_event(self, event, session, game):
        """返回 ``"back"`` / ``"handled"`` / ``None``（未处理，交给主循环）。"""

        if event.type == pygame.MOUSEBUTTONDOWN:
            if getattr(event, "button", 1) != 1:
                return "handled"
            for field in self.fields:
                field.handle_event(event)
            if self.back_button.contains(event.pos):
                self.clear_fields_focus()
                return "back"
            for mode_id, button in self.mode_buttons.items():
                if button.contains(event.pos):
                    self.set_match_mode(mode_id, session, game)
                    self.set_status("")
                    return "handled"
            if self.minus_button.contains(event.pos):
                self._step_max_players(-1, session, game)
            elif self.plus_button.contains(event.pos):
                self._step_max_players(1, session, game)
            elif self.create_button.contains(event.pos):
                return self.create_room(session, game)
            elif self.join_button.contains(event.pos):
                return self.join_room(session, game)
            return "handled"

        if event.type == pygame.TEXTINPUT:
            return "handled" if any(
                field.handle_event(event) for field in self.fields) else None

        if event.type == pygame.KEYDOWN:
            for field in self.fields:
                if field.handle_event(event):
                    return "handled"
            if event.key == pygame.K_F11:
                return None                     # 全屏切换在任何界面都要能用
            if event.key == pygame.K_ESCAPE:
                if self._focused_field() is not None:
                    self.clear_fields_focus()
                else:
                    return "back"
                return "handled"
            if self._focused_field() is not None:
                # 正在输入昵称 / IP：其余按键都归输入框，不能回落到全局快捷键
                # （否则在昵称里打 "1" 会顺手改对局速度）。
                return "handled"
            if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                # 回车只在「填了房主 IP」时等于加入房间，避免误触创建。
                if self.ip_field.value():
                    return self.join_room(session, game)
                return "handled"
            if event.key == pygame.K_TAB:
                self._cycle_focus()
                return "handled"
            return None

        return None

    def _cycle_focus(self):
        focused = next((index for index, field in enumerate(self.fields)
                        if field.focused), None)
        for field in self.fields:
            field.blur()
        index = 0 if focused is None else (focused + 1) % len(self.fields)
        self.fields[index].focus()

    def _step_max_players(self, delta, session, game):
        allowed = self.max_player_choices(game)
        current = self.max_players_value(session, game)
        if current not in allowed:
            target = min(allowed, key=lambda value: (abs(value - current), value))
        else:
            index = allowed.index(current)
            target = allowed[max(0, min(len(allowed) - 1, index + delta))]
        session.set_max_players(target)
        self.set_status("")

    # ==================================================
    # 创建 / 加入
    # ==================================================

    def create_room(self, session, game):
        """创建房间（按钮点击与 ``--host`` 启动参数共用一份逻辑）。"""

        nickname = self.nickname_field.value()
        if not nickname:
            self.set_status("请先填写昵称", STATUS_ERROR)
            return "handled"
        # 模式在建局之前就落到 Game 上：大厅显示的人数与真正开局的人数必须
        # 出自同一份推算（身份局 5～8 人，自由混战 2～8 人）。
        mode_id = self.apply_match_mode(session, game)
        session.max_players = self.max_players_value(session, game)
        ok, message = session.create_room(
            nickname, session.max_players, self.port_field.value(),
            game_mode=mode_id)
        if not ok:
            self.set_status(message, STATUS_ERROR)
            return "handled"
        self.set_status("房间已创建，等待其他玩家加入", STATUS_INFO)
        return "handled"

    def join_room(self, session, game):
        """加入房间（连接在后台线程进行，界面保持可拖动 / 可缩放）。"""

        nickname = self.nickname_field.value()
        if not nickname:
            self.set_status("请先填写昵称", STATUS_ERROR)
            return "handled"
        ok, message = session.join_room(
            nickname, self.ip_field.value(), self.port_field.value())
        if not ok:
            self.set_status(message, STATUS_ERROR)
            return "handled"
        self.connecting = True
        self.set_status("正在连接 " + self.ip_field.value() + " …", STATUS_INFO)
        return "handled"

    def max_player_choices(self, game):
        """房间容量（最多几名**真人**进房）：2～8 人。

        它和"本局人数"是两回事：身份局的本局人数由模式决定（5～8 人，不够
        的人头自动补 AI），而房间容量只限制真人。两者混在一个步进器上会让
        "2 个真人的身份局"变得不可能配置。
        """

        return tuple(range(2, 9))

    def max_players_value(self, session, game):
        choices = self.max_player_choices(game)
        value = int(getattr(session, "max_players", 0) or 0)
        if value in choices:
            return value
        return choices[-1] if choices else 8

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

        self.screen.blit(theme.table_surface(metrics.screen_w, metrics.screen_h), (0, 0))
        glow_radius = max(metrics.px(190), metrics.screen_w // 8)
        for center, color in (
            ((metrics.screen_w // 6, metrics.screen_h // 5), (24, 40, 52)),
            ((metrics.screen_w - metrics.screen_w // 8,
              metrics.screen_h - metrics.screen_h // 6), (20, 34, 46)),
        ):
            halo = theme.radial_glow(glow_radius, color, alpha=120)
            self.screen.blit(halo, halo.get_rect(center=center))

        panel = self.panel_rect
        draw_panel(self.screen, panel, fill=theme.PANEL_DEEP, border=theme.GOLD,
                   border_width=3, radius=metrics.px(22))

        title = fonts.get("hero").render("局 域 网 联 机", True, theme.GOLD_BRIGHT)
        self.screen.blit(title, title.get_rect(center=(panel.centerx, self.title_y)))
        subtitle = fonts.get("small").render(
            "同一局域网内：一台电脑创建房间，其他电脑输入房主 IP 加入", True, theme.TEXT_DIM)
        self.screen.blit(subtitle, subtitle.get_rect(center=(panel.centerx, self.subtitle_y)))
        pygame.draw.line(
            self.screen, theme.GOLD_DIM,
            (panel.x + metrics.px(70), self.divider_y),
            (panel.right - metrics.px(70), self.divider_y), 2)

        self.back_button.draw(self.screen, fonts, mouse)

        self.nickname_field.draw(self.screen, fonts, metrics)

        # ---- 创建房间 ----
        self._draw_section_title(fonts, metrics, "创 建 房 间", self.create_title_y)

        # 模式按钮：数量随注册表变化，这里做一次懒同步（场景切换时也会重建）。
        if game is not None:
            modes = game.modes.list_modes()
            if tuple(mode.id for mode in modes) != self.mode_order:
                self.sync_modes(modes, session, game)
                self.sync_layout(metrics)
            current_mode = self.match_mode(session, game)
            for mode_id, button in self.mode_buttons.items():
                button.kind = "primary" if mode_id == current_mode else "secondary"
                button.draw(self.screen, fonts, mouse)

        value_label = fonts.get("normal").render("房间人数上限", True, theme.TEXT_DIM)
        self.screen.blit(value_label, value_label.get_rect(
            midright=(self.stepper_label_rect.right, self.stepper_label_rect.centery)))

        pygame.draw.rect(self.screen, theme.PANEL_SUNKEN, self.value_rect,
                         border_radius=metrics.px(9))
        pygame.draw.rect(self.screen, theme.GOLD_DIM, self.value_rect, 2,
                         border_radius=metrics.px(9))
        value = fonts.get("menu_count").render(
            str(self.max_players_value(session, game)), True, theme.GOLD_BRIGHT)
        self.screen.blit(value, centered_text_origin(value, self.value_rect))

        choices = self.max_player_choices(game)
        current = self.max_players_value(session, game)
        index = choices.index(current) if current in choices else 0
        self.minus_button.enabled = index > 0 and session.client is None
        self.plus_button.enabled = index < len(choices) - 1 and session.client is None
        self.minus_button.draw(self.screen, fonts, mouse)
        self.plus_button.draw(self.screen, fonts, mouse)

        # 本局人数：真人 + AI 补位（身份局 5～8 人，自由混战 2～8 人）。
        size = session.planned_battle_size if session.lobby is not None else 1
        if not size:
            # 还没开房：按"房主 1 人 + 补位"预告本局人数。
            size = planned_battle_size(self.mode_object(session, game), 1)
        ai_count = max(0, size - 1)
        plan = "本局 %d 人：房主 1 人 ｜ AI 补位 %d 人" % (size, ai_count)
        plan_font = fonts.get("small")
        rendered_plan = plan_font.render(
            ellipsize_text(plan, plan_font, panel.width - metrics.px(90)),
            True, theme.TEXT_DIM)
        self.screen.blit(rendered_plan, rendered_plan.get_rect(
            center=(panel.centerx, self.mode_hint_y)))

        self.create_button.enabled = not self.connecting
        self.join_button.enabled = not self.connecting
        self.create_button.draw(self.screen, fonts, mouse)
        self.join_button.draw(self.screen, fonts, mouse)

        # ---- 加入房间 ----
        self._draw_section_title(fonts, metrics, "加 入 房 间", self.join_title_y)
        self.ip_field.draw(self.screen, fonts, metrics)
        self.port_field.draw(self.screen, fonts, metrics)

        hint = self.hint_text(session)
        hint_font = fonts.get("small")
        rendered = hint_font.render(
            ellipsize_text(hint, hint_font, panel.width - metrics.px(90)),
            True, theme.TEXT_DIM)
        self.screen.blit(rendered, rendered.get_rect(
            center=(panel.centerx, self.hint_y)))

        if self.status:
            color = theme.DANGER if self.status_tone == STATUS_ERROR else theme.GOLD_BRIGHT
            status_font = fonts.get("normal")
            text = ellipsize_text(self.status, status_font, panel.width - metrics.px(90))
            rendered = status_font.render(text, True, color)
            self.screen.blit(rendered, rendered.get_rect(
                center=(panel.centerx, self.status_y)))

    def hint_text(self, session):
        """底部提示：本机地址（房主要念给别人）+ 加入方式。

        房主开房后就会立刻进大厅（大厅顶部显示的是同一个地址），所以这里
        在开房之前就要把本机 IP 显示出来，否则玩家根本不知道该报哪个地址。
        """

        addresses = local_ip_addresses()
        return HINT_TEXT % (addresses[0] if addresses else "127.0.0.1")

    def _draw_section_title(self, fonts, metrics, text, y):
        rendered = fonts.get("normal").render(text, True, theme.GOLD_BRIGHT)
        self.screen.blit(rendered, rendered.get_rect(center=(self.panel_rect.centerx, y)))
        half = metrics.px(190)
        for start, end in ((self.panel_rect.centerx - half, self.panel_rect.centerx - metrics.px(96)),
                           (self.panel_rect.centerx + metrics.px(96), self.panel_rect.centerx + half)):
            pygame.draw.line(self.screen, theme.GOLD_DIM, (start, y), (end, y), 1)
