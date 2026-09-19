"""Dark-ink table theme: colours, fonts, and cached decoration surfaces.

Everything visual in the game reads from here so the palette stays coherent
and no surface is rebuilt per frame.
"""

import os
from functools import lru_cache

import pygame


# ==================================================
# 颜色
# ==================================================

# 桌面与面板
BG_DEEP = (14, 19, 26)
BG_TABLE = (23, 33, 44)
BG_TABLE_EDGE = (12, 16, 22)
PANEL = (35, 51, 66)
PANEL_ALT = (48, 70, 90)
PANEL_DEEP = (24, 35, 46)
PANEL_SUNKEN = (18, 27, 36)

# 强调
GOLD = (211, 176, 92)
GOLD_BRIGHT = (246, 215, 130)
GOLD_DIM = (138, 114, 60)
INK = (16, 20, 26)

# 文本
TEXT = (234, 239, 244)
TEXT_DIM = (156, 170, 184)
TEXT_MUTED = (104, 118, 132)

# 状态
TARGET_BLUE = (86, 172, 245)
TARGET_YELLOW = (245, 205, 70)
DANGER = (200, 74, 62)
DANGER_DEEP = (116, 44, 40)
HEAL = (98, 198, 122)
CHAIN = (146, 176, 232)
DEAD_TINT = (62, 70, 80)
DISABLED_FILL = (70, 80, 90)
DISABLED_TEXT = (128, 138, 148)

# 卡牌
CARD_FACE = {
    "basic": (240, 234, 216),
    "trick": (223, 229, 238),
    "equipment": (238, 226, 196),
}
CARD_FACE_FALLBACK = (236, 232, 220)
CARD_BORDER = (96, 88, 74)
CARD_EDGE_LIGHT = (255, 250, 236)
CARD_RED = (186, 46, 52)
CARD_BLACK = (38, 40, 46)
CARD_BACK_DARK = (86, 30, 30)
CARD_BACK_LIGHT = (128, 46, 42)
CARD_BACK_LINE = (206, 170, 96)
CARD_SHADOW = (10, 13, 18, 120)
CARD_DISABLED = (110, 116, 124)

# 圆角
RADIUS_CARD = 9
RADIUS_PANEL = 12
RADIUS_BUTTON = 9

# 边框
BORDER_THIN = 2
BORDER = 3
BORDER_THICK = 4


# ==================================================
# 字体（缓存，不每帧重建）
# ==================================================

FONT_CANDIDATES = (
    "Microsoft YaHei",
    "Microsoft JhengHei",
    "DengXian",
    "SimHei",
    "SimSun",
    "KaiTi",
    "FangSong",
    "PingFang SC",
    "Hiragino Sans GB",
    "Heiti SC",
    "STHeiti",
    "Arial Unicode MS",
)

SYMBOL_FONT_CANDIDATES = (
    "Segoe UI Symbol",
    "DejaVu Sans",
    "Arial Unicode MS",
    "Microsoft YaHei",
)

FALLBACK_FONT_PATHS = (
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "C:/Windows/Fonts/simsun.ttc",
    "/System/Library/Fonts/PingFang.ttc",
)


@lru_cache(maxsize=None)
def _resolve_font_path(candidates, extra_paths):
    for name in candidates:
        path = pygame.font.match_font(name)
        if path:
            return path
    for path in extra_paths:
        if os.path.exists(path):
            return path
    return None


@lru_cache(maxsize=None)
def load_font(size, *, symbol=False):
    if symbol:
        path = _resolve_font_path(SYMBOL_FONT_CANDIDATES, ())
    else:
        path = _resolve_font_path(FONT_CANDIDATES, FALLBACK_FONT_PATHS)
    return pygame.font.Font(path, size)


# 字号层级
FONT_SIZES = {
    "hero": 68,
    "title": 52,
    "huge": 44,
    "large": 32,
    "normal": 24,
    "small": 19,
    "tiny": 15,
    "micro": 13,
    "card": 26,
    "card_small": 20,
    "card_meta": 13,
    "seat_name": 20,
    "seat_meta": 15,
    "seat_small": 13,
}


