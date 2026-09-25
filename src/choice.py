import pygame

from src.ui import layout as ui_layout
from src.ui import theme
from src.ui.widgets import Button, draw_panel


class ChoiceRequest:

    def __init__(
        self,
        title,
        prompt,
        yes_label,
        no_label,
        on_yes,
        on_no,
        options=None,
        responder=None
    ):

        self.title = title
        self.prompt = prompt

        self.yes_label = yes_label
        self.no_label = no_label

        self.on_yes = on_yes
        self.on_no = on_no

        #: 可选的"任意数量选项"表示：``[(label, callback), …]``。
        #: 两个以内的选项沿用上面的 是/否 布局（单机一直是这个样子）；
        #: 三个以上才改用竖排列表，于是联机的多选一不会被写死成二选一。
        self.options = tuple(options or ())
        #: 这块面板属于谁（本机这一侧正在做选择的角色）。只用于界面归属：
        #: 双边手动测试据此把视角切到回答者身上，规则判定不看它。
        self.responder = responder


class ChoiceSystem:

    def __init__(self):

        self.current = None


    @property
    def active(self):

        return self.current is not None


    def clear(self):

        self.current = None


    def request(
        self,
        title,
        prompt,
        on_yes,
        on_no,
        yes_label="发动",
        no_label="不发动",
        options=None,
        responder=None
    ):

        self.current = ChoiceRequest(
            title,
            prompt,
            yes_label,
            no_label,
            on_yes,
            on_no,
            options=options,
            responder=responder,
        )


    def choose_yes(self):

        if self.current is None:
            return False

        request = self.current

        self.current = None

        request.on_yes()

        return True


    def choose_no(self):

        if self.current is None:
            return False

        request = self.current

        self.current = None

        request.on_no()

        return True


    def choose_option(self, index):

        if self.current is None:
            return False

        options = self.current.options

        if not 0 <= int(index) < len(options):
            return False

        request = self.current

        self.current = None

        _label, callback = options[int(index)]

        callback()

        return True


class ChoiceOverlay:
    """Dark modal with two themed choices, centred on any screen size.

    选项超过两个时改用竖排列表（联机的"多选一"用它；单机的二选一面板一行
    都没有改）。
    """

    def __init__(
        self,
        screen,
        title_font=None,
        body_font=None
    ):

        self.screen = screen
        self.title_font = title_font
        self.body_font = body_font
        self.panel_rect = pygame.Rect(0, 0, 10, 10)
        self.yes_rect = pygame.Rect(0, 0, 10, 10)
        self.no_rect = pygame.Rect(0, 0, 10, 10)
        self.option_rects = []
        self.metrics = None
        self.sync_layout(None)

    def sync_layout(self, metrics=None, count=0):
        metrics = metrics or ui_layout.LayoutMetrics(
            ui_layout.DESIGN_WIDTH, ui_layout.DESIGN_HEIGHT
        )
        self.metrics = metrics
        rows = int(count or 0)

        width = min(metrics.px(660), int(metrics.screen_w * 0.62))
        height = min(metrics.px(300), int(metrics.screen_h * 0.42))
        if rows > 2:
            # 列表布局：每行一个选项，面板随行数变高（仍然留出标题与提示）。
            row_h = metrics.px(58)
            height = min(
                metrics.px(140) + row_h * rows + metrics.px(24),
                int(metrics.screen_h * 0.8),
            )
        self.panel_rect = pygame.Rect(0, 0, width, height)
        self.panel_rect.center = (metrics.screen_w // 2, metrics.screen_h // 2)

        button_w = (width - metrics.px(72)) // 2
        button_h = metrics.px(60)
        gap = metrics.px(24)
        y = self.panel_rect.bottom - button_h - metrics.px(30)
        self.yes_rect = pygame.Rect(self.panel_rect.x + metrics.px(24), y, button_w, button_h)
        self.no_rect = pygame.Rect(self.yes_rect.right + gap, y, button_w, button_h)

        self.option_rects = []
        if rows > 2:
            top = self.panel_rect.y + metrics.px(140)
            for index in range(rows):
                self.option_rects.append(pygame.Rect(
                    self.panel_rect.x + metrics.px(36),
                    top + index * metrics.px(58),
                    width - metrics.px(72),
                    metrics.px(46),
                ))
        return self

    def _buttons(self, choice_system):
        request = choice_system.current
        yes = Button(pygame.Rect(self.yes_rect), request.yes_label, kind="primary", font="normal")
        no = Button(pygame.Rect(self.no_rect), request.no_label, kind="secondary", font="normal")
        return yes, no


    def handle_click(
        self,
        position,
        choice_system
    ):

        if not choice_system.active:
            return False

        request = choice_system.current

        if len(request.options) > 2:
            self.sync_layout(self.metrics, len(request.options))
            for index, rect in enumerate(self.option_rects):
                if rect.collidepoint(position):
                    choice_system.choose_option(index)
                    return True
            # 选择框存在期间，点击其他地方全部忽略。
            return True

        yes, no = self._buttons(choice_system)

        if yes.contains(position):
            choice_system.choose_yes()
            return True

        if no.contains(position):
            choice_system.choose_no()
            return True

        # 选择框存在期间，点击其他地方全部忽略。
        return True


    def draw(
        self,
        choice_system,
        metrics=None
    ):

        if not choice_system.active:
            return

        metrics = metrics or self.metrics
        # 度量变化（全屏启动 / 缩放窗口）时重新布局，避免第一帧尺寸不对。
        if metrics is None or self.metrics is not metrics or self.panel_rect.width <= 10:
            self.sync_layout(metrics, len(choice_system.current.options))
            metrics = self.metrics

        request = choice_system.current
        fonts = metrics.fonts

        veil = pygame.Surface((metrics.screen_w, metrics.screen_h), pygame.SRCALPHA)
        veil.fill((6, 9, 13, 176))
        self.screen.blit(veil, (0, 0))

        draw_panel(
            self.screen,
            self.panel_rect,
            fill=theme.PANEL,
            border=theme.GOLD,
            border_width=theme.BORDER_THICK,
            radius=metrics.px(16),
        )

        title = fonts.get("large").render(request.title, True, theme.GOLD_BRIGHT)
        self.screen.blit(title, title.get_rect(center=(self.panel_rect.centerx, self.panel_rect.y + metrics.px(56))))

        pygame.draw.line(
            self.screen,
            theme.GOLD_DIM,
            (self.panel_rect.x + metrics.px(56), self.panel_rect.y + metrics.px(96)),
            (self.panel_rect.right - metrics.px(56), self.panel_rect.y + metrics.px(96)),
            2,
        )

        from src.ui.widgets import ellipsize_text

        prompt_font = fonts.get("small")
        prompt_text = ellipsize_text(
            request.prompt, prompt_font, self.panel_rect.width - metrics.px(80)
        )
        prompt = prompt_font.render(prompt_text, True, theme.TEXT)
        self.screen.blit(prompt, prompt.get_rect(center=(self.panel_rect.centerx, self.panel_rect.y + metrics.px(130))))

        if len(request.options) > 2:
            self.sync_layout(metrics, len(request.options))
            mouse = pygame.mouse.get_pos()
            for (label, _callback), rect in zip(request.options, self.option_rects):
                Button(pygame.Rect(rect), str(label), kind="secondary",
                       font="normal").draw(self.screen, fonts, mouse)
            return

        yes, no = self._buttons(choice_system)
        mouse = pygame.mouse.get_pos()
        yes.draw(self.screen, fonts, mouse)
        no.draw(self.screen, fonts, mouse)
