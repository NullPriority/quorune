"""Native evidence for legendary snow copy entries and their retained values."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.compiler.entry_copy_templates import _legendary_snow_copy_entry_spec
from quorune.continuous_effect_model import ContinuousOperation, Layer
from quorune.continuous_effect_state import (
    ResolutionContinuousComponent, create_resolution_continuous_effect_components, resolution_effect_source,
)
from quorune.deck import DeckDefinition, DeckEntry
from quorune.entry_copy import EntryCopySpec, LEGENDARY_SNOW_COPY_CAPABILITY
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.evidence import validate_capability_evidence_index
from quorune.rules.evidence_storage import read_evidence_document
from quorune.token_copy_recipes import TokenCopyRecipeSpec
from scripts.build_test_database import build_fixture_database
from scripts.update_capability_evidence import build_index, discover_tests


class LegendarySnowCopyEntryTests(InheritedNativeFixture, unittest.TestCase):
    copier = "Trial Frost Echo"
    creature = "Trial Vale Surveyor"
    legendary = "Trial Legendary Scout"
    land = "Trial Snow Meadow"
    echo = "Trial Second Echo"
    copy_mana = {"C": 1, "U": 1}
    echo_mana = {"C": 1, "U": 1}
    doubler = "Doubling Season"

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        fixtures = ROOT / "tests/fixtures"
        document = json.loads((fixtures / "counter-replacement-cards.json").read_bytes())
        document["cards"] = [row for row in document["cards"] if row["name"] == cls.doubler]
        oid, = (row["oracle_id"] for row in document["cards"])
        document["rulings"] = [row for row in document.get("rulings", []) if row["oracle_id"] == oid]
        doubler = folder / "counter-replacement.json"
        doubler.write_text(json.dumps(document), encoding="utf-8")
        database = folder / "legendary-snow-copy-entry.sqlite3"
        build_fixture_database([
            fixtures / "bound-effect-program-cards.json",
            fixtures / "legendary-snow-copy-entry-cards.json",
            fixtures / "type-directed-copy-entry-cards.json",
            doubler,
        ], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Legendary snow copy entry", [
            DeckEntry("Generic Bound Commander", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def _offer(self, game, source):
        self.assert_closed(game, source)
        self.cast(game, "A", source)
        pending = self.resolve(game)
        self.assertEqual(("replacement.order", ["A"]), (pending.kind, pending.actors))
        return game.packet("pilot:A", full=True)["decision"]["ctx"]["options"]

    def _choose(self, game, options, target=None):
        if target is None:
            choice, = [row for row in options if row.get("decline")]
        else:
            choice, = [row for row in options if not row.get("decline") and row["id"].endswith(":" + target.ref)]
        command = {"action_id": "choose", "replacement": choice["id"]}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:D", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        return self.resolve(game)

    def _keep_legend(self, game, keep):
        before = authoritative_state_hash(game.state)
        command = {"action_id": "choose", "card": keep.ref}
        self.assertFalse(game.act("pilot:D", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))

    def _assert_changeling_copy(self, game, card):
        types, subtypes, supertypes = game.engine._effective_type_parts(card)
        self.assertIn("creature", types)
        self.assertTrue({"legendary", "snow"}.issubset(supertypes), supertypes)
        self.assertTrue({"elf", "dragon", "shapeshifter"}.issubset(subtypes))
        keywords = game.engine._effective_card_data(card)["keywords"]
        self.assertEqual(1, sum(str(word).casefold() == "changeling" for word in keywords))

    def test_controlled_creature_copy_retains_changeling_and_the_copy_of_copy_uses_new_values(self):
        game = self.session(707_940)
        source = self.add(game.engine, self.copier, zone="hand")
        echo = self.add(game.engine, self.echo, zone="hand")
        original = self.add(game.engine, self.creature, ref="controlled-echo-source")
        original.owner = "B"
        original.counters["+1/+1"] = 7
        opposed = self.add(game.engine, self.creature, seat="B", ref="opposing-echo-source")
        opposed.owner = "A"
        hidden = self.add(game.engine, self.creature, seat="D", zone="hand", ref="hidden-echo-source")
        self.assert_closed(game, original)
        mana = {kind: self.copy_mana.get(kind, 0) + self.echo_mana.get(kind, 0)
                for kind in self.copy_mana.keys() | self.echo_mana.keys()}
        self.prepare(game, mana=mana)
        self.checkpoint(game)
        options = self._offer(game, source)
        self.assertNotIn(opposed.ref, json.dumps(options))
        self.assertNotIn(hidden.ref, json.dumps(options))
        game = self.replay(game, load=True)
        self.assertIsNone(self._choose(game, options, original))
        first = game.state.cards[source.object_id]
        self.assertEqual("battlefield", first.zone)
        self.assertEqual(2, first.counters.get("+1/+1"))
        self.assertEqual(7, game.state.cards[original.object_id].counters["+1/+1"])
        self._assert_changeling_copy(game, first)
        options = self._offer(game, game.state.cards[echo.object_id])
        pending = self._choose(game, options, first)
        self.assertEqual(("state.legend", ["A"]), (pending.kind, pending.actors))
        game = self.replay(game, load=True)
        second = game.state.cards[echo.object_id]
        self._keep_legend(game, second)
        self.assertEqual("battlefield", second.zone)
        self.assertEqual(self.creature, game.engine.display_name(second.object_id))
        self.assertEqual("graveyard", game.state.cards[first.object_id].zone)
        self.assertEqual("battlefield", game.state.cards[original.object_id].zone)
        self._assert_changeling_copy(game, second)
        self.assertFalse(second.counters)
        self.replay(game, load=True)

    def test_animated_snow_land_keeps_basic_without_inheriting_animation_or_changeling(self):
        game = self.session(707_941)
        source = self.add(game.engine, self.copier, zone="hand")
        land = self.add(game.engine, self.land)
        create_resolution_continuous_effect_components(
            game.engine, source=resolution_effect_source(game.engine, {}, fallback_card=land), targets=(land,),
            components=(
                ResolutionContinuousComponent(
                    Layer.TYPE, "4", (ContinuousOperation("add_types", ["Creature"], field="card_types"),),
                ),
                ResolutionContinuousComponent(
                    Layer.POWER_TOUGHNESS, "7b", (ContinuousOperation("set_power_toughness", [2, 2]),),
                ),
            ),
        )
        self.assertIn("creature", game.engine._effective_type_parts(land)[0])
        self.assertEqual(2, game.engine._numeric_stat(land.object_id, "toughness"))
        self.prepare(game, mana=self.copy_mana)
        self.checkpoint(game)
        self.assertIsNone(self._choose(game, self._offer(game, source), land))
        copy = game.state.cards[source.object_id]
        self.assertEqual("battlefield", copy.zone)
        types, subtypes, supertypes = game.engine._effective_type_parts(copy)
        self.assertEqual({"land"}, types)
        self.assertIn("forest", subtypes)
        self.assertEqual({"basic", "legendary", "snow"}, supertypes)
        self.assertFalse(copy.counters)
        self.assertNotIn("changeling", [word.casefold() for word in game.engine._effective_card_data(copy)["keywords"]])
        self.replay(game, load=True)

    def test_additional_counters_use_the_native_counter_replacement_and_legend_choice(self):
        game = self.session(707_942)
        source = self.add(game.engine, self.copier, zone="hand")
        target = self.add(game.engine, self.legendary)
        doubler = self.add(game.engine, self.doubler)
        self.assert_closed(game, doubler)
        self.prepare(game, mana=self.copy_mana)
        self.checkpoint(game)
        pending = self._choose(game, self._offer(game, source), target)
        self.assertEqual(("state.legend", ["A"]), (pending.kind, pending.actors))
        game = self.replay(game, load=True)
        self._keep_legend(game, game.state.cards[source.object_id])
        copy = game.state.cards[source.object_id]
        self.assertEqual("graveyard", game.state.cards[target.object_id].zone)
        self.assertEqual(4, copy.counters.get("+1/+1"))
        self._assert_changeling_copy(game, copy)
        self.replay(game, load=True)

    def test_declining_the_copy_does_not_grant_entry_counters(self):
        game = self.session(707_943)
        source = self.add(game.engine, self.copier, zone="hand")
        self.add(game.engine, self.creature)
        self.prepare(game, mana=self.copy_mana)
        self.checkpoint(game)
        self.assertIsNone(self._choose(game, self._offer(game, source)))
        live = game.state.cards[source.object_id]
        self.assertEqual("graveyard", live.zone)
        self.assertFalse(live.counters)
        self.assertNotIn("copy_overrides", live.annotations)
        self.replay(game, load=True)

    def test_closed_legendary_snow_copy_shape_and_authority_keep_ordinary_copies(self):
        record = self.db.lookup(self.copier)
        line = record.oracle_text.splitlines()[-1]
        spec = _legendary_snow_copy_entry_spec(line, source_name=record.name)
        self.assertEqual(10, spec.schema_version)
        self.assertEqual(spec, EntryCopySpec.from_dict(spec.to_dict()))
        for delta in (
            {"schema_version": 1},
            {"selection": {**spec.selection.to_dict(), "controller": None}},
            {"exception": {**spec.exception.to_dict(), "add_supertypes": ["snow"]}},
            {"exception": {**spec.exception.to_dict(), "remove_legendary": True}},
            {"exception": {**spec.exception.to_dict(), "add_card_types": ["artifact"]}},
        ):
            with self.assertRaises(ValueError):
                EntryCopySpec.from_dict({**spec.to_dict(), **delta})
        with self.assertRaises(ValueError):
            TokenCopyRecipeSpec(origin="target", exception=spec.exception)

        def compile_card(card, registry):
            return compile_oracle_card(card, capability_registry=registry, capability_profile="commander_review")

        self.assertEqual("exact", compile_card(record, self.registry).status)
        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        cap = next(row for row in raw["capabilities"] if row["id"] == LEGENDARY_SNOW_COPY_CAPABILITY)
        cap.update(status="blocked", blockers=["Legendary snow copy entry withdrawn."])
        withdrawn = CapabilityRegistry(raw)
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        index = build_index(registry_value=raw, declaration_source=declarations,
                            discovered_test_ids=set(discover_tests(ROOT).values()))
        withdrawn.mark_evidence_verified(validate_capability_evidence_index(index, registry=withdrawn))
        self.assertNotEqual("exact", compile_card(record, withdrawn).status)
        for name in (self.echo, "Trial Divergent Mimic"):
            self.assertEqual("exact", compile_card(self.db.lookup(name), withdrawn).status)
        with patch("quorune.compiler.entry_copy_templates._legendary_snow_copy_entry_spec", return_value=None):
            self.assertNotEqual("exact", compile_card(record, self.registry).status)
