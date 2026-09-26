"""Reproduce suspected win-rate distortions without modifying game rules.

Run from the repository root: .venv/Scripts/python tools/winrate_diagnosis.py
Outputs observations, not a claim that the current rules pass validation.
"""

import json
import os
import random
import sys
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.card import Card
from src.game import Game
from src.game.atoms_v2 import MoveCardAtom
from src.game.engine import ActivateSkillAction, SelectTargetsAction, UseCardAction
from src.game.flows.damage import DamageContext, DamageFlow
from src.game.skills.expansions.wind import JushouFlow, JushouOld, Jushou
from src.game.skills.mechanics import start_pindian
from src.player import ControllerType


def card(name="SHA", rank="7", suit="spade"):
    return Card(name=name, category="basic" if name in ("SHA", "SHAN", "TAO") else "trick",
                color=(210, 200, 175), suit=suit, rank=rank)


def table(generals, *, ai=True):
    game = Game(ai_count=len(generals) - 1)
    game.rng.seed(926)
    game.general_assignments = dict(enumerate(generals))
    game.start_local_battle(len(generals) - 1)
    game.actions.clear()
    game.engine.pending.clear()
    game.engine.active_flows.clear()
    game.engine._deferred_resumes.clear()
    game.engine._flow_stack.clear()
    game.active_turn_flow = None
    game.response.clear()
    game.choice.clear()
    for name in ("pending_selection", "pending_target_selection", "pending_skill_input",
                 "pending_skill_picker", "pending_card_action", "pending_view_as"):
        setattr(game, name, None)
    game.turn_phase = None
    game.skipped_phases = set()
    game.ai_pacing = False
    game.ai_rng = random.Random(926)
    game.controllers.clear()
    players = sorted(game.players, key=lambda p: p.seat)
    for player, general_id in zip(players, generals):
        assert player.general_id == general_id
        player.controller_type = ControllerType.AI if ai else ControllerType.HUMAN
        player.clear_turn_state()
        player.hand.clear()
        player.judgement_zone.clear()
        for slot in player.equipment:
            player.equipment[slot] = None
    game.deck.draw_pile[:] = [card() for _ in range(30)]
    game.deck.discard_pile.clear()
    game.processing_zone.clear()
    game.public_card_pool.clear()
    game.current_turn_player = players[0]
    game.phase = "play"
    game.player = None if ai else players[0]
    return game, players


def settle(game, limit=1000):
    for _ in range(limit):
        if game.pending_request is not None:
            game.engine.present_or_auto_resolve(game.pending_request)
        if not game.busy and game.pending_request is None:
            return
        game.update(0.2)
    raise AssertionError("Probe did not settle")


def jushou(general_id, skill_class):
    game, (owner, _) = table([general_id, "zhangfei"])
    JushouFlow(game.engine, skill_class(owner), game).start()
    settle(game)
    before = len(owner.hand)
    mark_before = owner.mark("jushou_old", "skip_turn")
    face_before = owner.face_up
    game.actions.clear()
    game.start_turn(owner)
    # Observe at turn entry, before playing any queued AI actions.
    return {"general_id": general_id, "cards_after_jushou": before,
            "skip_mark_before": mark_before, "face_up_before": face_before,
            "cards_at_next_turn_entry": len(owner.hand),
            "skip_mark_after": owner.mark("jushou_old", "skip_turn"),
            "entered_turn_flow": game.active_turn_flow is not None}


def juxiang_use(own_use):
    game, players = table(["zhurong", "zhangfei", "guanyu"])
    owner = players[0]
    actor = owner if own_use else players[1]
    game.current_turn_player = actor
    used = card("NANMAN")
    actor.hand.append(used)
    before = [p.hp for p in players]
    from src.game.rules import target_candidates, TargetRule
    targets = target_candidates(game, actor, TargetRule.ALL_OTHERS, card=used)
    result = game.engine.submit(UseCardAction(actor, used, targets))
    assert result.status.value != "cancelled", "Nanman fixture must be a legal card use"
    settle(game)
    return {"user_is_zhurong": own_use, "status": result.status.value,
            "card_returned_to_zhurong": used in owner.hand,
            "card_in_discard": used in game.deck.discard_pile,
            "hp_before": before, "hp_after": [p.hp for p in players]}


def juxiang_discard():
    game, (owner, other) = table(["zhurong", "zhangfei"])
    unused = card("NANMAN")
    other.hand.append(unused)
    game.context.apply(MoveCardAtom(unused, other.hand, game.deck.discard_pile, reason="discard"))
    return {"unused_discarded_nanman_acquired": unused in owner.hand}


def mass_target_mismatch(general_id):
    from src.game.available_actions import AvailableActions
    from src.game.rules import target_candidates, TargetRule
    game, (immune, actor, _) = table([general_id, "zhangfei", "guanyu"])
    game.current_turn_player = actor
    used = card("NANMAN")
    actor.hand.append(used)
    generated = list(AvailableActions(game).target_profile(actor, card=used).players)
    rule_targets = target_candidates(game, actor, TargetRule.ALL_OTHERS, card=used)
    effect = game.engine.card_effects.require(used)
    valid, reason = effect.can_use(game, UseCardAction(actor, used, generated))
    control_valid, _ = effect.can_use(game, UseCardAction(actor, used, rule_targets))
    return {"immune_general": immune.general_id,
            "generated_targets": [p.general_id for p in generated],
            "rule_targets": [p.general_id for p in rule_targets],
            "generated_action_valid": valid, "rejection_reason": reason,
            "ai_builds_action": game.get_controller(actor)._build_action(used) is not None,
            "rule_target_action_valid": control_valid}


