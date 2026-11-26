"""Use committed physical arrivals for a granted tapped owner return."""

from collections.abc import Mapping

from ..continuous_effect_state import ResolutionEffectSource
from ..errors import GameRuleError
from ..replacement.immutable import FrozenMap, thaw_value
from .committed_quantity_followups import CommittedQuantityFollowup
from .death_battlefield_return import DEATH_BATTLEFIELD_ARRIVALS, decode_death_battlefield_arrivals


GRANTED_DEATH_ROLE_RETURN_CAPABILITY = "zone.return.granted_death_then_wicked_role"
GRANTED_SELF_DEATH_RETURN_CAPABILITY = "zone.return.granted_self_death_battlefield"
GRANTED_SELF_DEATH_RETURN_VERSION = "granted_self_death_return_version"
GRANTED_SELF_DEATH_ARRIVALS = "granted_self_death_arrivals"
DEATH_ROLE_VERSION_FIELD = "granted_death_role_return_version"
DEATH_ROLE_ARRIVALS = "granted_death_role_arrivals"
_FIELDS = {"op", DEATH_ROLE_VERSION_FIELD, DEATH_ROLE_ARRIVALS, "source", "role"}
_RUNTIME = {"_runtime_source", "_replacement_selections", "_aura_target_refs"}


def has_granted_self_death_return(host, previous_characteristics):
    from ..ability_fragments import canonical_ability_fragments, granted_triggered_specs

    fragments = canonical_ability_fragments(previous_characteristics.get("ability_fragments", ()))
    return any(
        grant.event == "permanent.graveyard.self"
        and (program := host.semantics.get(grant.semantic_key)) is not None
        and GRANTED_SELF_DEATH_RETURN_CAPABILITY in program.capability_dependencies
        for grant in granted_triggered_specs(fragments)
    )


def granted_self_death_return_effect():
    return {
        "op": "reanimate", GRANTED_SELF_DEATH_RETURN_VERSION: 1,
        GRANTED_SELF_DEATH_ARRIVALS: "$context." + DEATH_BATTLEFIELD_ARRIVALS,
        "source": "$stack",
    }


def is_granted_self_death_return(effect):
    return effect.get("op") == "reanimate" and (
        GRANTED_SELF_DEATH_RETURN_VERSION in effect or GRANTED_SELF_DEATH_ARRIVALS in effect
    )


def granted_self_death_return_dependencies(program, observed):
    marked = tuple(effect for effect in observed if is_granted_self_death_return(effect))
    if not marked:
        return ()
    if (len(marked) != 1 or tuple(program.effects) != marked or program.handlers
            or program.target_schema is not None or program.event_condition is not None
            or (program.event, program.active_zone) != ("permanent.graveyard.self", "battlefield")
            or program.provenance.get("granted_only") is not True
            or type(marked[0].get(GRANTED_SELF_DEATH_RETURN_VERSION)) is not int
            or marked[0] != granted_self_death_return_effect()):
        raise ValueError("A granted self-death return requires its exact granted departure and tapped owner result")
    return (GRANTED_SELF_DEATH_RETURN_CAPABILITY,)


def validate_granted_self_death_return(effect):
    expected = granted_self_death_return_effect()
    if (not expected.keys() <= effect.keys() <= expected.keys() | _RUNTIME
            or not is_granted_self_death_return(effect)
            or type(effect[GRANTED_SELF_DEATH_RETURN_VERSION]) is not int
            or effect[GRANTED_SELF_DEATH_RETURN_VERSION] != 1
            or type(effect["source"]) is not str or not effect["source"] or effect["source"].startswith("$")
            or not isinstance(effect.get("_replacement_selections", ()), (list, tuple))):
        raise ValueError("A granted self-death return has an invalid resolved shape")
    arrivals = decode_death_battlefield_arrivals(effect[GRANTED_SELF_DEATH_ARRIVALS])
    _validate_aura_arrivals(effect, arrivals)
    return arrivals


