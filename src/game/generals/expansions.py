"""扩展包武将定义：风 / 火 / 林 / 山 / 神 / 一将成名 / SP。

与标准版一样，这里**只有数据**——技能逻辑一律不写在这个文件里，只声明
``skill_ids``，由 ``SkillRegistry`` 解析。

版本口径
--------
* 风包的张角与曹仁各有两个**规则版本**（2008 初版 / 2010 修订版），卡面
  正文不同、结算也不同，因此登记成两条武将（``version="2008"`` /
  ``version="2010"``），两张卡面都保留。
* 神将卡面只印武将本名（关羽 / 诸葛亮…），「神」是左上角的势力印玺。
  这里用 ``version="神"`` 把展示名变成「关羽（神）」，避免与标准版、
  卧龙诸葛亮混淆。
* SP008 的吕布有两个**形态**（最强神话 / 暴怒的战神），两张卡面都保留，
  都标 ``test_only``：只能在测试模式里显式选择，不进随机池。
* 同名不同来源的武将（火包庞德 / SP006 庞德、山包蔡文姬 / SP009 蔡文姬、
  林包贾诩 / SP012 贾诩…）各自独立登记，**绝不覆盖**任何一条已有定义。

规则来源
--------
技能正文按**对应版本卡面**录入；卡面没写清的时序细则，以该版本官方规则
的措辞为准，并在各自的技能定义 ``description`` 里写明。具体依据见
``docs/reports/phase13_expansion_card_pack_v01.md`` 的逐条映射表。
"""

from .definitions import GeneralDef

# ==================================================
# 风包
# ==================================================

YUJI = GeneralDef(
    id="yuji", name="于吉", kingdom="qun", gender="male", max_hp=3,
    skill_ids=("guhuo",), title="太平道人", pack="风",
    description="【蛊惑】：你可以说出一种基本牌或非延时锦囊牌，将一张手牌正面朝下打出。其他人可以质疑。",
)

ZHOUTAI = GeneralDef(
    id="zhoutai", name="周泰", kingdom="wu", gender="male", max_hp=4,
    skill_ids=("buqu",), title="历战之躯", pack="风",
    description="【不屈】：当你体力降到 0 或更低时，每扣减 1 点体力就翻开一张牌；点数全部不同则你不会死去。",
)

XIAHOUYUAN = GeneralDef(
    id="xiahouyuan", name="夏侯渊", kingdom="wei", gender="male", max_hp=4,
    skill_ids=("shensu",), title="疾行的猎豹", pack="风",
    description="【神速】：回合开始前，你可以选择一至两项：跳过判定与摸牌阶段；跳过出牌阶段并弃一张装备牌。每选择一项，视为对一名角色使用一张【杀】。",
)

XIAOQIAO = GeneralDef(
    id="xiaoqiao", name="小乔", kingdom="wu", gender="female", max_hp=3,
    skill_ids=("tianxiang", "hongyan"), title="娇艳之花", pack="风",
    description="【天香】：受到伤害时，你可以弃一张红桃手牌把伤害转移给一名角色，该角色摸等量于其已损失体力的牌。【红颜】：锁定技，你的黑桃牌视为红桃。",
)

# 张角：2008 初版与 2010 修订版的【雷击】结算完全不同（失去体力 / 造成雷电
# 伤害），因此两条武将各自使用自己版本的技能 id。
ZHANGJIAO = GeneralDef(
    id="zhangjiao", name="张角", kingdom="qun", gender="male", max_hp=3,
    skill_ids=("leiji_old", "guidao", "huangtian"), title="天公将军", pack="风",
    version="2008",
    description="【雷击】：你打出一张【闪】时可令一名角色判定，黑桃则该角色减 2 点体力。",
)

ZHANGJIAO_2010 = GeneralDef(
    id="zhangjiao_2010", name="张角", kingdom="qun", gender="male", max_hp=3,
    skill_ids=("leiji", "guidao", "huangtian"), title="天公将军", pack="风",
    version="2010",
    description="【雷击】：你使用或打出一张【闪】时，可令一名角色判定，黑桃则你对其造成 2 点雷电伤害。",
)

# 曹仁：2008 初版是「跳过你下个回合」，2010 修订版改成「将你的武将牌翻面」。
CAOREN = GeneralDef(
    id="caoren", name="曹仁", kingdom="wei", gender="male", max_hp=4,
    skill_ids=("jushou_old",), title="大将军", pack="风", version="2008",
    description="【据守】：结束阶段，你可以摸三张牌。若如此做，跳过你下个回合。",
)

CAOREN_2010 = GeneralDef(
    id="caoren_2010", name="曹仁", kingdom="wei", gender="male", max_hp=4,
    skill_ids=("jushou",), title="大将军", pack="风", version="2010",
    description="【据守】：结束阶段，你可以摸三张牌。若如此做，将你的武将牌翻面。",
)

