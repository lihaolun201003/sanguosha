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
# 正在等待这个角色做出响应（引擎把 Pending 派给了他）
RESPONDING = (96, 216, 226)
DANGER = (200, 74, 62)
DANGER_DEEP = (116, 44, 40)
HEAL = (98, 198, 122)
CHAIN = (146, 176, 232)
DEAD_TINT = (62, 70, 80)
DISABLED_FILL = (70, 80, 90)
DISABLED_TEXT = (128, 138, 148)

# 判定结果语义色（Phase 10.5）
#
# 颜色表达的是"这个结果对被判定角色的实际含义"，不是牌的红黑，也不是
# 判定条件的真假。判定展示面板与任何结果文本都从这里取色。
JUDGE_POSITIVE = (108, 214, 138)
JUDGE_NEGATIVE = (232, 106, 92)
JUDGE_NEUTRAL = (176, 190, 206)

JUDGE_TONE_COLORS = {
    "positive": JUDGE_POSITIVE,
    "negative": JUDGE_NEGATIVE,
    "neutral": JUDGE_NEUTRAL,
}


def judge_tone_color(tone):
    """tone（枚举或字符串）→ 语义色；未知 tone 用中性色。"""

    value = getattr(tone, "value", tone)
    return JUDGE_TONE_COLORS.get(value, JUDGE_NEUTRAL)


# 局域网大厅的玩家状态色（Phase 11.1）
#
# 键与 LobbyPlayer 的状态语义一一对应，座位行 / 玩家列表只从这里取色，
# 组件不再自己配颜色。
LOBBY_STATUS_COLORS = {
    "host": GOLD_BRIGHT,
    "ready": HEAL,
    "waiting": TEXT_DIM,
    "offline": DANGER,
}


def lobby_status_color(label):
    """大厅状态文案（"房主" / "已准备" / "未准备" / "已掉线"）→ 语义色。"""

    return LOBBY_STATUS_COLORS.get({
        "房主": "host",
        "已准备": "ready",
        "未准备": "waiting",
        "已掉线": "offline",
    }.get(str(label), "waiting"), TEXT_DIM)

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
# 视觉状态（高亮的唯一参数来源）
# ==================================================
#
# 组件不再自己写 magic number 或复制颜色：所有 hover / selected / 目标高亮
# 都从这里取参数。每个状态给出：
#
#     border        描边颜色
#     width         描边宽度
#     glow          外发光颜色（None = 不发光）
#     glow_width    外发光层数（0 = 不发光）
#     dim           整体压暗的 alpha（0 = 不压暗）
#     label         状态角标文案（None = 不显示）
#
# 强度层次刻意拉开：合法目标 < hover < 选中，保证一眼能分辨。

VISUAL_STATES = {
    # 中性：什么都没发生
    "none": {
        "border": GOLD_DIM, "width": BORDER, "glow": None, "glow_width": 0,
        "dim": 0, "label": None,
    },
    # 鼠标悬停：亮边 + 轻微发光
    "hover": {
        "border": (120, 196, 255), "width": 4, "glow": TARGET_BLUE,
        "glow_width": 4, "dim": 0, "label": None,
    },
    # 已选中（手牌 / source / 费用牌）：最粗 + 双层发光 + 角标
    "selected": {
        "border": TARGET_YELLOW, "width": 6, "glow": TARGET_YELLOW,
        "glow_width": 8, "dim": 0, "label": "已选",
    },
    # 合法目标：整块 seat 外圈发光
    "valid_target": {
        "border": (108, 186, 255), "width": 4, "glow": TARGET_BLUE,
        "glow_width": 7, "dim": 0, "label": None,
    },
    # 合法目标 + 鼠标悬停：更强一档
    "valid_target_hover": {
        "border": (156, 214, 255), "width": 5, "glow": (130, 200, 255),
        "glow_width": 11, "dim": 0, "label": "可选",
    },
    # 已选中的目标：最强
    "selected_target": {
        "border": TARGET_YELLOW, "width": 6, "glow": TARGET_YELLOW,
        "glow_width": 12, "dim": 0, "label": "目标",
    },
    # 非法目标：轻微压暗，仍然看得清
    "invalid_target": {
        "border": (86, 94, 104), "width": BORDER_THIN, "glow": None,
        "glow_width": 0, "dim": 92, "label": None,
    },
    # 当前回合角色：常驻金色外圈 + 角标
    "current_turn": {
        "border": GOLD_BRIGHT, "width": 5, "glow": GOLD, "glow_width": 8,
        "dim": 0, "label": "当前回合",
    },
    # 引擎正在等这个角色响应
    "pending_response": {
        "border": RESPONDING, "width": 5, "glow": RESPONDING,
        "glow_width": 9, "dim": 0, "label": "响应中",
    },
    # 可响应 / 可出牌的手牌
    "playable": {
        "border": (120, 206, 150), "width": 3, "glow": (98, 198, 122),
        "glow_width": 3, "dim": 0, "label": None,
    },
    # 技能的合法 source 牌
    "view_as_candidate": {
        "border": (120, 196, 255), "width": 3, "glow": TARGET_BLUE,
        "glow_width": 4, "dim": 0, "label": None,
    },
    # 已选为 source 的牌
    "view_as_source": {
        "border": TARGET_BLUE, "width": 6, "glow": TARGET_BLUE,
        "glow_width": 8, "dim": 0, "label": "来源",
    },
    # 不可用：压暗但不隐藏
    "disabled": {
        "border": (96, 104, 112), "width": BORDER_THIN, "glow": None,
        "glow_width": 0, "dim": 148, "label": None,
    },
    # 阵亡
    "dead": {
        "border": (78, 86, 96), "width": BORDER_THIN, "glow": None,
        "glow_width": 0, "dim": 150, "label": "阵亡",
    },
}