def granted_death_role_effect(fragments):
    return {
        "op": "reanimate", DEATH_ROLE_VERSION_FIELD: 1,
        DEATH_ROLE_ARRIVALS: "$context." + DEATH_BATTLEFIELD_ARRIVALS, "source": "$stack",
        "role": {"op": "create_role_token", "controller": "$controller", "name": "Wicked",
                 "quantity": 1, "aura_target_ref": "$source.zone_object",
                 "characteristics": {"type_line": "Token Enchantment — Aura Role", "colors": [],
                                     "ability_fragments": fragments}},
    }


def is_granted_death_role_return(effect):
    return effect.get("op") == "reanimate" and DEATH_ROLE_VERSION_FIELD in effect


def _role_dependencies(role, *, actor=None):
    from .role_token_shapes import role_token_node_capabilities

    if (not isinstance(role, Mapping) or role.get("name") != "Wicked"
            or role.get("controller") != ("$controller" if actor is None else actor)
            or role.get("aura_target_ref") != ("$source.zone_object" if actor is None else None)):
        raise ValueError("The returned creature requires its canonical Wicked Role")
    payload = {**thaw_value(role), "controller": "$controller", "aura_target_ref": "$source.zone_object"}
    required = role_token_node_capabilities(effects=(payload,), target_schema=None, mechanics=("predefined-role-token",))
    from ..ability_fragments import GrantedTriggeredAbilitySpec, StaticComponentSpec, canonical_ability_fragments

    if not required:
        raise ValueError("The Wicked Role does not have its predefined definition")
    fragments = canonical_ability_fragments(payload["characteristics"]["ability_fragments"])
    if (len(fragments) != 3 or sum(isinstance(part, StaticComponentSpec) for part in fragments) != 1
            or sum(isinstance(part, GrantedTriggeredAbilitySpec) for part in fragments) != 1):
        raise ValueError("The Wicked Role requires its enchant, characteristic, and death fragments")
    return required


def granted_death_role_return_dependencies(program):
    from .effect_shape_walkers import nested_effects

    from .attached_role_batch import ATTACHED_ROLE_BATCH_COMMIT, ATTACHED_ROLE_BATCH_OPERATION

    observed = tuple(nested_effects((program.effects, program.handlers, program.target_schema)))
    if any(effect.get("op") == ATTACHED_ROLE_BATCH_OPERATION or ATTACHED_ROLE_BATCH_COMMIT in effect for effect in observed):
        raise ValueError("Attached Role batches can only be emitted by committed physical returns")
    marked = tuple(effect for effect in observed if is_granted_death_role_return(effect))
    if not marked:
        return ()
    if (len(marked) != 1 or tuple(program.effects) != marked or program.handlers
            or program.target_schema is not None or program.event_condition is not None
            or (program.event, program.active_zone) != ("creature.dies.self", "battlefield")
            or not program.provenance.get("granted_only")):
        raise ValueError("The Role return must be the granted original creature's self-death instruction")
    return granted_death_role_payload_dependencies(marked[0])


def granted_death_role_payload_dependencies(effect):
    if (not is_granted_death_role_return(effect) or set(effect) != _FIELDS
            or type(effect[DEATH_ROLE_VERSION_FIELD]) is not int
            or effect[DEATH_ROLE_VERSION_FIELD] != 1 or effect["source"] != "$stack"
            or effect[DEATH_ROLE_ARRIVALS] != "$context." + DEATH_BATTLEFIELD_ARRIVALS):
        raise ValueError("The Role return needs the sealed physical deaths of its source")
    from .attached_role_batch import ATTACHED_ROLE_BATCH_CAPABILITY

    return (GRANTED_DEATH_ROLE_RETURN_CAPABILITY, ATTACHED_ROLE_BATCH_CAPABILITY, *_role_dependencies(effect["role"]))