def pindian():
    game, (owner, other) = table(["taishici", "zhangfei"])
    owner.hand[:] = [card("SHAN", "A"), card("TAO", "K"), card("SHA", "9")]
    other.hand[:] = [card("SHA", "7")]
    flow = start_pindian(game.engine, owner, other, reason="tianyi")
    settle(game)
    return {"available_ranks": ["A", "K", "9"],
            "ai_selected_rank": flow.result.initiator_card.rank,
            "opponent_rank": flow.result.target_card.rank,
            "initiator_won": flow.result.initiator_wins}


def active_pindian(general_id, skill_id):
    game, (owner, other) = table([general_id, "zhangfei"])
    owner.hand[:] = [card("SHAN", "A"), card("TAO", "K"), card("SHA", "9")]
    other.hand[:] = [card("SHA", "7")]
    controller = game.get_controller(owner)
    activated = controller._try_active_skill()
    settle(game)
    return {"general_id": general_id, "activated": activated,
            "won": bool(owner.skill_state.get(skill_id, "won", 0)),
            "lost": bool(owner.skill_state.get(skill_id, "lost", 0)),
            "remaining_ranks": [c.rank for c in owner.hand],
            "initiator_cards_spent": 3 - len(owner.hand),
            "can_exploit_win_with_remaining_sha": any(c.name == "SHA" for c in owner.hand)}


def one_card_pindian(general_id, skill_id):
    game, (owner, other) = table([general_id, "zhangfei"])
    selected = card("SHA", "K")
    owner.hand[:] = [selected]
    other.hand[:] = [card("SHA", "7")]
    ok, message = game.engine.submit(ActivateSkillAction(owner, skill_id, target=other, cards=[selected]))
    settle(game)
    return {"general_id": general_id, "activation_accepted": ok, "message": message,
            "owner_hand": len(owner.hand), "opponent_hand": len(other.hand),
            "won": bool(owner.skill_state.get(skill_id, "won", 0)),
            "lost": bool(owner.skill_state.get(skill_id, "lost", 0)),
            "used": bool(owner.skill_state.get(skill_id, "used", 0))}


def jieming_ai():
    game, (enemy, owner, _) = table(["zhangfei", "xunyu", "guanyu"])
    DamageFlow(game.engine, DamageContext(enemy, owner, 1)).start()
    settle(game)
    return {"xunyu_seat": owner.seat, "enemy_seat": enemy.seat,
            "xunyu_cards_after": len(owner.hand), "enemy_cards_after": len(enemy.hand)}


def jieming_repeat():
    game, (first, owner, second) = table(["zhangfei", "xunyu", "guanyu"], ai=False)
    DamageFlow(game.engine, DamageContext(first, owner, 2)).start()
    windows = 0
    for _ in range(1000):
        request = game.pending_request
        if request is not None:
            assert request.context.get("reason") == "jieming", request.context
            target = first if windows == 0 else second
            windows += 1
            game.engine.submit(SelectTargetsAction(owner, request.request_id, [target]))
        elif not game.busy:
            break
        else:
            game.update(0.2)
    else:
        raise AssertionError("Damage probe did not settle")
    return {"damage": 2, "jieming_target_windows": windows,
            "first_recipient_cards": len(first.hand), "second_recipient_cards": len(second.hand)}


def shenfen_order():
    game, (owner, victim, _) = table(["shen_lvbu", "xunyu", "zhangfei"], ai=False)
    owner.set_mark("kuangbao", "rage", 6)
    victim.hand[:] = [card() for _ in range(4)]
    game.engine.submit(ActivateSkillAction(owner, "shenfen"))
    request = game.pending_request
    return {"pending_reason": request.context.get("reason") if request else None,
            "victim_hp": victim.hp, "victim_hand_while_damage_skill_pending": len(victim.hand),
            "lvbu_face_up_while_damage_skill_pending": owner.face_up}


def main():
    observations = {
        "jushou_old": jushou("caoren", JushouOld),
        "jushou_2010_control": jushou("caoren_2010", Jushou),
        "juxiang_own_use": juxiang_use(True),
        "juxiang_other_use_control": juxiang_use(False),
        "juxiang_unused_discard": juxiang_discard(),
        "nanman_targets_zhurong": mass_target_mismatch("zhurong"),
        "nanman_targets_menghuo": mass_target_mismatch("menghuo"),
        "ai_pindian": pindian(),
        "ai_tianyi": active_pindian("taishici", "tianyi"),
        "ai_xianzhen": active_pindian("gaoshun", "xianzhen"),
        "ai_quhu": active_pindian("xunyu", "quhu"),
        "single_card_tianyi": one_card_pindian("taishici", "tianyi"),
        "single_card_xianzhen": one_card_pindian("gaoshun", "xianzhen"),
        "single_card_quhu": one_card_pindian("xunyu", "quhu"),
        "ai_jieming": jieming_ai(),
        "jieming_two_damage": jieming_repeat(),
        "shenfen_damage_pending": shenfen_order(),
    }
    destination = ROOT / "docs/reports/winrate_diagnosis_probe.json"
    destination.write_text(json.dumps(observations, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(observations, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
