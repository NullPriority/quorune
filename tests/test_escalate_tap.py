"""Exercise the original Escalate spell through the ordinary cast and payment paths."""

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.escalate_tap_model import ESCALATE_TAP_CAPABILITY, ESCALATE_TAP_FIELD
from quorune.haste import is_summoning_sick
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


EFFORT = "Collective Effort"
PROFILE = "commander_review"


class EscalateTapTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "escalate-tap.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([
            fixtures / "nonartifact-nonblack-mass-destruction-cards.json",
            fixtures / "grouped-choose-one-cards.json",
            fixtures / "escalate-tap-cards.json",
        ], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Escalate native proof", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def test_complete_original_retains_ordered_modes_and_infers_its_payment_owner(self):
        card = self.db.lookup(EFFORT, fuzzy=False)
        args = dict(capability_registry=self.registry, capability_profile=PROFILE)
        ir = compile_oracle_card(card, **args)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        ready, semantics = register_card(self.db, self.registry, EFFORT)
        self.assertTrue(ready["strict_capability_ready"], ready["blockers"])
        program, = semantics.programs_for_oracle(card.oracle_id)
        self.assertIn(ESCALATE_TAP_CAPABILITY, program.capability_dependencies)
        self.assertEqual(["mode_1", "mode_2", "mode_3"], program.cost_schema[ESCALATE_TAP_FIELD]["mode_ids"])
        self.assertEqual((1, 3), (program.target_schema["min_modes"], program.target_schema["max_modes"]))
        deps = tuple(dep for dep in program.capability_dependencies if dep != ESCALATE_TAP_CAPABILITY)
        other = deepcopy(program.cost_schema)
        other[ESCALATE_TAP_FIELD]["mode_ids"].reverse()
        for forged in (
            replace(program, cost_schema=other),
            replace(program, capability_dependencies=deps,
                    capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict()),
        ):
            self.assertFalse(bind_semantic_program_runtime(
                forged, capability_registry=self.registry, profile=PROFILE,
            )["strict"])
        for before, after in (("Tap an untapped creature", "Sacrifice a creature"),
                              ("Choose one or more", "Choose two or more")):
            with self.subTest(after=after):
                changed = replace(card, oracle_text=card.oracle_text.replace(before, after))
                self.assertNotEqual("exact", compile_oracle_card(changed, **args).status)

    def accept(self, game, seat, command):
        result = game.act("pilot:" + seat, command)
        self.assertTrue(result.ok, result.summary)

    def reject(self, game, seat, command):
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:" + seat, command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def scene(self, seed):
        game = self.session(seed)
        spell = self.add(game.engine, EFFORT, zone="hand")
        fresh = self.add(game.engine, "Ornithopter", zone="hand", ref="escalate-new-creature")
        ready = self.add(game.engine, "Grizzly Bears", ref="escalate-ready-creature")
        tapped = self.add(game.engine, "Grizzly Bears", ref="escalate-already-tapped")
        tapped.tapped = True
        large = self.add(game.engine, "Grizzly Bears", seat="B", ref="escalate-large-target")
        large.counters["+1/+1"] = 2
        remaining = self.add(game.engine, "Grizzly Bears", seat="B", ref="escalate-counter-recipient")
        enchantment = self.add(game.engine, "Hardened Scales", seat="B")
        for card in (spell, fresh, enchantment):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 1, "W": 2})
        self.checkpoint(game)
        self.cast_for(game, "A", fresh)
        self.resolve_top(game)
        self.assertEqual("battlefield", game.state.cards[fresh.object_id].zone)
        self.assertTrue(is_summoning_sick(game.engine, game.state.cards[fresh.object_id]))
        return game, spell, fresh, ready, tapped, large, remaining, enchantment

    def claim(self, game, spell, modes):
        offered = self.action(game, "A", spell)
        suffix = "+escalate:" + ",".join(modes)
        price, = [row for row in offered["cost_options"] if row["id"] == "normal" + suffix]
        return offered, price

    def prove_three_modes(self):
        game, spell, fresh, ready, tapped, large, remaining, enchantment = self.scene(75179911)
        modes = ["mode_1", "mode_2", "mode_3"]
        offered, price = self.claim(game, spell, modes)
        payment = price.get("choice_schema", {}).get("tap_cost_cards", {})
        self.assertEqual((2, 2), (payment.get("minimum"), payment.get("maximum")))
        self.assertEqual({fresh.ref, ready.ref}, set(payment["legal_refs"]))
        command = {"action_id": offered["id"], "cost_option": price["id"], "pay": "auto",
                   "modes": list(reversed(modes)), "targets": [large.ref, enchantment.ref, "B"],
                   "tap_cost_cards": [fresh.ref, ready.ref]}
        self.reject(game, "D", command)
        for wrong in ([fresh.ref], [fresh.ref, fresh.ref], [ready.ref, tapped.ref], [ready.ref, remaining.ref]):
            self.reject(game, "A", {**command, "tap_cost_cards": wrong})
        self.reject(game, "A", {**command, "targets": [enchantment.ref, large.ref, "B"]})
        self.accept(game, "A", command)
        self.assertEqual(modes, game.state.stack[-1].modes)
        self.assertTrue(game.state.cards[fresh.object_id].tapped)
        self.assertTrue(game.state.cards[ready.object_id].tapped)
        self.assertFalse(game.state.cards[remaining.object_id].tapped)
        self.assertEqual(0, sum(game.state.players["A"].mana_pool.values()))
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[large.object_id].zone)
        self.assertEqual("graveyard", game.state.cards[enchantment.object_id].zone)
        self.assertEqual({"+1/+1": 1}, game.state.cards[remaining.object_id].counters)
        self.assertEqual({}, game.state.cards[ready.object_id].counters)
        self.replay(game, load=True)

    def test_all_three_modes_pay_two_distinct_creatures_including_a_new_permanent_and_replay(self):
        self.prove_three_modes()

    def test_one_or_two_modes_pay_zero_or_one_and_ignore_the_unselected_effects(self):
        for two in (False, True):
            with self.subTest(two=two):
                game, spell, fresh, ready, tapped, large, remaining, enchantment = self.scene(75179921 + two)
                modes = ["mode_2", "mode_3"] if two else ["mode_2"]
                offered, price = self.claim(game, spell, modes)
                payment = price.get("choice_schema", {}).get("tap_cost_cards")
                self.assertEqual(1 if two else None, payment.get("minimum") if payment is not None else None)
                command = {"action_id": offered["id"], "cost_option": price["id"], "pay": "auto",
                           "modes": modes, "targets": [enchantment.ref, "B"] if two else [enchantment.ref]}
                self.reject(game, "A", {**command, "tap_cost_cards": [ready.ref, fresh.ref] if two else [ready.ref]})
                if two:
                    command["tap_cost_cards"] = [ready.ref]
                self.accept(game, "A", command)
                self.assertEqual(two, game.state.cards[ready.object_id].tapped)
                self.assertFalse(game.state.cards[fresh.object_id].tapped)
                self.resolve_top(game)
                self.assertEqual("battlefield", game.state.cards[large.object_id].zone)
                self.assertEqual("graveyard", game.state.cards[enchantment.object_id].zone)
                self.assertEqual(int(two), game.state.cards[remaining.object_id].counters.get("+1/+1", 0))
                self.replay(game, load=True)

    def test_mutation_that_waives_the_extra_taps_fails_the_native_three_mode_quote(self):
        with patch("quorune.escalate_tap_model.EscalateTapModeSpec.tap_count", return_value=0):
            with self.assertRaises(AssertionError):
                self.prove_three_modes()
