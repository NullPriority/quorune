"""Omen alternative faces use the ordinary cast, replacement, and shuffle owners."""

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
import optional_result_followup_fixture as actions
from quorune.carddb import CardDatabase, CardRecord
from quorune.cast_face_offers import OMEN_CONTEXT, OMEN_COVERAGE
from quorune.cast_lifecycles import fixed_cast_lifecycle_resolution_destination
from quorune.deck import DeckDefinition, DeckEntry
from quorune.model import CardInstance, GameConfig, GameState, StackItem
from quorune.omen import OMEN_CAPABILITY, omen_stack_context
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class OmenContractTests(unittest.TestCase):
    def test_omen_capability_is_bound_only_to_the_typed_alternative_face(self):
        record = CardRecord(
            oracle_id="00000000-0000-4000-8000-000000000720", name="Dragon // Forewarning",
            mana_cost="{3}{U} // {U}", mana_value=4, type_line="Creature — Dragon // Instant — Omen",
            oracle_text="", power="3", toughness="3", loyalty=None, defense=None,
            colors=("U",), color_identity=("U",), keywords=("Flying",), produced_mana=(),
            layout="adventure", released_at="2025-04-11", legalities={"commander": "legal"},
            faces=(
                {"name": "Dragon", "type_line": "Creature — Dragon", "mana_cost": "{3}{U}",
                 "oracle_text": "Flying", "power": "3", "toughness": "3", "colors": ["U"]},
                {"name": "Forewarning", "type_line": "Instant — Omen", "mana_cost": "{U}",
                 "oracle_text": "Draw a card.", "colors": ["U"]},
            ),
            raw={},
        )
        ir = compile_oracle_card(record, capability_registry=load_default_capability_registry())
        self.assertEqual("exact", ir.status)
        faces = {face.face_name: face for face in ir.faces}
        self.assertTrue(any(OMEN_CAPABILITY in node.capability_dependencies for node in faces["Forewarning"].nodes))
        self.assertTrue(any(OMEN_COVERAGE in node.runtime_coverage for node in faces["Forewarning"].nodes))
        self.assertFalse(any(OMEN_CAPABILITY in node.capability_dependencies for node in faces["Dragon"].nodes))
        card = CardInstance(
            object_id="omen", ref="omen", oracle_id=record.oracle_id, printed_name=record.name,
            owner="A", controller="A", zone="stack", active_face="Forewarning",
        )
        self.assertEqual({}, omen_stack_context(record, card, cast_face_version=1))
        self.assertEqual(
            {OMEN_CONTEXT: {"schema_version": 1, "face": "Forewarning"}},
            omen_stack_context(record, card, cast_face_version=2),
        )

    def stack_omen(self):
        return StackItem(
            "stack-id", "stack-ref", "spell", "A", "Omen", card_object_id="physical-card",
            chosen_face="Omen", context={OMEN_CONTEXT: {"schema_version": 1, "face": "Omen"}},
        )

    def test_omen_stack_binding_and_legacy_face_versions_are_closed(self):
        item = self.stack_omen()
        self.assertEqual(item, StackItem.from_dict(item.to_dict()))
        self.assertEqual("library", fixed_cast_lifecycle_resolution_destination(item, "graveyard"))
        self.assertEqual("library", fixed_cast_lifecycle_resolution_destination(item, "library"))
        self.assertEqual("exile", fixed_cast_lifecycle_resolution_destination(item, "exile"))
        for raw in (None, {}, {"schema_version": True, "face": "Omen"},
                    {"schema_version": 1, "face": "other"}, {"schema_version": 1, "face": "Omen", "extra": 1}):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                StackItem.from_dict({**item.to_dict(), "context": {OMEN_CONTEXT: raw}})
        with self.assertRaises(ValueError):
            StackItem.from_dict({**item.to_dict(), "kind": "triggered_ability"})
        with self.assertRaises(ValueError):
            StackItem.from_dict({**item.to_dict(), "card_object_id": None})
        state = GameState("omen-version", GameConfig(), {}, {}, {}, {}, [], None, None)
        legacy = {**state.to_dict(), "cast_face_offers_version": 1}
        self.assertEqual(legacy, GameState.from_dict(legacy).to_dict())
        legacy_item = StackItem.from_dict({**item.to_dict(), "context": {}})
        self.assertEqual("graveyard", fixed_cast_lifecycle_resolution_destination(legacy_item, "graveyard"))

    def test_omitting_intrinsic_omen_destination_is_detected(self):
        with patch("quorune.omen.omen_resolution_destination", side_effect=lambda item, destination: destination):
            with self.assertRaises(AssertionError):
                self.test_omen_stack_binding_and_legacy_face_versions_are_closed()


