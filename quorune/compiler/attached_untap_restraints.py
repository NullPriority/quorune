from __future__ import annotations

"""Complete attached characteristic and untap-rule clauses."""

from . import regex_working_set as re
from ..ability_fragments import ability_fragment_to_dict,CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..attachment_references import AttachmentReferenceKind
from ..counter_permission import (
    ATTACHED_COUNTER_PROHIBITION_DEPENDENCIES,
    COUNTER_PROHIBITION_EVENT,
    FixedCounterProhibitionSpec,
)
from .counter_permission_templates import counter_prohibition_descriptor
from ..untap_restraint_model import AttachedUntapRestraintSpec,UNTAP_RESTRAINT_CAPABILITY,UNTAP_RESTRAINT_HANDLER
from .continuous_templates import attached_fixed_characteristics_handler
from .fixed_characteristic_settings import fixed_characteristic_setting_handler
from .static_runtime_nodes import runtime_handler_node


_ATTACHED_UNTAP_AND_COUNTER_PROHIBITION = re.compile(
    r"Enchanted creature can['’]t become untapped and can['’]t have counters put on it\.?", re.I,
)


def attached_counter_prohibition_nodes(*, source_attachment_relation, **values):
    """Keep both static rules of the complete Aura clause on their own events."""
    text = ' '.join(values['material_line'].split())
    if (source_attachment_relation is not AttachmentReferenceKind.ENCHANTED
            or _ATTACHED_UNTAP_AND_COUNTER_PROHIBITION.fullmatch(text) is None):
        return None
    untap = attached_untap_restraint_node(**{**values, 'material_line': "Enchanted creature can't become untapped."})
    counters = runtime_handler_node(
        node_id=f"{values['node_id']}:attached-counter-prohibition", line=values['line'], span=values['span'],
        compiled=('attached-counter-prohibition-v1',
            counter_prohibition_descriptor(FixedCounterProhibitionSpec('permanent', 'attached', types_any=('creature',))),
            ATTACHED_COUNTER_PROHIBITION_DEPENDENCIES),
        kind='static_ability', event=COUNTER_PROHIBITION_EVENT,
        runtime_coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE,),
        dependency_reason='Attached counter prohibition requires current source attachment and permission owners',
        capability_registry=values['capability_registry'], capability_profile=values['capability_profile'],
        residuals=values['residuals'],
    )
    return untap, counters


def attached_untap_restraint_node(**values):
    text = ' '.join(values['material_line'].split())
    match = re.fullmatch(
        r"(?P<subject>(?:Enchanted|Equipped) creature) (?P<modifier>gets [+-]\d+/[+-]\d+) and "
        r"doesn['’]t untap during its controller['’]s untap step\.?", text, re.I,
    )
    if match is not None:
        mode = 'controller_step'
        compile_modifier = attached_fixed_characteristics_handler
    else:
        match = re.fullmatch(
            r"(?P<subject>Enchanted (?:artifact|battle|creature|enchantment|land|permanent|planeswalker)) "
            r"(?:(?P<modifier>loses all abilities) and )?(?P<rule>can['’]t become untapped|"
            r"doesn['’]t untap during its controller['’]s untap step)\.?", text, re.I,
        )
        if match is None:
            return None
        mode = 'all_untaps' if match['rule'].casefold().startswith(("can't", 'can’t')) else 'controller_step'
        if mode == 'controller_step' and match['modifier'] is None:
            return None
        compile_modifier = fixed_characteristic_setting_handler
    kind=match['subject'].split()[-1].casefold()
    spec=AttachedUntapRestraintSpec(mode,() if kind=='permanent' else (kind,))
    descriptor={'handler_id':UNTAP_RESTRAINT_HANDLER,'schema_version':1,'event':'characteristics.evaluate',
        'fragment':ability_fragment_to_dict(spec)}
    handlers=[];required={UNTAP_RESTRAINT_CAPABILITY,'untap.step.static_participation'}
    if match['modifier'] is not None:
        settings = compile_modifier(f"{match['subject']} {match['modifier']}.", source_name=values['source_name'])
        if settings is None:return None
        handlers.append(settings[1]);required.update(settings[2])
    handlers.append(descriptor)
    return runtime_handler_node(node_id=values['node_id'],line=values['line'],span=values['span'],
        compiled=('attached-static-untap-restraint-v1',tuple(handlers),tuple(sorted(required))),
        kind='static_ability',event='characteristics.evaluate',runtime_coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE,),
        dependency_reason='Attached untap restraints require their rule and characteristic owners',
        capability_registry=values['capability_registry'],capability_profile=values['capability_profile'],residuals=values['residuals'])
