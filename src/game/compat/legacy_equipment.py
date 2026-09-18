"""Thin API shim for old callers; Engine V2 now owns equipment rules."""


class LegacyEquipmentCompatibility:
    def __init__(self, game):
        self.game = game
        self._tokens = []

    def install(self, context):
        return None

    def supports_v2_sha(self, attacker, defender, card):
        return card.name == "SHA"
