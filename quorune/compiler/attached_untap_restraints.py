from __future__ import annotations

"""Complete attached characteristic and untap-rule clauses."""

import re
from ..ability_fragments import ability_fragment_to_dict,CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..untap_restraint_model import AttachedUntapRestraintSpec,UNTAP_RESTRAINT_CAPABILITY,UNTAP_RESTRAINT_HANDLER
from .continuous_templates import attached_fixed_characteristics_handler
from .fixed_characteristic_settings import fixed_characteristic_setting_handler
from .static_runtime_nodes import runtime_handler_node


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
