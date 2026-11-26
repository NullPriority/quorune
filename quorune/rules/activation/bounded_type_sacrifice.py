"""Pay a bounded public sacrifice through the existing simultaneous cost owner."""

from dataclasses import replace

from ...additional_cost_vocabulary import SACRIFICE_ONE_COST
from ...errors import GameRuleError
from ...object_predicate import ObjectQuerySpec
from ...paid_activation_card_types import (
    CONTEXT_FIELD, MECHANIC, RESULT_TEXT, TEMPLATE, exact_paid_type_price, paid_type_capabilities,
    read_paid_type_receipt, uses_paid_type_condition,
)
from ..activation_costs import pay_fixed_zone_change_cost
from ..casting_additional_costs import FixedZoneChangeAdditionalCost, fixed_zone_change_cost_candidates


_COST = FixedZoneChangeAdditionalCost(
    operation=SACRIFICE_ONE_COST, choice_field="sacrifice_cards",
    predicate=ObjectQuerySpec(zones=("battlefield",), controller="$actor", known_to_actor=True),
)


def validate_paid_type_ability(ability):
    from ...compiler.activated_costs import activated_ability_cost

    if (ability.zones != ("battlefield",) or ability.target_schema is not None
            or ability.mana_ability or ability.sorcery_speed or ability.activation_conditions
            or ability.granting_attachment_payment is not None or ability.reconfigure or ability.forecast
            or ability.waterbend is not None or ability.generic_reduction_per_legendary_creature
            or ability.effect_text != RESULT_TEXT
            or not exact_paid_type_price(activated_ability_cost(ability))):
        raise ValueError("Paid card types require the exact battlefield price and result")


def paid_type_runtime_dependencies(program):
    from ...semantic_runtime.activated_abilities import ActivatedAbilityCatalogHandler

    if not (MECHANIC in program.coverage or program.provenance.get("template_id") == TEMPLATE
            or any(uses_paid_type_condition(effect) for effect in program.effects)):
        return ()
    required = paid_type_capabilities(
        effects=program.effects, target_schema=program.target_schema,
        mechanic_ids=program.coverage, cost_schema=program.cost_schema,
    )
    if (not required or program.event != "activate" or program.active_zone != "battlefield"
            or program.event_condition is not None or program.provenance.get("granted_only") is True
            or len(program.handlers) != 1):
        raise ValueError("Paid card types require an isolated reviewed activation")
    ability = ActivatedAbilityCatalogHandler().validate(program.handlers[0])
    validate_paid_type_ability(ability)
    if ability.builtin_semantic_key != program.key:
        raise ValueError("Paid card types do not match the activation catalog")
    return required


def bounded_sacrifice_candidates(host, actor):
    return fixed_zone_change_cost_candidates(host, actor=actor, cost=_COST)


def bounded_sacrifice_choice(host, actor):
    candidates = bounded_sacrifice_candidates(host, actor)
    maximum = min(3, len(candidates))
    return {"cost_cards": {
        "type": "array", "label": "Sacrifice up to three permanents you control",
        "legal_refs": list(candidates), "minimum": 0, "maximum": maximum,
        "allowed_counts": list(range(maximum + 1)),
    }}


def pay_bounded_type_sacrifice(host, proposal, source, ability, response):
    validate_paid_type_ability(ability)
    program = host.semantics.get(proposal.semantic_key)
    required = paid_type_runtime_dependencies(program) if program is not None else ()
    if not required or not set(required) <= set(program.capability_dependencies):
        raise GameRuleError("Paid card types lack their reviewed current price and result")
    if "cost_cards" in response and "cost_objects" in response:
        raise GameRuleError("Use only one bounded sacrifice selection field")
    refs = response.get("cost_cards", response.get("cost_objects", ()))
    candidates = bounded_sacrifice_candidates(host, proposal.seat)
    if (not isinstance(refs, (list, tuple)) or len(refs) > 3
            or any(type(ref) is not str for ref in refs) or len(set(refs)) != len(refs)
            or any(ref not in candidates for ref in refs)):
        raise GameRuleError("Sacrifice up to three distinct legal permanents you control")
    cards = tuple(host._resolve_object(proposal.seat, ref, zones={"battlefield"}, controlled_only=True)
                  for ref in refs)
    card_types = set()
    for card in cards:
        type_line = str(host._effective_card_data(card).get("type_line") or "")
        card_types.update(host._type_parts(type_line)[0])
    receipt = read_paid_type_receipt({
        "version": 1, "ability_key": program.key, "source": source.logical_object_id,
        "payer": proposal.seat, "objects": [card.object_id for card in cards], "types": sorted(card_types),
    })
    paid = pay_fixed_zone_change_cost(
        host, actor=proposal.seat, cost=replace(_COST, count=len(refs)), refs=refs,
        response=response, reason="activated ability cost",
    ) if refs else []
    if paid != receipt["objects"]:
        raise GameRuleError("Paid card types do not match the committed sacrifice")
    return paid, [], {}, (CONTEXT_FIELD, receipt)
