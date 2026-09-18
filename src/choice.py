import pygame


class ChoiceRequest:

    def __init__(
        self,
        title,
        prompt,
        yes_label,
        no_label,
        on_yes,
        on_no
    ):

        self.title = title
        self.prompt = prompt

        self.yes_label = yes_label
        self.no_label = no_label

        self.on_yes = on_yes
        self.on_no = on_no


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
        no_label="不发动"
    ):

        self.current = ChoiceRequest(
            title,
            prompt,
            yes_label,
            no_label,
            on_yes,
            on_no
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


class ChoiceOverlay:

    def __init__(
        self,
        screen,
        title_font,
        body_font
    ):

        self.screen = screen

        self.title_font = title_font
        self.body_font = body_font

        self.panel_rect = pygame.Rect(
            300,
            300,
            400,
            190
        )

        self.yes_rect = pygame.Rect(
            365,
            415,
            115,
            46
        )

        self.no_rect = pygame.Rect(
            520,
            415,
            115,
            46
        )


    def handle_click(
        self,
        position,
        choice_system
    ):

        if not choice_system.active:
            return False

        if self.yes_rect.collidepoint(
            position
        ):

            choice_system.choose_yes()

            return True

        if self.no_rect.collidepoint(
            position
        ):

            choice_system.choose_no()

            return True

        # 选择框存在期间，
        # 点击其他地方全部忽略。
        return True


    def draw(
        self,
        choice_system
    ):

        if not choice_system.active:
            return

        request = choice_system.current

        # ==================================================
        # 半透明背景
        # ==================================================

        overlay = pygame.Surface(
            self.screen.get_size(),
            pygame.SRCALPHA
        )

        overlay.fill(
            (
                0,
                0,
                0,
                105
            )
        )

        self.screen.blit(
            overlay,
            (
                0,
                0
            )
        )

        # ==================================================
        # 主面板
        # ==================================================

        pygame.draw.rect(
            self.screen,
            (
                58,
                58,
                58
            ),
            self.panel_rect,
            border_radius=12
        )

        pygame.draw.rect(
            self.screen,
            (
                225,
                190,
                120
            ),
            self.panel_rect,
            3,
            border_radius=12
        )

        # ==================================================
        # 标题
        # ==================================================

        title = self.title_font.render(
            request.title,
            True,
            (
                255,
                225,
                150
            )
        )

        self.screen.blit(
            title,
            title.get_rect(
                center=(
                    500,
                    335
                )
            )
        )

        # ==================================================
        # 提示
        # ==================================================

        prompt = self.body_font.render(
            request.prompt,
            True,
            (
                245,
                245,
                245
            )
        )

        self.screen.blit(
            prompt,
            prompt.get_rect(
                center=(
                    500,
                    382
                )
            )
        )

        # ==================================================
        # 发动按钮
        # ==================================================

        pygame.draw.rect(
            self.screen,
            (
                225,
                180,
                80
            ),
            self.yes_rect,
            border_radius=8
        )

        # ==================================================
        # 不发动按钮
        # ==================================================

        pygame.draw.rect(
            self.screen,
            (
                150,
                150,
                150
            ),
            self.no_rect,
            border_radius=8
        )

        yes_text = self.body_font.render(
            request.yes_label,
            True,
            (
                20,
                20,
                20
            )
        )

        no_text = self.body_font.render(
            request.no_label,
            True,
            (
                20,
                20,
                20
            )
        )

        self.screen.blit(
            yes_text,
            yes_text.get_rect(
                center=self.yes_rect.center
            )
        )

        self.screen.blit(
            no_text,
            no_text.get_rect(
                center=self.no_rect.center
            )
        )