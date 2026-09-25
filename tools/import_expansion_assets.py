"""把桌面素材包里的扩展卡面复制进项目，并生成逐项映射清单。

源目录是**只读**的：这里只做 COPY，从不 MOVE、不改名、不覆盖源文件。
目标目录：``assets/generals/expansions/<包>/<稳定 id>.png``。

为什么不用原始中文文件名
------------------------
扩展包里有多组**同名不同来源**的卡面（火包庞德 / SP006 庞德、林包贾诩 /
SP012 贾诩、山包蔡文姬 / SP009 蔡文姬…），平铺进一个目录会互相覆盖。
目标文件名一律用**稳定 id**，映射关系写在本文件的 ``PACK_ASSETS`` 表里，
并导出到 ``assets/expansion_inventory.json`` 供核对与测试使用。
"""

import json
import os
import shutil

# 源素材根目录（只读）
SOURCE_ROOT = os.path.join(
    os.path.expanduser("~"), "Desktop", "sanguosha", "sucau", "卡牌", "卡牌",
    "卡牌全高清图", "三国杀卡牌全高清图")

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARGET_ROOT = os.path.join(PROJECT_ROOT, "assets")
MANIFEST_PATH = os.path.join(TARGET_ROOT, "expansion_inventory.json")

# 武将卡面的子目录
GENERAL_SUBDIR = "generals/expansions"
# 非武将资源（标记、装备牌面）的子目录
EXTRA_SUBDIR = "expansions"

# ==================================================
# 源文件 → 稳定 id（唯一权威映射）
#
# 每一项：(源文件相对路径, 目标相对路径, 稳定 id, 备注)
# 备注里写清楚这条映射为什么长这样（错字纠正 / 同名分版本 / 非武将资源）。
# ==================================================

