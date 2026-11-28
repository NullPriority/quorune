"""Lower and resolve one scoped combat prevention and draw grant."""

from collections.abc import Mapping

from ..combat_prevention_draw_model import (
    CAPABILITY, FRAGMENT_KIND, CombatPreventionDrawSpec, matches_combat_prevention_draw_grant,
)


DRAW_FIELD = "combat_prevention_draw_instructions"


def combat_prevention_draw_grant_template(text: str):
    if not matches_combat_prevention_draw_grant(text):
        return None
    from .temporary_ability_grant import OPERATION, TEMPORARY_QUOTED_GRANT_MECHANIC

    effect = {
        "op": OPERATION, "schema_version": 7, "card": "$target.0", "keywords": [],
        "ability_fragments": [{"kind": FRAGMENT_KIND, "value": CombatPreventionDrawSpec().to_dict()}],
        "exclude_source": False, "printed_only": False,
    }
    target = {"zones": ["battlefield"], "categories": ["permanent"], "types_any": ["creature"], "count": 1}
    return "fixed-temporary-combat-prevention-draw-v1", (effect,), target, (TEMPORARY_QUOTED_GRANT_MECHANIC,)


def record_prevention_draw(event, payload, amount, effect_id):
    controller, target = payload.get("source_controller"), payload.get("target")
    if (not effect_id or payload.get("combat") is not True or payload.get("target_kind") != "player"
            or any(type(seat) is not str or not seat for seat in (controller, target))):
        raise ValueError("Combat prevention and draw requires the original source controller and a player recipient")
    cards = amount // 2
    if cards:
        # This additional instruction uses the amount at application even when prevention is forbidden.
        payload[DRAW_FIELD] = [*payload.get(DRAW_FIELD, ()), {
            "event": event.event_id, "effect": effect_id, "players": [controller, target], "cards": cards,
        }]


def combat_prevention_draw_replacements(host, proposals):
    from ..ability_fragments import canonical_ability_fragments
    from ..replacement import PreventAmount, ReplacementClass, ReplacementEffect

    seen = set()
    results = []
    for proposal in proposals:
        source = proposal.source
        if not proposal.combat or source.zone != "battlefield" or source.logical_object_id in seen:
            continue
        seen.add(source.logical_object_id)
        card = host.state.cards.get(source.object_id)
        if card is None or card.zone != "battlefield" or card.phased_out or card.logical_object_id != source.logical_object_id:
            continue
        current = host._effective_card_data(card)
        for fragment in canonical_ability_fragments(current.get("ability_fragments", ())):
            if not isinstance(fragment, CombatPreventionDrawSpec) or fragment.schema_version != 2:
                continue
            results.append(ReplacementEffect(
                effect_id=f"combat-prevention-draw:{fragment.grant_id}", source_id=source.ref,
                event_kind="damage", replacement_class=ReplacementClass.OTHER,
                conditions={"source_logical_object_id": {"eq": source.logical_object_id},
                            "combat": {"eq": True}, "target_kind": {"eq": "player"}, "amount": {"not_in": [0]}},
                operations=(PreventAmount(schema_version=4, draw_for_combat_players=True),),
                label="Prevent combat damage and draw cards",
            ))
    return tuple(results)


def combat_prevention_draws(host, events):
    from ..drawing.model import QueuedDraw
    from ..replacement.model import walk_events

    apnap = tuple(host.apnap_order())
    result = []
    seen = set()
    for root in events:
        for event in walk_events(root):
            rows = event.payload.get(DRAW_FIELD, ())
            if not isinstance(rows, (list, tuple)):
                raise ValueError("Combat prevention draws require an ordered record")
            for row in rows:
                if not isinstance(row, Mapping) or set(row) != {"event", "effect", "players", "cards"}:
                    raise ValueError("Combat prevention draw record has an invalid shape")
                if row["event"] != event.event_id:
                    continue
                players = row["players"]
                if (row["effect"] not in event.applied_effects or not isinstance(players, (list, tuple))
                        or len(players) != 2 or any(seat not in apnap for seat in players)
                        or type(row["cards"]) is not int or row["cards"] <= 0):
                    raise ValueError("Combat prevention draw lost its applied effect or affected player")
                key = (event.event_id, row["effect"])
                if key in seen:
                    raise ValueError("A combat prevention draw was repeated for the same applied effect")
                seen.add(key)
                for player in sorted(players, key=apnap.index):
                    result.append(QueuedDraw(player, row["cards"], "combat damage ability", private=True))
    return tuple(result)


def complete_combat_damage_draws(host):
    from ..errors import GameRuleError

    if (host.state.phase, host.state.step) != ("combat", "combat_damage"):
        raise GameRuleError("Combat damage draws must finish in their damage step")
    if host._semantic_pause_annotation() is None and not host._stabilize():
        host._grant_priority(host.state.active_player)
