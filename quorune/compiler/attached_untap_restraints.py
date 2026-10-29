from __future__ import annotations

"""Complete attached ability-removal and untap-rule clauses."""

import re
from ..ability_fragments import ability_fragment_to_dict,CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..untap_restraint_model import AttachedUntapRestraintSpec,UNTAP_RESTRAINT_CAPABILITY,UNTAP_RESTRAINT_HANDLER
from .fixed_characteristic_settings import fixed_characteristic_setting_handler
from .static_runtime_nodes import runtime_handler_node


def attached_untap_restraint_node(**values):
    match=re.fullmatch(r'(?P<subject>Enchanted (?:artifact|battle|creature|enchantment|land|permanent|planeswalker)) '
        r'(?:(?P<remove>loses all abilities) and )?(?P<rule>can[\'’]t become untapped|doesn[\'’]t untap during its controller[\'’]s untap step)\.?',
        ' '.join(values['material_line'].strip().split()),re.I)
    if match is None:return None
    mode='all_untaps' if match['rule'].casefold().startswith(('can\'t','can’t')) else 'controller_step'
    if mode=='controller_step' and match['remove'] is None:return None
    kind=match['subject'].split()[-1].casefold()
    spec=AttachedUntapRestraintSpec(mode,() if kind=='permanent' else (kind,))
    descriptor={'handler_id':UNTAP_RESTRAINT_HANDLER,'schema_version':1,'event':'characteristics.evaluate',
        'fragment':ability_fragment_to_dict(spec)}
    handlers=[];required={UNTAP_RESTRAINT_CAPABILITY,'untap.step.static_participation'}
    if match['remove'] is not None:
        settings=fixed_characteristic_setting_handler(match['subject']+' loses all abilities.',source_name=values['source_name'])
        if settings is None:return None
        handlers.append(settings[1]);required.update(settings[2])
    handlers.append(descriptor)
    return runtime_handler_node(node_id=values['node_id'],line=values['line'],span=values['span'],
        compiled=('attached-static-untap-restraint-v1',tuple(handlers),tuple(sorted(required))),
        kind='static_ability',event='characteristics.evaluate',runtime_coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE,),
        dependency_reason='Attached untap restraints require their rule and characteristic owners',
        capability_registry=values['capability_registry'],capability_profile=values['capability_profile'],residuals=values['residuals'])