PACK_ASSETS = (
    # ---------------- 风包 ----------------
    ("风包/于吉.png", GENERAL_SUBDIR + "/wind/yuji.png", "yuji", ""),
    ("风包/周泰.png", GENERAL_SUBDIR + "/wind/zhoutai.png", "zhoutai", ""),
    ("风包/夏侯渊.png", GENERAL_SUBDIR + "/wind/xiahouyuan.png", "xiahouyuan", ""),
    ("风包/小乔.png", GENERAL_SUBDIR + "/wind/xiaoqiao.png", "xiaoqiao", ""),
    # 张角有两版规则（2008 初版 / 2010 修订版），两个卡面都保留
    ("风包/张角.png", GENERAL_SUBDIR + "/wind/zhangjiao.png", "zhangjiao",
     "2008 初版规则：雷击为「打出闪→令其减 2 点体力」"),
    ("风包/张角2010.png", GENERAL_SUBDIR + "/wind/zhangjiao_2010.png", "zhangjiao_2010",
     "2010 修订版规则：雷击为「使用或打出闪→造成 2 点雷电伤害」"),
    ("风包/曹仁.png", GENERAL_SUBDIR + "/wind/caoren.png", "caoren",
     "2008 初版规则：据守为「跳过你下个回合」"),
    ("风包/曹仁2010.png", GENERAL_SUBDIR + "/wind/caoren_2010.png", "caoren_2010",
     "2010 修订版规则：据守为「将你的武将牌翻面」"),
    ("风包/魏延.png", GENERAL_SUBDIR + "/wind/weiyan.png", "weiyan", ""),
    ("风包/黄忠.png", GENERAL_SUBDIR + "/wind/huangzhong.png", "huangzhong", ""),

    # ---------------- 火包 ----------------
    ("火包/典韦.png", GENERAL_SUBDIR + "/fire/dianwei.png", "dianwei", ""),
    # 卡面实际印的是「卧龙 + 诸葛亮」，游戏内需与标准版诸葛亮明确区分
    ("火包/卧龙诸葛.png", GENERAL_SUBDIR + "/fire/wolongzhuge.png", "wolongzhuge",
     "卡面为称号「卧龙」+ 姓名「诸葛亮」；显式映射到卧龙诸葛亮，不与标准版诸葛亮混淆"),
    ("火包/太史慈.png", GENERAL_SUBDIR + "/fire/taishici.png", "taishici", ""),
    ("火包/庞德.png", GENERAL_SUBDIR + "/fire/pangde.png", "pangde", ""),
    ("火包/庞统.png", GENERAL_SUBDIR + "/fire/pangtong.png", "pangtong", ""),
    ("火包/荀彧.png", GENERAL_SUBDIR + "/fire/xunyu.png", "xunyu", ""),
    ("火包/袁绍.png", GENERAL_SUBDIR + "/fire/yuanshao.png", "yuanshao", ""),
    ("火包/颜良&文丑.png", GENERAL_SUBDIR + "/fire/yanliangwenchou.png", "yanliangwenchou", ""),

    # ---------------- 林包 ----------------
    ("林包/孙坚.png", GENERAL_SUBDIR + "/forest/sunjian.png", "sunjian", ""),
    ("林包/孟获.png", GENERAL_SUBDIR + "/forest/menghuo.png", "menghuo", ""),
    ("林包/徐晃.png", GENERAL_SUBDIR + "/forest/xuhuang.png", "xuhuang", ""),
    ("林包/曹丕.png", GENERAL_SUBDIR + "/forest/caopi.png", "caopi", ""),
    ("林包/祝融.png", GENERAL_SUBDIR + "/forest/zhurong.png", "zhurong", ""),
    ("林包/董卓.png", GENERAL_SUBDIR + "/forest/dongzhuo.png", "dongzhuo", ""),
    ("林包/贾诩.png", GENERAL_SUBDIR + "/forest/jiaxu.png", "jiaxu", ""),
    ("林包/鲁肃.png", GENERAL_SUBDIR + "/forest/lusu.png", "lusu", ""),

    # ---------------- 山包 ----------------
    ("山包/刘禅.png", GENERAL_SUBDIR + "/mountain/liushan.png", "liushan", ""),
    # 张颌.png 的文件名是错字，卡面印的是「张郃」——目标用正确 id，映射显式记录
    ("山包/张颌.png", GENERAL_SUBDIR + "/mountain/zhanghe.png", "zhanghe",
     "源文件名错字「张颌」，卡面实为「张郃」，这里按正确显示名登记"),
    ("山包/姜维.png", GENERAL_SUBDIR + "/mountain/jiangwei.png", "jiangwei", ""),
    ("山包/孙策.png", GENERAL_SUBDIR + "/mountain/sunce.png", "sunce", ""),
    ("山包/左慈.png", GENERAL_SUBDIR + "/mountain/zuoci.png", "zuoci", ""),
    ("山包/张昭&张紘.png", GENERAL_SUBDIR + "/mountain/zhangzhao_zhanghong.png",
     "zhangzhao_zhanghong", "卡面与文件名均作「张昭&张紘」"),
    ("山包/蔡文姬.png", GENERAL_SUBDIR + "/mountain/caiwenji.png", "caiwenji", ""),
    ("山包/邓艾.png", GENERAL_SUBDIR + "/mountain/dengai.png", "dengai", ""),
    # 刘禅标记是一个辅助指示物，不是武将牌、也不是摸牌堆里的牌
    ("山包/刘禅标记.png", EXTRA_SUBDIR + "/markers/liushan_marker.png",
     "marker:liushan", "辅助标记资源（配合【放权】的额外回合令标记），非武将牌"),

    # ---------------- 神将 ----------------
    # 神将卡面只印武将本名，「神」是左上角的势力印玺；这里按 id 前缀区分
    ("神将/神关羽.png", GENERAL_SUBDIR + "/god/shen_guanyu.png", "shen_guanyu",
     "卡面姓名「关羽」+ 神势力印玺，与标准版关羽分开"),
    ("神将/神司马懿.png", GENERAL_SUBDIR + "/god/shen_simayi.png", "shen_simayi", ""),
    ("神将/神吕布.png", GENERAL_SUBDIR + "/god/shen_lvbu.png", "shen_lvbu", ""),
    ("神将/神吕蒙.png", GENERAL_SUBDIR + "/god/shen_lvmeng.png", "shen_lvmeng", ""),
    ("神将/神周瑜.png", GENERAL_SUBDIR + "/god/shen_zhouyu.png", "shen_zhouyu", ""),
    ("神将/神曹操.png", GENERAL_SUBDIR + "/god/shen_caocao.png", "shen_caocao", ""),
    ("神将/神诸葛.png", GENERAL_SUBDIR + "/god/shen_zhugeliang.png", "shen_zhugeliang",
     "卡面姓名「诸葛亮」+ 神势力印玺，与标准版诸葛亮、卧龙诸葛亮三者分开"),
    ("神将/神赵云.png", GENERAL_SUBDIR + "/god/shen_zhaoyun.png", "shen_zhaoyun", ""),

    # ---------------- 一将成名 ----------------
    ("一将成名/于禁.png", GENERAL_SUBDIR + "/yijiang/yujin.png", "yujin", ""),
    ("一将成名/凌统.png", GENERAL_SUBDIR + "/yijiang/lingtong.png", "lingtong", ""),
    ("一将成名/吴国太.png", GENERAL_SUBDIR + "/yijiang/wuguotai.png", "wuguotai", ""),
    ("一将成名/张春华.png", GENERAL_SUBDIR + "/yijiang/zhangchunhua.png", "zhangchunhua", ""),
    # 徐庶两张卡是同一名武将的两个卡面版本：势力都按卡面左上角的「蜀」标记，
    # 绝不因为蓝色卡框判成魏。规则完全相同，因此登记为同一武将的备用卡面。
    ("一将成名/徐庶.png", GENERAL_SUBDIR + "/yijiang/xushu.png", "xushu",
     "蜀势力（按卡面蜀字标记）；两张徐庶卡面之一"),
    ("一将成名/徐庶2.png", GENERAL_SUBDIR + "/yijiang/xushu_alt.png", "xushu_alt",
     "徐庶（备用卡面）：蓝色卡框但左上角仍是蜀字标记，规则与 xushu 完全一致"),
    ("一将成名/徐盛.png", GENERAL_SUBDIR + "/yijiang/xusheng.png", "xusheng", ""),
    ("一将成名/曹植.png", GENERAL_SUBDIR + "/yijiang/caozhi.png", "caozhi", ""),
    ("一将成名/法正.png", GENERAL_SUBDIR + "/yijiang/fazheng.png", "fazheng", ""),
    ("一将成名/钟会.png", GENERAL_SUBDIR + "/yijiang/zhonghui.png", "zhonghui",
     "卡面只印技能名「同谋」「陷害」，正文为空白，规则来源待核实"),
    ("一将成名/陈宫.png", GENERAL_SUBDIR + "/yijiang/chengong.png", "chengong", ""),
    ("一将成名/马谡.png", GENERAL_SUBDIR + "/yijiang/masu.png", "masu", ""),
    ("一将成名/高顺.png", GENERAL_SUBDIR + "/yijiang/gaoshun.png", "gaoshun", ""),

    # ---------------- SP ----------------
    ("SP/SP001杨修.png", GENERAL_SUBDIR + "/sp/sp_yangxiu.png", "sp_yangxiu", ""),
    ("SP/SP002貂蝉.png", GENERAL_SUBDIR + "/sp/sp_diaochan.png", "sp_diaochan",
     "与标准版貂蝉同名：SP 版【离间】明确「此决斗不能被无懈可击响应」"),
    ("SP/SP003公孙瓒.png", GENERAL_SUBDIR + "/sp/sp_gongsunzan.png", "sp_gongsunzan", ""),
    ("SP/SP005孙尚香.png", GENERAL_SUBDIR + "/sp/sp_sunshangxiang.png", "sp_sunshangxiang",
     "与标准版孙尚香同名：技能组相同，单独登记为 SP 版本"),
    ("SP/SP006庞德.png", GENERAL_SUBDIR + "/sp/sp_pangde.png", "sp_pangde", ""),
    ("SP/SP007关羽.png", GENERAL_SUBDIR + "/sp/sp_guanyu.png", "sp_guanyu",
     "魏势力关羽，带觉醒技【单骑】，与标准版蜀关羽分开"),
    # SP008 是同编号的两个形态，两张卡都要保留，且都不能与神吕布混为一谈
    ("SP/SP008（2-1）吕布.png", GENERAL_SUBDIR + "/sp/sp_lvbu_myth.png", "sp_lvbu_myth",
     "SP008 形态一「最强神话」：神势力 8 体力，马术 + 无双"),
    ("SP/SP008（2-2）吕布.png", GENERAL_SUBDIR + "/sp/sp_lvbu_wrath.png", "sp_lvbu_wrath",
     "SP008 形态二「暴怒的战神」：神势力 4 体力，马术/无双/修罗/神威/神戟"),
    ("SP/SP009蔡文姬.png", GENERAL_SUBDIR + "/sp/sp_caiwenji.png", "sp_caiwenji",
     "与山包蔡文姬同名；SP 版称号「金璧之才」"),
    # 银月枪是装备牌，卡面进卡牌资源目录，不进武将卡目录
    ("SP/SP010银月枪.png", "cards/expansion/sp_yinyueqiang.png", "card:YINYUEQIANG",
     "装备牌（武器），方块 Q，攻击范围 3；不是武将牌"),
    ("SP/SP011马超.png", GENERAL_SUBDIR + "/sp/sp_machao.png", "sp_machao",
     "群势力马超，与标准版蜀马超分开"),
    # 源文件扩展名是 .jpg，内容其实是 PNG；统一转存为 .png 并验证可加载
    ("SP/SP012贾诩.jpg", GENERAL_SUBDIR + "/sp/sp_jiaxu.png", "sp_jiaxu",
     "源扩展名 .jpg 实为 PNG 编码，转存为 .png 后由 Pygame 正常加载"),
    ("SP/SP04袁术.png", GENERAL_SUBDIR + "/sp/sp_yuanshu.png", "sp_yuanshu",
     "文件名 SP04，卡面编号 SP 004"),
)

