"""View-As 会话：先点技能，再选实体牌（龙胆 / 武圣 / 未来的倾国、急救…）。

一次 View-As 只记录必要内容，不引入 DSL：
    skill_id / context / selected_source_cards / required_source_count
    effective_card / selected_targets / stage

真人流程：
    点击「发动技能」→ 选择视为技 → 选 source 牌 → （多 source 继续选）
    → 构造 VirtualCard → 目标选择 / 响应 → 提交
未提交前取消不写战报、不播放技能 FX、不消耗任何牌。
"""

from dataclasses import dataclass, field
from typing import Any, List, Optional, Tuple

STAGE_SELECT_SOURCE = "select_source"
STAGE_SELECT_TARGET = "select_target"
STAGE_READY = "ready"


@dataclass
class ViewAsSession:
    skill_id: str
    skill_name: str
    context: Any
    required_source_count: int = 1
    result_name: str = ""
    selected_source_cards: List[Any] = field(default_factory=list)
    selected_targets: List[Any] = field(default_factory=list)
    effective_card: Any = None
    source_rect: Any = None
    stage: str = STAGE_SELECT_SOURCE
    candidates: Tuple[Any, ...] = ()

    @property
    def owner(self):
        return self.context.actor

    @property
    def needs_more_sources(self):
        return len(self.selected_source_cards) < self.required_source_count

    @property
    def is_multi_source(self):
        return self.required_source_count > 1

    def accepts(self, card):
        for option in self.candidates:
            if any(card is item for item in option.source_cards):
                return True
        return False

    def describe_selected(self):
        parts = []
        for card in self.selected_source_cards:
            label = getattr(card, "display_name", None) or str(getattr(card, "name", "?"))
            identity = getattr(card, "identity_label", "") or ""
            parts.append((identity + "【" + label + "】") if identity else ("【" + label + "】"))
        return "、".join(parts)

    def __repr__(self):
        return "ViewAsSession(%s, %s, %d/%d)" % (
            self.skill_id, self.stage,
            len(self.selected_source_cards), self.required_source_count)
