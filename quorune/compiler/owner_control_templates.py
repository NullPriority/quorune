"""One simultaneous resolution instruction restoring all objects to owners."""
from . import regex_working_set as re
from ..owner_control_model import OWNER_CONTROL_MECHANIC,OWNER_CONTROL_OPERATION


def owner_control_effect_template(text):
    match=re.fullmatch(r'Each player gains control of all (?P<scope>creatures|nontoken permanents) they own\.',text.strip(),re.I)
    if match is None:return None
    return ('fixed-owner-control-restoration-v1',({'op':OWNER_CONTROL_OPERATION,'schema_version':1,
        'scope':match['scope'].casefold().replace(' ','_'),'source':'$source'},),None,
        (OWNER_CONTROL_MECHANIC,'cr-611-continuous-effects'))
