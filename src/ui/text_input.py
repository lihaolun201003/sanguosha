"""一行文本输入框（昵称 / IP / 端口），联机界面共用。

Pygame 的中文输入要两件事一起做：

* ``pygame.TEXTINPUT`` 才是**真正的输入事件**（走输入法，能打中文）；
* ``KEYDOWN`` 负责控制键（退格）。

只读 ``KEYDOWN.unicode`` 的老写法在中文输入法下会丢字，所以这里两个都接。
光标固定停在末尾：大厅里输入的都是一行短文本，做整段编辑属于过度设计。
"""

import pygame

from . import theme

CARET_BLINK_MS = 520


def _default_accepts(char):
    """默认只接受可见字符，不接受空格（昵称 / 地址里空格没有意义）。"""

    return bool(char) and char.isprintable() and char != " "


def accepts_digits_only(char):
    return char.isdigit()


def accepts_host(char):
    """IPv4 / 主机名：ASCII 字母数字 + ``.`` ``-`` ``:``（允许直接写 IP:端口）。"""

    if char in ".-:":
        return True
    return char.isalnum() and char.isascii()


class TextField:
    """带标签的一行输入框：``[标签]  [值……]``。"""

    def __init__(self, rect=None, *, label="", placeholder="", initial="",
                 max_length=24, accepts=None, placeholder_color=None):
        self.rect = pygame.Rect(rect or (0, 0, 10, 10))
        self.label = label
        self.placeholder = placeholder
        self.text = str(initial)
        self.max_length = int(max_length)
        self.accepts = accepts or _default_accepts
        self.placeholder_color = placeholder_color or theme.TEXT_MUTED
        self.focused = False
        self.label_rect = pygame.Rect(0, 0, 0, 0)
        self.box_rect = pygame.Rect(self.rect)
        self.sync_layout()

    # ==================================================
    # 布局
    # ==================================================

    def set_rect(self, rect, *, label_width=112):
        """重新摆放：标签占左边 label_width（设计像素），其余是输入框。"""

        self.rect = pygame.Rect(rect)
        gap = max(4, int(round(self.rect.height * 0.22)))
        width = self.rect.width if label_width <= 0 else min(
            label_width, max(0, self.rect.width - self.rect.height * 2))
        self.label_rect = pygame.Rect(
            self.rect.x, self.rect.y, max(0, width), self.rect.height)
        self.box_rect = pygame.Rect(
            self.label_rect.right + (gap if width else 0), self.rect.y,
            max(1, self.rect.right - self.label_rect.right - (gap if width else 0)),
            self.rect.height)
        return self

    def sync_layout(self):
        return self.set_rect(self.rect)

    # ==================================================
    # 焦点与内容
    # ==================================================

    def value(self):
        return self.text.strip()

    def set_text(self, text):
        self.text = str(text)[:self.max_length]
        return self

    def focus(self):
        if self.focused:
            return
        self.focused = True
        # 打开输入法事件：不打这一句就收不到 TEXTINPUT（中文打不进来）。
        try:
            pygame.key.start_text_input()
        except (AttributeError, pygame.error):
            pass

    def blur(self):
        if not self.focused:
            return
        self.focused = False
        try:
            pygame.key.stop_text_input()
        except (AttributeError, pygame.error):
            pass

    def hit(self, position):
        return self.box_rect.collidepoint(position) or self.label_rect.collidepoint(position)

    # ==================================================
    # 事件
    # ==================================================

    def handle_event(self, event):
        """返回 True 表示事件已被输入框吃掉。"""

        if event.type == pygame.MOUSEBUTTONDOWN and getattr(event, "button", 1) == 1:
            if self.hit(event.pos):
                self.focus()
            else:
                self.blur()
            return self.hit(event.pos)

        if not self.focused:
            return False

        if event.type == pygame.TEXTINPUT:
            self._append(event.text)
            return True

        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_BACKSPACE:
                self.text = self.text[:-1]
                return True
            if event.key == pygame.K_DELETE:
                self.text = ""
                return True
            # 回车 / Tab / Esc 交给界面处理（提交、切换焦点、返回）。
            return False

        return False

    def _append(self, chunk):
        for char in str(chunk or ""):
            if len(self.text) >= self.max_length:
                break
            if self.accepts(char):
                self.text += char

    # ==================================================
    # 绘制
    # ==================================================

    def draw(self, surface, fonts, metrics):
        mouse = pygame.mouse.get_pos()
        if self.label:
            label_font = fonts.get("normal")
            rendered = label_font.render(self.label, True, theme.TEXT_DIM)
            surface.blit(rendered, rendered.get_rect(
                midright=(self.label_rect.right, self.label_rect.centery)))

        box = self.box_rect
        hovered = box.collidepoint(mouse)
        border = theme.GOLD_BRIGHT if self.focused else (
            theme.GOLD_DIM if hovered else (96, 116, 136))
        pygame.draw.rect(surface, theme.PANEL_SUNKEN, box, border_radius=metrics.px(8))
        pygame.draw.rect(surface, border, box, 2 if self.focused else 1,
                         border_radius=metrics.px(8))

        font = fonts.get("normal")
        pad = metrics.px(12)
        if self.text:
            text = self.text
            color = theme.TEXT
        else:
            text = self.placeholder
            color = self.placeholder_color
        if text:
            rendered = font.render(text, True, color)
            clip = box.inflate(-pad * 2, 0)
            surface.blit(rendered, (clip.x, clip.centery - rendered.get_height() // 2))

        if self.focused and (pygame.time.get_ticks() // CARET_BLINK_MS) % 2 == 0:
            caret_x = self.box_rect.x + pad + font.size(self.text)[0]
            caret_x = min(caret_x, self.box_rect.right - pad)
            top = box.centery - font.get_height() // 2
            pygame.draw.line(surface, theme.GOLD_BRIGHT, (caret_x, top),
                             (caret_x, top + font.get_height()), 2)
