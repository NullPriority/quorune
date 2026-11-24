"""The complete printed temporary basic-land-type choice instruction."""
from . import regex_working_set as re
from ..target_land_type_model import (
    TARGET_LAND_TYPE_OPERATION,TARGET_LAND_TYPE_MECHANIC,target_land_type_schema,
    FIXED_TARGET_LAND_TYPE_MECHANIC,FixedTargetLandTypeSpec,
)


def target_land_type_choice_template(text):
    material = ' '.join(text.strip().split())
    pair = re.fullmatch(
        r'Target land becomes (?P<a>a|an) (?P<first>Plains|Island|Swamp|Mountain|Forest) or '
        r'(?P<b>a|an) (?P<second>Plains|Island|Swamp|Mountain|Forest) until end of turn[.]?',
        material, re.I,
    )
    if pair is not None:
        choices = (pair['first'].title(), pair['second'].title())
        articles = tuple('an' if name == 'Island' else 'a' for name in choices)
        if choices[0] == choices[1] or (pair['a'].casefold(), pair['b'].casefold()) != articles:
            return None
        return ('target-basic-land-type-choice-v3', ({
            'op':TARGET_LAND_TYPE_OPERATION, 'schema_version':3,
            'card':'$target.0', 'player':'$controller', 'choices':list(choices),
        },), target_land_type_schema(), (TARGET_LAND_TYPE_MECHANIC,'cr-115-targets'))
    match=re.fullmatch(r'Target land(?P<controlled> you control)? becomes the basic land type of your choice until end of turn\.?',
            ' '.join(text.strip().split()),re.I)
    if match is None:
        return None
    controlled=bool(match['controlled'])
    return ('target-basic-land-type-choice-v2' if controlled else 'target-basic-land-type-choice-v1',
        ({'op':TARGET_LAND_TYPE_OPERATION,'schema_version':2 if controlled else 1,'card':'$target.0','player':'$controller',
          **({'controller':'$controller'} if controlled else {})},),
        target_land_type_schema(controlled),(TARGET_LAND_TYPE_MECHANIC,'cr-115-targets'))



def fixed_target_land_type_template(text):
    match = re.fullmatch(
        r"Target land(?P<relation> you control| an opponent controls)? becomes "
        r"(?P<article>a|an) (?P<land>Plains|Island|Swamp|Mountain|Forest)"
        r"(?P<duration> until end of turn| for as long as this creature remains on the battlefield"
        r"| until this creature leaves the battlefield| until its controller['’]s next untap step)?"
        r"(?P<add> in addition to its other types)?[.]?",
        " ".join(text.strip().split()), re.I,
    )
    if match is None:
        return None
    basic_type = match["land"].casefold()
    if match["article"].casefold() != ("an" if basic_type == "island" else "a"):
        return None
    relation = {None: "any", " you control": "you", " an opponent controls": "opponent"}[
        match["relation"].casefold() if match["relation"] else None
    ]
    duration = match["duration"].casefold().replace("’", "'") if match["duration"] else ""
    lifetime = {
        "": "zone_object",
        " until end of turn": "until_end_of_turn",
        " for as long as this creature remains on the battlefield": "until_source_leaves",
        " until this creature leaves the battlefield": "until_source_battlefield_exit",
        " until its controller's next untap step": "until_recipient_controller_untap",
    }[duration]
    if lifetime in {"until_source_leaves", "until_source_battlefield_exit"} and match["add"]:
        return None
    if lifetime == "until_recipient_controller_untap" and (relation != "any" or match["add"]):
        return None
    spec = FixedTargetLandTypeSpec(basic_type, relation, lifetime, bool(match["add"]))
    return ("fixed-target-basic-land-type-v1", (spec.to_effect(),), spec.target_schema(),
            (FIXED_TARGET_LAND_TYPE_MECHANIC, "cr-115-targets", "cr-611-continuous-effects"))
