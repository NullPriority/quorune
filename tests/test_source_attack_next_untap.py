"""The printed source attack restricts only that permanent's next controller untap."""

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
from quorune.rules.capabilities import load_default_capability_registry
from quorune.tap_state import NEXT_UNTAP_PROHIBITION_ANNOTATION
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card
from test_qualified_zone_event_queries import query_record


APES = "Apes of Rath"
ORACLE_ID = "b52f5850-f1aa-412a-bae9-2ce76c287188"
PRINTED = "Whenever this creature attacks, it doesn't untap during its controller's next untap step."
EFFECT = {"op": "prohibit_next_untap", "card": "$source.zone_object"}
SELF_ATTACK = {"field": "card", "op": "eq", "value": "$source.ref"}
UNTAP_CAPABILITY = "untap.step.prohibit_next_controller"
ATTACK_CAPABILITY = "trigger.event.normalized_self_attack"


class SourceAttackNextUntapCompilerTests(unittest.TestCase):
    def test_complete_source_attack_next_untap_and_unsupported_neighbors(self):
        registry = load_default_capability_registry()
        source = query_record(PRINTED)
        ir = compile_oracle_card(source, capability_registry=registry)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        self.assertEqual(PRINTED, node.text)
        self.assertEqual((0, len(PRINTED), 1), (node.span.start, node.span.end, node.span.line))
        self.assertEqual("creature.attacks", node.event)
        self.assertEqual("battlefield", node.active_zone)
        self.assertEqual(SELF_ATTACK, node.event_condition)
        self.assertIsNone(node.target_schema)
        self.assertEqual((EFFECT,), node.effects)
        self.assertLessEqual({ATTACK_CAPABILITY, UNTAP_CAPABILITY}, set(node.capability_dependencies))
        for changed in (
            PRINTED.replace("this creature attacks", "a creature you control attacks"),
            PRINTED.replace("this creature attacks", "this creature attacks alone"),
            PRINTED.replace("its controller's", "its owner's"),
            PRINTED.replace("next untap step", "next two untap steps"),
            PRINTED.replace("it doesn't", "you may have it not"),
            PRINTED + " Draw a card.",
        ):
            with self.subTest(changed=changed):
                result = compile_oracle_card(replace(source, oracle_text=changed), capability_registry=registry)
                self.assertNotEqual("exact", result.status)

        targeted = PRINTED.replace("it doesn't", "target creature doesn't")
        targeted_ir = compile_oracle_card(replace(source, oracle_text=targeted), capability_registry=registry)
        self.assertEqual("exact", targeted_ir.status, targeted_ir.material_residuals)
        targeted_node, = targeted_ir.faces[0].nodes
        self.assertIsNotNone(targeted_node.target_schema)
        self.assertEqual(({"op": "prohibit_next_untap", "card": "$target.0"},), targeted_node.effects)


class SourceAttackNextUntapNativeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        fixtures = ROOT / "tests/fixtures"
        path = Path(cls.temporary.name) / "source-attack-next-untap.sqlite3"
        build_fixture_database([
            fixtures / "source-attack-next-untap-cards.json",
            fixtures / "source-attack-alone-cards.json",
            fixtures / "controlled-attacker-delayed-phase-out-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Source attack next untap", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_normal_whole_card_and_runtime_reject_wrong_or_missing_authority(self):
        card = self.db.lookup(APES, fuzzy=False)
        self.assertEqual((ORACLE_ID, PRINTED, "{2}{G}{G}", "5", "4"),
                         (card.oracle_id, card.oracle_text, card.mana_cost, card.power, card.toughness))
        bound, semantics = register_card(self.db, self.registry, APES)
        self.assertTrue(bound["strict_capability_ready"], bound["blockers"])
        program, = semantics.programs_for_oracle(ORACLE_ID)
        self.assertEqual((EFFECT,), tuple(program.effects))
        self.assertIsNone(program.target_schema)
        self.assertIsNone(program.cost_schema)
        self.assertIsNone(program.trigger_limit)
        for changed in (
            replace(program, event="creature.becomes_blocked"),
            replace(program, active_zone="graveyard"),
            replace(program, event_condition=None),
            replace(program, target_schema={"zones": ["battlefield"], "count": 1}),
            replace(program, effects=({**EFFECT, "step_count": 2},)),
        ):
            result = bind_semantic_program_runtime(changed, capability_registry=self.registry,
                                                  profile="commander_review")
            self.assertFalse(result["strict"], result)
        for capability in (ATTACK_CAPABILITY, UNTAP_CAPABILITY):
            deps = tuple(key for key in program.capability_dependencies if key != capability)
            changed = replace(program, capability_dependencies=deps,
                              capability_closure=self.registry.closure(deps, profile="commander_review").to_dict())
            result = bind_semantic_program_runtime(changed, capability_registry=self.registry,
                                                  profile="commander_review")
            self.assertFalse(result["strict"], result)
            self.assertIn("capability:undeclared_runtime_dependency:" + capability, result["blockers"])

        deps = (*program.capability_dependencies, "trigger.usage.once_per_turn")
        limited = replace(program, trigger_limit={"schema_version": 1, "kind": "once_per_turn"},
                          capability_dependencies=deps,
                          capability_closure=self.registry.closure(deps, profile="commander_review").to_dict())
        result = bind_semantic_program_runtime(limited, capability_registry=self.registry,
                                              profile="commander_review")
        self.assertFalse(result["strict"], result)
        self.assertIn("runtime_source_tap_duration:invalid:The source next-untap restriction requires one unqualified source attack",
                      result["blockers"])

    def card(self, game, source):
        return game.state.cards[source.object_id]

    def restriction(self, game, source):
        return self.card(game, source).annotations.get(NEXT_UNTAP_PROHIBITION_ANNOTATION)

    def next_upkeep(self, game):
        previous = game.state.current_turn.turn_id
        self.until(game, lambda current: current.state.current_turn.turn_id != previous
                   and current.state.step == "upkeep")

    def upkeep_of(self, game, seat):
        for _ in game.state.players:
            self.next_upkeep(game)
            if game.state.active_player == seat:
                return
        self.fail(f"No upkeep for active seat {seat}")

    def prepare_witness(self, game, mana):
        # Keep the multi-turn witness focused on the ordinary untap decisions.
        for player in game.state.players.values():
            for object_id in tuple(player.zones["hand"])[:-3]:
                game.engine.move_card(object_id, "library", position="bottom",
                                      log=False, semantic_events=False)
        self.prepare(game, mana=mana)
        self.checkpoint(game)

    def cast_scene(self, seed, *, blink=False):
        game = self.session(seed)
        source = self.add(game.engine, APES, zone="hand")
        other = self.add(game.engine, "Grizzly Bears")
        self.add(game.engine, "Fervor")
        private = self.add(game.engine, "Counterspell", seat="D", zone="hand", ref="apes-unrelated-private")
        cloudshift = self.add(game.engine, "Cloudshift", zone="hand") if blink else None
        if cloudshift:
            self.add(game.engine, "Plains")
            self.assert_closed(game, cloudshift)
        self.assert_closed(game, source)
        self.prepare_witness(game, {"C": 2, "G": 2})
        self.cast_for(game, "A", source, [])
        self.resolve_top(game)
        return game, source, other, private, cloudshift

    def test_actual_attack_skips_one_own_untap_and_other_attackers_untap(self):
        game, source, other, private, _ = self.cast_scene(50250831)
        self.attack(game, [(source, "B"), (other, "C")])
        self.assertEqual(1, self.trigger_count(game, source))
        self.assertEqual(0, self.trigger_count(game, other))
        self.assertTrue(self.card(game, source).tapped)
        self.assertTrue(self.card(game, other).tapped)
        self.assertIsNone(self.restriction(game, source))
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertIs(self.restriction(game, source), True, "Apes must receive its original untap restriction")
        self.assertIsNone(self.restriction(game, other))
        for viewer in ("pilot:A", "pilot:B", "pilot:C", "spectator"):
            self.assertNotIn(private.ref, json.dumps(game.packet(viewer, full=True)))
        self.next_upkeep(game)
        self.assertNotEqual("A", game.state.active_player)
        self.assertIs(self.restriction(game, source), True)
        self.upkeep_of(game, "A")
        self.assertTrue(self.card(game, source).tapped)
        self.assertFalse(self.card(game, other).tapped)
        self.assertIsNone(self.restriction(game, source))
        self.upkeep_of(game, "A")
        self.assertFalse(self.card(game, source).tapped)
        self.replay(game, load=True)

    def test_actual_other_attacker_creates_no_source_trigger_or_restriction(self):
        game, source, other, _, _ = self.cast_scene(50250832)
        self.attack(game, [(other, "B")])
        self.assertEqual(0, self.trigger_count(game, source))
        self.assertFalse(self.card(game, source).tapped)
        self.assertIsNone(self.restriction(game, source))
        self.assertIsNone(self.restriction(game, other))
        self.replay(game, load=True)

    def test_actual_control_return_uses_new_controllers_physical_untap(self):
        game = self.session(50250833)
        source = self.add(game.engine, APES, seat="B")
        theft = self.add(game.engine, "Act of Treason", zone="hand")
        self.assert_closed(game, source)
        self.assert_closed(game, theft)
        self.prepare_witness(game, {"C": 2, "R": 1})
        original = self.card(game, source).logical_object_id
        self.cast_for(game, "A", theft, [source.ref])
        self.resolve_top(game)
        self.assertEqual("A", self.card(game, source).controller)
        self.attack(game, [(source, "C")])
        self.assertEqual(1, self.trigger_count(game, source))
        self.resolve_top(game)
        self.assertIs(self.restriction(game, source), True)
        self.upkeep_of(game, "B")
        self.assertEqual(("B", original), (self.card(game, source).controller,
                                          self.card(game, source).logical_object_id))
        self.assertTrue(self.card(game, source).tapped)
        self.assertIsNone(self.restriction(game, source))
        self.upkeep_of(game, "B")
        self.assertFalse(self.card(game, source).tapped)
        self.replay(game, load=True)

    def test_actual_source_blink_leaves_stacked_trigger_and_unrestricted_new_object(self):
        game, source, _, _, cloudshift = self.cast_scene(50250834, blink=True)
        original = self.card(game, source).logical_object_id
        self.attack(game, [(source, "B")])
        self.assertEqual(1, self.trigger_count(game, source))
        self.cast_for(game, "A", cloudshift, [source.ref])
        self.resolve_top(game)
        self.assertEqual(1, self.trigger_count(game, source))
        self.assertNotEqual(original, self.card(game, source).logical_object_id)
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual("battlefield", self.card(game, source).zone)
        self.assertFalse(self.card(game, source).tapped)
        self.assertIsNone(self.restriction(game, source))
        self.replay(game, load=True)

    def test_canonical_untap_producer_omission_is_detected_by_the_actual_attack(self):
        with patch("quorune.tap_state.prohibit_next_untap", return_value=None):
            with self.assertRaisesRegex(AssertionError, "Apes must receive its original untap restriction"):
                self.test_actual_attack_skips_one_own_untap_and_other_attackers_untap()
