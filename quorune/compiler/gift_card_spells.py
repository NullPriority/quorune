"""Source-spanned lowering of closed optional Gift spell compositions."""

from ..characteristic_evaluation import type_parts
from ..gift_card_model import GIFT_CARD_CAPABILITY, GIFT_CARD_COST_FIELD, GIFT_CARD_PRICE, GIFT_CARD_TEMPLATE
from ..linked_exile_return_model import LINKED_EXILE_RETURN_MECHANIC
from ..rules.all_player_turn_rules import CONTROLLER_TURN_HEXPROOF_CAPABILITY
from ..rules.fixed_resolution_characteristic_shapes import FIXED_RESOLUTION_CHARACTERISTICS_CAPABILITY
from ..rules.gift_card_spells import gift_protection_effects
from ..rules.gift_counter_spells import (
    GIFT_COUNTER_DEPENDENCIES, GIFT_COUNTER_TEMPLATE, gift_counter_cost_schema,
    gift_counter_effect, gift_counter_target_schema,
)
from ..rules.gift_fish_spells import (
    GIFT_FISH_COUNTER_RETURN_DEPENDENCIES, GIFT_FISH_COUNTER_RETURN_TEMPLATE, GIFT_FISH_COUNTER_RETURN_VERSION,
    GIFT_FISH_DEPENDENCIES, GIFT_FISH_TEMPLATE, gift_bounce_effect, gift_fish_cost_schema,
    gift_fish_counter_return_effects, gift_fish_target_schema,
)
from .dependency_gate import explicit_capabilities_gate
from .ir_model import OracleFaceIR, OracleNode, SourceSpan, append_residual


_GIFT_LINE = "gift a card"
_PROTECTION_LINE = (
    "you and permanents you control gain hexproof until end of turn. "
    "if the gift was promised, permanents you control also gain indestructible until end of turn."
)


_FISH_LINE = "gift a tapped fish"
_BOUNCE_LINE = (
    "return target creature an opponent controls to its owner's hand. "
    "if the gift was promised, instead return target nonland permanent an opponent controls to its owner's hand."
)


_COUNTER_RETURN_LINE = (
    "exile target nontoken creature. if the gift wasn't promised, return that card to the battlefield "
    "under its owner's control with a +1/+1 counter on it at the beginning of the next end step."
)


_COUNTER_LINE = "counter target creature spell. if the gift was promised, instead counter target spell."


_GRAVEYARD_LIMITS_LINE = (
    "return up to two target creature cards each with mana value 2 or less from your graveyard to the battlefield. "
    "if the gift was promised, instead return up to three target creature cards each with mana value 2 or less "
    "from your graveyard to the battlefield."
)


_RETURN_COPY_LINE = (
    "return target creature card from your graveyard to the battlefield. then if the gift was promised "
    "and that creature isn't legendary, create a token that's a copy of that creature, except it's 1/1."
)


_SWEEP_RETURN_LINE = (
    "destroy all creatures. if the gift was promised, return a creature card put into your graveyard "
    "this way to the battlefield under your control."
)


_GREED_LINE = (
    "target opponent sacrifices a creature with the greatest power among creatures they control. "
    "if the gift was promised, return target creature card from your graveyard to your hand."
)


