"""Actual Shared Animosity counts the live attacking creature type set."""

from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.descriptor_requirements import typed_result_amount_runtime_dependencies
from quorune.carddb import CardDatabase
from quorune.compiler import attack_event_results
from quorune.deck import DeckDefinition, DeckEntry
from quorune.live_battlefield_quantities import current_live_attacking_shared_type_count
from quorune.object_query import ObjectQueryResult
from quorune.oracle_ir import compile_oracle_card
from quorune.query_effect_amount_model import (
    ATTACK_SHARED_TYPE_AMOUNT_CAPABILITY, PublicQueryAmountSpec, attack_shared_type_amount_spec,
)
from quorune.record import authoritative_state_hash
from quorune.rules.attack_event_results import attack_event_characteristic_capabilities
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


ORIGINAL = "Shared Animosity"
PROFILE = "commander_review"
FIXTURES = ROOT / "tests" / "fixtures"


class SharedAttackerTypeQuantityTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        support = {
            "attached-destruction-set-cards.json": {"Doomed Traveler", "Fervor", "Island", "Mountain", "Unsummon"},
            "attacking-token-result-cards.json": {"Skyknight Vanguard"},
            "creature-subtype-addition-cards.json": {"Goblin Piker", "Raging Goblin", "Vedalken Orrery"},
            "characteristic-query-union-cards.json": {"Universal Automaton"},
            "shared-creature-type-cards.json": {"Coat of Arms"},
            "bound-effect-program-cards.json": {"Generic Bound Plains", "Generic Bound Growth"},
            "source-attack-alone-cards.json": {"Isamaru, Hound of Konda"},
        }
        parts = [FIXTURES / "shared-attacker-type-quantity-cards.json"]
        for name, names in support.items():
            data = json.loads((FIXTURES / name).read_bytes())
            data["cards"] = [card for card in data["cards"] if card["name"] in names]
            assert names == {card["name"] for card in data["cards"]}, name
            ids = {card["oracle_id"] for card in data["cards"]}
            data["rulings"] = [ruling for ruling in data.get("rulings", ()) if ruling["oracle_id"] in ids]
            part = folder / name
            part.write_text(json.dumps(data), encoding="utf-8")
            parts.append(part)
        path = folder / "shared-attacker-type-quantity.sqlite3"
        build_fixture_database(parts, path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Shared attacking creature types", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def _node(self):
        card = self.db.lookup(ORIGINAL, fuzzy=False)
        ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
        node, = ir.faces[0].nodes
        return card, ir, node

    def _declare(self, game, assignments, *, resolve_first=None):
        self.until(game, lambda current: current.state.pending_decision is not None
                   and current.state.pending_decision.kind == "combat.attackers")
        command = {"action_id": "attack", "attackers": {card.ref: seat for card, seat in assignments}}
        attack = game.act("pilot:A", command)
        self.assertTrue(attack.ok, attack.summary)
        self.assertEqual("trigger.order", game.state.pending_decision.kind)
        items = [item for batch in game.state.pending_trigger_batches for item in batch.items]
        if resolve_first is not None:
            first, = [item for item in items if item.get("source_object_id") == resolve_first.object_id]
            items = [item for item in items if item is not first] + [first]
        order = game.act("pilot:A", {"action_id": "order", "triggers": [item.ref for item in items]})
        self.assertTrue(order.ok, order.summary)

    def test_wire_requires_the_exact_attacker_bound_positive_power_amount(self):
        _, ir, node = self._node()
        self.assertIn(ATTACK_SHARED_TYPE_AMOUNT_CAPABILITY, node.capability_dependencies)
        effect, = node.effects
        amount = effect["power"]
        self.assertEqual(attack_shared_type_amount_spec(), PublicQueryAmountSpec.from_dict(amount))
        self.assertIn(ATTACK_SHARED_TYPE_AMOUNT_CAPABILITY, typed_result_amount_runtime_dependencies(effect))
        self.assertIn(ATTACK_SHARED_TYPE_AMOUNT_CAPABILITY, attack_event_characteristic_capabilities(
            effects=(effect,), target_schema=None, mechanic_ids=node.mechanics,
        ))
        for changed in (
            dict(effect, card="$source.zone_object"),
            dict(effect, toughness=1),
            dict(effect, power={**amount, "schema_version": 1}),
            dict(effect, power={**amount, "coefficient": 2}),
        ):
            with self.subTest(changed=changed):
                self.assertEqual((), attack_event_characteristic_capabilities(
                    effects=(changed,), target_schema=None, mechanic_ids=node.mechanics,
                ))

    def test_live_shared_count_uses_other_creature_incarnations_once_across_controllers(self):
        def row(ref, seat, subtypes, *, attacking=True, phased=False):
            return ObjectQueryResult(
                ref, ref, ref, seat, seat, "battlefield", types=("creature",), subtypes=subtypes,
                attacking=attacking, phased_out=phased, logical_object_id=ref + "-incarnation",
            )

        rows = (
            row("recipient", "A", ("human", "soldier"), attacking=False),
            row("both", "B", ("human", "soldier")),
            row("one", "C", ("soldier",)),
            row("different", "C", ("cat",)),
            row("idle", "D", ("human",), attacking=False),
            row("phased", "A", ("soldier",), phased=True),
        )
        host = SimpleNamespace(affected_permanent_object_rows=lambda _actor: rows)
        quantity = attack_shared_type_amount_spec().quantity
        self.assertEqual(2, current_live_attacking_shared_type_count(host, "A", quantity, source_ref="recipient"))
        self.assertEqual(0, current_live_attacking_shared_type_count(host, "A", quantity, source_ref="departed"))

    def test_complete_original_binds_one_ordinary_attack_trigger_and_typed_live_amount(self):
        card, ir, node = self._node()
        self.assertEqual("exact", ir.status, ir.material_residuals)
        self.assertEqual("creature.attacks", node.event)
        bound, semantics = register_card(self.db, self.registry, ORIGINAL)
        self.assertTrue(bound["strict_capability_ready"], bound["blockers"])
        self.assertEqual("capability_closed", bound["trust_basis"])
        program, = semantics.programs_for_oracle(card.oracle_id)
        self.assertIn(ATTACK_SHARED_TYPE_AMOUNT_CAPABILITY, program.capability_dependencies)
        self.assertIsNone(program.target_schema)
        self.assertEqual("$event.permanent_object", program.effects[0]["card"])

    def test_paid_original_counts_a_new_attacking_token_and_only_one_overlap_per_creature(self):
        game = self.session(6501190)
        enchantment = self.add(game.engine, ORIGINAL, zone="hand")
        vanguard = self.add(game.engine, "Skyknight Vanguard")
        automaton = self.add(game.engine, "Universal Automaton")
        traveler = self.add(game.engine, "Doomed Traveler")
        goblin = self.add(game.engine, "Goblin Piker")
        self.add(game.engine, "Fervor")
        private = self.add(game.engine, "Generic Bound Growth", seat="D", zone="hand", ref="shared-attack-private")
        for card in (enchantment, vanguard, automaton):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 2, "R": 1})
        self.checkpoint(game)
        self.cast(game, "A", enchantment)
        self.assertEqual(0, sum(game.state.players["A"].mana_pool.values()))
        self.assertIsNone(self.resolve(game))

        self._declare(game, ((vanguard, "B"), (automaton, "C"), (traveler, "D"), (goblin, "B")),
                      resolve_first=vanguard)
        self.assertEqual(4, self.trigger_count(game, enchantment))
        self.assertEqual(vanguard.object_id, game.state.stack[-1].source_object_id)
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["A"]), (pending.kind, pending.actors))
        game = self.replay(game, load=True)
        schema = game.state.pending_decision.payload_by_actor["A"]["legal_actions"][0]["choice_schema"]
        ref, = schema["legal_refs"]
        choice = {"action_id": "choose", "attacking": {ref: "C"}}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:D", choice).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        accepted = game.act("pilot:A", choice)
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertIsNone(self.resolve(game))
        expected = ((vanguard, 3), (traveler, 4), (automaton, 5), (goblin, 3))
        for card, power in expected:
            self.assertEqual(power, game.engine._numeric_stat(card.object_id, "power"))
        token, = [card for card in game.state.cards.values() if card.is_token and card.zone == "battlefield"]
        self.assertEqual("C", token.attacking)
        self.assertEqual({"soldier"}, set(game.engine._effective_type_parts(token)[1]))
        self.assertEqual(1, game.engine._numeric_stat(token.object_id, "power"))
        for seat in "ABC":
            self.assertNotIn(private.ref, json.dumps(game.packet("pilot:" + seat, full=True)))
        self.replay(game, load=True)

    def test_paid_original_excludes_a_departed_attacker_and_its_recast_incarnation(self):
        game = self.session(6501200)
        enchantment = self.add(game.engine, ORIGINAL, zone="hand")
        piker = self.add(game.engine, "Goblin Piker")
        raging = self.add(game.engine, "Raging Goblin")
        universal = self.add(game.engine, "Universal Automaton")
        self.add(game.engine, "Fervor")
        orrery = self.add(game.engine, "Vedalken Orrery")
        bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        for card in (enchantment, orrery, bounce):
            self.assert_closed(game, card)
        mountain = self.add(game.engine, "Mountain")
        self.add(game.engine, "Island", seat="B")
        self.prepare(game, mana={"C": 2, "R": 1})
        self.checkpoint(game)
        played = game.act("pilot:A", {"action_id": "cast:" + enchantment.ref, "pay": "manual",
                                     "payment": {"C": 2, "R": 1}})
        self.assertTrue(played.ok, played.summary)
        self.assertIsNone(self.resolve(game))
        self.assertFalse(game.state.cards[mountain.object_id].tapped)

        self._declare(game, ((piker, "B"), (raging, "B"), (universal, "C")))
        original = game.state.cards[raging.object_id].logical_object_id
        self.assertEqual(3, self.trigger_count(game, enchantment))
        self.cast(game, "B", bounce, [raging.ref])
        self.resolve_top(game)
        self.cast(game, "A", game.state.cards[raging.object_id])
        self.resolve_top(game)
        current = game.state.cards[raging.object_id]
        self.assertNotEqual(original, current.logical_object_id)
        self.assertNotIn(current.object_id, game.state.combat.attackers)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        for card, power in ((piker, 3), (universal, 2), (raging, 1)):
            self.assertEqual(power, game.engine._numeric_stat(card.object_id, "power"))
        self.replay(game, load=True)

    def test_omitting_the_exact_shared_attack_amount_restores_the_original_residual(self):
        original = attack_event_results.qualified_attack_characteristic_result

        def omit(binding, body, **kwargs):
            if body.casefold().endswith("for each other attacking creature that shares a creature type with it."):
                return (None, (), None, ()), False
            return original(binding, body, **kwargs)

        with patch.object(attack_event_results, "qualified_attack_characteristic_result", side_effect=omit):
            card = self.db.lookup(ORIGINAL, fuzzy=False)
            ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
            residual, = ir.material_residuals
            self.assertEqual("trigger_effect", residual.kind)
            report, _ = register_card(self.db, self.registry, ORIGINAL)
            self.assertFalse(report["strict_capability_ready"], report)

    def test_genuine_attack_amount_withdrawal_preserves_the_distinct_coat_of_arms(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        known = set(discover_tests(ROOT).values())
        document = deepcopy(raw)
        next(row for row in document["capabilities"] if row["id"] == ATTACK_SHARED_TYPE_AMOUNT_CAPABILITY).update(
            status="blocked", blockers=["Scoped live attacker type-count authority withdrawal"])
        registry = CapabilityRegistry(document)
        index = build_index(registry_value=document, declaration_source=declarations, discovered_test_ids=known)
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        closure = registry.closure((ATTACK_SHARED_TYPE_AMOUNT_CAPABILITY,), profile=PROFILE)
        self.assertIn("status:" + ATTACK_SHARED_TYPE_AMOUNT_CAPABILITY + ":blocked", closure.blockers)
        self.assertFalse(any("unverified" in blocker for blocker in closure.blockers))
        report, _ = register_card(self.db, registry, ORIGINAL)
        self.assertFalse(report["strict_capability_ready"], report)
        control, _ = register_card(self.db, registry, "Coat of Arms")
        self.assertTrue(control["strict_capability_ready"], control["blockers"])


if __name__ == "__main__":
    unittest.main()
