"""Promise-dependent cardinality over the ordinary targeted reanimation owner."""
from collections.abc import Mapping

from ..gift_card_model import GIFT_CARD_CAPABILITY
from ..replacement.immutable import FrozenMap, thaw_value

COST_FIELD = "gift_card_graveyard_limits"
TEMPLATE = "gift-card-graveyard-reanimation-limits-v1"
CAPABILITY = "casting.additional_cost.gift_card_graveyard_limits"
DEPENDENCIES = (
    CAPABILITY, GIFT_CARD_CAPABILITY, "zone.draw.library_to_hand", "target.revalidate_resolution",
    "zone.reanimate.fixed_target", "zone.reanimate.fixed_target_set", "resolution.effect.fixed_homogeneous_target_set",
)


def target_schema(promised):
    return {"zones": ["graveyard"], "categories": ["card"], "owner_relation": "you",
            "types_any": ["creature"], "mana_value_max": 2, "min": 0, "max": 3 if promised else 2}


def effects(promised=False):
    return ({"op": "reanimate_targets", "cards": "$targets", "maximum_targets": 3 if promised else 2,
             "schema_version": 1, "battlefield_controller": "actor", "battlefield_tapped": False},)


def cost_schema():
    return {COST_FIELD: {"schema_version": 1, "unpromised": target_schema(False), "promised": target_schema(True)}}


def runtime_dependencies(program):
    if program is None:
        return ()
    cost = program.cost_schema
    if isinstance(cost, Mapping) and GREED_COST_FIELD in cost or (program.provenance or {}).get('template_id') == GREED_TEMPLATE:
        return greed_dependencies(program)
    if isinstance(cost, Mapping) and SWEEP_RETURN_COST_FIELD in cost or (program.provenance or {}).get('template_id') == SWEEP_RETURN_TEMPLATE:
        return sweep_return_dependencies(program)
    if isinstance(cost, Mapping) and RETURN_COPY_COST_FIELD in cost or (program.provenance or {}).get('template_id') == RETURN_COPY_TEMPLATE:
        return return_copy_dependencies(program)
    if not (isinstance(cost, Mapping) and COST_FIELD in cost
            or (program.provenance or {}).get("template_id") == TEMPLATE):
        return ()
    if (thaw_value(cost) != cost_schema() or type(cost[COST_FIELD]["schema_version"]) is not int
            or (program.event, program.active_zone) != ("resolve", "stack")
            or program.event_condition is not None or program.handlers
            or thaw_value(program.target_schema) != target_schema(False)
            or tuple(thaw_value(program.effects)) != effects()):
        raise ValueError("Gift reanimation requires the original promises and owned graveyard result")
    for raw in (program.target_schema, cost[COST_FIELD]["promised"], cost[COST_FIELD]["unpromised"]):
        if any(type(raw[key]) is not int for key in ("min", "max", "mana_value_max")):
            raise ValueError("Gift reanimation target bounds require integers")
    if (type(program.effects[0]["schema_version"]) is not int
            or type(program.effects[0]["maximum_targets"]) is not int
            or program.effects[0]["battlefield_tapped"] is not False):
        raise ValueError("Gift reanimation requires its exact simultaneous return")
    return DEPENDENCIES


def is_closed(program):
    try:
        required = runtime_dependencies(program)
    except (TypeError, ValueError, KeyError):
        return False
    return bool(required) and set(required).issubset(program.capability_dependencies)


RETURN_COPY_COST_FIELD = "gift_card_return_copy"
RETURN_COPY_TEMPLATE = "gift-card-committed-return-copy-v1"
RETURN_COPY_CAPABILITY = "casting.additional_cost.gift_card_committed_return_copy"
RETURN_COPY_DEPENDENCIES = (
    RETURN_COPY_CAPABILITY, GIFT_CARD_CAPABILITY, "zone.draw.library_to_hand",
    "target.revalidate_resolution", "zone.reanimate.fixed_target", "token.creation.fixed_copy",
)
RETURN_RECEIPT_FIELD = "gift_return_receipt_version"
COPY_RECEIPT_FIELD = "gift_return_copy_version"
_RECEIPT_CONTEXT = "gift_committed_return"


