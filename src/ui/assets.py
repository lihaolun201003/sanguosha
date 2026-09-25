"""美术资源层（Asset Registry）：稳定 ID → Surface，带缓存与 fallback。

设计约定
--------
* 渲染层只向这里要 Surface，任何模块都不自己拼 ``assets/`` 路径；路径只在
  本模块的映射表里出现一次。
* 素材目录只读：缩放、适配全部发生在内存里，绝不回写素材文件。
* 资源缺失 / 文件损坏 / 转换失败一律返回 ``None``，由调用方退回程序绘制，
  少一张图不会让整局游戏崩溃。
* 没有 display surface（headless / dummy 测试）时同样可用。
* 原始 Surface 永久缓存；缩放结果按 ``(asset_id, w, h)`` 做有界 LRU 缓存，
  因此窗口反复 resize 也不会无限增长。

映射的权威来源是下面的映射表，不是文件名猜测：``asset_inventory.json`` 只在
初始化时读一次，用于诊断（列出"映射指向了不存在的文件"）。
"""

import json
import os
from collections import OrderedDict

import pygame

from . import theme

ASSETS_DIRNAME = "assets"

# 缩放缓存上限：每种 UI 元素只用一两个尺寸，256 条足够覆盖所有分辨率组合。
MAX_SCALED_ENTRIES = 256
# 竖版容器合成的缓存上限（键里含牌名 / 花色点数，条目比缩放缓存多）。
MAX_PORTRAIT_ENTRIES = 256


# ==================================================
# 稳定 ID → 素材相对路径（唯一权威映射）
# ==================================================

GENERALS_DIR = "generals/cards"
#: 扩展包武将牌素材的根目录（按扩展包分子目录，见 EXPANSION_GENERAL_ASSETS）。
EXPANSION_GENERALS_DIR = "generals/expansions"
GENERAL_BACK = "generals/back/将牌.png"
# 卡牌背面（牌堆封面）：抽牌堆 / 弃牌堆 / 盖着的牌共用同一张素材。
CARD_BACK = "cards/back/卡牌.png"

# general_id → 武将牌正面素材
GENERAL_ASSETS = {
    # 第一批
    "zhangfei": "张飞.png",
    "huangyueying": "黄月英.png",
    "xiahoudun": "夏侯惇.png",
    "caocao": "曹操.png",
    "simayi": "司马懿.png",
    "guojia": "郭嘉.png",
    "zhangliao": "张辽.png",
    "guanyu": "关羽.png",
    "zhaoyun": "赵云.png",
    "zhouyu": "周瑜.png",
    "sunshangxiang": "孙尚香.png",
    # 第二批（Phase 11）
    "liubei": "刘备.png",
    "zhugeliang": "诸葛亮.png",
    "machao": "马超.png",
    "zhenji": "甄姬.png",
    # 素材文件名是「许诸」，规则里的武将名是「许褚」——显式 alias，不改规则名。
    "xuchu": "许诸.png",
    "sunquan": "孙权.png",
    "lvmeng": "吕蒙.png",
    "daqiao": "大乔.png",
    "ganning": "甘宁.png",
    "luxun": "陆逊.png",
    "huanggai": "黄盖.png",
    "huatuo": "华佗.png",
    "lvbu": "吕布.png",
    "diaochan": "貂蝉.png",
}

