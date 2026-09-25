"""General catalog: the standard-version generals shipped so far.

第一批 11 名是 Phase 8 的最小集合，第二批 14 名是 Phase 11 补齐的标准版武将，
两批都属于 ``DEFAULT_PACK``（标准版）；技能逻辑一律不写在这里，只声明
``skill_ids``，由 ``SkillRegistry`` 解析。

**后续新增扩展包（风 / 火 / 林 / 山、神将、一将成名、SP 等）**：

1. 在下面的文件里照常定义 ``GeneralDef``，写上 ``pack="风"``；
2. 同名不同版本（例如"界关羽"与"关羽"）写 ``version="界限突破"``；
3. 规则还没落地的条目写 ``implemented=False, unavailable_reason="…"``——
   界面上照样看得到，但会被明确禁止开局，不会被悄悄换掉；
4. 加进 ``STANDARD_GENERALS``（或新的包元组）并 ``register_all`` 即可。

界面（1v1 测试的设置页）完全从注册表推导包名、势力、版本标签与可用状态，
因此上面四步之外不需要再改任何一行 UI 代码。
"""

from .definitions import GeneralDef

ZHANGFEI = GeneralDef(
    id="zhangfei",
    name="张飞",
    kingdom="shu",
    gender="male",
    max_hp=4,
    skill_ids=("paoxiao",),
    title="万夫不当之勇",
    description="锁定技【咆哮】：出牌阶段使用【杀】无次数限制。",
)

HUANGYUEYING = GeneralDef(
    id="huangyueying",
    name="黄月英",
    kingdom="shu",
    gender="female",
    max_hp=3,
    skill_ids=("jizhi", "qicai"),
    title="归隐的贤内助",
    description="【集智】：使用锦囊牌时摸一张牌。【奇才】：锁定技，使用锦囊牌无距离限制。",
)

XIAHOUDUN = GeneralDef(
    id="xiahoudun",
    name="夏侯惇",
    kingdom="wei",
    gender="male",
    max_hp=4,
    skill_ids=("ganglie",),
    title="独眼将军",
    description="【刚烈】：受到伤害后判定，结果不为红桃时伤害来源受到 1 点伤害。",
)


CAOCAO = GeneralDef(
    id="caocao",
    name="曹操",
    kingdom="wei",
    gender="male",
    max_hp=4,
    skill_ids=("jianxiong",),
    title="乱世的奸雄",
    description="【奸雄】：当你受到伤害后，你可以获得造成此伤害的牌。",
)

SIMAYI = GeneralDef(
    id="simayi",
    name="司马懿",
    kingdom="wei",
    gender="male",
    max_hp=3,
    skill_ids=("fankui", "guicai"),
    title="狼顾之鬼",
    description="【反馈】：受到伤害后获得伤害来源一张牌。【鬼才】：判定牌生效前可以打出一张手牌代替之。",
)

GUOJIA = GeneralDef(
    id="guojia",
    name="郭嘉",
    kingdom="wei",
    gender="male",
    max_hp=3,
    skill_ids=("tiandu", "yiji"),
    title="早终的先知",
    description="【天妒】：判定牌生效后可获得此牌。【遗计】：受到 1 点伤害后摸两张牌。",
)

ZHANGLIAO = GeneralDef(
    id="zhangliao",
    name="张辽",
    kingdom="wei",
    gender="male",
    max_hp=4,
    skill_ids=("tuxi",),
    title="其疾如风",
    description="【突袭】：摸牌阶段可以放弃摸牌，改为获得至多两名其他角色各一张手牌。",
)

GUANYU = GeneralDef(
    id="guanyu",
    name="关羽",
    kingdom="shu",
    gender="male",
    max_hp=4,
    skill_ids=("wusheng",),
    title="美髯公",
    description="【武圣】：你可以将一张红色牌当【杀】使用或打出。",
)

ZHAOYUN = GeneralDef(
    id="zhaoyun",
    name="赵云",
    kingdom="shu",
    gender="male",
    max_hp=4,
    skill_ids=("longdan",),
    title="虎威将军",
    description="【龙胆】：你可以将【杀】当【闪】、【闪】当【杀】使用或打出。",
)

ZHOUYU = GeneralDef(
    id="zhouyu",
    name="周瑜",
    kingdom="wu",
    gender="male",
    max_hp=3,
    skill_ids=("yingzi", "fanjian"),
    title="美周郎",
    description="【英姿】：锁定技，摸牌阶段多摸一张牌。【反间】：出牌阶段限一次，以一张手牌令目标受伤或弃牌。",
)

SUNSHANGXIANG = GeneralDef(
    id="sunshangxiang",
    name="孙尚香",
    kingdom="wu",
    gender="female",
    max_hp=3,
    skill_ids=("jieyin", "xiaoji"),
    title="弓腰姬",
    description="【结姻】：出牌阶段限一次，弃两张手牌令自己与一名已受伤男性角色各回复 1 点体力。【枭姬】：失去装备牌时摸两张牌。",
)


# ==================================================
# 第二批（Phase 11）：标准版其余 14 名武将
# ==================================================
#
# 主公技（激将 / 救援）也写在 skill_ids 里，但绑定时会由 SkillManager
# 检查"当前角色是不是主公"：FFA 或非主公身份不会获得主公技。

LIUBEI = GeneralDef(
    id="liubei",
    name="刘备",
    kingdom="shu",
    gender="male",
    max_hp=4,
    skill_ids=("rende", "jijiang"),
    title="仁德之君",
    description="【仁德】：出牌阶段你可以将任意数量手牌交给一名其他角色。【激将】：主公技，你可以请蜀势力角色替你出【杀】。",
)

