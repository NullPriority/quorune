from __future__ import annotations

"""Closed fixed and printed-cost graveyard/exile lifecycle spec validation."""


def validate_fixed_zone_cast_spec(spec):
    from ...cast_lifecycles import (FixedCastLifecycleKind,FixedCastLifecycleError,
        _AFTERMATH,_JUMP_START,_REBOUND,_ESCAPE,_FORETELL,_PLOT,_fixed_count,_validate_fixed_lifecycle_mana)
    bare={FixedCastLifecycleKind.AFTERMATH:_AFTERMATH,FixedCastLifecycleKind.JUMP_START:_JUMP_START,FixedCastLifecycleKind.REBOUND:_REBOUND}
    if spec.kind in bare:
        if (bare[spec.kind].fullmatch(spec.oracle_line.strip()) is None or spec.cost_text is not None or spec.mana_cost is not None
                or spec.counter_count is not None or spec.exile_count is not None or spec.schema_version!=3):
            raise FixedCastLifecycleError(f'{spec.kind.value.title()} requires its closed bare declaration')
        return
    pattern={FixedCastLifecycleKind.ESCAPE:_ESCAPE,FixedCastLifecycleKind.FORETELL:_FORETELL,FixedCastLifecycleKind.PLOT:_PLOT}[spec.kind]
    match=pattern.fullmatch(spec.oracle_line.strip())
    escape_count=_fixed_count(match.group('count')) if match is not None and spec.kind is FixedCastLifecycleKind.ESCAPE else None
    valid=(type(spec.exile_count) is int and spec.exile_count>0 and spec.exile_count==escape_count) if spec.kind is FixedCastLifecycleKind.ESCAPE else spec.exile_count is None
    if match is None or spec.schema_version!=3 or spec.counter_count is not None or not valid:raise FixedCastLifecycleError('Fixed zone-cast lifecycle declaration is malformed')
    _validate_fixed_lifecycle_mana(spec,match)
