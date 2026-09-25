"""Measure how the pacing setting changes real-time game rhythm.

Counts the frames (at 60 FPS) needed for one full table round at each speed
step, so the effect of the in-game speed control can be reported objectively.

Run with ``python tools/pacing_measure.py``.
"""

import os
import random

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame

from src.game import Game
from tools.multiplayer_smoke import human_respond


def measure_round(speed, ai_count=3, frame_limit=200000, deck_seed=7):
    """Frames needed from the human ending a turn back to their next turn."""

    random.seed(deck_seed)
    game = Game(ai_count=ai_count)
    game.ai_pacing = True
    game.set_speed(speed)
    game.start_single_player()

    frames = 0
    started = False
    for _ in range(frame_limit):
        game.update(1 / 60)
        frames += 1

        if game.game_over:
            return frames, "game_over"

        if game.busy:
            continue

        request = game.pending_request
        if request is not None:
            # AI 的请求在节奏模式下会自己排队响应；这里只替真人作答。
            if getattr(request.target, "is_human", False):
                human_respond(game, request)
            continue

        if game.current_turn_player is not game.player:
            continue

        if game.phase == "discard":
            game.player_discard(len(game.player.hand) - 1, (0, 0, 10, 10))
            continue

        if game.phase != "play":
            continue

        if not started:
            started = True
            game.end_player_turn()
            continue
        return frames, "back to human"
    return frames, "frame limit"


def main():
    pygame.init()
    pygame.display.set_mode((320, 240))

    seeds = (11, 12, 13)
    print("每轮「玩家结束回合 → 回到玩家」所需帧数（60 FPS，3 种开局取平均）：")
    baseline = None
    for speed in Game.SPEED_STEPS:
        samples = [measure_round(speed, deck_seed=seed)[0] for seed in seeds]
        average = sum(samples) / len(samples)
        if baseline is None:
            baseline = average
        print("  速度 %-5s → 平均 %6.0f 帧（约 %5.1f 秒，相对最慢档 %.2f×）" % (
            ("%g×" % speed), average, average / 60.0, baseline / average))

    pygame.quit()


if __name__ == "__main__":
    main()
