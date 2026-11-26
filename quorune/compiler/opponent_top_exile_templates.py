"""Recognize one printed opponent-library top without changing its supplier."""

from ..land_exile_source_return_model import LAND_EXILE_RETURN_MECHANIC, land_exile_source_return_instruction
from ..opponent_library_top_model import OpponentLibraryTopSpec, opponent_half_top_exile_effect
from ..replacement.immutable import FrozenMap
from ..targeted_exile_land_entry import (
    TARGETED_EXILE_LAND_ENTRY_MECHANIC, targeted_exile_land_entry_instruction,
)
from ..rules.discover_capability_shapes import original_source_cast_condition
from ..rules.targeted_opponent_library_top import (
    OPPONENT_TOP_EXILE_MECHANICS, OWN_COLORLESS_CAST_CONDITION, OWN_SPELL_CAST_CONDITION,
    opponent_top_exile_target_schema,
)


_EXILE = "target opponent exiles the top card of their library."
_PLAY_BODIES = frozenset({
    _EXILE + " you may play that card this turn.",
    _EXILE + " until end of turn, you may play that card.",
})


_EXILE_FOUR_LAND_ENTRY = (
    "target opponent exiles the top four cards of their library, then you may put any number "
    "of land cards that player owns from exile onto the battlefield under your control."
)


_EXILE_LAND_RETURN = _EXILE + " if it's a land card, you may return this creature to its owner's hand."


def _compiled(allows_play):
    return ("targeted-opponent-top-exile-v1", (OpponentLibraryTopSpec(allows_play).effect(),),
            opponent_top_exile_target_schema(), OPPONENT_TOP_EXILE_MECHANICS)


def opponent_top_exile_trigger_result(binding, body):
    if ((binding.event.value, binding.active_zone) == ("spell.cast", "stack")
            and body == binding.body
            and " ".join(body.split()).casefold() in {
                "target opponent exiles the top half of their library, rounded up.",
                "target opponent exiles the top half of his or her library, rounded up.",
            }
            and FrozenMap(binding.event_condition or {}) == OWN_SPELL_CAST_CONDITION):
        return ("targeted-opponent-top-half-exile-v1", (opponent_half_top_exile_effect(),),
                opponent_top_exile_target_schema(), OPPONENT_TOP_EXILE_MECHANICS)
    if body != binding.body:
        return None
    normalized = " ".join(body.split()).casefold()
    if ((binding.event.value, binding.active_zone) == ("spell.cast", "stack")
            and normalized == _EXILE_FOUR_LAND_ENTRY
            and FrozenMap(binding.event_condition or {}) == original_source_cast_condition()):
        return ("targeted-exile-land-entry-v1", (targeted_exile_land_entry_instruction(),),
                opponent_top_exile_target_schema(), (TARGETED_EXILE_LAND_ENTRY_MECHANIC, "cr-115-targets"))
    if ((binding.event.value, binding.active_zone) == ("spell.cast", "battlefield")
            and normalized == _EXILE
            and FrozenMap(binding.event_condition or {}) == OWN_COLORLESS_CAST_CONDITION):
        return _compiled(False)
    return None


def opponent_top_exile_activation_result(ability, body):
    if tuple(ability.zones) != ("battlefield",) or ability.mana_ability or ability.uncompiled_costs:
        return None
    normalized = " ".join(body.split()).casefold().replace("\u2019", "'")
    if normalized in _PLAY_BODIES:
        return _compiled(True)
    if normalized == _EXILE_LAND_RETURN:
        return ("targeted-exiled-land-source-return-v1", (land_exile_source_return_instruction(),),
                opponent_top_exile_target_schema(), (LAND_EXILE_RETURN_MECHANIC, "cr-115-targets"))
    return None
