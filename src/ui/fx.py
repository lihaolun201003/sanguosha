"""Presentation-only effects driven by engine events.

The rules layer never waits for an animation: this module only subscribes to
read-only events and keeps its own timers.
"""

from src.game.atoms_v2 import RecoverHpAtom
from src.game.engine import EventType

from . import theme

FLASH_TIME = 0.45
SHAKE_TIME = 0.30
FLOAT_TIME = 1.00
JUDGE_TIME = 1.80
TURN_BANNER_TIME = 1.30


class FloatText:
    def __init__(self, text, position, color, life=FLOAT_TIME):
        self.text = text
        self.position = position
        self.color = color
        self.life = life
        self.max_life = life

    @property
    def alpha(self):
        return int(255 * max(0.0, self.life / self.max_life))

    @property
    def offset(self):
        return int(34 * (1 - self.life / self.max_life))


class Effects:
    """Collects engine events into short visual reactions."""

    def __init__(self):
        self.game = None
        self._tokens = []
        self._seat_flash = {}
        self._seat_shake = {}
        self.floats = []
        self.judge_info = None
        self.judge_timer = 0.0
        self.turn_banner = None
        self.turn_timer = 0.0
        self.last_draw_pulse = 0.0
        self.last_discard_pulse = 0.0

    # ==================================================
    # 订阅
    # ==================================================

    def attach(self, game):
        if self.game is game:
            return
        self.detach()
        self.game = game
        dispatcher = game.context.events
        subscriptions = (
            (EventType.DAMAGE_APPLIED, self._on_damage),
            (EventType.ATOM_AFTER, self._on_atom),
            (EventType.JUDGE_REVEALED, self._on_judge),
            (EventType.TURN_START, self._on_turn_start),
            (EventType.DEATH, self._on_death),
            (EventType.CHAIN_STATE_CHANGED, self._on_chain),
            (EventType.DYING_ENTERED, self._on_dying),
            (EventType.CARD_USED, self._on_card_used),
        )
        for event_name, handler in subscriptions:
            self._tokens.append(
                dispatcher.subscribe(event_name, handler, owner=self)
            )

    def detach(self):
        if self.game is not None:
            for token in self._tokens:
                self.game.context.events.unsubscribe(token)
        self._tokens = []

    def reset(self):
        self._seat_flash.clear()
        self._seat_shake.clear()
        self.floats.clear()
        self.judge_info = None
        self.judge_timer = 0.0
        self.turn_banner = None
        self.turn_timer = 0.0

    # ==================================================
    # 事件处理
    # ==================================================

    def _anchor(self, player):
        rect = self._seat_rect(player)
        if rect is not None:
            return rect.centerx, rect.y + 20
        return 500, 300

    def _seat_rect(self, player):
        if self.game is None or player is None:
            return None
        layout = getattr(self, "_layout", None)
        if layout is None:
            return None
        return layout.seat_rect(player)

    def set_layout(self, layout):
        """Renderer hands over the frame layout so effects can anchor to seats."""

        self._layout = layout

    def _on_damage(self, _context, event):
        damage = event.payload.get("damage")
        amount = event.payload.get("amount", 0)
        target = getattr(damage, "target", None) or event.target
        if target is None or amount <= 0:
            return
        self._seat_flash[target] = (FLASH_TIME, theme.DANGER)
        self._seat_shake[target] = SHAKE_TIME
        x, y = self._anchor(target)
        self.floats.append(FloatText("-" + str(amount), (x, y), (238, 122, 108)))

    def _on_atom(self, _context, event):
        atom = event.payload.get("atom")
        result = event.payload.get("result")
        if isinstance(atom, RecoverHpAtom):
            amount = getattr(result, "data", {}).get("amount", 0)
            if amount <= 0:
                return
            target = atom.target
            self._seat_flash[target] = (FLASH_TIME, theme.HEAL)
            x, y = self._anchor(target)
            self.floats.append(FloatText("+" + str(amount), (x, y), (146, 226, 160)))
        elif atom.__class__.__name__ == "DrawCardsAtom":
            self.last_draw_pulse = 0.5
        elif atom.__class__.__name__ == "MoveCardAtom":
            destination = getattr(atom, "destination", None)
            if self.game is not None and destination is self.game.deck.discard_pile:
                self.last_discard_pulse = 0.5

    def _on_judge(self, _context, event):
        result = event.payload.get("result")
        if result is None:
            return
        self.judge_info = {
            "card": result.card,
            "owner_name": getattr(result.target, "name", ""),
            "reason_text": _judge_reason_text(result.reason),
            "result_text": _judge_result_text(result),
        }
        self.judge_timer = JUDGE_TIME

    def _on_turn_start(self, _context, event):
        player = event.source
        if player is None:
            return
        self.turn_banner = {"text": getattr(player, "name", "") + " 的回合"}
        self.turn_timer = TURN_BANNER_TIME
        self._seat_flash[player] = (FLASH_TIME, theme.GOLD)

    def _on_death(self, _context, event):
        player = event.target
        if player is None:
            return
        x, y = self._anchor(player)
        self.floats.append(FloatText("阵亡", (x, y), (222, 142, 132)))

    def _on_chain(self, _context, event):
        player = event.target
        if player is None:
            return
        chained = event.payload.get("chained")
        x, y = self._anchor(player)
        color = theme.CHAIN if chained else (168, 220, 180)
        self.floats.append(FloatText("横置" if chained else "重置", (x, y), color))

    def _on_dying(self, _context, event):
        player = event.target
        if player is None:
            return
        self._seat_flash[player] = (FLASH_TIME, theme.DANGER)
        x, y = self._anchor(player)
        self.floats.append(FloatText("濒死", (x, y), (240, 170, 120)))

    def _on_card_used(self, _context, event):
        self.last_play_pulse = 0.45

    # ==================================================
    # 每帧推进
    # ==================================================

    def update(self, dt):
        for player, (remaining, _color) in list(self._seat_flash.items()):
            remaining -= dt
            if remaining <= 0:
                del self._seat_flash[player]
            else:
                self._seat_flash[player] = (remaining, _color)
        for player, remaining in list(self._seat_shake.items()):
            remaining -= dt
            if remaining <= 0:
                del self._seat_shake[player]
            else:
                self._seat_shake[player] = remaining

        for item in list(self.floats):
            item.life -= dt
            if item.life <= 0:
                self.floats.remove(item)

        if self.judge_timer > 0:
            self.judge_timer -= dt
            if self.judge_timer <= 0:
                self.judge_info = None
        if self.turn_timer > 0:
            self.turn_timer -= dt
            if self.turn_timer <= 0:
                self.turn_banner = None

        for name in ("last_draw_pulse", "last_discard_pulse", "last_play_pulse"):
            value = getattr(self, name, 0.0)
            if value > 0:
                setattr(self, name, max(0.0, value - dt))

    # ==================================================
    # 查询
    # ==================================================

    def seat_flash(self, player):
        entry = self._seat_flash.get(player)
        if entry is None:
            return 0.0, theme.DANGER
        remaining, color = entry
        return max(0.0, remaining / FLASH_TIME), color

    def seat_shake(self, player):
        remaining = self._seat_shake.get(player, 0.0)
        if remaining <= 0:
            return 0
        ratio = remaining / SHAKE_TIME
        import math

        return int(math.sin(remaining * 46) * 5 * ratio)

    def judge_display(self):
        if self.judge_info is None or self.judge_timer <= 0:
            return None
        info = dict(self.judge_info)
        remain_ratio = self.judge_timer / JUDGE_TIME
        info["alpha"] = int(255 * min(1.0, remain_ratio * 3))
        return info

    def turn_display(self):
        if self.turn_banner is None or self.turn_timer <= 0:
            return None
        info = dict(self.turn_banner)
        ratio = self.turn_timer / TURN_BANNER_TIME
        info["alpha"] = int(255 * min(1.0, ratio * 2.2))
        return info


def _judge_reason_text(reason):
    return {
        "lebu": "乐不思蜀",
        "bingliang": "兵粮寸断",
        "shandian": "闪电",
        "bagua": "八卦阵",
    }.get(reason, "")


def _judge_result_text(result):
    card = result.card
    if card is None:
        return ""
    return card.suit_name + str(card.rank)
