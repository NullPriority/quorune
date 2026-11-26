"""A fixed graveyard pair returns after one mandatory creature sacrifice."""

from collections.abc import Mapping

from ..replacement.immutable import FrozenMap
from .effect_shape_walkers import nested_effects


MANDATORY_CREATURE_SACRIFICE_VERSION = 17
MANDATORY_ARTIFACT_REANIMATION_VERSION = 18
MANDATORY_REANIMATION_PAYMENT_VERSIONS = (
    MANDATORY_CREATURE_SACRIFICE_VERSION, MANDATORY_ARTIFACT_REANIMATION_VERSION,
)
MANDATORY_SACRIFICE_REANIMATION_CAPABILITY = "zone.reanimate.after_mandatory_creature_sacrifice"
_TARGET = FrozenMap({"zones": ["graveyard"], "categories": ["card"], "owner_relation": "you",
                     "types_any": ["creature"], "count": 2})
_RETURN = FrozenMap({"op": "reanimate_targets", "cards": "$targets", "maximum_targets": 2,
                     "schema_version": 1, "battlefield_controller": "actor", "battlefield_tapped": True})
_PRINTED = ("choose two target creature cards in your graveyard. sacrifice a creature. "
            "if you do, return the chosen cards to the battlefield tapped.")
_RETURN_MECHANICS = ("fixed-target-reanimation", "cr-115-targets", "fixed-homogeneous-target-set",
                     "fixed-target-reanimation-set")
_ARTIFACT_TARGET = FrozenMap({"groups": [{
    "id": "artifact_card", "zones": ["graveyard"], "categories": ["card"],
    "owner": "you", "artifact": True, "count": 1,
}]})
_ARTIFACT_RETURN = FrozenMap({"op": "reanimate", "card": "$target.0", "controller": "$controller", "tapped": False})
_ARTIFACT_PRINTED = "sacrifice an artifact. if you do, return target artifact card from your graveyard to the battlefield."
_ARTIFACT_MECHANICS = ("fixed-target-reanimation", "cr-115-targets")


def uses_mandatory_sacrifice_reanimation(effect):
    payment = effect.get("payment")
    return (effect.get("op") == "offer_optional_mana_payment" and isinstance(payment, Mapping)
            and payment.get("schema_version") in MANDATORY_REANIMATION_PAYMENT_VERSIONS)


def mandatory_sacrifice_reanimation_parts(effect, *, resolved=False):
    from ..fixed_effect_payment import FixedEffectPaymentSpec

    if (not uses_mandatory_sacrifice_reanimation(effect)
            or set(effect) != {"op", "schema_version", "player", "payment", "effects"}
            or type(effect["schema_version"]) is not int or effect["schema_version"] != 2
            or type(effect["player"]) is not str or not effect["player"]
            or not resolved and effect["player"] != "$controller"):
        raise ValueError("Mandatory creature sacrifice requires its fixed reanimation payload")
    payment = FixedEffectPaymentSpec.from_dict(effect["payment"], allow_mandatory=True)
    if payment.predicate.controller != effect["player"]:
        raise ValueError("Only the resolving player sacrifices a creature")
    effects = effect["effects"]
    if not isinstance(effects, (tuple, list)) or len(effects) != 1 or not isinstance(effects[0], Mapping):
        raise ValueError("Mandatory creature sacrifice requires one original reanimation instruction")
    child = effects[0]
    if payment.schema_version == MANDATORY_ARTIFACT_REANIMATION_VERSION:
        expected = _ARTIFACT_RETURN
        if resolved:
            card = child.get("card")
            if type(card) is not str or not card or card.startswith("$"):
                raise ValueError("Artifact return requires the original announced target")
            expected = FrozenMap({**expected, "card": card, "controller": effect["player"]})
        if child.get("tapped") is not False or FrozenMap(child) != expected:
            raise ValueError("Artifact sacrifice returns its original graveyard artifact under its controller")
        return payment, child
    cards = child.get("cards")
    expected = _RETURN
    if resolved:
        if (not isinstance(cards, (list, tuple)) or not 1 <= len(cards) <= 2
                or any(type(ref) is not str or not ref or ref.startswith("$") for ref in cards)
                or len(cards) != len(set(cards))):
            raise ValueError("Only the remaining legal announced creature cards can return")
        expected = FrozenMap({**_RETURN, "cards": cards})
    if (type(child.get("maximum_targets")) is not int or type(child.get("schema_version")) is not int
            or child.get("battlefield_tapped") is not True or FrozenMap(child) != expected):
        raise ValueError("The surviving original targets must return together and tapped")
    return payment, child