# 状态归并优先级：数字越大越优先。
STATE_PRIORITY = {
    # 阵亡最高：尸体不该被"合法目标"之类的状态盖掉。
    "dead": 100,
    "selected": 90,
    "selected_target": 90,
    "view_as_source": 88,
    # Phase 10.5 统一优先级：
    # selected > 正在响应 > 合法目标 > hover > 当前回合 > 普通
    # "引擎正在等这个人回答"比"他可以被选"更紧急，所以排在合法目标之前。
    "pending_response": 80,
    "valid_target_hover": 74,
    "valid_target": 72,
    "hover": 46,
    "current_turn": 44,
    "view_as_candidate": 40,
    "invalid_target": 20,
    "playable": 30,
    "disabled": 15,
    "none": 0,
}


def visual_state(name):
    """取一个视觉状态的全部参数。"""

    return VISUAL_STATES.get(name, VISUAL_STATES["none"])


def resolve_state(*names):
    """按优先级把若干候选状态归并成一个（忽略 None）。"""

    best = None
    best_rank = -1
    for name in names:
        if not name:
            continue
        rank = STATE_PRIORITY.get(name, 0)
        if rank > best_rank:
            best, best_rank = name, rank
    return best or "none"


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
        try:
            path = pygame.font.match_font(name)
        except (TypeError, OSError, ValueError):
            path = None
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


# 字号层级（设计坐标 1600×900 下的像素值；实际渲染时按 LayoutMetrics.scale 缩放）
FONT_SIZES = {
    "hero": 72,
    "title": 56,
    "huge": 46,
    "large": 34,
    "normal": 26,
    "small": 21,
    "tiny": 17,
    "micro": 15,
    "card": 30,
    "card_small": 24,
    "card_meta": 15,
    "seat_name": 24,
    "seat_meta": 17,
    "seat_small": 15,
    # 主菜单人数步进器里的数值：它是**配置值**，不是标题，所以比模式按钮
    # （normal）还小一档，更不能和模式名 / 开始游戏（large）抢视线。
    "menu_count": 24,
}


class Fonts:
    """Lazily built font set; cache key includes the resolved pixel size so a
    resolution change reuses fonts instead of piling up new ones."""

    def __init__(self):
        self._cache = {}

    def get(self, name, scale=1.0):
        base = FONT_SIZES[name]
        size = max(9, int(round(base * scale)))
        key = (name, size)
        font = self._cache.get(key)
        if font is None:
            font = load_font(size)
            self._cache[key] = font
        return font

    def suit(self, size):
        size = max(8, int(size))
        key = ("suit", size)
        font = self._cache.get(key)
        if font is None:
            font = load_font(size, symbol=True)
            self._cache[key] = font
        return font

    def cached_sizes(self):
        return tuple(sorted({key[1] for key in self._cache if key[0] != "suit"}))


_fonts = Fonts()


def fonts():
    return _fonts


# ==================================================
# 缓存装饰面
# ==================================================

_gradient_cache = {}
_card_back_cache = {}
_glow_border_cache = {}


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


def glow_border(size, color, width, glow_width, radius, alpha=132):
    """带外发光的圆角描边，按参数缓存（不每帧重建 Surface）。

    返回的 Surface 比 ``size`` 四周各多 ``glow_width`` 像素，调用方按
    ``(x - glow_width, y - glow_width)`` blit 即可，一次 blit 完成"描边 + 发光"。
    """

    box_w = max(2, int(size[0]))
    box_h = max(2, int(size[1]))
    stroke = max(1, int(width))
    halo = max(0, int(glow_width))
    corner = max(0, int(radius))
    color = tuple(color)
    key = (box_w, box_h, color, stroke, halo, corner, int(alpha))
    cached = _glow_border_cache.get(key)
    if cached is not None:
        return cached

    surface = pygame.Surface((box_w + halo * 2, box_h + halo * 2), pygame.SRCALPHA)
    body = pygame.Rect(halo, halo, box_w, box_h)
    # 小卡片上过粗的描边会填满整张图，这里限制最大笔宽。
    limit = max(1, min(box_w, box_h) // 2 - 1)
    for step in range(halo, 0, -1):
        ratio = step / float(halo + 1)
        step_alpha = int(alpha * (1 - ratio) ** 1.6)
        if step_alpha <= 0:
            continue
        pygame.draw.rect(
            surface, (*color, step_alpha), body.inflate(step * 2, step * 2),
            min(stroke + step, limit), border_radius=corner + step,
        )
    pygame.draw.rect(surface, color, body, min(stroke, limit),
                     border_radius=corner)
    _glow_border_cache[key] = surface
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
    _glow_border_cache.clear()
