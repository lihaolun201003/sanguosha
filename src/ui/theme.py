"""古风桌游主题：颜色、字体、缓存装饰面。

**全项目只有这一份颜色**：所有界面（牌桌、大厅、菜单、设置、选将、结算）
都从这里取色，组件不许自己写 RGB 字面量。改一处，全场跟着变。

# 唯一色系：暖灰棕 + 暗金 + 玉绿

牌桌最初是冷色调（蓝黑桌面 + 亮金）；选将 / 武将池那两屏换成暖色（低饱和
暖灰棕背景、青铜边、玉绿选中）之后明显更贴近"实体卡牌"的观感，于是暖色系
被提升为**全局默认**：下面的 ``BG_*`` / ``PANEL_*`` / ``GOLD*`` / ``TEXT*``
就是暖色值，而 ``PANEL_WARM*`` / ``BRONZE*`` / ``TEXT_WARM*`` 是同一批颜色的
**别名**（保留旧名字，免得改一堆调用点）。

冷色只留在必须保持辨识度的语义色上（目标 / 响应 / 危险 / 连环），并且都做了
降饱和处理，好跟暖背景待在同一张画面里。
"""

import os
from functools import lru_cache

import pygame


# ==================================================
# 颜色
#
# 层次约定（从深到浅，牌桌与菜单共用同一套刻度）：
#     BG_DEEP < PANEL_SUNKEN < PANEL_DEEP < PANEL < PANEL_ALT
# 深色是"凹下去的底"，浅色是"浮起来的面"，越靠后越抢视线。
# ==================================================

# 桌面与面板
BG_DEEP = (24, 21, 18)
BG_TABLE = (48, 42, 36)
BG_TABLE_EDGE = (30, 26, 22)
PANEL = (56, 48, 41)
PANEL_ALT = (72, 62, 52)
PANEL_DEEP = (40, 34, 29)
PANEL_SUNKEN = (30, 26, 22)

# 强调：暗金 / 青铜（古风卡牌的描边色）
GOLD = (198, 164, 104)
GOLD_BRIGHT = (246, 215, 130)
GOLD_DIM = (120, 96, 58)
INK = (24, 21, 18)

# 文本：暖白 → 暖灰
TEXT = (236, 228, 212)
TEXT_DIM = (186, 172, 150)
TEXT_MUTED = (134, 122, 104)

# 状态
TARGET_BLUE = (96, 168, 208)
TARGET_YELLOW = (245, 205, 70)
# 正在等待这个角色做出响应（引擎把 Pending 派给了他）
RESPONDING = (96, 206, 214)
DANGER = (196, 78, 62)
DANGER_DEEP = (112, 44, 38)
HEAL = (108, 186, 152)
CHAIN = (146, 176, 232)
CHAIN_DIM = (104, 126, 168)
CHAIN_BRIGHT = (188, 212, 252)
DEAD_TINT = (78, 72, 64)
DISABLED_FILL = (74, 66, 58)
DISABLED_TEXT = (132, 122, 106)

# ==================================================
# 阴影 / 遮罩 / 底衬
#
# 这些原来是散落在 20 多个组件里的 RGB 字面量：同一个"面板投影色"在
# widgets / cards / skill_bar 里就有三份不同的值，色调一旦统一就会显出割裂。
# 现在只有一处定义。
# ==================================================

#: 面板 / 按钮的投影色（不带 alpha，绘制时按需加）
SHADOW = (14, 11, 9)
#: 面板顶部的白色高光（带 alpha，让面有厚度）
PANEL_HIGHLIGHT = (255, 255, 255, 14)
#: PANEL_ALT 被按下时的颜色
PANEL_ALT_PRESSED = (60, 51, 43)
#: 弹出层背后的压暗遮罩
VEIL = (10, 8, 6)
#: 文字底衬（卡面下方那种深色小板）
PLATE = (26, 22, 19)
#: 座位面板底板
SEAT_BODY = (50, 43, 37)
#: 座位头像圆底（比底板再深一档）
SEAT_AVATAR = (36, 31, 27)

#: 状态角标（"翻面"一类）的底 / 边 / 字
TAG_BG = (68, 54, 44)
TAG_BORDER = (176, 142, 214)
TAG_TEXT = (226, 210, 246)
#: 中性角标（非异常状态）
TAG_NEUTRAL_BG = (62, 54, 46)
TAG_NEUTRAL_TEXT = (214, 204, 186)

