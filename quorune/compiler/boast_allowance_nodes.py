from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..rules.boast_allowance import CAPABILITY, EVENT, PRINTED_ALLOWANCE, descriptor
from .static_runtime_nodes import runtime_handler_node


def boast_allowance_node(*, material_line, **context):
    if " ".join(material_line.split()).casefold() != PRINTED_ALLOWANCE:
        return None
    return runtime_handler_node(
        **context, compiled=("controlled-creatures-boast-twice-v1", descriptor(), CAPABILITY),
        kind="static_ability", event=EVENT, active_zone="battlefield",
        runtime_coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE,),
        dependency_reason="The second boast requires its live controller-turn allowance and usage owner.",
    )