def validate_granted_death_role_return(effect, *, actor=None):
    if (not _FIELDS <= set(effect) <= _FIELDS | _RUNTIME or not is_granted_death_role_return(effect)
            or type(effect[DEATH_ROLE_VERSION_FIELD]) is not int or effect[DEATH_ROLE_VERSION_FIELD] != 1
            or type(effect["source"]) is not str or not effect["source"] or effect["source"].startswith("$")
            or not isinstance(effect.get("_replacement_selections", ()), (list, tuple))):
        raise ValueError("The granted death Role return has an invalid shape")
    arrivals = decode_death_battlefield_arrivals(effect[DEATH_ROLE_ARRIVALS])
    _validate_aura_arrivals(effect, arrivals)
    role = effect["role"]
    controller = role.get("controller") if isinstance(role, Mapping) else None
    if type(controller) is not str or not controller or controller.startswith("$"):
        raise ValueError("The Wicked Role requires its exact resolving controller")
    _role_dependencies(role, actor=actor if actor is not None else controller)
    return arrivals


def _validate_aura_arrivals(effect, arrivals):
    selections = effect.get("_aura_target_refs", {})
    if (not isinstance(selections, Mapping) or not set(selections) <= {row.object_id for row in arrivals}
            or any(type(ref) is not str or not ref for ref in selections.values())):
        raise ValueError("A granted death return must name its exact Aura arrivals")


def _validate_resolving_death(host, effect, actor, arrivals, events):
    source = ResolutionEffectSource.from_effect(effect)
    item = next((candidate for candidate in host.state.stack if candidate.ref == source.stack_ref), None)
    if (item is None or item.controller != actor or effect["source"] != item.ref
            or item.context.get("currently_resolving") is not True
            or item.context.get("event") not in events
            or decode_death_battlefield_arrivals(item.context.get(DEATH_BATTLEFIELD_ARRIVALS)) != arrivals):
        raise ValueError("The granted death return lost its current trigger or physical arrivals")


def _return_owner_tapped(host, effect, actor, reason, arrivals):
    from ..characteristic_evaluation import type_parts
    from ..external_entry_state_model import PERMANENT_CARD_TYPES
    from ..zone_transitions import ZoneTransitionOwner

    cards = tuple(
        card for row in arrivals
        if row.matches(card := host.state.cards.get(row.object_id)) and card.owner in host.active_seats
        and type_parts(str(host._effective_card_data(card).get("type_line") or ""))[0] & PERMANENT_CARD_TYPES
    )
    if not cards:
        return (), {}
    originals = {card.object_id: card.logical_object_id for card in cards}
    moved = ZoneTransitionOwner(host).move_cards_simultaneously(
        tuple((card.object_id, "battlefield") for card in cards), reason=reason, tapped=True,
        destination_controllers={card.object_id: card.owner for card in cards},
        putting_players={card.object_id: actor for card in cards},
        replacement_selections=tuple(effect.get("_replacement_selections", ())),
        aura_target_refs=effect.get("_aura_target_refs"),
    )
    return moved, originals


def apply_granted_self_death_return(host, effect, *, actor, reason):
    try:
        arrivals = validate_granted_self_death_return(effect)
        _validate_resolving_death(host, effect, actor, arrivals, ("permanent.graveyard", "permanent.graveyard.self"))
    except (KeyError, TypeError, ValueError) as exc:
        raise GameRuleError(str(exc)) from exc
    moved, _ = _return_owner_tapped(host, effect, actor, reason, arrivals)
    return tuple(card.ref for card in moved if card.zone == "battlefield")


def apply_granted_death_role_return(host, effect, *, actor, reason):
    from ..characteristic_evaluation import type_parts

    try:
        arrivals = validate_granted_death_role_return(effect, actor=actor)
        _validate_resolving_death(host, effect, actor, arrivals, ("creature.dies", "creature.dies.self"))
    except (KeyError, TypeError, ValueError) as exc:
        raise GameRuleError(str(exc)) from exc
    moved, originals = _return_owner_tapped(host, effect, actor, reason, arrivals)
    recipients = tuple(card for card in moved if card.zone == "battlefield"
        and card.object_id in originals and card.logical_object_id != originals[card.object_id]
        and "creature" in type_parts(str(host._effective_card_data(card).get("type_line") or ""))[0])
    if not recipients:
        return CommittedQuantityFollowup(())
    from .attached_role_batch import attached_role_batch_effect

    batch = attached_role_batch_effect(effect["role"], actor=actor, stack_ref=effect["source"], recipients=recipients)
    return CommittedQuantityFollowup((FrozenMap(batch),))
