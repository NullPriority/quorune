"""The stock counter allocation completes before the title-dependent doubling."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.monarch_counter_doubling_condition import is_monarch_counter_doubling
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.monarch_counter_distribution import MONARCH_COUNTER_DISTRIBUTION_CAPABILITY
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card
import test_monarch_movement_replacements as movement_fixture


PROFILE = "commander_review"


class MonarchCounterDistributionDoublingTests(InheritedNativeFixture, unittest.TestCase):
    throne = movement_fixture.MonarchMovementReplacementTests.throne
    upkeep = movement_fixture.MonarchMovementReplacementTests.upkeep
    transfer = movement_fixture.MonarchMovementReplacementTests.transfer
    reject = movement_fixture.MonarchMovementReplacementTests.reject

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "monarch-counter-distribution.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/monarch-counter-distribution-doubling-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Court of Garenbrig", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def start(self, seed, *, scales=False, bounce=False, title=False):
        game = self.session(seed)
        court = self.add(game.engine, "Court of Garenbrig")
        own = self.add(game.engine, "Grizzly Bears", ref="selected-owned-creature")
        extra = self.add(game.engine, "Grizzly Bears", ref="other-owned-creature")
        foreign = self.add(game.engine, "Grizzly Bears", seat="B", ref="foreign-creature")
        for card, value in ((own, 1), (extra, 2), (foreign, 1)):
            card.counters["+1/+1"] = value
        if scales:
            self.assert_closed(game, self.add(game.engine, "Hardened Scales"))
        response = self.add(game.engine, "Unsummon", seat="B", zone="hand") if bounce else None
        if response is not None:
            self.add(game.engine, "Island", seat="B")
            self.assert_closed(game, response)
        throne = self.throne(game) if title else None
        self.upkeep(game, court, throne)
        pending = self.resolve(game)
        self.assertEqual(("semantic.target", ["A"]), (pending.kind, pending.actors))
        return game, own, extra, foreign, response, throne

    def counts(self, game, *cards):
        return tuple(game.state.cards[card.object_id].counters.get("+1/+1", 0) for card in cards)

    def test_actual_zero_original_targets_still_doubles_only_controlled_creatures(self):
        game, own, extra, foreign, _, _ = self.start(76338001)
        self.reject(game, "A", {"action_id": "choose", "targets": [own.ref, own.ref]})
        self.target(game, "A", [])
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual((2, 4, 1), self.counts(game, own, extra, foreign))
        self.replay(game, load=True)

    def test_actual_counter_replacement_commits_before_current_doubling(self):
        game, own, extra, foreign, _, _ = self.start(76338002, scales=True)
        self.target(game, "A", [own.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual((9, 5, 1), self.counts(game, own, extra, foreign))
        self.replay(game, load=True)

    def test_actual_target_loss_and_title_change_preserve_the_original_allocations(self):
        for mode in ("one_loss", "all_lost", "title"):
            with self.subTest(mode=mode):
                game, own, extra, foreign, bounce, throne = self.start(
                    76338010 + len(mode), bounce=mode != "title", title=mode == "title",
                )
                refs = [foreign.ref] if mode == "all_lost" else [own.ref, foreign.ref]
                self.target(game, "A", refs)
                if bounce is not None:
                    self.cast(game, "B", bounce, [foreign.ref])
                    self.resolve_top(game)
                if throne is not None:
                    self.transfer(game, throne)
                game = self.replay(game, load=True)
                self.assertIsNone(self.resolve(game))
                expected = {"one_loss": (4, 4, 0), "all_lost": (1, 2, 0), "title": (2, 2, 2)}[mode]
                self.assertEqual(expected, self.counts(game, own, extra, foreign))
                if bounce is not None:
                    self.assertEqual("hand", game.state.cards[foreign.object_id].zone)
                self.replay(game, load=True)

    def test_complete_original_preserves_optional_targets_and_ordered_doubling_authority(self):
        cap = MONARCH_COUNTER_DISTRIBUTION_CAPABILITY
        report, semantics = register_card(self.db, self.registry, "Court of Garenbrig")
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        self.assertEqual("capability_closed", report["trust_basis"])
        programs = semantics.programs_for_oracle(report["oracle_id"])
        self.assertEqual(2, len(programs))
        program, = (row for row in programs if cap in row.capability_dependencies)
        self.assertEqual(2, program.target_schema["up_to"])
        prefix, guard = program.effects
        self.assertEqual("place_counters_on_targets", prefix["op"])
        self.assertEqual(2, prefix["amount"]["schema_version"])
        self.assertTrue(is_monarch_counter_doubling(guard))
        deps = tuple(key for key in program.capability_dependencies if key != cap)
        absent = replace(program, capability_dependencies=deps,
                         capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
        result = bind_semantic_program_runtime(absent, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(result["strict"])
        self.assertIn(f"capability:undeclared_runtime_dependency:{cap}", result["blockers"])
        card = self.db.lookup("Court of Garenbrig", fuzzy=False)
        for old, new in (("up to two target", "up to three target"),
                         ("each creature you control", "each creature your opponents control"),
                         ("each creature you control.", "each creature you control. Rewrite history.")):
            changed = replace(card, oracle_text=card.oracle_text.replace(old, new))
            result = compile_oracle_card(changed, capability_registry=self.registry, capability_profile=PROFILE)
            self.assertNotEqual("exact", result.status)
