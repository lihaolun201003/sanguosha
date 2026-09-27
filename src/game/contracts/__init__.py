"""统一契约层（Phase 18）：交互契约 + 演出契约 + 高层运行状态。

    Game / Flow
        └─→ InteractionSchema / PresentationSchema
                └─→ ViewModel / Network
                        └─→ Local UI / Remote UI

UI 不再读一堆 Game 状态、再按 ``card.name`` / ``request.kind`` / ``skill.name``
自己猜"现在该显示什么、什么能点"。两侧（本机与联机）读的是同一份 schema。
"""

from .interaction import (                                          # noqa: F401
    ACTION_CANCEL,
    ACTION_END_PHASE,
    ACTION_PASS,
    ACTION_SKILL,
    ACTION_SUBMIT,
    InteractionKind,
    InteractionSchema,
    build_interaction,
    build_play_interaction,
    group_interaction,
    pending_member_id,
)
from .local_input import (                                          # noqa: F401
    LOCAL_SLOT_ATTRS,
    SLOT_LABELS,
    describe_slots,
    local_awaiting_input,
    local_interaction_slots,
    local_response_live,
    request_targets_player,
)
from .presentation import (                                         # noqa: F401
    BLOCKING_KINDS,
    KIND_BY_ENGINE_EVENT,
    KIND_BY_FACT,
    KIND_BY_STEP,
    PresentationGate,
    PresentationSchema,
    RuntimeState,
    is_blocking,
    missing_fact_kinds,
    runtime_state,
)

__all__ = [
    "InteractionKind",
    "InteractionSchema",
    "build_interaction",
    "build_play_interaction",
    "group_interaction",
    "pending_member_id",
    "LOCAL_SLOT_ATTRS",
    "SLOT_LABELS",
    "describe_slots",
    "local_awaiting_input",
    "local_interaction_slots",
    "local_response_live",
    "request_targets_player",
    "PresentationSchema",
    "PresentationGate",
    "RuntimeState",
    "runtime_state",
    "is_blocking",
    "BLOCKING_KINDS",
    "KIND_BY_ENGINE_EVENT",
    "KIND_BY_FACT",
    "KIND_BY_STEP",
    "missing_fact_kinds",
    "ACTION_SUBMIT",
    "ACTION_PASS",
    "ACTION_CANCEL",
    "ACTION_END_PHASE",
    "ACTION_SKILL",
]
