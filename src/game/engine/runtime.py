"""Engine V2 action dispatcher and Legacy UI/animation adapters."""

from src.actions import MoveCardAction
from src.constants import (
    ENEMY_HAND_RECT,
    PLAYER_HAND_SOURCE_RECT,
    RESPONSE_CARD_RECT,
    TABLE_CARD_RECT,
    DISCARD_PILE_RECT,
)

from src.game.atoms_v2 import MoveCardAtom
from src.game.card_effects import create_default_registry
from src.game.compat import LegacyEquipmentCompatibility
from src.game.equipment_skills.system import EquipmentSkillController

from .domain_actions import (
    ChooseOptionAction,
    ConfirmPendingAction,
    PassPendingAction,
    RespondCardAction,
    SelectCardsAction,
    UseCardAction,
)
from .pending import PendingManager, PendingRequestType, PendingResolution


class GameEngine:
    def __init__(self, context):
        self.context = context
        self.game = context.state
        self.pending = PendingManager(context)
        self.compatibility = LegacyEquipmentCompatibility(self.game)
        self.card_effects = create_default_registry()
        self.equipment = EquipmentSkillController(self)
        self.active_flows = []
        context.services["engine"] = self

    def reset(self):
        self.pending.clear()
        self.active_flows.clear()

    def submit(self, action):
        if isinstance(action, UseCardAction):
            return self._use_card(action)
        if isinstance(action, RespondCardAction):
            return self._respond_card(action)
        if isinstance(action, PassPendingAction):
            return self._pass_pending(action)
        if isinstance(action, ConfirmPendingAction):
            return self._resolve_simple(action, confirmed=action.confirmed)
        if isinstance(action, SelectCardsAction):
            return self._resolve_simple(action, cards=tuple(action.cards))
        if isinstance(action, ChooseOptionAction):
            return self._resolve_simple(action, option=action.option)
        raise TypeError("unsupported GameAction: " + type(action).__name__)

    def _use_card(self, action):
        from src.game.flows import UseCardFlow

        effect = self.card_effects.require(action.card)
        flow = UseCardFlow(self, action, effect)
        self.active_flows.append(flow)
        result = flow.start()
        self._prune_flows()
        return result

    def _respond_card(self, action):
        request = self.pending.require(action.request_id)
        if request.request_type is not PendingRequestType.RESPOND_CARD:
            raise ValueError("PendingRequest does not accept a card response")
        if action.actor is not request.target:
            raise ValueError("action actor is not the requested responder")
        if action.card.name not in request.allowed_cards:
            raise ValueError("card is not allowed for this PendingRequest")
        if not any(card is action.card for card in action.actor.hand):
            raise ValueError("response card is no longer in responder hand")

        self.pending.take(action.request_id)
        self.game.response.clear()

        self.context.apply(
            MoveCardAtom(
                action.card,
                source=action.actor.hand,
                destination=self.game.processing_zone,
            )
        )
        self.context.apply(
            MoveCardAtom(
                action.card,
                source=self.game.processing_zone,
                destination=self.game.deck.discard_pile,
            )
        )
        self.animate_response_card(action.card, action.source_rect, action.actor)
        resolution = PendingResolution(
            request=request,
            actor=action.actor,
            card=action.card,
            source_rect=action.source_rect,
        )
        self.pending.emit_resolved(resolution)
        result = request.owner_flow.resume(resolution)
        self._prune_flows()
        return result

    def _pass_pending(self, action):
        request = self.pending.require(action.request_id)
        if action.actor is not request.target:
            raise ValueError("action actor is not the requested responder")
        self.pending.take(action.request_id)
        self.game.response.clear()
        resolution = PendingResolution(
            request=request,
            actor=action.actor,
            passed=True,
        )
        self.pending.emit_resolved(resolution)
        result = request.owner_flow.resume(resolution)
        self._prune_flows()
        return result

    def _resolve_simple(self, action, **values):
        request = self.pending.require(action.request_id)
        if action.actor is not request.target:
            raise ValueError("action actor is not the requested responder")
        if isinstance(action, ConfirmPendingAction):
            if request.request_type is not PendingRequestType.CONFIRM:
                raise ValueError("PendingRequest does not accept confirmation")
        elif isinstance(action, SelectCardsAction):
            if request.request_type is not PendingRequestType.SELECT_CARDS:
                raise ValueError("PendingRequest does not accept card selection")
            count = len(action.cards)
            if count < request.min_cards or count > request.max_cards:
                raise ValueError("selected card count is outside request bounds")
            candidates = request.context.get("candidates")
            if candidates is not None:
                for card in action.cards:
                    if not any(candidate is card for candidate in candidates):
                        raise ValueError("selected card is not a request candidate")
        elif isinstance(action, ChooseOptionAction):
            if request.request_type is not PendingRequestType.CHOOSE_OPTION:
                raise ValueError("PendingRequest does not accept an option")
            if action.option not in request.options:
                raise ValueError("option is not allowed for this request")
        self.pending.take(action.request_id)
        resolution = PendingResolution(
            request=request,
            actor=action.actor,
            **values,
        )
        self.pending.emit_resolved(resolution)
        result = request.owner_flow.resume(resolution)
        self._prune_flows()
        return result

    def present_or_auto_resolve(self, request):
        responder = request.target
        if getattr(responder.controller_type, "value", responder.controller_type) == "ai":
            # AI 与真人共用同一个 Action 接口：由 AIController 决策并提交
            # GameAction，引擎再恢复 Flow。
            self.game.get_controller(responder).respond(request)
            return

        if request.request_type is PendingRequestType.RESPOND_CARD:
            if (
                request.context.get("reason") == "wuxie_chain"
                and not any(card.name in request.allowed_cards for card in responder.hand)
            ):
                self.submit(PassPendingAction(responder, request.request_id))
                return
            self.game.response.request(
                prompt=request.prompt,
                allowed_cards=request.allowed_cards,
                on_card=(
                    lambda index, card, rect, request_id=request.request_id:
                    self.submit(
                        RespondCardAction(
                            responder,
                            request_id,
                            card,
                            rect,
                        )
                    )
                ),
                on_pass=(
                    lambda request_id=request.request_id:
                    self.submit(
                        PassPendingAction(
                            responder,
                            request_id,
                        )
                    )
                ),
            )
            return

        if request.request_type is PendingRequestType.CONFIRM:
            self.game.choice.request(
                title="装备技能", prompt=request.prompt,
                yes_label="发动", no_label="不发动",
                on_yes=lambda request_id=request.request_id: self.submit(ConfirmPendingAction(responder, request_id, True)),
                on_no=lambda request_id=request.request_id: self.submit(ConfirmPendingAction(responder, request_id, False)),
            )
            return

        if request.request_type is PendingRequestType.CHOOSE_OPTION:
            first = request.options[0]
            second = request.options[1] if len(request.options) > 1 else first
            self.game.choice.request(
                title="请选择", prompt=request.prompt,
                yes_label=str(first), no_label=str(second),
                on_yes=lambda value=first, request_id=request.request_id: self.submit(ChooseOptionAction(responder, request_id, value)),
                on_no=lambda value=second, request_id=request.request_id: self.submit(ChooseOptionAction(responder, request_id, value)),
            )
            return

        if request.request_type is PendingRequestType.SELECT_CARDS:
            owner = request.context.get("zone_owner", responder)
            zone = request.context.get("zone")
            if zone is None:
                zone = self._zone_name(owner, request)
            raw_candidates = list(request.context.get("candidates", ()))
            candidates = []
            for card in raw_candidates:
                key = None
                for slot, equipped in owner.equipment.items():
                    if equipped is card:
                        key = slot
                        break
                candidates.append((card, key))
            self.game.start_card_selection(
                zone=zone,
                owner=owner,
                candidates=candidates,
                number=request.min_cards,
                prompt=request.prompt,
                on_complete=lambda selected, request_id=request.request_id: self.submit(
                    SelectCardsAction(
                        responder,
                        request_id,
                        [item[0] for item in selected],
                    )
                ),
            )

    def _zone_name(self, owner, request):
        """Pick the human input region for a card-selection request.

        Own cards are picked by clicking the hand; another character's cards are
        laid out in the public pool area so every candidate stays clickable
        even when eight panels share the table.
        """

        if owner is self.game.player:
            return "hand"
        return "public_pool"


    def animate_card_use(self, action):
        start_rect = action.source_rect
        if start_rect is None:
            start_rect = (
                PLAYER_HAND_SOURCE_RECT
                if action.actor is self.game.player
                else ENEMY_HAND_RECT
            )
        self.game.actions.add(
            MoveCardAction(
                action.card,
                start_rect,
                TABLE_CARD_RECT,
                duration=0.30,
                on_finish=(
                    lambda card=action.card:
                    self.game.add_table_card(card, TABLE_CARD_RECT)
                ),
            )
        )

    def animate_response_card(self, card, source_rect, actor):
        start_rect = source_rect
        if start_rect is None:
            start_rect = (
                PLAYER_HAND_SOURCE_RECT
                if actor is self.game.player
                else ENEMY_HAND_RECT
            )
        self.game.actions.add(
            MoveCardAction(
                card,
                start_rect,
                RESPONSE_CARD_RECT,
                duration=0.28,
                on_finish=(
                    lambda response=card:
                    self.game.add_table_card(response, RESPONSE_CARD_RECT)
                ),
            )
        )
        self.game.actions.add(
            MoveCardAction(
                card,
                RESPONSE_CARD_RECT,
                DISCARD_PILE_RECT,
                duration=0.30,
                on_finish=(
                    lambda response=card:
                    self.game.remove_table_card(response)
                ),
            )
        )

    def discard_processing_card(self, card):
        source = (
            self.game.processing_zone
            if any(item is card for item in self.game.processing_zone)
            else None
        )
        self.discard_zone_card(card, source)

    def discard_zone_card(self, card, source):
        """Move a visible table card from a logical zone to the discard pile."""
        self.context.apply(
            MoveCardAtom(
                card,
                source=source,
                destination=self.game.deck.discard_pile,
            )
        )
        self.game.actions.add(
            MoveCardAction(
                card,
                TABLE_CARD_RECT,
                DISCARD_PILE_RECT,
                duration=0.30,
                on_finish=(
                    lambda used_card=card:
                    self.game.remove_table_card(used_card)
                ),
            )
        )

    def clear_pending_ui(self):
        self.pending.clear()
        self.game.response.clear()

    def run_death(self, dead_player, source=None, cause=None):
        from src.game.flows import DeathFlow

        return DeathFlow(
            self,
            dead_player=dead_player,
            source=source,
            cause=cause,
        ).start()

    def run_turn(self, player, on_complete=None):
        from src.game.flows import TurnFlow
        return TurnFlow(self, player, on_complete=on_complete).start()

    def _prune_flows(self):
        self.active_flows = [
            flow
            for flow in self.active_flows
            if flow.status.value in {"running", "waiting"}
        ]
