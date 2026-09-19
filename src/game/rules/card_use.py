"""Card-use legality queries for migrated cards."""


def validate_sha_use(game, action):
    actor = action.actor
    targets = list(action.targets)

    if game.game_over:
        return False, "游戏已经结束。"
    if action.card.name != "SHA":
        return False, "当前 Engine V2 只支持【杀】。"
    if not any(card is action.card for card in actor.hand):
        return False, "这张牌已经不在使用者手牌中。"
    if len(targets) != 1:
        return False, "当前【杀】必须指定一个目标。"

    target = targets[0]
    if (
        actor.sha_used
        and not action.ignore_usage_limit
        and not game.can_use_unlimited_sha(actor)
    ):
        return False, "本回合已经使用过【杀】。"

    if not game.can_attack(actor, target):
        return False, "攻击距离不足。"
    return True, ""
