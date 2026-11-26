"""The original returned creature is sacrificed at the next end step."""

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
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.reanimation_followup import REANIMATION_SACRIFICE_ONLY_VERSION
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


class ReanimationDelayedSacrificeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "reanimation-delayed-sacrifice.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/reanimation-delayed-sacrifice-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("One original delayed sacrifice", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_original_binds_one_raw_sacrifice_without_a_haste_rider(self):
        admitted, semantics = register_card(self.db, self.registry, "Footsteps of the Goryo")
        self.assertTrue(admitted["strict_capability_ready"], admitted["blockers"])
        card = self.db.lookup("Footsteps of the Goryo", fuzzy=False)
        program, = semantics.programs_for_oracle(card.oracle_id)
        effect, = program.effects
        self.assertEqual((REANIMATION_SACRIFICE_ONLY_VERSION, False, "$target.0", "$controller"),
                         (effect["schema_version"], effect["tapped"], effect["card"], effect["controller"]))
        self.assertEqual({"departure": "sacrifice", "end_step": "next"}, effect["followup"])
        for recoded in ({**effect, "tapped": True},
                        {**effect, "followup": {**effect["followup"], "end_step": "controllers_next"}}):
            self.assertFalse(bind_semantic_program_runtime(
                replace(program, effects=[recoded]), capability_registry=self.registry, profile="commander_review",
            )["strict"])
        changed = replace(card, oracle_text=card.oracle_text.replace("the next end step", "your next end step"))
        self.assertNotEqual("exact", compile_oracle_card(
            changed, capability_registry=self.registry, capability_profile="commander_review",
        ).status)

    def current(self, game, card):
        return game.state.cards[card.object_id]

    def reanimate(self, game, spell, creature, *, reserve=None):
        self.cast_for(game, "A", spell, [creature.ref], reserve=reserve)
        self.assertIsNone(self.resolve(game))
        returned = self.current(game, creature)
        self.assertEqual(("battlefield", "A", False), (returned.zone, returned.controller, returned.tapped))
        self.assertNotIn("haste", game.engine._combat_keywords(returned))
        delayed, = (item for item in game.state.delayed_triggers if item.active)
        self.assertEqual(("A", True), (delayed.controller, delayed.once))
        return returned

    def delayed_sacrifice(self, game):
        self.until(game, lambda current: current.state.step == "end_step"
                   and any(item.label == "Delayed sacrifice" for item in current.state.stack))
        item, = (item for item in game.state.stack if item.label == "Delayed sacrifice")
        self.assertEqual("A", item.controller)
        self.assertIsNone(self.resolve(game))

    def test_actual_return_does_not_make_an_eligible_attacker_and_sacrifices_the_original(self):
        game = self.session(60377001)
        spell = self.add(game.engine, "Footsteps of the Goryo", zone="hand")
        creature = self.add(game.engine, "Grizzly Bears", zone="graveyard", ref="footsteps-original")
        established = self.add(game.engine, "Grizzly Bears", ref="footsteps-ready-witness")
        established.acquired_control_turn_count = -1
        self.assert_closed(game, spell)
        self.prepare(game, mana={"C": 2, "B": 1})
        self.checkpoint(game)
        returned = self.reanimate(game, spell, creature)
        self.until(game, lambda current: current.state.pending_decision is not None
                   and current.state.pending_decision.kind == "combat.attackers")
        before = authoritative_state_hash(game.state)
        illegal = game.act("pilot:A", {"action_id": "attack", "attackers": {returned.ref: "B"}})
        self.assertFalse(illegal.ok, illegal.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))
        game = self.replay(game, load=True)
        self.delayed_sacrifice(game)
        self.assertEqual("graveyard", self.current(game, creature).zone)
        self.assertTrue(any(event.object_incarnation == returned.logical_object_id
                            for event in game.engine._current_turn_history("permanent_sacrificed")))
        self.assertEqual("battlefield", self.current(game, established).zone)
        self.replay(game, load=True)

    def test_actual_blinked_incarnation_is_not_the_original_delayed_sacrifice_subject(self):
        game = self.session(60377002)
        spell = self.add(game.engine, "Footsteps of the Goryo", zone="hand")
        blink = self.add(game.engine, "Cloudshift", zone="hand")
        creature = self.add(game.engine, "Grizzly Bears", zone="graveyard")
        for card in (spell, blink):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 2, "B": 1, "W": 1})
        self.checkpoint(game)
        original = self.reanimate(game, spell, creature, reserve={"W": 1}).logical_object_id
        self.cast_for(game, "A", blink, [self.current(game, creature).ref])
        self.resolve_top(game)
        new = self.current(game, creature).logical_object_id
        self.assertNotEqual(original, new)
        game = self.replay(game, load=True)
        self.delayed_sacrifice(game)
        self.assertEqual(("battlefield", new), (self.current(game, creature).zone, self.current(game, creature).logical_object_id))
        self.replay(game, load=True)

    def test_actual_control_transfer_does_not_transfer_the_delayed_sacrifice_authority(self):
        game = self.session(60377003)
        spell = self.add(game.engine, "Footsteps of the Goryo", zone="hand")
        give = self.add(game.engine, "Donate", zone="hand")
        creature = self.add(game.engine, "Grizzly Bears", zone="graveyard")
        for card in (spell, give):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 4, "B": 1, "U": 1})
        self.checkpoint(game)
        original = self.reanimate(game, spell, creature, reserve={"C": 2, "U": 1}).logical_object_id
        self.cast_for(game, "A", give, ["B", self.current(game, creature).ref])
        self.resolve_top(game)
        self.assertEqual("B", self.current(game, creature).controller)
        self.delayed_sacrifice(game)
        self.assertEqual(("battlefield", "B", original),
                         (self.current(game, creature).zone, self.current(game, creature).controller,
                          self.current(game, creature).logical_object_id))
        self.replay(game, load=True)