WEIYAN = GeneralDef(
    id="weiyan", name="魏延", kingdom="shu", gender="male", max_hp=4,
    skill_ids=("kuanggu",), title="嗜血的狼", pack="风",
    description="【狂骨】：锁定技，你对距离 1 以内的角色每造成 1 点伤害，你回复 1 点体力。",
)

HUANGZHONG = GeneralDef(
    id="huangzhong", name="黄忠", kingdom="shu", gender="male", max_hp=4,
    skill_ids=("liegong",), title="没金饮羽", pack="风",
    description="【烈弓】：出牌阶段，对方手牌数不小于你的体力值、或不大于你的攻击范围时，你的【杀】不可被响应。",
)

WIND_GENERALS = (
    YUJI, ZHOUTAI, XIAHOUYUAN, XIAOQIAO, ZHANGJIAO, ZHANGJIAO_2010,
    CAOREN, CAOREN_2010, WEIYAN, HUANGZHONG,
)


# ==================================================
# 火包
# ==================================================

DIANWEI = GeneralDef(
    id="dianwei", name="典韦", kingdom="wei", gender="male", max_hp=4,
    skill_ids=("qiangxi",), title="古之恶来", pack="火",
    description="【强袭】：出牌阶段限一次，你可以失去 1 点体力或弃一张武器牌，对攻击范围内的一名角色造成 1 点伤害。",
)

WOLONGZHUGE = GeneralDef(
    id="wolongzhuge", name="诸葛亮", kingdom="shu", gender="male", max_hp=3,
    skill_ids=("bazhen", "huoji", "kanpo"), title="卧龙", pack="火",
    version="卧龙",
    description="【八阵】：锁定技，没有防具时视为装备八卦阵。【火计】：可将红色手牌当【火攻】。【看破】：可将黑色手牌当【无懈可击】。",
)

TAISHICI = GeneralDef(
    id="taishici", name="太史慈", kingdom="wu", gender="male", max_hp=4,
    skill_ids=("tianyi",), title="笃烈之士", pack="火",
    description="【天义】：出牌阶段限一次，你可以与一名角色拼点。赢了则本回合攻击范围无限、可额外使用一张【杀】且【杀】可多指定一个目标；没赢则本回合不能使用【杀】。",
)

PANGDE = GeneralDef(
    id="pangde", name="庞德", kingdom="qun", gender="male", max_hp=4,
    skill_ids=("mashu", "mengjin"), title="人马一体", pack="火",
    description="【马术】：锁定技，计算与其他角色的距离减一。【猛进】：你使用的【杀】被【闪】抵消时，可以弃置对方一张牌。",
)

PANGTONG = GeneralDef(
    id="pangtong", name="庞统", kingdom="shu", gender="male", max_hp=3,
    skill_ids=("lianhuan", "niepan"), title="凤雏", pack="火",
    description="【连环】：可将梅花手牌当【铁索连环】使用或重铸。【涅槃】：限定技，濒死时可以弃掉所有牌并重置武将牌，摸三张牌且体力回复至 3 点。",
)

XUNYU = GeneralDef(
    id="xunyu", name="荀彧", kingdom="wei", gender="male", max_hp=3,
    skill_ids=("quhu", "jieming"), title="王佐之才", pack="火",
    description="【驱虎】：出牌阶段限一次，可以与体力比你多的一名角色拼点，赢了令其对其攻击范围内由你指定的一名角色造成 1 点伤害，没赢则他对你造成 1 点伤害。【节命】：受到 1 点伤害后，可令一名角色将手牌补至其体力上限（至多五张）。",
)

YUANSHAO = GeneralDef(
    id="yuanshao", name="袁绍", kingdom="qun", gender="male", max_hp=4,
    skill_ids=("luanji", "xueyi"), title="高贵的名门", pack="火",
    description="【乱击】：出牌阶段，可以将两张相同花色的手牌当【万箭齐发】使用。【血裔】：主公技，锁定技，每有一名其他群势力角色存活，你的手牌上限加二。",
)

YANLIANGWENCHOU = GeneralDef(
    id="yanliangwenchou", name="颜良&文丑", kingdom="qun", gender="male", max_hp=4,
    skill_ids=("shuangxiong",), title="虎狼兄弟", pack="火",
    description="【双雄】：摸牌阶段，你可以放弃摸牌并进行判定，获得该判定牌；本回合可以将与该判定牌颜色不同的手牌当【决斗】使用。",
)

FIRE_GENERALS = (
    DIANWEI, WOLONGZHUGE, TAISHICI, PANGDE, PANGTONG, XUNYU, YUANSHAO,
    YANLIANGWENCHOU,
)


# ==================================================
# 林包
# ==================================================