# 扩展包武将牌正面素材：``generals/expansions/<包>/<稳定 id>.png``。
#
# 这里**必须显式列出**而不是按目录扫描：
#   * 扩展包里有多组同名不同来源的卡面（火包庞德 / SP006 庞德…），
#     目标文件名一律用稳定 id，靠这张表把 id 与文件钉死；
#   * 源文件名有错字（张颌.png 的卡面其实是「张郃」），
#     映射必须写清楚，不能靠文件名猜。
EXPANSION_GENERAL_ASSETS = {
    # ---- 风 ----
    "yuji": "wind/yuji.png",
    "zhoutai": "wind/zhoutai.png",
    "xiahouyuan": "wind/xiahouyuan.png",
    "xiaoqiao": "wind/xiaoqiao.png",
    "zhangjiao": "wind/zhangjiao.png",
    "zhangjiao_2010": "wind/zhangjiao_2010.png",
    "caoren": "wind/caoren.png",
    "caoren_2010": "wind/caoren_2010.png",
    "weiyan": "wind/weiyan.png",
    "huangzhong": "wind/huangzhong.png",
    # ---- 火 ----
    "dianwei": "fire/dianwei.png",
    "wolongzhuge": "fire/wolongzhuge.png",
    "taishici": "fire/taishici.png",
    "pangde": "fire/pangde.png",
    "pangtong": "fire/pangtong.png",
    "xunyu": "fire/xunyu.png",
    "yuanshao": "fire/yuanshao.png",
    "yanliangwenchou": "fire/yanliangwenchou.png",
    # ---- 林 ----
    "sunjian": "forest/sunjian.png",
    "menghuo": "forest/menghuo.png",
    "xuhuang": "forest/xuhuang.png",
    "caopi": "forest/caopi.png",
    "zhurong": "forest/zhurong.png",
    "dongzhuo": "forest/dongzhuo.png",
    "jiaxu": "forest/jiaxu.png",
    "lusu": "forest/lusu.png",
    # ---- 山（张颌.png 是源文件名错字，卡面实为张郃）----
    "liushan": "mountain/liushan.png",
    "jiangwei": "mountain/jiangwei.png",
    "sunce": "mountain/sunce.png",
    "zuoci": "mountain/zuoci.png",
    "zhangzhao_zhanghong": "mountain/zhangzhao_zhanghong.png",
    "zhanghe": "mountain/zhanghe.png",
    "caiwenji": "mountain/caiwenji.png",
    "dengai": "mountain/dengai.png",
    # ---- 神（卡面只印本名，「神」是势力印玺）----
    "shen_guanyu": "god/shen_guanyu.png",
    "shen_simayi": "god/shen_simayi.png",
    "shen_lvbu": "god/shen_lvbu.png",
    "shen_lvmeng": "god/shen_lvmeng.png",
    "shen_zhouyu": "god/shen_zhouyu.png",
    "shen_caocao": "god/shen_caocao.png",
    "shen_zhugeliang": "god/shen_zhugeliang.png",
    "shen_zhaoyun": "god/shen_zhaoyun.png",
    # ---- 一将成名 ----
    "yujin": "yijiang/yujin.png",
    "lingtong": "yijiang/lingtong.png",
    "wuguotai": "yijiang/wuguotai.png",
    "zhangchunhua": "yijiang/zhangchunhua.png",
    "xushu": "yijiang/xushu.png",
    "xusheng": "yijiang/xusheng.png",
    "caozhi": "yijiang/caozhi.png",
    "fazheng": "yijiang/fazheng.png",
    "zhonghui": "yijiang/zhonghui.png",
    "chengong": "yijiang/chengong.png",
    "masu": "yijiang/masu.png",
    "gaoshun": "yijiang/gaoshun.png",
    # ---- SP ----
    "sp_yangxiu": "sp/sp_yangxiu.png",
    "sp_diaochan": "sp/sp_diaochan.png",
    "sp_gongsunzan": "sp/sp_gongsunzan.png",
    "sp_sunshangxiang": "sp/sp_sunshangxiang.png",
    "sp_pangde": "sp/sp_pangde.png",
    "sp_guanyu": "sp/sp_guanyu.png",
    "sp_lvbu_myth": "sp/sp_lvbu_myth.png",
    "sp_lvbu_wrath": "sp/sp_lvbu_wrath.png",
    "sp_caiwenji": "sp/sp_caiwenji.png",
    "sp_machao": "sp/sp_machao.png",
    "sp_jiaxu": "sp/sp_jiaxu.png",
    "sp_yuanshu": "sp/sp_yuanshu.png",
}

#: 同一武将的**备用卡面**（保留、可显式取用，但不默认使用）。
#:
#: 一将成名目录里的徐庶有两张卡：一张是常见的蓝色卡框，另一张是红色卡框。
#: 两张卡的左上角势力标记都是「蜀」，技能名与正文逐字相同，规则上是同一名
#: 武将——因此登记成同一武将的两张卡面，而不是两名武将。
GENERAL_ALTERNATE_ASSETS = {
    "xushu": ("yijiang/xushu_alt.png",),
}

