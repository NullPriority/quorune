"""The controlled-creature counter resolves within the same ability before Proliferate."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.counter_placement_targets import controlled_creature_counter_choice_effect
from quorune.deck import DeckDefinition, DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


ORIGINAL = "Sword of Truth and Justice"


class EquippedCounterProliferateTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "equipped-counter-proliferate.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/equipped-counter-proliferate-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Equipped counter and Proliferate", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_original_preserves_untargeted_choice_then_ordinary_proliferate(self):
        result, semantics = register_card(self.db, self.registry, ORIGINAL)
        self.assertTrue(result["strict_capability_ready"], result["blockers"])
        record = self.db.lookup(ORIGINAL, fuzzy=False)
        trigger, = (row for row in semantics.programs_for_oracle(record.oracle_id) if row.event == "damage.dealt")
        self.assertIsNone(trigger.target_schema)
        first, second = trigger.effects
        self.assertEqual(controlled_creature_counter_choice_effect(), first)
        self.assertEqual("proliferate", second["op"])
        self.assertIn("resolution.effect_sequence.fixed_clauses", trigger.capability_dependencies)
        for effects in ([second, first], [first], [dict(first, minimum=0), second]):
            self.assertFalse(bind_semantic_program_runtime(
                replace(trigger, effects=effects), capability_registry=self.registry, profile="commander_review",
            )["strict"])

    def scene(self, seed, *, minus=False, removal=False):
        game = self.session(seed)
        sword = self.add(game.engine, ORIGINAL, zone="hand")
        carrier = self.add(game.engine, "Runeclaw Bear", ref="truth-justice-carrier")
        foreign = self.add(game.engine, "Runeclaw Bear", seat="C", ref="truth-justice-foreign")
        carrier.acquired_control_turn_count = -1
        if minus:
            carrier.counters["-1/-1"] = 1
        destroy = self.add(game.engine, "Doom Blade", seat="B", zone="hand") if removal else None
        if removal:
            self.add(game.engine, "Swamp", seat="B")
            self.add(game.engine, "Wastes", seat="B")
            self.assert_closed(game, destroy)
        game.state.players["B"].poison = 1
        self.assert_closed(game, sword)
        self.prepare(game, mana={"C": 5})
        self.checkpoint(game)
        self.cast_for(game, "A", sword, reserve={"C": 2})
        self.resolve_top(game)
        self.priority(game, "A")
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        equip, = [row for row in actions if row["id"].startswith(f"activate:{sword.ref}:")]
        result = game.act("pilot:A", {"action_id": equip["id"], "targets": [carrier.ref], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.resolve_top(game)
        self.assertEqual(carrier.object_id, game.state.cards[sword.object_id].attached_to)
        self.attack(game, [(carrier, "B")])
        self.until(game, lambda current: self.trigger_count(current, sword) == 1)
        self.assertEqual(37 if minus else 36, game.state.players["B"].life)
        self.assertFalse(game.state.stack[-1].targets)
        return game, carrier, foreign, destroy

    def choice(self, game, expected):
        desc = game.packet("pilot:A", full=True)["decision"]
        self.assertEqual("semantic.choice", desc["kind"])
        self.assertEqual(expected, desc["ctx"]["operation"])
        return desc["ctx"]

    def choose(self, game, objects):
        command = {"action_id": "choose", "objects": list(objects)}
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)

    def test_actual_fresh_counter_becomes_eligible_for_proliferate_in_the_same_resolution(self):
        game, carrier, foreign, _ = self.scene(70134081)
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        context = self.choice(game, "choose_objects")
        self.assertEqual([carrier.ref], [row["id"] for row in context["objects"]])
        before = authoritative_state_hash(game.state)
        for actor, picks in (("B", [carrier.ref]), ("A", [foreign.ref]), ("A", [])):
            self.assertFalse(game.act(f"pilot:{actor}", {"action_id": "choose", "objects": picks}).ok)
            self.assertEqual(before, authoritative_state_hash(game.state))
        game = self.replay(game, load=True)
        self.choose(game, [carrier.ref])
        self.choice(game, "proliferate")
        self.assertEqual({"+1/+1": 1}, game.state.cards[carrier.object_id].counters)
        game = self.replay(game, load=True)
        self.choose(game, [carrier.ref, "B"])
        self.assertIsNone(self.resolve(game))
        self.assertEqual({"+1/+1": 2}, game.state.cards[carrier.object_id].counters)
        self.assertEqual(2, game.state.players["B"].poison)
        self.replay(game, load=True)

    def test_actual_opposing_counter_pair_remains_through_both_instructions_until_state_actions(self):
        game, carrier, _, _ = self.scene(70134082, minus=True)
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        self.choose(game, [carrier.ref])
        self.choice(game, "proliferate")
        self.assertEqual({"+1/+1": 1, "-1/-1": 1}, game.state.cards[carrier.object_id].counters)
        game = self.replay(game, load=True)
        before = len(game.state.events)
        self.choose(game, [carrier.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual({"+1/+1", "-1/-1"}, {
            event.details["counter"] for event in game.state.events[before:]
            if event.code == "counter.add" and event.details.get("object") == carrier.ref
        })
        self.assertFalse(game.state.cards[carrier.object_id].counters)
        self.replay(game, load=True)

    def test_actual_last_creature_departure_still_permits_proliferate(self):
        game, carrier, _, destroy = self.scene(70134083, removal=True)
        self.cast_for(game, "B", destroy, [carrier.ref])
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[carrier.object_id].zone)
        game = self.replay(game, load=True)
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        self.choice(game, "proliferate")
        self.choose(game, ["B"])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(2, game.state.players["B"].poison)
        self.replay(game, load=True)
