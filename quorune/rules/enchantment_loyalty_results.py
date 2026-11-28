"""Fixed loyalty results for attached Aura creation and ordered enchantment returns."""

from collections.abc import Mapping

from ..replacement.immutable import FrozenMap, thaw_value


MASK_AURA_CAPABILITY = "token.creation.attached_mask_aura"
ORDERED_ENCHANTMENT_RETURN_CAPABILITY = "zone.return.ordered_enchantment_and_aura_sets"
LOYALTY_ENCHANTMENT_CAPABILITIES = frozenset({MASK_AURA_CAPABILITY, ORDERED_ENCHANTMENT_RETURN_CAPABILITY})
_PUBLIC_RETURN_MECHANICS = ("fixed-public-zone-move", "fixed-public-zone-move-set")


def mask_aura_target_schema():
    return {"zones":["battlefield"], "categories":["permanent"], "count":1, "source_exclusion":True}


def mask_aura_token_effect():
    from ..ability_fragments import ability_fragment_to_dict
    from ..enchant_spec import SimpleEnchantSpec
    from ..fixed_token_production import TOKEN_LITERAL_NAME_VERSION_FIELD
    from ..umbra_armor_model import UmbraArmorSpec

    return {"op":"create_token", "controller":"$controller", "quantity":1, "name":"Mask",
            "aura_target_ref":"$target.0", "characteristics":{
                "type_line":"Token Enchantment — Aura", "colors":["W"], TOKEN_LITERAL_NAME_VERSION_FIELD:1,
                "ability_fragments":[ability_fragment_to_dict(fragment)
                                     for fragment in (SimpleEnchantSpec("permanent"), UmbraArmorSpec())],
            }}


def _enchantment_return_effect(*, aura):
    from ..object_predicate import ObjectQuerySpec
    from ..public_zone_moves import (
        PUBLIC_AURA_ENTRY_VERSION, PublicBattlefieldController, PublicZoneDestination, PublicZoneMoveSetSpec,
        PublicZoneOrigin, PublicZoneRelationAxis, PublicZoneSeatRelation,
    )

    query = ObjectQuerySpec(zones=("graveyard",), types_all=("enchantment",),
                           subtypes_all=("aura",) if aura else (),
                           excluded_subtypes=() if aura else ("aura",))
    spec = PublicZoneMoveSetSpec(query=query, origin=PublicZoneOrigin.GRAVEYARD,
                                 destination=PublicZoneDestination.BATTLEFIELD,
                                 relation_axis=PublicZoneRelationAxis.OWNER, seat_relation=PublicZoneSeatRelation.ACTOR,
                                 battlefield_controller=PublicBattlefieldController.OWNER,
                                 schema_version=PUBLIC_AURA_ENTRY_VERSION)
    return {"op":"move_public_zone_set", "source":"$source", "set":spec.to_dict()}


def ordered_enchantment_return_effects():
    return ({"op":"mill", "player":"$controller", "count":7},
            _enchantment_return_effect(aura=False), _enchantment_return_effect(aura=True))


def enchantment_loyalty_template(ability, text):
    if ability.loyalty_delta not in (-1, -7):
        return None
    normalized = " ".join(text.split()).casefold()
    if ability.loyalty_delta == -1 and normalized == (
        "create a white aura enchantment token named mask attached to another target permanent. "
        "the token has enchant permanent and umbra armor."
    ):
        return ("loyalty-attached-mask-aura-v1", (mask_aura_token_effect(),), mask_aura_target_schema(),
                ("cr-111-tokens", "cr-115-targets"))
    if ability.loyalty_delta == -7 and normalized == (
        "mill seven cards. return all non-aura enchantment cards from your graveyard to the battlefield, "
        "then do the same for aura cards."
    ):
        from .mill_capability_shapes import MILL_MECHANIC_ID
        return ("loyalty-mill-and-ordered-enchantment-return-v1", ordered_enchantment_return_effects(), None,
                (MILL_MECHANIC_ID, *_PUBLIC_RETURN_MECHANICS))
    return None


