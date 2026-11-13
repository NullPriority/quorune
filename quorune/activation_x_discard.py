from __future__ import annotations

"""Closed fixed owned-hand discard obligations composed with activation X."""

ANNOUNCED_X_DISCARD_CAPABILITY = 'activation.mana_cost.announced_x_fixed_discard'


def activation_x_discard_count(cost_text: str) -> int | None:
    prefix, marker, suffix = cost_text.rpartition(', Discard ')
    if not prefix or not marker:
        return None
    if suffix == 'a card':
        return 1
    words = ('one','two','three','four','five','six','seven','eight','nine','ten')
    for count, word in enumerate(words, 1):
        noun = 'card' if count == 1 else 'cards'
        if suffix in (f'{word} {noun}', f'{count} {noun}'):
            return count
    return None


def validate_activation_x_discard(ability) -> None:
    count = activation_x_discard_count(ability.cost_text)
    if count is None:
        if ability.choices:
            raise ValueError('Activation X selected cost requires a closed fixed discard')
        return
    from .activation_cost_choice import CostChoice
    from .object_predicate import ObjectQuerySpec
    from .replacement.immutable import FrozenMap
    expected = CostChoice(kind='discard_one', count=count, zone='hand',
        predicate=FrozenMap(ObjectQuerySpec(zones=('hand',), owner='$actor', known_to_actor=True).to_dict()))
    if ability.choices != (expected,):
        raise ValueError('Activation X discard must retain the exact owned-hand query and count')
