"""One original target receives two counters or two original targets receive one each."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.split_two_counter_target_amount import SPLIT_TWO_COUNTER_TARGET_CAPABILITY, SplitTwoCounterTargetAmount
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card
import test_monarch_movement_replacements as movement_fixture


PROFILE = "commander_review"


class FixedTwoCounterTargetDistributionTests(InheritedNativeFixture, unittest.TestCase):
    reject = movement_fixture.MonarchMovementReplacementTests.reject

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "fixed-two-counter-distribution.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/fixed-two-counter-target-distribution-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Two-counter distribution", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def lands(self, game, names, *, seat="A"):
        for index, name in enumerate(names):
            self.add(game.engine, name, seat=seat, ref=f"counter-budget-{seat}-{index}")

    def ready(self, game, *cards):
        for card in cards:
            self.assert_closed(game, card)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game)
        self.checkpoint(game)

    def counter(self, game, card, name="+1/+1"):
        return game.state.cards[card.object_id].counters.get(name, 0)

    def test_actual_spell_keeps_the_original_share_after_the_other_target_leaves(self):
        game = self.session(76333001)
        source = self.add(game.engine, "Elven Rite", zone="hand")
        own = self.add(game.engine, "Grizzly Bears")
        other = self.add(game.engine, "Grizzly Bears", seat="B", ref="other-original-target")
        bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        self.lands(game, ("Forest", "Forest"))
        self.lands(game, ("Island",), seat="B")
        self.ready(game, source, bounce)
        for refs in ([], [own.ref, own.ref]):
            self.reject(game, "A", {"action_id": f"cast:{source.ref}", "targets": refs, "pay": "auto"})
        self.cast(game, "A", source, [own.ref, other.ref])
        self.cast(game, "B", bounce, [other.ref])
        self.resolve_top(game)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(1, self.counter(game, own))
        self.assertEqual("hand", game.state.cards[other.object_id].zone)
        self.assertEqual(0, self.counter(game, other))
        self.replay(game, load=True)

    def test_actual_single_target_receives_both_minus_counters_or_both_adventure_counters(self):
        for name, count_name in (("Splendid Agony", "-1/-1"), ("Ondu Knotmaster // Throw a Line", "+1/+1")):
            with self.subTest(original=name):
                game = self.session(76333010 + len(name))
                source = self.add(game.engine, name, zone="hand")
                target = self.add(game.engine, "Hill Giant", seat="B")
                adventure = name.startswith("Ondu")
                self.lands(game, ("Plains", "Swamp") if adventure else ("Swamp",)*3)
                self.ready(game, source)
                if adventure:
                    face = "Throw a Line"
                    action_id = f"cast:{source.ref}:face:{face}"
                    result = game.act("pilot:A", {"action_id": action_id, "face": face,
                                                   "targets": [target.ref], "pay": "auto"})
                    self.assertTrue(result.ok, result.summary)
                else:
                    self.cast(game, "A", source, [target.ref])
                self.assertIsNone(self.resolve(game))
                self.assertEqual(2, self.counter(game, target, count_name))
                if adventure:
                    self.assertEqual("exile", game.state.cards[source.object_id].zone)
                self.replay(game, load=True)

    def test_actual_entry_targets_itself_and_rejects_a_foreign_controlled_creature(self):
        game = self.session(76333003)
        source = self.add(game.engine, "Armament Corps", zone="hand")
        own = self.add(game.engine, "Grizzly Bears")
        foreign = self.add(game.engine, "Grizzly Bears", seat="B", ref="not-controlled")
        self.lands(game, ("Forest", "Forest", "Forest", "Plains", "Swamp"))
        self.ready(game, source)
        self.cast(game, "A", source)
        pending = self.resolve(game)
        self.assertEqual(("semantic.target", ["A"]), (pending.kind, pending.actors))
        self.reject(game, "A", {"action_id": "choose", "targets": [foreign.ref]})
        entered = game.state.cards[source.object_id]
        self.target(game, "A", [entered.ref, own.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual((1, 1, 0), tuple(self.counter(game, card) for card in (entered, own, foreign)))
        self.replay(game, load=True)

    def test_actual_crystal_doubles_the_complete_one_target_allocation(self):
        game = self.session(76333004)
        source = self.add(game.engine, "The Earth Crystal")
        own = self.add(game.engine, "Grizzly Bears")
        foreign = self.add(game.engine, "Grizzly Bears", seat="B", ref="not-controlled")
        self.lands(game, ("Forest",)*6)
        self.ready(game, source)
        action_id = f"activate:{source.ref}:ab3"
        self.reject(game, "A", {"action_id": action_id, "targets": [foreign.ref], "pay": "auto"})
        result = game.act("pilot:A", {"action_id": action_id, "targets": [own.ref], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertTrue(game.state.cards[source.object_id].tapped)
        self.assertIsNone(self.resolve(game))
        self.assertEqual((4, 0), (self.counter(game, own), self.counter(game, foreign)))
        self.replay(game, load=True)

    def test_actual_modal_distribution_and_draw_use_their_own_printed_modes(self):
        for mode in ("mode_2", "mode_3"):
            with self.subTest(mode=mode):
                game = self.session(76333020 + int(mode[-1]))
                source = self.add(game.engine, "Abzan Charm", zone="hand")
                target = self.add(game.engine, "Grizzly Bears", seat="B")
                self.lands(game, ("Plains", "Swamp", "Forest"))
                self.ready(game, source)
                initial_life = game.state.players["A"].life
                initial_hand = len(game.state.players["A"].zones["hand"])
                command = {"action_id": f"cast:{source.ref}", "pay": "auto", "modes": [mode],
                           "targets": [target.ref] if mode == "mode_3" else []}
                result = game.act("pilot:A", command)
                self.assertTrue(result.ok, result.summary)
                self.assertIsNone(self.resolve(game))
                draws = mode == "mode_2"
                self.assertEqual(initial_life - 2 * draws, game.state.players["A"].life)
                self.assertEqual(initial_hand - 1 + 2 * draws, len(game.state.players["A"].zones["hand"]))
                self.assertEqual(0 if draws else 2, self.counter(game, target))
                self.replay(game, load=True)

    def test_full_originals_require_one_exact_initial_allocation_and_its_independent_authority(self):
        cap = SPLIT_TWO_COUNTER_TARGET_CAPABILITY
        for name, (report, semantics) in (
            ("Abzan Charm", register_card(self.db, self.registry, "Abzan Charm")),
            ("Armament Corps", register_card(self.db, self.registry, "Armament Corps")),
            ("Elven Rite", register_card(self.db, self.registry, "Elven Rite")),
            ("Splendid Agony", register_card(self.db, self.registry, "Splendid Agony")),
            ("The Earth Crystal", register_card(self.db, self.registry, "The Earth Crystal")),
            ("Throw a Line", register_card(self.db, self.registry, "Ondu Knotmaster // Throw a Line")),
        ):
            with self.subTest(original=name):
                self.assertTrue(report["strict_capability_ready"], report["blockers"])
                self.assertEqual("capability_closed", report["trust_basis"])
                program, = (row for row in semantics.programs_for_oracle(report["oracle_id"])
                            if cap in row.capability_dependencies)
                schema = program.target_schema
                if name == "Abzan Charm":
                    self.assertEqual({"mode_1", "mode_2", "mode_3"}, set(schema["modes"]))
                    schema = schema["modes"]["mode_3"]
                self.assertEqual((1, 2), (schema["min"], schema["max"]))
                effect, = schema["effects"] if name == "Abzan Charm" else program.effects
                self.assertEqual(SplitTwoCounterTargetAmount(), SplitTwoCounterTargetAmount.from_dict(effect["amount"]))
                deps = tuple(key for key in program.capability_dependencies if key != cap)
                absent = replace(program, capability_dependencies=deps,
                                 capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
                bound = bind_semantic_program_runtime(absent, capability_registry=self.registry, profile=PROFILE)
                self.assertFalse(bound["strict"])
                self.assertIn(f"capability:undeclared_runtime_dependency:{cap}", bound["blockers"])
        card = self.db.lookup("Elven Rite", fuzzy=False)
        for old, new in (("two +1/+1 counters", "three +1/+1 counters"),
                         ("one or two", "up to two"),
                         ("target creatures.", "target creatures. Rewrite history.")):
            changed = replace(card, oracle_text=card.oracle_text.replace(old, new))
            result = compile_oracle_card(changed, capability_registry=self.registry, capability_profile=PROFILE)
            self.assertNotEqual("exact", result.status)
