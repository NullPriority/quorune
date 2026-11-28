"""Native command and replay witnesses for Equipment qualified by live block pairs."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.attachment_references import AttachmentReferenceKind
from quorune.attachments import attach_objects
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.card_programs import bind_card_program_runtime
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.compiler.tap_state_templates import targeted_tap_state_effect_template
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card, register_generated_programs
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantic_runtime.attached_combat_strike import EQUIPPED_COMBAT_STRIKE_CAPABILITY
from quorune.semantic_runtime.context import SemanticNodeError
from quorune.semantics import SemanticRegistry
from scripts.build_test_database import build_fixture_database


NAME = "Sting, the Glinting Dagger"
PROFILE = "commander_review"
CAPABILITY = EQUIPPED_COMBAT_STRIKE_CAPABILITY
FIXTURES = ROOT / "tests/fixtures"


class EquippedCombatStrikeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "equipped-combat-strike.sqlite3"
        build_fixture_database([
            FIXTURES / "equipped-combat-strike-cards.json",
            FIXTURES / "live-combat-counterpart-lifelink-cards.json",
            FIXTURES / "combat-defending-player-cards.json",
            FIXTURES / "bound-effect-program-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition(
            "Current Combat Counterparts",
            [DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30)],
            ["Isamaru, Hound of Konda"],
        )

    def test_strict_binding_preserves_typed_dependencies_and_rejects_neighbors(self):
        record = self.db.by_oracle_id("973c49a1-8425-40b9-8bdc-2c2434222314")
        semantics = SemanticRegistry()
        register_generated_programs(
            self.db, semantics, (record,), capability_registry=self.registry, capability_profile=PROFILE,
            promote_exact_runtime_handlers=True, promote_exact_trigger_programs=True,
            promote_exact_effect_programs=True, promote_exact_capability_declarations=True,
        )
        card_program = compile_best_available_card_program(
            self.db, record, semantic_registry=semantics, capability_registry=self.registry,
            capability_profile=PROFILE,
        )
        binding = bind_card_program_runtime(card_program, capability_registry=self.registry, profile=PROFILE)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        trigger, = semantics.programs_for_oracle(record.oracle_id, event="step.begin")
        self.assertIsNone(trigger.target_schema)
        self.assertEqual("untap", trigger.effects[0]["op"])
        self.assertEqual("equipped_object", trigger.effects[0]["card"]["relation"])
        self.assertEqual(2, trigger.effects[0]["card"]["schema_version"])
        self.assertIn("object.reference.source_attachment_information", trigger.capability_dependencies)
        program, = (
            value for value in semantics.programs_for_oracle(record.oracle_id, event="characteristics.evaluate")
            if CAPABILITY in value.capability_dependencies
        )
        descriptor, = program.handlers
        self.assertEqual(["goblin", "orc"], list(descriptor["counterpart"]["subtypes_any"]))
        for amended in (
            {**descriptor, "schema_version": True},
            {**descriptor, "counterpart": {**descriptor["counterpart"], "controller": "B"}},
            {**descriptor, "counterpart": {**descriptor["counterpart"], "subtypes_any": ["goblin"]}},
        ):
            with self.assertRaises((SemanticNodeError, ValueError)):
                replace(program, handlers=(amended,))
        dependencies = tuple(dependency for dependency in program.capability_dependencies if dependency != CAPABILITY)
        checked = bind_semantic_program_runtime(
            replace(program, capability_dependencies=dependencies,
                    capability_closure=self.registry.closure(dependencies, profile=PROFILE).to_dict()),
            capability_registry=self.registry, profile=PROFILE,
        )
        self.assertIn("capability:undeclared_runtime_dependency:" + CAPABILITY, checked["blockers"])
        self.assertFalse(checked["strict"])
        for neighbor in (
            replace(record, type_line="Artifact"),
            replace(record, oracle_text=record.oracle_text.replace("blocking or blocked", "that blocked")),
            replace(record, oracle_text=record.oracle_text.replace("Goblin or Orc", "Goblin and Orc")),
            replace(record, oracle_text=record.oracle_text.replace("has first strike", "has double strike")),
        ):
            ir = compile_oracle_card(neighbor, capability_registry=self.registry, capability_profile=PROFILE)
            self.assertNotEqual("exact", ir.status)
            self.assertFalse(any(CAPABILITY in node.capability_dependencies for face in ir.faces for node in face.nodes))
        self.assertIsNone(targeted_tap_state_effect_template(
            "Tap equipped creature.", source_attachment_relation=AttachmentReferenceKind.EQUIPPED,
        ))

    def setup_combat(self, seed, *, active="A", counterpart="Fixture Goblin Combatant", response_name=None):
        game = self.session(seed)
        engine = game.engine
        source = self.add(engine, NAME)
        receiver = self.add(engine, "Grizzly Bears", ref="sting-receiver")
        opponent = self.add(engine, counterpart, seat="B", ref="sting-combat-counterpart")
        unrelated = self.add(engine, "Fixture Goblin Combatant", seat="C", ref="sting-unrelated")
        receiver.acquired_control_turn_count = opponent.acquired_control_turn_count = -1
        attach_objects(engine.state.cards, source, receiver, source_timestamp=source.zone_timestamp)
        response = None
        if response_name is not None:
            response = self.add(engine, response_name, seat="B", zone="hand")
            self.add(engine, "Plains", seat="B")
            self.add(engine, "Plains", seat="B", ref="sting-response-plains")
        self.assert_closed(game, source)
        private = engine.state.cards[engine.state.players["D"].zones["hand"][0]].ref
        self.prepare(game, actor=active, active=active)
        return game, source, receiver, opponent, unrelated, response, private

    def beginning_trigger(self, game, source, active):
        self.until(game, lambda current: bool(current.state.stack)
                   and current.state.step == "beginning_combat")
        item = game.state.stack[-1]
        self.assertEqual("A", item.controller)
        self.assertEqual([], list(item.targets))
        self.assertEqual(active, item.context["player"])
        self.assertEqual(1, self.trigger_count(game, source))
        return item

    def first_strike(self, game, receiver):
        return "first strike" in game.engine._combat_keywords(game.state.cards[receiver.object_id])

    def declare(self, game, attacker, blocker, *, active):
        defender = "B" if active == "A" else "A"
        self.until(game, lambda current: current.state.pending_decision.kind == "combat.attackers")
        attacked = game.act(f"pilot:{active}", {
            "action_id": "attack", "attackers": {attacker.ref: defender},
        })
        self.assertTrue(attacked.ok, attacked.summary)
        self.until(game, lambda current: current.state.pending_decision.kind == "combat.blockers")
        blocked = game.act(f"pilot:{defender}", {
            "action_id": "block", "blocks": {blocker.ref: attacker.ref},
        })
        self.assertTrue(blocked.ok, blocked.summary)

    def test_actual_cast_equip_and_both_players_combat_beginnings_untap_with_replay(self):
        game = self.session(5072101)
        source = self.add(game.engine, NAME, zone="hand")
        receiver = self.add(game.engine, "Grizzly Bears", ref="sting-receiver")
        receiver.tapped = True
        self.prepare(game, mana={"C": 4})
        self.checkpoint(game)
        self.cast_for(game, "A", source)
        self.resolve_top(game)
        self.priority(game, "A")
        offers = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        equip, = [offer for offer in offers if offer["id"].startswith(f"activate:{source.ref}:")]
        result = game.act("pilot:A", {"action_id": equip["id"], "targets": [receiver.ref], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.resolve_top(game)
        equipped = game.state.cards[receiver.object_id]
        self.assertTrue(equipped.tapped)
        self.assertFalse(any(game.state.players["A"].mana_pool.values()))
        self.assertIn("haste", game.engine._combat_keywords(equipped))
        self.assertEqual((3, 3), tuple(game.engine._numeric_stat(equipped.object_id, stat)
                                       for stat in ("power", "toughness")))
        self.beginning_trigger(game, source, "A")
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertFalse(game.state.cards[receiver.object_id].tapped)
        self.replay(game, load=True)

        game, source, receiver, _opponent, _unrelated, _response, _private = self.setup_combat(5072102, active="B")
        receiver.tapped = True
        receiver.counters["stun"] = 1
        self.checkpoint(game)
        self.beginning_trigger(game, source, "B")
        game = self.replay(game, load=True)
        self.resolve_top(game)
        equipped = game.state.cards[receiver.object_id]
        self.assertTrue(equipped.tapped)
        self.assertNotIn("stun", equipped.counters)
        self.replay(game, load=True)

    def test_actual_both_block_directions_use_current_counterpart_subtype(self):
        cases = (
            ("A", "Fixture Goblin Combatant", True),
            ("B", "Fixture Orc Combatant", True),
            ("A", "Grizzly Bears", False),
            ("B", "Grizzly Bears", False),
        )
        for index, (active, subtype, qualified) in enumerate(cases):
            with self.subTest(active=active, counterpart=subtype):
                game, source, receiver, opponent, unrelated, _response, private = self.setup_combat(
                    5072110 + index, active=active, counterpart=subtype,
                )
                self.checkpoint(game)
                self.beginning_trigger(game, source, active)
                self.resolve_top(game)
                self.assertFalse(self.first_strike(game, receiver))
                attacker, blocker = (receiver, opponent) if active == "A" else (opponent, receiver)
                self.declare(game, attacker, blocker, active=active)
                self.assertEqual(qualified, self.first_strike(game, receiver), "Actual qualified block grants first strike")
                self.assertFalse(self.first_strike(game, unrelated))
                self.assertFalse(self.first_strike(game, opponent))
                game = self.replay(game, load=True)
                self.until(game, lambda current: current.state.step == "combat_damage"
                           and current.state.pending_decision.kind == "priority")
                self.assertEqual(qualified, game.state.combat.first_strike_step)
                self.assertEqual(0 if qualified else 2, game.state.cards[receiver.object_id].marked_damage)
                self.assertEqual("graveyard", game.state.cards[opponent.object_id].zone)
                for seat in ("A", "B", "C"):
                    self.assertNotIn(private, json.dumps(game.packet("pilot:" + seat, full=True)))
                self.replay(game, load=True)

    def test_returned_qualifying_counterpart_no_longer_confers_first_strike(self):
        game, source, receiver, opponent, _unrelated, response, _private = self.setup_combat(
            5072120, active="B", response_name="Cloudshift",
        )
        self.checkpoint(game)
        self.beginning_trigger(game, source, "B")
        self.resolve_top(game)
        self.declare(game, opponent, receiver, active="B")
        self.assertTrue(self.first_strike(game, receiver))
        original = opponent.logical_object_id
        self.cast_for(game, "B", response, [opponent.ref])
        self.resolve_top(game)
        self.assertNotEqual(original, game.state.cards[opponent.object_id].logical_object_id)
        self.assertFalse(self.first_strike(game, receiver))
        self.replay(game, load=True)

    def test_original_untap_resolves_against_departed_equipment_last_known_attachment(self):
        game, source, receiver, _opponent, unrelated, response, _private = self.setup_combat(
            5072130, response_name="Disenchant",
        )
        receiver.tapped = unrelated.tapped = True
        self.checkpoint(game)
        self.beginning_trigger(game, source, "A")
        self.cast_for(game, "B", response, [source.ref])
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertFalse(game.state.cards[receiver.object_id].tapped)
        self.assertTrue(game.state.cards[unrelated.object_id].tapped)
        self.replay(game, load=True)

    def test_removing_the_live_relation_kills_the_strike_witness(self):
        with patch("quorune.semantic_runtime.attached_combat_strike.current_block_counterpart_ids", return_value=()):
            game, source, receiver, opponent, _unrelated, _response, _private = self.setup_combat(5072140)
            self.checkpoint(game)
            self.beginning_trigger(game, source, "A")
            self.resolve_top(game)
            self.declare(game, receiver, opponent, active="A")
            self.assertFalse(self.first_strike(game, receiver))