# ==================================================
# 势力色（武将卡边框 / 图鉴 / 1v1 阵营标识）
#
# 玩家靠颜色认势力，所以**保留色相**，只做降饱和处理，好跟暖色背景待得住。
# 这张表原来在 ``duel_setup`` 与 ``general_cards`` 里各有一份，现在只有这里一处。
# ==================================================

KINGDOM_COLORS = {
    "wei": (96, 118, 168),
    "shu": (176, 88, 74),
    "wu": (86, 148, 122),
    "qun": (140, 132, 108),
    # 神势力：金色（与神将卡的印玺 / 卡框一致）。
    "god": (198, 166, 74),
}
KINGDOM_ORDER = ("wei", "shu", "wu", "qun", "god")


def kingdom_color(kingdom):
    """势力 → 颜色；未知势力用群势力的暖灰。"""

    return KINGDOM_COLORS.get(str(kingdom or ""), KINGDOM_COLORS["qun"])


#: 卡面（暖白纸面）上的文字：正文与红黑花色
CARD_FACE_TEXT = (232, 224, 208)
CARD_FACE_RED = (222, 112, 96)
#: 比 TEXT 更亮的一档（面板标题 / 大字）
TEXT_BRIGHT = (250, 236, 206)

#: 卡面文字 / 边框的中性档（灰化时用）
CARD_MUTED_TEXT = (150, 142, 128)
CARD_EMPTY_BORDER = (98, 88, 76)
CARD_EMPTY_TEXT = (120, 110, 96)

# ==================================================
# 武将池 / 选将界面：暖色系就是上面的默认色板
#
# 这几个名字保留下来，是因为它们最早诞生在选将 / 武将池两屏。现在它们与
# ``PANEL*`` / ``GOLD*`` / ``TEXT*`` 是**同一个值**（别名），改一处两边都变。
# ==================================================

# 背景（从深到浅的三层棕灰）
MENU_BG_TOP = BG_TABLE
MENU_BG_MID = (58, 50, 43)
MENU_BG_BOTTOM = BG_DEEP
#: 背景纹理（程序生成的细颗粒，低对比）
MENU_TEXTURE = (86, 74, 60)

# 暖色面板
PANEL_WARM = PANEL
PANEL_WARM_DEEP = PANEL_DEEP
PANEL_WARM_SUNKEN = PANEL_SUNKEN

# 强调：暗金 / 青铜
BRONZE = (150, 116, 66)
BRONZE_BRIGHT = GOLD
BRONZE_DIM = (104, 82, 50)

# 玉绿（选中）：不要荧光绿
JADE = HEAL
JADE_BRIGHT = (150, 214, 182)
JADE_DIM = (62, 112, 94)

# 暖白文字 / 灰金次要文字
TEXT_WARM = TEXT
TEXT_WARM_DIM = TEXT_DIM
TEXT_WARM_MUTED = TEXT_MUTED

# 危险 / 删除（暗红）
BLOOD = (150, 60, 52)
BLOOD_DIM = (98, 44, 40)

# 判定结果语义色（Phase 10.5）
#
# 颜色表达的是"这个结果对被判定角色的实际含义"，不是牌的红黑，也不是
# 判定条件的真假。判定展示面板与任何结果文本都从这里取色。
JUDGE_POSITIVE = (108, 214, 138)
JUDGE_NEGATIVE = (232, 106, 92)
JUDGE_NEUTRAL = (196, 186, 168)

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
CARD_BORDER = (104, 94, 78)
CARD_EDGE_LIGHT = (255, 250, 236)
CARD_RED = (186, 46, 52)
CARD_BLACK = (38, 40, 46)
CARD_BACK_DARK = (92, 34, 32)
CARD_BACK_LIGHT = (134, 50, 44)
CARD_BACK_LINE = GOLD
CARD_SHADOW = (*SHADOW, 120)
CARD_DISABLED = CARD_MUTED_TEXT

# 圆角
RADIUS_CARD = 9
RADIUS_PANEL = 12
RADIUS_BUTTON = 9

# ==================================================
# 尺寸 token（语义名）
#
# 组件不再写 magic number：圆角只有三档，边框只有两档，间距只有四档。
# 旧的 ``RADIUS_*`` / ``BORDER_*`` 保留为别名，免得改一堆调用点。
# ==================================================