SUNJIAN = GeneralDef(
    id="sunjian", name="孙坚", kingdom="wu", gender="male", max_hp=4,
    skill_ids=("yinghun",), title="武烈帝", pack="林",
    description="【英魂】：回合开始阶段，若你已受伤，可以令一名其他角色摸 X 张牌并弃一张牌，或摸一张牌并弃 X 张牌（X 为你已损失的体力值）。",
)

MENGHUO = GeneralDef(
    id="menghuo", name="孟获", kingdom="shu", gender="male", max_hp=4,
    skill_ids=("huoshou", "zaiqi"), title="南蛮王", pack="林",
    description="【祸首】：锁定技，【南蛮入侵】对你无效；你是【南蛮入侵】造成伤害的来源。【再起】：摸牌阶段，若你已受伤，可以改为亮出牌堆顶的 X 张牌（X 为已损失体力），其中每张红桃回复 1 点体力后弃置，其余收入手牌。",
)

XUHUANG = GeneralDef(
    id="xuhuang", name="徐晃", kingdom="wei", gender="male", max_hp=4,
    skill_ids=("duanliang",), title="周亚夫之风", pack="林",
    description="【断粮】：你可以将一张黑色基本牌或黑色装备牌当【兵粮寸断】使用；你可以对距离 2 以内的角色使用【兵粮寸断】。",
)

CAOPI = GeneralDef(
    id="caopi", name="曹丕", kingdom="wei", gender="male", max_hp=3,
    skill_ids=("xingshang", "fangzhu", "songwei"), title="魏文帝", pack="林",
    description="【行殇】：你可以立即获得死亡角色的所有牌。【放逐】：你每受到一次伤害，可令一名其他角色摸 X 张牌（X 为你已损失的体力值），然后其武将牌翻面。【颂威】：主公技，其他魏势力角色的判定牌为黑色且生效后，可以让你摸一张牌。",
)

ZHURONG = GeneralDef(
    id="zhurong", name="祝融", kingdom="shu", gender="female", max_hp=4,
    skill_ids=("juxiang", "lieren"), title="野性的女王", pack="林",
    description="【巨象】：锁定技，【南蛮入侵】对你无效；其他角色的【南蛮入侵】结算完毕进入弃牌堆时，你立即获得它。【烈刃】：你使用【杀】造成伤害后，可与受伤角色拼点，赢了获得对方一张牌。",
)

DONGZHUO = GeneralDef(
    id="dongzhuo", name="董卓", kingdom="qun", gender="male", max_hp=8,
    skill_ids=("jiuchi", "roulin", "benguai", "baonue"), title="魔王", pack="林",
    description="【酒池】：可将黑色手牌当【酒】使用。【肉林】：锁定技，对女性角色或女性角色对你使用【杀】时，都需连续使用两张【闪】。【崩坏】：锁定技，结束阶段若你的体力不是全场最少，你须减 1 点体力或体力上限。【暴虐】：主公技，其他群势力角色造成伤害后可判定，黑色则你回复 1 点体力。",
)

JIAXU = GeneralDef(
    id="jiaxu", name="贾诩", kingdom="qun", gender="male", max_hp=3,
    skill_ids=("wansha", "luanwu", "weimu"), title="冷酷的毒士", pack="林",
    description="【完杀】：锁定技，你的回合内，除你以外只有濒死角色才能使用【桃】。【乱武】：限定技，出牌阶段可令所有其他角色对与其距离最近的角色使用一张【杀】，否则失去 1 点体力。【帷幕】：锁定技，你不能成为黑色锦囊牌的目标。",
)

LUSU = GeneralDef(
    id="lusu", name="鲁肃", kingdom="wu", gender="male", max_hp=3,
    skill_ids=("haoshi", "dimeng"), title="独断的外交家", pack="林",
    description="【好施】：摸牌阶段你可以额外摸两张牌；若此时手牌多于五张，须将一半（向下取整）交给手牌最少的一名其他角色。【缔盟】：出牌阶段限一次，你可以弃置等同于两名角色手牌数差的牌，然后交换他们的手牌。",
)

FOREST_GENERALS = (
    SUNJIAN, MENGHUO, XUHUANG, CAOPI, ZHURONG, DONGZHUO, JIAXU, LUSU,
)


# ==================================================
# 山包
# ==================================================

LIUSHAN = GeneralDef(
    id="liushan", name="刘禅", kingdom="shu", gender="male", max_hp=3,
    skill_ids=("xiangle", "fangquan", "ruoyu"), title="无为的真命主", pack="山",
    description="【享乐】：锁定技，其他角色对你使用【杀】时需额外弃置一张基本牌，否则该【杀】对你无效。【放权】：你可以跳过出牌阶段，则回合结束时弃一张手牌令一名其他角色获得一个额外回合。【若愚】：主公技、觉醒技，回合开始阶段若你的体力为全场最少，须加 1 点体力上限、回复 1 点体力并永久获得【激将】。",
    implemented=False,
    unavailable_reason=(
        "【享乐】需要「成为【杀】的目标时须先弃一张基本牌」的通用目标代价机制，【放权】需要从技能侧跳过出牌阶段并挂一个额外回合的结束钩子——两者本轮都未接入引擎。为避免只做半截，本条目仅作图鉴展示，不提供开局。"
    )
)

