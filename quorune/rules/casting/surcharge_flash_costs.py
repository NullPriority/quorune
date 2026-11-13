"""Couple an intrinsic instant-timing permission to its actual paid branch."""
from copy import deepcopy
from ...cast_timing import SURCHARGE_FLASH_OPTION_FIELD,cast_timing_is_legal
from ...compiled_cast_timing import compiled_mana_surcharge_flash_spec


def mana_surcharge_flash_variants(host,seat,card,options,*,suppress_source_costs,ignore_timing=False):
    if suppress_source_costs:return options
    spec=compiled_mana_surcharge_flash_spec(host,card)
    if spec is None:return options
    record=host.card_record(card);face=host._select_cast_face(record,card.active_face)
    from .proposal import _combined_cast_timing_permissions
    independent=_combined_cast_timing_permissions(host,seat,card,face=face,
        face_name=str(face.get('name') or '') if face else None,face_down=False,include_surcharge=False)
    type_line=str(face.get('type_line') or '') if face else record.type_line
    normal=ignore_timing or cast_timing_is_legal(host.state,seat,type_line,independent)
    result=list(options) if normal else []
    for raw in options:
        option=deepcopy(raw);requirements=host._mana_vector(option['requirements'])
        requirements['GENERIC']+=spec.generic
        option.update(id=str(option['id'])+'+instant-surcharge',kind='optional_additional',
            label=f'Pay {spec.generic} additional mana for instant timing',requirements=requirements,
            **{SURCHARGE_FLASH_OPTION_FIELD:spec.to_dict()})
        result.append(option)
    return result
