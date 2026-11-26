"""Native witnesses for live face-down creature characteristics."""

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
from quorune.object_predicate import (
    FACE_DOWN_CREATURE_QUERY_CAPABILITY,
    ObjectQueryError,
    ObjectQuerySpec,
    PermanentStatePredicateSpec,
    object_query_capabilities,
)
from quorune.object_query import ObjectQueryResult, object_matches_query
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.evidence import validate_capability_evidence_index
from quorune.rules.evidence_storage import read_evidence_document
from scripts.build_test_database import build_fixture_database
from scripts.update_capability_evidence import build_index, discover_tests


PLANS = "Secret Plans"
WHISPERER = "Primal Whisperer"
CAPABILITY = FACE_DOWN_CREATURE_QUERY_CAPABILITY


class PublicFaceDownCreatureTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def use_database(cls, path):
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Face-down public characteristics", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "public-face-down.sqlite3"
        build_fixture_database(ROOT / "tests/fixtures/public-face-down-creature-cards.json", path)
        cls.use_database(path)

    def stats(self, game, card):
        return tuple(game.engine._numeric_stat(card.object_id, stat) for stat in ("power", "toughness"))

    def offers(self, game, seat):
        self.priority(game, seat)
        return game.packet("pilot:" + seat, full=True)["decision"]["ctx"]["legal"]["actions"]

    def morph(self, game, seat, card, *, reserve):
        self.use_morph_offer(game, seat, "cast-morph:" + card.ref, reserve=reserve)
        self.assertIsNone(self.resolve(game))
        current = game.state.cards[card.object_id]
        self.assertEqual("battlefield", current.zone)
        self.assertTrue(current.face_down)

    def use_morph_offer(self, game, seat, action_id, *, reserve):
        offered = next(row for row in self.offers(game, seat) if row["id"] == action_id)
        command = {"action_id": offered["id"], "pay": "auto", "reserve": reserve}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:D", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        accepted = game.act("pilot:" + seat, command)
        self.assertTrue(accepted.ok, accepted.summary)

    def test_native_casts_count_both_seats_and_remove_the_bonus_before_lethal_state_actions(self):
        game = self.session(70800241)
        game.state.config.semantic_policy = "trusted_only"
        plans = self.add(game.engine, PLANS, zone="hand")
        whisperer = self.add(game.engine, WHISPERER, zone="hand")
        canyon = self.add(game.engine, "Canyon Lurkers", zone="hand")
        foreign = self.add(game.engine, "Glacial Stalker", zone="hand", seat="B")
        conceal = self.add(game.engine, "Clever Concealment", zone="hand", seat="B")
        shock = self.add(game.engine, "Shock", zone="hand", seat="B")
        orrery = self.add(game.engine, "Vedalken Orrery")
        for card in (plans, whisperer, canyon, foreign, conceal, shock, orrery):
            self.assert_closed(game, card)
        self.prepare(game, actor="B", active="B", mana={"C": 9, "W": 2, "R": 1, "U": 1})
        game.state.players["A"].mana_pool.update({"C": 12, "G": 2, "U": 1, "R": 1})
        self.checkpoint(game)

        self.morph(game, "B", foreign, reserve={"C": 6, "W": 2, "U": 1, "R": 1})
        self.morph(game, "A", whisperer, reserve={"C": 9, "G": 2, "U": 1, "R": 1})
        self.morph(game, "A", canyon, reserve={"C": 6, "G": 2, "U": 1, "R": 1})
        self.assertEqual((2, 2), self.stats(game, whisperer))
        self.cast_for(game, "A", plans, reserve={"C": 6, "G": 1, "R": 1})
        self.assertIsNone(self.resolve(game))
        self.assertEqual((2, 3), self.stats(game, whisperer))
        self.assertEqual((2, 3), self.stats(game, canyon))
        self.assertEqual((2, 2), self.stats(game, foreign))
        for seat, hidden in (("A", "Glacial Stalker"), ("B", "Canyon Lurkers"), ("C", WHISPERER)):
            self.assertNotIn(hidden, json.dumps(game.packet("pilot:" + seat, full=True)))

        game = self.replay(game, load=True)
        hand_before = len(game.state.players["A"].zones["hand"])
        self.use_morph_offer(game, "A", "turn-face-up:" + whisperer.ref, reserve={"C": 3, "R": 1})
        self.assertEqual((6, 6), self.stats(game, whisperer))
        self.assertEqual(1, self.trigger_count(game, plans))
        self.assertIsNone(self.resolve(game))
        self.assertEqual(hand_before + 1, len(game.state.players["A"].zones["hand"]))

        self.assertIn("turn-face-up:" + foreign.ref, {row["id"] for row in self.offers(game, "B")})
        self.cast_for(game, "B", conceal, [foreign.ref], reserve={"C": 4, "U": 1, "R": 1})
        self.assertIsNone(self.resolve(game))
        self.assertTrue(game.state.cards[foreign.object_id].phased_out)
        self.assertNotIn("turn-face-up:" + foreign.ref, {row["id"] for row in self.offers(game, "B")})
        self.assertEqual((4, 4), self.stats(game, whisperer))
        self.cast_for(game, "B", shock, [canyon.ref], reserve={"C": 4, "U": 1})
        self.assertIsNone(self.resolve(game))
        self.assertEqual(2, game.state.cards[canyon.object_id].marked_damage)
        self.assertEqual((2, 3), self.stats(game, canyon))

        self.use_morph_offer(game, "A", "turn-face-up:" + canyon.ref, reserve={})
        self.assertEqual("graveyard", game.state.cards[canyon.object_id].zone)
        self.assertEqual((2, 2), self.stats(game, whisperer))
        self.assertEqual(1, self.trigger_count(game, plans))
        self.assertIsNone(self.resolve(game))
        self.assertEqual(hand_before + 2, len(game.state.players["A"].zones["hand"]))
        self.replay(game, load=True)

    def test_face_down_query_rejects_other_zones_types_and_phased_objects_and_retains_older_wire(self):
        state = PermanentStatePredicateSpec(face_down=True)
        query = ObjectQuerySpec(zones=("battlefield",), types_all=("creature",), state_predicate=state)
        self.assertEqual(query, ObjectQuerySpec.from_dict(query.to_dict()))
        self.assertEqual((CAPABILITY,), object_query_capabilities(query.to_dict()))
        hidden = ObjectQueryResult("ob1", "public-1", "", "A", "A", "battlefield", types=("creature",), face_down=True)
        self.assertTrue(object_matches_query(hidden, query))
        for variant in (replace(hidden, face_down=False), replace(hidden, phased_out=True),
                        replace(hidden, types=("land",)), replace(hidden, zone="hand")):
            self.assertFalse(object_matches_query(variant, query))
        for variant in ({"types_all": ()}, {"zones": ("hand",)}, {"include_phased_out": True}):
            with self.assertRaises(ObjectQueryError):
                replace(query, **variant)
        for malformed in ({"face_down": 1}, {"face_down": False}, {"face_down": True, "tapped": True}):
            with self.assertRaises(ObjectQueryError):
                PermanentStatePredicateSpec.from_dict(malformed)
        legacy = PermanentStatePredicateSpec(tapped=True)
        self.assertEqual(legacy, PermanentStatePredicateSpec.from_dict(legacy.to_dict()))
        self.assertNotIn("face_down", legacy.to_dict())

    def test_both_originals_require_the_face_status_dependency_and_do_not_expand_neighboring_queries(self):
        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        next(row for row in raw["capabilities"] if row["id"] == CAPABILITY).update(
            status="blocked", blockers=["Withhold the public face-status query"],
        )
        withdrawn = CapabilityRegistry(raw)
        index = build_index(
            registry_value=raw,
            declaration_source=read_evidence_document(ROOT / "platform/capability-evidence-declarations.json"),
            discovered_test_ids=set(discover_tests(ROOT).values()),
        )
        withdrawn.mark_evidence_verified(validate_capability_evidence_index(index, registry=withdrawn))
        game = self.session(70800242)
        for name in (PLANS, WHISPERER):
            original = self.db.lookup(name)
            self.assertEqual("exact", compile_oracle_card(original, capability_registry=self.registry,
                                                          capability_profile="commander_review").status)
            self.assertNotEqual("exact", compile_oracle_card(original, capability_registry=withdrawn,
                                                             capability_profile="commander_review").status)
            forged_text = original.oracle_text.replace("Face-down creatures", "Face-down lands").replace(
                "face-down creature", "face-down land",
            )
            self.assertNotEqual(original.oracle_text, forged_text)
            forged = replace(original, oracle_text=forged_text)
            self.assertNotEqual("exact", compile_oracle_card(forged, capability_registry=self.registry,
                                                             capability_profile="commander_review").status)
            card = self.add(game.engine, name)
            self.assert_closed(game, card)
            program, = (item for item in game.engine.semantics.programs_for_oracle(card.oracle_id)
                        if CAPABILITY in item.capability_dependencies)
            deps = tuple(dep for dep in program.capability_dependencies if dep != CAPABILITY)
            stripped = replace(program, capability_dependencies=deps,
                               capability_closure=self.registry.closure(deps, profile="commander_review").to_dict())
            binding = bind_semantic_program_runtime(stripped, capability_registry=self.registry, profile="commander_review")
            self.assertFalse(binding["strict"])
            self.assertIn("capability:undeclared_runtime_dependency:" + CAPABILITY, binding["blockers"])

    def test_query_witness_detects_omitted_face_status(self):
        with patch("quorune.object_query.permanent_state_predicate_matches", return_value=True):
            with self.assertRaises(AssertionError):
                self.test_face_down_query_rejects_other_zones_types_and_phased_objects_and_retains_older_wire()
