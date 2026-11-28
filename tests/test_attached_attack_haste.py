from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from test_qualified_zone_event_queries import query_record
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.attached_attack_haste import (
    ATTACHED_ATTACK_HASTE_CAPABILITY,
    ATTACK_PERMISSION_EVENT,
    AttachedAttackHastePermissionHandler,
    AttackHasteScope,
)
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantic_runtime.context import SemanticNodeError
from scripts.build_test_database import build_fixture_database


SOURCE = "Instill Energy"
ATTACK_TEXT = "Enchanted creature can attack as though it had haste."
FIXTURE_NAME = "attached-attack-haste-cards.json"


class AttachedAttackHasteCompilerTests(unittest.TestCase):
    def test_exact_aura_span_and_closed_declaration_descriptor(self):
        record = replace(query_record(ATTACK_TEXT), type_line="Enchantment — Aura")
        registry = load_default_capability_registry()
        ir = compile_oracle_card(record, capability_registry=registry, capability_profile="commander_review")
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        self.assertEqual((ATTACK_TEXT, 0, len(ATTACK_TEXT)), (node.text, node.span.start, node.span.end))
        self.assertEqual(ATTACK_PERMISSION_EVENT, node.event)
        self.assertEqual((ATTACHED_ATTACK_HASTE_CAPABILITY,), node.capability_dependencies)
        descriptor, = node.handlers
        handler = AttachedAttackHastePermissionHandler()
        self.assertEqual(AttackHasteScope.ENCHANTED_CREATURE, handler.validate(descriptor))
        for malformed in (
            {**descriptor, "scope": "controller_creatures"},
            {**descriptor, "schema_version": True},
            {**descriptor, "event": "characteristics.evaluate"},
            {**descriptor, "grant_haste": True},
        ):
            with self.subTest(malformed=malformed), self.assertRaises(SemanticNodeError):
                handler.validate(malformed)
        for boundary in (
            replace(record, type_line="Enchantment"),
            replace(record, oracle_text=ATTACK_TEXT.replace("attack", "attack or tap")),
            replace(record, oracle_text=ATTACK_TEXT + " Untap all other creatures."),
        ):
            with self.subTest(text=boundary.oracle_text, type_line=boundary.type_line):
                compiled = compile_oracle_card(boundary, capability_registry=registry)
                self.assertTrue(compiled.material_residuals)


class AttachedAttackHasteNativeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "attached-attack-haste.sqlite3"
        fixtures = ROOT / "tests" / "fixtures"
        build_fixture_database([
            fixtures / "bound-effect-program-cards.json",
            fixtures / "all-kind-counter-cards.json",
            fixtures / "enchanted-player-static-cards.json",
            fixtures / FIXTURE_NAME,
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Attack-only haste", [
            DeckEntry(commander, 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], [commander])

    def reject(self, session, seat, command):
        before = authoritative_state_hash(session.state)
        result = session.act("pilot:" + seat, command)
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(session.state))

    def accept(self, session, seat, command):
        result = session.act("pilot:" + seat, command)
        self.assertTrue(result.ok, result.summary)

    def activation(self, session, source):
        source = session.state.cards[source.object_id]
        ability, = session.engine._activated_abilities(source)
        return {"action_id": f"activate:{source.ref}:{ability.ability_id}", "pay": "auto"}

    def assert_activation_is_offered(self, session, seat, command, expected):
        packet = session.packet("pilot:" + seat, full=True)
        actions = packet["decision"]["ctx"]["legal"]["actions"]
        self.assertEqual(expected, any(action["id"] == command["action_id"] for action in actions))

    def attackers(self, session, seat):
        self.until(session, lambda game: (
            game.state.active_player == seat
            and game.state.pending_decision is not None
            and game.state.pending_decision.kind == "combat.attackers"
        ))
        decision = session.packet("pilot:" + seat, full=True)["decision"]
        return {candidate["id"] for candidate in decision["ctx"]["candidates"]}

    def test_actual_aura_allows_a_sick_attacker_and_keeps_tap_and_untap_costs_closed(self):
        for tapped, name in ((False, "Llanowar Elves"), (True, "Gilder Bairn")):
            with self.subTest(creature=name):
                self.exercise_sick_attacker(tapped, name)

    def exercise_sick_attacker(self, tapped, name):
        game = self.session(60940010 + tapped)
        aura = self.add(game.engine, SOURCE, zone="hand")
        body = self.add(game.engine, name)
        outside = self.add(game.engine, "Grizzly Bears", ref="unenchanted-sick")
        old = self.add(game.engine, "Memnite", ref="old-eligible-neighbor")
        land = self.add(game.engine, "Forest")
        hidden = self.add(game.engine, "Grizzly Bears", seat="D", zone="hand", ref="private-seat-d")
        self.assert_closed(game, aura)
        self.prepare(game, mana={"C": 3, "G": 2})
        for creature in (body, outside):
            creature.acquired_control_turn_count = game.state.players["A"].turns_begun
        body.tapped = tapped
        old.acquired_control_turn_count = -1
        self.checkpoint(game)

        cast = self.action(game, "A", aura)
        self.assertIn(body.ref, cast["target_schema"]["legal_refs"])
        self.assertNotIn(land.ref, cast["target_schema"]["legal_refs"])
        self.reject(game, "A", {"action_id": cast["id"], "targets": [land.ref], "pay": "auto"})
        self.cast_for(game, "A", aura, [body.ref], reserve={"C": 3, "G": 1})
        self.assertIsNone(self.resolve(game))
        self.assertEqual(body.object_id, game.state.cards[aura.object_id].attached_to)
        self.assertNotIn("haste", game.engine._combat_keywords(game.state.cards[body.object_id]))

        self.priority(game, "A")
        source_ability = self.activation(game, body)
        if tapped:
            source_ability["targets"] = [body.ref]
        else:
            source_ability["mana_output"] = {"G": 1}
        self.assert_activation_is_offered(game, "A", source_ability, False)
        self.reject(game, "A", source_ability)
        if tapped:
            self.accept(game, "A", self.activation(game, aura))
            self.assertTrue(game.state.cards[body.object_id].tapped)
            self.assertIsNone(self.resolve(game))
            self.assertFalse(game.state.cards[body.object_id].tapped)

        candidates = self.attackers(game, "A")
        self.assertIn(body.ref, candidates, "the enchanted sick creature must be offered")
        self.assertNotIn(outside.ref, candidates)
        self.reject(game, "A", {"action_id": "attack", "attackers": {outside.ref: "B"}})
        self.reject(game, "B", {"action_id": "attack", "attackers": {body.ref: "C"}})
        self.accept(game, "A", {"action_id": "attack", "attackers": {body.ref: "C"}})
        self.assertIn(body.object_id, game.state.combat.attackers)
        self.assertTrue(game.state.cards[body.object_id].tapped)
        for seat in "ABC":
            self.assertNotIn(hidden.ref, json.dumps(game.packet("pilot:" + seat, full=True)))
        self.replay(game, load=True)

    def reach_upkeep(self, game, seat):
        def at_upkeep(current):
            return current.state.active_player == seat and current.state.step == "upkeep"

        def at_upkeep_or_cleanup(current):
            decision = current.state.pending_decision
            return at_upkeep(current) or (decision is not None and decision.kind == "cleanup.discard")

        self.until(game, at_upkeep_or_cleanup)
        if not at_upkeep(game):
            discarder, = game.state.pending_decision.actors
            context = game.packet("pilot:" + discarder, full=True)["decision"]["ctx"]
            selected = [row["id"] for row in context["hand"][:context["count"]]]
            self.accept(game, discarder, {"action_id": "discard", "cards": selected})
            self.until(game, at_upkeep)

    def test_actual_aura_controller_untaps_foreign_creature_once_each_own_turn(self):
        game = self.session(60940020)
        aura = self.add(game.engine, SOURCE, zone="hand")
        body = self.add(game.engine, "Grizzly Bears", seat="B")
        self.prepare(game, mana={"G": 1})
        body.acquired_control_turn_count = -1
        body.tapped = True
        self.checkpoint(game)
        self.cast_for(game, "A", aura, [body.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("B", game.state.cards[body.object_id].controller)
        self.assertEqual("A", game.state.cards[aura.object_id].controller)
        self.priority(game, "A")
        command = self.activation(game, aura)
        self.assert_activation_is_offered(game, "A", command, True)
        self.reject(game, "B", command)
        self.accept(game, "A", command)
        self.assertTrue(game.state.cards[body.object_id].tapped)
        self.assertEqual("A", game.state.stack[-1].controller)
        self.assert_activation_is_offered(game, "A", command, False)
        self.reject(game, "A", command)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertFalse(game.state.cards[body.object_id].tapped)

        self.until(game, lambda current: (
            current.state.active_player == "B" and current.state.step == "upkeep"
        ))
        self.priority(game, "B")
        self.assert_activation_is_offered(game, "B", command, False)
        self.reject(game, "B", command)
        self.priority(game, "A")
        self.assert_activation_is_offered(game, "A", command, False)
        self.reject(game, "A", command)
        self.assertIn(body.ref, self.attackers(game, "B"))
        self.accept(game, "B", {"action_id": "attack", "attackers": {body.ref: "C"}})
        self.assertTrue(game.state.cards[body.object_id].tapped)
        for seat in "CDA":
            self.reach_upkeep(game, seat)
        self.assertTrue(game.state.cards[body.object_id].tapped)
        self.priority(game, "A")
        self.assert_activation_is_offered(game, "A", command, True)
        self.accept(game, "A", command)
        self.assertIsNone(self.resolve(game))
        self.assertFalse(game.state.cards[body.object_id].tapped)
        self.replay(game, load=True)

    def test_runtime_permission_requires_its_declared_capability(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime

        game = self.session(60940021)
        aura = self.add(game.engine, SOURCE, zone="hand")
        permission, = (program for program in game.engine.semantics.programs_for_oracle(aura.oracle_id)
                       if program.event == ATTACK_PERMISSION_EVENT)
        stripped = replace(permission, capability_dependencies=(), capability_closure=None)
        result = bind_semantic_program_runtime(
            stripped, capability_registry=self.registry, profile="commander_review",
        )
        self.assertFalse(result["strict"])
        self.assertIn("capability:undeclared_runtime_dependency:" + ATTACHED_ATTACK_HASTE_CAPABILITY,
                      result["blockers"])

    def test_actual_departed_aura_keeps_queued_untap_and_loses_attack_permission(self):
        game = self.session(60940030)
        aura = self.add(game.engine, SOURCE, zone="hand")
        body = self.add(game.engine, "Llanowar Elves")
        old = self.add(game.engine, "Grizzly Bears")
        remover = self.add(game.engine, "Disenchant", seat="B", zone="hand")
        self.prepare(game, mana={"G": 1})
        game.state.players["B"].mana_pool.update({"C": 1, "W": 1})
        body.acquired_control_turn_count = game.state.players["A"].turns_begun
        body.tapped = True
        old.acquired_control_turn_count = -1
        self.checkpoint(game)
        self.cast_for(game, "A", aura, [body.ref])
        self.assertIsNone(self.resolve(game))
        self.priority(game, "A")
        self.accept(game, "A", self.activation(game, aura))
        self.cast_for(game, "B", remover, [game.state.cards[aura.object_id].ref])
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[aura.object_id].zone)
        self.assertTrue(game.state.cards[body.object_id].tapped)
        self.assertIsNone(self.resolve(game))
        self.assertFalse(game.state.cards[body.object_id].tapped)
        self.assertNotIn(body.ref, self.attackers(game, "A"))
        self.reject(game, "A", {"action_id": "attack", "attackers": {body.ref: "C"}})
        self.accept(game, "A", {"action_id": "attack", "attackers": {old.ref: "C"}})
        self.replay(game, load=True)

    def test_omitting_declaration_permission_fails_the_actual_attack_witness(self):
        with patch("quorune.rules.attached_attack_haste.current_enchanted_attack_permission", return_value=False):
            with self.assertRaisesRegex(AssertionError, "the enchanted sick creature must be offered"):
                self.exercise_sick_attacker(False, "Llanowar Elves")
