"""Lower one exact opponent-caused discard trigger to an existing result owner."""

from . import regex_working_set as re
from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..continuous_conditions import FIXED_PUBLIC_STATE_INTERVENING_COVERAGE
from ..delayed_object_model import DELAYED_OBJECT_MECHANIC
from ..discard_effect_origin import PUBLIC_DISCARD_DESTINATION_SCOPE
from ..rules.opponent_discard_results import (
    OPPONENT_DISCARD_SELF_EVENT, controlled_plains_discard_condition, discard_causer_life_loss_effect,
    discard_delayed_counter_return_effect,
    discard_life_and_delayed_return_effects,
    opponent_discard_group_capabilities, return_discarded_source_effect,
)
from .damage_templates import fixed_damage_effect_template
from .dependency_gate import explicit_capabilities_gate
from .ir_model import OracleNode, append_residual
from .token_templates import fixed_token_creation_effect_template

_SOURCE_DISCARD = re.compile(
    r"^When a spell or ability an opponent controls causes you to discard this card, (?P<body>.+)$",
    re.IGNORECASE,
)
_LIFE_LOSS = re.compile(r"^that player loses (?P<amount>[1-9][0-9]*) life\.$", re.IGNORECASE)
_CONTROLLED_PLAINS = re.compile(r"if you control a Plains, (?P<effect>.+)", re.IGNORECASE)
_LIFE_AND_DELAYED_RETURN = re.compile(
    r"You gain (?P<amount>[1-9][0-9]*) life, and you return this card from your graveyard "
    r"to your hand at the beginning of the next end step\.", re.IGNORECASE,
)


_COUNTER_RETURN = re.compile(
    r"Return this card from your graveyard to the battlefield with a \+1/\+1 counter on it "
    r"at the beginning of the next end step\.", re.IGNORECASE,
)


def opponent_discard_effect(body, *, card_name):
    if _COUNTER_RETURN.fullmatch(body):
        return discard_delayed_counter_return_effect(), None, (DELAYED_OBJECT_MECHANIC, "cr-122-counters")
    if body.casefold() == "return it to your hand.":
        return return_discarded_source_effect(), None, ("return-to-owner-hand",)
    loss = _LIFE_LOSS.fullmatch(body)
    if loss is not None:
        try:
            amount = int(loss["amount"])
        except ValueError:
            return None
        return discard_causer_life_loss_effect(amount), None, ("cr-119-life",)
    token = fixed_token_creation_effect_template(body)
    if token is not None and token.target_schema is None:
        return token.effect, None, token.mechanics
    # An explicit self reference is bound by the discarded-source occurrence.
    damage_body = re.sub(r"^(?:it|this card)(?= deals )", lambda _: card_name, body, flags=re.IGNORECASE)
    damage = fixed_damage_effect_template(damage_body, card_name=card_name)
    if damage is None or damage.target_schema is None:
        return None
    _, (effect,), schema, mechanics = damage.compiled()
    return effect, schema, mechanics


def opponent_discard_node(*, node_id, line, material_line, span, capability_registry,
                          capability_profile, residuals, card_name):
    match = _SOURCE_DISCARD.fullmatch(material_line.strip())
    if match is None:
        return None
    body = match["body"]
    condition = _CONTROLLED_PLAINS.fullmatch(body)
    event_condition = None
    coverage = (CURRENT_ABILITY_FRAGMENT_COVERAGE,)
    if condition is not None:
        body = condition["effect"]
        event_condition = controlled_plains_discard_condition()
        coverage = (*coverage, "intervening_condition", FIXED_PUBLIC_STATE_INTERVENING_COVERAGE)
    future = _LIFE_AND_DELAYED_RETURN.fullmatch(body)
    if future is None:
        result = opponent_discard_effect(body, card_name=card_name)
        if result is None:
            return None
        effect, target_schema, mechanics = result
        effects = (effect,)
    else:
        try:
            amount = int(future["amount"])
        except ValueError:
            return None
        effects = discard_life_and_delayed_return_effects(amount)
        target_schema = None
        mechanics = ("cr-119-life", DELAYED_OBJECT_MECHANIC, "return-to-owner-hand")
    required = opponent_discard_group_capabilities(
        effects, target_schema=target_schema, mechanic_ids=(*mechanics, *coverage),
        event_condition=event_condition,
    )
    if not required:
        return None
    gate = explicit_capabilities_gate(required,
        capability_registry=capability_registry, capability_profile=capability_profile)
    residual_ids = ()
    if gate.blockers:
        residual_ids = (append_residual(
            residuals, kind="dependency_contract", text=line, span=span,
            reason="The source discard needs its reviewed occurrence and result owners", blockers=gate.blockers,
        ),)
    closure = gate.closure
    return OracleNode(
        node_id=node_id, kind="triggered_ability", text=line, span=span,
        active_zone=PUBLIC_DISCARD_DESTINATION_SCOPE, event=OPPONENT_DISCARD_SELF_EVENT,
        lowerable=True, exact=not residual_ids, template_id="opponent-effect-discard-source-v1",
        effects=effects, target_schema=target_schema, mechanics=(*mechanics, "cr-603-handling-triggered-abilities"),
        event_condition=event_condition,
        runtime_coverage=coverage,
        residual_ids=residual_ids, capability_dependencies=gate.capabilities,
        capability_closure=closure.reachable if closure else (),
        capability_profile=closure.profile if closure else None,
        capability_fingerprint=closure.fingerprint if closure else None,
    )
