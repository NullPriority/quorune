"""One simultaneous instruction with independently constrained target roles."""
from copy import deepcopy
from . import regex_working_set as re

from .fixed_homogeneous_target_sets import _compiled_singular, _OPERATION_LIFT

COMPOUND_TARGET_SET_MECHANIC = 'fixed-compound-target-set'
COMPOUND_TARGET_SET_CAPABILITY = 'resolution.effect.fixed_compound_target_set'
_OPERATIONS = {'destroy','exile_permanent','bounce'}


def compound_target_set_effect_template(text):
    material = ' '.join(text.strip().split())
    if material.casefold() == 'destroy up to one target planeswalker and up to one target artifact.':
        from ..rules.compound_target_sets import OPTIONAL_DESTROY_ROLES_VERSION_FIELD, optional_destroy_roles_schema

        effect = {'op': 'destroy_targets', 'cards': '$targets', 'maximum_targets': 2,
                  'target_roles': True, OPTIONAL_DESTROY_ROLES_VERSION_FIELD: 1}
        return ('fixed-compound-destroy-optional-roles-v1', (effect,), optional_destroy_roles_schema(),
                ('destroy', COMPOUND_TARGET_SET_MECHANIC, 'cr-115-targets'))
    prohibited = False
    no_regen = re.fullmatch(r"(.+?\.) (?:They|Those permanents) can['’]t be regenerated\.",material,re.I)
    if no_regen:
        material = no_regen[1];prohibited = True
    match = re.fullmatch(r'(Destroy|Exile|Return) (.+?)\.?',material,re.I)
    if match is None:return None
    verb, body = match[1].casefold(), match[2]
    if prohibited and verb != 'destroy':return None
    suffix = ''
    if verb == 'return':
        tail = re.fullmatch(r"(.+?) to their owners['’] hands",body,re.I)
        if tail is None:return None
        body = tail[1];suffix = " to its owner's hand"
    parts = re.split(r',\s*(?:and\s+)?(?=target\b)|\s+and\s+(?=target\b)',body,flags=re.I)
    if not 2 <= len(parts) <= 4 or any(not p.casefold().startswith('target ') for p in parts):return None
    children = [_compiled_singular(verb,verb+' '+part+suffix+'.') for part in parts]
    if any(c is None or len(c[1]) != 1 for c in children):return None
    effects = [c[1][0] for c in children]
    operation = effects[0].get('op')
    if operation not in _OPERATIONS or any(e != {'op':operation,'card':'$target.0'} for e in effects):return None
    groups = []
    for index, child in enumerate(children):
        schema = deepcopy(dict(child[2]))
        if schema.get('count') != 1 or schema.get('zones') != ['battlefield']:return None
        schema['id'] = 'role_'+str(index);groups.append(schema)
    mechanics = tuple(dict.fromkeys(m for child in children for m in child[3]))
    effect = {'op':_OPERATION_LIFT[operation],'cards':'$targets','maximum_targets':len(groups),'target_roles':True}
    if prohibited:effect['regeneration_prohibited'] = True
    return ('fixed-compound-'+operation+'-v1',(effect,),{'groups':groups},
        tuple(dict.fromkeys((*mechanics,COMPOUND_TARGET_SET_MECHANIC,'cr-115-targets',*(('regeneration-prohibition',) if prohibited else ())))))
