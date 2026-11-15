"""Coverage labels whose dependencies come from typed rule-action shapes."""

from ..earthbend_model import EARTHBEND_CAPABILITY, EARTHBEND_MECHANIC
from ..combat_removal import COMBAT_REMOVAL_CAPABILITY, COMBAT_REMOVAL_MECHANIC
from ..suspect_model import SUSPECT_CAPABILITY, SUSPECT_MECHANIC
from ..turn_skip_model import TURN_SKIP_CAPABILITY, TURN_SKIP_MECHANIC
from ..dungeon_progress import DUNGEON_VENTURE_CAPABILITY, DUNGEON_VENTURE_MECHANIC
from ..initiative_model import TAKE_INITIATIVE_CAPABILITY, TAKE_INITIATIVE_MECHANIC
from .event_counter_threshold import EVENT_COUNTER_THRESHOLD_CAPABILITY, EVENT_COUNTER_THRESHOLD_MECHANIC
from ..next_spell_cost_model import CAPABILITY as NEXT_COST_CAPABILITY, MECHANIC as NEXT_COST_MECHANIC
from ..day_night_model import DAY_NIGHT_DESIGNATION_CAPABILITY, DAY_NIGHT_DESIGNATION_MECHANIC, validate_day_night_designation


FIXED_RULE_ACTION_CAPABILITIES = {
    EARTHBEND_MECHANIC: (EARTHBEND_CAPABILITY,),
    COMBAT_REMOVAL_MECHANIC: (COMBAT_REMOVAL_CAPABILITY,),
    SUSPECT_MECHANIC: (SUSPECT_CAPABILITY,),
    TURN_SKIP_MECHANIC: (TURN_SKIP_CAPABILITY,),
    DUNGEON_VENTURE_MECHANIC: (DUNGEON_VENTURE_CAPABILITY,),
    TAKE_INITIATIVE_MECHANIC: (TAKE_INITIATIVE_CAPABILITY,),
    EVENT_COUNTER_THRESHOLD_MECHANIC: (EVENT_COUNTER_THRESHOLD_CAPABILITY,),
    NEXT_COST_MECHANIC: (NEXT_COST_CAPABILITY,),
    DAY_NIGHT_DESIGNATION_MECHANIC: (DAY_NIGHT_DESIGNATION_CAPABILITY,),
}


def fixed_day_night_designation_capabilities(*,effects,target_schema,mechanic_ids,**kwargs):
    if DAY_NIGHT_DESIGNATION_MECHANIC not in mechanic_ids or target_schema is not None or len(effects)!=1:return ()
    try:validate_day_night_designation(effects[0])
    except (TypeError,ValueError):return ()
    return (DAY_NIGHT_DESIGNATION_CAPABILITY,)


def transform_rule_action_capabilities(**kwargs):
    """Own the related direct-face and world-designation rule-action shapes."""
    from .transform_capability_shapes import source_transform_node_capabilities
    return source_transform_node_capabilities(**kwargs) or fixed_day_night_designation_capabilities(**kwargs)
