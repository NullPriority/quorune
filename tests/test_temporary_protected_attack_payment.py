"""Native paid declarations under the original temporary defender tax."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.protected_attack_payment_model import CAPABILITY, ResolvedProtectedAttackPayment
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.evidence import validate_capability_evidence_index
from quorune.rules.evidence_storage import read_evidence_document
from scripts.build_test_database import build_fixture_database
from scripts.update_capability_evidence import build_index, discover_tests


class TemporaryProtectedAttackPaymentTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "protected-attack-payment.sqlite3"
        fixtures = ROOT / "tests" / "fixtures"
        build_fixture_database([
            fixtures / "bound-effect-program-cards.json",
            fixtures / "shared-type-library-cards.json",
            fixtures / "attached-stat-activation-prohibition-cards.json",
            fixtures / "temporary-protected-attack-payment-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        ruler = "Generic Bound Commander"
        cls.deck = DeckDefinition("Temporary attack payment", [
            DeckEntry(ruler, 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], [ruler])

    def advance(self, game, *, seat, kind):
        for _ in range(500):
            game.engine.pump()
            decision = game.state.pending_decision
            if (game.state.active_player == seat and decision is not None
                    and decision.kind == kind and decision.actors == [seat]):
                return
            self.assertIsNotNone(decision)
            if decision.kind == "priority":
                command = {"action_id": "pass"}
            elif decision.kind == "combat.attackers":
                command = {"action_id": "attack", "attackers": {}}
            elif decision.kind == "combat.blockers":
                command = {"action_id": "block", "blocks": {}}
            elif decision.kind == "cleanup.discard":
                principal = game.pending_principals()[0]
                context = game.packet(principal, full=True)["decision"]["ctx"]
                command = {"action_id": "discard", "cards": [row["id"] for row in context["hand"][:context["count"]]]}
            else:
                self.fail(f"Unexpected real turn decision {decision.kind}")
            completed = game.act(game.pending_principals()[0], command)
            self.assertTrue(completed.ok, completed.summary)
        self.fail("The requested real turn did not arrive")

    def test_paid_split_attack_survives_the_creatures_departure_and_expires_on_next_turn(self):
        game = self.session(508010913)
        engine = game.engine
        source = self.add(engine, "Forbidding Spirit", zone="hand", ref="RESOLVING-PROTECTION")
        bounce = self.add(engine, "Unsummon", zone="hand", ref="REMOVE-SOURCE")
        attackers = [self.add(engine, "Grizzly Bears", seat="B", ref=f"ATTACKER-{index}") for index in range(2)]
        lands = [self.add(engine, "Plains", seat="B", ref=f"ATTACK-PAYMENT-{index}") for index in range(2)]
        walkers = [engine.create_token(seat, name=f"{seat} Public Walker", characteristics={
            "type_line": "Token Planeswalker — Test", "loyalty": "4",
        })[0] for seat in "AC"]
        for card in (source, bounce):
            self.assert_closed(game, card)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, mana={"C": 2, "W": 2, "U": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", source, reserve={"U": 1})
        self.assertIsNone(self.resolve(game))
        rule, = [effect for effect in game.state.continuous_effects
                 if isinstance(effect, ResolvedProtectedAttackPayment)]
        self.assertEqual("A", rule.expires_on_next_turn_of)
        self.cast_for(game, "A", bounce, [source.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("hand", game.state.cards[source.object_id].zone)

        self.advance(game, seat="B", kind="combat.attackers")
        costs = game.state.pending_decision.payload_by_actor["B"]["declaration_costs"]
        self.assertEqual({(card.ref, destination) for card in attackers for destination in ("A", walkers[0])},
                         {(row["variable"], row["option"]) for row in costs})
        self.assertTrue(all(row["mana"] == {"GENERIC": 2} and row["source"] == rule.effect_id for row in costs))
        self.assertIsNone(game.packet("pilot:C", full=True)["decision"])
        game = self.replay(game, load=True)
        before = authoritative_state_hash(game.state)
        attempt = {"a": "attack", "atk": {attackers[0].ref: "A", attackers[1].ref: walkers[0]}, "pay": "auto"}
        self.assertFalse(game.act("pilot:C", attempt).ok)
        self.assertFalse(game.act("pilot:B", attempt).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.assertTrue(all(not game.state.cards[land.object_id].tapped for land in lands))
        split = {**attempt, "atk": {attackers[0].ref: walkers[0], attackers[1].ref: walkers[1]}}
        result = game.act("pilot:B", split)
        self.assertTrue(result.ok, result.summary)
        self.assertTrue(all(game.state.cards[land.object_id].tapped for land in lands))
        self.assertIsNotNone(game.state.cards[attackers[0].object_id].attacking)
        self.assertIsNotNone(game.state.cards[attackers[1].object_id].attacking)
        self.advance(game, seat="A", kind="priority")
        self.assertEqual("A", game.state.active_player)
        self.assertFalse(any(isinstance(effect, ResolvedProtectedAttackPayment) for effect in game.state.continuous_effects))
        self.replay(game, load=True)

    def test_original_attack_payment_requires_the_exact_owner_scope_and_duration(self):
        card = self.db.lookup("Forbidding Spirit", fuzzy=False)
        def compile_card(record, registry=self.registry):
            return compile_oracle_card(record, capability_registry=registry, capability_profile="commander_review")
        whole = compile_card(card)
        self.assertEqual("exact", whole.status, whole.material_residuals)
        self.assertTrue(any(CAPABILITY in node.capability_dependencies for face in whole.faces for node in face.nodes))
        for old, new in (("until your next turn", "until next end step"),
                         ("or planeswalkers you control", "or battles you protect"),
                         ("for each of those creatures", "to attack once"),
                         ("When this creature enters", "Whenever this creature attacks")):
            altered = replace(card, oracle_text=card.oracle_text.replace(old, new))
            self.assertNotEqual("exact", compile_card(altered).status)
        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        selected = next(row for row in raw["capabilities"] if row["id"] == CAPABILITY)
        selected.update(status="blocked", blockers=["Withdraw the protected-defender payment authority."])
        registry = CapabilityRegistry(raw)
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        index = build_index(registry_value=raw, declaration_source=declarations,
                            discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        self.assertNotEqual("exact", compile_card(card, registry).status)
        with patch("quorune.compiler.protected_attack_payment_templates.next_turn_attack_payment_template", return_value=None):
            self.assertNotEqual("exact", compile_card(card).status)
