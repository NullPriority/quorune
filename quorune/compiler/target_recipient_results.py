"""Fixed removal followups addressed to the same target's player information."""
from dataclasses import dataclass
import re

from ..target_controller_information import (
    TARGET_CONTROLLER_INFORMATION_MECHANIC, TARGET_OWNER_INFORMATION_MECHANIC,
    target_controller_reference, target_owner_reference,
)
from .bound_effect_programs import BoundEffectProgramTemplate, _program, _target_type_is_guaranteed
from .closed_effect_programs import _sentence_partition

TARGET_RECIPIENT_RESULT_CAPABILITY = 'resolution.effect_program.target_recipient_result'
TARGET_RECIPIENT_RESULT_MECHANIC = 'fixed-target-recipient-result'
TARGET_RECIPIENT_RESULT_TEMPLATE = 'bound-target-recipient-result-v1'
_TAIL = re.compile(
    r"(?:Then )?(?:(?:Its|That (?P<kind>permanent|artifact|battle|creature|enchantment|land|planeswalker)['’]s) "
    r"(?P<relation>controller|owner)|(?P<owner_player>That player)) "
    r"(?P<verb>creates|discards|gets|gains|loses|draws) (?P<body>.+)\.", re.I,
)


@dataclass(frozen=True, slots=True)
class TargetRecipientResultTemplate(BoundEffectProgramTemplate):
    @property
    def template_id(self): return TARGET_RECIPIENT_RESULT_TEMPLATE


def target_recipient_result_template(text, *, compile_component):
    clauses = _sentence_partition(text)
    if clauses is None or len(clauses) not in {2,3}: return None
    tail = _TAIL.fullmatch(clauses[-1])
    if tail is None: return None
    prefix = compile_component(' '.join(clauses[:-1]))
    schema = prefix[2]
    if (prefix[0] is None or schema is None or schema.get('count') != 1
            or type(schema.get('count')) is not int or schema.get('up_to') or len(prefix[1]) != 1): return None
    from ..rules.target_recipient_results import target_recipient_prefix_is_closed, bind_fixed_recipient_result
    relation = 'owner' if tail['owner_player'] else tail['relation'].casefold()
    if tail['owner_player'] and prefix[1][0].get('op')!='bounce':return None
    if not target_recipient_prefix_is_closed(prefix[1][0],schema,relation): return None
    if tail['kind'] and not _target_type_is_guaranteed(schema,tail['kind'].casefold()): return None
    verb = tail['verb'].casefold().removesuffix('s')
    targeted = verb in {'discard','get'}
    subject = 'Target player ' + tail['verb'] if targeted else 'Create' if verb=='create' else 'You ' + verb
    result = compile_component(subject + ' ' + tail['body'] + '.')
    if result[0] is None or len(result[1]) != 1: return None
    recipient = target_controller_reference() if relation == 'controller' else target_owner_reference()
    bound = bind_fixed_recipient_result(result[1][0],result[2],recipient)
    if bound is None: return None
    information = TARGET_CONTROLLER_INFORMATION_MECHANIC if relation == 'controller' else TARGET_OWNER_INFORMATION_MECHANIC
    component = (result[0],(bound,),None,(*result[3],information,TARGET_RECIPIENT_RESULT_MECHANIC))
    program = _program((prefix,component),schema=schema)
    if program is None:return None
    return TargetRecipientResultTemplate(program.component_template_ids,program.component_effect_counts,
        program._effects,program._target_schema,program.mechanic_ids)
