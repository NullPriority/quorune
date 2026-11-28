"""Commit a counter-bound combat rule and bind current declaration options."""

from ..continuous_effect_model import ContinuousObjectIdentity
from ..continuous_effect_state import commit_continuous_effect
from ..counter_bound_attack_model import CounterBoundAttackRestrictionIntent, ResolvedCounterBoundAttackRestriction


def apply_counter_bound_attack_restriction(host, intent: CounterBoundAttackRestrictionIntent):
    candidates = {card.ref: card for card in host.state.cards.values() if card.zone == "battlefield"}
    result = []
    for ref, logical_id in intent.subjects:
        card = candidates.get(ref)
        if (card is None or card.phased_out or card.logical_object_id != logical_id
                or card.counters.get(intent.counter_name, 0) <= 0):
            continue
        identity = ContinuousObjectIdentity(card.object_id, logical_id)
        effect = ResolvedCounterBoundAttackRestriction(
            host._next_ref("CE"), intent.source_id, host._next_zone_timestamp(),
            identity, intent.actor, intent.counter_name,
        )
        commit_continuous_effect(host.state, effect)
        result.append(effect.effect_id)
        host._log(
            intent.actor, "combat.declaration_restriction",
            f"{card.printed_name} cannot attack {intent.actor} or their planeswalkers while marked.",
            {"card": ref, "protected_player": intent.actor, "counter": intent.counter_name},
            importance=1, changed_objects=[ref],
        )
    return tuple(result)


def counter_bound_attack_options(state, attacker, defenders, planeswalkers):
    protected = {
        effect.protected_player for effect in state.continuous_effects or ()
        if isinstance(effect, ResolvedCounterBoundAttackRestriction) and effect.applies(attacker)
    }
    if not protected:
        return tuple(defenders)
    prohibited = protected | {row["id"] for row in planeswalkers if row["controller"] in protected}
    return tuple(reference for reference in defenders if reference not in prohibited)
