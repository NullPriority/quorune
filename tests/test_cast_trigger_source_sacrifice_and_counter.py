"""CR 608.2c and 609.3 keep original sacrifice and cast-counter attempts independent."""

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
from quorune.rules.event_outcome_shapes import CAST_TRIGGER_SACRIFICE_COUNTER_CAPABILITY as CAPABILITY
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


HESITATION = "Hesitation"
LUNAR = "Lunar Force"
PROFILE = "commander_review"
FIXTURES = ROOT / "tests/fixtures"


class CastTriggerSacrificeCounterTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        base = json.loads((FIXTURES / "bound-effect-program-cards.json").read_bytes())
        base["cards"] = [row for row in base["cards"] if row["name"] == "Generic Bound Plains"]
        base["rulings"] = []
        basic = folder / "base.json"
        basic.write_text(json.dumps(base), encoding="utf-8")
        path = folder / "cast-counter-sacrifice.sqlite3"
        build_fixture_database([basic, FIXTURES / "cast-trigger-source-sacrifice-and-counter-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Cast counter with source sacrifice", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def program(self, name):
        record = self.db.lookup(name, fuzzy=False)
        result, semantics = register_card(self.db, self.registry, name)
        self.assertTrue(result["strict_capability_ready"], result["blockers"])
        program, = semantics.programs_for_oracle(record.oracle_id)
        return record, program

    def test_complete_originals_keep_both_independent_curated_instructions(self):
        for name, condition in (
            (HESITATION, None),
            (LUNAR, {"field": "controller", "op": "ne", "value": "$source.controller"}),
        ):
            record, program = self.program(name)
            self.assertIn(CAPABILITY, program.capability_dependencies)
            self.assertIsNone(program.target_schema)
            self.assertEqual(condition, program.event_condition)
            sacrifice, counter = program.effects
            self.assertEqual(
                ("self_zone_move", "battlefield", "graveyard", 1),
                (sacrifice["op"], sacrifice["origin"], sacrifice["destination"],
                 sacrifice["fixed_source_sacrifice_version"]),
            )
            self.assertEqual({"op": "counter_stack_target", "stack": "$context.stack"}, counter)
            for text in (
                record.oracle_text.replace("and counter", ". If you do, counter"),
                record.oracle_text.replace("sacrifice this enchantment", "you may sacrifice this enchantment"),
                record.oracle_text.replace("counter that spell", "counter target spell"),
                record.oracle_text + " Invent an unsupported instruction.",
            ):
                with self.subTest(name=name, text=text):
                    ir = compile_oracle_card(replace(record, oracle_text=text), capability_registry=self.registry,
                                            capability_profile=PROFILE)
                    self.assertFalse(any(CAPABILITY in node.capability_dependencies
                                         for face in ir.faces for node in face.nodes))

    def test_raw_cast_sequence_requires_both_leaves_order_and_original_event(self):
        _, program = self.program(LUNAR)
        for changed in (
            replace(program, effects=program.effects[::-1]),
            replace(program, effects=program.effects[:1]),
            replace(program, event_condition={"field": "combat", "op": "truthy", "value": True}),
        ):
            result = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile=PROFILE)
            self.assertFalse(result["strict"], result)
        deps = [cap for cap in program.capability_dependencies if cap != CAPABILITY]
        changed = replace(program, capability_dependencies=deps,
                          capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
        result = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(result["strict"], result)
        self.assertIn("capability:undeclared_runtime_dependency:" + CAPABILITY, result["blockers"])

    def test_actual_hesitation_ignores_land_play_and_sacrifices_against_uncounterable_own_spell(self):
        game = self.session(701606090)
        source = self.add(game.engine, HESITATION)
        tyrant = self.add(game.engine, "Carnage Tyrant", zone="hand")
        forest = self.add(game.engine, "Forest", zone="hand")
        self.assert_closed(game, source)
        self.assert_closed(game, tyrant)
        self.prepare(game, mana={"C": 4, "G": 2})
        self.checkpoint(game)
        land = game.act("pilot:A", {"action_id": f"play-land:{forest.ref}"})
        self.assertTrue(land.ok, land.summary)
        self.assertFalse(game.state.stack)
        self.assertEqual("battlefield", game.state.cards[source.object_id].zone)
        self.cast_for(game, "A", tyrant)
        cast, trigger = game.state.stack
        self.assertEqual(cast.ref, trigger.context["stack"])
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        self.assertEqual([cast.ref], [item.ref for item in game.state.stack])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("battlefield", game.state.cards[tyrant.object_id].zone)
        self.replay(game, load=True)

    def test_actual_lunar_counter_survives_source_blink_and_own_response_does_not_trigger(self):
        game = self.session(701606091)
        source = self.add(game.engine, LUNAR)
        bolt = self.add(game.engine, "Lightning Bolt", seat="B", zone="hand")
        blink = self.add(game.engine, "Flicker of Fate", zone="hand")
        private = self.add(game.engine, "Counterspell", seat="C", zone="hand", ref="lunar-private-control")
        self.assert_closed(game, source)
        self.assert_closed(game, blink)
        self.prepare(game, actor="B", active="B", mana={"R": 1})
        game.state.players["A"].mana_pool.update({"C": 1, "W": 1})
        self.checkpoint(game)
        original_source = source.logical_object_id
        self.cast_for(game, "B", bolt, ["D"])
        cast, trigger = game.state.stack
        self.assertEqual(cast.ref, trigger.context["stack"])
        self.cast_for(game, "A", blink, [source.ref])
        self.assertEqual(3, len(game.state.stack))
        self.resolve_top(game)
        self.assertEqual(2, len(game.state.stack))
        remaining = game.state.cards[source.object_id]
        self.assertEqual("battlefield", remaining.zone)
        self.assertNotEqual(original_source, remaining.logical_object_id)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(40, game.state.players["D"].life)
        self.assertEqual("graveyard", game.state.cards[bolt.object_id].zone)
        self.assertEqual("battlefield", game.state.cards[source.object_id].zone)
        for viewer in ("pilot:A", "pilot:B", "pilot:D", "spectator"):
            self.assertNotIn(private.ref, json.dumps(game.packet(viewer, full=True)))
        self.replay(game, load=True)

    def test_actual_two_lunar_forces_both_sacrifice_one_counters_the_original_cast(self):
        game = self.session(701606092)
        first = self.add(game.engine, LUNAR, ref="lunar-first")
        second = self.add(game.engine, LUNAR, ref="lunar-second")
        bolt = self.add(game.engine, "Lightning Bolt", seat="B", zone="hand")
        self.prepare(game, actor="B", active="B", mana={"R": 1})
        self.checkpoint(game)
        self.cast_for(game, "B", bolt, ["D"])
        decision = game.state.pending_decision
        self.assertEqual(("trigger.order", ["A"]), (decision.kind, list(decision.actors)))
        context = game.packet("pilot:A", full=True)["decision"]["ctx"]
        command = {"action_id": "order", "triggers": [row["id"] for row in context["triggers"]]}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        game = self.replay(game, load=True)
        chosen = game.act("pilot:A", command)
        self.assertTrue(chosen.ok, chosen.summary)
        self.assertEqual(3, len(game.state.stack))
        self.resolve_top(game)
        self.assertEqual(1, len(game.state.stack))
        self.assertEqual(1, sum(game.state.cards[card.object_id].zone == "graveyard"
                                for card in (first, second)))
        self.assertEqual("graveyard", game.state.cards[bolt.object_id].zone)
        self.assertIsNone(self.resolve(game))
        self.assertTrue(all(game.state.cards[card.object_id].zone == "graveyard" for card in (first, second)))
        self.assertEqual(40, game.state.players["D"].life)
        self.replay(game, load=True)

    def test_native_witness_kills_counter_gated_on_original_source(self):
        from quorune import engine

        prepare = engine.prepare_stack_resolution_effect

        def require_living_original(host, item, effect):
            if effect.get("op") == "counter_stack_target" and effect.get("stack") == "$context.stack":
                card = host.state.cards.get(item.source_object_id)
                if (card is None or card.zone != "battlefield"
                        or card.logical_object_id != item.context.get("source_logical_object_id")):
                    return None
            return prepare(host, item, effect)

        with patch.object(engine, "prepare_stack_resolution_effect", side_effect=require_living_original):
            with self.assertRaisesRegex(AssertionError, "40 != 37"):
                self.test_actual_lunar_counter_survives_source_blink_and_own_response_does_not_trigger()
