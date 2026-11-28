"""Bind controller sacrifice selections to their committed results."""

from collections.abc import Mapping

from ..apnap_object_payment import ORDER_GRAVEYARD_ARRIVALS

from ..object_predicate import ObjectQuerySpec
from ..object_query import object_matches_query
from ..private_hand_entry_model import (
    PRIVATE_HAND_COLLECTION_MECHANICS, PRIVATE_HAND_COLLECTION_OPERATION, decode_private_hand_collection,
)
from ..replacement.immutable import FrozenMap
from .committed_quantity_followups import (
    CommittedQuantityFollowup, fixed_followup_capabilities, fixed_quantity_effect, quantity_followup_from_count,
)
from .effect_shape_walkers import nested_effects
from .committed_land_sacrifice_search import (
    COMMITTED_LAND_SEARCH_CAPABILITY, commit_land_sacrifice_search, validate_land_sacrifice_search_recipe,
)
from .named_searches import (
    SACRIFICE_BOUND_CREATURE_RECIPE_FIELD, SACRIFICE_BOUND_CREATURE_SEARCH_CAPABILITY,
    committed_sacrifice_creature_search_choice, sacrifice_bound_creature_search_recipe,
    validate_sacrifice_bound_creature_search_recipe,
)
from .graveyard_return_choices import (
    CAPABILITY as GRAVEYARD_RETURN_CHOICE_CAPABILITY,
    COMMITTED_GRAVEYARD_RETURN_CAPABILITY, COMMITTED_GRAVEYARD_RETURN_RECIPE,
    OPERATION as GRAVEYARD_RETURN_CHOICE, committed_graveyard_return_effect,
)
from .committed_sacrifice_damage import (
    COMMITTED_SACRIFICE_DAMAGE_CAPABILITY, capture_committed_sacrifice_damage_target,
    committed_sacrifice_damage_dependencies, committed_sacrifice_damage_domain_matches,
    committed_sacrifice_damage_followup, committed_sacrifice_damage_recipe_parts, committed_sacrifice_damage_target,
)


ANY_NUMBER_SACRIFICE_FIELD = "any_number_sacrifice_result"
ANY_NUMBER_SACRIFICE_CAPABILITY = "choice.affected_player.any_number_committed_sacrifice"
COMMITTED_LAND_SACRIFICE_MANA_CAPABILITY = "mana.production.committed_land_sacrifice"
ANY_NUMBER_SACRIFICE_TEMPLATE = "any-number-committed-sacrifice-v1"
COMMITTED_SACRIFICE_HAND_ENTRY_CAPABILITY = "resolution.effect.committed_sacrifice_hand_entry"
COMMITTED_SACRIFICE_HAND_ENTRY_TEMPLATE = "committed-sacrifice-draw-hand-entry-v1"
ANY_NUMBER_SACRIFICE_COUNT = FrozenMap({"kind": "any_number_sacrifice", "schema_version": 1})
_SCOPE = ("battlefield",)
_ALL_PERMANENTS = ObjectQuerySpec(zones=_SCOPE)
_ARTIFACTS_ENCHANTMENTS = ObjectQuerySpec(zones=_SCOPE, types_any=("artifact", "enchantment"))
_PREDICATES = {
    "permanents": _ALL_PERMANENTS,
    "other_permanents": _ALL_PERMANENTS,
    "artifact_creature_land": ObjectQuerySpec(zones=_SCOPE, types_any=("artifact", "creature", "land")),
    "artifact_enchantment_token": _ALL_PERMANENTS,
    "lands": ObjectQuerySpec(zones=_SCOPE, types_all=("land",)),
    "mountains": ObjectQuerySpec(zones=_SCOPE, types_all=("land",), subtypes_all=("mountain",)),
    "creatures": ObjectQuerySpec(zones=_SCOPE, types_all=("creature",)),
}
_SELF_ENTRY_DOMAINS = {"other_permanents", "artifact_enchantment_token"}
_COLORLESS_RECIPE = FrozenMap({"op": "mana", "player": "$controller", "color": "C", "amount": 1})
_PRIVATE_DRAW = FrozenMap({"op": "draw", "player": "$controller", "count": 1, "private": True})