JIANGWEI = GeneralDef(
    id="jiangwei", name="姜维", kingdom="shu", gender="male", max_hp=4,
    skill_ids=("tiaoxin", "zhiji"), title="龙的衣钵", pack="山",
    description="【挑衅】：出牌阶段限一次，你可以指定一名能攻击到你的角色，其须对你使用一张【杀】，否则你弃置其一张牌。【志继】：觉醒技，回合开始阶段若你没有手牌，你回复 1 点体力或摸两张牌，然后减 1 点体力上限并永久获得【观星】。",
)

SUNCE = GeneralDef(
    id="sunce", name="孙策", kingdom="wu", gender="male", max_hp=4,
    skill_ids=("jiang", "hunzi", "zhiba"), title="江东的小霸王", pack="山",
    description="【激昂】：当你使用或被使用【决斗】或红色【杀】时，可以摸一张牌。【魂姿】：觉醒技，回合开始阶段若你的体力为 1，须减 1 点体力上限并永久获得【英姿】与【英魂】。【制霸】：主公技，其他吴势力角色出牌阶段可与你拼点，没赢时你可以获得双方的拼点牌。",
)

ZUOCI = GeneralDef(
    id="zuoci", name="左慈", kingdom="qun", gender="male", max_hp=3,
    skill_ids=("huashen", "xinsheng"), title="迷之仙人", pack="山",
    description="【化身】：开局时你随机获得两张未加入游戏的武将牌，选一张置于面前并声明其一项技能（不可声明锁定技、觉醒技、主公技），你拥有该技能且性别与势力变为与该武将相同。【新生】：你每受到 1 点伤害，可获得一张新的化身牌。",
    implemented=False,
    unavailable_reason=(
        "【化身】需要「从未加入游戏的武将牌里随机取两张并声明其一项技能」的化身牌区与技能临时替换，涉及性别 / 势力改写与多个技能的生命周期，本轮未实现；【新生】依附于它。本条目仅作图鉴展示，不提供开局。"
    )
)

ZHANGZHAO_ZHANGHONG = GeneralDef(
    id="zhangzhao_zhanghong", name="张昭&张紘", kingdom="wu", gender="male", max_hp=3,
    skill_ids=("zhijian", "guzheng"), title="经天纬地", pack="山",
    description="【直谏】：出牌阶段，你可以将一张装备牌置于一名其他角色的装备区里（不得替换原装备），然后摸一张牌。【固政】：其他角色的弃牌阶段结束时，你可以将其中一张弃牌交还该角色，并获得其余弃牌。",
)

ZHANGHE = GeneralDef(
    id="zhanghe", name="张郃", kingdom="wei", gender="male", max_hp=4,
    skill_ids=("qiaobian",), title="料敌机先", pack="山",
    description="【巧变】：你可以弃一张手牌跳过自己的一个阶段（回合开始与结束阶段除外）；跳过摸牌阶段则从至多两名角色手里各抽取一张牌；跳过出牌阶段则可将场上的一张牌移动到另一个合理位置。",
    implemented=False,
    unavailable_reason=(
        "【巧变】需要「弃一张手牌跳过自己的任意一个阶段（判定 / 摸牌 / 出牌），并按跳过的阶段触发抽牌或移牌」——当前阶段替代只在摸牌阶段开口，本轮未扩展到任意阶段。本条目仅作图鉴展示，不提供开局。"
    )
)

CAIWENJI = GeneralDef(
    id="caiwenji", name="蔡文姬", kingdom="qun", gender="female", max_hp=3,
    skill_ids=("beige", "duanchang"), title="异乡的孤女", pack="山",
    description="【悲歌】：一名角色受到【杀】造成的伤害后，你可以弃一张牌令其判定：红桃回复 1 点体力，方块摸两张牌，梅花伤害来源弃两张牌，黑桃伤害来源武将牌翻面。【断肠】：锁定技，杀死你的角色失去当前的所有武将技能。",
)

DENGAIL = GeneralDef(
    id="dengai", name="邓艾", kingdom="wei", gender="male", max_hp=4,
    skill_ids=("tuntian", "zaoxian"), title="矫然的壮士", pack="山",
    description="【屯田】：你于回合外失去牌时，可判定并将非红桃的判定牌置于武将牌上，称为「田」；每有一张「田」，你计算与其他角色的距离减一。【凿险】：觉醒技，回合开始阶段若「田」数达到 3 张，须减 1 点体力上限并永久获得【急袭】。",
)

