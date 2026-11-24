
"""Native verification of shield counters tied to the selected copy's controller."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.compiler.entry_copy_templates import entry_copy_spec
from quorune.deck import DeckDefinition, DeckEntry
from quorune.entry_copy import CONTROLLED_COPY_SHIELD_CAPABILITY, EntryCopySpec
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.evidence import validate_capability_evidence_index
from quorune.rules.evidence_storage import read_evidence_document
from scripts.build_test_database import build_fixture_database
from scripts.update_capability_evidence import build_index, discover_tests


class ControlledCopyEntryShieldTests(InheritedNativeFixture, unittest.TestCase):
    copier = "Trial Shield Mimic"
    target = "Generic Bound Body"
    doubler = "Doubling Season"
    prohibition = "Solemnity"
    basic = "Generic Bound Plains"
    blue_cost = 1
    generic_cost = 3

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        fixtures = ROOT / "tests/fixtures"
        parts = [
            fixtures / "bound-effect-program-cards.json",
            fixtures / "controlled-copy-entry-cards.json",
        ]
        for filename, name in (("counter-replacement-cards.json", cls.doubler),
                               ("counter-prohibition-cards.json", cls.prohibition)):
            document = json.loads((fixtures / filename).read_bytes())
            document["cards"] = [row for row in document["cards"] if row["name"] == name]
            assert len(document["cards"]) == 1
            oid = document["cards"][0]["oracle_id"]
            document["rulings"] = [row for row in document.get("rulings", []) if row["oracle_id"] == oid]
            temporary = folder / filename
            temporary.write_text(json.dumps(document), encoding="utf-8")
            parts.append(temporary)
        database = folder / "controlled-copy-entry.sqlite3"
        build_fixture_database(parts, database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Copy-entry counters", [
            DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry(cls.basic, 30),
        ], ["Generic Bound Commander"])

    def _cast_copy(self, game, source):
        self.assert_closed(game, source)
        self.prepare(game, mana={"U": self.blue_cost, "C": self.generic_cost})
        self.checkpoint(game)
        self.cast(game, "A", source)
        pending = self.resolve(game)
        self.assertEqual(("replacement.order", ["A"]), (pending.kind, pending.actors))
        return game.packet("pilot:A", full=True)["decision"]["ctx"]["options"]

    def _apply_choice(self, game, choice):
        command = {"action_id": "choose", "replacement": choice["id"]}
        previous = authoritative_state_hash(game.state)
        denied = game.act("pilot:D", command)
        self.assertFalse(denied.ok, denied.summary)
        self.assertEqual(previous, authoritative_state_hash(game.state))
        accepted = game.act("pilot:A", command)
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertIsNone(self.resolve(game))

    def _selected_option(self, options, target):
        choice, = [row for row in options if not row.get("decline") and row["id"].endswith(":" + target.ref)]
        return choice

    def test_controlled_copy_choice_places_its_own_shield_counter_and_replays(self):
        for controlled in (True, False):
            with self.subTest(controlled=controlled):
                game = self.session(707_411 + int(controlled))
                source = self.add(game.engine, self.copier, zone="hand")
                own = self.add(game.engine, self.target, ref="controlled-copy-body")
                opponent = self.add(game.engine, self.target, seat="B", ref="opposing-copy-body")
                own.owner = "B"
                opponent.owner = "A"
                selected = own if controlled else opponent
                selected.counters["shield"] = 3
                hidden = self.add(game.engine, self.target, seat="D", zone="hand", ref="private-copy-may-not-appear")
                options = self._cast_copy(game, source)
                self.assertNotIn(hidden.ref, json.dumps(game.packet("pilot:A", full=True)))
                self._selected_option(options, own)
                self._selected_option(options, opponent)
                choice = self._selected_option(options, selected)
                game = self.replay(game, load=True)
                self._apply_choice(game, choice)
                live = game.state.cards[source.object_id]
                self.assertEqual("battlefield", live.zone)
                self.assertEqual(self.target, game.engine.display_name(live.object_id))
                self.assertEqual(int(controlled), live.counters.get("shield", 0))
                self.assertEqual(3, game.state.cards[selected.object_id].counters["shield"])
                self.assertEqual("A", live.controller)
                self.replay(game, load=True)

    def test_controlled_copy_shield_uses_counter_replacement_and_prohibition(self):
        for seed, modifier, seat, expected in (
            (707_414, self.doubler, "A", 2),
            (707_415, self.prohibition, "C", 0),
        ):
            with self.subTest(modifier=modifier):
                game = self.session(seed)
                source = self.add(game.engine, self.copier, zone="hand")
                target = self.add(game.engine, self.target)
                change = self.add(game.engine, modifier, seat=seat)
                self.assert_closed(game, change)
                choice = self._selected_option(self._cast_copy(game, source), target)
                self._apply_choice(game, choice)
                live = game.state.cards[source.object_id]
                self.assertEqual("battlefield", live.zone)
                self.assertEqual(self.target, game.engine.display_name(live.object_id))
                self.assertEqual(expected, live.counters.get("shield", 0))
                self.replay(game, load=True)

    def test_declining_the_copy_places_no_shield_and_preserves_replay(self):
        game = self.session(707_416)
        source = self.add(game.engine, self.copier, zone="hand")
        self.add(game.engine, self.target)
        choices = self._cast_copy(game, source)
        decline, = [choice for choice in choices if choice.get("decline")]
        game = self.replay(game, load=True)
        self._apply_choice(game, decline)
        live = game.state.cards[source.object_id]
        self.assertEqual("graveyard", live.zone)
        self.assertFalse(live.counters)
        self.assertNotIn("copy_overrides", live.annotations)
        self.replay(game, load=True)

    def test_controlled_copy_shield_requires_exact_descriptor_and_authority(self):
        source = self.db.lookup(self.copier)
        spec = entry_copy_spec(source.oracle_text, source_name=source.name)
        self.assertEqual(7, spec.schema_version)
        self.assertEqual(spec, EntryCopySpec.from_dict(spec.to_dict()))
        for changed in ({"controlled_shield": False}, {"controlled_shield": 1}, {"schema_version": 1},
                        {"selection": {**spec.selection.to_dict(), "controller": "$controller"}}):
            with self.assertRaises(ValueError):
                EntryCopySpec.from_dict({**spec.to_dict(), **changed})
        def compile(row, registry):
            return compile_oracle_card(row, capability_registry=registry, capability_profile="commander_review")
        self.assertEqual("exact", compile(source, self.registry).status)
        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        cap = next(r for r in raw["capabilities"] if r["id"] == CONTROLLED_COPY_SHIELD_CAPABILITY)
        cap.update(status="blocked", blockers=["Controlled copy shield withdrawn."])
        withdrawn = CapabilityRegistry(raw)
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        index = build_index(registry_value=raw, declaration_source=declarations,
                            discovered_test_ids=set(discover_tests(ROOT).values()))
        withdrawn.mark_evidence_verified(validate_capability_evidence_index(index, registry=withdrawn))
        self.assertNotEqual("exact", compile(source, withdrawn).status)
        plain = replace(source, oracle_text=source.oracle_text.split(", except")[0] + ".")
        self.assertEqual("exact", compile(plain, withdrawn).status)
        with patch("quorune.compiler.entry_copy_templates.entry_copy_spec", return_value=None):
            self.assertNotEqual("exact", compile(source, self.registry).status)
