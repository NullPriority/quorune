from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping


FIRST_STRIKE = "first strike"
DOUBLE_STRIKE = "double strike"
DEATHTOUCH = "deathtouch"
TRAMPLE = "trample"
LIFELINK = "lifelink"


def current_attacker_blocker_count(state, attacker_object_id: str) -> int:
    blockers = state.combat.blockers.get(attacker_object_id, ())
    return sum(
        1 for object_id in dict.fromkeys(blockers)
        if (card := state.cards.get(object_id)) is not None
        and card.zone == "battlefield" and not card.phased_out and card.blocking is not None
    )


def current_block_counterpart_ids(
    blockers: Mapping[str, Iterable[str]], source_object_id: str, *,
    live_attackers: Collection[str] | None,
) -> tuple[str, ...]:
    """Return current block relations while leaving former attackers' rows intact."""
    if live_attackers is None:
        live_attackers = blockers
    related = set(blockers.get(source_object_id, ())) if source_object_id in live_attackers else set()
    related.update(
        attacker for attacker, assigned in blockers.items()
        if attacker in live_attackers and source_object_id in assigned
    )
    related.discard(source_object_id)
    return tuple(sorted(related))


def normalized_keywords(values: Iterable[object]) -> frozenset[str]:
    """Return the rules-facing, case-insensitive keyword set."""

    return frozenset(str(value).casefold() for value in values)


def first_strike_step_required(
    keywords_by_object: Mapping[str, frozenset[str]],
) -> bool:
    return any(
        keywords.intersection({FIRST_STRIKE, DOUBLE_STRIKE})
        for keywords in keywords_by_object.values()
    )


def ordinary_second_step_combatants(
    keywords_by_object: Mapping[str, frozenset[str]],
) -> frozenset[str]:
    """Snapshot creatures that had neither strike ability as step one began."""

    return frozenset(
        object_id
        for object_id, keywords in keywords_by_object.items()
        if not keywords.intersection({FIRST_STRIKE, DOUBLE_STRIKE})
    )


def assigns_in_damage_step(
    *,
    object_id: str,
    current_keywords: frozenset[str],
    step_index: int,
    first_strike_step: bool,
    ordinary_second_step: frozenset[str],
) -> bool:
    """Implement the participant split in CR 510.4.

    The ordinary second-step set is frozen when the first damage step begins.
    Double strike is checked again for the second step, as required when the
    ability is gained or lost between steps.
    """

    if not first_strike_step:
        return step_index == 0
    if step_index == 0:
        return bool(
            current_keywords.intersection({FIRST_STRIKE, DOUBLE_STRIKE})
        )
    if step_index == 1:
        return (
            object_id in ordinary_second_step
            or DOUBLE_STRIKE in current_keywords
        )
    return False