def _fixed_negative_loyalty_price(cost, delta):
    from .linked_loyalty_exile_shapes import _linked_loyalty_cost_is_closed

    return (isinstance(cost, Mapping) and type(cost.get("loyalty_delta")) is int
            and cost["loyalty_delta"] == delta and type(cost.get("text")) is str
            and cost["text"].replace("−", "-") == str(delta)
            and _linked_loyalty_cost_is_closed({**cost, "text":"0", "loyalty_delta":0}, "zero"))


def enchantment_loyalty_node_capabilities(*, effects, target_schema, mechanics, active_zone, cost_schema):
    if active_zone != "battlefield" or len(effects) not in (1, 3):
        return ()
    if len(effects) == 1 and (effects[0].get("op"), effects[0].get("name")) != ("create_token", "Mask"):
        return ()
    if len(effects) == 3 and effects[0].get("op") != "mill":
        return ()
    actual = tuple(thaw_value(effect) for effect in effects)
    if (len(actual) == 1 and actual == (mask_aura_token_effect(),)
            and isinstance(target_schema, Mapping) and type(target_schema.get("count")) is int
            and FrozenMap(target_schema) == FrozenMap(mask_aura_target_schema())
            and {"cr-111-tokens", "cr-115-targets"} <= set(mechanics)
            and _fixed_negative_loyalty_price(cost_schema, -1)):
        from ..umbra_armor_model import UMBRA_ARMOR_CAPABILITY, UMBRA_ARMOR_FRAGMENT_CAPABILITY
        return (MASK_AURA_CAPABILITY, "token.creation.fixed_definition", "target.revalidate_resolution",
                "attachment.aura.simple_object", UMBRA_ARMOR_CAPABILITY, UMBRA_ARMOR_FRAGMENT_CAPABILITY)
    if (len(actual) != 3 or target_schema is not None or actual != ordered_enchantment_return_effects()
            or not _fixed_negative_loyalty_price(cost_schema, -7)):
        return ()
    from .mill_capability_shapes import fixed_mill_node_capabilities
    from .public_zone_move_capability_shapes import fixed_public_zone_move_set_node_capabilities
    mill = fixed_mill_node_capabilities(effects=effects[:1], target_schema=None, mechanic_ids=mechanics)
    returns = tuple(fixed_public_zone_move_set_node_capabilities(
        effects=(effect,), target_schema=None, mechanic_ids=mechanics,
    ) for effect in effects[1:])
    if not mill or not all(returns):
        return ()
    return tuple(sorted({ORDERED_ENCHANTMENT_RETURN_CAPABILITY, *mill, *returns[0], *returns[1]}))


def enchantment_loyalty_runtime_dependencies(program):
    from .linked_loyalty_exile_shapes import has_plain_loyalty_catalog

    claimed = bool(LOYALTY_ENCHANTMENT_CAPABILITIES.intersection(program.capability_dependencies))
    mask = any(effect.get("op") == "create_token" and effect.get("name") == "Mask" and "aura_target_ref" in effect
               for effect in program.effects)
    ordered = (len(program.effects) == 3 and program.effects[0].get("op") == "mill"
               and tuple(thaw_value(effect) for effect in program.effects) == ordered_enchantment_return_effects())
    if not (claimed or mask or ordered):
        return ()
    required = enchantment_loyalty_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanics=program.coverage,
        active_zone=program.active_zone, cost_schema=program.cost_schema,
    )
    if not required or not has_plain_loyalty_catalog(program, require_semantic_key=True):
        raise ValueError("Enchantment loyalty result requires its original price, public scope and effect order")
    return required


def loyalty_result_runtime_dependencies(program):
    from .loyalty_damage import loyalty_damage_runtime_dependencies

    return enchantment_loyalty_runtime_dependencies(program) or loyalty_damage_runtime_dependencies(program)


def is_closed_enchantment_loyalty_program(program):
    try:
        required = enchantment_loyalty_runtime_dependencies(program)
    except (TypeError, ValueError, KeyError):
        return False
    return bool(required) and set(required).issubset(program.capability_dependencies)
