"""Native next-turn player protection from an ordinary paid activation."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import test_next_personal_turn_protection as prior_protection
from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from test_qualified_zone_event_queries import query_record
from quorune.card_programs import bind_card_program_runtime
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.errors import GameRuleError
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.next_personal_turn_protection import (
    ACTIVATED_PROTECTION_CAPABILITY,
    ACTIVATED_PROTECTION_TEMPLATE,
    PROTECTION_OPERATION,
    activated_protection_effect,
    next_personal_turn_protection_runtime_dependencies,
)
from quorune.semantics import SemanticRegistry
from scripts.build_test_database import build_fixture_database


CARD = "The Stasis Coffin"
PROFILE = "commander_review"


class ActivatedPlayerProtectionTests(InheritedNativeFixture, unittest.TestCase):
    until_following_turn = prior_protection.NextPersonalTurnProtectionTests.until_following_turn
    reject = prior_protection.NextPersonalTurnProtectionTests.reject

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "activated-protection.sqlite3"
        fixtures = ROOT / "tests" / "fixtures"
        build_fixture_database([
            fixtures / "bound-effect-program-cards.json",
            fixtures / "next-personal-turn-protection-cards.json",
            fixtures / "next-spell-cost-reduction-cards.json",
            fixtures / "scryfall-exact-lists.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Activated protection", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_the_protection_producer_rejects_other_durations_and_wrong_program_shapes(self):
        source = query_record("{2}, {T}, Exile this artifact: You gain protection from everything until your next turn.")
        source = replace(source, type_line="Artifact", power=None, toughness=None)
        options = {"capability_registry": self.registry, "capability_profile": PROFILE}
        ir = compile_oracle_card(source, **options)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        ability_node, = ir.faces[0].nodes
        self.assertEqual(ACTIVATED_PROTECTION_TEMPLATE, ability_node.template_id)
        self.assertEqual((activated_protection_effect(),), tuple(ability_node.effects))
        self.assertIn(ACTIVATED_PROTECTION_CAPABILITY, ability_node.capability_dependencies)
        for revised in (
            source.oracle_text.replace("your next turn", "end of turn"),
            source.oracle_text.replace("You gain", "Target player gains"),
            source.oracle_text + " Draw a card.",
        ):
            with self.subTest(text=revised):
                self.assertNotEqual("exact", compile_oracle_card(replace(source, oracle_text=revised), **options).status)

        card = self.db.lookup(CARD)
        program = compile_best_available_card_program(self.db, card, semantic_registry=SemanticRegistry(), **options)
        report = bind_card_program_runtime(program, capability_registry=self.registry, profile=PROFILE)
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        ability, = (row for row in program.abilities if row.event == "activate")
        for changes in (
            {"event": "resolve"},
            {"active_zone": "hand"},
            {"handlers": ()},
            {"effects": ({**activated_protection_effect(), "preserve_life_total": True},)},
            {"effects": ({**activated_protection_effect(), "schema_version": 4.0},)},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                next_personal_turn_protection_runtime_dependencies(replace(ability, **changes))

    def test_actual_paid_activation_protects_its_controller_until_their_next_turn(self):
        game = self.session(603_702_170)
        game.state.config.semantic_policy = "trusted_only"
        coffin = self.add(game.engine, CARD)
        game.engine.change_control(coffin.object_id, "B", reason="Fixture separates owner and controller")
        bolt = self.add(game.engine, "Lightning Bolt", zone="hand")
        second_bolt = self.add(game.engine, "Lightning Bolt", zone="hand", ref="second-coffin-bolt")
        rift = self.add(game.engine, "Flame Rift", seat="B", zone="hand")
        pain = self.add(game.engine, "Flaring Pain", seat="B", zone="hand")
        second_rift = self.add(game.engine, "Flame Rift", seat="B", zone="hand", ref="second-coffin-rift")
        grasp = self.add(game.engine, "Infernal Grasp", seat="B", zone="hand")
        bear = self.add(game.engine, "Grizzly Bears")
        for spell in (coffin, bolt, rift, pain, grasp):
            self.assert_closed(game, spell)
        for seat, names in (("A", ("Mountain", "Mountain")),
                            ("B", ("Plains",) * 6 + ("Mountain",) * 6 + ("Swamp",) * 2)):
            for index, name in enumerate(names):
                self.add(game.engine, name, seat=seat, ref=f"coffin-mana-{seat}-{index}")
        self.prepare(game)
        self.checkpoint(game)
        self.until_following_turn(game, game.state.turn_sequence, "B")
        self.until(game, lambda current: current.state.phase == "precombat_main")
        self.priority(game, "A")
        action_id = f"activate:{coffin.ref}:ab1"
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        self.assertNotIn(action_id, {row["id"] for row in actions})
        self.cast(game, "A", bolt, ["B"])
        self.priority(game, "B")
        actions = game.packet("pilot:B", full=True)["decision"]["ctx"]["legal"]["actions"]
        activation, = (row for row in actions if row["id"] == action_id)
        self.reject(game, "A", {"action_id": activation["id"], "pay": "auto"})
        self.reject(game, "B", {"action_id": activation["id"], "pay": "manual", "payment": {"C": 1}})
        current = game.state.cards[coffin.object_id]
        self.assertEqual(("battlefield", False), (current.zone, current.tapped))
        original_identity = current.logical_object_id
        lands_before = {uid for uid, card in game.state.cards.items()
                        if card.zone == "battlefield" and card.controller == "B" and card.tapped}
        result = game.act("pilot:B", {"action_id": activation["id"], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        current = game.state.cards[coffin.object_id]
        self.assertEqual(("A", "exile"), (current.owner, current.zone))
        tapped_for_price = [uid for uid, card in game.state.cards.items()
                            if card.zone == "battlefield" and card.controller == "B" and card.tapped
                            and uid not in lands_before]
        self.assertEqual(2, len(tapped_for_price))
        self.assertNotIn(PROTECTION_OPERATION, game.state.players["B"].stats)
        self.assertEqual("B", game.state.stack[-1].controller)
        program = game.engine.semantics.get(game.state.stack[-1].semantic_key)
        self.assertEqual((activated_protection_effect(),), tuple(program.effects))
        self.assertEqual(original_identity, game.state.stack[-1].context["source_logical_object_id"])
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertNotIn(PROTECTION_OPERATION, game.state.players["A"].stats)
        self.assertTrue(game.state.players["B"].stats[PROTECTION_OPERATION])
        self.resolve_top(game)
        self.assertEqual(40, game.state.players["B"].life)

        self.cast(game, "B", rift)
        self.priority(game, "A")
        offer = self.action(game, "A", second_bolt)
        self.assertIn("A", offer["target_schema"]["legal_refs"])
        self.assertNotIn("B", offer["target_schema"]["legal_refs"])
        self.reject(game, "A", {"action_id": offer["id"], "targets": ["B"], "pay": "auto"})
        self.cast(game, "B", grasp, [bear.ref])
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[bear.object_id].zone)
        self.assertEqual(38, game.state.players["B"].life)
        self.assertIsNone(self.resolve(game))
        self.assertEqual([36, 38, 36, 36], [game.state.players[seat].life for seat in "ABCD"])
        self.cast(game, "B", second_rift)
        self.cast(game, "B", pain)
        self.assertIsNone(self.resolve(game))
        self.assertEqual([32, 34, 32, 32], [game.state.players[seat].life for seat in "ABCD"])
        turn = game.state.turn_sequence
        for seat in "CDA":
            self.until_following_turn(game, turn, seat)
            self.assertTrue(game.state.players["B"].stats[PROTECTION_OPERATION])
        self.until_following_turn(game, turn, "B")
        self.assertNotIn(PROTECTION_OPERATION, game.state.players["B"].stats)
        self.priority(game, "A")
        self.cast(game, "A", second_bolt, ["B"])
        self.resolve_top(game)
        self.assertEqual(31, game.state.players["B"].life)
        self.replay(game, load=True)

    def test_forged_protection_cannot_change_the_state(self):
        game = self.session(603_702_171)
        before = authoritative_state_hash(game.state)
        proposed = {**activated_protection_effect(), "player": "A", "source": "outside-the-stack"}
        with self.assertRaises(GameRuleError):
            game.engine.apply_effect(proposed, actor="A")
        self.assertEqual(before, authoritative_state_hash(game.state))

    def test_withdrawn_protection_authority_rejects_the_whole_card(self):
        import json
        from quorune.rules.capabilities import CapabilityRegistry, DEFAULT_CAPABILITY_REGISTRY
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        test_ids = set(discover_tests(ROOT).values())
        card = self.db.lookup(CARD)
        for dependency in (ACTIVATED_PROTECTION_CAPABILITY, "protection.typed.debt"):
            raw = json.loads(DEFAULT_CAPABILITY_REGISTRY.read_bytes())
            row = next(row for row in raw["capabilities"] if row["id"] == dependency)
            row.update(status="blocked", blockers=["Test-only withdrawal"])
            registry = CapabilityRegistry(raw)
            index = build_index(registry_value=raw, declaration_source=declarations, discovered_test_ids=test_ids)
            registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
            ir = compile_oracle_card(card, capability_registry=registry, capability_profile=PROFILE)
            with self.subTest(dependency=dependency):
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(any(dependency in blocker for row in ir.material_residuals for blocker in row.blockers))

    def test_runtime_protection_omission_is_detected(self):
        from unittest.mock import patch
        from quorune.effect_runtime.damage_life_and_turns import HANDLERS

        def witness():
            game = self.session(603_702_172)
            coffin = self.add(game.engine, CARD)
            self.prepare(game, mana={"C": 2})
            self.checkpoint(game)
            result = game.act("pilot:A", {"action_id": f"activate:{coffin.ref}:ab1", "pay": "auto"})
            self.assertTrue(result.ok, result.summary)
            self.resolve_top(game)
            self.assertTrue(game.state.players["A"].stats.get(PROTECTION_OPERATION), "Protection was omitted")

        witness()

        def omit_protection(_host, _effect, *, actor, **_context):
            return actor

        with patch.dict(HANDLERS, {PROTECTION_OPERATION: omit_protection}):
            with self.assertRaisesRegex(AssertionError, "Protection was omitted"):
                witness()
