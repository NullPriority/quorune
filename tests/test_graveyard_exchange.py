"""Living Death resolves three separate simultaneous batches through normal commands."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import json
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.rules.graveyard_exchange_model import (
    GRAVEYARD_EXCHANGE_CAPABILITY,
    GRAVEYARD_EXCHANGE_FIELD,
    graveyard_exchange_exiled_objects,
    graveyard_exchange_program_dependencies,
    validate_graveyard_exchange_phase,
)
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class GraveyardExchangeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "graveyard-exchange.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([
            fixtures / "bound-effect-program-cards.json",
            fixtures / "graveyard-exchange-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Generic Bound Commander"
        cls.deck = DeckDefinition("Graveyard exchange", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], [commander])

    def test_living_death_compiles_exact_order_and_fails_closed(self):
        card = self.db.lookup("Living Death", fuzzy=False)
        ir = compile_oracle_card(card, capability_registry=self.registry)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        self.assertEqual(("exile", "sacrifice", "return"), tuple(effect["phase"] for effect in node.effects))
        self.assertEqual((GRAVEYARD_EXCHANGE_CAPABILITY, "zone.linked_exile_return.fixed"),
                         node.capability_dependencies)
        self.assertEqual(card.oracle_text, card.oracle_text[node.span.start : node.span.end])
        malformed = replace(card, oracle_text=card.oracle_text.replace("all creatures they control", "a creature they control"))
        self.assertNotEqual("exact", compile_oracle_card(malformed, capability_registry=self.registry).status)

        program = SimpleNamespace(event="resolve", active_zone="stack", target_schema=None, cost_schema=None,
                                  effects=node.effects)
        self.assertEqual((GRAVEYARD_EXCHANGE_CAPABILITY,), graveyard_exchange_program_dependencies(program))
        version_forgery = ({**node.effects[0], GRAVEYARD_EXCHANGE_FIELD: True}, *node.effects[1:])
        for effects in (node.effects[1:], tuple(reversed(node.effects)), version_forgery,
                        (*node.effects, {"op": "draw", "amount": 1})):
            with self.assertRaises(ValueError):
                graveyard_exchange_program_dependencies(SimpleNamespace(**{**vars(program), "effects": effects}))
        with self.assertRaises(ValueError):
            validate_graveyard_exchange_phase({**node.effects[0], GRAVEYARD_EXCHANGE_FIELD: True})
        with self.assertRaises(ValueError):
            graveyard_exchange_exiled_objects({"next_phase": "sacrifice", "objects": []}, phase="return")

        registry_path = ROOT / "quorune/rules/capability-registry.json"
        for dependency in (GRAVEYARD_EXCHANGE_CAPABILITY, "zone.linked_exile_return.fixed",
                           "zone.change.destination_replacement", "trigger.placement.apnap",
                           "zone.graveyard.ordered_simultaneous_arrival", "zone.composition.departure_order"):
            data = json.loads(registry_path.read_text(encoding="utf-8"))
            entry = next(entry for entry in data["capabilities"] if entry["id"] == dependency)
            entry.update(status="blocked", blockers=["Focused graveyard-exchange dependency mutation"])
            registry = CapabilityRegistry(data)
            registry.mark_evidence_verified(self.registry.evidence_fingerprint)
            with self.subTest(dependency=dependency):
                self.assertNotEqual("exact", compile_oracle_card(card, capability_registry=registry).status)

    def _cast_living_death(self, game, spell):
        game.state.config.semantic_policy = "trusted_only"
        self.assert_closed(game, spell)
        self.prepare(game, mana={"B": 5})
        self.checkpoint(game)
        self.cast(game, "A", spell)

    def test_living_death_keeps_owners_and_excludes_replacement_sacrifices_from_return(self):
        game = self.session(608_021_01)
        engine = game.engine
        spell = self.add(engine, "Living Death", zone="hand", ref="LIVING-DEATH")
        first = self.add(engine, "Grizzly Bears", zone="graveyard", ref="FIRST-A")
        second = self.add(engine, "Grizzly Bears", seat="B", zone="graveyard", ref="FIRST-B")
        visionary = self.add(engine, "Elvish Visionary", seat="D", zone="graveyard", ref="FIRST-D")
        noncreature = self.add(engine, "Forest", seat="D", zone="graveyard", ref="GRAVE-NONCREATURE")
        unrelated = self.add(engine, "Grizzly Bears", seat="C", zone="exile", ref="UNRELATED-EXILE")
        redirected = self.add(engine, "Grizzly Bears", ref="SACRIFICE-TO-EXILE")
        walker = self.add(engine, "Dauthi Voidwalker", seat="B", ref="REPLACEMENT")
        traveler = self.add(engine, "Doomed Traveler", seat="B", ref="BORROWED")
        engine.change_control(traveler.object_id, "C", reason="Fixture sets different owner and controller")
        self._cast_living_death(game, spell)
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["B"]), (pending.kind, pending.actors))
        ordered = game.act("pilot:B", {"action_id": "choose", "cards": [traveler.ref, walker.ref]})
        self.assertTrue(ordered.ok, ordered.summary)

        current = lambda card: game.state.cards[card.object_id]
        for card in (first, second, visionary):
            self.assertEqual(("battlefield", card.owner), (current(card).zone, current(card).controller))
        self.assertEqual("graveyard", current(noncreature).zone)
        self.assertEqual("exile", current(unrelated).zone)
        self.assertEqual("exile", current(redirected).zone)
        self.assertEqual("graveyard", current(walker).zone)
        self.assertEqual(("graveyard", "B"), (current(traveler).zone, current(traveler).owner))
        self.assertEqual("graveyard", current(spell).zone)

        queued = {(item.source_object_id, item.controller) for item in game.state.stack}
        self.assertEqual({(traveler.object_id, "C"), (visionary.object_id, "D")}, queued)
        self.assertIsNone(self.resolve(game))
        self.replay(game)

    def test_living_death_replacement_choice_replays_only_uncommitted_stage(self):
        game = self.session(608_021_02)
        engine = game.engine
        spell = self.add(engine, "Living Death", zone="hand", ref="LIVING-CONTINUATION")
        first = self.add(engine, "Grizzly Bears", zone="graveyard", ref="ORIGINAL-EXILE")
        second = self.add(engine, "Grizzly Bears", seat="C", zone="graveyard", ref="ORIGINAL-C-EXILE")
        ordinary = self.add(engine, "Grizzly Bears", ref="SACRIFICED-A")
        first_walker = self.add(engine, "Dauthi Voidwalker", seat="B", ref="WALKER-B")
        second_walker = self.add(engine, "Dauthi Voidwalker", seat="C", ref="WALKER-C")
        self._cast_living_death(game, spell)
        pending = self.resolve(game)
        self.assertEqual(("replacement.order", ["A"]), (pending.kind, pending.actors))
        item, = game.state.stack
        journal = item.context[GRAVEYARD_EXCHANGE_FIELD]
        self.assertEqual("sacrifice", journal["next_phase"])
        self.assertEqual({first.object_id, second.object_id}, {entry["object_id"] for entry in journal["objects"]})
        for retained in journal["objects"]:
            current = game.state.cards[retained["object_id"]]
            self.assertEqual(current.logical_object_id, retained["logical_object_id"])
            self.assertEqual("exile", current.zone)
        for card in (ordinary, first_walker, second_walker):
            self.assertEqual("battlefield", game.state.cards[card.object_id].zone)

        game = self.replay(game, load=True)
        options = game.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
        self.assertEqual(2, len(options))
        response = {"action_id": "choose", "replacement": options[0]["id"]}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", response).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        accepted = game.act("pilot:A", response)
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertIsNone(self.resolve(game))
        for card in (first, second):
            moved = game.state.cards[card.object_id]
            self.assertEqual(("battlefield", card.owner), (moved.zone, moved.controller))
        for card in (ordinary, first_walker, second_walker):
            self.assertEqual("exile", game.state.cards[card.object_id].zone)
        self.replay(game, load=True)

    def test_living_death_orders_each_owners_actual_arrivals_before_return_and_replays(self):
        game = self.session(404_301_11)
        engine = game.engine
        spell = self.add(engine, "Living Death", zone="hand")
        original = self.add(engine, "Grizzly Bears", zone="graveyard", ref="RETURN-EXILE")
        a_ordinary = self.add(engine, "Grizzly Bears", ref="OWNER-A-ARRIVAL")
        a_borrowed = self.add(engine, "Doomed Traveler", ref="OWNER-A-HIDDEN-ARRIVAL")
        engine.change_control(a_borrowed.object_id, "B", reason="Fixture retains a different controller")
        a_borrowed = game.state.cards[a_borrowed.object_id]
        a_borrowed.face_down = True
        a_borrowed.known_to = ["B"]
        a_borrowed.revealed_to = []
        from quorune.manifest import library_face_down_annotations
        a_borrowed.annotations.update(library_face_down_annotations(cloaked=False))
        b_first = self.add(engine, "Grizzly Bears", seat="B", ref="OWNER-B-ONE")
        b_second = self.add(engine, "Grizzly Bears", seat="B", ref="OWNER-B-TWO")

        self._cast_living_death(game, spell)
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["A"]), (pending.kind, pending.actors))
        self.assertEqual("sacrifice", game.state.stack[0].context[GRAVEYARD_EXCHANGE_FIELD]["next_phase"])
        a_packet = game.packet("pilot:A", full=True)["decision"]
        a_refs = [a_borrowed.ref, a_ordinary.ref]
        self.assertEqual(set(a_refs), set(a_packet["legal_actions"][0]["choice_schema"]["legal_refs"]))
        objects = {row["id"]: row["name"] for row in a_packet["ctx"]["objects"]}
        self.assertEqual("Face-down component", objects[a_borrowed.ref])
        game = self.replay(game, load=True)
        accepted = {"action_id": "choose", "cards": a_refs}
        before = authoritative_state_hash(game.state)
        for principal, response in [
            ("pilot:B", accepted), ("pilot:A", {"action_id": "choose", "cards": [a_refs[0], a_refs[0]]}),
        ]:
            self.assertFalse(game.act(principal, response).ok)
            self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:A", accepted)
        self.assertTrue(result.ok, result.summary)

        pending = game.state.pending_decision
        self.assertEqual(("semantic.choice", ["B"]), (pending.kind, pending.actors))
        self.assertEqual("exile", game.state.cards[original.object_id].zone)
        for card in (a_ordinary, a_borrowed, b_first, b_second):
            self.assertEqual("battlefield", game.state.cards[card.object_id].zone)
        result = game.act("pilot:B", {"action_id": "choose", "cards": [b_second.ref, b_first.ref]})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("battlefield", game.state.cards[original.object_id].zone)
        self.assertEqual(
            [a_borrowed.object_id, a_ordinary.object_id, spell.object_id],
            game.state.players["A"].zones["graveyard"][-3:],
        )
        self.assertEqual(
            [b_second.object_id, b_first.object_id], game.state.players["B"].zones["graveyard"][-2:],
        )
        self.replay(game, load=True)