MOUNTAIN_GENERALS = (
    LIUSHAN, JIANGWEI, SUNCE, ZUOCI, ZHANGZHAO_ZHANGHONG, ZHANGHE,
    CAIWENJI, DENGAIL,
)


# ==================================================
# 神将
#
# 卡面只印武将本名；version="神" 把展示名变成「关羽（神）」，
# 与标准版 / 卧龙 / SP 的同名武将互相区分开。
# ==================================================

SHEN_GUANYU = GeneralDef(
    id="shen_guanyu", name="关羽", kingdom="god", gender="male", max_hp=5,
    skill_ids=("wushen", "wuhun"), title="鬼神再临", pack="神", version="神",
    description="【武神】：锁定技，你的红桃手牌均视为【杀】，你使用红桃【杀】无距离限制。【武魂】：锁定技，每名角色每对你造成 1 点伤害就获得一个梦魇标记；你死亡时，持有最多梦魇标记的角色判定，结果不为【桃】或【桃园结义】则其立即死亡。",
)

SHEN_SIMAYI = GeneralDef(
    id="shen_simayi", name="司马懿", kingdom="god", gender="male", max_hp=4,
    skill_ids=("renjie", "baiyin", "lianpo"), title="晋国之祖", pack="神", version="神",
    description="【忍戒】：锁定技，你受到伤害后或于弃牌阶段弃牌后，获得等量的「忍」标记。【拜印】：觉醒技，准备阶段若你有 4 枚或更多「忍」标记，减 1 点体力上限并获得【极略】。【连破】：一名角色的回合结束后，若你于该回合内杀死过角色，你可以进行一个额外回合。",
)

SHEN_LVBU = GeneralDef(
    id="shen_lvbu", name="吕布", kingdom="god", gender="male", max_hp=5,
    skill_ids=("kuangbao", "wumou", "wuqian", "shenfen"), title="修罗之道", pack="神", version="神",
    description="【狂暴】：锁定技，游戏开始时你获得 2 个暴怒标记，每受到 1 点伤害获得 1 个暴怒标记。【无谋】：锁定技，你每使用一张非延时锦囊，需弃 1 个暴怒标记或失去 1 点体力。【无前】：出牌阶段，你可以弃 2 个暴怒标记并指定一名角色，其防具无效且你获得【无双】直到回合结束。【神愤】：出牌阶段限一次，弃 6 个暴怒标记，对每名其他角色造成 1 点伤害，其他角色先弃置装备区所有牌、再各弃四张手牌，然后将你的武将牌翻面。",
)

SHEN_LVMENG = GeneralDef(
    id="shen_lvmeng", name="吕蒙", kingdom="god", gender="male", max_hp=3,
    skill_ids=("shelie", "gongxin"), title="圣光之国士", pack="神", version="神",
    description="【涉猎】：摸牌阶段，你可以改为亮出牌堆顶五张牌，拿走不同花色的各一张，弃掉其余的。【攻心】：出牌阶段，你可以观看一名其他角色的手牌，展示其中一张红桃牌并弃置它或将它置于牌堆顶。",
)

SHEN_ZHOUYU = GeneralDef(
    id="shen_zhouyu", name="周瑜", kingdom="god", gender="male", max_hp=4,
    skill_ids=("qinyin", "yeyan"), title="赤壁的火神", pack="神", version="神",
    description="【琴音】：弃牌阶段，当你弃置两张或更多手牌时，可以令所有角色各回复 1 点体力或各失去 1 点体力。【业炎】：限定技，出牌阶段你可以选择至多三名角色，对其分配合计至多 3 点火焰伤害；对同一角色分配 2 点或更多时，须先弃置四张不同花色的手牌并失去 3 点体力。",
)

SHEN_CAOCAO = GeneralDef(
    id="shen_caocao", name="曹操", kingdom="god", gender="male", max_hp=3,
    skill_ids=("guixin", "feiying"), title="超世之英杰", pack="神", version="神",
    description="【归心】：你每受到 1 点伤害，可以分别从每名其他角色的手牌、装备区与判定区各获得一张牌；若如此做，将你的武将牌翻面。【飞影】：锁定技，其他角色计算与你的距离加一。",
)

SHEN_ZHUGE = GeneralDef(
    id="shen_zhugeliang", name="诸葛亮", kingdom="god", gender="male", max_hp=3,
    skill_ids=("qixing", "kuangfeng", "dawu"), title="赤壁的妖术师", pack="神", version="神",
    description="【七星】：开局时共发你十一张牌，选四张作为手牌，其余背面朝下置于一旁称为「星」；你于摸牌阶段摸牌后，可用任意数量手牌等量交换「星」。【狂风】：结束阶段，你可以弃 1 枚「星」指定一名角色，直到你下回合开始其受到的火焰伤害加一。【大雾】：结束阶段，你可以弃 X 枚「星」指定 X 名角色，直到你下回合开始防止其受到的雷电伤害以外的一切伤害。",
    implemented=False,
    unavailable_reason=(
        "【七星】需要「游戏开始时共发十一张牌并从中选四张」的开局钩子；当前发牌与绑定武将的先后顺序无法在技能层安全插入这个时机。【狂风】【大雾】依附于「星」，因此一并标为不可开局，而不是拿“第一回合再补”的近似实现冒充它。"
    )
)