class Fonts:
    """Lazily built, cached font set shared by every UI component."""

    def __init__(self):
        self._cache = {}

    def get(self, name):
        font = self._cache.get(name)
        if font is None:
            font = load_font(FONT_SIZES[name])
            self._cache[name] = font
        return font

    def suit(self, size=18):
        key = ("suit", size)
        font = self._cache.get(key)
        if font is None:
            font = load_font(size, symbol=True)
            self._cache[key] = font
        return font


_fonts = Fonts()


def fonts():
    return _fonts


# ==================================================
# 缓存装饰面
# ==================================================

_gradient_cache = {}
_card_back_cache = {}


def vertical_gradient(size, top_color, bottom_color):
    """Cached vertical gradient surface."""

    key = (size, top_color, bottom_color)
    surface = _gradient_cache.get(key)
    if surface is not None:
        return surface

    width, height = size
    surface = pygame.Surface((width, height)).convert()
    for y in range(height):
        ratio = y / max(1, height - 1)
        color = tuple(
            round(top + (bottom - top) * ratio)
            for top, bottom in zip(top_color, bottom_color)
        )
        pygame.draw.line(surface, color, (0, y), (width, y))
    _gradient_cache[key] = surface
    return surface


def radial_glow(radius, color, alpha=140):
    """Cached soft radial glow used for the current-turn halo."""

    key = ("glow", radius, color, alpha)
    surface = _gradient_cache.get(key)
    if surface is not None:
        return surface

    size = radius * 2
    surface = pygame.Surface((size, size), pygame.SRCALPHA)
    for step in range(radius, 0, -1):
        ratio = step / radius
        step_alpha = int(alpha * (1 - ratio) ** 1.6)
        if step_alpha <= 0:
            continue
        pygame.draw.circle(surface, (*color, step_alpha), (radius, radius), step)
    _gradient_cache[key] = surface
    return surface


def card_back_surface(width, height):
    """Procedurally drawn card back: dark red field, gold frame, diamond knot."""

    key = (width, height)
    surface = _card_back_cache.get(key)
    if surface is not None:
        return surface

    surface = pygame.Surface((width, height), pygame.SRCALPHA)
    body = pygame.Rect(0, 0, width, height)
    surface.blit(vertical_gradient((width, height), CARD_BACK_LIGHT, CARD_BACK_DARK), (0, 0))

    inner = body.inflate(-10, -10)
    pygame.draw.rect(surface, CARD_BACK_LINE, inner, 2, border_radius=6)

    # 菱形回纹
    center = body.center
    for scale, width_step in ((0.62, 3), (0.40, 2)):
        half_w = int(width * scale / 2)
        half_h = int(height * scale / 2)
        points = [
            (center[0], center[1] - half_h),
            (center[0] + half_w, center[1]),
            (center[0], center[1] + half_h),
            (center[0] - half_w, center[1]),
        ]
        pygame.draw.polygon(surface, CARD_BACK_LINE, points, width_step)

    pygame.draw.circle(surface, CARD_BACK_LINE, center, max(4, width // 12), 2)
    pygame.draw.rect(surface, (222, 190, 120), body, 3, border_radius=9)

    _card_back_cache[key] = surface
    return surface


def table_surface(width, height):
    """Cached table felt with a subtle vignette."""

    key = ("table", width, height)
    surface = _gradient_cache.get(key)
    if surface is not None:
        return surface

    surface = pygame.Surface((width, height)).convert()
    surface.blit(vertical_gradient((width, height), BG_TABLE, BG_TABLE_EDGE), (0, 0))

    vignette = pygame.Surface((width, height), pygame.SRCALPHA)
    rings = 26
    for step in range(rings):
        ratio = step / rings
        alpha = int(52 * ratio ** 2)
        if alpha <= 0:
            continue
        inset = int(min(width, height) * 0.5 * ratio)
        pygame.draw.rect(
            vignette,
            (0, 0, 0, alpha),
            pygame.Rect(-inset, -inset, width + inset * 2, height + inset * 2),
            width=inset,
        )
    surface.blit(vignette, (0, 0))
    _gradient_cache[key] = surface
    return surface


def clear_caches():
    _gradient_cache.clear()
    _card_back_cache.clear()