def any_number_self_entry(domain, followup):
    return domain in _SELF_ENTRY_DOMAINS and followup["op"] != GRAVEYARD_RETURN_CHOICE


def any_number_sacrifice_count(count):
    return (isinstance(count, Mapping) and FrozenMap(count) == ANY_NUMBER_SACRIFICE_COUNT
            and type(count["schema_version"]) is int)


def _result_version(followup):
    return {
        "mana": 2, "damage": 3, PRIVATE_HAND_COLLECTION_OPERATION: 4, "search": 5, GRAVEYARD_RETURN_CHOICE: 5,
    }.get(followup["op"], 1)


def any_number_sacrifice_effect(domain, followup, *, actor="$controller", excluded="$source.zone_object"):
    fixed_creature = followup["op"] == PRIVATE_HAND_COLLECTION_OPERATION
    return {
        "op": "choose_cards_apnap", "actor": actor, "players": [actor],
        "zone": "battlefield", "predicate": _PREDICATES[domain].to_dict(),
        "count": 1 if fixed_creature else dict(ANY_NUMBER_SACRIFICE_COUNT), "then": "sacrifice",
        "prompt": ("Choose a creature you control to sacrifice." if fixed_creature
                   else "Choose any number of creatures you control to sacrifice." if SACRIFICE_BOUND_CREATURE_RECIPE_FIELD in followup
                   else "Choose any number of permanents you control to sacrifice."),
        ANY_NUMBER_SACRIFICE_FIELD: {
            "schema_version": _result_version(followup), "domain": domain, "followup": followup,
        },
        **({"exclude_ref": excluded} if domain == "other_permanents" and excluded is not None else {}),
    }


def any_number_sacrifice_followup(value, *, actor="$controller"):
    if not isinstance(value, Mapping) or value.get("player") != actor:
        raise ValueError("Any-number sacrifice needs its resolving controller's fixed result")
    if value.get("op") == "damage":
        return committed_sacrifice_damage_recipe_parts(value, actor=actor)
    if SACRIFICE_BOUND_CREATURE_RECIPE_FIELD in value:
        validate_sacrifice_bound_creature_search_recipe(value, actor=actor)
        return FrozenMap(sacrifice_bound_creature_search_recipe())
    if value.get("op") == "search":
        validate_land_sacrifice_search_recipe(value, actor=actor)
        return FrozenMap({**value, "player": "$controller"})
    if value.get("op") == PRIVATE_HAND_COLLECTION_OPERATION:
        spec = decode_private_hand_collection(value, actor=actor)
        if not spec.one_of_each:
            raise ValueError("Sacrifice and draw require the complete creature and land slots")
        return FrozenMap({**value, "player": "$controller", "selection": spec.to_dict()})
    normalized = {**value, "player": "$controller"}
    if FrozenMap(normalized) == COMMITTED_GRAVEYARD_RETURN_RECIPE:
        return COMMITTED_GRAVEYARD_RETURN_RECIPE
    if FrozenMap(normalized) == _COLORLESS_RECIPE and type(value.get("amount")) is int:
        return _COLORLESS_RECIPE
    canonical = fixed_quantity_effect(normalized)
    if canonical["op"] != "draw" and canonical != FrozenMap({"op": "life", "player": "$controller", "delta": 2}):
        raise ValueError("Any-number sacrifice supports one private draw or two life per committed permanent")
    return canonical