RADIUS_SMALL = 8          # 小徽章 / 装备格 / 内层小框
RADIUS_MEDIUM = RADIUS_CARD   # 卡牌 / 按钮 / 输入框
RADIUS_LARGE = 18         # 演出面板 / 专用交互面板
RADIUS_MODAL = 16         # 模态框与 picker

BORDER_NORMAL = 3
BORDER_STRONG = 4
#: 模态框 / 演出面板的描边
BORDER_MODAL = 3

PANEL_PADDING = 16        # 面板内边距
MODAL_PADDING = 24        # 模态框内边距
CARD_GAP = 10             # 卡牌之间的间距
SECTION_GAP = 18          # 面板内的分区间距

# ---- 语义色别名：写代码时用"用途"而不是"颜色" ----
#
# 这些与上面的颜色是同一个值。新增界面请优先用语义名（``TEXT_SECONDARY``
# 比 ``TEXT_DIM`` 更说明问题），旧名字继续可用。
BACKGROUND = BG_DEEP
TABLE_SURFACE = BG_TABLE
PANEL_ELEVATED = PANEL_ALT
OVERLAY_DIM = VEIL

TEXT_PRIMARY = TEXT
TEXT_SECONDARY = TEXT_DIM

ACCENT = GOLD
WARNING = TARGET_YELLOW
SUCCESS = JADE
DANGER_SOFT = BLOOD

LEGAL = TARGET_BLUE        # 可点 / 合法
ILLEGAL = CARD_MUTED_TEXT  # 不可点 / 非法
SELECTED = TARGET_YELLOW   # 已选中
HOVER = (146, 200, 232)    # 鼠标悬停（见下面的 HOVER_LAYER）
CURRENT_TURN = GOLD_BRIGHT
CURRENT_RESPONDER = RESPONDING
SKILL_ACTIVE = JADE_BRIGHT
#: 判定语义色见 JUDGE_TONE_COLORS

# ---- 字体档位（对应 FONT_SIZES 的键，界面按"用途"取 ----

FONT_TITLE = "title"       # 页面级标题
FONT_MODAL = "large"       # 模态框标题
FONT_SECTION = "normal"    # 面板小标题 / 正文强调
FONT_BODY = "small"        # 正文
FONT_SMALL = "tiny"        # 次要说明
FONT_STATUS = "seat_small"  # 角色状态数字（与 micro 同值）
FONT_CARD = "card"         # 卡面文字
FONT_SKILL = "small"       # 技能说明
FONT_BADGE = "micro"       # 角标
FONT_FALLBACK_MICRO = "micro"  # 缩略图/占位字

# ---- 模态框的统一视觉（所有 picker / 确认框 / 结算框共用）----

MODAL_VEIL_ALPHA = 176     # 遮罩浓度（原来有 168/170/172/176/190 五档）
MODAL_FILL = PANEL
MODAL_BORDER = GOLD
MODAL_WIDTH = BORDER_MODAL
MODAL_RADIUS = RADIUS_MODAL
DEMO_PANEL_VEIL_ALPHA = 150  # 演出面板的遮罩（比模态轻，不全屏压暗）

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
# # 两件必须分开的事：语义状态（互斥）与悬停（正交）
#
# 原来的做法把 hover 也塞进状态归并，于是鼠标一扫就出事：
#
#   * hover(46) 优先级高于 current_turn(44) —— 移到当前回合角色上，
#     金色描边被换成蓝色，**「当前回合」角标直接消失**；
#   * hover(46) 高于 invalid_target(20) —— 移到非法目标上，压暗被取消，
#     那个座位看起来反而像能点。
#
# 现在 hover **不参与归并**：它是一个独立的叠加层（``HOVER_LAYER``），
# 由绘制方用 ``hovered=`` 参数叠在语义状态之上。这样"当前回合 + 鼠标在上面"
# 和"非法目标 + 鼠标在上面"都能同时表达，不再互相洗掉。
#
# 强度层次（从弱到强）：普通 < 悬停叠加 < 合法目标 < 已选目标 < 响应中
#                     < 技能发动 < 濒死 < 阵亡

