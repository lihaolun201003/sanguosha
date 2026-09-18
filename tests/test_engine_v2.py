import unittest
from dataclasses import dataclass

from src.game.engine import (
    Atom,
    AtomResult,
    Event,
    EventType,
    Flow,
    FlowStatus,
    GameContext,
    Skill,
    SkillBinding,
)


@dataclass
class CounterState:
    value: int = 0


class IncrementAtom(Atom):
    def __init__(self, amount):
        self.amount = amount

    def apply(self, context):
        context.state.value += self.amount
        return AtomResult(data={"amount": self.amount})


class RecordingSkill(Skill):
    id = "recording"
    name = "Recording Skill"

    def __init__(self, log):
        super().__init__()
        self.log = log

    def bindings(self):
        return [SkillBinding(EventType.ATOM_AFTER, priority=5)]

    def resolve(self, context, event):
        self.log.append((event.name, context.state.value))


class PromptFlow(Flow):
    def advance(self, response=None):
        if response is None:
            return self.wait({"kind": "confirm"})
        return self.complete(response)


class EngineV2Tests(unittest.TestCase):
    def test_atom_is_observable_and_changes_state_once(self):
        context = GameContext(CounterState())
        seen = []
        context.events.subscribe(
            EventType.ATOM_BEFORE,
            lambda _, event: seen.append(event.name),
        )
        context.events.subscribe(
            EventType.ATOM_AFTER,
            lambda _, event: seen.append(event.name),
        )

        result = context.apply(IncrementAtom(2))

        self.assertTrue(result.applied)
        self.assertEqual(context.state.value, 2)
        self.assertEqual(
            seen,
            [EventType.ATOM_BEFORE, EventType.ATOM_AFTER],
        )

    def test_before_hook_can_cancel_atom(self):
        context = GameContext(CounterState())
        context.events.subscribe(
            EventType.ATOM_BEFORE,
            lambda _, event: event.cancel(),
        )

        result = context.apply(IncrementAtom(3))

        self.assertTrue(result.cancelled)
        self.assertFalse(result.applied)
        self.assertEqual(context.state.value, 0)

    def test_skill_install_and_uninstall(self):
        context = GameContext(CounterState())
        log = []
        skill = RecordingSkill(log)
        skill.install(context)
        context.apply(IncrementAtom(1))
        skill.uninstall(context)
        context.apply(IncrementAtom(1))

        self.assertEqual(log, [(EventType.ATOM_AFTER, 1)])

    def test_flow_waits_and_resumes_without_async_runtime(self):
        context = GameContext(CounterState())
        flow = PromptFlow(context)

        waiting = flow.start()
        completed = flow.resume(True)

        self.assertEqual(waiting.status, FlowStatus.WAITING)
        self.assertEqual(completed.status, FlowStatus.COMPLETED)
        self.assertTrue(completed.value)

    def test_dispatch_uses_priority_and_stable_registration_order(self):
        context = GameContext(CounterState())
        order = []
        context.events.subscribe("test", lambda *_: order.append("low"))
        context.events.subscribe(
            "test", lambda *_: order.append("high-first"), priority=10
        )
        context.events.subscribe(
            "test", lambda *_: order.append("high-second"), priority=10
        )

        context.emit(Event("test"))

        self.assertEqual(order, ["high-first", "high-second", "low"])

    def test_legacy_game_exposes_side_by_side_context(self):
        from src.game import Game

        game = Game()

        self.assertIs(game.context.state, game)
        self.assertIs(game.context.services["engine"], game.engine)
        self.assertIsNone(game.pending_request)


if __name__ == "__main__":
    unittest.main()
