"""Whole fixed single-player library-shuffle clauses."""
from . import regex_working_set as re
from ..library_order_model import SHUFFLE_OPERATION
from ..rules.target_library_shuffle import TARGET_SHUFFLE_FIELD, TARGET_SHUFFLE_MECHANIC, target_shuffle_schema


def fixed_target_library_shuffle_template(text):
    if re.fullmatch(r'Target player shuffles(?: their library)?\.',text.strip(),re.I) is None:return None
    return ('fixed-target-player-library-shuffle-v1',
        ({'op':SHUFFLE_OPERATION,'player':'$target.0',TARGET_SHUFFLE_FIELD:1},),
        target_shuffle_schema(),(TARGET_SHUFFLE_MECHANIC,'cr-115-targets'))
