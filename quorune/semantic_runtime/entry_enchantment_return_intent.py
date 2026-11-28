"""Typed physical graveyard return whose new incarnation is an enchantment."""

from dataclasses import dataclass

from .zone_move_intent import ZoneMoveIntent


@dataclass(frozen=True, slots=True)
class EntryEnchantmentReturnIntent(ZoneMoveIntent):
    entry_source_ref: str = ""

    def __post_init__(self):
        ZoneMoveIntent.__post_init__(self)
        if (
            type(self.entry_source_ref) is not str or not self.entry_source_ref or self.entry_source_ref.startswith("$")
            or self.expected_zones != ("graveyard",) or self.destination != "battlefield"
            or self.expected_zone_change_counter is None or not self.optional_if_missing
            or self.new_controller is None or self.effect_entry_counters or self.library_face_down_entry
        ):
            raise ValueError("Enchantment return requires its fixed original graveyard and source binding")
