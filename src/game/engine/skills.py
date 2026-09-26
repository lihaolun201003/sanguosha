"""Common hook contract for equipment and future character skills."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Hashable, Iterable, List

from .events import Event, EventType


@dataclass(frozen=True)
class SkillBinding:
    event_name: Hashable
    priority: int = 0


class Skill(ABC):
    id = "skill"
    name = "Skill"
    optional = False

    #: 触发时要不要发一条"技能发动"只读通知（界面据此显示技能提示）。
    #: 默认要；装备一类的**规则集合**技能（``equipment.locked_rules``）关掉它——
    #: 那不是一个武将技能，名字（"装备锁定技"）对玩家没有意义，而且它绑的是
    #: 每次伤害计算 / 每次杀，会把提示刷爆。真正生效的锁定效果（仁王盾、
    #: 藤甲、白银狮子）由规则自己发一条带牌名的通知。
    announces = True

    # 技能 resolve 期间可能触发新的 Atom/事件，进而再次触发技能。
    # 允许合理嵌套（受到伤害 → 摸牌 → 摸牌事件 → 其他技能），但超过
    # 这个深度就放弃当次触发，避免无限连锁。
    MAX_DEPTH = 12
    _depth = 0

    def __init__(self, owner=None) -> None:
        self.owner = owner
        self._subscription_tokens: List[int] = []

    @abstractmethod
    def bindings(self) -> Iterable[SkillBinding]:
        raise NotImplementedError

    def can_trigger(self, context: "GameContext", event: Event) -> bool:
        return True

    @abstractmethod
    def resolve(self, context: "GameContext", event: Event) -> None:
        raise NotImplementedError

    def install(self, context: "GameContext") -> None:
        if self._subscription_tokens:
            raise RuntimeError("skill is already installed")

        for binding in self.bindings():
            token = context.events.subscribe(
                binding.event_name,
                self._handle_event,
                priority=binding.priority,
                owner=self,
            )
            self._subscription_tokens.append(token)

    def uninstall(self, context: "GameContext") -> None:
        for token in self._subscription_tokens:
            context.events.unsubscribe(token)
        self._subscription_tokens.clear()

    @property
    def installed(self) -> bool:
        return bool(self._subscription_tokens)

    def _handle_event(self, context: "GameContext", event: Event) -> None:
        if Skill._depth >= self.MAX_DEPTH:
            return
        if not self.can_trigger(context, event):
            return
        Skill._depth += 1
        try:
            if self.announces:
                # 只读通知：UI 用它显示技能发动提示，不驱动任何规则。
                context.emit(Event(
                    EventType.SKILL_TRIGGERED,
                    source=self.owner,
                    payload={"skill_id": self.id, "skill_name": self.name},
                ))
            self.resolve(context, event)
        finally:
            Skill._depth -= 1

    @classmethod
    def reset_depth(cls) -> None:
        """Safety valve used by reset paths after an aborted dispatch."""

        cls._depth = 0


from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .context import GameContext
