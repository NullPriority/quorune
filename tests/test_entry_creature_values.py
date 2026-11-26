from __future__ import annotations

"""Pre-entry current creature values through the native as-enters owner."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.entry_counter_model import ENTRY_CREATURE_VALUE_CAPABILITY
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


ORIGINALS = (
    "Prime Speaker Zegana",
    "Ambitious Dragonborn",
    "Towering Titan",
    "Ascendant Acolyte",
    "Ulasht, the Hate Seed",
)


class EntryCreatureValueTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def use_database(cls, path):
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition(
            "Current entry creature values",
            [DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30)],
            ["Isamaru, Hound of Konda"],
        )

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "entry-creature-values.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/entry-creature-values-cards.json"], path)
        cls.use_database(path)

    def start(self, game, payment):
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, mana=payment)
        self.checkpoint(game)

    def placed(self, game, card):
        return game.state.cards[card.object_id].counters.get("+1/+1", 0)

    def test_original_programs_are_whole_and_have_the_typed_entry_dependency(self):
        game = self.session(61412091)
        for name in ORIGINALS:
            with self.subTest(name=name):
                card = self.add(game.engine, name, zone="hand")
                self.assert_closed(game, card)
                self.assertTrue(any(
                    ENTRY_CREATURE_VALUE_CAPABILITY in program.capability_dependencies
                    for program in game.engine.semantics.programs_for_oracle(card.oracle_id)
                ))

    def test_zegana_uses_live_other_power_and_draws_her_new_power(self):
        game = self.session(61412092)
        entrant = self.add(game.engine, "Prime Speaker Zegana", zone="hand")
        bear = self.add(game.engine, "Grizzly Bears")
        bear.counters["+1/+1"] = 2
        self.add(game.engine, "Shivan Dragon", seat="B")
        phased = self.add(game.engine, "Shivan Dragon", ref="phased-dragon")
        phased.phased_out = True
        self.assert_closed(game, entrant)
        self.start(game, {"C": 2, "G": 2, "U": 2})
        hand_size = len(game.state.players["A"].zones["hand"])
        self.cast(game, "A", entrant)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(4, self.placed(game, entrant))
        self.assertEqual(hand_size - 1 + 5, len(game.state.players["A"].zones["hand"]))
        self.replay(game, load=True)

    def test_dragonborn_reads_own_graveyard_as_well_as_controlled_creatures(self):
        game = self.session(61412093)
        entrant = self.add(game.engine, "Ambitious Dragonborn", zone="hand")
        self.add(game.engine, "Grizzly Bears")
        self.add(game.engine, "Shivan Dragon", zone="graveyard")
        self.add(game.engine, "Colossal Dreadmaw", seat="C", zone="graveyard")
        hidden = self.add(game.engine, "Colossal Dreadmaw", zone="hand", ref="entry-private-large-creature")
        self.assert_closed(game, entrant)
        self.start(game, {"C": 3, "G": 1})
        self.cast(game, "A", entrant)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(5, self.placed(game, entrant))
        import json
        self.assertNotIn(hidden.ref, json.dumps(game.packet("pilot:B", full=True)))
        self.replay(game, load=True)

    def test_titan_sums_toughness_but_excludes_a_simultaneous_entrant(self):
        from quorune.zone_transitions import ZoneTransitionOwner

        game = self.session(61412094)
        titan = self.add(game.engine, "Towering Titan", zone="graveyard")
        arriving = self.add(game.engine, "Shivan Dragon", zone="graveyard")
        self.add(game.engine, "Wall of Omens")
        self.add(game.engine, "Grizzly Bears")
        self.add(game.engine, "Colossal Dreadmaw", seat="B")
        self.assert_closed(game, titan)
        self.start(game, {})
        ZoneTransitionOwner(game.engine).move_cards_simultaneously(
            ((titan.object_id, "battlefield"), (arriving.object_id, "battlefield")),
            reason="simultaneous control fixture",
        )
        self.assertEqual("battlefield", game.state.cards[arriving.object_id].zone)
        self.assertEqual(6, self.placed(game, titan))

    def test_acolyte_counts_only_the_named_counters_on_its_controllers_creatures(self):
        game = self.session(61412095)
        entrant = self.add(game.engine, "Ascendant Acolyte", zone="hand")
        bear = self.add(game.engine, "Grizzly Bears")
        bear.counters.update({"+1/+1": 3, "charge": 7})
        ring = self.add(game.engine, "Sol Ring")
        ring.counters["+1/+1"] = 5
        self.add(game.engine, "Grizzly Bears", seat="B", ref="opposing-bear").counters["+1/+1"] = 9
        self.assert_closed(game, entrant)
        self.start(game, {"C": 4, "G": 1})
        self.cast(game, "A", entrant)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(3, self.placed(game, entrant))
        self.replay(game, load=True)

    def test_ulasht_counts_both_colors_of_a_single_current_creature(self):
        game = self.session(61412096)
        entrant = self.add(game.engine, "Ulasht, the Hate Seed", zone="hand")
        self.add(game.engine, "Burning-Tree Emissary")
        self.add(game.engine, "Grizzly Bears")
        self.add(game.engine, "Shivan Dragon")
        self.add(game.engine, "Burning-Tree Emissary", seat="C", ref="opposing-emissary")
        self.assert_closed(game, entrant)
        self.start(game, {"C": 2, "R": 1, "G": 1})
        self.cast(game, "A", entrant)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(4, self.placed(game, entrant))
        self.replay(game, load=True)

    def test_verified_withdrawal_blocks_all_five_complete_entry_programs(self):
        import json

        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        next(row for row in raw["capabilities"] if row["id"] == ENTRY_CREATURE_VALUE_CAPABILITY).update(
            status="blocked", blockers=["The current public pre-entry creature read was withdrawn"],
            dependency_fail_closed_status="not_run",
        )
        without = CapabilityRegistry(raw)
        index = build_index(registry_value=raw,
                            declaration_source=read_evidence_document(ROOT / "platform/capability-evidence-declarations.json"),
                            discovered_test_ids=set(discover_tests(ROOT).values()))
        without.mark_evidence_verified(validate_capability_evidence_index(index, registry=without))
        for name in ORIGINALS:
            with self.subTest(card=name):
                result = compile_oracle_card(self.db.lookup(name), capability_registry=without,
                                             capability_profile="commander_review")
                blockers = {item for residual in result.material_residuals for item in residual.blockers}
                self.assertTrue(any("status:" + ENTRY_CREATURE_VALUE_CAPABILITY + ":blocked" in item
                                    for item in blockers), blockers)
                self.assertFalse(any("evidence_index:unverified" in item for item in blockers))

    def test_omitting_the_pre_entry_read_breaks_the_native_amount(self):
        with patch("quorune.entry_counters._current_creature_entry_value", return_value=1) as omit:
            with self.assertRaisesRegex(AssertionError, "5 != 1"):
                self.test_dragonborn_reads_own_graveyard_as_well_as_controlled_creatures()
        omit.assert_called_once()
