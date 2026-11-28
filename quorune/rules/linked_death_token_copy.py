"""A committed death exile creates copiable tokens with an incarnation-bound return."""

from collections.abc import Mapping

from ..ability_fragments import GrantedTriggeredAbilitySpec, ability_fragment_from_dict
from ..errors import GameRuleError
from ..linked_exile_return_model import ExiledObjectIdentity
from ..replacement.immutable import FrozenMap, thaw_value
from .death_battlefield_return import DEATH_BATTLEFIELD_ARRIVALS, decode_death_battlefield_arrivals
from .destroyed_creature_copy import COMMITTED_DEATH_COPY_FIELD
from .effect_shape_walkers import nested_effects


CAPABILITY = "zone.exile.committed_death_linked_token"
EXILE = "exile_death_then_create_linked_copy"
RETURN = "return_linked_death_copy_card"
RETURN_LABEL = "When this token leaves the battlefield, return the exiled card to its owner's graveyard."
TOKEN_LINK = "death_copy_token_exile_link"
RETURN_CONTEXT = "death_copy_exile_return_receipts"
COMMITTED_VERSION = 2
DEATH_EVENT = "creature.dies"
LEAVE_EVENT = "permanent.leave.self"
DEPENDENCIES = (
    CAPABILITY, "trigger.event.qualified_zone_change", "trigger.placement.apnap",
    "token.creation.fixed_copy", "zone.change.destination_replacement",
)


def death_copy_condition():
    return {"all": [
        {"field": "controller", "op": "eq", "value": "$source.controller"},
        {"field": "card", "op": "ne", "value": "$source.ref"},
        {"field": "token", "op": "eq", "value": False},
    ]}


def linked_death_copy_effect(fragment):
    return {"op": EXILE, "schema_version": 1, "return_ability": fragment}


def linked_death_return_effect():
    return {"op": RETURN, "schema_version": 1}


def _return_ability(effect):
    if (not isinstance(effect, Mapping) or set(effect) != {"op", "schema_version", "return_ability"}
            or effect["op"] != EXILE or type(effect["schema_version"]) is not int or effect["schema_version"] != 1):
        raise ValueError("The death token copy requires its complete exile and return grant")
    ability = ability_fragment_from_dict(effect["return_ability"])
    if (not isinstance(ability, GrantedTriggeredAbilitySpec) or ability.schema_version != 1
            or ability.event != LEAVE_EVENT or ability.label != RETURN_LABEL):
        raise ValueError("The death token copy requires its exact copiable leave trigger")
    return ability


def linked_death_token_copy_program_dependencies(program):
    marked = tuple(effect for effect in nested_effects((program.effects, program.handlers))
                   if effect["op"] in {EXILE, RETURN})
    if not marked:
        return ()
    if len(marked) != 1 or marked != tuple(program.effects) or program.handlers or program.target_schema is not None:
        raise ValueError("Linked death token copy instructions require their complete standalone bodies")
    effect, = marked
    if effect["op"] == RETURN:
        if (dict(effect) != linked_death_return_effect() or type(effect["schema_version"]) is not int
                or (program.event, program.active_zone) != (LEAVE_EVENT, "battlefield")
                or program.event_condition is not None or program.cost_schema is not None
                or program.provenance.get("granted_only") is not True):
            raise ValueError("A linked death-token return is solely its granted leave ability")
        return DEPENDENCIES
    ability = _return_ability(effect)
    if ((program.event, program.active_zone) != (DEATH_EVENT, "battlefield")
            or thaw_value(program.event_condition) != death_copy_condition() or program.cost_schema is not None
            or not ability.semantic_key.startswith(program.oracle_id + ":")
            or ability.semantic_key != f"{program.oracle_id}:{ability.ability_id}"
            or not ability.ability_id.startswith("trigger:")):
        raise ValueError("Linked death token copying requires the original other controlled nontoken death")
    return DEPENDENCIES


def supports_linked_death_token_copy_operation(program, operation):
    """Admit wire descriptors only through the existing complete program shape."""
    if operation not in (EXILE, RETURN):
        return False
    try:
        return bool(linked_death_token_copy_program_dependencies(program))
    except (ValueError, TypeError, KeyError):
        return False