def mandatory_sacrifice_reanimation_payload_dependencies(effect):
    from .fixed_homogeneous_target_set_capability_shapes import fixed_homogeneous_target_set_node_capabilities
    from ..replacement.immutable import thaw_value

    payment, child = mandatory_sacrifice_reanimation_parts(effect)
    if payment.schema_version == MANDATORY_ARTIFACT_REANIMATION_VERSION:
        from .reanimation_capability_shapes import fixed_target_reanimation_node_capabilities

        leaf_target = {
            "zones": ["graveyard"], "categories": ["card"], "owner_relation": "you",
            "types_any": ["artifact"], "count": 1,
        }
        owned = fixed_target_reanimation_node_capabilities(
            effects=(child,), target_schema=leaf_target, mechanic_ids=_ARTIFACT_MECHANICS,
        )
    else:
        owned = fixed_homogeneous_target_set_node_capabilities(
            effects=(child,), target_schema=thaw_value(_TARGET), mechanic_ids=_RETURN_MECHANICS,
        )
    if not owned:
        raise ValueError("The sacrifice requires the existing fixed target reanimation owner")
    return tuple(dict.fromkeys((*payment.capabilities, *owned)))


def mandatory_sacrifice_reanimation_node_dependencies(effect, target_schema):
    if effect["payment"]["schema_version"] == MANDATORY_ARTIFACT_REANIMATION_VERSION:
        if (not isinstance(target_schema, Mapping) or FrozenMap(target_schema) != _ARTIFACT_TARGET
                or type(target_schema["groups"][0]["count"]) is not int
                or target_schema["groups"][0]["artifact"] is not True):
            raise ValueError("The sacrifice requires one target artifact card in its controller's graveyard")
        return (*mandatory_sacrifice_reanimation_payload_dependencies(effect),
                "target.revalidate_resolution", "zone.change.destination_replacement")
    if (not isinstance(target_schema, Mapping) or type(target_schema.get("count")) is not int
            or FrozenMap(target_schema) != _TARGET):
        raise ValueError("The sacrifice requires exactly two distinct owned graveyard creature targets")
    return mandatory_sacrifice_reanimation_payload_dependencies(effect)


def mandatory_sacrifice_reanimation_runtime_dependencies(program):
    effects = tuple(effect for effect in nested_effects((program.effects, program.handlers, program.target_schema))
                    if uses_mandatory_sacrifice_reanimation(effect))
    if not effects:
        return ()
    if (len(effects) == 1 and effects[0]["payment"]["schema_version"] == MANDATORY_ARTIFACT_REANIMATION_VERSION):
        from ..semantic_runtime.activated_abilities import ActivatedAbilityCatalogHandler

        delta = (program.cost_schema or {}).get("loyalty_delta")
        if (effects != tuple(program.effects) or program.event_condition is not None
                or (program.active_zone, program.event) != ("battlefield", "activate")
                or len(program.handlers) != 1 or type(delta) is not int or delta >= 0):
            raise ValueError("Artifact sacrifice and return require the complete original loyalty activation")
        ability = ActivatedAbilityCatalogHandler().validate(program.handlers[0])
        if (ability.mana_ability or ability.builtin_semantic_key != program.key
                or ability.loyalty_delta != delta):
            raise ValueError("Artifact sacrifice target selection differs from its activation catalog")
        return mandatory_sacrifice_reanimation_node_dependencies(effects[0], program.target_schema)
    if (len(effects) != 1 or effects != tuple(program.effects) or program.handlers
            or program.cost_schema is not None or program.event_condition is not None
            or (program.active_zone, program.event) != ("stack", "resolve")
            or not program.ability_id.startswith("spell:")):
        raise ValueError("Mandatory creature sacrifice and reanimation require the complete original spell")
    return mandatory_sacrifice_reanimation_node_dependencies(effects[0], program.target_schema)


def mandatory_sacrifice_reanimation_template(text, *, source_is_permanent=False):
    printed = " ".join(text.split()).casefold()
    artifact = printed == _ARTIFACT_PRINTED and source_is_permanent is True
    if printed != _PRINTED and not artifact:
        return None
    from ..fixed_effect_payment import FIXED_EFFECT_PAYMENT_MECHANIC, FixedEffectPaymentSpec, mandatory_sacrifice_predicate
    from ..replacement.immutable import thaw_value

    version = MANDATORY_ARTIFACT_REANIMATION_VERSION if artifact else MANDATORY_CREATURE_SACRIFICE_VERSION
    payment = FixedEffectPaymentSpec(
        "sacrifice", schema_version=version,
        predicate=mandatory_sacrifice_predicate("$controller", "artifact" if artifact else "creature"),
    )
    result, target, mechanics = (_ARTIFACT_RETURN, _ARTIFACT_TARGET, _ARTIFACT_MECHANICS) if artifact else (
        _RETURN, _TARGET, _RETURN_MECHANICS,
    )
    effect = {"op": "offer_optional_mana_payment", "schema_version": 2, "player": "$controller",
              "payment": payment.to_dict(), "effects": [thaw_value(result)]}
    template = ("mandatory-artifact-sacrifice-to-return-graveyard-artifact-v1" if artifact
                else "mandatory-creature-sacrifice-to-return-graveyard-pair-v1")
    return template, (effect,), thaw_value(target), (FIXED_EFFECT_PAYMENT_MECHANIC, *mechanics)
