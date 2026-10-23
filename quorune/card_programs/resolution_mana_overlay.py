from __future__ import annotations

"""Current source-bound mana triggers supersede their old compatibility views."""

from ..resolution_mana_model import ResolutionManaChoiceSpec,RESOLUTION_MANA_CHOICE_CAPABILITY


def _same_source(current,old,record):
    return (current.oracle_id==old.oracle_id==record.oracle_id and old.trust_level=='trusted'
        and not str(old.provenance.get('authored_by','')).startswith('oracle-ir-v')
        and all(current.provenance.get(key) and current.provenance[key]==old.provenance.get(key)
            for key in ('source_oracle_hash','source_rulings_hash')))


def shadowed_reviewed_resolution_mana_keys(record,generated_programs,reviewed_programs):
    """Retire only the exact legacy land-entry output and empty spell view.

    The current typed trigger owns its complete source body, five-color domain
    and controller predicate. Persisted legacy payloads remain available.
    """
    reviewed=tuple(reviewed_programs);shadowed=set()
    if not record.is_permanent_spell:return shadowed
    for current in generated_programs:
        if (current.trust_level!='trusted' or not str(current.provenance.get('authored_by','')).startswith('oracle-ir-v')
                or RESOLUTION_MANA_CHOICE_CAPABILITY not in current.capability_dependencies
                or not current.capability_closure or current.capability_closure.get('trusted') is not True
                or current.active_zone!='battlefield' or current.event!='land.enter'
                or current.event_condition!={'field':'controller','op':'eq','value':'$source.controller'}
                or len(current.effects)!=1 or current.handlers or current.target_schema is not None
                or current.cost_schema is not None or current.requires_arbiter):continue
        try:spec=ResolutionManaChoiceSpec.from_effect(current.effects[0])
        except (TypeError,ValueError,KeyError):continue
        if spec.selection!='one_color' or current.effects[0]['player']!='$controller' or current.effects[0]['source']!='$source':continue
        matches=[old for old in reviewed if _same_source(current,old,record)
            and old.active_zone=='battlefield' and old.event=='land.enter' and old.event_condition is None
            and old.effects==[{'op':'choose_mana','player':'$controller','amount':spec.amount}]
            and not old.handlers and old.target_schema is None and old.cost_schema is None
            and old.destination==current.destination and not old.requires_arbiter]
        if len(matches)!=1:continue
        shadowed.add(matches[0].key)
        for old in reviewed:
            if (_same_source(current,old,record) and old.active_zone=='stack' and old.event=='resolve'
                    and old.ability_id.startswith('spell:') and old.destination in {None,'battlefield'}
                    and old.event_condition is None and not old.effects and not old.handlers
                    and old.target_schema is None and old.cost_schema is None and not old.requires_arbiter):
                shadowed.add(old.key)
    return shadowed
