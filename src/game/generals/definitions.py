"""General definitions: who a character is and which skills they own."""

from dataclasses import dataclass, field
from typing import Any, Tuple


KINGDOMS = {
    "wei": "魏",
    "shu": "蜀",
    "wu": "吴",
    "qun": "群",
    "god": "神",
}

# 现有武将所属的扩展包（后续新增风 / 火 / 林 / 山 / 神 / 一将成名 / SP 时，
# 各自在自己的武将定义里写 ``pack="风"`` 即可，界面不需要任何改动）。
DEFAULT_PACK = "标准版"

#: 允许使用「测试用配置」武将的模式 id。这些武将只能在**显式指定**时开局
#: （1v1 测试模式的设置页），既不会被随机抽到，也不会出现在普通候选池里。
TEST_ONLY_MODES = ("duel_test",)


@dataclass(frozen=True)
class GeneralDef:
    """Data-only description of a general.

    No skill logic lives here: the definition only says which skill ids the
    general owns, and ``SkillRegistry`` resolves them.

    扩展包（``pack``）与版本 / 形态（``version``）是纯展示元数据：筛选、分组
    与"同名不同版本"的标签都从这两个字段推导，界面不认识任何具体武将。
    ``implemented`` / ``unavailable_reason`` 说明这条武将能不能真的开一局：
    未实现的条目照样列出来（看得见、知道为什么不能选），但被明确禁止开局，
    而不是悄悄换成别的武将。
    """

    id: str
    name: str
    kingdom: str = "qun"
    gender: str = "male"
    max_hp: int = 4
    skill_ids: Tuple[str, ...] = field(default_factory=tuple)
    title: str = ""
    description: str = ""
    portrait: Any = None
    #: 扩展包：标准版 / 风 / 火 / 林 / 山 / 神 / 一将成名 / SP…
    pack: str = DEFAULT_PACK
    #: 版本 / 形态标签（同名不同版本时用来区分，例如"界限突破"、"SP"）。
    version: str = ""
    #: False = 这条武将还不能真正开一局（规则未实现或待核实）。
    implemented: bool = True
    #: ``implemented`` 为 False 时给出的原因（会原样显示给玩家）。
    unavailable_reason: str = ""
    #: True = 只能在**测试模式显式指定**时使用（不进随机池 / 普通候选池）。
    #: 用于同一编号的多个形态（SP008 的两张吕布）与跨势力同名变体。
    test_only: bool = False
    #: 额外的可玩性限制：非空时只有这些模式允许开局（空 = 所有模式）。
    #: 需要"这个武将只能在某类模式里成立"时用它，例如【伪帝】依赖主公。
    allowed_modes: Tuple[str, ...] = ()

    def __post_init__(self):
        if not self.id:
            raise ValueError("GeneralDef.id is required")
        if not self.name:
            raise ValueError("GeneralDef.name is required")

    @property
    def kingdom_name(self):
        return KINGDOMS.get(self.kingdom, self.kingdom)

    @property
    def has_skills(self):
        return bool(self.skill_ids)

    @property
    def display_name(self):
        """带版本 / 形态的展示名（没有版本时就是名字本身）。"""

        if self.version:
            return self.name + "（" + self.version + "）"
        return self.name

    @property
    def availability(self):
        """(能不能开局, 原因)；原因在能开局时是空串。

        这是**不带模式**的粗判据（"规则实现了吗"），设置页显示与
        ``random_eligible`` 都以它为前提。
        """

        if self.implemented:
            return True, ""
        return False, (self.unavailable_reason
                       or "该武将尚未实现，本测试模式不提供开局。")

    @property
    def random_eligible(self):
        """能不能被随机抽到 / 出现在普通候选池里。

        与"能不能开局"是两件事：实现的武将当然能开局，但**测试用配置**
        （``test_only``）只能由玩家在测试模式的设置页指名，随机抽到就等于
        把特殊形态当普通武将发出去。
        """

        return bool(self.implemented) and not self.test_only

    def availability_for(self, mode_id=None):
        """(能不能在 ``mode_id`` 模式开局, 原因)。

        单一入口：本地选将、随机抽取、AI 分配、身份局、联机候选池与权威端
        校验全部走这里，不允许任何调用方自己判断 ``implemented``。
        """

        ok, reason = self.availability
        if not ok:
            return False, reason
        if self.test_only and str(mode_id or "") not in TEST_ONLY_MODES:
            return False, ("「" + self.display_name + "」是测试用配置，"
                           "只能在 1v1 测试模式里显式选择，不参与随机分配。")
        if self.allowed_modes and str(mode_id or "") not in self.allowed_modes:
            return False, ("「" + self.display_name + "」只在这些模式里启用："
                           + "、".join(str(item) for item in self.allowed_modes))
        return True, ""

    def random_eligible_for(self, mode_id=None):
        ok, _reason = self.availability_for(mode_id)
        return bool(ok) and self.random_eligible