# 身份牌：用 Identity 枚举的 value（稳定字符串）作 key
IDENTITY_ASSETS = {
    "lord": "identities/主公.png",
    "loyalist": "identities/忠臣.png",
    "rebel": "identities/反贼.png",
    "renegade": "identities/内奸.png",
}

# 卡牌：Card.name → 卡面素材
CARD_ASSETS = {
    # 基本牌
    "SHA": "cards/standard/basic/杀.png",
    "SHAN": "cards/standard/basic/闪.png",
    "TAO": "cards/standard/basic/桃.png",
    "JIU": "cards/junzheng/酒.png",
    # 锦囊
    "WUZHONG": "cards/standard/tricks/无中生有.png",
    "GUOHE": "cards/standard/tricks/过河拆桥.png",
    "SHUNSHOU": "cards/standard/tricks/顺手牵羊.png",
    "JUEDOU": "cards/standard/tricks/决斗.png",
    "NANMAN": "cards/standard/tricks/南蛮入侵.png",
    "WANJIAN": "cards/standard/tricks/万箭齐发.png",
    "TAOYUAN": "cards/standard/tricks/桃园结义.png",
    "WUGU": "cards/standard/tricks/五谷丰登.png",
    "WUXIE": "cards/standard/tricks/无懈可击.png",
    "JIEDAO": "cards/standard/tricks/借刀杀人.png",
    "LEBU": "cards/standard/tricks/乐不思蜀.png",
    "SHANDIAN": "cards/ex/闪电.png",
    "HUOGONG": "cards/junzheng/火攻.png",
    "TIESUO": "cards/junzheng/铁索连环.png",
    "BINGLIANG": "cards/junzheng/兵粮寸断.png",
    # 装备（注意：两张牌的游戏名与素材文件名不同，见下方别名）
    "ZHUGE": "cards/standard/weapons/诸葛连弩.png",
    "CIXIONG": "cards/standard/weapons/雌雄双剑.png",
    "HANBING": "cards/ex/寒冰剑.png",
    "QINGGANG": "cards/standard/weapons/青钢剑.png",
    "GUDING": "cards/junzheng/古锭刀.png",
    "QINGLONG": "cards/standard/weapons/青龙偃月刀.png",
    "ZHANGBA": "cards/standard/weapons/丈八蛇矛.png",
    "GUANSHI": "cards/standard/weapons/贯石斧.png",
    "FANGTIAN": "cards/standard/weapons/方天画戟.png",
    "ZHUQUE": "cards/junzheng/朱雀羽扇.png",
    "QILIN": "cards/standard/weapons/麒麟弓.png",
    "BAGUA": "cards/standard/armor/八卦阵.png",
    "RENWANG": "cards/ex/仁王盾.png",
    "TENGJIA": "cards/junzheng/藤甲.png",
    "BAIYIN": "cards/junzheng/白银狮子.png",
    "JUEYING": "cards/standard/horses/绝影.png",
    "DILU": "cards/standard/horses/的卢.png",
    "ZHAOHUANG": "cards/standard/horses/爪黄飞电.png",
    "CHITU": "cards/standard/horses/赤兔.png",
    "DAWAN": "cards/standard/horses/大宛.png",
    "ZIXING": "cards/standard/horses/紫骍.png",
    # 骅骝（HUALIU）没有素材：保持缺省，由调用方 fallback 到程序绘制。
    # SP010 银月枪：扩展装备牌（武器，方块 Q，攻击范围 3）。
    "YINYUEQIANG": "cards/expansion/sp_yinyueqiang.png",
}

# 属性变体：【杀】的火 / 雷版本使用军争篇卡面。
CARD_NATURE_ASSETS = {
    ("SHA", "fire"): "cards/junzheng/火杀.png",
    ("SHA", "thunder"): "cards/junzheng/雷杀.png",
}

# 多版本素材：第一版只用 default，备用版本保留在库里、可显式取用。
# 闪电：唯一一张完整卡面是 EX 版（420×572），misc 版只是插画局部。
# 无懈可击：标准版卡面语义正确（EX 版卡面印着 EX 标记，会误导玩家）。
CARD_ALTERNATE_ASSETS = {
    "SHANDIAN": ("cards/misc/闪电.png",),
    "WUXIE": ("cards/ex/无懈.png",),
}