def any_number_sacrifice_parts(effect, *, actor="$controller", excluded="$source.zone_object"):
    if not isinstance(effect, Mapping):
        raise ValueError("Committed sacrifice requires its complete selection")
    marker = effect.get(ANY_NUMBER_SACRIFICE_FIELD)
    if (not isinstance(marker, Mapping) or set(marker) != {"schema_version", "domain", "followup"}
            or type(marker["schema_version"]) is not int
            or type(marker["domain"]) is not str or marker["domain"] not in _PREDICATES):
        raise ValueError("Any-number sacrifice requires its printed domain and committed result")
    domain = marker["domain"]
    followup = any_number_sacrifice_followup(marker["followup"], actor=actor)
    if followup["op"] == PRIVATE_HAND_COLLECTION_OPERATION:
        if (domain != "creatures" or type(effect.get("count")) is not int or effect["count"] != 1
                or FrozenMap(effect) != FrozenMap(any_number_sacrifice_effect(
                    domain, marker["followup"], actor=actor, excluded=excluded))):
            raise ValueError("Sacrifice and draw require one mandatory creature and its exact private entry")
        return domain, followup
    if not any_number_sacrifice_count(effect.get("count")):
        raise ValueError("Any-number sacrifice must preserve its zero-inclusive selection")
    if SACRIFICE_BOUND_CREATURE_RECIPE_FIELD in followup:
        if domain != "creatures" or FrozenMap(effect) != FrozenMap(any_number_sacrifice_effect(
            domain, marker["followup"], actor=actor, excluded=excluded,
        )):
            raise ValueError("The committed search requires the complete any-number creature sacrifice")
        return domain, followup
    damage_domain = domain in {"mountains", "creatures"}
    if ((followup["op"] in {"mana", "search"}) != (domain == "lands")
            or (followup["op"] == "damage") != damage_domain
            or damage_domain and not committed_sacrifice_damage_domain_matches(domain, followup)
            or followup["op"] == "life" and domain != "permanents"
            or followup["op"] == GRAVEYARD_RETURN_CHOICE and domain != "artifact_enchantment_token"

            or FrozenMap(effect) != FrozenMap(any_number_sacrifice_effect(
                domain, marker["followup"], actor=actor, excluded=excluded,
            ))):
        raise ValueError("Any-number sacrifice uses only the complete printed selection and result")
    return domain, followup


def any_number_sacrifice_row_matches(effect, row):
    marker = effect.get(ANY_NUMBER_SACRIFICE_FIELD)
    if not isinstance(marker, Mapping) or marker.get("domain") != "artifact_enchantment_token":
        return True
    return row.token or object_matches_query(row, _ARTIFACTS_ENCHANTMENTS)


def any_number_sacrifice_commit_fields(host, item, effect, state, *, actor, selected_refs, incarnation_field):
    if ANY_NUMBER_SACRIFICE_FIELD not in effect:
        return {}
    _domain, followup = any_number_sacrifice_parts(effect, actor=actor, excluded=effect.get("exclude_ref"))
    if not selected_refs:
        return {}
    payload = {
        "schema_version": _result_version(followup), "followup": followup,
        "selected_incarnations": {ref: state[incarnation_field][ref] for ref in selected_refs},
    }
    if followup["op"] == "damage":
        payload["target"] = capture_committed_sacrifice_damage_target(host, item)
    return {
        ANY_NUMBER_SACRIFICE_FIELD: payload,
        **({ORDER_GRAVEYARD_ARRIVALS: True} if followup["op"] == "search" else {}),
    }


def any_number_sacrifice_commit_parts(payload, refs, *, actor):
    if not isinstance(payload, Mapping) or type(payload.get("schema_version")) is not int:
        raise ValueError("Committed any-number sacrifices require their exact revision and follow-up")
    version = payload["schema_version"]
    expected = {"schema_version", "followup", "selected_incarnations"}
    if version == 3:
        expected.add("target")
    if version not in (1, 2, 3, 4, 5) or set(payload) != expected or version == 4 and len(refs) != 1:
        raise ValueError("Committed any-number sacrifices require their exact revision and follow-up")
    selected = payload["selected_incarnations"]
    if (not refs or not isinstance(selected, Mapping) or set(selected) != set(refs)
            or any(type(identity) is not str or not identity for identity in selected.values())
            or len(set(selected.values())) != len(refs)):
        raise ValueError("Committed any-number sacrifices lost a selected public incarnation")
    followup = payload["followup"]
    receiver = followup.get("player") if isinstance(followup, Mapping) else None
    if receiver not in ("$controller", actor):
        raise ValueError("Committed any-number sacrifice result lost its resolving controller")
    canonical = any_number_sacrifice_followup(followup, actor=receiver)
    if version != _result_version(canonical):
        raise ValueError("Committed sacrifice result requires its explicit payload revision")
    target = committed_sacrifice_damage_target(payload["target"]) if version == 3 else None
    return canonical, FrozenMap(selected), target


def _source_mana_instruction(recipe):
    return FrozenMap({**recipe, "source": "$source"})


