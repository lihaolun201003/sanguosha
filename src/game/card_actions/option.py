"""Data-driven description of one thing a physical card can do right now.

Every consumer (CardActionPicker, AI, response filtering, rescue) reads the
same object; nothing downstream re-derives rules from a card name.
"""

from dataclasses import dataclass, field
from typing import Any, Optional, Tuple

from .context import ActionKind


@dataclass(frozen=True)
class CardActionOption:
    """一个候选动作：使用 / 打出某张（某组）实体牌的一种方式。"""

    action_id: str
    kind: str                       # ActionKind.NORMAL / ActionKind.CONVERSION
    context: str                    # play / response / rescue
    owner: Any
    source_cards: Tuple[Any, ...]
    result_name: str
    result_category: str = "basic"
    result_subtype: Optional[str] = None
    result_nature: str = "normal"
    skill_id: str = ""
    skill_name: str = ""
    enabled: bool = True
    disabled_reason: str = ""
    min_sources: int = 1
    max_sources: int = 1
    requires_targets: bool = False
    metadata: Tuple[Any, ...] = ()

    # ---- 身份 ----

    @property
    def is_conversion(self):
        return self.kind == ActionKind.CONVERSION

    @property
    def kind_context(self):
        """六个可发现场合的稳定标签：normal_play / conversion_response …"""

        return "%s_%s" % (self.kind, self.context)

    @property
    def source_count(self):
        return len(self.source_cards)

    @property
    def complete(self):
        """已选 source 是否已经够组成这个动作。"""

        return len(self.source_cards) >= self.min_sources

    @property
    def needs_more_sources(self):
        return len(self.source_cards) < self.min_sources

    @property
    def is_multi_source(self):
        return self.max_sources > 1

    # ---- 展示 ----

    @property
    def result_display(self):
        from src.card import display_name_for

        return display_name_for(self.result_name, self.result_nature)

    @property
    def label(self):
        """Picker 里那一行主标题。"""

        if not self.is_conversion:
            if self.context == "response":
                return "打出【" + self.result_display + "】"
            return "使用【" + self.result_display + "】"
        verb = "打出" if self.context == "response" else "使用"
        return "【%s】将此牌当【%s】%s" % (
            self.skill_name or self.skill_id,
            self.result_display,
            verb,
        )

    def describe_sources(self):
        parts = []
        for card in self.source_cards:
            label = getattr(card, "display_name", None) or str(getattr(card, "name", "?"))
            identity = getattr(card, "identity_label", "") or ""
            parts.append((identity + "【" + label + "】") if identity else ("【" + label + "】"))
        return "、".join(parts)

    @property
    def detail(self):
        """Picker 里那一行副标题 / Prompt 的第二行。"""

        if not self.is_conversion:
            return self.disabled_reason or ""
        if self.needs_more_sources:
            return "还需要选择 %d 张牌" % (self.min_sources - len(self.source_cards))
        if self.disabled_reason:
            return self.disabled_reason
        return "将" + self.describe_sources() + "当【" + self.result_display + "】使用"

    @property
    def log_text(self):
        """战报文案：必须让玩家看出发生过转换。"""

        if not self.is_conversion:
            return None
        verb = "打出" if self.context == "response" else "使用"
        return "%s发动【%s】，将%s当【%s】%s" % (
            getattr(self.owner, "name", ""),
            self.skill_name or self.skill_id,
            self.describe_sources(),
            self.result_display,
            verb,
        )

    def with_sources(self, cards):
        """复制一份并替换 source cards（保持 action identity 稳定）。"""

        from .context import conversion_action_id

        cards = tuple(cards)
        if self.is_conversion:
            changed = CardActionOption(
                action_id=conversion_action_id(
                    self.skill_id, cards, self.result_name, self.context),
                kind=self.kind,
                context=self.context,
                owner=self.owner,
                source_cards=cards,
                result_name=self.result_name,
                result_category=self.result_category,
                result_subtype=self.result_subtype,
                result_nature=self.result_nature,
                skill_id=self.skill_id,
                skill_name=self.skill_name,
                enabled=self.enabled,
                disabled_reason=self.disabled_reason,
                min_sources=self.min_sources,
                max_sources=self.max_sources,
                requires_targets=self.requires_targets,
                metadata=self.metadata,
            )
            return changed
        return self

    def with_state(self, enabled, reason=""):
        """复制一份并替换可用性（用于需要二次校验的场合）。"""

        return CardActionOption(
            action_id=self.action_id,
            kind=self.kind,
            context=self.context,
            owner=self.owner,
            source_cards=self.source_cards,
            result_name=self.result_name,
            result_category=self.result_category,
            result_subtype=self.result_subtype,
            result_nature=self.result_nature,
            skill_id=self.skill_id,
            skill_name=self.skill_name,
            enabled=bool(enabled),
            disabled_reason="" if enabled else str(reason),
            min_sources=self.min_sources,
            max_sources=self.max_sources,
            requires_targets=self.requires_targets,
            metadata=self.metadata,
        )

    def __repr__(self):
        return "CardActionOption(%s, %s->%s, sources=%d%s)" % (
            self.action_id,
            self.kind,
            self.result_name,
            len(self.source_cards),
            "" if self.enabled else ", disabled=" + self.disabled_reason,
        )