def _gift_spell_form(keyword, body):
    if keyword == _GIFT_LINE and body == _GREED_LINE:
        from ..rules import gift_graveyard_spells as graveyard

        return (graveyard.GREED_TEMPLATE, graveyard.greed_effects(), graveyard.greed_target_schema(False),
                graveyard.greed_cost_schema(), ("cr-115-targets", "fixed-affected-player-sacrifice", "return-to-owner-hand"),
                graveyard.greed_capabilities())
    if keyword == _GIFT_LINE and body == _SWEEP_RETURN_LINE:
        from ..rules import gift_graveyard_spells as graveyard

        return (graveyard.SWEEP_RETURN_TEMPLATE, (graveyard.sweep_return_base_effect(),), None,
                graveyard.sweep_return_cost_schema(), ("destroy",), graveyard.SWEEP_RETURN_DEPENDENCIES)
    if keyword == _GIFT_LINE and body == _RETURN_COPY_LINE:
        from ..rules import gift_graveyard_spells as graveyard

        return (graveyard.RETURN_COPY_TEMPLATE, (graveyard.return_copy_base_effect(),), graveyard.return_copy_target_schema(),
                graveyard.return_copy_cost_schema(), ("cr-115-targets", "fixed-target-reanimation"), graveyard.RETURN_COPY_DEPENDENCIES)
    if keyword == _GIFT_LINE and body == _GRAVEYARD_LIMITS_LINE:
        from ..rules import gift_graveyard_spells as graveyard

        return (graveyard.TEMPLATE, graveyard.effects(), graveyard.target_schema(False), graveyard.cost_schema(),
                ("cr-115-targets", "fixed-target-reanimation", "fixed-target-reanimation-set"), graveyard.DEPENDENCIES)
    if keyword == _GIFT_LINE and body == _COUNTER_LINE:
        return (
            GIFT_COUNTER_TEMPLATE, (gift_counter_effect(),), gift_counter_target_schema(False), gift_counter_cost_schema(),
            ("counter", "cr-115-targets"), GIFT_COUNTER_DEPENDENCIES,
        )
    if keyword == _GIFT_LINE and body == _PROTECTION_LINE:
        return (
            GIFT_CARD_TEMPLATE, gift_protection_effects(), None, {GIFT_CARD_COST_FIELD: dict(GIFT_CARD_PRICE)},
            ("cr-611-continuous-effects", "hexproof"),
            (GIFT_CARD_CAPABILITY, CONTROLLER_TURN_HEXPROOF_CAPABILITY, FIXED_RESOLUTION_CHARACTERISTICS_CAPABILITY,
             "target.protection.hexproof_permanent"),
        )
    if keyword == _FISH_LINE and body == _BOUNCE_LINE:
        return (
            GIFT_FISH_TEMPLATE, (gift_bounce_effect(),), gift_fish_target_schema(False), gift_fish_cost_schema(),
            ("cr-115-targets", "return-to-hand"), GIFT_FISH_DEPENDENCIES,
        )
    if keyword == _FISH_LINE and body == _COUNTER_RETURN_LINE:
        version = GIFT_FISH_COUNTER_RETURN_VERSION
        return (
            GIFT_FISH_COUNTER_RETURN_TEMPLATE, gift_fish_counter_return_effects(),
            gift_fish_target_schema(False, version=version), gift_fish_cost_schema(version=version),
            ("cr-115-targets", "cr-122-counters", LINKED_EXILE_RETURN_MECHANIC),
            GIFT_FISH_COUNTER_RETURN_DEPENDENCIES,
        )
    return None


def compile_gift_card_face(*, record, face_id, face_name, oracle_text, material_rows,
                           capability_registry, capability_profile, residuals):
    from .gift_extra_turn_spells import compile_gift_extra_turn_face

    extra_turn = compile_gift_extra_turn_face(
        record=record, face_id=face_id, face_name=face_name, oracle_text=oracle_text, material_rows=material_rows,
        capability_registry=capability_registry, capability_profile=capability_profile, residuals=residuals,
    )
    if extra_turn is not None:
        return extra_turn
    if record.layout != "normal" or record.faces or len(material_rows) != 2:
        return None
    types, _, _ = type_parts(record.type_line)
    if not {"instant", "sorcery"}.intersection(types):
        return None
    first, second = material_rows
    keyword = " ".join(first[1].casefold().split()).removesuffix(".")
    body = " ".join(second[1].casefold().replace("’", "'").split())
    form = _gift_spell_form(keyword, body)
    if form is None:
        return None
    template, effects, targets, cost, mechanics, dependencies = form
    gate = explicit_capabilities_gate(
        dependencies, capability_registry=capability_registry, capability_profile=capability_profile,
    )
    span = SourceSpan(first[2].start, second[2].end, first[2].line)
    residual_ids = (
        (append_residual(residuals, kind="dependency_contract", text=oracle_text, span=span,
                         reason="The Gift spell requires current declaration and complete resolution authority",
                         blockers=gate.blockers),)
        if gate.blockers else ()
    )
    node = OracleNode(
        node_id=f"{face_id}:n1", kind="spell_ability", text=oracle_text, span=span,
        active_zone="stack", event="resolve", lowerable=True, exact=not residual_ids,
        template_id=template, effects=effects, target_schema=targets, mechanics=mechanics, cost=cost,
        residual_ids=residual_ids, capability_dependencies=gate.capabilities,
        capability_closure=gate.closure.reachable if gate.closure else (),
        capability_profile=gate.closure.profile if gate.closure else None,
        capability_fingerprint=gate.closure.fingerprint if gate.closure else None,
    )
    return OracleFaceIR(
        face_id=face_id, face_name=face_name, oracle_text=oracle_text,
        nodes=(node,), residuals=tuple(residuals),
    )
