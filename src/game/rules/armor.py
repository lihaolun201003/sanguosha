"""One armor-effectiveness query shared by every armor skill."""


class ArmorRule:
    @staticmethod
    def is_effective(source, target, card=None):
        weapon = source.get_equipment("weapon") if source is not None else None
        return weapon is None or weapon.name != "QINGGANG"