def is_closed_linked_death_copy_program(program):
    try:
        required = linked_death_token_copy_program_dependencies(program)
    except (ValueError, TypeError, KeyError):
        return False
    return bool(required) and set(required) <= set(program.capability_dependencies)


def _resolving_program(host, item, effect):
    program = host.semantics.get(item.semantic_key)
    if (program is None or item.kind not in {"triggered_ability", "triggered_ability_copy"}
            or item.context.get("currently_resolving") is not True
            or not host.semantic_program_is_current_trusted(program)
            or not is_closed_linked_death_copy_program(program)
            or tuple(program.effects) != (FrozenMap(effect),)):
        raise GameRuleError("Linked death token copies require their current trusted resolution")
    return program


def _receipts(value):
    if not isinstance(value, (list, tuple)):
        raise ValueError("Death-copy linked destinations require a sealed list")
    result = []
    for row in value:
        if (not isinstance(row, Mapping) or set(row) != {"identity", "owner"}
                or type(row["owner"]) is not str or not row["owner"]):
            raise ValueError("Death-copy linked destination is malformed")
        result.append((ExiledObjectIdentity.from_dict(row["identity"]), row["owner"]))
    if len({identity.object_id for identity, _ in result}) != len(result):
        raise ValueError("Death-copy exile links may not repeat physical cards")
    return tuple(result)


def linked_death_copy_dispatch_facts(host, departure, destination):
    from .event_subscriptions import event_has_capability_subscriber

    if (departure.origin == "battlefield" and destination == "graveyard" and departure.copy_snapshot is not None
            and event_has_capability_subscriber(host, DEATH_EVENT, CAPABILITY)):
        return {"origin_copy_snapshot": departure.copy_snapshot}
    return {}


def _snapshotted_token(item, effect):
    from ..characteristic_evaluation import type_parts
    from ..token_copy_recipes import TokenCopyExceptionSpec, apply_copy_exception

    observed = item.context.get("copiable_snapshot")
    snapshot = thaw_value(FrozenMap(observed)) if isinstance(observed, Mapping) else None
    if (not isinstance(snapshot, dict) or set(snapshot) != {"oracle_id", "printed_name", "annotations", "characteristics"}
            or not isinstance(snapshot["annotations"], dict) or not isinstance(snapshot["characteristics"], dict)
            or snapshot["annotations"].get("copied_from", "") != item.context.get("card_object_identity", "").rpartition("@")[0]):
        raise GameRuleError("The death trigger lost its exact prior battlefield copy values")
    characteristics = snapshot["characteristics"]
    types, _, _ = type_parts(str(characteristics.get("type_line") or ""))
    subtypes = ("spirit",) if {"creature", "kindred"}.intersection(types) else ()
    values = apply_copy_exception(characteristics, TokenCopyExceptionSpec(
        add_subtypes=subtypes, add_ability_fragments=(effect["return_ability"],),
    ))
    return {**snapshot, "characteristics": values, "annotations": {**snapshot["annotations"], "copy_overrides": values}}


def commit_linked_death_copy(host, item, effect):
    from ..zone_transitions import ZoneTransitionOwner

    expected = {key: value for key, value in effect.items() if key != "_replacement_selections"}
    _resolving_program(host, item, expected)
    arrivals = decode_death_battlefield_arrivals(item.context.get(DEATH_BATTLEFIELD_ARRIVALS))
    active = set(host.active_seats)
    current = tuple((row, card) for row in arrivals
                    if row.matches(card := host.state.cards.get(row.object_id)) and row.owner in active)
    if not current:
        return ()
    snapshot = _snapshotted_token(item, expected)
    previous = {card.object_id: card.logical_object_id for _, card in current}
    moved = ZoneTransitionOwner(host).move_cards_simultaneously(
        [(card.object_id, "exile") for _, card in current], reason=item.label, log=False,
        replacement_selections=tuple(effect.get("_replacement_selections", ())),
    )
    links = [
        {"identity": ExiledObjectIdentity(card.object_id, card.ref, card.logical_object_id).to_dict(),
         "owner": card.owner}
        for card in moved
        if card.object_id in previous and card.zone == "exile" and card.logical_object_id != previous[card.object_id]
        and card.is_card_object
    ]
    if not links:
        return ()
    return ({
        "op": "create_token", "controller": item.controller, "quantity": 1, "copy_of": item.context["card"],
        COMMITTED_DEATH_COPY_FIELD: {
            "schema_version": COMMITTED_VERSION, "stack_ref": item.ref, "snapshot": snapshot, "links": links,
        },
    },)


