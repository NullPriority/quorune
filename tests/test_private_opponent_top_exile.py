from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
import test_mutate_cast_native as mutate_actions
from quorune.carddb import CardDatabase
from quorune.card_programs.binding import bind_semantic_program_runtime
from test_controller_creature_keyword_absence import register_card
from quorune.deck import DeckDefinition, DeckEntry
from quorune.rules.controller_private_top_exile_model import CONTROLLER_PRIVATE_TOP_EXILE_CAPABILITY
from quorune.record import authoritative_state_hash
from quorune.errors import GameRuleError
from quorune.opponent_library_exile_model import PRIVATE_TOP_EXILE_CAPABILITY
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.private_opponent_top_exile import FIELD, current_private_top_exile_permission
from scripts.build_test_database import build_fixture_database


PRIVATE_ADVENTURE = "Gandalf, Goblins' Bane // Flameshape"


class PrivateOpponentTopExileTests(InheritedNativeFixture, unittest.TestCase):
    mutate = mutate_actions.MutateCastNativeTests.mutate
    choose_merge = mutate_actions.MutateCastNativeTests.choose_merge

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        db = Path(cls.temporary.name) / "private-opponent-tops.sqlite3"
        fixtures = ("bound-effect-program-cards", "private-opponent-top-exile-cards",
                    "mutate-casting-cards", "attached-cast-restriction-cards", "activation-restriction-material-cards")
        build_fixture_database([ROOT / f"tests/fixtures/{name}.json" for name in fixtures], db)
        cls.db = CardDatabase(db)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Private opponent tops", [
            DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def top(self, game, name, seat, reference):
        card = self.add(game.engine, name, seat=seat, zone="library", ref=reference)
        card.known_to = []
        card.revealed_to = []
        return card

    def assert_private_batch(self, game, cards):
        viewer = game.packet("pilot:A", full=True)
        observers = {principal:game.packet(principal, full=True)
                     for principal in ("pilot:B", "pilot:C", "pilot:D", "spectator")}
        for card in cards:
            current = game.state.cards[card.object_id]
            self.assertEqual(("exile", True, ["A"], []), (current.zone, current.face_down, current.known_to, current.revealed_to))
            self.assertEqual(current.logical_object_id, current.annotations[FIELD]["identity"])
            visible, = (row for row in viewer["state"]["players"][current.owner]["ex"] if row["id"] == current.ref)
            self.assertEqual(current.printed_name, visible["n"])
            for principal, packet in observers.items():
                hidden, = (row for row in packet["state"]["players"][current.owner]["ex"] if row["id"] == current.ref)
                self.assertEqual({"id":current.ref, "n":"?", "fd":1}, hidden)
                self.assertNotIn(current.printed_name, json.dumps(packet))
                self.assertNotIn(current.ref, json.dumps(packet.get("events", ())))
                self.assertIsNone(current_private_top_exile_permission(game.state, principal.removeprefix("pilot:"), current))
        with self.assertRaises(GameRuleError):
            game.engine._resolve_object("B", cards[0].printed_name, zones={"exile"})

    def actions(self, game):
        self.priority(game, "A")
        return {row["id"]:row for row in game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]}

    def test_two_native_mutations_keep_separate_private_batches_and_ordinary_play_cost_and_timing(self):
        game = self.session(70214032)
        carrier = self.add(game.engine, "Grizzly Bears")
        first = self.add(game.engine, "Mindleecher", zone="hand")
        second = self.add(game.engine, "Gemrazer", zone="hand")
        artifact = self.add(game.engine, "Sol Ring", seat="B")
        second_b = self.top(game, "Swamp", "B", "t-32-b2")
        second_c = self.top(game, "Control Magic", "C", "t-32-c2")
        second_d = self.top(game, "Watchwolf", "D", "t-32-d2")
        land = self.top(game, "Mountain", "B", "t-32-b1")
        red_spell = self.top(game, "Shock", "C", "t-32-c1")
        first_d = self.top(game, "Disenchant", "D", "t-32-d1")
        for card in (first, second, red_spell, land):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C":5,"B":1,"G":2})
        self.checkpoint(game)

        self.mutate(game, first, carrier, payment={"C":4,"B":1})
        game = self.choose_merge(game, "top", load=True)
        self.assertIsNone(self.resolve(game))
        self.assert_private_batch(game, (land, red_spell, first_d))
        self.assertEqual({"library"}, {game.state.cards[card.object_id].zone for card in (second_b, second_c, second_d)})
        old_exile = game.state.cards[land.object_id].logical_object_id
        first_times = {game.state.cards[card.object_id].zone_timestamp for card in (land, red_spell, first_d)}
        self.assertEqual(1, len(first_times))
        invalid_grant = replace(game.state.cards[land.object_id], annotations={
            FIELD: {"schema_version": 1, "viewer": "A", "identity": old_exile + "-stale"},
        })
        self.assertIsNone(current_private_top_exile_permission(game.state, "A", invalid_grant))
        offers = self.actions(game)
        self.assertIn("play-land:"+land.ref, offers)
        self.assertNotIn("cast:"+red_spell.ref, offers)

        self.mutate(game, second, carrier, payment={"C":1,"G":2})
        self.assertNotIn("play-land:"+land.ref, self.actions(game))
        game = self.choose_merge(game, "bottom", load=True)
        order = self.resolve(game)
        self.assertEqual("trigger.order", order.kind)
        rows = game.packet("pilot:A", full=True)["decision"]["ctx"]["triggers"]
        self.assertEqual(2, len(rows))
        refs = [row["id"] for row in rows]
        result = game.act("pilot:A", {"action_id":"order", "triggers":refs})
        self.assertTrue(result.ok, result.summary)
        self.target(game, "A", [artifact.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[artifact.object_id].zone)
        self.assert_private_batch(game, (land, red_spell, first_d, second_b, second_c, second_d))
        self.assertEqual(old_exile, game.state.cards[land.object_id].logical_object_id)
        second_times = {game.state.cards[card.object_id].zone_timestamp for card in (second_b, second_c, second_d)}
        self.assertEqual(1, len(second_times))
        self.assertTrue(first_times.isdisjoint(second_times))
        self.assertTrue(game.act("pilot:A", {"action_id":"play-land:"+land.ref}).ok)
        played = game.state.cards[land.object_id]
        self.assertEqual(("battlefield","A","B"), (played.zone, played.controller, played.owner))
        self.assertFalse(played.face_down)
        self.assertNotEqual(old_exile, played.logical_object_id)
        self.assertIsNone(current_private_top_exile_permission(game.state, "A", played))
        self.assertNotIn("play-land:"+second_b.ref, self.actions(game))
        self.cast(game,"A",red_spell,["B"])
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[red_spell.object_id].zone)
        self.assertIsNone(current_private_top_exile_permission(game.state, "A", game.state.cards[red_spell.object_id]))
        self.replay(game, load=True)

    def test_original_requires_its_private_mutation_and_normal_play_clause(self):
        original = self.db.lookup("Mindleecher", fuzzy=False)
        result = compile_oracle_card(original,capability_registry=self.registry,capability_profile="commander_review")
        self.assertEqual("exact", result.status, result.material_residuals)
        self.assertTrue(any(PRIVATE_TOP_EXILE_CAPABILITY in node.capability_dependencies
                            for face in result.faces for node in face.nodes))
        for before, after in (("face down", "face up"), ("and play", "and cast"),
                              ("remain exiled", "are owned by opponents")):
            variant = replace(original, oracle_text=original.oracle_text.replace(before, after))
            self.assertNotEqual("exact", compile_oracle_card(variant,capability_registry=self.registry,
                                                            capability_profile="commander_review").status)

    def test_compiler_omitting_private_mutation_is_killed(self):
        with patch("quorune.compiler.opponent_library_exile_templates.each_opponent_library_top_exile_result",return_value=None):
            with self.assertRaises(AssertionError):
                self.test_original_requires_its_private_mutation_and_normal_play_clause()


    def cast_face(self, game, card, face):
        row, = (row for row in self.actions(game).values()
                if row.get("card") == card.ref and row.get("face") == face)
        command = {"action_id": row["id"], "face": face, "pay": "auto"}
        if row.get("cost_options"):
            command["cost_option"] = row["cost_options"][0]["id"]
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)

    def assert_rejected_without_mutation(self, game, command):
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:A", command)
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def test_two_face_spell_preserves_private_top_play_and_source_trigger(self):
        game = self.session(40630013)
        adventure = self.add(game.engine, PRIVATE_ADVENTURE, zone="hand")
        spell = self.top(game, "Shock", "A", "private-own-top-spell")
        land = self.top(game, "Mountain", "A", "private-own-top-land")
        left = self.top(game, "Disenchant", "A", "private-own-next-card")
        library = game.state.players["A"].zones["library"]
        library[-3:] = [left.object_id, spell.object_id, land.object_id]
        bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        self.assert_closed(game, adventure)
        self.prepare(game, mana={"C": 7, "R": 4})
        game.state.players["B"].mana_pool["U"] = 1
        self.checkpoint(game)

        self.cast_face(game, adventure, "Flameshape")
        game = self.replay(game, load=True)
        self.settle(game)
        self.assert_private_batch(game, (land, spell))
        self.assertEqual("library", game.state.cards[left.object_id].zone)
        self.assertEqual(game.state.cards[land.object_id].zone_timestamp,
                         game.state.cards[spell.object_id].zone_timestamp)
        self.assertNotIn("play-land:" + land.ref, self.actions(game))
        self.assertNotIn("cast:" + spell.ref, self.actions(game))
        self.assert_rejected_without_mutation(game, {"action_id": "play-land:" + land.ref})

        self.cast_face(game, adventure, "Gandalf, Goblins' Bane")
        self.settle(game)
        self.assertIn("play-land:" + land.ref, self.actions(game))
        self.assertIn("cast:" + spell.ref, self.actions(game))
        self.assert_rejected_without_mutation(game, {
            "action_id": "cast:" + spell.ref, "targets": ["B"], "pay": "manual", "payment": {"C": 1},
        })
        life = {seat: game.state.players[seat].life for seat in "ABCD"}
        self.cast(game, "A", spell, ["B"])
        self.assertEqual(("spell", "triggered_ability"), tuple(item.kind for item in game.state.stack))
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual((3, 4), tuple(game.engine._numeric_stat(adventure.object_id, stat)
                                      for stat in ("power", "toughness")))
        self.assertEqual(life["A"], game.state.players["A"].life)
        for seat in "BCD":
            self.assertEqual(life[seat] - 1, game.state.players[seat].life)

        self.cast(game, "B", bounce, [adventure.ref])
        self.settle(game)
        self.assertEqual("hand", game.state.cards[adventure.object_id].zone)
        self.assertEqual(life["B"] - 3, game.state.players["B"].life)
        self.assert_private_batch(game, (land,))
        self.assertNotIn("play-land:" + land.ref, self.actions(game))
        self.assert_rejected_without_mutation(game, {"action_id": "play-land:" + land.ref})
        game = self.replay(game, load=True)

        self.cast_face(game, adventure, "Gandalf, Goblins' Bane")
        self.settle(game)
        old_land_identity = game.state.cards[land.object_id].logical_object_id
        self.assertIn("play-land:" + land.ref, self.actions(game))
        result = game.act("pilot:A", {"action_id": "play-land:" + land.ref})
        self.assertTrue(result.ok, result.summary)
        played = game.state.cards[land.object_id]
        self.assertEqual(("battlefield", False), (played.zone, played.face_down))
        self.assertNotEqual(old_land_identity, played.logical_object_id)
        self.replay(game, load=True)

    def test_private_top_spell_requires_the_exact_condition_and_capability(self):
        original = self.db.lookup(PRIVATE_ADVENTURE, fuzzy=False)

        def compile_source(record):
            return compile_oracle_card(record, capability_registry=self.registry,
                                       capability_profile="commander_review")

        compiled = compile_source(original)
        self.assertEqual("exact", compiled.status, compiled.material_residuals)
        front, adventure = compiled.faces
        trigger, = front.nodes
        self.assertEqual(("apply_source_characteristics_until_end_of_turn", "damage_each_opponent"),
                         tuple(effect["op"] for effect in trigger.effects))
        self.assertEqual("$source.zone_object", trigger.effects[0]["card"])
        self.assertEqual("$source", trigger.effects[1]["source"])
        effect, = adventure.nodes
        self.assertIn(CONTROLLER_PRIVATE_TOP_EXILE_CAPABILITY, effect.capability_dependencies)
        admission, semantics = register_card(self.db, self.registry, PRIVATE_ADVENTURE)
        self.assertTrue(admission["strict_capability_ready"], admission["blockers"])
        program, = (row for row in semantics.programs_for_oracle(original.oracle_id) if row.event == "resolve")
        dependencies = tuple(value for value in program.capability_dependencies
                             if value != CONTROLLER_PRIVATE_TOP_EXILE_CAPABILITY)
        withdrawn = replace(program, capability_dependencies=dependencies,
                            capability_closure=self.registry.closure(dependencies, profile="commander_review").to_dict())
        wrong_event = replace(program, event="spell.cast")
        for candidate in (withdrawn, wrong_event):
            fact = bind_semantic_program_runtime(candidate, capability_registry=self.registry,
                                                 profile="commander_review")
            self.assertFalse(fact["strict"])
        for before, after in (("face down", "face up"), ("may play", "may cast"),
                              ("a Wizard", "a Wizard or a Shaman"), ("top two", "top three")):
            faces = (original.faces[0], {**original.faces[1],
                    "oracle_text": original.faces[1]["oracle_text"].replace(before, after)})
            changed = replace(original, faces=faces, oracle_text=original.oracle_text.replace(before, after))
            self.assertNotEqual("exact", compile_source(changed).status)
        with patch("quorune.compiler.discover_templates.controller_private_top_play_template", return_value=None):
            self.assertNotEqual("exact", compile_source(original).status)
