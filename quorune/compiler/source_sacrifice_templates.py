"""Close one explicit source-self sacrifice without choosing a recipient."""
from . import regex_working_set as re

from ..rules.source_references import SourceReferenceSpec, source_self_permanent_type
from ..source_sacrifice_model import SOURCE_SACRIFICE_MECHANIC, fixed_source_sacrifice_effect


def fixed_source_sacrifice_template(text, *, source_name, source_is_permanent):
    if source_is_permanent is not True:
        return None
    match = re.fullmatch(r'Sacrifice (?P<subject>.+?)\.?', ' '.join(text.split()), re.I)
    if match is None:
        return None
    subject = match['subject']
    if source_self_permanent_type(subject) is None and not SourceReferenceSpec(source_name).matches(subject):
        return None
    return ('fixed-source-sacrifice-v1', (fixed_source_sacrifice_effect(),), None,
            (SOURCE_SACRIFICE_MECHANIC, 'sacrifice'))