def return_copy_cost_schema():
    return {RETURN_COPY_COST_FIELD: {"schema_version": 1}}


def target_schema_for_program(program, promised):
    if GREED_COST_FIELD in program.cost_schema:
        return greed_target_schema(promised)
    if SWEEP_RETURN_COST_FIELD in program.cost_schema:
        return None
    if RETURN_COPY_COST_FIELD in program.cost_schema:
        return return_copy_target_schema()
    return target_schema(promised)


def return_copy_target_schema():
    schema = target_schema(False)
    for key in ("min", "max", "mana_value_max"):
        del schema[key]
    return {**schema, "count": 1}


def return_copy_base_effect():
    return {"op": "reanimate", "card": "$target.0", "controller": "$controller", "tapped": False}


def return_copy_dependencies(program):
    if (thaw_value(program.cost_schema) != return_copy_cost_schema()
            or type(program.cost_schema[RETURN_COPY_COST_FIELD]["schema_version"]) is not int
            or (program.event, program.active_zone) != ("resolve", "stack")
            or program.event_condition is not None or program.handlers
            or thaw_value(program.target_schema) != return_copy_target_schema()
            or type(program.target_schema["count"]) is not int
            or tuple(thaw_value(program.effects)) != (return_copy_base_effect(),)
            or program.effects[0]["tapped"] is not False):
        raise ValueError("Gift return copy requires the original one-target untapped return")
    return RETURN_COPY_DEPENDENCIES


def gift_graveyard_resolution(program, promised):
    if GREED_COST_FIELD in program.cost_schema:
        return greed_effects(promised)
    if SWEEP_RETURN_COST_FIELD in program.cost_schema:
        return (sweep_return_base_effect(),)
    if RETURN_COPY_COST_FIELD not in program.cost_schema:
        return effects(promised)
    move = return_copy_base_effect()
    if not promised:
        return (move,)
    from ..token_copy_recipes import TokenCopyExceptionSpec, TokenCopyRecipeSpec

    recipe = TokenCopyRecipeSpec(origin="target", exception=TokenCopyExceptionSpec(power=1, toughness=1))
    return ({**move, RETURN_RECEIPT_FIELD: 1},
            {"op": "create_token", "controller": "$controller", "quantity": 1,
             "copy_spec": recipe.to_dict(), COPY_RECEIPT_FIELD: 1})


def _resolving_return_copy(host, effect, actor, field):
    from ..continuous_effect_state import ResolutionEffectSource
    from ..gift_card_model import GIFT_CARD_PROMISE_FIELD, GiftCardPromise

    if type(effect.get(field)) is not int or effect[field] != 1:
        raise ValueError("Gift return capture requires its explicit version")
    source = ResolutionEffectSource.from_effect(effect)
    item = next((candidate for candidate in host.state.stack if candidate.ref == source.stack_ref), None)
    program = host.semantics.get(item.semantic_key) if item is not None else None
    if (item is None or item.kind not in {"spell", "spell_copy"} or item.controller != actor
            or item.context.get("currently_resolving") is not True or len(item.targets) != 1
            or program is None or not host.semantic_program_is_current_trusted(program)
            or RETURN_COPY_CAPABILITY not in runtime_dependencies(program)
            or GiftCardPromise.from_dict(item.context.get(GIFT_CARD_PROMISE_FIELD)).recipient is None):
        raise ValueError("Gift return capture lost its original promised spell")
    return item


def prepare_gift_return_capture(host, effect, actor, card):
    item = _resolving_return_copy(host, effect, actor, RETURN_RECEIPT_FIELD)
    if (item.targets[0] != card.ref or card.owner != actor or card.zone != "graveyard"
            or effect.get("controller") != actor or effect.get("tapped") is not False):
        raise ValueError("Gift return capture does not match its current owned target")
    return item, card.object_id, card.zone_change_counter


def commit_gift_return_capture(binding, entered):
    item, object_id, previous_counter = binding
    if (entered is not None and entered.object_id == object_id and entered.zone == "battlefield"
            and entered.zone_change_counter == previous_counter + 1):
        item.context[_RECEIPT_CONTEXT] = {
            "object_id": entered.object_id, "logical_object_id": entered.logical_object_id, "ref": entered.ref,
        }


