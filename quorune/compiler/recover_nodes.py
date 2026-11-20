"""Consume the complete fixed-mana Recover keyword and its exact reminder."""
from . import regex_working_set as re
from ..recover_model import RecoverSpec
from ..fixed_effect_payment import FixedEffectPaymentSpec
from ..replacement.immutable import FrozenMap
from ..util import mana_cost_to_vector


_RECOVER = re.compile(r'Recover (?P<cost>(?:\{(?:0|[1-9][0-9]*|[WUBRGC])\})+)'
    r'(?: \((?P<reminder>[^()]+)\))?\.?', re.I)
_REMINDER = ('When a creature is put into your graveyard from the battlefield, you may pay {cost}. '
    'If you do, return this card from your graveyard to your hand. Otherwise, exile this card.')


def recover_spec_from_line(line):
    from .fractional_life_payment_templates import recover_half_life_price_from_line
    from ..rules.fractional_life_payment import FractionalLifeRecoverSpec
    fraction = recover_half_life_price_from_line(line)
    if fraction is not None:return FractionalLifeRecoverSpec(fraction)
    match = _RECOVER.fullmatch(' '.join(line.strip().split()))
    if match is None:
        return None
    cost = match['cost'].upper()
    if match['reminder'] is not None and match['reminder'].casefold() != _REMINDER.format(cost=cost).casefold():
        return None
    requirements, symbols = mana_cost_to_vector(cost)
    try:
        return RecoverSpec(FixedEffectPaymentSpec('mana', requirements=FrozenMap(requirements)))
    except (TypeError, ValueError):
        return None


def recover_keyword_node(*, node_id, line, material_line, span, capability_registry,
                         capability_profile, residuals, mechanics=()):
    from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
    from ..recover_model import RECOVER_MECHANIC
    from ..rules.fractional_life_payment import FractionalLifeRecoverSpec, FRACTIONAL_RECOVER_MECHANIC
    from ..rules.recover import recover_payment_effect, recover_node_dependencies
    from .fixed_public_event_trigger_bindings import creature_graveyard_owner_spec
    from .dependency_gate import explicit_capabilities_gate
    from .ir_model import OracleNode, append_residual
    spec = recover_spec_from_line(material_line)
    if spec is None or recover_spec_from_line(line) != spec:
        return None
    binding = creature_graveyard_owner_spec(
        'When a creature is put into your graveyard from the battlefield, draw a card.', active_zone='graveyard')
    effect = recover_payment_effect(spec)
    gate = explicit_capabilities_gate(recover_node_dependencies(effect), capability_registry=capability_registry,
                                      capability_profile=capability_profile)
    blocked = (append_residual(residuals, kind='dependency_contract', text=line, span=span,
        reason='Recover requires its typed graveyard event, payment and original-source result owners',
        blockers=gate.blockers),) if gate.blockers else ()
    closure = gate.closure
    fractional = isinstance(spec, FractionalLifeRecoverSpec)
    return OracleNode(node_id=node_id, kind='triggered_ability', text=line, span=span,
        active_zone=binding.active_zone, event=binding.event, event_condition=binding.condition,
        lowerable=True, exact=not blocked, template_id='recover-half-current-life-v2' if fractional else 'recover-fixed-mana-v1', effects=(effect,),
        target_schema=None, runtime_coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE,),
        mechanics=(FRACTIONAL_RECOVER_MECHANIC if fractional else RECOVER_MECHANIC, binding.mechanic, 'cr-603-handling-triggered-abilities'),
        residual_ids=blocked, capability_dependencies=gate.capabilities,
        capability_closure=closure.reachable if closure else (), capability_profile=closure.profile if closure else None,
        capability_fingerprint=closure.fingerprint if closure else None)
