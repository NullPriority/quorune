"""Live block counterparts use ordinary lifelink while the source remains in combat."""

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
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantic_runtime.context import SemanticNodeError
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


NAME = "Alms Beast"
PROFILE = "commander_review"
CAPABILITY = "continuous.ability.combat_counterpart_lifelink"
HANDLER = "continuous.ability.combat-counterpart-lifelink.v1"


class LiveCombatCounterpartLifelinkTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "live-combat-counterpart-lifelink.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/live-combat-counterpart-lifelink-cards.json",
            ROOT / "tests/fixtures/combat-defending-player-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Current reciprocal combat relation", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def program(self):
        record = self.db.lookup(NAME, fuzzy=False)
        binding, semantics = register_card(self.db, self.registry, NAME)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        program, = semantics.programs_for_oracle(record.oracle_id, event="characteristics.evaluate")
        return record, program

    def test_complete_original_uses_closed_live_reciprocal_combat_edges(self):
        record, program = self.program()
        self.assertIn(CAPABILITY, program.capability_dependencies)
        component, = program.handlers
        self.assertEqual({
            "handler_id": HANDLER, "schema_version": 1, "event": "characteristics.evaluate",
        }, component)
        self.assertIsNone(program.target_schema)
        for modified in (
            record.oracle_text.replace("blocking or blocked", "that blocked"),
            record.oracle_text.replace("this creature", "a creature you control"),
            record.oracle_text.replace("have lifelink", "have deathtouch"),
        ):
            ir = compile_oracle_card(replace(record, oracle_text=modified), capability_registry=self.registry)
            self.assertFalse(any(CAPABILITY in node.capability_dependencies for face in ir.faces for node in face.nodes))

    def test_raw_component_rejects_another_scope_and_requires_live_combat_authority(self):
        _, program = self.program()
        descriptor, = program.handlers
        with self.assertRaises(SemanticNodeError):
            replace(program, handlers=({**descriptor, "scope": "all_creatures"},))
        caps = [value for value in program.capability_dependencies if value != CAPABILITY]
        closure = self.registry.closure(caps, profile=PROFILE).to_dict() if caps else None
        result = bind_semantic_program_runtime(replace(program, capability_dependencies=caps, capability_closure=closure),
            capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(result["strict"], result)
        self.assertIn("capability:undeclared_runtime_dependency:" + CAPABILITY, result["blockers"])

    def scene(self, seed, *, active="A", counterpart="Grizzly Bears", response_name=None):
        game = self.session(seed)
        engine = game.engine
        beast = self.add(engine, NAME, zone="hand")
        other = self.add(engine, counterpart, seat="B")
        other.acquired_control_turn_count = -1
        unrelated = self.add(engine, "Grizzly Bears", seat="C", ref="alms-unrelated-creature")
        support = self.add(engine, "Fervor" if active == "A" else "Vedalken Orrery")
        response = None
        if response_name is not None:
            response = self.add(engine, response_name, seat="B", zone="hand")
            self.add(engine, "Island" if response_name == "Unsummon" else "Plains", seat="B")
        private = self.add(engine, "Counterspell", seat="D", zone="hand", ref="alms-private-d")
        for card in (beast, other, support):
            self.assert_closed(game, card)
        self.prepare(game, actor="A", active=active, mana={"C": 2, "W": 1, "B": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", beast)
        self.resolve_top(game)
        return game, beast, other, unrelated, response, private

    def declare(self, game, attacker, blocker, *, active):
        defender = "B" if active == "A" else "A"
        self.until(game, lambda current: current.state.pending_decision.kind == "combat.attackers")
        attack = game.act(f"pilot:{active}", {"action_id": "attack", "attackers": {attacker.ref: defender}})
        self.assertTrue(attack.ok, attack.summary)
        self.until(game, lambda current: current.state.pending_decision.kind == "combat.blockers")
        block = game.act(f"pilot:{defender}", {"action_id": "block", "blocks": {blocker.ref: attacker.ref}})
        self.assertTrue(block.ok, block.summary)
        self.assertEqual([blocker.object_id], game.state.combat.blockers[attacker.object_id])

    def lifelink(self, game, card):
        return "lifelink" in game.engine._combat_keywords(game.state.cards[card.object_id])

    def combat_damage(self, game):
        self.until(game, lambda current: current.state.step == "combat_damage"
                   and current.state.pending_decision.kind == "priority")

    def test_actual_blocker_gets_lifelink_for_its_ability_and_simultaneous_combat_damage(self):
        game, beast, blocker, unrelated, _response, private = self.scene(7021501, counterpart="Prodigal Sorcerer")
        self.assertFalse(self.lifelink(game, blocker))
        self.declare(game, beast, blocker, active="A")
        self.assertTrue(self.lifelink(game, blocker), "A live blocker receives lifelink")
        self.assertFalse(self.lifelink(game, beast))
        self.assertFalse(self.lifelink(game, unrelated))
        self.priority(game, "B")
        rows = game.packet("pilot:B", full=True)["decision"]["ctx"]["legal"]["actions"]
        offered, = [row for row in rows if row["id"].startswith(f"activate:{blocker.ref}:")]
        activated = game.act("pilot:B", {"action_id": offered["id"], "targets": ["C"], "pay": "auto"})
        self.assertTrue(activated.ok, activated.summary)
        self.resolve_top(game)
        self.assertEqual((41, 39), tuple(game.state.players[seat].life for seat in "BC"))
        self.assertTrue(game.state.cards[blocker.object_id].tapped)

        self.combat_damage(game)
        self.assertEqual(42, game.state.players["B"].life)
        self.assertEqual(40, game.state.players["A"].life)
        self.assertEqual("graveyard", game.state.cards[blocker.object_id].zone)
        for principal in ("pilot:A", "pilot:B", "pilot:C", "spectator"):
            self.assertNotIn(private.ref, str(game.packet(principal, full=True)))
        self.replay(game, load=True)

    def test_actual_blocked_attackers_gain_life_once_with_or_without_intrinsic_lifelink(self):
        for index, name in enumerate(("Grizzly Bears", "Child of Night")):
            with self.subTest(name=name):
                game, beast, attacker, unrelated, _response, _private = self.scene(
                    7021502 + index, active="B", counterpart=name,
                )
                self.assertEqual(index == 1, self.lifelink(game, attacker))
                self.declare(game, attacker, beast, active="B")
                self.assertTrue(self.lifelink(game, attacker))
                self.assertFalse(self.lifelink(game, unrelated))
                self.combat_damage(game)
                self.assertEqual(42, game.state.players["B"].life)
                self.assertEqual(40, game.state.players["A"].life)
                self.replay(game, load=True)

    def test_actual_departure_eliminates_the_live_grant(self):
        game, beast, blocker, _unrelated, response, _private = self.scene(7021504, response_name="Unsummon")
        self.declare(game, beast, blocker, active="A")
        self.assertTrue(self.lifelink(game, blocker))
        self.cast_for(game, "B", response, [beast.ref])
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[beast.object_id].zone)
        self.assertFalse(self.lifelink(game, blocker))
        self.replay(game, load=True)

    def test_actual_returned_attacker_does_not_keep_lifelink_from_its_previous_blocker(self):
        game, beast, attacker, _unrelated, response, _private = self.scene(
            7021505, active="B", response_name="Cloudshift",
        )
        self.declare(game, attacker, beast, active="B")
        self.assertTrue(self.lifelink(game, attacker))
        old_identity = attacker.logical_object_id
        self.cast_for(game, "B", response, [attacker.ref])
        self.resolve_top(game)
        returned = game.state.cards[attacker.object_id]
        self.assertEqual("battlefield", returned.zone)
        self.assertNotEqual(old_identity, returned.logical_object_id)
        self.assertEqual(attacker.object_id, game.state.cards[beast.object_id].blocking)
        self.assertEqual([beast.object_id], game.state.combat.blockers[attacker.object_id])
        self.assertNotIn(attacker.object_id, game.state.combat.attackers)
        self.assertFalse(self.lifelink(game, returned))
        self.replay(game, load=True)

    def test_actual_blocker_witness_kills_dropping_the_current_pair(self):
        with patch("quorune.combat.current_block_counterpart_ids", return_value=()):
            with self.assertRaisesRegex(AssertionError, "A live blocker receives lifelink"):
                self.test_actual_blocker_gets_lifelink_for_its_ability_and_simultaneous_combat_damage()
