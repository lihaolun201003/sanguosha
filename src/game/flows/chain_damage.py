"""Sequential elemental chain propagation with shared visited state."""

from uuid import uuid4

from src.game.engine import Event, EventType, Flow, FlowStatus


class ChainDamageFlow(Flow):
    def __init__(self, engine, *, source, card, nature, amount, targets, visited=None, chain_id=None, on_complete=None):
        super().__init__(engine.context)
        self.engine = engine
        self.source = source
        self.card = card
        self.nature = nature
        self.amount = amount
        self.targets = list(targets)
        self.visited = set(visited or ())
        self.chain_id = chain_id or str(uuid4())
        self.on_complete = on_complete
        self.index = 0

    def advance(self, response=None):
        return self._next()

    def _next(self):
        from .damage import DamageContext, DamageFlow
        while self.index < len(self.targets):
            if self.engine.game.game_over:
                # 对局已经结束：不再继续结算剩余传播，也不留下等待请求。
                break
            target = self.targets[self.index]
            self.index += 1
            if target.hp <= 0 or target in self.visited:
                continue
            self.visited.add(target)
            if target.chained:
                target.chained = False
                self.context.emit(Event(EventType.CHAIN_STATE_CHANGED, source=self.source, target=target, payload={"chained": False, "chain_id": self.chain_id}))
            child = DamageFlow(
                self.engine,
                DamageContext(
                    self.source, target, self.amount, self.nature, self.card,
                    chain_id=self.chain_id, is_chain_damage=True,
                    visited_players=self.visited,
                ),
                on_complete=lambda _result: self._after_child(),
            )
            result = child.start()
            if result.status is FlowStatus.WAITING:
                return self.wait(child)
            return self.current_result()
        result = self.complete({"chain_id": self.chain_id, "visited": self.visited})
        self.notify_on_complete(result)
        return result

    def _after_child(self):
        if self.status is FlowStatus.WAITING:
            self.status = FlowStatus.RUNNING
            self.pending_request = None
        return self._next()