def gift_return_copy_reference(host, effect, actor):
    from ..token_copy_references import _snapshot
    from ..token_copy_recipes import TokenCopyExceptionSpec, TokenCopyRecipeSpec

    item = _resolving_return_copy(host, effect, actor, COPY_RECEIPT_FIELD)
    expected = TokenCopyRecipeSpec(origin="target", exception=TokenCopyExceptionSpec(power=1, toughness=1))
    allowed = {"op", "controller", "quantity", "copy_spec", COPY_RECEIPT_FIELD, "_runtime_source", "_replacement_selections", "reason"}
    if (set(effect) - allowed or effect.get("op") != "create_token"
            or effect.get("controller") != actor or type(effect.get("quantity")) is not int
            or effect["quantity"] != 1 or TokenCopyRecipeSpec.from_dict(effect.get("copy_spec")) != expected):
        raise ValueError("Gift return copy changed its one 1/1 token recipe")
    receipt = item.context.get(_RECEIPT_CONTEXT)
    if receipt is None:
        return None
    if not isinstance(receipt, Mapping) or set(receipt) != {"object_id", "logical_object_id", "ref"}:
        raise ValueError("Gift return copy receipt is malformed")
    card = host.state.cards.get(receipt["object_id"])
    if (card is None or card.zone != "battlefield" or card.phased_out
            or card.ref != receipt["ref"] or card.logical_object_id != receipt["logical_object_id"]):
        return None
    _, _, supertypes = host._type_parts(host._effective_card_data(card)["type_line"])
    return None if "legendary" in supertypes else _snapshot(host, card)



SWEEP_RETURN_COST_FIELD = "gift_card_committed_sweep_return"
SWEEP_RETURN_TEMPLATE = "gift-card-committed-sweep-return-v1"
SWEEP_RETURN_CAPABILITY = "casting.additional_cost.gift_card_committed_sweep_return"
SWEEP_RESULT_VERSION = 2
SWEEP_RETURN_DEPENDENCIES = (
    SWEEP_RETURN_CAPABILITY, GIFT_CARD_CAPABILITY, "zone.draw.library_to_hand", "permanent.destroy.fixed_set",
    "zone.change.destination_replacement", "zone.graveyard.ordered_simultaneous_arrival", "zone.reanimate.fixed_target",
)


def sweep_return_cost_schema():
    return {SWEEP_RETURN_COST_FIELD: {"schema_version": 1}}


def sweep_return_base_effect():
    from .destruction_result_model import DestructionFollowup

    return {"op": "destroy_all", "source": "$source", "set": DestructionFollowup.owned_creature_return().scope.to_dict()}


def sweep_return_dependencies(program):
    if (thaw_value(program.cost_schema) != sweep_return_cost_schema()
            or type(program.cost_schema[SWEEP_RETURN_COST_FIELD]["schema_version"]) is not int
            or (program.event, program.active_zone) != ("resolve", "stack")
            or program.event_condition is not None or program.handlers or program.target_schema is not None
            or tuple(thaw_value(program.effects)) != (sweep_return_base_effect(),)):
        raise ValueError("Gift sweep return requires its exact original untargeted destruction")
    return SWEEP_RETURN_DEPENDENCIES


def prepare_gift_sweep_resolution(item, promised):
    from .announced_token_sweep import TOKEN_SWEEP_RESULT_FIELD

    return {"op": "choose_objects", "player": item.controller, TOKEN_SWEEP_RESULT_FIELD: {
        "schema_version": SWEEP_RESULT_VERSION, "stack_ref": item.ref, "controller": item.controller,
        "protected": [], "return_one": promised,
    }}


