"""Native non-targeted returns using the original entrant's public types."""

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
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.controlled_return import SHARED_ENTRY_RETURN_CAPABILITY
from quorune.rules.entry_type_return import ENTRY_TYPE_RETURN_CONTEXT, EntryTypeReturnObservation
from scripts.build_test_database import build_fixture_database


class EntrySharedTypeReturnTests(InheritedNativeFixture, unittest.TestCase):
    source = "Arcway Gnomon"
    profile = "commander_review"

    @classmethod
    def use_database(cls, path, land):
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.land_name = land
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Shared permanent type", [DeckEntry(commander, 1, "commander"),
                                                          DeckEntry(land, 30)], [commander])

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "shared-entry-return.sqlite3"
        fixtures = ROOT / "tests" / "fixtures"
        build_fixture_database([
            fixtures / "bound-effect-program-cards.json",
            fixtures / "paid-sacrifice-creature-stat-cards.json",
            fixtures / "activation-result-views-cards.json",
            fixtures / "fixed-direct-phasing-cards.json",
            fixtures / "entry-shared-type-return-cards.json",
        ], path)
        cls.use_database(path, "Generic Bound Plains")

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def enter(self, session, entrant):
        self.cast(session, "A", entrant)
        self.resolve_top(session)

    def options(self, session, expected):
        decision = self.resolve(session)
        self.assertIsNotNone(decision)
        self.assertEqual(("semantic.choice", ["A"]), (decision.kind, list(decision.actors)))
        choice = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal_actions"][0]["choice_schema"]
        self.assertEqual((0, 1, True), (choice["minimum"], choice["maximum"], choice["optional"]))
        self.assertEqual(set(expected), set(choice["legal_refs"]))

    def reject(self, session, actor, selector):
        before = authoritative_state_hash(session.state)
        result = session.act(f"pilot:{actor}", {"action_id": "choose", "objects": [selector]})
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(session.state))

    def test_native_entry_uses_another_matching_permanent_and_the_owners_hand(self):
        session = self.session(608400701)
        source = self.add(session.engine, self.source)
        entrant = self.add(session.engine, "Grizzly Bears", zone="hand")
        borrowed = self.add(session.engine, "Grizzly Bears", seat="B", ref="entry-borrowed")
        session.engine.change_control(borrowed.object_id, "A", reason="prepare a controlled permanent with another owner")
        prior_artifact_creature = self.add(session.engine, "Ornithopter")
        artifact_only = self.add(session.engine, "Sol Ring")
        opposing = self.add(session.engine, "Grizzly Bears", seat="C", ref="opposing-bear")
        for card in (source, entrant, borrowed, prior_artifact_creature, artifact_only, opposing):
            self.assert_closed(session, card)
        self.prepare(session, mana={"G": 1, "C": 1})
        self.checkpoint(session)
        self.enter(session, entrant)
        self.assertFalse(session.state.stack[-1].targets)
        self.assertEqual(1, self.trigger_count(session, source))
        self.options(session, [borrowed.ref, prior_artifact_creature.ref])
        self.reject(session, "B", borrowed.ref)
        for illegal in (entrant, artifact_only, opposing):
            self.reject(session, "A", illegal.ref)
        session = self.replay(session, load=True)
        result = session.act("pilot:A", {"action_id": "choose", "objects": [borrowed.ref]})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("battlefield", session.state.cards[entrant.object_id].zone)
        self.assertIn(borrowed.object_id, session.state.players["B"].zones["hand"])
        self.replay(session, load=True)

    def test_artifact_entry_does_not_trigger_and_the_optional_choice_can_decline(self):
        session = self.session(608400702)
        source = self.add(session.engine, self.source)
        artifact = self.add(session.engine, "Ornithopter", zone="hand")
        entrant = self.add(session.engine, "Grizzly Bears", zone="hand")
        self.prepare(session, mana={"G": 1, "C": 1})
        self.checkpoint(session)
        self.enter(session, artifact)
        self.assertEqual(0, self.trigger_count(session, source))
        self.enter(session, entrant)
        self.options(session, [artifact.ref])
        declined = session.act("pilot:A", {"action_id": "choose", "objects": []})
        self.assertTrue(declined.ok, declined.summary)
        self.assertEqual("battlefield", session.state.cards[artifact.object_id].zone)
        self.assertEqual("battlefield", session.state.cards[entrant.object_id].zone)
        self.replay(session)

    def _changed_entrant_departure(self, departure="Unsummon"):
        session = self.session(608400703)
        source = self.add(session.engine, self.source)
        entrant = self.add(session.engine, "Grizzly Bears", zone="hand")
        coating = self.add(session.engine, "Liquimetal Coating", seat="B")
        removal = self.add(session.engine, departure, zone="hand", seat="B")
        self.add(session.engine, "Island", seat="B")
        self.add(session.engine, "Island", seat="B", ref="entry-second-removal-land")
        self.assert_closed(session, source)
        self.assert_closed(session, coating)
        self.prepare(session, mana={"G": 1, "C": 1})
        self.checkpoint(session)
        self.enter(session, entrant)
        self.priority(session, "B")
        packet = session.packet("pilot:B", full=True)
        actions = packet["decision"]["ctx"]["legal"]["actions"]
        activation = next(action for action in actions if action["id"].startswith(f"activate:{coating.ref}:"))
        result = session.act("pilot:B", {"action_id": activation["id"], "targets": [entrant.ref]})
        self.assertTrue(result.ok, result.summary)
        self.resolve_top(session)
        self.cast(session, "B", removal, [entrant.ref])
        self.resolve_top(session)
        return session, source, session.state.cards[entrant.object_id]

    def test_last_public_types_after_a_change_survive_departure_and_replay(self):
        session, source, entrant = self._changed_entrant_departure()
        self.assertEqual("hand", entrant.zone)
        observation = EntryTypeReturnObservation.from_dict(
            session.state.stack[-1].context[ENTRY_TYPE_RETURN_CONTEXT])
        self.assertEqual(("artifact", "creature"), observation.last_known_types)
        session = self.replay(session, load=True)
        self.options(session, [source.ref])
        result = session.act("pilot:A", {"action_id": "choose", "objects": [source.ref]})
        self.assertTrue(result.ok, result.summary)
        self.assertIn(source.object_id, session.state.players["A"].zones["hand"])
        self.replay(session, load=True)

    def test_phased_out_entrant_uses_the_last_types_before_it_phased(self):
        session, source, entrant = self._changed_entrant_departure("Reality Ripple")
        self.assertEqual("battlefield", entrant.zone)
        self.assertTrue(entrant.phased_out)
        session = self.replay(session, load=True)
        self.options(session, [source.ref])
        result = session.act("pilot:A", {"action_id": "choose", "objects": []})
        self.assertTrue(result.ok, result.summary)
        self.replay(session)

    def test_returned_physical_card_can_be_selected_as_a_different_permanent(self):
        session = self.session(608400704)
        source = self.add(session.engine, self.source)
        entrant = self.add(session.engine, "Grizzly Bears", zone="hand")
        blink = self.add(session.engine, "Cloudshift", zone="hand")
        self.add(session.engine, self.land_name)
        self.assert_closed(session, blink)
        self.prepare(session, mana={"G": 1, "C": 1})
        self.checkpoint(session)
        self.enter(session, entrant)
        original_id = session.state.cards[entrant.object_id].logical_object_id
        self.cast(session, "A", blink, [entrant.ref])
        self.resolve_top(session)
        current = session.state.cards[entrant.object_id]
        self.assertNotEqual(original_id, current.logical_object_id)
        self.assertEqual(2, self.trigger_count(session, source))
        self.options(session, [current.ref])
        result = session.act("pilot:A", {"action_id": "choose", "objects": [current.ref]})
        self.assertTrue(result.ok, result.summary)
        self.assertIn(entrant.object_id, session.state.players["A"].zones["hand"])
        self.replay(session, load=True)

    def test_omitting_departure_capture_fails_the_type_snapshot_witness(self):
        with (patch("quorune.rules.entry_type_return.pin_entry_type_return_departures"),
              self.assertRaisesRegex(AssertionError, r"artifact.*creature.*None")):
            self.test_last_public_types_after_a_change_survive_departure_and_replay()

    def test_withdrawn_authority_and_widened_printed_variants_stay_unresolved(self):
        card = self.db.lookup(self.source, fuzzy=False)
        compiled = compile_oracle_card(card, capability_registry=self.registry, capability_profile=self.profile)
        self.assertEqual("exact", compiled.status, compiled.material_residuals)
        node, = compiled.faces[0].nodes
        self.assertIn(SHARED_ENTRY_RETURN_CAPABILITY, node.capability_dependencies)
        for variant in (card.oracle_text.replace("you may return", "return"),
                        card.oracle_text.replace("another permanent", "target permanent"),
                        card.oracle_text.replace("nonartifact permanent", "artifact permanent")):
            compiled = compile_oracle_card(replace(card, oracle_text=variant),
                                           capability_registry=self.registry, capability_profile=self.profile)
            self.assertNotEqual("exact", compiled.status)
        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_text(encoding="utf-8"))
        next(row for row in raw["capabilities"] if row["id"] == SHARED_ENTRY_RETURN_CAPABILITY).update(
            status="blocked", blockers=["entry return authority withdrawn"])
        registry = CapabilityRegistry(raw)
        registry.mark_evidence_verified(self.registry.evidence_fingerprint)
        self.assertNotEqual("exact", compile_oracle_card(card, capability_registry=registry,
                                                       capability_profile=self.profile).status)