class OmenRuntimeTests(actions.OptionalResultFollowupFixture, unittest.TestCase):
    name = "Twinmaw Stormbrood // Charring Bite"

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "omen-resolution.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/bound-effect-program-cards.json",
            ROOT / "tests/fixtures/fixed-stack-spell-copy-cards.json",
            ROOT / "tests/fixtures/graveyard-exile-payment-cards.json",
            ROOT / "tests/fixtures/state-based-destination-cards.json",
            ROOT / "tests/fixtures/omen-resolution-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Omen resolution", [
            DeckEntry("Generic Bound Commander", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def prepare(self, session, **kwargs):
        session.state.config.semantic_policy = "trusted_only"
        super().prepare(session, **kwargs)

    def shuffle_counts(self, session):
        return {seat: session.state.players[seat].stats.get("shuffle_count", 0) for seat in "ABCD"}

    def test_original_and_opponent_copy_shuffle_their_owners_libraries_and_replay(self):
        session = self.session(720301)
        spell = self.add(session.engine, self.name, zone="hand")
        first = self.add(session.engine, "Grizzly Bears", seat="C")
        second = self.add(session.engine, "Grizzly Bears", seat="D", ref="omen-copy-target")
        copier = self.add(session.engine, "Reverberate", seat="B", zone="hand")
        private = self.add(session.engine, "Generic Bound Growth", seat="D", zone="hand", ref="omen-private-card")
        self.assert_closed(session, spell)
        self.assert_closed(session, copier)
        self.prepare(session, mana={"C": 1, "R": 1})
        session.state.players["B"].mana_pool["R"] = 2
        counts = self.shuffle_counts(session)
        self.checkpoint(session)

        self.cast_face(session, spell, "Charring Bite", [first.ref])
        original = session.state.stack[-1]
        self.assertEqual("Charring Bite", original.context[OMEN_CONTEXT]["face"])
        self.cast(session, "B", copier, [original.ref])
        self.assertEqual("semantic.choice", self.resolve(session).kind)
        self.assertEqual(["B"], session.state.pending_decision.actors)
        self.assertEqual(counts, self.shuffle_counts(session))
        command = {"action_id": "choose", "targets": [second.ref]}
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act("pilot:A", command).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        session = self.replay(session, load=True)
        result = session.act("pilot:B", command)
        self.assertTrue(result.ok, result.summary)
        copied = session.state.stack[-1]
        self.assertEqual(("spell_copy", "Charring Bite"), (copied.kind, copied.chosen_face))
        self.assertEqual(original.context[OMEN_CONTEXT], copied.context[OMEN_CONTEXT])
        self.assertEqual("B", session.state.cards[copied.card_object_id].owner)

        self.resolve_top(session)
        self.assertEqual({**counts, "B": counts["B"] + 1}, self.shuffle_counts(session))
        self.assertNotIn(copied.card_object_id, session.state.players["B"].zones["library"])
        self.assertEqual("graveyard", session.state.cards[second.object_id].zone)
        self.assertEqual("battlefield", session.state.cards[first.object_id].zone)
        self.settle(session)
        self.assertEqual({**counts, "A": counts["A"] + 1, "B": counts["B"] + 1}, self.shuffle_counts(session))
        self.assertEqual("graveyard", session.state.cards[first.object_id].zone)
        resolved = session.state.cards[spell.object_id]
        self.assertEqual(("library", [], []), (resolved.zone, resolved.known_to, resolved.revealed_to))
        self.assertEqual("Creature — Dragon", session.engine._effective_card_data(resolved)["type_line"])
        for seat in "ABC":
            self.assertNotIn(private.ref, json.dumps(session.packet("pilot:" + seat, full=True)))
        self.assertIn(private.ref, json.dumps(session.packet("pilot:D", full=True)))
        self.replay(session, load=True)

    def test_countered_fizzled_and_normal_face_casts_do_not_shuffle(self):
        session = self.session(720302)
        countered = self.add(session.engine, self.name, zone="hand", ref="countered-omen")
        fizzled = self.add(session.engine, self.name, zone="hand", ref="fizzled-omen")
        normal = self.add(session.engine, self.name, zone="hand", ref="normal-omen-creature")
        trawlers = self.add(session.engine, "Kishla Trawlers", zone="hand")
        payment = self.add(session.engine, "Grizzly Bears", zone="graveyard", ref="trawlers-payment")
        instant = self.add(session.engine, "Counterspell", zone="graveyard", ref="trawlers-instant")
        target = self.add(session.engine, "Grizzly Bears", seat="C")
        counter = self.add(session.engine, "Counterspell", seat="B", zone="hand")
        removal = self.add(session.engine, "Lightning Bolt", seat="B", zone="hand")
        self.prepare(session, mana={"C": 9, "R": 2, "W": 1, "U": 1})
        session.state.players["B"].mana_pool.update({"U": 2, "R": 1})
        counts = self.shuffle_counts(session)
        self.checkpoint(session)

        self.cast_face(session, countered, "Charring Bite", [target.ref])
        self.cast(session, "B", counter, [session.state.stack[-1].ref])
        self.settle(session)
        self.assertEqual("graveyard", session.state.cards[countered.object_id].zone)
        self.assertEqual(("battlefield", 0), (session.state.cards[target.object_id].zone,
                                             session.state.cards[target.object_id].marked_damage))
        self.assertEqual(counts, self.shuffle_counts(session))

        self.cast_face(session, fizzled, "Charring Bite", [target.ref])
        self.cast(session, "B", removal, [target.ref])
        self.settle(session)
        self.assertEqual("graveyard", session.state.cards[fizzled.object_id].zone)
        self.assertEqual(counts, self.shuffle_counts(session))
        life = session.state.players["A"].life
        self.cast_face(session, normal, "Twinmaw Stormbrood")
        self.assertNotIn(OMEN_CONTEXT, session.state.stack[-1].context)
        self.settle(session)
        self.assertEqual("battlefield", session.state.cards[normal.object_id].zone)
        self.assertEqual(life + 5, session.state.players["A"].life)

        self.cast(session, "A", trawlers)
        self.assertEqual("semantic.choice", self.resolve(session).kind)
        result = session.act("pilot:A", {"action_id": "choose", "cards": [payment.ref]})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("semantic.target", session.state.pending_decision.kind)
        context = session.packet("pilot:A", full=True)["decision"]["ctx"]
        legal_refs = context["target_schema"]["legal_refs"]
        self.assertIn(instant.ref, legal_refs)
        self.assertNotIn(countered.ref, legal_refs)
        before = authoritative_state_hash(session.state)
        result = session.act("pilot:A", {"action_id": "choose", "targets": [countered.ref]})
        self.assertFalse(result.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        result = session.act("pilot:A", {"action_id": "choose", "targets": [instant.ref]})
        self.assertTrue(result.ok, result.summary)
        self.settle(session)
        self.assertEqual("hand", session.state.cards[instant.object_id].zone)
        graveyard_data = session.engine._effective_card_data(countered.object_id)
        self.assertEqual(("Twinmaw Stormbrood", "Creature — Dragon", 6),
                         (graveyard_data["name"], graveyard_data["type_line"], graveyard_data["mana_value"]))
        self.assertEqual(counts, self.shuffle_counts(session))
        self.replay(session, load=True)

    def test_registered_omen_runtime_requires_its_declared_capability(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime

        session = self.session(720303)
        spell = self.add(session.engine, "Riling Dawnbreaker // Signaling Roar", zone="hand")
        self.assert_closed(session, spell)
        program = next(program for program in session.engine.semantics.programs_for_oracle(spell.oracle_id)
                       if OMEN_COVERAGE in program.coverage)
        dependencies = tuple(capability for capability in program.capability_dependencies if capability != OMEN_CAPABILITY)
        missing = replace(program, capability_dependencies=dependencies,
                          capability_closure=self.registry.closure(dependencies, profile="commander_review").to_dict())
        binding = bind_semantic_program_runtime(missing, capability_registry=self.registry, profile="commander_review")
        self.assertFalse(binding["strict"])
        self.assertIn(f"capability:undeclared_runtime_dependency:{OMEN_CAPABILITY}", binding["blockers"])
        self.prepare(session, mana={"C": 1, "W": 1})
        counts = self.shuffle_counts(session)
        self.checkpoint(session)
        self.cast_face(session, spell, "Signaling Roar")
        self.settle(session)
        tokens = [card for card in session.state.cards.values() if card.is_token and card.zone == "battlefield"]
        self.assertEqual(1, len(tokens))
        token, = tokens
        data = session.engine._effective_card_data(token)
        self.assertIn("soldier", session.engine._type_parts(data["type_line"])[1])
        self.assertEqual((2, 2), tuple(
            session.engine._numeric_stat(token.object_id, stat) for stat in ("power", "toughness")
        ))
        self.assertEqual("library", session.state.cards[spell.object_id].zone)
        self.assertEqual({**counts, "A": counts["A"] + 1}, self.shuffle_counts(session))
        self.replay(session, load=True)

    def test_roost_seek_search_keeps_the_separate_omen_shuffle_and_legacy_mode(self):
        for version, destination, shuffles in ((2, "library", 2), (1, "graveyard", 1)):
            with self.subTest(version=version):
                session = self.session(720310 + version)
                session.state.cast_face_offers_version = version
                spell = self.add(session.engine, "Sagu Wildling // Roost Seek", zone="hand")
                land = self.add(session.engine, "Generic Bound Plains", zone="library", ref="roost-search-basic")
                self.assert_closed(session, spell)
                self.prepare(session, mana={"G": 1})
                counts = self.shuffle_counts(session)
                self.checkpoint(session)

                self.cast_face(session, spell, "Roost Seek")
                self.assertEqual("semantic.search", self.resolve(session).kind)
                packet = session.packet("pilot:A", full=True)
                legal = packet["decision"]["legal_actions"][0]["choice_schema"]["legal_refs"]
                self.assertIn(land.ref, legal)
                self.assertNotIn(land.ref, json.dumps(session.packet("pilot:B", full=True)))
                self.assertEqual(counts, self.shuffle_counts(session))
                session = self.replay(session, load=True)
                command = {"action_id": "choose", "search_cards": [land.ref]}
                result = session.act("pilot:A", command)
                self.assertTrue(result.ok, result.summary)
                self.settle(session)
                self.assertEqual("hand", session.state.cards[land.object_id].zone)
                self.assertEqual(destination, session.state.cards[spell.object_id].zone)
                self.assertEqual({**counts, "A": counts["A"] + shuffles}, self.shuffle_counts(session))
                self.replay(session, load=True)