SHEN_ZHAOYUN = GeneralDef(
    id="shen_zhaoyun", name="赵云", kingdom="god", gender="male", max_hp=2,
    skill_ids=("juejing", "longhun"), title="神威如龙", pack="神", version="神",
    description="【绝境】：锁定技，摸牌阶段你摸已损失体力值加二张牌，你的手牌上限加二。【龙魂】：你可以将同花色的 X 张牌按花色规则使用或打出（红桃当【桃】，方块当火【杀】，梅花当【闪】，黑桃当【无懈可击】），X 为你当前的体力值且至少为 1。",
)

GOD_GENERALS = (
    SHEN_GUANYU, SHEN_SIMAYI, SHEN_LVBU, SHEN_LVMENG, SHEN_ZHOUYU,
    SHEN_CAOCAO, SHEN_ZHUGE, SHEN_ZHAOYUN,
)


# ==================================================
# 一将成名
# ==================================================

YUJIN = GeneralDef(
    id="yujin", name="于禁", kingdom="wei", gender="male", max_hp=4,
    skill_ids=("yizhong",), title="魏武之刚", pack="一将成名",
    description="【毅重】：锁定技，当你没有装备防具时，黑色的【杀】对你无效。",
)

LINGTONG = GeneralDef(
    id="lingtong", name="凌统", kingdom="wu", gender="male", max_hp=4,
    skill_ids=("xuanfeng",), title="豪情烈胆", pack="一将成名",
    description="【旋风】：你失去装备区里的牌时，可以视为对一名其他角色使用一张【杀】（不计入次数限制），或对距离 1 以内的一名其他角色造成 1 点伤害。",
)

WUGUOTAI = GeneralDef(
    id="wuguotai", name="吴国太", kingdom="wu", gender="female", max_hp=3,
    skill_ids=("ganlu", "buyi"), title="武烈皇后", pack="一将成名",
    description="【甘露】：出牌阶段限一次，你可以选择两名角色交换他们装备区里的所有牌，交换的装备牌数差不能超过你已损失的体力值。【补益】：当有角色进入濒死状态时，你可以展示其一张手牌，若不为基本牌则该角色弃置此牌并回复 1 点体力。",
)

ZHANGCHUNHUA = GeneralDef(
    id="zhangchunhua", name="张春华", kingdom="wei", gender="female", max_hp=3,
    skill_ids=("jueqing", "shangshi"), title="冷血皇后", pack="一将成名",
    description="【绝情】：锁定技，你造成的伤害均视为体力流失。【伤逝】：除弃牌阶段外，每当你的手牌数小于你已损失的体力值时，可以立即将手牌补至等同于已损失的体力值。",
)

XUSHU = GeneralDef(
    id="xushu", name="徐庶", kingdom="shu", gender="male", max_hp=3,
    skill_ids=("wuyan", "jujian"), title="忠孝的侠士", pack="一将成名",
    description="【无言】：锁定技，你使用的非延时类锦囊对其他角色无效，其他角色使用的非延时类锦囊对你无效。【举荐】：出牌阶段限一次，你可以弃置至多三张牌令一名其他角色摸等量的牌；弃置不少于三张且类别相同时，你回复 1 点体力。",
)

XUSHENG = GeneralDef(
    id="xusheng", name="徐盛", kingdom="wu", gender="male", max_hp=4,
    skill_ids=("pojun",), title="江东的铁壁", pack="一将成名",
    description="【破军】：你使用【杀】造成伤害后，可以令受伤角色摸 X 张牌（X 为其当前体力值，至多 5），然后其武将牌翻面。",
)

CAOZHI = GeneralDef(
    id="caozhi", name="曹植", kingdom="wei", gender="male", max_hp=3,
    skill_ids=("luoying", "jiushi"), title="八斗之才", pack="一将成名",
    description="【落英】：其他角色的梅花牌因弃置或判定进入弃牌堆时，你可以获得之。【酒诗】：武将牌正面朝上时，你可以将其翻面来视为使用一张【酒】；背面朝上时你受到伤害，可以在伤害结算后将其翻回正面。",
)

