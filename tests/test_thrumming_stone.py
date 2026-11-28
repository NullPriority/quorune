from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from common import ROOT
import test_ripple as ripple_controls

from quorune.ability_fragments import canonical_ability_fragments, granted_triggered_specs
from quorune.card_programs import bind_card_program_runtime
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.ripple_model import RippleSpec
from quorune.rules.capabilities import CapabilityRegistry
from quorune.rules.evidence_storage import read_evidence_document
from quorune.rules.ripple import RIPPLE_CAST_CONTEXT, ripple_instruction
from quorune.semantic_runtime.battlefield_ripple_grants import (
    BattlefieldRippleGrantHandler,
    RIPPLE_GRANT_CAPABILITY,
    RIPPLE_GRANT_EVENT,
)
from quorune.semantics import SemanticRegistry


class ThrummingStoneTests(ripple_controls.RippleNativeFixture, unittest.TestCase):
    extra_ripple_fixtures = (ROOT / "tests/fixtures/thrumming-stone-cards.json",)

    def _choose(self, session, seat, **choice):
        principal = "pilot:" + seat
        packet = session.packet(principal, full=True)
        self.assertEqual("semantic.choice", packet["decision"]["kind"])
        result = session.act(principal, {"action_id": "choose", **choice})
        self.assertTrue(result.ok, result.summary)

    def _order_if_requested(self, session, seat, *, count):
        pending = session.state.pending_decision
        if pending.kind != "trigger.order":
            return
        self.assertEqual([seat], list(pending.actors))
        principal = "pilot:" + seat
        packet = session.packet(principal, full=True)
        order = [row["id"] for row in packet["decision"]["ctx"]["triggers"]]
        self.assertEqual(count, len(order))
        result = session.act(principal, {"action_id": "order", "triggers": order})
        self.assertTrue(result.ok, result.summary)

    def _ripple_stack(self, session, card):
        return [item for item in session.state.stack
                if item.source_object_id == card.object_id and RIPPLE_CAST_CONTEXT in item.context]

    def _reject(self, session, seat, command):
        prior = authoritative_state_hash(session.state)
        result = session.act("pilot:" + seat, command)
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(prior, authoritative_state_hash(session.state))

    def test_actual_stone_reveals_only_four_casts_twice_orders_remainder_and_replays(self):
        session = self.session(702600201)
        stone = self.add(session.engine, "Thrumming Stone", zone="hand")
        original = self.add(session.engine, "Lightning Bolt", zone="hand")
        remainder = [self.add(session.engine, name, zone="library", ref=f"stone-remainder-{i}")
                     for i, name in enumerate(("Forest", "Grizzly Bears"))]
        copies = [self.add(session.engine, "Lightning Bolt", zone="library", ref=f"stone-free-{i}")
                  for i in range(2)]
        hidden = self.add(session.engine, "Counterspell", zone="library", ref="stone-hidden-suffix")
        library = session.state.players["A"].zones["library"]
        library.remove(hidden.object_id)
        library.insert(0, hidden.object_id)
        self.assert_closed(session, stone)
        self.assert_closed(session, original)
        self.add(session.engine, "Mountain", ref="stone-paid-red-source")
        self.prepare(session, mana={"C": 5})
        self.checkpoint(session)

        self.cast_for(session, "A", stone)
        self.assertFalse(self._ripple_stack(session, stone))
        self.assertIsNone(self.resolve(session))
        self.assertEqual("battlefield", session.state.cards[stone.object_id].zone)
        self.cast_for(session, "A", original, ["B"])
        spell = next(item for item in session.state.stack if item.card_object_id == original.object_id)
        triggers = self._ripple_stack(session, original)
        self.assertEqual(1, len(triggers))
        self.assertEqual({
            "schema_version": 1, "controller": "A", "source_spell_ref": spell.ref,
            "rules_names": ["Lightning Bolt"],
        }, triggers[0].context[RIPPLE_CAST_CONTEXT])
        fragments = granted_triggered_specs(
            canonical_ability_fragments(session.engine._effective_card_data(
                session.state.cards[original.object_id])["ability_fragments"]),
        )
        self.assertEqual([("spell.cast", "Ripple 4")], [(f.event, f.label) for f in fragments])

        self.assertEqual("semantic.choice", self.resolve(session).kind)
        for seat in "BCD":
            packet = json.dumps(session.packet("pilot:" + seat, full=True))
            for card in [*remainder, *copies, hidden]:
                self.assertNotIn(card.ref, packet)
        self._reject(session, "B", {"action_id": "choose", "choice": "reveal"})
        self._choose(session, "A", choice="reveal")
        for seat in "BCD":
            packet = json.dumps(session.packet("pilot:" + seat, full=True))
            self.assertNotIn(hidden.ref, packet)
            self.assertIn("Grizzly Bears", packet)

        for i, (card, target) in enumerate(zip(copies, "CD", strict=True)):
            options = session.packet("pilot:A", full=True)["decision"]["ctx"]["cast_options"]
            self.assertEqual({object_.ref for object_ in copies[i:]}, {option["card"] for option in options})
            option = next(option for option in options if option["card"] == card.ref)
            command = {"card": card.ref, "cost_option": option["id"], "targets": [target], "pay": "auto"}
            self._reject(session, "B", {"action_id": "choose", "choice": "cast", "cast": command})
            self._choose(session, "A", choice="cast", cast=command)
            self.assertEqual("stack", session.state.cards[card.object_id].zone)
            self.assertFalse(any(session.state.players["A"].mana_pool.values()))
            if i == 0:
                session = self.replay(session, load=True)
        private_order = [remainder[1].ref, remainder[0].ref]
        self._reject(session, "A", {"action_id": "choose", "order": [hidden.ref, private_order[0]]})
        self._reject(session, "B", {"action_id": "choose", "order": private_order})
        self._choose(session, "A", order=private_order)
        self.assertEqual([remainder[1].object_id, remainder[0].object_id],
                         session.state.players["A"].zones["library"][:2])
        self._order_if_requested(session, "A", count=2)
        for card in copies:
            self.assertEqual(1, len(self._ripple_stack(session, card)))
        for _ in copies:
            choice = self.resolve(session)
            self.assertEqual(("semantic.choice", ["A"]), (choice.kind, list(choice.actors)))
            self._choose(session, "A", choice="decline")
        self.assertIsNone(self.resolve(session))
        self.assertEqual([37, 37, 37], [session.state.players[seat].life for seat in "BCD"])
        self.replay(session, load=True)

    def test_actual_granted_and_intrinsic_ripple_survive_stone_departure_independently(self):
        session = self.session(702600202)
        stone = self.add(session.engine, "Thrumming Stone")
        original = self.add(session.engine, "Surging Flame", zone="hand")
        followup = self.add(session.engine, "Lightning Bolt", zone="hand")
        bounce = self.add(session.engine, "Surging Aether", seat="B", zone="hand")
        session.state.players["B"].mana_pool.update(U=1, C=3)
        self.add(session.engine, "Mountain", ref="stone-followup-red-source")
        self.prepare(session, mana={"C": 1, "R": 1})
        self.checkpoint(session)
        library_before = list(session.state.players["A"].zones["library"])

        self.cast_for(session, "A", original, ["C"])
        self._order_if_requested(session, "A", count=2)
        triggers = self._ripple_stack(session, original)
        self.assertEqual(2, len(triggers))
        programs = [session.engine.semantics.get(item.semantic_key) for item in triggers]
        self.assertEqual(1, sum(program.provenance.get("granted_only") is True for program in programs))
        self.assertEqual(1, len({item.context[RIPPLE_CAST_CONTEXT]["source_spell_ref"] for item in triggers}))
        self.cast_for(session, "B", bounce, [stone.ref])
        self.assertEqual(1, len(self._ripple_stack(session, bounce)))
        decision = self.resolve(session)
        self.assertEqual(("semantic.choice", ["B"]), (decision.kind, list(decision.actors)))
        self._choose(session, "B", choice="decline")
        self.resolve_top(session)
        self.assertEqual("hand", session.state.cards[stone.object_id].zone)
        self.assertEqual(2, len(self._ripple_stack(session, original)))
        session = self.replay(session, load=True)
        for _ in triggers:
            decision = self.resolve(session)
            self.assertEqual(("semantic.choice", ["A"]), (decision.kind, list(decision.actors)))
            self._choose(session, "A", choice="decline")
        self.assertIsNone(self.resolve(session))
        self.assertEqual(38, session.state.players["C"].life)
        self.assertEqual(library_before, session.state.players["A"].zones["library"])
        self.cast_for(session, "A", followup, ["D"])
        self.assertFalse(self._ripple_stack(session, followup))
        self.assertIsNone(self.resolve(session))
        self.assertEqual(37, session.state.players["D"].life)
        self.replay(session, load=True)

    def test_static_carrier_and_granted_body_are_strictly_linked_and_source_spanned(self):
        record = self.db.lookup("Thrumming Stone")
        ir = compile_oracle_card(record, capability_registry=self.registry, capability_profile="commander_review")
        self.assertEqual("exact", ir.status, ir.material_residuals)
        static, child = [node for face in ir.faces for node in face.nodes
                         if node.event in {RIPPLE_GRANT_EVENT, "spell.cast"}]
        self.assertEqual(("battlefield", "stack"), (static.active_zone, child.active_zone))
        self.assertEqual("granted_triggered_ability", child.kind)
        self.assertEqual(record.oracle_text[child.span.start:child.span.end], child.text)
        program = compile_best_available_card_program(
            self.db, record, semantic_registry=SemanticRegistry(), capability_registry=self.registry,
            capability_profile="commander_review")
        binding = bind_card_program_runtime(program, capability_registry=self.registry, profile="commander_review")
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        carrier, = [ability for ability in program.abilities if ability.event == RIPPLE_GRANT_EVENT]
        body, = [ability for ability in program.abilities if ability.event == "spell.cast"]
        spec = BattlefieldRippleGrantHandler().validate(carrier.handlers[0])
        self.assertEqual((spec.fragment.semantic_key, spec.ripple), (f"{record.oracle_id}:{body.ability_id}", RippleSpec(4)))
        self.assertTrue(body.provenance["granted_only"])
        self.assertEqual((ripple_instruction(RippleSpec(4)),), tuple(body.effects))
        with self.assertRaises(ValueError):
            BattlefieldRippleGrantHandler().validate({**carrier.handlers[0], "extra": 1})

    def test_thrumming_stone_compiler_omission_and_specific_authority_withdrawal_fail_closed(self):
        from quorune.rules.evidence import validate_capability_evidence_index
        from scripts.update_capability_evidence import build_index

        record = self.db.lookup("Thrumming Stone")
        closure_options = {"capability_profile": "commander_review"}
        with patch("quorune.compiler.ripple_nodes.battlefield_ripple_grant_nodes", return_value=None):
            omitted = compile_oracle_card(record, capability_registry=self.registry, **closure_options)
        self.assertEqual("unresolved", omitted.status)
        self.assertEqual(1, len(omitted.material_residuals))
        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_text(encoding="utf-8"))
        next(row for row in raw["capabilities"] if row["id"] == RIPPLE_GRANT_CAPABILITY).update(
            status="blocked", blockers=["test withdrawal of the live Ripple grant"],
        )
        registry = CapabilityRegistry(raw)
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        trusted_ids = {row["test_id"] for row in read_evidence_document(
            ROOT / "quorune/rules/capability-evidence.json")["declarations"]}
        registry.mark_evidence_verified(validate_capability_evidence_index(build_index(
            registry_value=raw, declaration_source=declarations, discovered_test_ids=trusted_ids),
            registry=registry))
        blocked = compile_oracle_card(record, capability_registry=registry, **closure_options)
        self.assertIn(f"capability:status:{RIPPLE_GRANT_CAPABILITY}:blocked",
                      [blocker for residual in blocked.material_residuals for blocker in residual.blockers])
        intrinsic = compile_oracle_card(self.db.lookup("Surging Flame"), capability_registry=registry,
                                        **closure_options)
        self.assertEqual("exact", intrinsic.status, intrinsic.material_residuals)
