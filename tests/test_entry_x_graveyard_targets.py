
"""Public target offers bound to an X-cost permanent's sealed entry."""

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
from quorune.rules.entry_x_graveyard_target import ENTRY_X_GRAVEYARD_TARGET_CAPABILITY
from quorune.rules.evidence import validate_capability_evidence_index
from quorune.rules.evidence_storage import read_evidence_document
from scripts.build_test_database import build_fixture_database
from scripts.update_capability_evidence import build_index, discover_tests


class EntryXGraveyardTargetTests(InheritedNativeFixture, unittest.TestCase):
    vehicle = "Trial Formation Rover"
    zero = "Trial Charge Bauble"
    artifact = "Trial Engraved Plate"
    creature = "Trial Field Beast"
    expensive = "Trial Watcher"
    basic = "Generic Bound Plains"
    removal = "Trial Decommission"
    return_spell = "Trial Restoration"

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "entry-x-graveyard.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/bound-effect-program-cards.json",
            ROOT / "tests/fixtures/entry-x-graveyard-target-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Entry X targets", [
            DeckEntry("Generic Bound Commander", 1, "commander"),
            DeckEntry(cls.basic, 30),
        ], ["Generic Bound Commander"])

    def offer(self, game, seat="A"):
        self.until(game, lambda s: s.state.pending_decision.kind == "semantic.target")
        return game.packet("pilot:" + seat, full=True)["decision"]["ctx"]["target_schema"]

    def cast_x(self, game, source, x):
        self.priority(game, "A")
        sent = game.act("pilot:A", {"action_id": "cast:" + source.ref, "x": x, "pay": "auto"})
        self.assertTrue(sent.ok, sent.summary)
        self.assertEqual(x, game.state.stack[-1].x_value)

    def test_paid_entry_limits_public_targets_and_survives_its_sources_departure(self):
        game = self.session(107301101)
        source = self.add(game.engine, self.vehicle, zone="hand")
        free = self.add(game.engine, self.zero, zone="graveyard")
        artifact = self.add(game.engine, self.artifact, zone="graveyard")
        creature = self.add(game.engine, self.creature, zone="graveyard")
        expensive = self.add(game.engine, self.expensive, zone="graveyard")
        land = self.add(game.engine, self.basic, zone="graveyard")
        foreign = self.add(game.engine, self.zero, seat="C", zone="graveyard", ref="entry-x-foreign")
        hidden = self.add(game.engine, self.creature, seat="D", zone="hand", ref="entry-x-secret")
        removal = self.add(game.engine, self.removal, seat="B", zone="hand")
        self.assert_closed(game, source)
        self.assert_closed(game, removal)
        game.state.players["B"].mana_pool.update(W=1, C=1)
        self.prepare(game, mana={"W": 1, "B": 1, "C": 2})
        self.checkpoint(game)
        self.cast_x(game, source, 2)
        game = self.replay(game, load=True)
        offer = self.offer(game)
        self.assertCountEqual(offer["legal_refs"], [free.ref, artifact.ref, creature.ref])
        for seat in "BCD":
            self.assertIsNone(game.packet("pilot:" + seat, full=True)["decision"])
        self.assertNotIn(hidden.ref, json.dumps(game.packet("pilot:A", full=True)))
        game = self.replay(game, load=True)
        previous = authoritative_state_hash(game.state)
        for denied in (expensive, foreign, land):
            result = game.act("pilot:A", {"action_id": "choose", "targets": [denied.ref]})
            self.assertFalse(result.ok, result.summary)
            self.assertEqual(previous, authoritative_state_hash(game.state))
        self.target(game, "A", [creature.ref])
        self.cast(game, "B", removal, [source.ref])
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        card = game.state.cards[creature.object_id]
        self.assertEqual(("battlefield", "A", False), (card.zone, card.controller, card.tapped))
        self.assertEqual("graveyard", game.state.cards[artifact.object_id].zone)
        self.replay(game, load=True)

    def test_an_uncast_entry_reads_zero_even_after_a_different_spell_resolves(self):
        game = self.session(107301102)
        source = self.add(game.engine, self.vehicle, zone="graveyard")
        reanimate = self.add(game.engine, self.return_spell, zone="hand")
        free = self.add(game.engine, self.zero, zone="graveyard")
        artifact = self.add(game.engine, self.artifact, zone="graveyard")
        self.assert_closed(game, source)
        self.assert_closed(game, reanimate)
        self.prepare(game, mana={"W": 1, "C": 3})
        self.checkpoint(game)
        self.cast(game, "A", reanimate, [source.ref])
        self.assertEqual([free.ref], self.offer(game)["legal_refs"])
        game = self.replay(game, load=True)
        self.target(game, "A", [free.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("battlefield", game.state.cards[free.object_id].zone)
        self.assertEqual("graveyard", game.state.cards[artifact.object_id].zone)
        self.replay(game, load=True)

    def test_missing_zero_value_target_does_not_leave_a_mandatory_choice(self):
        game = self.session(107301103)
        source = self.add(game.engine, self.vehicle, zone="hand")
        artifact = self.add(game.engine, self.artifact, zone="graveyard")
        self.add(game.engine, self.zero, seat="B", zone="graveyard")
        self.assert_closed(game, source)
        self.prepare(game, mana={"W": 1, "B": 1})
        self.checkpoint(game)
        self.cast_x(game, source, 0)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("battlefield", game.state.cards[source.object_id].zone)
        self.assertEqual("graveyard", game.state.cards[artifact.object_id].zone)
        self.replay(game, load=True)

    def test_verified_target_authority_withdraws_only_its_typed_bound(self):
        card = self.db.lookup(self.vehicle)
        def compile(record, registry):
            return compile_oracle_card(record, capability_registry=registry,
                                       capability_profile="commander_review")
        exact = compile(card, self.registry)
        self.assertEqual("exact", exact.status, exact.material_residuals)
        entry, = (n for face in exact.faces for n in face.nodes if n.event == "permanent.enter.self")
        self.assertIn(ENTRY_X_GRAVEYARD_TARGET_CAPABILITY, entry.capability_dependencies)
        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        row, = (r for r in raw["capabilities"] if r["id"] == ENTRY_X_GRAVEYARD_TARGET_CAPABILITY)
        row.update(status="blocked", blockers=["Sealed public target bound withdrawn."])
        withdrawn = CapabilityRegistry(raw)
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        index = build_index(registry_value=raw, declaration_source=declarations,
                            discovered_test_ids=set(discover_tests(ROOT).values()))
        withdrawn.mark_evidence_verified(validate_capability_evidence_index(index, registry=withdrawn))
        self.assertNotEqual("exact", compile(card, withdrawn).status)
        printed_fixed = replace(card, oracle_text=card.oracle_text.replace("mana value X", "mana value 3"))
        self.assertEqual("exact", compile(printed_fixed, withdrawn).status)
        with patch("quorune.compiler.entry_x_results.entry_x_target_trigger_effect_compiler",
                   side_effect=lambda record, line, *, card_name, ordinary: ordinary):
            self.assertNotEqual("exact", compile(card, self.registry).status)
