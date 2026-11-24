"""Native entry and departure triggers sharing one original exile batch."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.attachments import attach_objects
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.linked_exile_return_model import (
    LINKED_BATCH_DEPARTURE_RETURN_CAPABILITY, LINKED_BATCH_DEPARTURE_RETURN_VERSION, LinkedExileReturnSpec,
)
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.evidence import validate_capability_evidence_index
from quorune.rules.evidence_storage import read_evidence_document
from quorune.zone_transitions import ZoneTransitionOwner
from scripts.build_test_database import build_fixture_database
from scripts.update_capability_evidence import build_index, discover_tests


class LinkedBatchDepartureReturnTests(InheritedNativeFixture, unittest.TestCase):
    realm = "Trial Boundary Warden"
    world = "Trial Horizon Drake"
    petradon = "Trial Stone Sentinel"
    creature = "Trial Public Beast"
    aura = "Trial Calming Spell"
    bounce = "Trial Departure Gust"
    land = "Generic Bound Plains"

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "linked-batch-departure.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([
            fixtures / "bound-effect-program-cards.json",
            fixtures / "linked-batch-departure-return-cards.json",
        ], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        name = "Generic Bound Commander"
        cls.deck = DeckDefinition("Linked batches", [
            DeckEntry(name, 1, "commander"), DeckEntry(cls.land, 30),
        ], [name])

    def _prepare_sources(self, game, original, removal, mana):
        game.state.config.semantic_policy = "trusted_only"
        for card in (original, removal):
            self.assert_closed(game, card)
        game.state.players["B"].mana_pool["U"] = 1
        self.prepare(game, mana=mana)
        self.checkpoint(game)
        self.cast(game, "A", original)
        self.resolve_top(game)

    def _current(self, game, card):
        return game.state.cards[card.object_id]

    def _assert_zone(self, game, zone, cards):
        for card in cards:
            self.assertEqual(zone, self._current(game, card).zone, card.ref)

    def test_linked_all_lands_return_tapped_to_owners_and_ignore_reentered_exile(self):
        game = self.session(607_207_01)
        engine = game.engine
        source = self.add(engine, self.realm, zone="hand", ref="REALM-SOURCE")
        removal = self.add(engine, self.bounce, seat="B", zone="hand", ref="REALM-DEPARTURE")
        own = self.add(engine, self.land, ref="LINKED-A-LAND")
        borrowed = self.add(engine, self.land, seat="B", ref="LINKED-B-LAND")
        reentered = self.add(engine, self.land, seat="C", ref="LINKED-STALE-LAND")
        unrelated = self.add(engine, self.land, seat="D", zone="exile", ref="UNLINKED-LAND")
        creature = self.add(engine, self.creature, ref="NONLAND")
        engine.change_control(borrowed.object_id, "A", reason="A controls B's land at exile")
        self._prepare_sources(game, source, removal, {"C": 3, "R": 1, "G": 1, "W": 1})
        self.assertIsNone(self.resolve(game))
        self._assert_zone(game, "exile", (own, borrowed, reentered, unrelated))
        self._assert_zone(game, "battlefield", (source, creature))
        initial_exile = reentered.logical_object_id
        owner = ZoneTransitionOwner(engine)
        owner.move_card(reentered.object_id, "graveyard")
        owner.move_card(reentered.object_id, "exile")
        self.assertNotEqual(initial_exile, reentered.logical_object_id)
        self.checkpoint(game)
        game = self.replay(game, load=True)
        self.cast(game, "B", removal, [source.ref])
        self.assertIsNone(self.resolve(game))
        for card in (own, borrowed):
            current = self._current(game, card)
            self.assertEqual(("battlefield", card.owner, True), (current.zone, current.controller, current.tapped))
        self._assert_zone(game, "exile", (reentered, unrelated))
        self._assert_zone(game, "hand", (source,))
        self.replay(game, load=True)

    def test_linked_other_permanents_hold_return_until_the_aura_owner_chooses(self):
        game = self.session(607_207_02)
        engine = game.engine
        source = self.add(engine, self.world, zone="hand", ref="WORLD-SOURCE")
        removal = self.add(engine, self.bounce, seat="B", zone="hand", ref="WORLD-DEPARTURE")
        land = self.add(engine, self.land, ref="WORLD-LAND")
        aura = self.add(engine, self.aura, seat="C", ref="WORLD-AURA")
        anchor = self.add(engine, self.creature, seat="B", ref="VISIBLE-AURA-RECIPIENT")
        other = self.add(engine, self.creature, seat="D", ref="OTHER-AURA-RECIPIENT")
        private = self.add(engine, self.creature, seat="D", zone="hand", ref="PRIVATE-AURA-DECOY")
        attach_objects(game.state.cards, aura, anchor, source_timestamp=aura.zone_timestamp, players=game.state.players)
        engine.change_control(aura.object_id, "A", reason="A controls the Aura before exile")
        self._prepare_sources(game, source, removal, {"C": 3, "R": 3})
        self.assertIsNone(self.resolve(game))
        self._assert_zone(game, "exile", (land, aura))
        self._assert_zone(game, "battlefield", (source, anchor, other))
        game = self.replay(game, load=True)
        self.cast(game, "B", removal, [source.ref])
        pending = self.resolve(game)
        self.assertEqual(("aura.entry", ["C"]), (pending.kind, pending.actors))
        self._assert_zone(game, "exile", (land, aura))
        packet = game.packet("pilot:C", full=True)
        self.assertNotIn(private.ref, json.dumps(packet))
        command = {"action_id": "choose", "aura_target": anchor.ref}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:A", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        game = self.replay(game, load=True)
        selected = game.act("pilot:C", command)
        self.assertTrue(selected.ok, selected.summary)
        self.assertIsNone(self.resolve(game))
        self._assert_zone(game, "battlefield", (land, aura, anchor, other))
        self.assertEqual(("C", anchor.object_id), (
            self._current(game, aura).controller, self._current(game, aura).attached_to,
        ))
        self.assertFalse(self._current(game, land).tapped)
        self.replay(game, load=True)

    def test_linked_departure_before_entry_leaves_later_exiled_objects_in_exile(self):
        game = self.session(607_207_03)
        engine = game.engine
        source = self.add(engine, self.world, zone="hand", ref="EARLY-DEPARTURE-SOURCE")
        removal = self.add(engine, self.bounce, seat="B", zone="hand", ref="EARLY-DEPARTURE-SPELL")
        linked = self.add(engine, self.land, ref="EXILED-AFTER-DEPARTURE")
        outside = self.add(engine, self.land, seat="B", ref="NOT-CONTROLLED")
        self._prepare_sources(game, source, removal, {"C": 3, "R": 3})
        trigger = game.state.stack[-1]
        self.assertEqual(("permanent.enter", source.object_id), (trigger.context["event"], trigger.source_object_id))
        self.cast(game, "B", removal, [source.ref])
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self._assert_zone(game, "hand", (source,))
        self._assert_zone(game, "exile", (linked,))
        self._assert_zone(game, "battlefield", (outside,))
        self.replay(game, load=True)

    def test_linked_two_land_targets_use_only_the_surviving_original_incarnation(self):
        game = self.session(607_207_04)
        engine = game.engine
        source = self.add(engine, self.petradon, zone="hand", ref="TWO-LAND-SOURCE")
        removal = self.add(engine, self.bounce, seat="B", zone="hand", ref="TWO-LAND-DEPARTURE")
        stale = self.add(engine, self.land, seat="C", ref="STALE-LAND-TARGET")
        retained = self.add(engine, self.land, seat="D", ref="SURVIVING-LAND-TARGET")
        unrelated = self.add(engine, self.land, ref="UNSELECTED-LAND")
        self._prepare_sources(game, source, removal, {"C": 6, "R": 2})
        pending = game.state.pending_decision
        self.assertEqual(("semantic.target", ["A"]), (pending.kind, pending.actors))
        before = authoritative_state_hash(game.state)
        bad = game.act("pilot:A", {"action_id": "choose", "targets": [stale.ref, stale.ref]})
        self.assertFalse(bad.ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        accepted = game.act("pilot:A", {"action_id": "choose", "targets": [stale.ref, retained.ref]})
        self.assertTrue(accepted.ok, accepted.summary)
        owner = ZoneTransitionOwner(engine)
        owner.move_card(stale.object_id, "exile")
        owner.move_card(stale.object_id, "battlefield")
        self.checkpoint(game)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self._assert_zone(game, "battlefield", (stale, unrelated))
        self._assert_zone(game, "exile", (retained,))
        self.cast(game, "B", removal, [source.ref])
        self.assertIsNone(self.resolve(game))
        self._assert_zone(game, "battlefield", (stale, unrelated, retained))
        self.assertEqual(("D", False), (
            self._current(game, retained).controller, self._current(game, retained).tapped,
        ))
        self.replay(game, load=True)

    def test_linked_batch_authority_is_required_for_both_printed_trigger_phases(self):
        def compile_card(name, registry):
            return compile_oracle_card(self.db.lookup(name), capability_registry=registry,
                                       capability_profile="commander_review")

        for name in (self.realm, self.world, self.petradon):
            ir = compile_card(name, self.registry)
            self.assertEqual("exact", ir.status, ir.material_residuals)
            entry, departure = [node for node in ir.faces[0].nodes
                                if node.event in ("permanent.enter.self", "permanent.leave.self")]
            self.assertEqual(("exile", "return"), (entry.effects[0]["phase"], departure.effects[0]["phase"]))
            self.assertEqual(entry.effects[0]["binding_id"], departure.effects[0]["binding_id"])
            self.assertEqual(entry.effects[0]["spec"], departure.effects[0]["spec"])
            spec = LinkedExileReturnSpec.from_dict(entry.effects[0]["spec"])
            self.assertEqual(LINKED_BATCH_DEPARTURE_RETURN_VERSION, spec.schema_version)
        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        owner = next(row for row in raw["capabilities"]
                     if row["id"] == LINKED_BATCH_DEPARTURE_RETURN_CAPABILITY)
        owner.update(status="blocked", blockers=["Linked batch authority withdrawn."])
        registry = CapabilityRegistry(raw)
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        index = build_index(registry_value=raw, declaration_source=declarations,
                            discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        for name in (self.realm, self.world, self.petradon):
            self.assertNotEqual("exact", compile_card(name, registry).status)
        with patch("quorune.compiler.linked_exile_return_templates.linked_batch_departure_scope", return_value=None):
            self.assertNotEqual("exact", compile_card(self.world, self.registry).status)