def resolve_any_number_sacrifice_followup(host, followup, count, *, target=None, committed_members=(), actor=None):
    if SACRIFICE_BOUND_CREATURE_RECIPE_FIELD in followup:
        choice = committed_sacrifice_creature_search_choice(host, actor, count)
        return CommittedQuantityFollowup((FrozenMap(choice),))
    if followup["op"] == "search":
        item = next((entry for entry in host.state.stack if entry.context.get("currently_resolving") is True), None)
        return CommittedQuantityFollowup((commit_land_sacrifice_search(host, item, committed_members),))
    if followup["op"] == PRIVATE_HAND_COLLECTION_OPERATION:
        if type(count) is not int or count not in (0, 1):
            raise ValueError("A mandatory creature sacrifice produces at most one committed result")
        return CommittedQuantityFollowup((_PRIVATE_DRAW, followup) if count == 1 else ())
    if followup["op"] == GRAVEYARD_RETURN_CHOICE:
        if type(count) is not int or count < 0:
            raise ValueError("Committed graveyard return requires an actual sacrifice count")
        return CommittedQuantityFollowup((FrozenMap(committed_graveyard_return_effect(count)),) if count else ())
    if followup["op"] == "damage":
        return committed_sacrifice_damage_followup(host, followup, target, count)
    if followup["op"] == "mana":
        followup = _source_mana_instruction(followup)
    return quantity_followup_from_count(followup, count)


def zero_land_sacrifice_search(host, item, effect, selected_refs):
    marker = effect.get(ANY_NUMBER_SACRIFICE_FIELD)
    if selected_refs or not isinstance(marker, Mapping) or marker.get("schema_version") != 5:
        return ()
    _, followup = any_number_sacrifice_parts(effect, actor=item.controller)
    if followup["op"] != "search" or SACRIFICE_BOUND_CREATURE_RECIPE_FIELD in followup:
        return ()
    return (commit_land_sacrifice_search(host, item, ()),)


def zero_sacrifice_search_effects(host, effect, actor, selected):
    if selected:
        return ()
    marker = effect.get(ANY_NUMBER_SACRIFICE_FIELD)
    if not isinstance(marker, Mapping) or marker.get("schema_version") != 5:
        return ()
    _domain, followup = any_number_sacrifice_parts(effect, actor=actor)
    if SACRIFICE_BOUND_CREATURE_RECIPE_FIELD not in followup:
        return ()
    return (committed_sacrifice_creature_search_choice(host, actor, 0),)


def is_sacrifice_search_chapter(event):
    return isinstance(event, str) and event.startswith("saga.chapter.") and event.removeprefix("saga.chapter.").isdigit()


def _land_sacrifice_mana_dependencies(recipe):
    from .fixed_resolution_mana_shapes import fixed_resolution_mana_capabilities

    owned = fixed_resolution_mana_capabilities(
        effects=(_source_mana_instruction(recipe),), target_schema=None, mechanic_ids=("fixed-resolution-mana",),
    )
    if not owned:
        raise ValueError("Committed land sacrifice uses one ordinary fixed colorless mana per departure")
    return (COMMITTED_LAND_SACRIFICE_MANA_CAPABILITY, *owned)


def any_number_sacrifice_capabilities(effect, target_schema):
    domain, result = any_number_sacrifice_parts(effect)
    if result["op"] == PRIVATE_HAND_COLLECTION_OPERATION:
        from .private_hand_entry_shapes import private_hand_collection_node_capabilities

        if target_schema is not None:
            raise ValueError("Sacrifice and private entry choose without targets")
        collection = private_hand_collection_node_capabilities(
            effects=(result,), target_schema=None, mechanic_ids=PRIVATE_HAND_COLLECTION_MECHANICS,
        )
        return (COMMITTED_SACRIFICE_HAND_ENTRY_CAPABILITY, "choice.affected_player.fixed_sacrifice",
                "zone.draw.library_to_hand", *collection)
    if result["op"] == "damage":
        followup = committed_sacrifice_damage_dependencies(result, target_schema)
    else:
        if target_schema is not None:
            raise ValueError("Any-number sacrifice has no target outside its exact damage result")
        if result["op"] == "search":
            capability = (SACRIFICE_BOUND_CREATURE_SEARCH_CAPABILITY if SACRIFICE_BOUND_CREATURE_RECIPE_FIELD in result
                          else COMMITTED_LAND_SEARCH_CAPABILITY)
            followup = (capability, "library.search.fixed_counted")
        elif result["op"] == GRAVEYARD_RETURN_CHOICE:
            followup = (
                COMMITTED_GRAVEYARD_RETURN_CAPABILITY, GRAVEYARD_RETURN_CHOICE_CAPABILITY,
                "attachment.aura.typed_restriction",
            )
        else:
            followup = _land_sacrifice_mana_dependencies(result) if domain == "lands" else fixed_followup_capabilities(result)
    return (ANY_NUMBER_SACRIFICE_CAPABILITY, "choice.affected_player.fixed_sacrifice",
            "zone.change.destination_replacement", *followup,
            *(("trigger.event.normalized_zone_change", "trigger.placement.apnap") if any_number_self_entry(domain, result) else ()))


