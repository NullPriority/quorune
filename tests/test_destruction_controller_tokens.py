"""Actual destruction results create simultaneous tokens for the former controllers."""

from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.descriptor_requirements import typed_result_amount_runtime_dependencies
from quorune.carddb import CardDatabase
from quorune.compiler import destruction_followup_templates
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.replacement.immutable import thaw_value
from quorune.rules.capabilities import capability_covered_mechanics, load_default_capability_registry
from quorune.rules.destruction_result_model import (
    DESTRUCTION_CONTROLLER_TOKEN_CAPABILITY, DESTRUCTION_RESULT_CAPABILITY,
    DESTRUCTION_RESULT_MECHANIC, DESTRUCTION_TOKEN_COUNTS_FIELD, DestructionFollowup,
)
from quorune.semantic_runtime.distributed_token_creation import _distributed_creators
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


MARCH = "March of Souls"
RAMPAGE = "Rampage of the Clans"
ORIGINALS = (MARCH, RAMPAGE)
PROFILE = "commander_review"
FIXTURES = ROOT / "tests" / "fixtures"


class DestructionControllerTokenTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        support = {
            "attached-destruction-set-cards.json": {
                "Darksteel Myr", "Grizzly Bears", "Rest in Peace", "Sol Ring", "Troll Ascetic",
            },
            "announced-target-cardinality-cards.json": {"Darksteel Citadel"},
            "bound-effect-program-cards.json": {"Generic Bound Plains", "Generic Bound Growth", "Frost Lynx"},
            "source-attack-alone-cards.json": {"Isamaru, Hound of Konda", "Act of Treason"},
            "fixed-distributed-token-cards.json": {"Parallel Lives", "Anointed Procession"},
        }
        parts = [FIXTURES / "destruction-controller-tokens-cards.json"]
        for name, names in support.items():
            document = json.loads((FIXTURES / name).read_bytes())
            document["cards"] = [card for card in document["cards"] if card["name"] in names]
            assert names == {card["name"] for card in document["cards"]}, name
            ids = {card["oracle_id"] for card in document["cards"]}
            document["rulings"] = [ruling for ruling in document.get("rulings", ()) if ruling["oracle_id"] in ids]
            part = folder / name
            part.write_text(json.dumps(document), encoding="utf-8")
            parts.append(part)
        path = folder / "destruction-controller-tokens.sqlite3"
        build_fixture_database(parts, path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Destruction controller tokens", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def _lowered(self, name):
        record = self.db.lookup(name, fuzzy=False)
        ir = compile_oracle_card(record, capability_registry=self.registry, capability_profile=PROFILE)
        effect, = [effect for face in ir.faces for node in face.nodes for effect in node.effects]
        return record, ir, thaw_value(effect)

    @staticmethod
    def _tokens(game):
        return [card for card in game.state.cards.values() if card.is_token and card.zone == "battlefield"]

    def _assert_token_batch(self, game, counts, name):
        tokens = self._tokens(game)
        self.assertEqual(counts, Counter(card.controller for card in tokens))
        self.assertTrue(all(card.owner == card.controller and card.printed_name == name for card in tokens))
        self.assertEqual(1, len({card.zone_timestamp for card in tokens}))
        destruction, = [event for event in game.state.events if event.code == "effect.permanent.destroy_set"]
        starts = [event for event in game.state.events if event.code == "token.create"]
        self.assertTrue(starts)
        self.assertTrue(all(game.state.events.index(destruction) < game.state.events.index(entry) for entry in starts))
        return {entry.actor: entry.details["base_quantity"] for entry in starts}

    def test_versioned_scopes_and_runtime_creator_counts_are_closed(self):
        _, _, march = self._lowered(MARCH)
        _, _, rampage = self._lowered(RAMPAGE)
        first = DestructionFollowup.from_destruction_effect(march)
        second = DestructionFollowup.from_destruction_effect(rampage)
        self.assertTrue(first.uses_destroyed_controllers)
        self.assertTrue(second.uses_destroyed_controllers)
        self.assertEqual(2, first.schema_version)
        self.assertEqual(("creature",), first.scope.query.types_all)
        self.assertEqual(("artifact", "enchantment"), second.scope.query.types_any)
        self.assertIs(march["regeneration_prohibited"], True)
        self.assertNotIn("regeneration_prohibited", rampage)
        dependencies = typed_result_amount_runtime_dependencies(rampage)
        self.assertIn(DESTRUCTION_CONTROLLER_TOKEN_CAPABILITY, dependencies)
        self.assertIn(DESTRUCTION_RESULT_MECHANIC, capability_covered_mechanics(dependencies))
        self.assertNotIn(DESTRUCTION_RESULT_CAPABILITY, dependencies)
        self.assertEqual((), first.from_committed_controllers(()).effects)
        prepared, = first.from_committed_controllers(("A", "B", "A")).effects
        self.assertEqual("create_distributed_tokens", prepared["op"])
        self.assertEqual({"A": 2, "B": 1}, dict(prepared[DESTRUCTION_TOKEN_COUNTS_FIELD]))
        self.assertEqual((("B", "A"), (1, 2)), _distributed_creators(prepared, "A", ("B", "C", "D", "A")))
        for bad in ({"B": True}, {"B": 0}, {"untied-seat": 1}, {}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                _distributed_creators({**prepared, DESTRUCTION_TOKEN_COUNTS_FIELD: bad}, "A", ("A", "B"))
        for bad in (
            dict(rampage, set=march["set"]),
            dict(rampage, followup={**rampage["followup"], "schema_version": True}),
            dict(rampage, followup={**rampage["followup"], "effect": {
                "op": "draw", "player": "$controller", "count": 1, "private": True,
            }}),
        ):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                DestructionFollowup.from_destruction_effect(bad)

    def test_both_originals_bind_the_complete_program_and_legacy_quantity_retains_v1(self):
        for name in ORIGINALS:
            with self.subTest(name=name):
                card, ir, effect = self._lowered(name)
                self.assertEqual("exact", ir.status, ir.material_residuals)
                report, semantics = register_card(self.db, self.registry, name)
                self.assertTrue(report["strict_capability_ready"], report["blockers"])
                self.assertEqual("capability_closed", report["trust_basis"])
                program, = semantics.programs_for_oracle(card.oracle_id)
                self.assertIsNone(program.target_schema)
                self.assertIn(DESTRUCTION_CONTROLLER_TOKEN_CAPABILITY, program.capability_dependencies)
                self.assertIn("permanent.destroy.fixed_set", program.capability_dependencies)
                self.assertEqual(DestructionFollowup.from_destruction_effect(effect),
                                 DestructionFollowup.from_destruction_effect(program.effects[0]))
        _, ir, effect = self._lowered("Fumigate")
        self.assertEqual("exact", ir.status, ir.material_residuals)
        old = DestructionFollowup.from_destruction_effect(effect)
        self.assertEqual(effect["followup"], old.to_dict())
        self.assertEqual(1, old.schema_version)
        self.assertIn(DESTRUCTION_RESULT_CAPABILITY, old.capability_dependencies)
        self.assertEqual(3, old.from_committed_count(3).effects[0]["delta"])

    def test_paid_march_uses_the_last_controller_and_replays_unequal_token_replacements(self):
        game = self.session(6495210)
        march = self.add(game.engine, MARCH, zone="hand")
        theft = self.add(game.engine, "Act of Treason", zone="hand")
        own = self.add(game.engine, "Grizzly Bears", seat="A")
        captured = self.add(game.engine, "Frost Lynx", seat="B")
        troll = self.add(game.engine, "Troll Ascetic", seat="B")
        preserve = self.add(game.engine, "Darksteel Myr", seat="C")
        lives = self.add(game.engine, "Parallel Lives", seat="B")
        procession = self.add(game.engine, "Anointed Procession", seat="B")
        private = self.add(game.engine, "Generic Bound Growth", seat="D", zone="hand", ref="controller-token-private")
        for card in (theft, troll, lives, procession):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 6, "R": 1, "W": 1})
        game.state.players["B"].mana_pool.update({"C": 1, "G": 1})
        self.checkpoint(game)

        self.cast(game, "A", theft, [captured.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(("B", "A"), (game.state.cards[captured.object_id].owner,
                                     game.state.cards[captured.object_id].controller))
        self.cast(game, "A", march)
        self.assertEqual(0, sum(game.state.players["A"].mana_pool.values()))
        self.priority(game, "B")
        actions = game.packet("pilot:B", full=True)["decision"]["ctx"]["legal"]["actions"]
        offer, = [action for action in actions if action["id"].startswith(f"activate:{troll.ref}:")]
        result = game.act("pilot:B", {"action_id": offer["id"], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.resolve_top(game)
        self.assertEqual(1, game.state.cards[troll.object_id].regeneration_shields)

        decision = self.resolve(game)
        self.assertEqual(("replacement.order", ["B"]), (decision.kind, decision.actors))
        for card in (own, captured, troll):
            self.assertEqual("graveyard", game.state.cards[card.object_id].zone)
        self.assertEqual("battlefield", game.state.cards[preserve.object_id].zone)
        self.assertEqual([], self._tokens(game))
        for principal in ("pilot:A", "pilot:B", "pilot:C", "spectator"):
            self.assertNotIn(private.ref, json.dumps(game.packet(principal, full=True)))
        game = self.replay(game, load=True)
        self.assertEqual(["pilot:B"], game.pending_principals())
        options = game.packet("pilot:B", full=True)["decision"]["ctx"]["options"]
        self.assertEqual(2, len(options))
        command = {"action_id": "choose", "replacement": options[0]["id"]}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:A", command).ok)
        self.assertFalse(game.act("pilot:B", {**command, "replacement": "unissued"}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        observed = []
        dispatch = game.engine._dispatch_semantic_event

        def observe(event, context, **fields):
            if event == "permanent.enter" and context.get("token"):
                observed.append(Counter(card.controller for card in self._tokens(game)))
            return dispatch(event, context, **fields)

        with patch.object(game.engine, "_dispatch_semantic_event", side_effect=observe):
            chosen = game.act("pilot:B", command)
        self.assertTrue(chosen.ok, chosen.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual({"A": 2, "B": 1}, self._assert_token_batch(game, {"A": 2, "B": 4}, "Spirit Token"))
        self.assertEqual([{"A": 2, "B": 4}] * 6, observed)
        for card in self._tokens(game):
            self.assertIn("flying", game.engine._combat_keywords(card))
        self.replay(game, load=True)

    def test_paid_rampage_counts_the_union_once_and_includes_destroyed_exiled_objects(self):
        game = self.session(6495220)
        rampage = self.add(game.engine, RAMPAGE, zone="hand")
        doubler = self.add(game.engine, "Parallel Lives", seat="A")
        redirect = self.add(game.engine, "Rest in Peace", seat="B")
        ring = self.add(game.engine, "Sol Ring", seat="B")
        both = self.add(game.engine, "Hammer of Purphoros", seat="B")
        darksteel = self.add(game.engine, "Darksteel Citadel", seat="C")
        creature = self.add(game.engine, "Grizzly Bears", seat="D")
        for card in (doubler, redirect, both, darksteel):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 3, "G": 1})
        self.checkpoint(game)

        self.cast(game, "A", rampage)
        self.assertEqual([], game.state.stack[-1].targets)
        self.assertEqual(0, sum(game.state.players["A"].mana_pool.values()))
        self.assertIsNone(self.resolve(game))
        for card in (doubler, redirect, ring, both):
            self.assertEqual("exile", game.state.cards[card.object_id].zone)
        for card in (darksteel, creature):
            self.assertEqual("battlefield", game.state.cards[card.object_id].zone)
        self.assertEqual({"A": 1, "B": 3}, self._assert_token_batch(game, {"A": 1, "B": 3}, "Centaur Token"))
        for card in self._tokens(game):
            self.assertEqual((3, 3), tuple(game.engine._numeric_stat(card.object_id, key) for key in ("power", "toughness")))
        self.replay(game, load=True)

    def test_omitting_controller_tokens_restores_each_originals_material_spell_result(self):
        with patch.object(destruction_followup_templates, "_controller_token_followup", side_effect=ValueError):
            for name in ORIGINALS:
                card = self.db.lookup(name, fuzzy=False)
                ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(any("its controller creates" in residual.text for residual in ir.material_residuals))
                report, _ = register_card(self.db, self.registry, name)
                self.assertFalse(report["strict_capability_ready"], report)

    def test_genuine_controller_token_authority_withdrawal_keeps_the_legacy_whole_card(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        known = set(discover_tests(ROOT).values())
        document = deepcopy(raw)
        next(row for row in document["capabilities"] if row["id"] == DESTRUCTION_CONTROLLER_TOKEN_CAPABILITY).update(
            status="blocked", blockers=["Scoped committed-controller token withdrawal"])
        registry = CapabilityRegistry(document)
        index = build_index(registry_value=document, declaration_source=declarations, discovered_test_ids=known)
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        closure = registry.closure((DESTRUCTION_CONTROLLER_TOKEN_CAPABILITY,), profile=PROFILE)
        self.assertIn("status:" + DESTRUCTION_CONTROLLER_TOKEN_CAPABILITY + ":blocked", closure.blockers)
        self.assertFalse(any("unverified" in blocker for blocker in closure.blockers))
        for name in ORIGINALS:
            report, _ = register_card(self.db, registry, name)
            self.assertFalse(report["strict_capability_ready"], report)
        legacy, _ = register_card(self.db, registry, "Fumigate")
        self.assertTrue(legacy["strict_capability_ready"], legacy["blockers"])


if __name__ == "__main__":
    unittest.main()
