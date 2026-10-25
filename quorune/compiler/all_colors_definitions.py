from __future__ import annotations

"""Source-spanned unconditional self color definition."""

import re
from ..all_colors import ALL_COLORS_CAPABILITY,ALL_COLORS_HANDLER,AllColorsCharacteristicDefinitionSpec
from ..ability_fragments import ability_fragment_to_dict
from ..rules.source_references import SourceReferenceSpec


def all_colors_definition_handler(text,*,source_name):
    subject=SourceReferenceSpec(source_name).regex_pattern
    if re.fullmatch(rf'(?:This creature|This permanent|{subject}) is all colors\.?',text.strip(),re.I) is None:return None
    descriptor={'handler_id':ALL_COLORS_HANDLER,'schema_version':1,'event':'continuous',
        'fragment':ability_fragment_to_dict(AllColorsCharacteristicDefinitionSpec())}
    return 'source-all-colors-characteristic-definition-v1',descriptor,(ALL_COLORS_CAPABILITY,)
