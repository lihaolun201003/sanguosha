"""Generic card-use flow backed by the CardEffectRegistry."""

from src.game.atoms_v2 import MoveCardAtom
from src.game.engine import Event, EventType, Flow, FlowResult, FlowStatus


class UseCardFlow(Flow):
    def __init__(self, engine, action, effect):
        super().__init__(engine.context)
        self.engine = engine
        self.game = engine.game
        self.action = action
        self.effect = effect
        self.actor = action.actor
        self.targets = list(action.targets)
        self.target = self.targets[0] if self.targets else None
        self.card = action.card
        self.stage = "ready"
        self.base_damage = int(action.metadata.get("base_damage", 1))
        self.extra_targets = list(action.metadata.get("extra_targets", self.targets[1:]))
        self.cancelled = False
        self.keep_processing_card = False

    def advance(self, response=None):
        if self.stage == "ready":
            valid, message = self.effect.can_use(self.game, self.action)
            if not valid:
                self.game.message = message
                return self.cancel(message)

            before = self.context.emit(
                Event(
                    EventType.CARD_USE_BEFORE,
                    source=self.actor,
                    target=self.target,
                    payload={"card": self.card, "flow": self},
                )
            )
            if before.cancelled:
                return self.cancel("card use cancelled")

            for material in self.action.metadata.get("materials", ()):
                self.context.apply(
                    MoveCardAtom(
                        material,
                        source=self.actor.hand,
                        destination=self.game.deck.discard_pile,
                    )
                )

            if not getattr(self.card, "_virtual", False):
                self.context.apply(MoveCardAtom(self.card, source=self.actor.hand, destination=self.game.processing_zone))
            self.engine.animate_card_use(self.action)

            if self.card.name == "SHA" and self.actor is self.game.player:
                self.game.sha_used = True
                if self.game.player_wine_buff:
                    self.base_damage += 1
                    self.game.player_wine_buff = False
                    self.game.wine_sha_required = False
                self.game.message = "你使用了【" + self.card.display_name + "】。"
            elif self.card.name == "SHA":
                if self.game.enemy_wine_buff:
                    self.base_damage += 1
                    self.game.enemy_wine_buff = False
                self.game.message = self.actor.name + "使用了【" + self.card.display_name + "】。"

            self.context.emit(Event(EventType.CARD_USED, source=self.actor, payload={"card": self.card, "flow": self, "targets": self.targets}))
            for target in self.targets:
                for event_type in (EventType.TARGET_SELECTED, EventType.BECOME_TARGET):
                    self.context.emit(Event(event_type, source=self.actor, target=target, payload={"card": self.card, "flow": self}))

            if self.action.metadata.get("zhuque_fire") and self.card.name == "SHA" and self.card.nature == "normal":
                self.card._original_nature = self.card.nature
                self.card.nature = "fire"

            if self.card.name != "SHA":
                self.game.message = self.actor.name + "使用了【" + self.card.display_name + "】。"
            self.game.add_log(self.actor.name + " 使用【" + self.card.display_name + "】" +
                              ((" → " + "、".join(target.name for target in self.targets)) if self.targets else ""))

            if self.effect.cancellable_by_wuxie and not getattr(self.effect, "per_target_wuxie", False) and not self.action.metadata.get("skip_wuxie"):
                from .wuxie import WuxieResponseChain
                self.stage = "wuxie"
                chain = WuxieResponseChain(
                    self.engine,
                    self.actor,
                    self.card,
                    self.targets,
                    self._after_wuxie,
                )
                self.wait(chain)
                chain.start()
                return self.current_result()

            self.stage = "effect"
            return self.effect.begin(self)

        if self.stage in {
            "waiting_for_shan", "effect_waiting", "bagua_confirm",
            "qinglong_confirm", "qinglong_select", "guanshi_confirm",
            "guanshi_select", "hanbing_confirm", "hanbing_select",
            "cixiong_option", "cixiong_select", "qilin_confirm", "qilin_select",
        }:
            return self.effect.resume(self, response)

        if self.stage == "waiting_for_damage":
            return FlowResult(self.status, self.result)

        raise RuntimeError("UseCardFlow cannot advance from stage " + self.stage)

    def _after_wuxie(self, nullified):
        if self.status in (FlowStatus.COMPLETED, FlowStatus.CANCELLED):
            return self.current_result()
        self.status = FlowStatus.RUNNING
        self.pending_request = None
        if nullified:
            self.game.message = "【" + self.card.display_name + "】被【无懈可击】抵消。"
            return self.finish(cancelled=True)
        self.stage = "effect"
        return self.effect.begin(self)

    def emit_card_event(self, event_type, target=None, **payload):
        return self.context.emit(
            Event(event_type, source=self.actor, target=target, payload={"card": self.card, "flow": self, **payload})
        )

    def start_damage(self):
        from .damage import DamageContext, DamageFlow

        equipment_result = self.engine.equipment.before_sha_damage(self)
        if equipment_result is not None:
            return equipment_result
        self.stage = "waiting_for_damage"
        damage = DamageContext(
            source=self.actor,
            target=self.target,
            amount=self.base_damage,
            nature=getattr(self.card, "nature", "normal"),
            card=self.card,
        )
        child = DamageFlow(
            self.engine,
            damage,
            on_complete=self._after_damage,
        )
        child_result = child.start()
        if child_result.status is FlowStatus.WAITING:
            return self.wait(child)
        return FlowResult(self.status, self.result)

    def _after_damage(self, _result):
        if self.status not in (FlowStatus.COMPLETED, FlowStatus.CANCELLED):
            post = self.engine.equipment.after_sha_damage(self)
            if post is None:
                self.finish(cancelled=False)

    def finish(self, *, cancelled):
        if self.status is FlowStatus.COMPLETED:
            return FlowResult(self.status, self.result)
        self.cancelled = cancelled
        self.context.emit(
            Event(
                EventType.CARD_EFFECT_AFTER,
                source=self.actor,
                target=self.target,
                payload={
                    "card": self.card,
                    "flow": self,
                    "cancelled": cancelled,
                },
            )
        )
        if not getattr(self.card, "_virtual", False) and not self.keep_processing_card:
            self.engine.discard_processing_card(self.card)
        self.context.emit(
            Event(
                EventType.CARD_USE_FINISHED,
                source=self.actor,
                target=self.target,
                payload={
                    "card": self.card,
                    "flow": self,
                    "cancelled": cancelled,
                },
            )
        )
        result = self.complete(
            {"card": self.card, "cancelled": cancelled}
        )
        if self.action.on_complete is not None and not self.game.game_over:
            self.action.on_complete(result)
        return result
