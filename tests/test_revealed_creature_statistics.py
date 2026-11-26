"""Printed post-Scry reveal, draw replacement and revealed-stat binding."""

from dataclasses import replace
import json
from pathlib import Path
import tempfile
from unittest import TestCase
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.evidence import validate_capability_evidence_index
from quorune.rules.evidence_storage import read_evidence_document
from quorune.rules.revealed_creature_statistics import (
    REVEALED_CREATURE_CAPABILITY, revealed_creature_dependencies, revealed_creature_effect,
)
from scripts.build_test_database import build_fixture_database
from scripts.update_capability_evidence import build_index, discover_tests
from test_bound_effect_programs import record


TEXT = (
    "Scry 5, then reveal the top card of your library. "
    "If it's a creature card, you draw cards equal to its power and you gain life equal to its toughness."
)
FIXTURE = "revealed-creature-statistics-cards"
ORACLE_ID = "ba9cd7bc-846f-4b5c-a1dc-9763a60dafc9"


class RevealedCreatureStatisticsCompilerTests(TestCase):
    def test_complete_scry_composition_and_closed_descriptor(self):
        registry = load_default_capability_registry()
        ir = compile_oracle_card(record(TEXT, "Sorcery"), capability_registry=registry)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        self.assertEqual((0, len(TEXT)), (node.span.start, node.span.end))
        self.assertIsNone(node.target_schema)
        self.assertEqual((
            {"op": "scry", "player": "$controller", "count": 5}, revealed_creature_effect(),
        ), node.effects)
        self.assertTrue({
            "library.scry.fixed_controller", REVEALED_CREATURE_CAPABILITY,
            "zone.draw.library_to_hand", "life.change.effect",
        }.issubset(node.capability_dependencies))
        for malformed in (
            {**revealed_creature_effect(), "schema_version": True},
            {**revealed_creature_effect(), "player": "B"},
            {**revealed_creature_effect(), "count": 10},
        ):
            with self.assertRaises(ValueError):
                revealed_creature_dependencies(malformed)
        for text in (
            TEXT.replace("Scry 5,", "Scry X,"),
            TEXT.replace("a creature card", "a permanent card"),
            TEXT.replace("its toughness", "its power"),
            TEXT + " Draw a card.",
        ):
            with self.subTest(text=text):
                self.assertNotEqual("exact", compile_oracle_card(
                    record(text, "Sorcery"), capability_registry=registry,
                ).status)
        with patch("quorune.compiler.revealed_library_top_templates.scry_revealed_creature_template", return_value=None):
            self.assertNotEqual("exact", compile_oracle_card(record(TEXT, "Sorcery"), capability_registry=registry).status)

    def test_reveal_withdrawal_retains_the_ordinary_scry_and_draw_owners(self):
        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        row = next(row for row in raw["capabilities"] if row["id"] == REVEALED_CREATURE_CAPABILITY)
        row.update(status="blocked", blockers=["Current controller reveal owner withdrawn"])
        registry = CapabilityRegistry(raw)
        index = build_index(
            registry_value=raw,
            declaration_source=read_evidence_document(ROOT / "platform/capability-evidence-declarations.json"),
            discovered_test_ids=set(discover_tests(ROOT).values()),
        )
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        ir = compile_oracle_card(record(TEXT, "Sorcery"), capability_registry=registry)
        self.assertNotEqual("exact", ir.status)
        self.assertTrue(any(REVEALED_CREATURE_CAPABILITY in blocker for residual in ir.material_residuals
                            for blocker in residual.blockers), ir.material_residuals)
        for text in ("Scry 5.", "Draw a card."):
            self.assertEqual("exact", compile_oracle_card(record(text, "Sorcery"), capability_registry=registry).status)


