"""Controller boundary shared by local humans and AI."""


class PlayerController:
    def __init__(self, game, player):
        self.game = game
        self.player = player

    def submit(self, action):
        if action.actor is not self.player:
            raise ValueError("controller cannot submit an action for another player")
        return self.game.submit_action(action)