VISUAL_STATES = {
    # 中性：什么都没发生
    "none": {
        "border": GOLD_DIM, "width": BORDER, "glow": None, "glow_width": 0,
        "dim": 0, "label": None,
    },
    # 中性悬停：**只在没有任何语义状态时**才会被 resolve 选中（优先级最低档）。
    # 真正的悬停反馈由 HOVER_LAYER 叠加，见上面的说明。
    "hover": {
        "border": HOVER, "width": 4, "glow": TARGET_BLUE,
        "glow_width": 4, "dim": 0, "label": None,
    },
    # 已选中（手牌 / source / 费用牌）：最粗 + 双层发光 + 角标
    "selected": {
        "border": TARGET_YELLOW, "width": 6, "glow": TARGET_YELLOW,
        "glow_width": 8, "dim": 0, "label": "已选",
    },
    # 合法目标：整块 seat 外圈发光
    "valid_target": {
        "border": (128, 190, 226), "width": 4, "glow": TARGET_BLUE,
        "glow_width": 7, "dim": 0, "label": None,
    },
    # 合法目标 + 鼠标悬停：更强一档
    "valid_target_hover": {
        "border": (172, 218, 242), "width": 5, "glow": (146, 204, 232),
        "glow_width": 11, "dim": 0, "label": "可选",
    },
    # 已选中的目标：最强
    "selected_target": {
        "border": TARGET_YELLOW, "width": 6, "glow": TARGET_YELLOW,
        "glow_width": 12, "dim": 0, "label": "目标",
    },
    # 非法目标：轻微压暗，仍然看得清
    "invalid_target": {
        "border": (100, 92, 80), "width": BORDER_THIN, "glow": None,
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
        "border": JADE_BRIGHT, "width": 3, "glow": HEAL,
        "glow_width": 3, "dim": 0, "label": None,
    },
    # 技能的合法 source 牌
    "view_as_candidate": {
        "border": (146, 200, 232), "width": 3, "glow": TARGET_BLUE,
        "glow_width": 4, "dim": 0, "label": None,
    },
    # 已选为 source 的牌
    "view_as_source": {
        "border": TARGET_BLUE, "width": 6, "glow": TARGET_BLUE,
        "glow_width": 8, "dim": 0, "label": "来源",
    },
    # 不可用：压暗但不隐藏
    "disabled": {
        "border": (102, 94, 82), "width": BORDER_THIN, "glow": None,
        "glow_width": 0, "dim": 148, "label": None,
    },
    # ---- 武将池 / 选将界面（Phase 19）----
    # 已加入武将池 / 已选中候选：玉绿描边 + 轻微发光
    "pool_selected": {
        "border": JADE, "width": 5, "glow": JADE_DIM, "glow_width": 7,
        "dim": 0, "label": "已加入",
    },
    # 候选里当前聚焦的那一张：暗金描边（比玉绿弱一级，表示"正在看"）
    "draft_focus": {
        "border": BRONZE_BRIGHT, "width": 4, "glow": BRONZE_DIM,
        "glow_width": 6, "dim": 0, "label": None,
    },
    # 武将池里的悬停：更弱一档的暖色高亮
    "pool_hover": {
        "border": BRONZE, "width": 3, "glow": None, "glow_width": 0,
        "dim": 0, "label": None,
    },
    # ---- 角色级：濒死与技能发动 ----

    # 濒死：危险色描边 + 外发光 + 角标。比"响应中"更紧急（人快没了），
    # 但比"阵亡"弱一档（还在救）。
    "dying": {
        "border": DANGER, "width": 6, "glow": DANGER, "glow_width": 12,
        "dim": 0, "label": "濒死",
    },
    # 技能发动：这名角色刚刚发动了技能，武将区短暂高亮。
    # 与"目标/血线"无关，只是让玩家看清"是谁发动了什么"。
    "skill_acting": {
        "border": SKILL_ACTIVE, "width": 5, "glow": JADE, "glow_width": 9,
        "dim": 0, "label": None,
    },

    # ---- 卡牌级：出牌许可与各种候选 ----

    # 本回合**可以打出**的牌：正向强调（以前只有"不能出的变灰"，
    # 玩家得靠排除法才知道哪些能出）。
    "legal_card": {
        "border": JADE_BRIGHT, "width": BORDER, "glow": JADE_DIM,
        "glow_width": 4, "dim": 0, "label": None,
    },
    # 响应窗口里许可打出的牌（闪 / 桃 / 无懈）
    "response_candidate": {
        "border": JADE_BRIGHT, "width": 4, "glow": HEAL,
        "glow_width": 5, "dim": 0, "label": None,
    },
    # 弃牌阶段可按手牌上限弃掉的牌
    "discard_candidate": {
        "border": BRONZE_BRIGHT, "width": 3, "glow": None,
        "glow_width": 0, "dim": 0, "label": None,
    },

    # 阵亡
    "dead": {
        "border": (88, 82, 74), "width": BORDER_THIN, "glow": None,
        "glow_width": 0, "dim": 150, "label": "阵亡",
    },
}

