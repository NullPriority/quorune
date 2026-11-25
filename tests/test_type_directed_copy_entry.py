"""Native checks for type-directed copy-entry counter placement."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.compiler.entry_copy_templates import _type_directed_copy_entry_spec
from quorune.deck import DeckDefinition, DeckEntry
from quorune.entry_copy import EntryCopySpec, TYPE_DIRECTED_COPY_COUNTER_CAPABILITY
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.evidence import validate_capability_evidence_index
from quorune.rules.evidence_storage import read_evidence_document
from scripts.build_test_database import build_fixture_database
from scripts.update_capability_evidence import build_index, discover_tests


class TypeDirectedCopyEntryTests(InheritedNativeFixture, unittest.TestCase):
    copier = "Trial Divergent Mimic"
    creature = "Trial Legendary Scout"
    walker = "Trial Wayfinder"
    doubler = "Doubling Season"
    basic = "Generic Bound Plains"
    blue_cost = 1
    generic_cost = 3
    initial_loyalty = 3

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        fixtures = ROOT / "tests/fixtures"
        document = json.loads((fixtures / "counter-replacement-cards.json").read_bytes())
        document["cards"] = [row for row in document["cards"] if row["name"] == cls.doubler]
        assert len(document["cards"]) == 1
        oid = document["cards"][0]["oracle_id"]
        document["rulings"] = [row for row in document.get("rulings", []) if row["oracle_id"] == oid]
        doubler = folder / "counter-replacement.json"
        doubler.write_text(json.dumps(document), encoding="utf-8")
        database = folder / "type-directed-copy-entry.sqlite3"
        build_fixture_database([
            fixtures / "bound-effect-program-cards.json",
            fixtures / "type-directed-copy-entry-cards.json",
            fixtures / "announced-x-copy-entry-cards.json",
            fixtures / "controlled-copy-entry-cards.json",
            doubler,
        ], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Type-directed copy entry", [
            DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry(cls.basic, 30),
        ], ["Generic Bound Commander"])

    def _offer(self, game, source):
        self.assert_closed(game, source)
        self.prepare(game, mana={"U":self.blue_cost, "C":self.generic_cost})
        self.checkpoint(game)
        self.cast(game, "A", source)
        pending = self.resolve(game)
        self.assertEqual(("replacement.order", ["A"]), (pending.kind, pending.actors))
        return game.packet("pilot:A", full=True)["decision"]["ctx"]["options"]

    def _choose(self, game, choice):
        command = {"action_id":"choose", "replacement":choice["id"]}
        before = authoritative_state_hash(game.state)
        denied = game.act("pilot:D", command)
        self.assertFalse(denied.ok, denied.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))
        accepted = game.act("pilot:A", command)
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertIsNone(self.resolve(game))

    def _copy(self, options, target):
        choice, = [row for row in options if not row.get("decline") and row["id"].endswith(":" + target.ref)]
        return choice

    def test_paid_copy_uses_only_controlled_types_and_removes_legendary(self):
        for i, (name, counter, expected) in enumerate((
            (self.creature, "+1/+1", 1), (self.walker, "loyalty", self.initial_loyalty+1),
        )):
            with self.subTest(name=name):
                game = self.session(707_821+i)
                source = self.add(game.engine, self.copier, zone="hand")
                good = self.add(game.engine, name, ref="controlled-copy-target")
                # Control and ownership deliberately differ in both directions.
                good.owner = "B"
                opposed = self.add(game.engine, name, seat="B", ref="opposing-copy-target")
                opposed.owner = "A"
                hidden = self.add(game.engine, name, seat="D", zone="hand", ref="hidden-copy-target")
                good.counters[counter] = 7
                self.assert_closed(game, good)
                options = self._offer(game, source)
                encoded = json.dumps(options)
                self.assertNotIn(opposed.ref, encoded)
                self.assertNotIn(hidden.ref, encoded)
                choice = self._copy(options, good)
                game = self.replay(game, load=True)
                self._choose(game, choice)
                copy = game.state.cards[source.object_id]
                current = game.state.cards[good.object_id]
                self.assertEqual("battlefield", copy.zone)
                self.assertEqual("battlefield", current.zone)
                self.assertEqual(name, game.engine.display_name(copy.object_id))
                copy_types, _, supertypes = game.engine._effective_type_parts(copy)
                self.assertNotIn("legendary", supertypes)
                self.assertIn("creature" if counter == "+1/+1" else "planeswalker", copy_types)
                self.assertEqual(expected, copy.counters.get(counter, 0))
                self.assertEqual(7, current.counters[counter])
                self.replay(game, load=True)

    def test_type_directed_loyalty_uses_existing_counter_replacement(self):
        game = self.session(707_824)
        source = self.add(game.engine, self.copier, zone="hand")
        walker = self.add(game.engine, self.walker)
        doubler = self.add(game.engine, self.doubler)
        self.assert_closed(game, doubler)
        self.assert_closed(game, walker)
        self._choose(game, self._copy(self._offer(game, source), walker))
        copy = game.state.cards[source.object_id]
        self.assertEqual("battlefield", copy.zone)
        self.assertEqual(2*(self.initial_loyalty+1), copy.counters.get("loyalty", 0))
        self.assertNotIn("+1/+1", copy.counters)
        self.replay(game, load=True)

    def test_decline_preserves_ordinary_type_directed_copy_entry_and_replay(self):
        game = self.session(707_825)
        source = self.add(game.engine, self.copier, zone="hand")
        self.add(game.engine, self.creature)
        options = self._offer(game, source)
        decline, = [row for row in options if row.get("decline")]
        game = self.replay(game, load=True)
        self._choose(game, decline)
        live = game.state.cards[source.object_id]
        self.assertEqual("graveyard", live.zone)
        self.assertFalse(live.counters)
        self.assertNotIn("copy_overrides", live.annotations)
        self.replay(game, load=True)

    def test_type_directed_copy_requires_exact_shape_and_separate_authority(self):
        rec = self.db.lookup(self.copier)
        spec = _type_directed_copy_entry_spec(rec.oracle_text, source_name=rec.name)
        self.assertEqual(9, spec.schema_version)
        self.assertEqual(spec, EntryCopySpec.from_dict(spec.to_dict()))
        for delta in ({"type_directed_counters":1}, {"type_directed_counters":False}, {"schema_version":1},
            {"selection":{**spec.selection.to_dict(), "controller":None}},
            {"exception":{**spec.exception.to_dict(),"remove_legendary":False}}):
            with self.assertRaises(ValueError):
                EntryCopySpec.from_dict({**spec.to_dict(),**delta})
        def compile(card, registry):
            return compile_oracle_card(card,capability_registry=registry,capability_profile="commander_review")
        self.assertEqual("exact", compile(rec,self.registry).status)
        raw = json.loads((ROOT/"quorune/rules/capability-registry.json").read_bytes())
        cap = next(row for row in raw["capabilities"] if row["id"]==TYPE_DIRECTED_COPY_COUNTER_CAPABILITY)
        cap.update(status="blocked", blockers=["Type-directed copy counters withdrawn."])
        withdrawn=CapabilityRegistry(raw)
        declarations=read_evidence_document(ROOT/"platform/capability-evidence-declarations.json")
        index=build_index(registry_value=raw,declaration_source=declarations,
                          discovered_test_ids=set(discover_tests(ROOT).values()))
        withdrawn.mark_evidence_verified(validate_capability_evidence_index(index,registry=withdrawn))
        self.assertNotEqual("exact", compile(rec,withdrawn).status)
        self.assertEqual("exact", compile(replace(rec,oracle_text=rec.oracle_text.split(", except")[0]+"."),withdrawn).status)
        for existing in ("Trial Shield Mimic","Trial Measured Mimic"):
            self.assertEqual("exact", compile(self.db.lookup(existing),withdrawn).status)
        with patch("quorune.compiler.entry_copy_templates._type_directed_copy_entry_spec",return_value=None):
            self.assertNotEqual("exact",compile(rec,self.registry).status)
