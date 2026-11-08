"""Ordered existing results over one announced bounded target set."""
import re
from ..tap_state import TAP_TARGET_SET_UNTAP_FIELD
from .closed_effect_programs import _candidate_partitions
from .fixed_homogeneous_target_sets import fixed_homogeneous_target_set_effect_template
from .quantified_counter_targets import quantified_counter_target_template
from .target_characteristic_sets import target_characteristic_set_template

TARGET_SET_SEQUENCE_MECHANIC='shared-target-set-result-sequence'
TARGET_SET_SEQUENCE_CAPABILITY='resolution.target_set.ordered_results'
_FIRST=re.compile(r'(?P<verb>Tap|Untap) (?P<count>up to (?:one|two|three|four|five|six)|one or two|two|three|four|five|six) target (?P<subject>[^.]+)\.',re.I)
_REFERENCE=r'(?:them|those creatures|those permanents|those artifacts)'
_NEXT_UNTAP = re.compile(
    r"(?:(?:They|Those (?P<plural>[a-z]+)s) don['’]t untap during their "
    r"(?:controller['’]s|controllers['’]) next untap steps?|"
    r"(?:It|That (?P<singular>[a-z]+)) doesn['’]t untap during its controller['’]s next untap step)\.",
    re.I,
)


def _tap_set_next_untap_template(base, clauses, *, compile_independent):
    if len(clauses) < 2:
        return None
    _name, effects, schema, mechanics = base.compiled()
    tap = effects[0]
    if tap.get("op") != "tap_targets":
        return None
    clause = re.sub(r"^(?:Then|And)\s+", "", clauses[1], flags=re.I)
    match = _NEXT_UNTAP.fullmatch(clause)
    if match is None:
        return None
    if clause.casefold().startswith(("it ", "that ")) and tap["maximum_targets"] != 1:
        return None
    kind = (match["plural"] or match["singular"] or "permanent").casefold()
    if (kind != "permanent" and tuple(schema.get("types_any", ())) != (kind,)
            and kind not in schema.get("types_all", ())):
        return None
    effects = ({**tap, TAP_TARGET_SET_UNTAP_FIELD: True},)
    if len(clauses) == 2:
        return "tap-target-set-next-controller-untap-v1", effects, schema, mechanics
    tail = compile_independent(" ".join(clauses[2:]))
    if tail[0] is None or tail[2] is not None or len(tail[1]) != 1:
        return None
    return ("shared-target-set-result-sequence-v1", (*effects, *tail[1]), schema,
            tuple(dict.fromkeys((TARGET_SET_SEQUENCE_MECHANIC, *mechanics, *tail[3]))))


def target_set_result_sequence_template(text,*,compile_independent):
    partitions=list(_candidate_partitions(text))
    for clauses in tuple(partitions):
        split=re.fullmatch(r'(?P<first>(?:Tap|Untap) .+?) and (?P<rest>put .+)\.',clauses[0],re.I)
        if split is not None:partitions.append((split['first']+'.',split['rest']+'.',*clauses[1:]))
    for clauses in partitions:
        first=_FIRST.fullmatch(clauses[0])
        if first is None:continue
        base=fixed_homogeneous_target_set_effect_template(clauses[0])
        if base is None:continue
        next_untap = _tap_set_next_untap_template(base, clauses, compile_independent=compile_independent)
        if next_untap is not None:
            return next_untap
        name,effects,schema,mechanics=base.compiled();effects=list(effects);mechanics=list(mechanics)
        referenced=False;independent=False
        for clause in clauses[1:]:
            clause=re.sub(r'^(?:Then|And)\s+','',clause,flags=re.I)
            result=None
            counter=re.fullmatch(rf'(?P<result>Put .+) on each of {_REFERENCE}\.',clause,re.I)
            characteristic=re.fullmatch(rf'(?:Each of {_REFERENCE}|They each|Those creatures each) (?P<verb>gets?|gains?) (?P<result>.+)',clause,re.I)
            if counter is not None and not independent:
                result=quantified_counter_target_template('Choose '+first['count']+' target '+first['subject']+'. '+counter['result']+' on each of them.')
            elif characteristic is not None and not independent:
                result=target_characteristic_set_template(first['count']+' target '+first['subject']+' '+characteristic['verb']+' '+characteristic['result'])
            if result is not None:
                if result[2]!=schema:break
                referenced=True
            else:
                if re.search(r'\b(target|them|those|they|it|their|its|either|if|unless|may|each of)\b',clause,re.I):break
                result=compile_independent(clause)
                if result[0] is None or result[2] is not None or len(result[1])!=1:break
                independent=True
            effects.extend(result[1]);mechanics.extend(result[3])
        else:
            if referenced and 2<=len(effects)<=4:
                return ('shared-target-set-result-sequence-v1',tuple(effects),schema,
                    tuple(dict.fromkeys((TARGET_SET_SEQUENCE_MECHANIC,*mechanics))))
    return None
