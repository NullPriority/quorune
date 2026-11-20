"""Closed target-library look and single-card continuation grammar."""
from dataclasses import dataclass
from . import regex_working_set as re
from ..target_library_inspection_model import TargetLibraryInspectionSpec, TARGET_LIBRARY_INSPECTION_MECHANIC
from .delayed_draw_templates import FixedNextTurnDrawTemplate, fixed_next_turn_upkeep_draw_effect_template

_HEAD = re.compile(r"Look at the top (?:(?P<count>one|two|three|[1-3]) cards?|(?P<single>card)) "
                   r"of target (?P<relation>player|opponent)['’]s library(?P<tail>.*)", re.I)


@dataclass(frozen=True, slots=True)
class TargetLibraryInspectionTemplate:
    spec: TargetLibraryInspectionSpec
    delayed_draw: bool = False

    def compiled(self):
        effects = (self.spec.effect(),);mechanics = (TARGET_LIBRARY_INSPECTION_MECHANIC,)
        if self.delayed_draw:
            _name, followup, _schema, ids = FixedNextTurnDrawTemplate().compiled()
            effects += followup;mechanics += ids
        return "fixed-target-library-inspection-v1", effects, self.spec.target_schema(), mechanics


def target_library_inspection_template(text):
    match = _HEAD.fullmatch(" ".join(text.strip().split()))
    if match is None:return None
    count = 1 if match["single"] else {"one":1,"two":2,"three":3}.get(match["count"].casefold(), int(match["count"]) if match["count"].isdigit() else 0)
    tail = match["tail"].strip();mode = None;delayed = False
    if tail in {"", "."}:mode = "look"
    elif count == 1 and re.fullmatch(r"\. You may put that card into (?:their|that player['’]s) graveyard\.",tail,re.I):mode = "may_graveyard"
    elif re.fullmatch(r"\. Put one of them into (?:their|that player['’]s) graveyard\.",tail,re.I):mode = "one_graveyard"
    elif re.fullmatch(r"(?:, then|\.) [Ee]xile one of them\.",tail,re.I):mode = "one_exile"
    elif tail.startswith(". ") and fixed_next_turn_upkeep_draw_effect_template(tail[2:]) is not None:
        mode = "look";delayed = True
    if mode is None:return None
    try:return TargetLibraryInspectionTemplate(TargetLibraryInspectionSpec(count, mode, match["relation"].casefold() == "opponent"), delayed)
    except ValueError:return None