def is_linked_death_copy_commit(effect):
    receipt = effect.get(COMMITTED_DEATH_COPY_FIELD)
    return isinstance(receipt, Mapping) and type(receipt.get("schema_version")) is int and receipt["schema_version"] == COMMITTED_VERSION


def linked_death_token_creation_controller(host, effect, item):
    receipt = effect[COMMITTED_DEATH_COPY_FIELD]
    scope = {"op", "controller", "quantity", "copy_of", COMMITTED_DEATH_COPY_FIELD}
    allowed = scope | {"reason", "_replacement_selections", "_runtime_source"}
    if (not scope <= set(effect) <= allowed or set(receipt) != {"schema_version", "stack_ref", "snapshot", "links"}
            or item is None or receipt["stack_ref"] != item.ref or effect["op"] != "create_token"
            or effect["controller"] != item.controller or type(effect["quantity"]) is not int or effect["quantity"] != 1
            or effect["copy_of"] != item.context.get("card")):
        raise GameRuleError("A linked token creation lost its resolving death trigger")
    program = host.semantics.get(item.semantic_key)
    if program is None or len(program.effects) != 1 or program.effects[0].get("op") != EXILE:
        raise GameRuleError("Linked copy creation requires its original committed exile")
    _resolving_program(host, item, program.effects[0])
    if FrozenMap(receipt["snapshot"]) != FrozenMap(_snapshotted_token(item, program.effects[0])):
        raise GameRuleError("A linked token creation changed its retained copy values")
    arrivals = {row.object_id: row for row in decode_death_battlefield_arrivals(item.context[DEATH_BATTLEFIELD_ARRIVALS])}
    links = _receipts(receipt["links"])
    if not links or any(
        (row := arrivals.get(identity.object_id)) is None or identity.ref != row.reference or owner != row.owner
        or identity.logical_object_id != f"{row.object_id}@{row.zone_change_counter + 1}"
        for identity, owner in links
    ):
        raise GameRuleError("A linked token creation lost its committed exile identities")
    return item.controller


def record_created_death_token_links(host, effect, created):
    links = thaw_value(effect[COMMITTED_DEATH_COPY_FIELD]["links"])
    tokens = {card.ref: card for card in host.state.cards.values() if card.ref in created}
    for ref in created:
        card = tokens.get(ref)
        if card is None or card.zone != "battlefield" or not card.is_token:
            raise GameRuleError("The committed death copy must link each surviving created token")
        card.annotations[TOKEN_LINK] = {"token_incarnation": card.logical_object_id, "cards": links}


def linked_death_token_leave_context(source, program, event, occurrence_context):
    if event != "permanent.leave" or tuple(program.effects) != (FrozenMap(linked_death_return_effect()),):
        return {}
    raw = source.annotations.get(TOKEN_LINK)
    if raw is None:
        return {RETURN_CONTEXT: []}
    if not isinstance(raw, Mapping) or set(raw) != {"token_incarnation", "cards"}:
        raise ValueError("The committed death-copy token has malformed linked cards")
    if (raw["token_incarnation"] != source.logical_object_id
            or occurrence_context.get("card_object_identity") != source.logical_object_id):
        return {RETURN_CONTEXT: []}
    return {RETURN_CONTEXT: [{"identity": row.to_dict(), "owner": owner} for row, owner in _receipts(raw["cards"])]}


def return_linked_death_token_cards(host, item, effect):
    from ..zone_transitions import ZoneTransitionOwner

    expected = {key: value for key, value in effect.items() if key != "_replacement_selections"}
    _resolving_program(host, item, expected)
    moves = []
    for identity, owner in _receipts(item.context.get(RETURN_CONTEXT)):
        card = host.state.cards.get(identity.object_id)
        if identity.matches_exiled_card(card) and card.owner == owner and owner in host.active_seats:
            moves.append((card.object_id, "graveyard"))
    if moves:
        ZoneTransitionOwner(host).move_cards_simultaneously(
            moves, reason=item.label, log=False,
            replacement_selections=tuple(effect.get("_replacement_selections", ())),
        )
