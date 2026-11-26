"""Actual fixed destruction and black-green allocation from committed results."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.destruction_result_model import DESTRUCTION_MANA_ALLOCATION_CAPABILITY, DestructionFollowup
from quorune.variable_resolution_mana_model import VariableResolutionManaChoiceSpec
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


RITUAL = "Culling Ritual"
PROFILE = "commander_review"


class CommittedDestructionColorAllocationTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "committed-destruction-mana.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/committed-destruction-color-allocation-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Destruction mana", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def start(self, game, *cards):
        for card in cards:
            self.assert_closed(game, card)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, mana={"B": 2, "G": 2, "W": 1, "C": 5})
        self.checkpoint(game)

    def allocation(self, game, count):
        pending = self.resolve(game)
        self.assertIsNotNone(pending)
        self.assertEqual(("semantic.choice", ["A"]), (pending.kind, pending.actors))
        choose, = (row for row in pending.payload_by_actor["A"]["legal_actions"] if row["id"] == "choose")
        schema = choose["choice_schema"]
        self.assertEqual(count, schema["total"])
        self.assertEqual({"B", "G"}, set(schema["allowed_colors"]))
        for seat in "BCD":
            self.assertIsNone(game.packet("pilot:" + seat, full=True)["decision"])

    def pay_allocation(self, game, mana):
        before = dict(game.state.players["A"].mana_pool)
        result = game.act("pilot:A", {"action_id": "choose", "mana": mana})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual({color: amount + mana.get(color, 0) for color, amount in before.items()},
                         game.state.players["A"].mana_pool)

    def test_actual_regeneration_and_indestructible_reduce_the_count_before_mixed_mana(self):
        game = self.session(61401801)
        spell = self.add(game.engine, RITUAL, zone="hand")
        alarm = self.add(game.engine, "Raise the Alarm", zone="hand")
        wrap = self.add(game.engine, "Wrap in Vigor", seat="B", zone="hand")
        ring = self.add(game.engine, "Sol Ring")
        protected = self.add(game.engine, "Llanowar Elves", seat="B")
        limit = self.add(game.engine, "Grizzly Bears", seat="C")
        durable = self.add(game.engine, "Darksteel Relic", seat="B")
        land = self.add(game.engine, "Tree of Tales", seat="B")
        large = self.add(game.engine, "Colossal Dreadmaw", seat="D")
        for i in range(2):
            self.add(game.engine, "Forest", seat="B", ref=f"ritual-b-green-{i}")
        self.start(game, spell, alarm, wrap, durable, land, large)
        self.cast(game, "A", alarm)
        self.assertIsNone(self.resolve(game))
        soldiers = [card for card in game.state.cards.values()
                    if card.zone == "battlefield" and card.is_token and card.printed_name == "Soldier Token"]
        self.assertEqual(2, len(soldiers))
        self.cast(game, "A", spell)
        self.cast(game, "B", wrap)
        self.resolve_top(game)
        self.assertEqual(1, game.state.cards[protected.object_id].regeneration_shields)
        self.allocation(game, 4)
        self.assertEqual(["graveyard", "graveyard"], [game.state.cards[c.object_id].zone for c in (ring, limit)])
        self.assertTrue(all(game.state.cards[c.object_id].zone != "battlefield" for c in soldiers))
        self.assertTrue(all(game.state.cards[c.object_id].zone == "battlefield" for c in (protected, durable, land, large)))
        self.assertTrue(game.state.cards[protected.object_id].tapped)
        game = self.replay(game, load=True)
        before = authoritative_state_hash(game.state)
        for seat, allocation in (("B", {"B": 2, "G": 2}), ("A", {"B": 3}),
                                 ("A", {"G": 5}), ("A", {"B": 3, "R": 1})):
            denied = game.act("pilot:" + seat, {"action_id": "choose", "mana": allocation})
            self.assertFalse(denied.ok, denied.summary)
            self.assertEqual(before, authoritative_state_hash(game.state))
        self.pay_allocation(game, {"B": 1, "G": 3})
        self.replay(game, load=True)

    def test_actual_destruction_redirected_to_exile_still_counts_each_permanent(self):
        game = self.session(61401802)
        spell = self.add(game.engine, RITUAL, zone="hand")
        replacement = self.add(game.engine, "Rest in Peace", seat="B")
        creature = self.add(game.engine, "Llanowar Elves", seat="C")
        self.start(game, spell, replacement, creature)
        self.cast(game, "A", spell)
        self.allocation(game, 2)
        self.assertEqual(["exile", "exile"], [game.state.cards[c.object_id].zone for c in (replacement, creature)])
        self.pay_allocation(game, {"G": 2})
        self.replay(game, load=True)

    def test_actual_zero_committed_destructions_skip_the_choice_and_mana(self):
        game = self.session(61401803)
        spell = self.add(game.engine, RITUAL, zone="hand")
        durable = self.add(game.engine, "Darksteel Relic", seat="B")
        self.add(game.engine, "Tree of Tales", seat="C")
        self.start(game, spell, durable)
        self.cast(game, "A", spell)
        before = dict(game.state.players["A"].mana_pool)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(before, game.state.players["A"].mana_pool)
        self.assertEqual("battlefield", game.state.cards[durable.object_id].zone)
        self.replay(game, load=True)

    def test_whole_original_requires_exact_scope_colors_and_committed_authority(self):
        original = self.db.lookup(RITUAL, fuzzy=False)
        report, semantics = register_card(self.db, self.registry, "Culling Ritual")
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        self.assertEqual("capability_closed", report["trust_basis"])
        program, = semantics.programs_for_oracle(original.oracle_id)
        raw, = program.effects
        followup = DestructionFollowup.from_destruction_effect(raw)
        self.assertEqual(3, followup.schema_version)
        self.assertFalse(followup.mana_from_committed_count(0, actor="A", source_ref="spell").effects)
        effect, = followup.mana_from_committed_count(12, actor="A", source_ref="spell").effects
        self.assertEqual(VariableResolutionManaChoiceSpec("combination", ("B", "G"), 12),
                         VariableResolutionManaChoiceSpec.from_effect(effect, resolved=True))
        deps = tuple(cap for cap in program.capability_dependencies if cap != DESTRUCTION_MANA_ALLOCATION_CAPABILITY)
        unbound = replace(program, capability_dependencies=deps,
                          capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
        runtime = bind_semantic_program_runtime(unbound, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(runtime["strict"])
        self.assertIn(f"capability:undeclared_runtime_dependency:{DESTRUCTION_MANA_ALLOCATION_CAPABILITY}", runtime["blockers"])
        for before, after in (
            ("nonland permanent", "creature"),
            ("2 or less", "3 or less"),
            ("{B} or {G}", "{B} or {R}"),
            ("destroyed this way.", "destroyed this way. Change the name of each card in your hand."),
        ):
            with self.subTest(after=after):
                changed = replace(original, oracle_text=original.oracle_text.replace(before, after))
                self.assertNotEqual("exact", compile_oracle_card(
                    changed, capability_registry=self.registry, capability_profile=PROFILE,
                ).status)
        with patch("quorune.compiler.destruction_followup_templates.committed_black_green_mana_template", return_value=None):
            self.assertNotEqual("exact", compile_oracle_card(
                original, capability_registry=self.registry, capability_profile=PROFILE,
            ).status)