#: 同一武将的备用卡面（不删除、不默认使用，与 CARD_ALTERNATE_ASSETS 同思路）
ALTERNATE_ARTS = (
    ("xushu", "xushu_alt"),
)


def import_assets(*, verbose=True):
    """执行复制；返回清单条目列表。已存在且大小一致的文件跳过复制。"""

    entries = []
    for source_rel, target_rel, stable_id, note in PACK_ASSETS:
        source_path = os.path.join(SOURCE_ROOT, *source_rel.split("/"))
        target_path = os.path.join(TARGET_ROOT, *target_rel.split("/"))
        exists = os.path.exists(source_path)
        copied = False
        if exists:
            os.makedirs(os.path.dirname(target_path), exist_ok=True)
            if (not os.path.exists(target_path)
                    or os.path.getsize(target_path) != os.path.getsize(source_path)):
                shutil.copy2(source_path, target_path)
                copied = True
        entries.append({
            "source": source_rel,
            "source_path": source_path,
            "target": target_rel,
            "target_path": target_path,
            "stable_id": stable_id,
            "note": note,
            "source_exists": exists,
            "copied": copied,
        })
        if verbose:
            mark = "复制" if copied else ("已存在" if exists else "缺失")
            print("[%s] %s -> %s" % (mark, source_rel, target_rel))
    return entries


def write_manifest(entries):
    payload = {
        "source_root": SOURCE_ROOT,
        "note": "扩展卡面逐项映射清单；源目录只读，本文件由 tools/import_expansion_assets.py 生成。",
        "alternate_arts": [{"general_id": gid, "alternate": alt} for gid, alt in ALTERNATE_ARTS],
        "items": [
            {
                "source": item["source"],
                "target": item["target"],
                "stable_id": item["stable_id"],
                "note": item["note"],
                "source_exists": item["source_exists"],
            }
            for item in entries
        ],
    }
    os.makedirs(os.path.dirname(MANIFEST_PATH), exist_ok=True)
    with open(MANIFEST_PATH, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    return MANIFEST_PATH


def main():
    entries = import_assets()
    path = write_manifest(entries)
    missing = [item for item in entries if not item["source_exists"]]
    print("")
    print("共 %d 项，缺失 %d 项，清单写入 %s" % (len(entries), len(missing), path))
    for item in missing:
        print("  缺失：" + item["source"])
    return 0 if not missing else 1


if __name__ == "__main__":
    raise SystemExit(main())