def validate_gift_sweep_result(host, item, effect):
    from ..gift_card_model import GIFT_CARD_PROMISE_FIELD, GiftCardPromise
    from .announced_token_sweep import TOKEN_SWEEP_ORDER_FIELD, TOKEN_SWEEP_RESULT_FIELD

    program = host.semantics.get(item.semantic_key)
    promise = GiftCardPromise.from_dict(item.context.get(GIFT_CARD_PROMISE_FIELD))
    expected = prepare_gift_sweep_resolution(item, promise.recipient is not None)
    raw = effect[TOKEN_SWEEP_RESULT_FIELD]
    if (item.kind not in {"spell", "spell_copy"} or item.context.get("currently_resolving") is not True
            or program is None or not host.semantic_program_is_current_trusted(program)
            or SWEEP_RETURN_CAPABILITY not in runtime_dependencies(program)
            or set(effect) - {*expected, TOKEN_SWEEP_ORDER_FIELD} or effect.get("op") != "choose_objects"
            or thaw_value(FrozenMap(raw)) != expected[TOKEN_SWEEP_RESULT_FIELD]
            or type(raw.get("schema_version")) is not int or type(raw.get("return_one")) is not bool
            or (TOKEN_SWEEP_ORDER_FIELD not in effect and effect.get("player") != item.controller)):
        raise ValueError("Gift destruction lost its promised original spell and physical sweep")



GREED_COST_FIELD = "gift_card_targeted_sacrifice_hand_return"
GREED_TEMPLATE = "gift-card-greatest-power-sacrifice-hand-return-v1"
GREED_CAPABILITY = "casting.additional_cost.gift_card_greatest_sacrifice_hand_return"


def _greed_sacrifice():
    from ..power_sacrifice_model import GreatestPowerSacrificeSpec

    return GreatestPowerSacrificeSpec(target_player="$target.0", opponent_only=True)


def greed_effects(promised=False):
    sacrifice = _greed_sacrifice().effect()
    if not promised:
        return (sacrifice,)
    return (sacrifice, {"op": "return_graveyard_card_to_owner_hand", "card": "$target.1"})


def greed_target_schema(promised):
    opponent = _greed_sacrifice().target_schema
    if not promised:
        return opponent
    graveyard = return_copy_target_schema()
    return {"groups": [{**opponent, "id": "affected_opponent"}, {**graveyard, "id": "owned_graveyard_creature"}]}


def greed_cost_schema():
    return {GREED_COST_FIELD: {"schema_version": 1, "unpromised": greed_target_schema(False),
                              "promised": greed_target_schema(True)}}


def greed_capabilities():
    from .affected_player_sacrifice_capability_shapes import _greatest_power_sacrifice_dependencies
    from ..public_zone_moves import FIXED_OWNER_ZONE_MOVE_CAPABILITY

    return (GREED_CAPABILITY, GIFT_CARD_CAPABILITY, "zone.draw.library_to_hand", FIXED_OWNER_ZONE_MOVE_CAPABILITY,
            "card.return.own_graveyard_to_owner_hand",
            *_greatest_power_sacrifice_dependencies(_greed_sacrifice()))


def greed_dependencies(program):
    if (thaw_value(program.cost_schema) != greed_cost_schema()
            or type(program.cost_schema[GREED_COST_FIELD]["schema_version"]) is not int
            or (program.event, program.active_zone) != ("resolve", "stack")
            or program.event_condition is not None or program.handlers
            or thaw_value(program.target_schema) != greed_target_schema(False)
            or type(program.target_schema["count"]) is not int
            or type(program.cost_schema[GREED_COST_FIELD]["unpromised"]["count"]) is not int
            or any(type(group["count"]) is not int for group in program.cost_schema[GREED_COST_FIELD]["promised"]["groups"])
            or tuple(thaw_value(program.effects)) != greed_effects()):
        raise ValueError("Gift sacrifice and return require both exact locked target schemas")
    return greed_capabilities()


def require_resolving_greatest_power_opponent(host, item, opponent):
    from ..gift_card_model import GIFT_CARD_PROMISE_FIELD, GiftCardPromise

    program = host.semantics.get(item.semantic_key)
    promise = GiftCardPromise.from_dict(item.context.get(GIFT_CARD_PROMISE_FIELD))
    if (program is None or not host.semantic_program_is_current_trusted(program)
            or GREED_CAPABILITY not in runtime_dependencies(program)
            or item.context.get("targets_revalidated") is not True
            or len(item.targets) != (1 if promise.recipient is None else 2)
            or item.targets[0] != opponent or opponent == item.controller or opponent not in host.active_seats):
        raise ValueError("Greatest-power sacrifice requires its original announced opponent")
