"""Native delegated targeting with the original control exchange entrant."""

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
from quorune.entry_control_exchange_model import ENTRY_EXCHANGE_CAPABILITY, ENTRY_EXCHANGE_TARGET_FIELD
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.selection.copy_targets import copy_target_public_schema
from quorune.target_source import stack_copy_targeting_source
from scripts.build_test_database import build_fixture_database


class EntryControlExchangeTests(InheritedNativeFixture, unittest.TestCase):
    source = "Parliament of Copper"
    profile = "commander_review"

    @classmethod
    def use_database(cls, path, land):
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Entry exchange", [
            DeckEntry(commander, 1, "commander"), DeckEntry(land, 30),
        ], [commander])

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "entry-control-exchange.sqlite3"
        fixtures = ROOT / "tests" / "fixtures"
        build_fixture_database([
            fixtures / "bound-effect-program-cards.json",
            fixtures / "activation-result-views-cards.json",
            fixtures / "announced-x-source-sacrifice-cards.json",
            fixtures / "untargeted-untap-cards.json",
            fixtures / "entry-control-exchange-cards.json",
        ], path)
        cls.use_database(path, "Generic Bound Plains")

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def pending_targets(self, session, *, chooser, controller, expected):
        self.until(session, lambda state: state.state.pending_decision is not None
                   and state.state.pending_decision.kind == "semantic.target")
        decision = session.state.pending_decision
        self.assertEqual([chooser], list(decision.actors))
        item = session.state.stack[-1]
        self.assertEqual(controller, item.controller)
        schema = session.packet(f"pilot:{chooser}", full=True)["decision"]["ctx"]["target_schema"]
        self.assertEqual(set(expected), set(schema["legal_refs"]))
        self.assertEqual(1, schema["count"])
        return item

    def choose(self, session, seat, targets):
        return session.act(f"pilot:{seat}", {"action_id": "choose", "targets": list(targets)})

    def test_entrant_controller_chooses_a_land_using_the_trigger_controllers_protection(self):
        session = self.session(701120601)
        source = self.add(session.engine, self.source)
        entrant = self.add(session.engine, "Treasure Vault", seat="B", zone="hand")
        own_land = self.add(session.engine, "Forest", seat="B", ref="same-controller-land")
        legal_hexproof = self.add(session.engine, "Lotus Field", seat="A")
        other_land = self.add(session.engine, "Forest", seat="C", ref="other-controller-land")
        for card in (source, entrant, legal_hexproof, other_land):
            self.assert_closed(session, card)
        self.prepare(session, active="B", actor="B")
        self.checkpoint(session)
        result = session.act("pilot:B", {"action_id": f"play-land:{entrant.ref}"})
        self.assertTrue(result.ok, result.summary)
        self.pending_targets(session, chooser="B", controller="A", expected=[legal_hexproof.ref, other_land.ref])
        before = authoritative_state_hash(session.state)
        self.assertFalse(self.choose(session, "A", [legal_hexproof.ref]).ok)
        self.assertFalse(self.choose(session, "B", []).ok)
        self.assertFalse(self.choose(session, "B", [own_land.ref]).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        session = self.replay(session, load=True)
        result = self.choose(session, "B", [legal_hexproof.ref])
        self.assertTrue(result.ok, result.summary)
        copy_probe = self.replay(session, load=True)
        engine = copy_probe.engine
        item = copy_probe.state.stack[-1]
        current_schema = engine._stack_target_schema(item, engine.semantics.get(item.semantic_key))
        copied = copy_target_public_schema(
            engine, "C", current_schema, item.modes, item.targets, item.context["target_groups"],
            engine._stack_source_ref(item), acting_source=stack_copy_targeting_source(engine, item, controller="C"),
            original_snapshots=item.context["target_snapshots"],
        )
        self.assertEqual([other_land.ref], copied["groups"][0]["legal_refs"])
        self.assertIsNone(self.resolve(session))
        self.assertEqual("A", session.state.cards[entrant.object_id].controller)
        self.assertEqual("B", session.state.cards[legal_hexproof.object_id].controller)
        self.assertEqual("C", session.state.cards[other_land.object_id].controller)
        self.replay(session, load=True)

    def test_original_entrant_departure_preserves_the_ability_and_prevents_a_partial_exchange(self):
        session = self.session(701120602)
        source = self.add(session.engine, self.source)
        entrant = self.add(session.engine, "Ornithopter", seat="B", zone="hand")
        candidate = self.add(session.engine, "Grizzly Bears", seat="C")
        bounce = self.add(session.engine, "Unsummon", zone="hand")
        self.add(session.engine, "Island")
        self.assert_closed(session, source)
        self.prepare(session, active="B", actor="B")
        self.checkpoint(session)
        self.cast(session, "B", entrant)
        self.resolve_top(session)
        item = self.pending_targets(session, chooser="B", controller="A", expected=[candidate.ref])
        result = self.choose(session, "B", [candidate.ref])
        self.assertTrue(result.ok, result.summary)
        self.cast(session, "A", bounce, [entrant.ref])
        self.resolve_top(session)
        self.assertEqual("hand", session.state.cards[entrant.object_id].zone)
        self.assertEqual(item.ref, session.state.stack[-1].ref)
        session = self.replay(session, load=True)
        target = session.state.stack[-1]
        schema = session.engine._stack_target_schema(target, session.engine.semantics.get(target.semantic_key))
        self.assertEqual({"controller": "B", "types": ["artifact", "creature"]},
                         schema[ENTRY_EXCHANGE_TARGET_FIELD])
        self.assertIsNone(self.resolve(session))
        self.assertEqual("C", session.state.cards[candidate.object_id].controller)
        self.assertEqual("B", session.state.cards[entrant.object_id].controller)
        self.replay(session, load=True)

    def test_source_enchantment_can_be_the_original_entrant(self):
        session = self.session(701120603)
        source = self.add(session.engine, self.source, zone="hand")
        other = self.add(session.engine, "Fervor", seat="B")
        self.assert_closed(session, source)
        self.prepare(session, mana={"C": 3, "R": 2})
        self.checkpoint(session)
        self.cast(session, "A", source)
        self.resolve_top(session)
        self.pending_targets(session, chooser="A", controller="A", expected=[other.ref])
        result = self.choose(session, "A", [other.ref])
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(("B", "A"), (session.state.cards[source.object_id].controller,
                                     session.state.cards[other.object_id].controller))
        self.replay(session)

    def test_exchange_scope_and_withdrawal_are_closed(self):
        card = self.db.lookup(self.source, fuzzy=False)
        result = compile_oracle_card(card, capability_registry=self.registry, capability_profile=self.profile)
        self.assertEqual("exact", result.status, result.material_residuals)
        self.assertIn(ENTRY_EXCHANGE_CAPABILITY, result.faces[0].nodes[0].capability_dependencies)
        for body in (
            card.oracle_text.replace("its controller chooses", "you may choose"),
            card.oracle_text.replace("shares a card type", "shares a color"),
            card.oracle_text.replace("or enchantment enters", "or land enters"),
        ):
            self.assertNotEqual("exact", compile_oracle_card(replace(card, oracle_text=body),
                                                            capability_registry=self.registry,
                                                            capability_profile=self.profile).status)
        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_text(encoding="utf-8"))
        next(row for row in raw["capabilities"] if row["id"] == ENTRY_EXCHANGE_CAPABILITY).update(
            status="blocked", blockers=["entry exchange authority withdrawn"])
        registry = CapabilityRegistry(raw)
        registry.mark_evidence_verified(self.registry.evidence_fingerprint)
        self.assertNotEqual("exact", compile_oracle_card(card, capability_registry=registry,
                                                       capability_profile=self.profile).status)

    def test_delegated_chooser_omission_is_killed(self):
        with (patch("quorune.rules.entry_control_exchange.entry_exchange_target_chooser", return_value="A"),
              self.assertRaisesRegex(AssertionError, r"'B'.*'A'")):
            self.test_entrant_controller_chooses_a_land_using_the_trigger_controllers_protection()