FAZHENG = GeneralDef(
    id="fazheng", name="法正", kingdom="shu", gender="male", max_hp=3,
    skill_ids=("enyuan", "xuanhuo"), title="蜀汉的辅翼", pack="一将成名",
    description="【恩怨】：锁定技，其他角色每令你回复 1 点体力，该角色摸一张牌；其他角色每对你造成一次伤害，须给你一张红桃手牌，否则其失去 1 点体力。【眩惑】：出牌阶段限一次，你可以将一张红桃手牌交给一名其他角色，然后获得其一张牌并交给除其以外的其他角色。",
)

# 卡面只印了【同谋】【陷害】两个技能名，正文是空白的。规则来源无法从卡面
# 确认，**绝不**擅自填成【权计】/【自立】之类的其他版本技能——保留图鉴
# 条目，明确标为不可开局。
ZHONGHUI = GeneralDef(
    id="zhonghui", name="钟会", kingdom="wei", gender="male", max_hp=3,
    skill_ids=("tongmou", "xianhai"), title="自傲的野心家", pack="一将成名",
    implemented=False,
    unavailable_reason="该卡面只印了技能名【同谋】【陷害】，技能正文为空白，无法从卡面确认规则；也未找到与之匹配的可信规则来源。为避免把技能做错，本条目仅作图鉴展示，不提供开局。",
    description="【同谋】【陷害】：卡面正文为空白，规则来源待核实。",
)

CHENGONG = GeneralDef(
    id="chengong", name="陈宫", kingdom="qun", gender="male", max_hp=3,
    skill_ids=("mingce", "zhichi"), title="刚直壮烈", pack="一将成名",
    description="【明策】：出牌阶段限一次，你可以交给一名其他角色一张装备牌或【杀】，该角色选择：视为对其攻击范围内由你指定的一名角色使用一张【杀】，或摸一张牌。【智迟】：锁定技，你的回合外，你受到伤害后，直到本回合结束，任何【杀】或非延时锦囊均对你无效。",
)

MASU = GeneralDef(
    id="masu", name="马谡", kingdom="shu", gender="male", max_hp=3,
    skill_ids=("xinzhan", "huilei"), title="怀才自负", pack="一将成名",
    description="【心战】：出牌阶段限一次，若你的手牌数大于体力上限，你可以观看牌堆顶三张牌，展示并获得其中任意数量的红桃牌，其余以任意顺序放回牌堆顶。【挥泪】：锁定技，杀死你的角色立即弃置所有牌。",
)

GAOSHUN = GeneralDef(
    id="gaoshun", name="高顺", kingdom="qun", gender="male", max_hp=4,
    skill_ids=("xianzhen", "jinjiu"), title="攻无不克", pack="一将成名",
    description="【陷阵】：出牌阶段限一次，你可以与一名角色拼点。赢了则本回合无视与该角色的距离及其防具，并可对其使用任意数量的【杀】；没赢则本回合不能使用【杀】。【禁酒】：锁定技，你的【酒】均视为【杀】。",
)

YIJIANG_GENERALS = (
    YUJIN, LINGTONG, WUGUOTAI, ZHANGCHUNHUA, XUSHU, XUSHENG, CAOZHI,
    FAZHENG, ZHONGHUI, CHENGONG, MASU, GAOSHUN,
)


# ==================================================
# SP
# ==================================================

SP_YANGXIU = GeneralDef(
    id="sp_yangxiu", name="杨修", kingdom="qun", gender="male", max_hp=3,
    skill_ids=("danlao", "jilei"), title="恃才放旷", pack="SP",
    description="【啖酪】：当一个锦囊指定了包括你在内的多名目标时，你可以摸一张牌，若如此做该锦囊对你无效。【鸡肋】：你受到伤害时，可以说出一种牌的类别（基本牌 / 锦囊牌 / 装备牌），伤害来源直到本回合结束不能使用、打出或弃置该类别的手牌。",
)

SP_DIAOCHAN = GeneralDef(
    id="sp_diaochan", name="貂蝉", kingdom="qun", gender="female", max_hp=3,
    skill_ids=("lijian_sp", "biyue"), title="绝世的舞姬", pack="SP", version="SP",
    description="【离间】：出牌阶段限一次，你可以弃一张牌并选择两名男性角色，视为其中一名对另一名使用一张【决斗】（此【决斗】不能被【无懈可击】响应）。【闭月】：结束阶段，你可以摸一张牌。",
)

SP_GONGSUNZAN = GeneralDef(
    id="sp_gongsunzan", name="公孙瓒", kingdom="qun", gender="male", max_hp=4,
    skill_ids=("yicong",), title="白马将军", pack="SP",
    description="【义从】：锁定技，体力大于 2 时你计算与其他角色的距离减一；体力为 2 或更低时其他角色计算与你的距离加一。",
)

SP_SUNSHANGXIANG = GeneralDef(
    id="sp_sunshangxiang", name="孙尚香", kingdom="wu", gender="female", max_hp=3,
    skill_ids=("jieyin", "xiaoji"), title="梦醉良缘", pack="SP", version="SP",
    description="【结姻】：出牌阶段限一次，你可以弃两张手牌并指定一名已受伤的男性角色，你与其各回复 1 点体力。【枭姬】：你失去装备区里的一张牌时，可以摸两张牌。",
)