ZHUGE_LIANG = GeneralDef(
    id="zhugeliang",
    name="诸葛亮",
    kingdom="shu",
    gender="male",
    max_hp=3,
    skill_ids=("guanxing", "kongcheng"),
    title="卧龙",
    description="【观星】：准备阶段你可以查看牌堆顶若干张牌。【空城】：锁定技，没有手牌时你不能成为【杀】或【决斗】的目标。",
)

MA_CHAO = GeneralDef(
    id="machao",
    name="马超",
    kingdom="shu",
    gender="male",
    max_hp=4,
    skill_ids=("mashu", "tieji"),
    title="一骑当千",
    description="【马术】：锁定技，你计算与其他角色的距离减一。【铁骑】：使用【杀】指定目标后判定，红色则目标不能使用【闪】。",
)

ZHEN_JI = GeneralDef(
    id="zhenji",
    name="甄姬",
    kingdom="wei",
    gender="female",
    max_hp=3,
    skill_ids=("qingguo", "luoshen"),
    title="洛神",
    description="【倾国】：你可以将一张黑色手牌当【闪】使用或打出。【洛神】：准备阶段判定，黑色则获得该判定牌。",
)

XU_CHU = GeneralDef(
    id="xuchu",
    name="许褚",
    kingdom="wei",
    gender="male",
    max_hp=4,
    skill_ids=("luoyi", "luoyi_boost"),
    title="虎痴",
    description="【裸衣】：摸牌阶段你可以少摸一张牌，若如此做本回合你造成的【杀】/【决斗】伤害 +1。",
)

SUN_QUAN = GeneralDef(
    id="sunquan",
    name="孙权",
    kingdom="wu",
    gender="male",
    max_hp=4,
    skill_ids=("zhiheng", "jiuyuan"),
    title="制衡之主",
    description="【制衡】：出牌阶段限一次，弃任意数量牌并摸等量牌。【救援】：主公技，其他吴势力角色对你使用【桃】时你额外回复 1 点体力。",
)

LV_MENG = GeneralDef(
    id="lvmeng",
    name="吕蒙",
    kingdom="wu",
    gender="male",
    max_hp=4,
    skill_ids=("keji",),
    title="白衣渡江",
    description="【克己】：若你出牌阶段没有使用或打出过【杀】，你可以跳过弃牌阶段。",
)

DA_QIAO = GeneralDef(
    id="daqiao",
    name="大乔",
    kingdom="wu",
    gender="female",
    max_hp=3,
    skill_ids=("guose", "liuli"),
    title="国色天香",
    description="【国色】：你可以将一张方块牌当【乐不思蜀】使用。【流离】：成为【杀】的目标时，你可以弃一张牌将此【杀】转移给攻击范围内的一名其他角色。",
)

GAN_NING = GeneralDef(
    id="ganning",
    name="甘宁",
    kingdom="wu",
    gender="male",
    max_hp=4,
    skill_ids=("qixi",),
    title="锦帆贼",
    description="【奇袭】：你可以将一张黑色牌当【过河拆桥】使用。",
)

LU_XUN = GeneralDef(
    id="luxun",
    name="陆逊",
    kingdom="wu",
    gender="male",
    max_hp=3,
    skill_ids=("qianxun", "lianying"),
    title="儒生",
    description="【谦逊】：锁定技，你不能成为【顺手牵羊】或【乐不思蜀】的目标。【连营】：当你失去最后一张手牌时，你可以摸一张牌。",
)

HUANG_GAI = GeneralDef(
    id="huanggai",
    name="黄盖",
    kingdom="wu",
    gender="male",
    max_hp=4,
    skill_ids=("kurou",),
    title="轻身重义",
    description="【苦肉】：出牌阶段你可以失去 1 点体力，然后摸两张牌。",
)

HUA_TUO = GeneralDef(
    id="huatuo",
    name="华佗",
    kingdom="qun",
    gender="male",
    max_hp=3,
    skill_ids=("jijiu", "qingnang"),
    title="神医",
    description="【急救】：你的回合外，你可以将一张红色牌当【桃】使用。【青囊】：出牌阶段你可以弃一张手牌，令一名已受伤的角色回复 1 点体力。",
)

LV_BU = GeneralDef(
    id="lvbu",
    name="吕布",
    kingdom="qun",
    gender="male",
    max_hp=4,
    skill_ids=("wushuang",),
    title="飞将",
    description="【无双】：锁定技，你使用的【杀】需要两张【闪】才能抵消。",
)

DIAO_CHAN = GeneralDef(
    id="diaochan",
    name="貂蝉",
    kingdom="qun",
    gender="female",
    max_hp=3,
    skill_ids=("lijian", "biyue"),
    title="绝世舞姬",
    description="【离间】：出牌阶段限一次，弃一张手牌令两名男性角色决斗。【闭月】：结束阶段你可以摸一张牌。",
)

SECOND_ROSTER_GENERALS = (
    LIUBEI,
    ZHUGE_LIANG,
    MA_CHAO,
    ZHEN_JI,
    XU_CHU,
    SUN_QUAN,
    LV_MENG,
    DA_QIAO,
    GAN_NING,
    LU_XUN,
    HUANG_GAI,
    HUA_TUO,
    LV_BU,
    DIAO_CHAN,
)


STANDARD_GENERALS = (
    ZHANGFEI,
    HUANGYUEYING,
    XIAHOUDUN,
    CAOCAO,
    SIMAYI,
    GUOJIA,
    ZHANGLIAO,
    GUANYU,
    ZHAOYUN,
    ZHOUYU,
    SUNSHANGXIANG,
) + SECOND_ROSTER_GENERALS