class RevealedCreatureStatisticsNativeTests(InheritedNativeFixture, TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "revealed-creature-statistics.sqlite3"
        fixture_names = ("bound-effect-program-cards", "revealed-library-top-cards",
                         "qualified-library-search-cards", FIXTURE)
        build_fixture_database([ROOT / "tests/fixtures" / (name + ".json") for name in fixture_names], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Public reveal", [
            DeckEntry("Generic Bound Commander", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def _start(self, session):
        source = self.add(session.engine, "Nissa's Revelation", zone="hand")
        self.assertEqual(ORACLE_ID, source.oracle_id)
        self.assert_closed(session, source)
        self.prepare(session, mana={"C": 5, "G": 2})
        self.checkpoint(session)
        self.cast(session, "A", source)
        pending = self.resolve(session)
        self.assertEqual(["A"], list(pending.actors))
        schema = pending.payload_by_actor["A"]["legal_actions"][0]["choice_schema"]
        self.assertEqual("ordered_partition", schema["shape"])
        looked = schema["legal_refs"]
        self.assertEqual(5, len(looked))
        return source, looked

    def _choose(self, session, top, bottom):
        result = session.act("pilot:A", {
            "action_id": "choose", "cards": {"top": list(top), "bottom": list(bottom)},
        })
        self.assertTrue(result.ok, result.summary)
        return self.resolve(session)

    def _events(self, session, code):
        return [event for event in session.state.events if event.code == code]

    def test_actual_reordered_front_face_is_public_and_first_ordinary_draw(self):
        session = self.session(57261001)
        engine = session.engine
        sixth = self.add(engine, "Generic Bound Growth", zone="library", ref="scry-sixth-private")
        bottom = [self.add(engine, "Generic Bound Growth", zone="library", ref=f"scry-private-{i}")
                  for i in range(3)]
        revealed = self.add(engine, "Esika, God of the Tree // The Prismatic Bridge",
                            zone="library", ref="scry-current-creature")
        old_top = self.add(engine, "Generic Bound Plains", zone="library", ref="scry-original-top")
        source, looked = self._start(session)
        self.assertEqual([old_top.ref, revealed.ref, *(card.ref for card in reversed(bottom))], list(looked))
        self.assertNotIn(sixth.ref, looked)
        self.assertFalse(self._events(session, "library.top.reveal"))
        for principal in ("pilot:B", "pilot:C", "pilot:D", "spectator"):
            body = json.dumps(session.packet(principal, full=True))
            for secret in (*looked, sixth.ref, "Esika, God of the Tree"):
                self.assertNotIn(secret, body)
        session = self.replay(session, load=True)
        life = session.state.players["A"].life
        initial_hand = len(session.state.players["A"].zones["hand"])
        command = {"action_id": "choose", "cards": {"top": [revealed.ref], "bottom": [old_top.ref, *looked[2:]]}}
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act("pilot:B", command).ok)
        self.assertFalse(session.act("pilot:A", {**command, "cards": {"top": [sixth.ref], "bottom": list(looked)}}).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        self.assertIsNone(self._choose(session, [revealed.ref], [old_top.ref, *looked[2:]]))
        event, = self._events(session, "library.top.reveal")
        self.assertEqual("Esika, God of the Tree", event.details["card"])
        draws = [draw for draw in self._events(session, "card.draw")
                 if draw.actor == "A" and draw.event_id > event.event_id]
        self.assertEqual(1, len(draws))
        life_event, = [change for change in self._events(session, "effect.life")
                       if change.actor == "A" and change.event_id > event.event_id]
        self.assertGreater(life_event.event_id, draws[0].event_id)
        self.assertEqual(initial_hand + 1, len(session.state.players["A"].zones["hand"]))
        self.assertEqual("hand", session.state.cards[revealed.object_id].zone)
        self.assertEqual("graveyard", session.state.cards[source.object_id].zone)
        self.assertEqual(life + 4, session.state.players["A"].life)
        body = json.dumps(session.packet("spectator", full=True))
        self.assertIn("A revealed Esika, God of the Tree.", body)
        for secret in (old_top.ref, sixth.ref, *(card.ref for card in bottom)):
            self.assertNotIn(secret, body)
        self.replay(session)

    def test_actual_noncreature_and_zero_power_keep_current_top_and_do_not_draw(self):
        for offset, (name, gain) in enumerate((("Generic Bound Growth", 0), ("Wall of Blossoms", 4))):
            with self.subTest(name=name):
                session = self.session(57261002 + offset)
                top = self.add(session.engine, name, zone="library", ref="scry-current-top")
                _, looked = self._start(session)
                life = session.state.players["A"].life
                hand = tuple(session.state.players["A"].zones["hand"])
                self.assertIsNone(self._choose(session, looked, ()))
                event, = self._events(session, "library.top.reveal")
                self.assertEqual(name, event.details["card"])
                self.assertEqual(top.object_id, session.state.players["A"].zones["library"][-1])
                self.assertEqual(hand, tuple(session.state.players["A"].zones["hand"]))
                self.assertEqual(life + gain, session.state.players["A"].life)
                self.replay(session)

    def test_actual_dynamic_creature_keeps_reveal_time_toughness_across_dredge_replay(self):
        session = self.session(57261004)
        engine = session.engine
        dredge = self.add(engine, "Life from the Loam", zone="graveyard")
        self.add(engine, "Generic Bound Body", zone="graveyard", ref="first-grave-creature")
        self.add(engine, "Generic Bound Body", seat="B", zone="graveyard", ref="second-grave-creature")
        followup = self.add(engine, "Generic Bound Growth", zone="library", ref="private-after-dredge")
        milled = [self.add(engine, "Generic Bound Body", zone="library", ref=f"milled-creature-{i}")
                  for i in range(2)]
        top = self.add(engine, "Lhurgoyf", zone="library")
        self.assert_closed(session, top)
        _, looked = self._start(session)
        life = session.state.players["A"].life
        pending = self._choose(session, looked, ())
        self.assertEqual("draw.replacement", pending.kind)
        self.assertEqual(["A"], list(pending.actors))
        self.assertEqual(life, session.state.players["A"].life)
        self.assertEqual(top.object_id, session.state.players["A"].zones["library"][-1])
        self.assertEqual("Lhurgoyf", self._events(session, "library.top.reveal")[0].details["card"])
        opponent = session.packet("pilot:B", full=True)
        self.assertIsNone(opponent["decision"])
        self.assertNotIn(followup.ref, json.dumps(opponent))
        session = self.replay(session, load=True)
        result = session.act("pilot:A", {"action_id": "choose", "choice": dredge.ref})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(life + 3, session.state.players["A"].life)
        self.assertEqual("graveyard", session.state.cards[top.object_id].zone)
        self.assertTrue(all(session.state.cards[card.object_id].zone == "graveyard" for card in milled))
        self.assertEqual("hand", session.state.cards[dredge.object_id].zone)
        self.assertEqual("hand", session.state.cards[followup.object_id].zone)
        self.assertEqual(1, len(self._events(session, "library.top.reveal")))
        self.assertNotIn(followup.ref, json.dumps(session.packet("pilot:B", full=True)))
        self.replay(session)

    def test_runtime_reveal_dependency_is_inferred_without_its_declaration(self):
        session = self.session(57261005)
        source = self.add(session.engine, "Nissa's Revelation", zone="hand")
        program, = session.engine.semantics.programs_for_oracle(source.oracle_id)
        deps = tuple(value for value in program.capability_dependencies if value != REVEALED_CREATURE_CAPABILITY)
        altered = replace(program, capability_dependencies=deps,
                          capability_closure=self.registry.closure(deps, profile="commander_review").to_dict())
        result = bind_semantic_program_runtime(altered, capability_registry=self.registry, profile="commander_review")
        self.assertFalse(result["strict"])
        self.assertIn("capability:undeclared_runtime_dependency:" + REVEALED_CREATURE_CAPABILITY, result["blockers"])
