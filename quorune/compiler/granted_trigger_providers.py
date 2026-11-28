"""Bind once-per-turn quoted triggers to the current granting permanent."""

from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE, GRANTED_TRIGGER_PROVIDER_VERSION
from ..rules.tap_state_capability_shapes import is_source_untap_counter_sequence
from ..rules.trigger_limits import TriggerLimitSpec
from ..rules.attacked_player_life import ATTACKED_PLAYER_LIFE_COVERAGE


def quoted_trigger_provider_contract(fragment, node, grant_coverage):
    if (grant_coverage == "fixed_query_typed_ability_grant"
            and node.trigger_limit == TriggerLimitSpec().to_dict()
            and node.event in {"permanent.tap", "permanent.untap"}
            and is_source_untap_counter_sequence(node.effects, node.target_schema)):
        fragment = {**fragment, "value": {**fragment["value"], "schema_version": GRANTED_TRIGGER_PROVIDER_VERSION}}
    coverage = node.runtime_coverage
    provider_bound = (fragment.get("kind") == "granted_triggered"
                      and fragment["value"].get("schema_version") == GRANTED_TRIGGER_PROVIDER_VERSION)
    if ATTACKED_PLAYER_LIFE_COVERAGE in coverage or provider_bound:
        # The typed grant supplies current ability presence for its recipient.
        coverage = tuple(tag for tag in coverage if tag != CURRENT_ABILITY_FRAGMENT_COVERAGE)
    return fragment, coverage
