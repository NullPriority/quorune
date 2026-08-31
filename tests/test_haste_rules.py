from __future__ import annotations

import unittest
from types import SimpleNamespace

from quorune.characteristic_evaluation import type_parts
from quorune.haste import (
    HasteRuleError,
    has_effective_haste,
    is_summoning_sick,
    summoning_sickness_prohibits_attack,
    summoning_sickness_prohibits_tap_or_untap_cost,
)


class _Host:
    def __init__(self) -> None:
        self.state = SimpleNamespace(
            players={"A": SimpleNamespace(turns_begun=1)}
        )

    @staticmethod
    def _effective_card_data(card, *, ignore_face_down=False):
        return card.effective

    @staticmethod
    def _type_parts(type_line: str):
        return type_parts(type_line)


def _card(
    *,
    keywords=(),
    type_line: str = "Creature — Test",
    acquired: int = 1,
):
    return SimpleNamespace(
        controller="A",
        acquired_control_turn_count=acquired,
        effective={"type_line": type_line, "keywords": keywords},
    )


class HasteRuleTests(unittest.TestCase):
    def test_current_effective_haste_is_case_insensitive_and_redundant(self):
        host = _Host()
        creature = _card(keywords=("haste", "HASTE", "Haste"))

        self.assertTrue(is_summoning_sick(host, creature))
        self.assertTrue(has_effective_haste(host, creature))
        self.assertFalse(
            summoning_sickness_prohibits_attack(host, creature)
        )
        self.assertFalse(
            summoning_sickness_prohibits_tap_or_untap_cost(host, creature)
        )

    def test_control_timestamp_restricts_only_current_creatures(self):
        host = _Host()
        sick = _card()
        controlled_since_prior_turn = _card(acquired=0)
        noncreature = _card(type_line="Artifact")

        self.assertTrue(summoning_sickness_prohibits_attack(host, sick))
        self.assertTrue(
            summoning_sickness_prohibits_tap_or_untap_cost(host, sick)
        )
        self.assertFalse(is_summoning_sick(host, controlled_since_prior_turn))
        self.assertFalse(is_summoning_sick(host, _card(acquired=-1)))
        self.assertFalse(is_summoning_sick(host, noncreature))

    def test_as_though_haste_applies_only_to_activation_cost_boundary(self):
        host = _Host()
        creature = _card()

        self.assertTrue(summoning_sickness_prohibits_attack(host, creature))
        self.assertFalse(
            summoning_sickness_prohibits_tap_or_untap_cost(
                host,
                creature,
                as_though_haste=True,
            )
        )

    def test_malformed_effective_keywords_fail_closed(self):
        host = _Host()
        creature = _card(keywords=("Haste", 1))

        with self.assertRaisesRegex(HasteRuleError, "keywords"):
            summoning_sickness_prohibits_attack(host, creature)

    def test_malformed_control_snapshot_fails_closed(self):
        host = _Host()
        creature = _card(acquired=1)
        creature.controller = "missing"

        with self.assertRaisesRegex(HasteRuleError, "controller"):
            is_summoning_sick(host, creature)
        with self.assertRaisesRegex(HasteRuleError, "boolean"):
            summoning_sickness_prohibits_tap_or_untap_cost(
                host,
                _card(),
                as_though_haste=1,
            )


if __name__ == "__main__":
    unittest.main()
