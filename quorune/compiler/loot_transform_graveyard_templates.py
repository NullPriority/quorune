"""Lower the complete loot-return or paid graveyard permission on its original cost."""

from ..rules.loot_transform_graveyard import LOOT_TRANSFORM_CAPABILITY, LOOT_TRANSFORM_MECHANIC, loot_transform_dependencies, loot_transform_effects
from ..rules.loyalty_graveyard_cast import (
    LOYALTY_GRAVEYARD_CAST_CAPABILITY, LOYALTY_GRAVEYARD_CAST_MECHANIC,
    loyalty_graveyard_cast_dependencies, loyalty_graveyard_cast_effect, loyalty_graveyard_targets,
)
from ..rules.artifact_graveyard_cast_activation import (
    ARTIFACT_GRAVEYARD_CAST_CAPABILITY, ARTIFACT_GRAVEYARD_CAST_MECHANIC,
    artifact_graveyard_cast_dependencies, artifact_graveyard_cast_effect, artifact_graveyard_cast_target,
    first_paid_graveyard_source_ability_is_closed,
)
from ..rules.source_references import SourceReferenceSpec
from . import regex_working_set as re
from .activated_costs import activated_ability_cost
from .dependency_gate import DependencyGate, explicit_capabilities_gate


def loot_transform_or_graveyard_cast(ability, *, text, source_name, public_timing):
    surface = " ".join(text.split()).replace("’", "'")
    if surface.casefold() == (
        "choose target nonland permanent card in your graveyard. if you haven't cast a spell this turn, "
        "you may cast that card. if you do, you can't cast additional spells this turn."
    ):
        from ..rules.graveyard_card_targets import targeted_graveyard_cast_instruction_schema
        from ..targeted_graveyard_cast_model import GRAVEYARD_CAST_MECHANIC, first_paid_graveyard_cast_effect

        cost = activated_ability_cost(ability, public_timing=public_timing)
        if first_paid_graveyard_source_ability_is_closed(ability, cost):
            return ("targeted-first-paid-graveyard-cast-v1", (first_paid_graveyard_cast_effect(),),
                    targeted_graveyard_cast_instruction_schema("you", first_paid=True),
                    (GRAVEYARD_CAST_MECHANIC, "cr-115-targets"))
        return None
    if surface.casefold() == "choose target artifact card in your graveyard. you may cast that card this turn.":
        effect, target = artifact_graveyard_cast_effect(), artifact_graveyard_cast_target()
        cost = activated_ability_cost(ability, public_timing=public_timing)
        if (ability.zones == ("battlefield",) and not ability.mana_ability and not ability.sorcery_speed
                and not ability.activation_conditions
                and artifact_graveyard_cast_dependencies((effect,), target, "battlefield", cost)):
            return ("owned-artifact-graveyard-cast-v1", (effect,), target,
                    (ARTIFACT_GRAVEYARD_CAST_MECHANIC, "cr-115-targets"))
        return None
    if surface.casefold() == (
        "you may cast target instant or sorcery card from your graveyard this turn. "
        "if that spell would be put into your graveyard, exile it instead."
    ):
        effects = (loyalty_graveyard_cast_effect(),)
        targets = loyalty_graveyard_targets()
        required = loyalty_graveyard_cast_dependencies(
            effects, targets, "battlefield" if ability.zones == ("battlefield",) else None,
            activated_ability_cost(ability, public_timing=public_timing),
        )
        if required and not ability.mana_ability:
            return ("targeted-graveyard-loyalty-permission-v1", effects, targets,
                    (LOYALTY_GRAVEYARD_CAST_MECHANIC, "cr-115-targets"))
        return None
    match = re.fullmatch(
        r"Draw a card, then discard a card\. If there are five or more cards in your graveyard, "
        r"exile (?P<source>.+?), then return (?:him|her|it) to the battlefield transformed under "
        r"(?:his|her|its) owner's control\.", surface, re.IGNORECASE,
    )
    if match is None or not source_name or not SourceReferenceSpec(source_name).matches(match["source"]):
        return None
    effects = loot_transform_effects()
    required = loot_transform_dependencies(
        effects, None, "battlefield" if ability.zones == ("battlefield",) else None,
        activated_ability_cost(ability, public_timing=public_timing),
    )
    if not required or ability.mana_ability:
        return None
    return ("loot-graveyard-owner-transform-v1", effects, None, (LOOT_TRANSFORM_MECHANIC,))


def loot_transform_and_graveyard_cast_gate(**scope):
    mechanics = set(scope["mechanics"])
    from ..targeted_graveyard_cast_model import (
        FIRST_PAID_GRAVEYARD_CAST_CAPABILITY, GRAVEYARD_CAST_MECHANIC, first_paid_graveyard_cast,
    )
    if (GRAVEYARD_CAST_MECHANIC in mechanics and len(scope["effects"]) == 1
            and first_paid_graveyard_cast(scope["effects"][0])):
        from ..rules.graveyard_card_targets import targeted_graveyard_cast_node_capabilities

        required = targeted_graveyard_cast_node_capabilities(
            effects=scope["effects"], target_schema=scope["target_schema"], mechanic_ids=mechanics,
        )
        if not required:
            return DependencyGate(blockers=(f"capability:{FIRST_PAID_GRAVEYARD_CAST_CAPABILITY}",))
        return explicit_capabilities_gate(required, capability_registry=scope["capability_registry"],
                                          capability_profile=scope["capability_profile"])
    if LOOT_TRANSFORM_MECHANIC in mechanics:
        resolver, capability = loot_transform_dependencies, LOOT_TRANSFORM_CAPABILITY
    elif ARTIFACT_GRAVEYARD_CAST_MECHANIC in mechanics:
        resolver, capability = artifact_graveyard_cast_dependencies, ARTIFACT_GRAVEYARD_CAST_CAPABILITY
    elif LOYALTY_GRAVEYARD_CAST_MECHANIC in mechanics:
        resolver, capability = loyalty_graveyard_cast_dependencies, LOYALTY_GRAVEYARD_CAST_CAPABILITY
    else:
        return None
    required = resolver(scope["effects"], scope["target_schema"], scope["active_zone"], scope["cost_schema"])
    if not required:
        return DependencyGate(blockers=(f"capability:{capability}",))
    return explicit_capabilities_gate(
        required, capability_registry=scope["capability_registry"], capability_profile=scope["capability_profile"],
    )
