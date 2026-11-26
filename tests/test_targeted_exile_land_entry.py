"""A self-cast trigger exiles one opponent's library top and then recruits land cards."""

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
from quorune.targeted_exile_land_entry import (
    TARGETED_EXILE_LAND_ENTRY_CAPABILITY, targeted_exile_land_entry_instruction,
    validate_targeted_exile_land_entry,
)
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card
from test_qualified_zone_event_queries import query_record


PROFILE = "commander_review"
CARD = "Oblivion Sower"
TEXT = ("When you cast this spell, target opponent exiles the top four cards of their library, then you may "
        "put any number of land cards that player owns from exile onto the battlefield under your control.")


class TargetedExileLandEntryShapeTests(unittest.TestCase):
    def test_typed_exile_land_instruction_requires_fixed_roles_and_count(self):
        effect = targeted_exile_land_entry_instruction()
        self.assertEqual("targeted_exile_land_entry", validate_targeted_exile_land_entry(effect))
        roles = {"player": "A", "supplier": "B"}
        self.assertEqual("targeted_exile_land_entry", validate_targeted_exile_land_entry({**effect, **roles}, resolved=True))
        for change in ({"count": True}, {"count": 3}, {"supplier": "$controller"}, {"schema_version": 6},
                       {"destination": "hand"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_targeted_exile_land_entry({**effect, **change})
        with self.assertRaises(ValueError):
            validate_targeted_exile_land_entry({**effect, "player": "A", "supplier": "A"}, resolved=True)


class TargetedExileLandEntryNativeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "targeted-exile-land-entry.sqlite3"
        names = (
            "targeted-exile-land-entry-cards.json", "targeted-opponent-library-top-exile-cards.json",
            "color-cast-land-entry-cards.json", "activation-paid-creature-subtype-cards.json",
        )
        build_fixture_database([ROOT / "tests" / "fixtures" / name for name in names], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Public exile land entry", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def add_hidden(self, game, name, seat, zone, ref):
        card = self.add(game.engine, name, seat=seat, zone=zone, ref=ref)
        card.known_to = []
        card.revealed_to = []
        return card

    def empty_library(self, game, seat):
        for object_id in tuple(game.state.players[seat].zones["library"]):
            game.engine.move_card(object_id, "graveyard", reason="Set up a short library for the witness")

    def ready(self, game, *cards, counter_mana=False):
        for card in cards:
            self.assert_closed(game, card)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, mana={"C": 6})
        if counter_mana:
            game.state.players["B"].mana_pool["U"] = 2
        game.state.players["A"].land_plays_remaining = 0
        self.checkpoint(game)

    def reject(self, game, seat, command):
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:" + seat, command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def cast_and_target(self, game, source):
        self.cast(game, "A", source)
        self.assertEqual(0, game.state.players["A"].mana_pool.get("C", 0))
        self.assertEqual("semantic.target", game.state.pending_decision.kind)
        self.reject(game, "A", {"action_id": "choose", "targets": ["A"]})
        self.target(game, "A", ["B"])
        self.assertEqual(("spell", "triggered_ability"), tuple(item.kind for item in game.state.stack))

    def select(self, game, *, objects):
        result = game.act("pilot:A", {"action_id": "choose", "objects": objects})
        self.assertTrue(result.ok, result.summary)

    def test_actual_sower_uses_earlier_public_lands_after_its_spell_is_countered(self):
        game = self.session(76380201)
        source = self.add(game.engine, CARD, zone="hand", ref="sower-source")
        counter = self.add(game.engine, "Counterspell", seat="B", zone="hand", ref="sower-counterspell")
        watcher = self.add(game.engine, "Staff of the Mind Magus", ref="sower-entry-watcher")
        self.empty_library(game, "B")
        top = self.add_hidden(game, "Forest", "B", "library", "sower-private-top")
        nonland = self.add_hidden(game, "Grizzly Bears", "B", "library", "sower-private-nonland")
        earlier = self.add(game.engine, "Hallowed Fountain", seat="B", zone="exile", ref="sower-earlier-fountain")
        face_down = self.add_hidden(game, "Island", "B", "exile", "sower-exiled-hidden-land")
        face_down.face_down = True
        face_down.known_to = ["B"]
        wrong_owner = self.add(game.engine, "Forest", seat="C", zone="exile", ref="sower-wrong-owner")
        other_library = tuple(game.state.players["C"].zones["library"])
        self.ready(game, source, counter, watcher, earlier, counter_mana=True)
        self.cast_and_target(game, source)
        for seat in "ABCD":
            packet = json.dumps(game.packet("pilot:" + seat, full=True))
            for card in (top, nonland):
                self.assertFalse(card.ref in packet, f"Pilot {seat} saw a hidden library reference")
                self.assertFalse(card.object_id in packet, f"Pilot {seat} saw a hidden physical identity")
        self.cast_for(game, "B", counter, [game.state.stack[0].ref])
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["A"]), (pending.kind, pending.actors))
        self.assertEqual([], game.state.players["B"].zones["library"])
        self.assertEqual(other_library, tuple(game.state.players["C"].zones["library"]))
        for card in (top, nonland):
            current = game.state.cards[card.object_id]
            self.assertEqual(("exile", "B", False), (current.zone, current.owner, current.face_down))
            self.assertNotIn("temporary_play_permission", current.annotations)
        view = game.packet("pilot:A", full=True)["decision"]
        choice = view["legal_actions"][0]["choice_schema"]
        self.assertEqual({top.ref, earlier.ref}, set(choice["legal_refs"]))
        self.assertEqual((0, 2), (choice["minimum"], choice["maximum"]))
        for seat in "ABCD":
            packet = game.packet("pilot:" + seat, full=True)
            exiled = {row["id"]: row for row in packet["state"]["players"]["B"]["ex"]}
            self.assertEqual("Forest", exiled[top.ref]["n"])
            self.assertEqual("Hallowed Fountain", exiled[earlier.ref]["n"])
            hidden_row = exiled[face_down.ref]
            if seat == "B":
                self.assertEqual("Island", hidden_row["n"])
            else:
                self.assertEqual({"id": face_down.ref, "n": "?", "fd": 1}, hidden_row)
            self.assertFalse(face_down.ref in json.dumps(packet.get("decision")),
                             f"Pilot {seat} saw the face-down card in the choice")
            self.assertFalse(face_down.object_id in json.dumps(packet),
                             f"Pilot {seat} saw the face-down card's physical identity")
        for references in ([face_down.ref], [wrong_owner.ref], [nonland.ref], [top.ref, top.ref]):
            self.reject(game, "A", {"action_id": "choose", "objects": references})
        self.reject(game, "B", {"action_id": "choose", "objects": [earlier.ref]})
        game = self.replay(game, load=True)
        self.select(game, objects=[top.ref, earlier.ref])
        self.assertEqual("semantic.choice", game.state.pending_decision.kind)
        pay = game.packet("pilot:A", full=True)["decision"]
        self.assertIn("Pay 2 life", pay["ctx"]["prompt"])
        self.assertEqual("exile", game.state.cards[top.object_id].zone)
        self.assertEqual("exile", game.state.cards[earlier.object_id].zone)
        self.reject(game, "A", {"action_id": "choose", "pay": "yes"})
        game = self.replay(game, load=True)
        result = game.act("pilot:A", {"action_id": "choose", "pay": True})
        self.assertTrue(result.ok, result.summary)
        arrivals = tuple(game.state.cards[card.object_id] for card in (top, earlier))
        self.assertEqual(1, len({card.zone_timestamp for card in arrivals}))
        self.assertEqual({("battlefield", "B", "A", False)},
                         {(card.zone, card.owner, card.controller, card.tapped) for card in arrivals})
        self.assertEqual(38, game.state.players["A"].life)
        self.assertEqual(40, game.state.players["B"].life)
        self.assertEqual(0, game.state.players["A"].land_plays_remaining)
        self.assertEqual(1, self.trigger_count(game, watcher))
        self.assertIsNone(self.resolve(game))
        self.assertEqual(39, game.state.players["A"].life)
        self.replay(game, load=True)

    def test_actual_sower_exiles_exactly_four_and_accepts_an_empty_selection(self):
        game = self.session(76380202)
        source = self.add(game.engine, CARD, zone="hand")
        added = tuple(self.add_hidden(game, "Forest", "B", "library", f"sower-top-{index}") for index in range(5))
        self.ready(game, source)
        self.cast_and_target(game, source)
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        self.assertEqual("library", game.state.cards[added[0].object_id].zone)
        self.assertTrue(all(game.state.cards[card.object_id].zone == "exile" for card in added[1:]))
        self.select(game, objects=[])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("battlefield", game.state.cards[source.object_id].zone)
        self.assertTrue(all(game.state.cards[card.object_id].zone == "exile" for card in added[1:]))
        self.assertEqual(0, game.state.players["A"].land_plays_remaining)
        self.replay(game, load=True)

    def test_actual_sower_limits_multiple_entry_payments_to_the_controllers_life(self):
        game = self.session(76380204)
        source = self.add(game.engine, CARD, zone="hand")
        self.empty_library(game, "B")
        first = self.add(game.engine, "Hallowed Fountain", seat="B", zone="exile", ref="sower-payable-entry")
        second = self.add(game.engine, "Hallowed Fountain", seat="B", zone="exile", ref="sower-unpayable-entry")
        game.state.players["A"].life = 3
        self.ready(game, source, first)
        self.cast_and_target(game, source)
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        self.select(game, objects=[second.ref, first.ref])
        self.assertEqual("semantic.choice", game.state.pending_decision.kind)
        result = game.act("pilot:A", {"action_id": "choose", "pay": True})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("priority", game.state.pending_decision.kind)
        self.assertEqual(1, game.state.players["A"].life)
        self.assertEqual(40, game.state.players["B"].life)
        self.assertFalse(game.state.cards[first.object_id].tapped)
        self.assertTrue(game.state.cards[second.object_id].tapped)
        self.assertIsNone(self.resolve(game))
        self.replay(game, load=True)

    def test_actual_sower_revalidates_the_original_opponent_before_either_instruction(self):
        game = self.session(76380203)
        source = self.add(game.engine, CARD, zone="hand")
        other = self.add_hidden(game, "Forest", "C", "library", "sower-unrelated-library")
        exile = self.add(game.engine, "Forest", seat="C", zone="exile", ref="sower-unrelated-exile")
        self.ready(game, source)
        self.cast_and_target(game, source)
        self.priority(game, "B")
        result = game.act("pilot:B", {"action_id": "concede", "choices": {"confirm_concede": True}})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("library", game.state.cards[other.object_id].zone)
        self.assertEqual("exile", game.state.cards[exile.object_id].zone)
        self.assertEqual("battlefield", game.state.cards[source.object_id].zone)
        self.replay(game, load=True)

    def test_sower_strict_binding_rejects_a_changed_scope_and_withdrawn_capability(self):
        report, semantics = register_card(self.db, self.registry, CARD)
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        self.assertEqual("capability_closed", report["trust_basis"])
        program, = (item for item in semantics.programs_for_oracle(report["oracle_id"])
                    if TARGETED_EXILE_LAND_ENTRY_CAPABILITY in item.capability_dependencies)
        self.assertEqual(("spell.cast", "stack", "opponent"),
                         (program.event, program.active_zone, program.target_schema["player_relation"]))
        self.assertEqual(targeted_exile_land_entry_instruction(), dict(program.effects[0]))
        deps = tuple(key for key in program.capability_dependencies if key != TARGETED_EXILE_LAND_ENTRY_CAPABILITY)
        omitted = replace(program, capability_dependencies=deps,
                          capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
        binding = bind_semantic_program_runtime(omitted, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(binding["strict"])
        self.assertIn(f"capability:undeclared_runtime_dependency:{TARGETED_EXILE_LAND_ENTRY_CAPABILITY}", binding["blockers"])

        options = {"capability_registry": self.registry, "capability_profile": PROFILE}
        for old, new in (("When you cast this spell", "Whenever you cast a spell"),
                         ("target opponent", "target player"), ("top four", "top five"),
                         ("that player owns", "you own"), ("under your control", "under their control")):
            probe = replace(query_record(TEXT.replace(old, new)), type_line="Creature — Eldrazi")
            with self.subTest(clause=new):
                self.assertNotEqual("exact", compile_oracle_card(probe, **options).status)

        minimal = replace(query_record(TEXT), type_line="Creature — Eldrazi")
        self.assertEqual("exact", compile_oracle_card(minimal, **options).status)
        with patch("quorune.compiler.opponent_top_exile_templates.opponent_top_exile_trigger_result", return_value=None):
            self.assertNotEqual("exact", compile_oracle_card(minimal, **options).status)