# ==================================================
# 横版素材的竖版化
# ==================================================
#
# 素材里有一批横版卡面（延时锦囊横置形态、插画局部）。
# 处理方式只有两种，**都不修改原文件**：
#
#   rotate_cw / rotate_ccw  图片本身是竖版卡牌被转存成横版，转回来即可
#   frame                   内容本身就是横版构图 —— 放进竖版容器，不旋转
#
# 判定依据是"旋转后文字能否正读"：实测 乐不思蜀 / 兵粮寸断 的牌名是
# **竖排且字正立**的横版设计（延时锦囊横置形态），旋转 90° 会让文字侧躺，
# 因此走 frame。没有显式标注的横版素材一律走 frame（最安全，永不变形）。

LANDSCAPE_MODES = {
    "card:LEBU": "frame",
    "card:BINGLIANG": "frame",
}

DEFAULT_LANDSCAPE_MODE = "frame"


# ==================================================
# 资源 ID 构造
# ==================================================

def general_asset_id(general_id):
    return "general:" + str(general_id)


def general_back_asset_id():
    return "general_back"


def card_back_asset_id():
    """牌背（牌堆封面）的稳定 ID。"""

    return "card_back"


def identity_asset_id(identity):
    value = getattr(identity, "value", identity)
    return "identity:" + str(value)


def card_asset_id(card):
    """一张实体牌对应的资源 ID；同名不同属性（火杀 / 雷杀）分开。"""

    name = getattr(card, "name", None)
    if name is None:
        return None
    nature = getattr(card, "nature", "normal") or "normal"
    if (name, nature) in CARD_NATURE_ASSETS:
        return "card:%s:%s" % (name, nature)
    return "card:" + str(name)


# ==================================================
# 定位
# ==================================================

def default_assets_root():
    """项目根目录下的 ``assets/``；与本模块位置无关地稳定解析。"""

    here = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(os.path.dirname(here))
    return os.path.join(project_root, ASSETS_DIRNAME)


def fit_contain(rect, source_size, *, align="center"):
    """在 ``rect`` 内按原始比例放入 ``source_size``，返回目标矩形。

    不拉伸、不变形；图片比 rect 小时也不会被放大到超出 rect。
    """

    rect = pygame.Rect(rect)
    width, height = source_size
    if width <= 0 or height <= 0:
        return pygame.Rect(rect)
    scale = min(rect.width / float(width), rect.height / float(height))
    target_w = max(1, int(round(width * scale)))
    target_h = max(1, int(round(height * scale)))
    if align == "midtop":
        x = rect.x + (rect.width - target_w) // 2
        y = rect.y
    elif align == "midbottom":
        x = rect.x + (rect.width - target_w) // 2
        y = rect.bottom - target_h
    else:
        x = rect.x + (rect.width - target_w) // 2
        y = rect.y + (rect.height - target_h) // 2
    return pygame.Rect(x, y, target_w, target_h)


def landscape_mode(asset_id):
    """该横版素材的竖版化方式。"""

    return LANDSCAPE_MODES.get(asset_id, DEFAULT_LANDSCAPE_MODE)


