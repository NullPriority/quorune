"""The original source's committed depletion count is read after placement."""

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
from quorune.rules.event_counter_threshold import SOURCE_DEPLETION_THRESHOLD_CAPABILITY as CAPABILITY
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


NAME = "Decree of Silence"
PROFILE = "commander_review"
FIXTURES = ROOT / "tests/fixtures"


class CastCounterAndSourceDepletionThresholdTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        base = json.loads((FIXTURES / "bound-effect-program-cards.json").read_bytes())
        base["cards"] = [row for row in base["cards"] if row["name"] == "Generic Bound Plains"]
        base["rulings"] = []
        basic = folder / "base.json"
        basic.write_text(json.dumps(base), encoding="utf-8")
        path = folder / "source-depletion.sqlite3"
        build_fixture_database([basic, FIXTURES / "cast-counter-and-source-depletion-threshold-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Source depletion", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def programs(self):
        record = self.db.lookup(NAME, fuzzy=False)
        result, semantics = register_card(self.db, self.registry, NAME)
        self.assertTrue(result["strict_capability_ready"], result["blockers"])
        return record, {program.event: program for program in semantics.programs_for_oracle(record.oracle_id)}

    def test_complete_original_keeps_cast_sequence_and_separate_cycling_siblings(self):
        record, programs = self.programs()
        self.assertEqual({"spell.cast", "card.cycled.self", "activate"}, set(programs))
        trigger = programs["spell.cast"]
        self.assertIn(CAPABILITY, trigger.capability_dependencies)
        self.assertIsNone(trigger.target_schema)
        counter, place, sacrifice = trigger.effects
        self.assertEqual({"op": "counter_stack_target", "stack": "$context.stack"}, counter)
        self.assertEqual(("depletion", 1, "$source.zone_object"),
                         (place["counter"], place["amount"], place["card"]))
        self.assertEqual((2, "depletion", 3, "$source.depletion_counter_count"),
                         (sacrifice["schema_version"], sacrifice["counter"],
                          sacrifice["minimum"], sacrifice["observed"]))
        for text in (
            record.oracle_text.replace("an opponent", "a player", 1),
            record.oracle_text.replace("three or more", "exactly three", 1),
            record.oracle_text.replace("depletion counter", "charge counter"),
            record.oracle_text.replace("and put", ". If you do, put", 1),
            record.oracle_text + " Invent an unsupported instruction.",
        ):
            with self.subTest(text=text):
                ir = compile_oracle_card(replace(record, oracle_text=text), capability_registry=self.registry,
                                        capability_profile=PROFILE)
                self.assertFalse(any(CAPABILITY in n.capability_dependencies for f in ir.faces for n in f.nodes)
                                 and ir.status == "exact")

    def test_raw_depletion_result_rejects_unsealed_threshold_body_or_binding(self):
        _, programs = self.programs()
        program = programs["spell.cast"]
        counter, place, threshold = program.effects
        for changed in (
            replace(program, effects=(place, counter, threshold)),
            replace(program, effects=(counter, place, {**threshold, "minimum": 2})),
            replace(program, effects=(counter, place, {**threshold, "observed": 3})),
            replace(program, event_condition=None),
        ):
            result = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile=PROFILE)
            self.assertFalse(result["strict"], result)
        deps = [cap for cap in program.capability_dependencies if cap != CAPABILITY]
        changed = replace(program, capability_dependencies=deps,
                          capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
        result = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(result["strict"], result)
        self.assertIn("capability:undeclared_runtime_dependency:" + CAPABILITY, result["blockers"])

    def test_actual_replacement_order_crosses_the_threshold_after_uncounterable_cast(self):
        game = self.session(701612203)
        source = self.add(game.engine, NAME)
        source.counters["depletion"] = 1
        season = self.add(game.engine, "Doubling Season")
        vorinclex = self.add(game.engine, "Vorinclex, Monstrous Raider")
        verdict = self.add(game.engine, "Supreme Verdict", seat="B", zone="hand")
        private = self.add(game.engine, "Counterspell", seat="C", zone="hand", ref="decree-private")
        for card in (source, season, vorinclex, verdict):
            self.assert_closed(game, card)
        self.prepare(game, actor="B", active="B", mana={"C": 1, "W": 2, "U": 1})
        self.checkpoint(game)
        self.cast_for(game, "B", verdict)
        original_spell, trigger = game.state.stack
        self.assertEqual(original_spell.ref, trigger.context["stack"])
        pending = self.resolve(game)
        self.assertEqual(("replacement.order", ["A"]), (pending.kind, list(pending.actors)))
        self.assertEqual(1, game.state.cards[source.object_id].counters["depletion"])
        self.assertIn(original_spell.ref, [item.ref for item in game.state.stack])
        options = game.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
        option, = [row["id"] for row in options if row["source"] == season.ref]
        command = {"action_id": "choose", "replacement": option}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        game = self.replay(game, load=True)
        selected = game.act("pilot:A", command)
        self.assertTrue(selected.ok, selected.summary)
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        self.assertEqual([original_spell.ref], [item.ref for item in game.state.stack])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[verdict.object_id].zone)
        for viewer in ("pilot:A", "pilot:B", "pilot:D", "spectator"):
            self.assertNotIn(private.ref, json.dumps(game.packet(viewer, full=True)))
        self.replay(game, load=True)

    def test_actual_prohibited_placement_compares_the_existing_count(self):
        for count, destination in ((2, "battlefield"), (3, "graveyard")):
            with self.subTest(count=count):
                game = self.session(701612210 + count)
                source = self.add(game.engine, NAME)
                source.counters["depletion"] = count
                restriction = self.add(game.engine, "Solemnity")
                bolt = self.add(game.engine, "Lightning Bolt", seat="B", zone="hand")
                self.assert_closed(game, source)
                self.assert_closed(game, restriction)
                self.prepare(game, actor="B", active="B", mana={"R": 1})
                self.checkpoint(game)
                self.cast_for(game, "B", bolt, ["D"])
                self.assertIsNone(self.resolve(game))
                self.assertEqual(destination, game.state.cards[source.object_id].zone)
                if destination == "battlefield":
                    self.assertEqual(2, game.state.cards[source.object_id].counters["depletion"])
                self.assertEqual(40, game.state.players["D"].life)
                self.replay(game, load=True)

    def test_actual_blink_preserves_original_counter_but_new_source_gets_no_depletion(self):
        game = self.session(701612214)
        source = self.add(game.engine, NAME)
        source.counters["depletion"] = 2
        blink = self.add(game.engine, "Flicker of Fate", zone="hand")
        bolt = self.add(game.engine, "Lightning Bolt", seat="B", zone="hand")
        self.assert_closed(game, source)
        self.prepare(game, actor="B", active="B", mana={"R": 1})
        game.state.players["A"].mana_pool.update({"C": 1, "W": 1})
        self.checkpoint(game)
        identity = source.logical_object_id
        self.cast_for(game, "B", bolt, ["D"])
        self.cast_for(game, "A", blink, [source.ref])
        self.resolve_top(game)
        self.assertNotEqual(identity, game.state.cards[source.object_id].logical_object_id)
        self.assertIsNone(self.resolve(game))
        current = game.state.cards[source.object_id]
        self.assertEqual("battlefield", current.zone)
        self.assertEqual(0, current.counters.get("depletion", 0))
        self.assertEqual(40, game.state.players["D"].life)
        self.replay(game, load=True)

    def test_actual_cycling_draw_stays_legal_with_no_spell_to_counter(self):
        game = self.session(701612215)
        source = self.add(game.engine, NAME, zone="hand")
        self.assert_closed(game, source)
        self.prepare(game, mana={"C": 4, "U": 2})
        self.checkpoint(game)
        offers = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        action, = [offer for offer in offers if offer["id"].startswith(f"activate:{source.ref}:")]
        hand = set(game.state.players["A"].zones["hand"]) - {source.object_id}
        result = game.act("pilot:A", {"action_id": action["id"], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(1, len(set(game.state.players["A"].zones["hand"]) - hand))
        self.replay(game, load=True)

    def test_native_threshold_witness_kills_preplacement_count_mutation(self):
        from quorune.rules import event_counter_threshold

        with patch.object(event_counter_threshold, "current_source_depletion_count", return_value=2):
            with self.assertRaisesRegex(AssertionError, "'graveyard' != 'battlefield'"):
                self.test_actual_replacement_order_crosses_the_threshold_after_uncounterable_cast()
