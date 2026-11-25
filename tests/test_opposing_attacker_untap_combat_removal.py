"""Use the real transformed land and a declared opposing attacker."""

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
from quorune.turn_history import current_turn_history_events
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


COMPASS = "Thaumatic Compass // Spires of Orazca"


class OpposingAttackerUntapCombatRemovalTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "opposing-attacker-removal.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/opposing-attacker-untap-combat-removal-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Opposing attacker removal", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_printed_faces_preserve_search_transform_mana_and_exact_back_activation(self):
        card = self.db.lookup(COMPASS, fuzzy=False)
        binding, semantics = register_card(self.db, self.registry, COMPASS)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        programs = semantics.programs_for_oracle(card.oracle_id)
        activation, = [program for program in programs if any(
            effect.get("op") == "remove_from_combat" for effect in program.effects)]
        self.assertEqual(("activate", "battlefield"), (activation.event, activation.active_zone))
        self.assertEqual({"zones": ["battlefield"], "categories": ["permanent"], "count": 1,
                          "types_any": ["creature"], "controller_relation": "opponent", "combat_state": "attacking"},
                         activation.target_schema)
        self.assertEqual(({"op": "remove_from_combat", "card": "$target.0", "untap": "before"},),
                         tuple(activation.effects))
        self.assertLessEqual({"combat.effect.remove_from_combat", "permanent.untap.effect", "target.revalidate_resolution"},
                             set(activation.capability_dependencies))
        ir = compile_oracle_card(card, capability_registry=self.registry)
        self.assertEqual("exact", ir.status)
        lines = [node.text for face in ir.faces for node in face.nodes]
        self.assertTrue(any("Search your library for a basic land card" in line for line in lines))
        self.assertTrue(any("transform this artifact" in line for line in lines))
        self.assertTrue(any("Add {C}" in line for line in lines))

    def test_raw_receiver_and_untap_order_cannot_be_reinterpreted(self):
        card = self.db.lookup(COMPASS, fuzzy=False)
        _, semantics = register_card(self.db, self.registry, COMPASS)
        activation, = [program for program in semantics.programs_for_oracle(card.oracle_id) if any(
            effect.get("op") == "remove_from_combat" for effect in program.effects)]
        effect, = activation.effects
        for changed in (
            replace(activation, target_schema={**activation.target_schema, "controller_relation": "you"}),
            replace(activation, target_schema={**activation.target_schema, "combat_state": "attacking_or_blocking"}),
            replace(activation, effects=({**effect, "untap": "after"},)),
            replace(activation, effects=({**effect, "card": "$source.zone_object"},)),
        ):
            bound = bind_semantic_program_runtime(changed, capability_registry=self.registry,
                                                  profile="commander_review")
            self.assertFalse(bound["strict"], bound)

    def transform_and_attack(self, game, *controls):
        engine = game.engine
        source = self.add(engine, COMPASS, zone="hand")
        attacker = self.add(engine, "Grizzly Bears", seat="B", ref="spires-declared-attacker")
        idle = self.add(engine, "Grizzly Bears", seat="A", ref="spires-idle-creature")
        for index in range(7):
            self.add(engine, "Plains", ref=f"spires-land-{index}")
        for card in (source, *controls):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 2})
        self.checkpoint(game)
        self.cast_for(game, "A", source)
        self.resolve_top(game)
        self.assertNotEqual("Spires of Orazca", game.state.cards[source.object_id].active_face)
        self.until(game, lambda current: current.state.step == "end_step"
                   and self.trigger_count(current, source) == 1)
        self.resolve_top(game)
        current = game.state.cards[source.object_id]
        self.assertEqual(("Spires of Orazca", 1, False),
                         (current.active_face, current.transform_count, current.tapped))
        self.until(game, lambda current: current.state.active_player == "B"
                   and current.state.pending_decision.kind == "combat.attackers")
        result = game.act("pilot:B", {"action_id": "attack", "attackers": {attacker.ref: "C"}})
        self.assertTrue(result.ok, result.summary)
        self.assertTrue(game.state.cards[attacker.object_id].tapped)
        self.assertIn(attacker.object_id, game.state.combat.attackers)
        return source, attacker, idle

    def offer(self, game, source):
        self.priority(game, "A")
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        action, = [row for row in actions if row["id"].startswith("activate:" + source.ref + ":")
                   and row.get("target_schema") is not None]
        return action

    def test_actual_spires_untaps_then_removes_an_attacker_against_another_player(self):
        game = self.session(61341401)
        private = self.add(game.engine, "Counterspell", seat="D", zone="hand", ref="spires-private-card")
        source, attacker, idle = self.transform_and_attack(game)
        action = self.offer(game, source)
        self.assertEqual([attacker.ref], action["target_schema"]["legal_refs"])
        command = {"action_id": action["id"], "targets": [attacker.ref], "pay": "auto"}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:D", command).ok)
        self.assertFalse(game.act("pilot:A", {**command, "targets": [idle.ref]}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        self.assertTrue(game.state.cards[source.object_id].tapped)
        self.assertTrue(game.state.cards[attacker.object_id].tapped)
        after = game.state.event_sequence
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertFalse(game.state.cards[attacker.object_id].tapped)
        self.assertNotIn(attacker.object_id, game.state.combat.attackers)
        events = [event.code for event in game.state.events
                  if event.event_id > after and event.details.get("object") == attacker.ref
                  and event.code in ("permanent.untap", "combat.remove")]
        self.assertEqual(["permanent.untap", "combat.remove"], events, "The printed untap must commit first")
        attacks = current_turn_history_events(game.state.turn_history,
            turn_sequence=game.state.turn_sequence, kind="creature_attacked")
        self.assertTrue(any(event.actor == "B" and event.target == "C"
                            and event.object_incarnation == game.state.cards[attacker.object_id].logical_object_id
                            for event in attacks))
        for viewer in ("pilot:A", "pilot:B", "pilot:C", "spectator"):
            self.assertNotIn(private.ref, json.dumps(game.packet(viewer, full=True)))
        self.replay(game, load=True)

    def test_actual_response_revalidates_the_original_attacker_before_either_result(self):
        game = self.session(61341402)
        bounce = self.add(game.engine, "Unsummon", seat="C", zone="hand")
        self.add(game.engine, "Island", seat="C")
        source, attacker, _ = self.transform_and_attack(game, bounce)
        action = self.offer(game, source)
        command = {"action_id": action["id"], "targets": [attacker.ref], "pay": "auto"}
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        self.cast_for(game, "C", bounce, [attacker.ref])
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[attacker.object_id].zone)
        offset = game.state.event_sequence
        self.resolve_top(game)
        self.assertTrue(game.state.cards[source.object_id].tapped)
        self.assertFalse(any(event.event_id > offset and event.code == "permanent.untap"
                             and event.details.get("object") == attacker.ref for event in game.state.events))
        self.replay(game, load=True)

    def test_wrong_instruction_order_is_killed_with_actual_transformed_land_and_attack(self):
        from quorune.semantic_runtime import executor

        original = executor.execute_combat_removal

        def reversed_order(host, intent):
            return original(host, replace(intent, untap="after"))

        with patch.object(executor, "execute_combat_removal", side_effect=reversed_order):
            with self.assertRaisesRegex(AssertionError, "The printed untap must commit first"):
                self.test_actual_spires_untaps_then_removes_an_attacker_against_another_player()