SP_PANGDE = GeneralDef(
    id="sp_pangde", name="庞德", kingdom="qun", gender="male", max_hp=4,
    skill_ids=("mashu", "mengjin"), title="抬榇之悟", pack="SP", version="SP",
    description="【马术】：锁定技，计算与其他角色的距离减一。【猛进】：你使用的【杀】被【闪】抵消时，可以弃置对方一张牌。",
)

SP_GUANYU = GeneralDef(
    id="sp_guanyu", name="关羽", kingdom="wei", gender="male", max_hp=4,
    skill_ids=("wusheng", "danqi"), title="汉寿亭侯", pack="SP", version="SP",
    description="【武圣】：你可以将一张红色牌当【杀】使用或打出。【单骑】：觉醒技，回合开始阶段若你的手牌数大于当前体力值且本局主公为曹操，你须减 1 点体力上限并永久获得【马术】。",
)

# SP008 的两个形态：两张卡面都保留，且都不与神将目录的神吕布、标准吕布混同。
SP_LVBU_MYTH = GeneralDef(
    id="sp_lvbu_myth", name="吕布", kingdom="god", gender="male", max_hp=8,
    skill_ids=("mashu", "wushuang"), title="最强神话", pack="SP",
    version="最强神话", test_only=True,
    description="【马术】：锁定技，计算与其他角色的距离减一。【无双】：锁定技，你使用【杀】时目标需连续使用两张【闪】；与你决斗的角色每次须连续打出两张【杀】。",
)

SP_LVBU_WRATH = GeneralDef(
    id="sp_lvbu_wrath", name="吕布", kingdom="god", gender="male", max_hp=4,
    skill_ids=("mashu", "wushuang", "xiuluo", "shenwei", "shenji"),
    title="暴怒的战神", pack="SP", version="暴怒的战神", test_only=True,
    description="【马术】【无双】同最强神话形态。【修罗】：回合开始阶段，你可以弃一张手牌弃置判定区里花色相同的一张延时锦囊。【神威】：锁定技，摸牌阶段额外摸两张牌，手牌上限加二。【神戟】：没有装备武器时，你使用的【杀】可以指定至多三名角色。",
)

SP_CAIWENJI = GeneralDef(
    id="sp_caiwenji", name="蔡文姬", kingdom="qun", gender="female", max_hp=3,
    skill_ids=("beige", "duanchang"), title="金璧之才", pack="SP", version="SP",
    description="【悲歌】：一名角色受到【杀】造成的伤害后，你可以弃一张牌令其判定：红桃回复 1 点体力，方块摸两张牌，梅花伤害来源弃两张牌，黑桃伤害来源武将牌翻面。【断肠】：锁定技，杀死你的角色失去当前的所有武将技能。",
)

SP_MACHAO = GeneralDef(
    id="sp_machao", name="马超", kingdom="qun", gender="male", max_hp=4,
    skill_ids=("mashu", "tieji"), title="西凉的雄狮", pack="SP", version="SP",
    description="【马术】：锁定技，计算与其他角色的距离减一。【铁骑】：你使用【杀】指定目标后可以判定，红色则此【杀】不可被【闪】响应。",
)

SP_JIAXU = GeneralDef(
    id="sp_jiaxu", name="贾诩", kingdom="qun", gender="male", max_hp=3,
    skill_ids=("wansha", "luanwu", "weimu"), title="算无遗策", pack="SP", version="SP",
    description="【完杀】【乱武】【帷幕】与林包贾诩相同。",
)

SP_YUANSHU = GeneralDef(
    id="sp_yuanshu", name="袁术", kingdom="qun", gender="male", max_hp=4,
    skill_ids=("yongsi", "weidi"), title="仲家帝", pack="SP",
    description="【庸肆】：锁定技，摸牌阶段你额外摸 X 张牌（X 为场上现存势力数）；弃牌阶段你至少须弃置等同于现存势力数的牌。【伪帝】：锁定技，你拥有当前主公的主公技。",
)

SP_GENERALS = (
    SP_YANGXIU, SP_DIAOCHAN, SP_GONGSUNZAN, SP_SUNSHANGXIANG, SP_PANGDE,
    SP_GUANYU, SP_LVBU_MYTH, SP_LVBU_WRATH, SP_CAIWENJI, SP_MACHAO,
    SP_JIAXU, SP_YUANSHU,
)


# ==================================================
# 汇总
# ==================================================

EXPANSION_GENERALS = (
    WIND_GENERALS + FIRE_GENERALS + FOREST_GENERALS + MOUNTAIN_GENERALS
    + GOD_GENERALS + YIJIANG_GENERALS + SP_GENERALS
)
