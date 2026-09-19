import pygame

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
    """Dark modal with two themed choices."""

    def __init__(
        self,
        screen,
        title_font,
        body_font
    ):

        self.screen = screen

        self.title_font = title_font
        self.body_font = body_font

        self.panel_rect = pygame.Rect(280, 280, 440, 200)

        self.yes_rect = pygame.Rect(312, 398, 176, 54)
        self.no_rect = pygame.Rect(512, 398, 176, 54)

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
        choice_system
    ):

        if not choice_system.active:
            return

        request = choice_system.current
        fonts = theme.fonts()

        veil = pygame.Surface(self.screen.get_size(), pygame.SRCALPHA)
        veil.fill((6, 9, 13, 176))
        self.screen.blit(veil, (0, 0))

        draw_panel(
            self.screen,
            self.panel_rect,
            fill=theme.PANEL,
            border=theme.GOLD,
            border_width=theme.BORDER_THICK,
        )

        title = fonts.get("large").render(request.title, True, theme.GOLD_BRIGHT)
        self.screen.blit(title, title.get_rect(center=(self.panel_rect.centerx, self.panel_rect.y + 46)))

        pygame.draw.line(
            self.screen,
            theme.GOLD_DIM,
            (self.panel_rect.x + 48, self.panel_rect.y + 78),
            (self.panel_rect.right - 48, self.panel_rect.y + 78),
            2,
        )

        prompt = fonts.get("small").render(request.prompt[:40], True, theme.TEXT)
        self.screen.blit(prompt, prompt.get_rect(center=(self.panel_rect.centerx, self.panel_rect.y + 106)))

        yes, no = self._buttons(choice_system)
        mouse = pygame.mouse.get_pos()
        yes.draw(self.screen, fonts, mouse)
        no.draw(self.screen, fonts, mouse)
