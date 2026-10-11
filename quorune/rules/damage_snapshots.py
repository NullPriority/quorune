from __future__ import annotations

"""Canonical public source and recipient snapshots before damage replacement."""

from typing import Any
from ..damage_source import DamageSourceSnapshot,DamageError,represented_toxic_value
from ..damage_values import DamageRecipientSnapshot
from ..damage_scope_facts import current_damage_scope_facts,current_damage_combat_partners


def _normalized_keywords(values):
    return tuple(sorted({" ".join(str(value).casefold().split()) for value in values if str(value).strip()}))


def source_snapshot(
    host: Any,
    source_ref: str | None,
    *,
    controller: str,
) -> DamageSourceSnapshot:
    """Capture CR 120.1/120.2 source facts before event transformation."""

    source = next(
        (
            card
            for card in host.state.cards.values()
            if card.ref == source_ref
        ),
        None,
    )
    if source is None:
        stack_item = next(
            (
                item
                for item in host.state.stack
                if item.ref == source_ref and item.card_object_id
            ),
            None,
        )
        if stack_item is not None:
            source = host.state.cards.get(stack_item.card_object_id)
    if source is None:
        # Direct low-level effect calls and legacy checkpoints may not carry a
        # card handle. Keep their deterministic identity while withholding all
        # source characteristics; ordinary compiled damage always supplies the
        # exact source. This compatibility source does not claim CR 120.2
        # coverage for arbitrary off-battlefield objects.
        ref = str(source_ref or f"legacy-effect:{controller}")
        return DamageSourceSnapshot(
            ref=ref,
            object_id=f"unrepresented:{ref}",
            logical_object_id=f"unrepresented:{ref}",
            controller=controller,
            owner=controller,
            zone="unknown",
        )
    data = host._effective_card_data(source)
    card_types, subtypes, supertypes = host._type_parts(
        str(data.get("type_line") or "")
    )
    keywords = _normalized_keywords(data.get("keywords", ()))
    toxic_value = represented_toxic_value(
        data,
        temporary_keywords=getattr(source, "temporary_keywords", ()),
    )
    if toxic_value != 0 and "toxic" not in keywords:
        keywords = tuple(sorted({*keywords, "toxic"}))
    return DamageSourceSnapshot(
        ref=source.ref,
        object_id=source.object_id,
        logical_object_id=source.logical_object_id,
        controller=(
            source.controller
            if source.controller in host.state.players
            else controller
        ),
        owner=source.owner,
        zone=source.zone,
        oracle_id=source.oracle_id,
        commander_designation_id=source.commander_designation_id,
        types=tuple(sorted(card_types)),
        subtypes=tuple(sorted(subtypes)),
        supertypes=tuple(sorted(supertypes)),
        colors=tuple(
            sorted(str(value).upper() for value in data.get("colors", ()))
        ),
        keywords=keywords,
        mana_value=data.get("mana_value"),
        is_commander=bool(source.is_commander),
        toxic_value=toxic_value,
        scope_facts=current_damage_scope_facts(host,source,data),
        combat_partners=current_damage_combat_partners(host,source),
    )


def recipient_snapshot(
    host: Any,
    target: str,
    *,
    actor: str,
) -> DamageRecipientSnapshot:
    if target in host.state.players:
        if target not in host.active_seats:
            raise DamageError("Damage cannot be dealt to a player who left")
        return DamageRecipientSnapshot(
            ref=target,
            kind="player",
            controller=target,
        )
    card = host._resolve_object(actor, target, zones={"battlefield"})
    data = host._effective_card_data(card)
    card_types, subtypes, supertypes = host._type_parts(
        str(data.get("type_line") or "")
    )
    if not card_types.intersection({"battle", "creature", "planeswalker"}):
        raise DamageError(
            f"Damage cannot be dealt to {card.ref}; it is not a Battle, "
            "creature, or planeswalker"
        )
    return DamageRecipientSnapshot(
        ref=card.ref,
        kind="permanent",
        controller=card.controller,
        object_id=card.object_id,
        logical_object_id=card.logical_object_id,
        owner=card.owner,
        types=tuple(sorted(card_types)),
        subtypes=tuple(sorted(subtypes)),
        scope_facts=current_damage_scope_facts(host,card,data),
        combat_partners=current_damage_combat_partners(host,card),
        colors=tuple(sorted(str(c).upper() for c in data.get('colors',()))),
        supertypes=tuple(sorted(supertypes)),
    )