def portrait_composite(source, size, *, title="", category=""):
    """把横版素材合成进竖版卡位（运行时生成，不写任何文件）。

    结构：顶部给花色点数徽标让位 → 中间横版原图（保持比例、居中、尽量放大）
    → 下方牌名 → 底部类型。花色与点数不画在这里：它们由卡牌层用真实 Card
    数据画在最上层，避免与容器里的文字抢位置。
    """

    width, height = max(1, int(size[0])), max(1, int(size[1]))
    canvas = pygame.Surface((width, height), pygame.SRCALPHA)
    body = canvas.get_rect()

    # 底色沿用程序绘制卡面，保证与竖版卡牌同一套视觉。
    pygame.draw.rect(canvas, theme.CARD_FACE_FALLBACK, body,
                     border_radius=theme.RADIUS_CARD)

    # 太小的地方（判定区标签、装备图标位）塞不下文字：只保留居中的原图，
    # 仍然是一张竖版卡，不会退化成横条。
    compact = height < 52 or width < 36
    pad = max(1 if compact else 3, width // 20)

    if compact:
        art_rect = body.inflate(-pad * 2, -pad * 2)
        target = fit_contain(art_rect, source.get_size())
        canvas.blit(pygame.transform.smoothscale(source, target.size), target.topleft)
        return canvas

    pygame.draw.rect(canvas, (255, 255, 255, 42), body.inflate(-6, -6),
                     1, border_radius=6)

    badge_reserve = max(6, int(height * 0.175))   # 左上角花色点数徽标的位置
    title_height = max(11, int(height * 0.105)) if title else 0
    tag_height = max(9, int(height * 0.082)) if category else 0

    art_rect = pygame.Rect(
        body.x + pad,
        body.y + pad + badge_reserve,
        max(1, width - pad * 2),
        max(1, height - pad * 2 - badge_reserve - title_height - tag_height),
    )
    target = fit_contain(art_rect, source.get_size())
    scaled = pygame.transform.smoothscale(source, target.size)
    canvas.blit(scaled, target.topleft)
    pygame.draw.rect(canvas, theme.CARD_BORDER, target.inflate(2, 2), 1, border_radius=3)

    if title:
        font_size = max(10, int(title_height * 0.74))
        font = theme.load_font(font_size)
        rendered = font.render(str(title), True, theme.INK)
        max_width = width - pad * 2
        if rendered.get_width() > max_width:
            shrink = max(9, int(font_size * max_width / float(rendered.get_width())))
            rendered = theme.load_font(shrink).render(str(title), True, theme.INK)
        title_y = body.bottom - pad - tag_height - title_height // 2
        canvas.blit(rendered, rendered.get_rect(center=(body.centerx, title_y)))

    if category:
        font = theme.load_font(max(9, int(tag_height * 0.72)))
        rendered = font.render(str(category), True, theme.CARD_BORDER)
        canvas.blit(rendered, rendered.get_rect(
            center=(body.centerx, body.bottom - pad - tag_height // 2)))
    return canvas


# ==================================================
# Registry
# ==================================================

class AssetRegistry:
    """资源定位 + 缓存 + fallback。

    任何取图接口都可能返回 ``None``（资源不在库里或读取失败），调用方必须
    准备好退回程序绘制。
    """

    def __init__(self, root=None, *, enabled=True):
        self.root = root or default_assets_root()
        self.enabled = bool(enabled)
        self._original = {}          # asset_id -> Surface | None
        self._scaled = OrderedDict()  # (asset_id, w, h) -> Surface
        self._portrait = OrderedDict()  # 竖版容器合成结果
        self._missing = set()        # asset_id：已知没有对应文件
        self._warned = set()         # 只警告一次，避免刷日志
        self._inventory_paths = None
        self._load_count = 0         # 真正读过磁盘的次数（测试用）
        self._scale_count = 0
        self._portrait_count = 0
        self._cache_hits = 0

    # ---- 路径解析 ----

    def path_for(self, asset_id):
        """资源 ID → 绝对路径；没有映射时返回 None。"""

        if asset_id == general_back_asset_id():
            return os.path.join(self.root, *GENERAL_BACK.split("/"))
        if asset_id == card_back_asset_id():
            return os.path.join(self.root, *CARD_BACK.split("/"))
        if asset_id.startswith("general:"):
            general_id = asset_id.split(":", 1)[1]
            name = GENERAL_ASSETS.get(general_id)
            if name:
                return os.path.join(self.root, *GENERALS_DIR.split("/"), name)
            expansion = EXPANSION_GENERAL_ASSETS.get(general_id)
            if expansion:
                return os.path.join(
                    self.root, *EXPANSION_GENERALS_DIR.split("/"), *expansion.split("/"))
            return None
        if asset_id.startswith("identity:"):
            rel = IDENTITY_ASSETS.get(asset_id.split(":", 1)[1])
            return os.path.join(self.root, *rel.split("/")) if rel else None
        if asset_id.startswith("card:"):
            rel = self._card_asset_path(asset_id)
            return os.path.join(self.root, *rel.split("/")) if rel else None
        return None

    @staticmethod
    def _card_asset_path(asset_id):
        parts = asset_id.split(":")
        if len(parts) == 3:
            return CARD_NATURE_ASSETS.get((parts[1], parts[2]))
        if len(parts) == 2:
            return CARD_ASSETS.get(parts[1])
        return None

    def alternate_paths(self, asset_id):
        """同一资源 ID 的备用素材（多版本，不删除也不默认使用）。"""

        if asset_id.startswith("card:"):
            name = asset_id.split(":")[1]
            return tuple(
                os.path.join(self.root, *rel.split("/"))
                for rel in CARD_ALTERNATE_ASSETS.get(name, ())
            )
        if asset_id.startswith("general:"):
            general_id = asset_id.split(":", 1)[1]
            return tuple(
                os.path.join(self.root, *EXPANSION_GENERALS_DIR.split("/"), *rel.split("/"))
                for rel in GENERAL_ALTERNATE_ASSETS.get(general_id, ())
            )
        return ()

    def has_asset(self, asset_id):
        path = self.path_for(asset_id)
        return bool(path) and os.path.exists(path)

    # ---- 加载 ----

    def _load_surface(self, path):
        """读一个图片文件；失败返回 None。headless 下也能工作。"""

        try:
            image = pygame.image.load(path)
        except (pygame.error, OSError, FileNotFoundError):
            return None
        self._load_count += 1
        try:
            display = pygame.display.get_surface()
        except pygame.error:
            display = None
        if display is not None:
            try:
                return image.convert_alpha()
            except pygame.error:
                pass
        # 没有 display：保留原始 Surface（可能与显示格式不匹配，但可用）。
        return image

    def surface(self, asset_id):
        """原始尺寸 Surface（永久缓存）；没有资源时返回 None。"""

        if not self.enabled or asset_id is None:
            return None
        if asset_id in self._original:
            cached = self._original[asset_id]
            if cached is not None:
                self._cache_hits += 1
            return cached

        path = self.path_for(asset_id)
        if not path or not os.path.exists(path):
            self._original[asset_id] = None
            self._missing.add(asset_id)
            self._warn_once(asset_id, "素材缺失，退回程序绘制：%s" % asset_id)
            return None

        image = self._load_surface(path)
        if image is None:
            self._original[asset_id] = None
            self._missing.add(asset_id)
            self._warn_once(asset_id, "素材读取失败，退回程序绘制：%s" % path)
            return None

        self._original[asset_id] = image
        return image

    def _warn_once(self, key, message):
        if key in self._warned:
            return
        self._warned.add(key)
        print("[assets] " + message)

    # ---- 缩放 ----

    def scaled(self, asset_id, size):
        """按 ``size`` 缩放；跨帧缓存，重复请求不再 smoothscale。"""

        if not self.enabled or asset_id is None:
            return None
        width = max(1, int(size[0]))
        height = max(1, int(size[1]))
        key = (asset_id, width, height)
        cached = self._scaled.get(key)
        if cached is not None:
            self._cache_hits += 1
            self._scaled.move_to_end(key)
            return cached

        source = self.surface(asset_id)
        if source is None:
            return None
        if source.get_size() == (width, height):
            result = source
        else:
            try:
                result = pygame.transform.smoothscale(source, (width, height))
            except (pygame.error, ValueError):
                return None
        self._scale_count += 1
        self._scaled[key] = result
        while len(self._scaled) > MAX_SCALED_ENTRIES:
            self._scaled.popitem(last=False)
        return result

    def contained(self, asset_id, rect, *, align="center", surface=None):
        """把资源按比例放进 ``rect``；返回 ``(Surface, Rect)`` 或 ``None``。"""

        rect = pygame.Rect(rect)
        source = surface if surface is not None else self.surface(asset_id)
        if source is None:
            return None
        target = fit_contain(rect, source.get_size(), align=align)
        scaled = self.scaled(asset_id, target.size) if surface is None else None
        if scaled is None:
            try:
                scaled = pygame.transform.smoothscale(source, target.size)
            except (pygame.error, ValueError):
                return None
        return scaled, target

    # ---- 方向适配：横版素材 → 竖版卡位 ----

    def is_landscape(self, asset_id):
        source = self.surface(asset_id)
        if source is None:
            return False
        width, height = source.get_size()
        return width > height

    def oriented(self, asset_id, size, *, title="", category=""):
        """返回适配竖版卡位的 Surface（横版素材自动竖版化）。

        - 竖版素材：走普通缩放缓存。
        - 横版素材：按 ``LANDSCAPE_MODES`` 处理，结果进独立的合成缓存，
          **绝不写文件、绝不拉伸**。
        """

        if not self.enabled or asset_id is None:
            return None
        width = max(1, int(size[0]))
        height = max(1, int(size[1]))
        source = self.surface(asset_id)
        if source is None:
            return None
        source_w, source_h = source.get_size()
        if source_w <= source_h:
            return self.scaled(asset_id, (width, height))

        mode = landscape_mode(asset_id)
        title = str(title or "")
        category = str(category or "")
        key = (asset_id, width, height, mode, title, category)
        cached = self._portrait.get(key)
        if cached is not None:
            self._cache_hits += 1
            self._portrait.move_to_end(key)
            return cached

        try:
            if mode in ("rotate_cw", "rotate_ccw"):
                angle = -90 if mode == "rotate_cw" else 90
                rotated = pygame.transform.rotate(source, angle)
                canvas = pygame.Surface((width, height), pygame.SRCALPHA)
                target = fit_contain(canvas.get_rect(), rotated.get_size())
                canvas.blit(
                    pygame.transform.smoothscale(rotated, target.size), target.topleft)
                result = canvas
            else:
                result = portrait_composite(source, (width, height),
                                            title=title, category=category)
        except (pygame.error, ValueError):
            return None

        self._portrait_count += 1
        self._portrait[key] = result
        while len(self._portrait) > MAX_PORTRAIT_ENTRIES:
            self._portrait.popitem(last=False)
        return result

    # ---- 语义接口 ----

    def general_card(self, general_id, size=None):
        return self._maybe_scaled(general_asset_id(general_id), size)

    def general_back(self, size=None):
        return self._maybe_scaled(general_back_asset_id(), size)

    def identity_card(self, identity, size=None):
        if identity is None:
            return None
        return self._maybe_scaled(identity_asset_id(identity), size)

    def card_art(self, card, size=None):
        asset_id = card_asset_id(card)
        if asset_id is None:
            return None
        return self._maybe_scaled(asset_id, size)

    def _maybe_scaled(self, asset_id, size):
        if size is None:
            return self.surface(asset_id)
        return self.scaled(asset_id, size)

    # ---- 诊断 / 测试 ----

    def missing(self):
        """已知没有素材的资源 ID（稳定排序，便于断言）。"""

        return tuple(sorted(self._missing))

    def clear_cache(self):
        self._scaled.clear()
        self._portrait.clear()

    def inventory(self):
        """读一次 ``asset_inventory.json``（只读一次，之后走内存缓存）。"""

        if self._inventory_paths is not None:
            return self._inventory_paths
        path = os.path.join(self.root, "asset_inventory.json")
        paths = set()
        try:
            with open(path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            for item in payload.get("items", ()):
                rel = item.get("relative_path")
                if rel:
                    paths.add(rel.replace("\\", "/"))
        except (OSError, ValueError):
            paths = set()
        self._inventory_paths = paths
        return paths

    def stats(self):
        return {
            "loads": self._load_count,
            "scales": self._scale_count,
            "portraits": self._portrait_count,
            "hits": self._cache_hits,
            "originals": len(self._original),
            "scaled_entries": len(self._scaled),
            "portrait_entries": len(self._portrait),
            "missing": len(self._missing),
        }

    def reset_counters(self):
        self._load_count = 0
        self._scale_count = 0
        self._portrait_count = 0
        self._cache_hits = 0


# ==================================================
# 进程级默认实例
# ==================================================
#
# UI 组件通过 ``get_registry()`` 拿同一个实例，这样一次加载全进程复用。
# 测试可以构造自己的实例（可注入 root / 关闭），互不干扰。

_default_registry = None


def get_registry():
    global _default_registry
    if _default_registry is None:
        _default_registry = AssetRegistry()
    return _default_registry


def set_registry(registry):
    """替换进程级实例（测试用）。"""

    global _default_registry
    _default_registry = registry
    return registry


def reset_registry():
    global _default_registry
    _default_registry = None
