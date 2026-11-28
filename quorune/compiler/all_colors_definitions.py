from __future__ import annotations

"""Source-spanned all-zone color and land subtype definitions."""

from . import regex_working_set as re
from ..all_colors import ALL_COLORS_CAPABILITY,ALL_COLORS_HANDLER,AllColorsCharacteristicDefinitionSpec
from ..ability_fragments import ability_fragment_to_dict
from ..rules.source_references import SourceReferenceSpec


def all_colors_definition_handler(text,*,source_name):
    subject=SourceReferenceSpec(source_name).regex_pattern
    if re.fullmatch(
        rf"(?:This creature|This permanent|{subject}) is all colors"
        r"(?:\. This ability doesn['’]t affect its color identity)?\.?",
        text.strip(), re.I,
    ) is None:
        return None
    descriptor={'handler_id':ALL_COLORS_HANDLER,'schema_version':1,'event':'continuous',
        'fragment':ability_fragment_to_dict(AllColorsCharacteristicDefinitionSpec())}
    return 'source-all-colors-characteristic-definition-v1',descriptor,(ALL_COLORS_CAPABILITY,)


def all_nonbasic_land_types_handler(text):
    from ..basic_land_types import (
        ALL_NONBASIC_LAND_TYPES_CAPABILITY, ALL_NONBASIC_LAND_TYPES_HANDLER, AllNonbasicLandTypesDefinitionSpec,
    )

    if text.strip().casefold() != "this land is every nonbasic land type.":
        return None
    descriptor = {"handler_id": ALL_NONBASIC_LAND_TYPES_HANDLER, "schema_version": 1, "event": "continuous",
                  "fragment": ability_fragment_to_dict(AllNonbasicLandTypesDefinitionSpec())}
    return "source-all-nonbasic-land-types-v1", descriptor, (ALL_NONBASIC_LAND_TYPES_CAPABILITY,)
