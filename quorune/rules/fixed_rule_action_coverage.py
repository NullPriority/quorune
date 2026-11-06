"""Coverage labels whose dependencies come from typed rule-action shapes."""

from ..earthbend_model import EARTHBEND_CAPABILITY, EARTHBEND_MECHANIC
from ..suspect_model import SUSPECT_CAPABILITY, SUSPECT_MECHANIC
from ..turn_skip_model import TURN_SKIP_CAPABILITY, TURN_SKIP_MECHANIC


FIXED_RULE_ACTION_CAPABILITIES = {
    EARTHBEND_MECHANIC: (EARTHBEND_CAPABILITY,),
    SUSPECT_MECHANIC: (SUSPECT_CAPABILITY,),
    TURN_SKIP_MECHANIC: (TURN_SKIP_CAPABILITY,),
}