#: 悬停叠加层：与语义状态**正交**，画在语义描边之外再补一圈柔光。
#: 任何元素（座位 / 卡牌 / 技能按钮）都可以叠它，语义状态不受影响。
HOVER_LAYER = {
    "border": HOVER, "width": 3, "glow": TARGET_BLUE, "glow_width": 6,
}

# 状态归并优先级：数字越大越优先。
STATE_PRIORITY = {
    # 阵亡最高：尸体不该被"合法目标"之类的状态盖掉。
    "dead": 100,
    # 武将池 / 选将：选中 > 聚焦 > 悬停
    "pool_selected": 92,
    "draft_focus": 60,
    "pool_hover": 42,
    # 濒死：比任何"操作态"都紧急（这个人马上要死了），但低于阵亡。
    "dying": 118,
    "selected": 90,
    "selected_target": 90,
    "view_as_source": 88,
    # 技能发动：短暂高亮，盖过操作态，好让玩家看清是谁在发动。
    "skill_acting": 86,
    # "引擎正在等这个人回答"比"他可以被选"更紧急，所以排在合法目标之前。
    "pending_response": 84,
    # 当前回合 > 合法目标：目标选择期间也要能看出"谁在行动"，所以
    # 「当前回合」的描边不会被目标高亮顶掉（两者同时需要时以回合为准，
    # 目标信息由角标补足）。
    "current_turn": 82,
    "valid_target_hover": 76,
    "valid_target": 74,
    "legal_card": 62,
    "response_candidate": 60,
    "view_as_candidate": 56,
    "discard_candidate": 54,
    "hover": 50,
    "invalid_target": 30,
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
    pygame.draw.rect(surface, CARD_BACK_LINE, body, 3, border_radius=9)

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


def menu_background(width, height):
    """武将池 / 选将页面的背景（缓存）。

    三层叠出来的"旧纸 + 木质"氛围，**不是一块平色矩形**：

    1. 竖直渐变（深棕 → 暖灰棕 → 更深），整体偏低饱和；
    2. 程序生成的细颗粒纹理（确定性伪随机，低对比，像纸面/木纹颗粒）；
    3. 中心提亮 + 四角压暗（vignette），把视线收到中间。

    全部程序绘制，不引入任何外部素材；同一尺寸只算一次。
    """

    key = ("menu_bg", width, height)
    surface = _gradient_cache.get(key)
    if surface is not None:
        return surface

    surface = vertical_gradient((width, height), MENU_BG_TOP, MENU_BG_BOTTOM)
    # 中段提亮：渐变 + 一条柔和的横向光带
    band = vertical_gradient(
        (width, max(1, height // 2)), MENU_BG_MID, MENU_BG_MID)
    band.set_alpha(120)
    surface.blit(band, (0, height // 4))

    # 纹理：每 3 像素一颗，透明度 3~10，确定性（同一个尺寸每次一样）。
    texture = pygame.Surface((width, height), pygame.SRCALPHA)
    seed = 0x5A17
    for y in range(0, height, 3):
        seed = (seed * 1103515245 + 12345) & 0x7FFFFFFF
        for x in range(0, width, 3):
            seed = (seed * 1103515245 + 12345) & 0x7FFFFFFF
            alpha = 3 + (seed >> 16) % 8
            texture.set_at((x, y), (*MENU_TEXTURE, alpha))
    surface.blit(texture, (0, 0))

    # vignette：四角压暗
    vignette = pygame.Surface((width, height), pygame.SRCALPHA)
    rings = 30
    for step in range(rings):
        ratio = step / rings
        alpha = int(64 * ratio ** 2)
        if alpha <= 0:
            continue
        inset = int(min(width, height) * 0.5 * ratio)
        pygame.draw.rect(
            vignette, (0, 0, 0, alpha),
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