def any_number_sacrifice_runtime_dependencies(program):
    effects = (*program.effects, *program.handlers)
    marked = tuple(effect for effect in nested_effects(effects) if ANY_NUMBER_SACRIFICE_FIELD in effect)
    authorities = {
        ANY_NUMBER_SACRIFICE_CAPABILITY, COMMITTED_LAND_SACRIFICE_MANA_CAPABILITY, COMMITTED_SACRIFICE_DAMAGE_CAPABILITY,
        COMMITTED_LAND_SEARCH_CAPABILITY,
        SACRIFICE_BOUND_CREATURE_SEARCH_CAPABILITY,
        COMMITTED_GRAVEYARD_RETURN_CAPABILITY,
    }
    if COMMITTED_SACRIFICE_HAND_ENTRY_CAPABILITY in program.capability_dependencies:
        if ((program.event, program.active_zone) != ("resolve", "stack")
                or program.event_condition is not None or program.cost_schema is not None):
            raise ValueError("Sacrifice and private entry require the original resolving spell")
        if not marked and not isinstance((program.target_schema or {}).get("modes"), Mapping):
            raise ValueError("Sacrifice and private entry authority requires its complete result")
    if not marked and authorities.isdisjoint(program.capability_dependencies):
        return ()
    if len(marked) != 1 or tuple(program.effects) != marked or program.handlers or program.cost_schema is not None:
        raise ValueError("Any-number sacrifice requires the complete printed body")
    domain, result = any_number_sacrifice_parts(marked[0])
    if COMMITTED_SACRIFICE_DAMAGE_CAPABILITY in program.capability_dependencies and result["op"] != "damage":
        raise ValueError("Committed sacrifice damage must retain the original stock target result")
    creature_search = SACRIFICE_BOUND_CREATURE_RECIPE_FIELD in result
    if COMMITTED_LAND_SEARCH_CAPABILITY in program.capability_dependencies and (result["op"] != "search" or creature_search):
        raise ValueError("Committed land search must retain the original search follow-up")
    if COMMITTED_LAND_SACRIFICE_MANA_CAPABILITY in program.capability_dependencies and result["op"] != "mana":
        raise ValueError("Committed colorless mana must retain its land sacrifice domain")
    if creature_search:
        if not is_sacrifice_search_chapter(program.event) or program.active_zone != "battlefield" or program.event_condition is not None:
            raise ValueError("The committed sacrifice search requires its original battlefield chapter")
        return any_number_sacrifice_capabilities(marked[0], program.target_schema)
    if SACRIFICE_BOUND_CREATURE_SEARCH_CAPABILITY in program.capability_dependencies:
        raise ValueError("Committed sacrifice search authority requires its original creature search")
    graveyard_return = result["op"] == GRAVEYARD_RETURN_CHOICE
    if COMMITTED_GRAVEYARD_RETURN_CAPABILITY in program.capability_dependencies and not graveyard_return:
        raise ValueError("Committed sacrifice return must keep its original graveyard result")
    source = ("permanent.enter.self", "battlefield") if any_number_self_entry(domain, result) else ("resolve", "stack")
    if (program.event, program.active_zone) != source or program.event_condition is not None:
        raise ValueError("Any-number sacrifice requires its original spell or self entry")
    return any_number_sacrifice_capabilities(marked[0], program.target_schema)
