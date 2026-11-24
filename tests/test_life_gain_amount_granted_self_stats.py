"""The printed Equipment grant uses the exact positive life-gain occurrence."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.scalar_effect_amount_model import ScalarAmountOrigin, ScalarEffectAmountSpec
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


NAME = "Field-Tested Frying Pan"
AMOUNT = "quantity_expression.scalar_effect_amount"
PROFILE = "commander_review"
FIXTURES = ROOT / "tests/fixtures"


class LifeGainAmountGrantedSelfStatsTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        base = json.loads((FIXTURES / "bound-effect-program-cards.json").read_bytes())
        base["cards"] = [row for row in base["cards"] if row["name"] == "Generic Bound Plains"]
        base["rulings"] = []
        basic = folder / "base.json"
        basic.write_text(json.dumps(base), encoding="utf-8")
        path = folder / "life-gain-source-growth.sqlite3"
        build_fixture_database([basic, FIXTURES / "life-gain-amount-granted-self-stats-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Life-gain granted source growth", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def program(self):
        original = self.db.lookup(NAME, fuzzy=False)
        entry, semantics = register_card(self.db, self.registry, NAME)
        self.assertTrue(entry["strict_capability_ready"], entry["blockers"])
        programs = semantics.programs_for_oracle(original.oracle_id)
        ability, = [part for part in programs if part.event == "life.gained"]
        return original, programs, ability

    def test_complete_original_uses_one_committed_gain_for_both_stats_and_keeps_both_siblings(self):
        original, programs, ability = self.program()
        self.assertTrue(any(part.event == "permanent.enter.self" for part in programs))
        self.assertTrue(any(part.event == "activate" for part in programs))
        self.assertEqual({"field": "player", "op": "eq", "value": "$source.controller"}, ability.event_condition)
        effect, = ability.effects
        self.assertEqual("$source.zone_object", effect["card"])
        expected = ScalarEffectAmountSpec(ScalarAmountOrigin.EVENT_AMOUNT)
        self.assertEqual(expected, ScalarEffectAmountSpec.from_dict(effect["power"]))
        self.assertEqual(expected, ScalarEffectAmountSpec.from_dict(effect["toughness"]))
        for text in (
            original.oracle_text.replace("Whenever you gain life", "Whenever an opponent gains life"),
            original.oracle_text.replace("this creature gets +X/+X", "target creature gets +X/+X"),
            original.oracle_text.replace("+X/+X", "+X/+0"),
            original.oracle_text.replace("life you gained.", "life target player gained."),
        ):
            ir = compile_oracle_card(replace(original, oracle_text=text), capability_registry=self.registry,
                                     capability_profile=PROFILE)
            self.assertFalse(any(AMOUNT in node.capability_dependencies for face in ir.faces for node in face.nodes), text)

    def test_raw_granted_growth_requires_the_existing_scalar_authority(self):
        _, _, program = self.program()
        dependencies = [cap for cap in program.capability_dependencies if cap != AMOUNT]
        changed = replace(program, capability_dependencies=dependencies,
                          capability_closure=self.registry.closure(dependencies, profile=PROFILE).to_dict())
        binding = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(binding["strict"], binding)
        self.assertIn("capability:undeclared_runtime_dependency:" + AMOUNT, binding["blockers"])

    def cast_pan(self, game, pan, *, reserve=None):
        self.cast_for(game, "A", pan, reserve=reserve)
        self.assertIsNone(self.resolve(game))
        tokens = {game.engine.display_name(item.object_id): item for item in game.state.cards.values()
                  if item.is_token and item.zone == "battlefield" and item.controller == "A"}
        self.assertEqual({"Food Token", "Halfling Token"}, tokens.keys())
        halfling, food = tokens["Halfling Token"], tokens["Food Token"]
        self.assertEqual(halfling.object_id, game.state.cards[pan.object_id].attached_to)
        self.assertIn(pan.object_id, halfling.attachments)
        return halfling, food

    def activate(self, game, source, *, targets=(), reserve=None):
        self.priority(game, "A")
        offers = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        action, = [row for row in offers if row["id"].startswith(f"activate:{source.ref}:")]
        result = game.act("pilot:A", {"action_id": action["id"], "targets": list(targets),
                                       "pay": "auto", "reserve": reserve or {}})
        self.assertTrue(result.ok, result.summary)
        self.resolve_top(game)

    def stats(self, game, card):
        return tuple(game.engine._numeric_stat(card.object_id, stat) for stat in ("power", "toughness"))

    def test_actual_food_gain_original_receiver_reattachment_and_controller_gain_then_cleanup(self):
        game = self.session(119090501)
        engine = game.engine
        pan = self.add(engine, NAME, zone="hand")
        reflection = self.add(engine, "Boon Reflection")
        theft = self.add(engine, "Magnetic Theft", zone="hand")
        own_gain = self.add(engine, "Revitalize", zone="hand", ref="pan-remaining-own-life")
        their_gain = self.add(engine, "Revitalize", seat="B", zone="hand", ref="pan-other-life")
        bolt = self.add(engine, "Lightning Bolt", seat="B", zone="hand")
        foreign = self.add(engine, "Wind Drake", seat="B")
        bear = self.add(engine, "Grizzly Bears")
        private = self.add(engine, "Counterspell", seat="C", zone="hand", ref="pan-private")
        for card in (pan, reflection, theft, own_gain, their_gain, bolt):
            self.assert_closed(game, card)
        for object_id in tuple(game.state.players["A"].zones["hand"]):
            if game.state.cards[object_id].printed_name == "Generic Bound Plains":
                engine.move_card(object_id, "library", log=False)
        self.prepare(game, mana={"C": 7, "W": 2, "R": 1})
        game.state.players["B"].mana_pool.update({"C": 1, "W": 1, "R": 1})
        self.checkpoint(game)

        halfling, food = self.cast_pan(game, pan, reserve={"C": 5, "W": 1, "R": 1})
        self.activate(game, food, reserve={"C": 3, "W": 1, "R": 1})
        granted, = game.state.stack
        self.assertEqual((halfling.object_id, "A", 6), (granted.source_object_id, granted.controller, granted.context["amount"]))
        self.assertEqual(46, game.state.players["A"].life)
        self.assertEqual("outside", game.state.cards[food.object_id].zone)
        self.cast_for(game, "B", bolt, ["A"], reserve={"C": 1, "W": 1})
        self.priority(game, "A")
        offer = self.action(game, "A", theft)
        command = {"action_id": offer["id"], "pay": "auto", "reserve": {"C": 3, "W": 1},
                   "targets": {"target_0": [pan.ref], "target_1": [foreign.ref]}}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        game = self.replay(game, load=True)
        moved = game.act("pilot:A", command)
        self.assertTrue(moved.ok, moved.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(43, game.state.players["A"].life)
        self.assertEqual(foreign.object_id, game.state.cards[pan.object_id].attached_to)
        self.assertEqual("A", game.state.cards[pan.object_id].controller)
        self.assertEqual((7, 7), self.stats(game, halfling))
        self.assertEqual((2, 2), self.stats(game, foreign))

        self.cast_for(game, "A", own_gain, reserve={"C": 2})
        self.assertIsNone(self.resolve(game))
        self.assertEqual((2, 2), self.stats(game, foreign))
        self.cast_for(game, "B", their_gain)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(43, game.state.players["B"].life)
        self.assertEqual((5, 5), self.stats(game, foreign))
        self.activate(game, pan, targets=[bear.ref])
        self.assertEqual(bear.object_id, game.state.cards[pan.object_id].attached_to)
        self.assertEqual((2, 2), self.stats(game, bear))
        self.assertEqual((5, 5), self.stats(game, foreign))
        turn = game.state.turn_sequence
        self.until(game, lambda current: current.state.turn_sequence > turn)
        self.assertEqual((1, 1), self.stats(game, halfling))
        self.assertEqual((2, 2), self.stats(game, foreign))
        for principal in ("pilot:A", "pilot:B", "pilot:D", "spectator"):
            self.assertNotIn(private.ref, json.dumps(game.packet(principal, full=True)))
        self.replay(game, load=True)

    def test_actual_blinked_recipient_never_inherits_the_old_granted_trigger(self):
        game = self.session(119090511)
        engine = game.engine
        pan = self.add(engine, NAME, zone="hand")
        bear = self.add(engine, "Grizzly Bears")
        gain = self.add(engine, "Revitalize", zone="hand")
        blink = self.add(engine, "Ephemerate", zone="hand")
        self.prepare(game, mana={"C": 5, "W": 3})
        self.checkpoint(game)
        self.cast_pan(game, pan, reserve={"C": 3, "W": 2})
        self.activate(game, pan, targets=[bear.ref], reserve={"C": 1, "W": 2})
        self.cast_for(game, "A", gain, reserve={"W": 1})
        self.resolve_top(game)
        old = game.state.cards[bear.object_id].logical_object_id
        trigger, = game.state.stack
        self.assertEqual(bear.object_id, trigger.source_object_id)
        self.cast_for(game, "A", blink, [bear.ref])
        self.assertIsNone(self.resolve(game))
        self.assertNotEqual(old, game.state.cards[bear.object_id].logical_object_id)
        self.assertEqual((2, 2), self.stats(game, bear))
        self.assertIsNone(game.state.cards[pan.object_id].attached_to)
        self.replay(game, load=True)

    def test_actual_food_receipt_kills_using_requested_life_instead_of_committed_life(self):
        with patch("quorune.semantic_runtime.values.resolve_scalar_effect_amount", return_value=3):
            with self.assertRaisesRegex(AssertionError, r"\(7, 7\).*\(4, 4\)"):
                self.test_actual_food_gain_original_receiver_reattachment_and_controller_gain_then_cleanup()
