from __future__ import annotations

import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.activation_cost_choice import CostChoice
from quorune.card_programs import bind_card_program_runtime
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.rules.named_sacrifice_cost_model import (
    NAMED_SACRIFICE_CAPABILITY,
    NAMED_SACRIFICE_COST_VERSION,
    named_creature_sacrifice_predicate,
)
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.casting_additional_costs import FixedZoneChangeAdditionalCost
from quorune.rules.evidence_storage import read_evidence_document
from quorune.rules.named_sacrifice_costs import named_sacrifice_payload_dependencies
from quorune.semantics import SemanticRegistry
from scripts.build_test_database import build_fixture_database


class ShadowbornApostleTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        fixtures = ROOT / "tests/fixtures"
        path = Path(cls.temporary.name) / "apostle.sqlite3"
        build_fixture_database([
            fixtures / "bound-effect-program-cards.json",
            fixtures / "ripple-cards.json",
            fixtures / "committed-land-sacrifice-mana-cards.json",
            fixtures / "external-entry-tapped-cards.json",
            fixtures / "shadowborn-apostle-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Shadowborn Apostle witnesses", [
            DeckEntry("Sliver Hivelord", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Sliver Hivelord"])

    def _offer(self, session, source):
        packet = session.packet("pilot:A", full=True)
        return next(action for action in packet["decision"]["ctx"]["legal"]["actions"]
                    if action["id"].startswith(f"activate:{source.ref}:"))

    def _reject(self, session, principal, response):
        before = authoritative_state_hash(session.state)
        result = session.act(principal, response)
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(session.state))

    def _search(self, session, demon, decoy):
        self.assertIsNotNone(self.resolve(session))
        packet = session.packet("pilot:A", full=True)
        self.assertEqual({demon.ref}, {row["id"] for row in packet["decision"]["ctx"]["search_cards"]})
        schema = packet["decision"]["legal_actions"][0]["choice_schema"]
        self.assertEqual(0, schema["minimum"])
        self.assertTrue(schema["rules_may_fail_to_find"])
        for seat in "BCD":
            public = json.dumps(session.packet("pilot:" + seat, full=True))
            for card in (demon, decoy):
                self.assertNotIn(card.ref, public)
                self.assertNotIn(card.printed_name, public)

    def test_actual_six_other_apostles_pay_together_and_private_demon_search_uses_entry_replacement(self):
        session = self.session(59515001)
        source = self.add(session.engine, "Shadowborn Apostle", ref="apostle-ability-source")
        others = [self.add(session.engine, "Shadowborn Apostle", ref=f"apostle-paid-{i}") for i in range(6)]
        wrong_name = self.add(session.engine, "Grizzly Bears", ref="apostle-wrong-name")
        other_seat = self.add(session.engine, "Shadowborn Apostle", seat="B", ref="apostle-wrong-seat")
        face_down = self.add(session.engine, "Shadowborn Apostle", ref="apostle-no-current-name")
        face_down.face_down = True
        demon = self.add(session.engine, "Bloodgift Demon", zone="library", ref="apostle-private-demon")
        decoy = self.add(session.engine, "Darksteel Colossus", zone="library", ref="apostle-private-nondemon")
        replacement = self.add(session.engine, "Authority of the Consuls", seat="B")
        self.assert_closed(session, source)
        self.assert_closed(session, replacement)
        self.prepare(session, mana={"B": 1})
        self.checkpoint(session)

        action = self._offer(session, source)
        choice, = action["cost_summary"]["choose_cost"]
        self.assertEqual(6, choice["n"])
        self.assertEqual({card.ref for card in (source, *others)}, set(choice["legal_refs"]))
        refs = [card.ref for card in others]
        base = {"action_id": action["id"], "pay": "auto"}
        for selection in (refs[:-1], refs[:-1] + [refs[0]], refs + [source.ref],
                          refs[:-1] + [wrong_name.ref], refs[:-1] + [other_seat.ref],
                          refs[:-1] + [face_down.ref]):
            self._reject(session, "pilot:A", {**base, "cost_cards": selection})
        self._reject(session, "pilot:B", {**base, "cost_cards": refs})
        engine = session.engine
        with patch.object(engine, "_move_cards_simultaneously", wraps=engine._move_cards_simultaneously) as move:
            result = session.act("pilot:A", {**base, "cost_cards": refs})
        self.assertTrue(result.ok, result.summary)
        paid = {card.object_id for card in others}
        self.assertEqual(1, sum({object_id for object_id, _ in call.args[0]} == paid
                                for call in move.call_args_list))
        self.assertEqual("battlefield", session.state.cards[source.object_id].zone)
        self.assertTrue(all(session.state.cards[ref].zone == "graveyard" for ref in paid))
        self.assertFalse(any(session.state.players["A"].mana_pool.values()))
        self.assertEqual("activated_ability", session.state.stack[-1].kind)

        self._search(session, demon, decoy)
        self._reject(session, "pilot:A", {"action_id": "choose", "search_cards": [decoy.ref]})
        self._reject(session, "pilot:B", {"action_id": "choose", "search_cards": [demon.ref]})
        session = self.replay(session, load=True)
        shuffled = session.state.players["A"].stats.get("shuffle_count", 0)
        result = session.act("pilot:A", {"action_id": "choose", "search_cards": [demon.ref]})
        self.assertTrue(result.ok, result.summary)
        returned = session.state.cards[demon.object_id]
        self.assertEqual(("battlefield", "A", True), (returned.zone, returned.controller, returned.tapped))
        self.assertEqual("library", session.state.cards[decoy.object_id].zone)
        self.assertEqual(shuffled + 1, session.state.players["A"].stats["shuffle_count"])
        self.assertIsNone(self.resolve(session))
        self.replay(session, load=True)

    def test_actual_apostle_can_pay_itself_through_exile_replacement_and_fail_to_find(self):
        session = self.session(59515002)
        source = self.add(session.engine, "Shadowborn Apostle", ref="apostle-self-paid")
        others = [self.add(session.engine, "Shadowborn Apostle", ref=f"apostle-exiled-{i}") for i in range(5)]
        replacement = self.add(session.engine, "Rest in Peace", seat="B")
        demon = self.add(session.engine, "Bloodgift Demon", zone="library", ref="apostle-declined-demon")
        decoy = self.add(session.engine, "Darksteel Colossus", zone="library", ref="apostle-declined-decoy")
        self.assert_closed(session, replacement)
        self.prepare(session, mana={"B": 1})
        self.checkpoint(session)

        selection = [card.ref for card in (source, *others)]
        action = self._offer(session, source)
        self.assertEqual(set(selection), set(action["cost_summary"]["choose_cost"][0]["legal_refs"]))
        result = session.act("pilot:A", {"action_id": action["id"], "cost_cards": selection, "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertTrue(all(session.state.cards[card.object_id].zone == "exile" for card in (source, *others)))
        self.assertEqual(source.object_id, session.state.stack[-1].source_object_id)
        self.assertFalse(any(session.state.players["A"].mana_pool.values()))
        self._search(session, demon, decoy)
        session = self.replay(session, load=True)
        shuffled = session.state.players["A"].stats.get("shuffle_count", 0)
        result = session.act("pilot:A", {"action_id": "choose", "search_cards": []})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(shuffled + 1, session.state.players["A"].stats["shuffle_count"])
        for card in (demon, decoy):
            self.assertEqual("library", session.state.cards[card.object_id].zone)
            self.assertNotIn(card.ref, json.dumps(session.packet("pilot:B", full=True)))
        self.assertIsNone(self.resolve(session))
        self.replay(session, load=True)

    def test_original_deck_copy_rule_and_existing_demon_search_close_with_exact_named_cost_codecs(self):
        record = self.db.lookup("Shadowborn Apostle")
        options = {"capability_registry": self.registry, "capability_profile": "commander_review"}
        ir = compile_oracle_card(record, **options)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        deck, activation = ir.faces[0].nodes
        self.assertEqual("deck.construction", deck.event)
        self.assertIsNone(deck.handlers[0]["fragment"]["value"]["maximum"])
        self.assertEqual("fixed-library-search-to-battlefield-v1", activation.template_id)
        self.assertEqual({"types": ["creature"], "subtypes_any": ["demon"]}, activation.effects[0]["selector"])
        self.assertIn(NAMED_SACRIFICE_CAPABILITY, activation.capability_dependencies)
        program = compile_best_available_card_program(self.db, record, semantic_registry=SemanticRegistry(), **options)
        binding = bind_card_program_runtime(program, capability_registry=self.registry, profile="commander_review")
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        choice = CostChoice(kind="sacrifice_one", count=6, predicate=named_creature_sacrifice_predicate().to_dict(),
                            sacrifice_schema_version=NAMED_SACRIFICE_COST_VERSION, sacrifice_name=record.name)
        self.assertEqual(choice, CostChoice.from_dict(choice.to_dict()))
        self.assertEqual(activation.cost["choices"][0], choice.compact())
        cost = choice.fixed_zone_change_cost()
        self.assertEqual(cost, FixedZoneChangeAdditionalCost.from_descriptor(cost.to_descriptor()))
        for representation in (choice.compact(), choice.to_dict(), cost.to_descriptor()):
            self.assertIn(NAMED_SACRIFICE_CAPABILITY, named_sacrifice_payload_dependencies(representation))
        for altered in ({"count": 5}, {"schema_version": 8}, {"sacrifice_name": None},
                        {"sacrifice_name": "$source"}, {"operation": "return_one_to_owner_hand"}):
            with self.subTest(altered=altered), self.assertRaises(ValueError):
                FixedZoneChangeAdditionalCost.from_descriptor({**cost.to_descriptor(), **altered})
        for altered in ({"other": 1}, {"sacrifice_schema_version": True}, {"sacrifice_name": None}):
            with self.subTest(altered=altered), self.assertRaises(ValueError):
                named_sacrifice_payload_dependencies({**choice.compact(), **altered})

    def test_named_price_compiler_omission_and_specific_authority_withdrawal_leave_cost_residual(self):
        from quorune.rules.evidence import validate_capability_evidence_index
        from scripts.update_capability_evidence import build_index

        record = self.db.lookup("Shadowborn Apostle")
        with patch("quorune.compiler.activated_zone_change_costs._NAMED_SIX_CREATURE_COST", re.compile(r"(?!x)x")):
            omitted = compile_oracle_card(record, capability_registry=self.registry, capability_profile="commander_review")
        self.assertEqual(["cost"], [row.kind for row in omitted.material_residuals])
        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        next(row for row in raw["capabilities"] if row["id"] == NAMED_SACRIFICE_CAPABILITY).update(
            status="blocked", blockers=["test withdrawal of exact named price"],
        )
        registry = CapabilityRegistry(raw)
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        tests = {row["test_id"] for row in read_evidence_document(
            ROOT / "quorune/rules/capability-evidence.json")["declarations"]}
        registry.mark_evidence_verified(validate_capability_evidence_index(build_index(
            registry_value=raw, declaration_source=declarations, discovered_test_ids=tests),
            registry=registry))
        blocked = compile_oracle_card(record, capability_registry=registry, capability_profile="commander_review")
        self.assertIn(f"capability:status:{NAMED_SACRIFICE_CAPABILITY}:blocked",
                      [blocker for node in blocked.material_residuals for blocker in node.blockers])
        self.assertTrue(blocked.faces[0].nodes[0].exact)
