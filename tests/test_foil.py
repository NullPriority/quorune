from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs import bind_card_program_runtime
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.replacement.model import ReplacementEffectError
from quorune.replacement.replay import ReplacementContinuation
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.casting.whole_zone_cost_frame import (
    WHOLE_ZONE_COST_FRAME_FIELD, WholeZoneCostFrame, WholeZonePaymentGroup,
)
from quorune.rules.casting_additional_costs import FixedZoneChangeAdditionalCost
from quorune.rules.evidence_storage import read_evidence_document
from quorune.rules.island_pair_discard_cost import ISLAND_PAIR_DISCARD_CAPABILITY, island_pair_discard_cost
from quorune.semantics import SemanticRegistry
from scripts.build_test_database import build_fixture_database


class FoilTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        fixtures = ROOT / "tests/fixtures"
        path = Path(cls.temporary.name) / "foil.sqlite3"
        build_fixture_database([
            fixtures / "bound-effect-program-cards.json",
            fixtures / "ripple-cards.json",
            fixtures / "committed-land-sacrifice-mana-cards.json",
            fixtures / "scaled-public-count-cards.json",
            fixtures / "per-player-query-damage-cards.json",
            fixtures / "foil-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Foil witnesses", [
            DeckEntry("Sliver Hivelord", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Sliver Hivelord"])

    def _offer(self, session, foil):
        self.priority(session, "A")
        packet = session.packet("pilot:A", full=True)
        return next(action for action in packet["decision"]["ctx"]["legal"]["actions"]
                    if action["id"] == f"cast:{foil.ref}")

    def _reject(self, session, seat, response):
        before = authoritative_state_hash(session.state)
        result = session.act("pilot:" + seat, response)
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(session.state))

    def _pending_command(self, session, seat):
        context = session.packet("pilot:" + seat, full=True)["decision"]["ctx"]
        response = {"action_id": "choose", "replacement": context["options"][0]["id"]}
        if context.get("event_order_options"):
            response["replacement_event"] = context["event_order_options"][0]
        return response

    def _finish_stack(self, session):
        for _ in range(6):
            decision = self.resolve(session)
            if decision is None:
                return
            self.assertEqual("replacement.order", decision.kind)
            seat, = decision.actors
            result = session.act("pilot:" + seat, self._pending_command(session, seat))
            self.assertTrue(result.ok, result.summary)
        self.fail("Foil stack did not finish")

    def test_actual_foil_alternate_uses_distinct_cards_and_resumes_one_atomic_discard_batch(self):
        session = self.session(11890711)
        foil = self.add(session.engine, "Foil", zone="hand", ref="foil-paid-spell")
        island = self.add(session.engine, "Steam Vents", zone="hand", ref="foil-island-subtype")
        other = self.add(session.engine, "Grizzly Bears", zone="hand", ref="foil-nonisland-other")
        wrong = self.add(session.engine, "Forest", zone="hand", ref="foil-no-island")
        foreign = self.add(session.engine, "Island", seat="C", zone="hand", ref="foil-foreign-island")
        target = self.add(session.engine, "Lightning Bolt", seat="B", zone="hand")
        rip = self.add(session.engine, "Rest in Peace", seat="B", ref="foil-exile-replacement")
        competing = self.add(session.engine, "Rest in Peace", seat="C", ref="foil-competing-replacement")
        bounce = self.add(session.engine, "Disperse", seat="B", zone="hand")
        for card in (foil, rip, competing, bounce, target):
            self.assert_closed(session, card)
        self.prepare(session)
        for mana in session.state.players["A"].mana_pool:
            session.state.players["A"].mana_pool[mana] = 0
        session.state.players["B"].mana_pool.update({"R": 1, "C": 1, "U": 1})
        self.checkpoint(session)
        self.cast_for(session, "B", target, ["A"])
        target_item = next(item for item in session.state.stack if item.card_object_id == target.object_id)

        action = self._offer(session, foil)
        option, = [value for value in action["cost_options"] if value["id"].startswith("plain-alternative-")]
        self.assertFalse(any(value["id"] == "normal" for value in action["cost_options"]))
        selection = option["choice_schema"]["discard_cards"]
        self.assertEqual(2, selection["count"])
        self.assertTrue({island.ref, other.ref, wrong.ref} <= set(selection["legal_refs"]))
        self.assertTrue({foil.ref, foreign.ref}.isdisjoint(selection["legal_refs"]))
        base = {"action_id": action["id"], "cost_option": option["id"], "targets": [target_item.ref], "pay": "auto"}
        pair = [other.ref, island.ref]
        for refs in ([island.ref], [island.ref, island.ref], [foil.ref, island.ref],
                     [wrong.ref, other.ref], [foreign.ref, other.ref]):
            self._reject(session, "A", {**base, "discard_cards": refs})
        self._reject(session, "A", {**base, "discard_cards": pair, "cost_cards": pair})
        self._reject(session, "D", {**base, "discard_cards": pair})
        started = session.act("pilot:A", {**base, "discard_cards": pair})
        self.assertTrue(started.ok, started.summary)
        sessions_with_replacements = 0

        for _ in range(6):
            pending = session.state.pending_decision
            if pending.kind != "replacement.order":
                break
            self.assertEqual(["A"], list(pending.actors))
            frame = pending.continuation["priority_response"][WHOLE_ZONE_COST_FRAME_FIELD]
            self.assertEqual((2, 11), (frame["schema_version"], frame["cost_schema_version"]))
            self.assertEqual(tuple(pair), tuple(row[2] for row in frame["groups"][0]["members"]))
            self.assertTrue(all(session.state.cards[card.object_id].zone == "hand" for card in (foil, island, other)))
            self.assertEqual([target_item.ref], [item.ref for item in session.state.stack])
            for seat in "BCD":
                packet = json.dumps(session.packet("pilot:" + seat, full=True))
                for ref in pair:
                    self.assertNotIn(ref, packet)
            if sessions_with_replacements == 0:
                altered = deepcopy(pending.continuation)
                altered["priority_response"][WHOLE_ZONE_COST_FRAME_FIELD]["cost_schema_version"] = 10
                with self.assertRaises(ReplacementEffectError):
                    ReplacementContinuation.from_dict(altered)
                session = self.replay(session, load=True)
            command = self._pending_command(session, "A")
            self._reject(session, "D", command)
            result = session.act("pilot:A", command)
            self.assertTrue(result.ok, result.summary)
            sessions_with_replacements += 1

        self.assertGreaterEqual(sessions_with_replacements, 2)
        cards = session.state.cards
        self.assertEqual(("exile", "exile"), (cards[island.object_id].zone, cards[other.object_id].zone))
        self.assertEqual(cards[island.object_id].zone_timestamp, cards[other.object_id].zone_timestamp)
        self.assertEqual("hand", cards[wrong.object_id].zone)
        self.assertEqual("hand", cards[foreign.object_id].zone)
        self.assertEqual("stack", cards[foil.object_id].zone)
        self.assertFalse(any(session.state.players["A"].mana_pool.values()))
        self.cast_for(session, "B", bounce, [competing.ref])
        self.resolve_top(session)
        self.assertEqual("hand", session.state.cards[competing.object_id].zone)
        self._finish_stack(session)
        self.assertEqual("exile", session.state.cards[target.object_id].zone)
        self.assertEqual(40, session.state.players["A"].life)
        self.replay(session, load=True)

    def test_actual_foil_normal_payment_is_separate_from_the_available_discard_option(self):
        session = self.session(11890712)
        foil = self.add(session.engine, "Foil", zone="hand")
        hand = [self.add(session.engine, "Island", zone="hand", ref="foil-kept-island"),
                self.add(session.engine, "Forest", zone="hand", ref="foil-kept-forest")]
        target = self.add(session.engine, "Lightning Bolt", seat="B", zone="hand")
        self.assert_closed(session, foil)
        self.prepare(session, mana={"C": 2, "U": 2})
        session.state.players["B"].mana_pool["R"] = 1
        self.checkpoint(session)
        self.cast_for(session, "B", target, ["A"])
        target_ref = next(item.ref for item in session.state.stack if item.card_object_id == target.object_id)
        action = self._offer(session, foil)
        self.assertEqual({"normal", "plain-alternative-ab1"}, {value["id"] for value in action["cost_options"]})
        result = session.act("pilot:A", {"action_id": action["id"], "cost_option": "normal",
                                         "targets": [target_ref], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertFalse(any(session.state.players["A"].mana_pool.values()))
        self.assertTrue(all(session.state.cards[card.object_id].zone == "hand" for card in hand))
        session = self.replay(session, load=True)
        self._finish_stack(session)
        self.assertEqual("graveyard", session.state.cards[target.object_id].zone)
        self.assertEqual("graveyard", session.state.cards[foil.object_id].zone)
        self.assertEqual(40, session.state.players["A"].life)
        self.replay(session, load=True)

    def test_foil_source_binds_counter_and_strictly_separates_price_and_frame_versions(self):
        card = self.db.lookup("Foil", fuzzy=False)
        options = {"capability_registry": self.registry, "capability_profile": "commander_review"}
        ir = compile_oracle_card(card, **options)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        price, counter = ir.faces[0].nodes
        self.assertEqual("counter-target-spell-v2", counter.template_id)
        self.assertIn(ISLAND_PAIR_DISCARD_CAPABILITY, price.capability_dependencies)
        cost = island_pair_discard_cost()
        descriptor = cost.to_descriptor()
        self.assertEqual([descriptor], price.handlers[0]["alternative_cost"]["additional_costs"])
        self.assertEqual(cost, FixedZoneChangeAdditionalCost.from_descriptor(descriptor))
        from quorune.rules.casting_additional_costs import fixed_zone_change_cost_candidates

        session = self.session(11890713)
        self.add(session.engine, "Grizzly Bears", zone="hand")
        self.add(session.engine, "Forest", zone="hand")
        self.assertEqual((), fixed_zone_change_cost_candidates(session.engine, actor="A", cost=cost))
        first = self.add(session.engine, "Steam Vents", zone="hand", ref="foil-same-name-first")
        second = self.add(session.engine, "Steam Vents", zone="hand", ref="foil-same-name-second")
        self.assertTrue({first.ref, second.ref} <= set(
            fixed_zone_change_cost_candidates(session.engine, actor="A", cost=cost),
        ))
        for altered in ({"schema_version": 1}, {"schema_version": 10}, {"island_pair": False},
                        {"island_pair": 1}, {"count": 1}):
            with self.subTest(altered=altered), self.assertRaises(ValueError):
                FixedZoneChangeAdditionalCost.from_descriptor({**descriptor, **altered})
        with self.assertRaises(ValueError):
            FixedZoneChangeAdditionalCost.from_descriptor({**descriptor, "sacrifice_name": "fixture"})
        group = WholeZonePaymentGroup("hand", (("object-1", "inc-1", "first"), ("object-2", "inc-2", "other")))
        frame = WholeZoneCostFrame("foil", "alternate", (group,), schema_version=2)
        self.assertEqual(frame, WholeZoneCostFrame.from_dict(frame.to_dict()))
        request = {"card": "foil", "cost_option": "alternate", "discard_cards": ["first", "other"]}
        frame.validate_response(request)
        with self.assertRaises(ReplacementEffectError):
            frame.validate_response({**request, "discard_cards": ["other", "first"]})
        with self.assertRaises(ReplacementEffectError):
            WholeZoneCostFrame.from_dict({**frame.to_dict(), "schema_version": 1})
        with self.assertRaises(ReplacementEffectError):
            WholeZoneCostFrame("foil", "alternate", (group,)).validate_response(request)

        program = compile_best_available_card_program(self.db, card, semantic_registry=SemanticRegistry(), **options)
        binding = bind_card_program_runtime(program, capability_registry=self.registry, profile="commander_review")
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])

    def test_foil_compiler_omission_and_verified_authority_withdrawal_keep_exact_counter(self):
        from quorune.rules.evidence import validate_capability_evidence_index
        from scripts.update_capability_evidence import build_index

        card = self.db.lookup("Foil", fuzzy=False)
        with patch("quorune.compiler.fixed_public_alternative_costs.ISLAND_PAIR_PRICE_TEXT", "omitted pair"):
            baseline = compile_oracle_card(card, capability_registry=self.registry, capability_profile="commander_review")
        self.assertEqual(["spell_effect"], [row.kind for row in baseline.material_residuals])
        self.assertTrue(baseline.faces[0].nodes[1].exact)
        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        next(row for row in raw["capabilities"] if row["id"] == ISLAND_PAIR_DISCARD_CAPABILITY).update(
            status="blocked", blockers=["test withdrawal of the qualified discard payment"],
        )
        registry = CapabilityRegistry(raw)
        declaration = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        ids = {row["test_id"] for row in read_evidence_document(
            ROOT / "quorune/rules/capability-evidence.json")["declarations"]}
        index = build_index(registry_value=raw, declaration_source=declaration, discovered_test_ids=ids)
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        blocked = compile_oracle_card(card, capability_registry=registry, capability_profile="commander_review")
        self.assertIn(f"capability:status:{ISLAND_PAIR_DISCARD_CAPABILITY}:blocked",
                      [reason for row in blocked.material_residuals for reason in row.blockers])
        self.assertTrue(blocked.faces[0].nodes[1].exact)
