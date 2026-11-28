from __future__ import annotations

"""Source-spanned fixed-mana casting authority from a transforming front face."""

from . import regex_working_set as re
from ..card_programs.admission import REQUIRES_COMPLETE_CARD_PROGRAM_FIELD
from ..disturb_model import DISTURB_CAPABILITY, DISTURB_HANDLER, FixedManaDisturbSpec
from ..util import ORDINARY_FIXED_MANA_PATTERN, mana_cost_to_vector
from .ir_model import OracleNode, append_residual
from .static_runtime_nodes import runtime_handler_node


_DISTURB_PREFIX = re.compile(r"^Disturb\b", re.I)
_CONVERTED_CAST_PREFIX = re.compile(r"^More Than Meets the Eye\b", re.I)
_DISTURB_COST = re.compile(rf"^Disturb (?P<cost>{ORDINARY_FIXED_MANA_PATTERN})(?:\s+\([^()]*\))?\.?$", re.I)
_CONVERTED_CAST_COST = re.compile(
    rf"^More Than Meets the Eye (?P<cost>{ORDINARY_FIXED_MANA_PATTERN})(?:\s+\([^()]*\))?\.?$", re.I,
)


def printed_disturb_spec(record):
    return _printed_fixed_face_pair_spec(record, _DISTURB_PREFIX, _DISTURB_COST, "disturb")


def printed_more_than_meets_the_eye_spec(record):
    return _printed_fixed_face_pair_spec(
        record, _CONVERTED_CAST_PREFIX, _CONVERTED_CAST_COST, "more-than-meets-the-eye",
    )


def _printed_fixed_face_pair_spec(record, prefix, cost_pattern, keyword_id):
    if record.layout != 'transform' or len(record.faces) != 2:
        return None
    front, back = record.faces
    lines = [(i, line.strip()) for i, line in enumerate(front.get('oracle_text', '').splitlines())
        if prefix.match(line.strip())]
    if len(lines) != 1 or any(prefix.match(line.strip())
            for line in back.get('oracle_text', '').splitlines()):
        return None
    index, line = lines[0]
    match = cost_pattern.fullmatch(line)
    if match is None:
        return None
    mana, complex_symbols = mana_cost_to_vector(match['cost'].upper())
    if complex_symbols:
        return None
    return FixedManaDisturbSpec(f'front:{keyword_id}:line:{index + 1}', str(front['name']), str(back['name']), mana)


def disturb_static_node(*, record, face_name, node_id, line, material_line, span,
        capability_registry, capability_profile, residuals):
    if _DISTURB_PREFIX.match(material_line) is None:
        return None
    return _fixed_face_pair_cast_node(
        spec=printed_disturb_spec(record), keyword_name="Disturb", handler=DISTURB_HANDLER,
        field="disturb", capability=DISTURB_CAPABILITY, template="fixed-mana-disturb-v1",
        face_name=face_name, node_id=node_id, line=line, span=span, capability_registry=capability_registry,
        capability_profile=capability_profile, residuals=residuals,
    )


def fixed_face_pair_cast_static_node(**values):
    material = values["material_line"]
    if _CONVERTED_CAST_PREFIX.match(material) is None:
        return disturb_static_node(**values)
    from ..disturb_model import MORE_THAN_MEETS_THE_EYE_CAPABILITY, MORE_THAN_MEETS_THE_EYE_HANDLER

    record = values["record"]
    return _fixed_face_pair_cast_node(
        spec=printed_more_than_meets_the_eye_spec(record), keyword_name="More Than Meets the Eye",
        handler=MORE_THAN_MEETS_THE_EYE_HANDLER, field="converted_cast",
        capability=MORE_THAN_MEETS_THE_EYE_CAPABILITY, template="fixed-mana-more-than-meets-the-eye-v1",
        **{key: value for key, value in values.items() if key not in {"record", "material_line"}},
    )


def _fixed_face_pair_cast_node(*, spec, keyword_name, handler, field, capability, template, face_name,
        node_id, line, span, capability_registry, capability_profile, residuals):
    if spec is None or face_name != spec.front_face:
        rid = append_residual(residuals, kind='keyword_grammar', text=line, span=span,
            reason=f'{keyword_name} requires one fixed ordinary mana front-face declaration on a transforming card',
            blockers=('complex costs, repeated declarations and other source faces',))
        return OracleNode(node_id=node_id, kind='keyword_ability', text=line, span=span, active_zone='all',
            event='unresolved', lowerable=False, exact=False, residual_ids=(rid,))
    descriptor = {'handler_id': handler, 'schema_version': 1, 'event': 'cast.cost',
        REQUIRES_COMPLETE_CARD_PROGRAM_FIELD: True, field: spec.to_dict()}
    return runtime_handler_node(node_id=node_id, line=line, span=span,
        compiled=(template, descriptor, (capability,)), kind='keyword_ability',
        event='cast.cost', active_zone='all', dependency_reason=f'{keyword_name} requires complete front/back authority',
        capability_registry=capability_registry, capability_profile=capability_profile, residuals=residuals)
