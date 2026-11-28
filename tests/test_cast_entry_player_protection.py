"""Cast-conditioned player protection and source-current burden results."""

from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
import test_next_personal_turn_protection as prior_protection
from quorune.card_programs import bind_card_program_runtime
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.errors import GameRuleError
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, DEFAULT_CAPABILITY_REGISTRY, load_default_capability_registry
from quorune.rules.next_personal_turn_protection import (
    CAST_ENTRY_PROTECTION_CAPABILITY, CAST_ENTRY_PROTECTION_TEMPLATE, PROTECTION_OPERATION,
    cast_entry_protection_effect, next_personal_turn_protection_runtime_dependencies,
)
from quorune.semantic_runtime.life_prohibitions import life_payment_prohibited
from quorune.semantics import SemanticRegistry
from quorune.source_entry_cast_information import SOURCE_ENTRY_CAST_CAPABILITY, source_entry_cast_condition
from scripts.build_test_database import build_fixture_database


CARD = "The One Ring"
PROFILE = "commander_review"


class CastEntryPlayerProtectionTests(InheritedNativeFixture, unittest.TestCase):
    until_following_turn = prior_protection.NextPersonalTurnProtectionTests.until_following_turn

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "cast-entry-player-protection.sqlite3"
        fixtures = ROOT / "tests" / "fixtures"
        build_fixture_database([
            fixtures / "bound-effect-program-cards.json",
            fixtures / "source-entry-cast-cards.json",
            fixtures / "combat-removal-cards.json",
            fixtures / "next-personal-turn-protection-cards.json",
            fixtures / "all-kind-counter-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Cast-entry protection", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_cast_entry_shape_and_authority_fail_closed(self):
        card = self.db.lookup(CARD)
        options = {"capability_registry": self.registry, "capability_profile": PROFILE}
        ir = compile_oracle_card(card, **options)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        entry, = (node for node in ir.faces[0].nodes if node.event == "permanent.enter.self")
        self.assertEqual(CAST_ENTRY_PROTECTION_TEMPLATE, entry.template_id)
        self.assertEqual(source_entry_cast_condition(), entry.event_condition)
        self.assertEqual((cast_entry_protection_effect(),), tuple(entry.effects))
        program = compile_best_available_card_program(self.db, card, semantic_registry=SemanticRegistry(), **options)
        report = bind_card_program_runtime(program, capability_registry=self.registry, profile=PROFILE)
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        ability, = (row for row in program.abilities if row.event == "permanent.enter.self")
        for change in (
            {"event_condition": None},
            {"event_condition": source_entry_cast_condition(origin="hand")},
            {"event": "resolve", "active_zone": "stack"},
            {"effects": [{**cast_entry_protection_effect(), "preserve_life_total": True}]},
            {"effects": [{**cast_entry_protection_effect(), "schema_version": 3.0}]},
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                next_personal_turn_protection_runtime_dependencies(replace(ability, **change))
        for wording in ("", " from your hand", " from exile"):
            clause = "if you cast it" + wording + ", " if wording else ""
            nearby = replace(card, oracle_text=card.oracle_text.replace("if you cast it, ", clause))
            self.assertNotEqual("exact", compile_oracle_card(nearby, **options).status)
        for dependency in (CAST_ENTRY_PROTECTION_CAPABILITY, SOURCE_ENTRY_CAST_CAPABILITY):
            raw = json.loads(DEFAULT_CAPABILITY_REGISTRY.read_bytes())
            raw["capabilities"] = self.registry.capabilities()
            row = next(row for row in raw["capabilities"] if row["id"] == dependency)
            row.update(status="blocked", blockers=["Test control withdraws this sole dependency"])
            registry = CapabilityRegistry(raw)
            registry.mark_evidence_verified(sha256(b"Test-only Ring dependency withdrawal").hexdigest())
            changed = compile_oracle_card(card, capability_registry=registry, capability_profile=PROFILE)
            self.assertNotEqual("exact", changed.status)
            self.assertTrue(any(dependency in blocker for row in changed.material_residuals for blocker in row.blockers))

    def test_noncast_entry_does_not_copy_the_original_cast_trigger(self):
        game = self.session(603_702_160)
        game.state.config.semantic_policy = "trusted_only"
        ring = self.add(game.engine, CARD)
        blink = self.add(game.engine, "Planar Incision", zone="hand")
        original = ring.logical_object_id
        self.assert_closed(game, ring)
        self.assert_closed(game, blink)
        self.prepare(game, mana={"C": 1, "U": 1})
        self.checkpoint(game)
        self.cast(game, "A", blink, [ring.ref])
        self.assertIsNone(self.resolve(game))
        current = game.state.cards[ring.object_id]
        self.assertEqual("battlefield", current.zone)
        self.assertNotEqual(original, current.logical_object_id)
        self.assertEqual({"+1/+1": 1}, current.counters)
        self.assertEqual(0, self.trigger_count(game, ring))
        self.assertNotIn(PROTECTION_OPERATION, game.state.players["A"].stats)
        self.assertFalse(any(event.code == "player.protection" for event in game.state.events))

        before = authoritative_state_hash(game.state)
        forged = {**cast_entry_protection_effect(), "player": "A", "source": "not-the-resolving-stack"}
        with self.assertRaises(GameRuleError):
            game.engine.apply_effect(forged, actor="A")
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.replay(game, load=True)

    def test_cast_entry_native_witness_kills_omitted_protection(self):
        from unittest.mock import patch
        from quorune.effect_runtime.damage_life_and_turns import HANDLERS

        def witness():
            game = self.session(603_702_162)
            game.state.config.semantic_policy = "trusted_only"
            ring = self.add(game.engine, CARD, zone="hand")
            self.assert_closed(game, ring)
            self.prepare(game, mana={"C": 4})
            self.checkpoint(game)
            self.cast(game, "A", ring)
            self.resolve_top(game)
            self.assertEqual(1, self.trigger_count(game, ring))
            self.assertIsNone(self.resolve(game))
            self.assertTrue(game.state.players["A"].stats.get(PROTECTION_OPERATION),
                            "Cast-entry protection was not applied")

        witness()
        def omit_protection(_host, _effect, *, actor, **_):
            return actor

        with patch.dict(HANDLERS, {PROTECTION_OPERATION: omit_protection}):
            with self.assertRaisesRegex(AssertionError, "Cast-entry protection was not applied"):
                witness()

    def test_cast_entry_protects_until_next_turn_and_burden_uses_resolved_count(self):
        game = self.session(603_702_161)
        game.state.config.semantic_policy = "trusted_only"
        ring = self.add(game.engine, CARD, zone="hand")
        life = self.add(game.engine, "Revitalize", zone="hand")
        ordinary = self.add(game.engine, "Flame Rift", zone="hand", ref="preventable-rift")
        unpreventable = self.add(game.engine, "Flame Rift", zone="hand", ref="unpreventable-rift")
        pain = self.add(game.engine, "Flaring Pain", zone="hand")
        bolt = self.add(game.engine, "Lightning Bolt", seat="B", zone="hand")
        season = self.add(game.engine, "Doubling Season")
        for card in (ring, life, ordinary, pain, bolt, season):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 9, "R": 3, "W": 1})
        game.state.players["B"].mana_pool["R"] = 1
        self.checkpoint(game)
        for seat in "BCD":
            self.assertNotIn(ring.ref, json.dumps(game.packet("pilot:" + seat, full=True)))
        self.cast_for(game, "A", ring, reserve={"R": 3, "W": 1})
        self.resolve_top(game)
        self.assertEqual("battlefield", game.state.cards[ring.object_id].zone)
        self.assertEqual(1, self.trigger_count(game, ring))
        self.assertNotIn(PROTECTION_OPERATION, game.state.players["A"].stats)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertTrue(game.state.players["A"].stats[PROTECTION_OPERATION])
        self.assertFalse(life_payment_prohibited(game.engine, "A"))
        for seat in "BCD":
            self.assertNotIn(PROTECTION_OPERATION, game.state.players[seat].stats)

        self.cast_for(game, "A", life, reserve={"R": 3})
        self.priority(game, "B")
        offer = self.action(game, "B", bolt)
        self.assertIn("B", offer["target_schema"]["legal_refs"])
        self.assertNotIn("A", offer["target_schema"]["legal_refs"])
        before = authoritative_state_hash(game.state)
        denied = game.act("pilot:B", {"action_id": offer["id"], "targets": ["A"], "pay": "auto"})
        self.assertFalse(denied.ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.assertIsNone(self.resolve(game))
        self.assertEqual(43, game.state.players["A"].life)
        self.cast_for(game, "A", ordinary, reserve={"R": 2})
        self.assertIsNone(self.resolve(game))
        self.assertEqual([43, 36, 36, 36], [game.state.players[seat].life for seat in "ABCD"])
        self.cast_for(game, "A", pain, reserve={"R": 1})
        self.assertIsNone(self.resolve(game))
        self.cast(game, "A", unpreventable)
        self.assertIsNone(self.resolve(game))
        self.assertEqual([39, 32, 32, 32], [game.state.players[seat].life for seat in "ABCD"])

        self.priority(game, "A")
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        activation, = (row for row in actions if row["id"].startswith(f"activate:{ring.ref}:"))
        before_hand = set(game.state.players["A"].zones["hand"])
        accepted = game.act("pilot:A", {"action_id": activation["id"], "pay": "auto"})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertTrue(game.state.cards[ring.object_id].tapped)
        self.assertNotIn("burden", game.state.cards[ring.object_id].counters)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(2, game.state.cards[ring.object_id].counters["burden"])
        drawn = set(game.state.players["A"].zones["hand"]) - before_hand
        self.assertEqual(2, len(drawn))
        for seat in "BCD":
            packet = json.dumps(game.packet("pilot:" + seat, full=True))
            for uid in drawn:
                self.assertNotIn(game.state.cards[uid].ref, packet)

        turn = game.state.turn_sequence
        self.until_following_turn(game, turn, "B")
        self.assertTrue(game.state.players["A"].stats[PROTECTION_OPERATION])
        self.until_following_turn(game, turn, "A")
        self.assertNotIn(PROTECTION_OPERATION, game.state.players["A"].stats)
        self.until(game, lambda state: self.trigger_count(state, ring) == 1)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(37, game.state.players["A"].life)
        self.assertEqual(2, game.state.cards[ring.object_id].counters["burden"])
        self.replay(game, load=True)
