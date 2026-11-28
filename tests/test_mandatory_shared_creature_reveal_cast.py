"""Actual controller upkeep, public reveal, optional free cast and bottom."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.shared_type_library import mandatory_shared_type_cast_program_dependencies
from quorune.rules.shared_type_library_model import SHARED_TYPE_REVEAL_CAST_CAPABILITY
from scripts.build_test_database import build_fixture_database

CARD = "Descendants' Path"
FIXTURES = ROOT / "tests" / "fixtures"


class MandatorySharedCreatureRevealCastTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        files = {
            "bound-effect-program-cards": {"Generic Bound Commander", "Generic Bound Plains", "Generic Bound Growth"},
            "shared-type-library-cards": {"Grizzly Bears", "Colossal Dreadmaw", "Nameless Inversion"},
            "changeling-characteristics-cards": {"Universal Automaton"},
            "permanent-additional-costs": {"Makeshift Mauler"},
        }
        pieces = [FIXTURES / "upkeep-reveal-shared-creature-cast-cards.json"]
        for name, wanted in files.items():
            document = json.loads((FIXTURES / (name + ".json")).read_bytes())
            cards = [card for card in document["cards"] if card["name"] in wanted]
            assert wanted == {card["name"] for card in cards}, name
            ids = {card["oracle_id"] for card in cards}
            document["rulings"] = [row for row in document.get("rulings", ()) if row["oracle_id"] in ids]
            piece = folder / (name + ".json")
            piece.write_text(json.dumps({**document, "cards": cards}), encoding="utf-8")
            pieces.append(piece)
        path = folder / "public-shared-creature-reveal.sqlite3"
        build_fixture_database(pieces, path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Public creature reveal", [
            DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def compile_original(self, *, text=None, registry=None):
        record = self.db.lookup(CARD, fuzzy=False)
        if text is not None:
            record = replace(record, oracle_text=text)
        return compile_oracle_card(record, capability_registry=registry or self.registry,
                                   capability_profile="commander_review")

    def scene(self, seed, *, top, ally="Grizzly Bears", ally_seat="A", cost=None):
        game = self.session(seed)
        engine = game.engine
        source = self.add(engine, CARD)
        game.state.config.semantic_policy = "trusted_only"
        self.assert_closed(game, source)
        self.add(engine, ally, seat=ally_seat, ref="path-creature-witness")
        paid = None
        if cost:
            paid = self.add(engine, "Grizzly Bears", zone="graveyard", ref="path-mandatory-exile")
        hidden = self.add(engine, "Generic Bound Growth", seat="C", zone="hand", ref="path-hidden-hand")
        if top is None:
            for object_id in tuple(game.state.players["A"].zones["library"]):
                engine.move_card(object_id, "exile", log=False, semantic_events=False)
            revealed = None
        else:
            revealed = self.add(engine, top, zone="library", ref="path-original-top")
            revealed.known_to = []
            revealed.revealed_to = []
        engine.state.active_player = "A"
        engine.state.phase, engine.state.step, engine.state.phase_index = "beginning", "untap", 0
        engine._grant_priority("A")
        engine.pump()
        self.checkpoint(game)
        for principal in ("pilot:A", "pilot:B", "pilot:C", "pilot:D", "spectator"):
            self.assertFalse("path-original-top" in json.dumps(game.packet(principal, full=True)), principal)
        self.until(game, lambda g: g.state.step == "upkeep" and bool(self.trigger_count(g, source)))
        self.assertEqual("A", game.state.active_player)
        return game, source, revealed, hidden, paid

    def options(self, game, candidate):
        pending = game.state.pending_decision
        self.assertEqual(("semantic.choice", ["A"]), (pending.kind, pending.actors))
        schema = pending.payload_by_actor["A"]["legal_actions"][0]["choice_schema"]
        self.assertEqual(["cast", "decline"], schema["legal_values"])
        self.assertTrue(all(option["card"] == candidate.ref for option in schema["cast_options"]))
        return schema["cast_options"]

    def reject(self, game, seat, command):
        original = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:" + seat, command).ok)
        self.assertEqual(original, authoritative_state_hash(game.state))

    def assert_public_reveal(self, game, candidate):
        events = [event for event in game.state.events if event.code == "library.top.reveal"]
        self.assertEqual([candidate.printed_name], [event.details["card"] for event in events])
        if game.state.players["A"].zones["library"][-1:] == [candidate.object_id]:
            for principal in ("pilot:B", "pilot:C", "pilot:D"):
                self.assertTrue(candidate.ref in json.dumps(game.packet(principal, full=True)), principal)
        spectator = game.packet("spectator", full=True)
        self.assertTrue(any(event.get("c") == "library.top.reveal" and candidate.printed_name in event["s"]
                            for event in spectator["events"]))

    def test_actual_public_reveal_casts_only_the_matching_card_and_replays(self):
        game, _, top, hidden, _ = self.scene(3344501, top="Universal Automaton")
        game = self.replay(game, load=True)
        self.resolve(game)
        self.assert_public_reveal(game, top)
        option, = self.options(game, top)
        self.assertEqual(0, sum(option["requirements"].values()))
        game = self.replay(game, load=True)
        command = {"action_id": "choose", "choice": "cast", "cast": {"cost_option": option["id"]}}
        self.reject(game, "B", command)
        self.reject(game, "A", {**command, "cast": {**command["cast"], "card": hidden.ref}})
        self.assertTrue(game.act("pilot:A", command).ok)
        spell, = game.state.stack
        self.assertEqual(("A", "spell", top.object_id, 0),
                         (spell.controller, spell.kind, spell.card_object_id, spell.context["mana_spent_total"]))
        self.assertNotIn(top.object_id, game.state.players["A"].zones["library"])
        self.assertNotIn(hidden.ref, json.dumps(game.packet("pilot:D", full=True)))
        self.assertIsNone(self.resolve(game))
        self.replay(game, load=True)

    def test_actual_decline_noncreature_nonmatch_and_empty_library(self):
        scenes = [("Grizzly Bears", "A"), ("Nameless Inversion", "A"),
                  ("Colossal Dreadmaw", "A"), ("Grizzly Bears", "B"), (None, "A")]
        for index, (name, owner) in enumerate(scenes):
            with self.subTest(top=name, matching_creature_controller=owner):
                game, _, top, _, _ = self.scene(3344510 + index, top=name, ally_seat=owner)
                order = tuple(game.state.players["A"].zones["library"])
                pending = self.resolve(game)
                if index == 0:
                    self.options(game, top)
                    game = self.replay(game, load=True)
                    command = {"action_id": "choose", "choice": "decline"}
                    self.reject(game, "D", command)
                    result = game.act("pilot:A", command)
                    self.assertTrue(result.ok, result.summary)
                else:
                    self.assertIsNone(pending)
                if top is not None:
                    self.assert_public_reveal(game, top)
                    self.assertEqual((top.object_id, *order[:-1]), tuple(game.state.players["A"].zones["library"]))
                else:
                    self.assertFalse(any(event.code == "library.top.reveal" for event in game.state.events))
                self.assertFalse(game.state.stack)
                self.replay(game, load=True)

    def test_actual_matching_creature_keeps_its_mandatory_additional_cost(self):
        for price_available in (True, False):
            with self.subTest(price_available=price_available):
                game, _, top, _, price = self.scene(3344520 + price_available, top="Makeshift Mauler",
                                                   ally="Universal Automaton", cost=price_available)
                self.resolve(game)
                self.assert_public_reveal(game, top)
                if price_available:
                    option, = self.options(game, top)
                    action = {"action_id": "choose", "choice": "cast", "cast": {"cost_option": option["id"]}}
                    self.reject(game, "A", {**action, "cast": {**action["cast"], "exile_cards": []}})
                    action["cast"]["exile_cards"] = [price.ref]
                    result = game.act("pilot:A", action)
                    self.assertTrue(result.ok, result.summary)
                    self.assertEqual("exile", game.state.cards[price.object_id].zone)
                    self.assertEqual(0, game.state.stack[-1].context["mana_spent_total"])
                    self.assertIsNone(self.resolve(game))
                    self.assertEqual("battlefield", game.state.cards[top.object_id].zone)
                else:
                    self.assertFalse(game.state.stack)
                    self.assertEqual(top.object_id, game.state.players["A"].zones["library"][0])
                self.replay(game, load=True)

    def test_exact_print_and_upkeep_scope_fail_closed(self):
        actual = self.compile_original()
        self.assertEqual("exact", actual.status, actual.material_residuals)
        node, = actual.faces[0].nodes
        self.assertIn(SHARED_TYPE_REVEAL_CAST_CAPABILITY, node.capability_dependencies)
        self.assertEqual(2, node.effects[0]["result"]["schema_version"])
        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        authority = next(row for row in raw["capabilities"] if row["id"] == SHARED_TYPE_REVEAL_CAST_CAPABILITY)
        authority.update(status="blocked", blockers=["Scoped original"])
        self.assertNotEqual("exact", self.compile_original(registry=CapabilityRegistry(raw)).status)
        original = self.db.lookup(CARD).oracle_text
        for changed in (original.replace("your upkeep", "each upkeep"),
                        original.replace("a creature you control", "a creature an opponent controls"),
                        original.replace("put it on the bottom", "leave it on top")):
            with self.subTest(changed=changed):
                self.assertNotEqual("exact", self.compile_original(text=changed).status)
        game, source, _, _, _ = self.scene(3344529, top="Grizzly Bears")
        program, = game.engine.semantics.programs_for_oracle(source.oracle_id)
        self.assertIn(SHARED_TYPE_REVEAL_CAST_CAPABILITY, mandatory_shared_type_cast_program_dependencies(program))
        with self.assertRaises(ValueError):
            mandatory_shared_type_cast_program_dependencies(replace(program, event_condition=None))

    def test_dropping_the_mandatory_reveal_lowering_is_killed(self):
        def witness():
            ir = self.compile_original()
            self.assertEqual("exact", ir.status, ir.material_residuals)
        witness()
        with patch("quorune.compiler.shared_type_library_templates.shared_type_library_result_template", return_value=None):
            with self.assertRaises(AssertionError):
                witness()
