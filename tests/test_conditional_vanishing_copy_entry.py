"""Native evidence for a retained conditional copy of the Vanishing lifecycle."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
import test_legendary_snow_copy_entry as copy_witnesses
import test_vanishing_rules as vanishing_witnesses
from quorune.carddb import CardDatabase
from quorune.continuous_effect_model import ContinuousOperation, Layer
from quorune.continuous_effect_state import (
    ResolutionContinuousComponent, create_resolution_continuous_effect_components, resolution_effect_source,
)
from quorune.deck import DeckDefinition, DeckEntry
from quorune.entry_copy import EntryCopySpec, CONDITIONAL_VANISHING_COPY_CAPABILITY
from quorune.keyword_abilities import normalized_characteristic_keywords
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.evidence import validate_capability_evidence_index
from quorune.rules.evidence_storage import read_evidence_document
from quorune.token_copy_recipes import TokenCopyRecipeSpec
from scripts.build_test_database import build_fixture_database
from scripts.update_capability_evidence import build_index, discover_tests


class ConditionalVanishingCopyEntryTests(InheritedNativeFixture, unittest.TestCase):
    copier = "Trial Perishable Echo"
    ordinary = "Trial Clockless Scout"
    printed = "Calciderm"
    echo = "Trial Second Echo"
    copy_mana = {"U": 2}
    echo_mana = {"C": 1, "U": 1}
    _offer = copy_witnesses.LegendarySnowCopyEntryTests._offer
    _choose = copy_witnesses.LegendarySnowCopyEntryTests._choose

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "conditional-vanishing-copy.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([
            fixtures / "bound-effect-program-cards.json",
            fixtures / "conditional-vanishing-copy-entry-cards.json",
            fixtures / "legendary-snow-copy-entry-cards.json",
            fixtures / "vanishing-cards.json",
        ], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        commander = "Generic Bound Commander"
        cls.deck = DeckDefinition("Conditional copy Vanishing", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], [commander])

    def _enter_copy(self, game, options, target):
        pending = self._choose(game, options, target)
        for _ in range(3):
            if pending is None:
                return
            self.assertEqual(("replacement.order", ["A"]), (pending.kind, pending.actors))
            automatic = game.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
            choice = next(row for row in automatic if not row.get("decline"))
            result = game.act("pilot:A", {"action_id": "choose", "replacement": choice["id"]})
            self.assertTrue(result.ok, result.summary)
            pending = self.resolve(game)
        self.assertIsNone(pending)

    def _assert_keyword(self, game, card):
        self.assertIn("vanishing", normalized_characteristic_keywords(game.engine._effective_card_data(card)))

    def _sacrifice_on_last_counter(self, game, source):
        current = game.state.cards[source.object_id]
        current.counters["time"] = 1
        vanishing_witnesses.VanishingRuntimeTests.upkeep(self, game)
        self.checkpoint(game)
        self.assertEqual(1, len(game.state.stack))
        vanishing_witnesses.VanishingRuntimeTests.resolve_top(self, game)
        self.assertEqual("battlefield", current.zone)
        self.assertEqual(0, current.counters.get("time", 0))
        self.assertEqual(1, len(game.state.stack))
        self.assertTrue(vanishing_witnesses.VanishingRuntimeTests.is_sacrifice(self, game.engine, game.state.stack[-1]))
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        return self.replay(game, load=True)

    def test_ordinary_copy_gets_three_and_separate_upkeep_and_sacrifice_triggers(self):
        game = self.session(707_960)
        source = self.add(game.engine, self.copier, zone="hand")
        target = self.add(game.engine, self.ordinary, seat="B", ref="ordinary-public-source")
        target.owner = "A"
        hidden = self.add(game.engine, self.ordinary, seat="D", zone="hand", ref="private-copy-trial")
        self.prepare(game, mana=self.copy_mana)
        self.checkpoint(game)
        options = self._offer(game, source)
        self.assertNotIn(hidden.ref, json.dumps(options))
        self.assertNotIn(hidden.ref, json.dumps(game.packet("pilot:C", full=True)))
        game = self.replay(game, load=True)
        self._enter_copy(game, options, target)
        copy = game.state.cards[source.object_id]
        self.assertEqual("battlefield", copy.zone)
        self.assertEqual(self.ordinary, game.engine.display_name(copy.object_id))
        self.assertEqual(3, copy.counters.get("time"))
        self.assertFalse(game.state.cards[target.object_id].counters)
        self._assert_keyword(game, copy)
        vanishing_witnesses.VanishingRuntimeTests.upkeep(self, game)
        self.checkpoint(game)
        self.assertEqual(1, len(game.state.stack))
        vanishing_witnesses.VanishingRuntimeTests.resolve_top(self, game)
        self.assertEqual(2, copy.counters.get("time"))
        self.assertFalse(game.state.stack)
        after = self._sacrifice_on_last_counter(game, copy)
        self.assertEqual("battlefield", after.state.cards[target.object_id].zone)

    def test_printed_vanishing_is_retained_without_a_second_grant(self):
        game = self.session(707_961)
        source = self.add(game.engine, self.copier, zone="hand")
        target = self.add(game.engine, self.printed, seat="B")
        target.counters["time"] = 1
        self.assert_closed(game, target)
        self.prepare(game, mana=self.copy_mana)
        self.checkpoint(game)
        self._enter_copy(game, self._offer(game, source), target)
        copy = game.state.cards[source.object_id]
        self.assertEqual(4, copy.counters.get("time"))
        self.assertEqual(1, game.state.cards[target.object_id].counters["time"])
        self._assert_keyword(game, copy)
        self._sacrifice_on_last_counter(game, copy)

    def test_copy_of_copy_inherits_the_typed_grant_exactly_once(self):
        game = self.session(707_962)
        source = self.add(game.engine, self.copier, zone="hand")
        second = self.add(game.engine, self.echo, zone="hand")
        native = self.add(game.engine, self.ordinary, seat="B")
        mana = {name: self.copy_mana.get(name, 0) + self.echo_mana.get(name, 0)
                for name in self.copy_mana.keys() | self.echo_mana.keys()}
        self.prepare(game, mana=mana)
        self.checkpoint(game)
        self._enter_copy(game, self._offer(game, source), native)
        first = game.state.cards[source.object_id]
        self.assertEqual(3, first.counters.get("time"))
        self._enter_copy(game, self._offer(game, second), first)
        after = game.state.cards[second.object_id]
        self.assertEqual((3, 3), (first.counters.get("time"), after.counters.get("time")))
        self._assert_keyword(game, after)
        receiver = game.engine._copyable_characteristics(after)
        self.assertEqual(1, sum(b["kind"] == "fixed_keyword_entry_counter" for b in receiver["ability_fragments"]))
        self.assertEqual(2, sum(b["kind"] == "granted_triggered" for b in receiver["ability_fragments"]))
        self.replay(game, load=True)

    def test_current_keyword_check_keeps_printed_values_when_the_source_loses_abilities(self):
        game = self.session(707_963)
        source = self.add(game.engine, self.copier, zone="hand")
        target = self.add(game.engine, self.printed, seat="B")
        target.counters["time"] = 1
        create_resolution_continuous_effect_components(
            game.engine, source=resolution_effect_source(game.engine, {}, fallback_card=target), targets=(target,),
            components=(ResolutionContinuousComponent(
                Layer.ABILITY, "6", (ContinuousOperation("remove_all_abilities"),),
            ),),
        )
        self.assertNotIn("vanishing", normalized_characteristic_keywords(game.engine._effective_card_data(target)))
        self.prepare(game, mana=self.copy_mana)
        self.checkpoint(game)
        self._enter_copy(game, self._offer(game, source), target)
        copy = game.state.cards[source.object_id]
        self.assertEqual(7, copy.counters.get("time"))
        self.assertEqual(1, game.state.cards[target.object_id].counters["time"])
        self._assert_keyword(game, copy)
        self.replay(game, load=True)

    def test_decline_leaves_the_original_without_the_copy_lifecycle(self):
        game = self.session(707_964)
        source = self.add(game.engine, self.copier, zone="hand")
        self.add(game.engine, self.ordinary)
        self.prepare(game, mana=self.copy_mana)
        self.checkpoint(game)
        self._enter_copy(game, self._offer(game, source), None)
        live = game.state.cards[source.object_id]
        self.assertEqual("graveyard", live.zone)
        self.assertFalse(live.counters)
        self.assertNotIn("copy_overrides", live.annotations)
        self.replay(game, load=True)

    def test_face_down_copy_gains_vanishing_from_public_values_only(self):
        from quorune.morph import MORPH_FACE_DOWN_ANNOTATION, face_down_characteristics

        game = self.session(707_965)
        source = self.add(game.engine, self.copier, zone="hand")
        target = self.add(game.engine, self.printed, seat="B", ref="anonymous-copy-source")
        target.face_down = True
        target.known_to = ["B"]
        target.revealed_to = []
        target.annotations[MORPH_FACE_DOWN_ANNOTATION] = face_down_characteristics("morph")
        self.prepare(game, mana=self.copy_mana)
        self.checkpoint(game)
        self.assertFalse(self.printed in json.dumps(game.packet("pilot:C", full=True)),
                         "The face-down fixture introduced a previously known identity")
        options = self._offer(game, source)
        self.assertFalse(self.printed in json.dumps(game.packet("pilot:C", full=True)),
                         "The anonymous choice exposed the printed identity")
        game = self.replay(game, load=True)
        self._enter_copy(game, options, target)
        resulting = game.state.cards[source.object_id]
        self.assertEqual("battlefield", resulting.zone)
        self.assertFalse(resulting.face_down)
        self.assertEqual(2, game.engine._numeric_stat(resulting.object_id, "power"))
        self.assertEqual(2, game.engine._numeric_stat(resulting.object_id, "toughness"))
        self.assertEqual(3, resulting.counters.get("time"))
        data = game.engine._effective_card_data(resulting)
        self.assertEqual({"vanishing"}, normalized_characteristic_keywords(data))
        from quorune.entry_copy import copiable_oracle_id

        self.assertEqual("custom-copy:face-down", copiable_oracle_id(resulting))
        self.assertFalse(self.printed in json.dumps(game.packet("pilot:C", full=True)),
                         "The anonymous copy exposed the printed identity")
        self.replay(game, load=True)

    def test_closed_shape_authority_and_two_distinct_grants_keep_ordinary_copies(self):
        record = self.db.lookup(self.copier)

        def compile_card(card, registry):
            return compile_oracle_card(card, capability_registry=registry, capability_profile="commander_review")

        ir = compile_card(record, self.registry)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        nodes = ir.faces[0].nodes
        self.assertEqual({"step.begin", "counter.removed"},
                         {n.event for n in nodes if n.kind == "granted_triggered_ability"})
        descriptor, = [h for n in nodes for h in n.handlers if h["handler_id"] == "replacement.zone.entry-copy.v11"]
        spec = EntryCopySpec.from_dict(descriptor["copy"])
        self.assertEqual(spec, EntryCopySpec.from_dict(spec.to_dict()))
        raw_copy = spec.to_dict()
        for changed in (
            {"schema_version": 1},
            {"selection": {**raw_copy["selection"], "types_all": []}},
            {"exception": {**raw_copy["exception"], "add_ability_fragments": raw_copy["exception"]["add_ability_fragments"][:2]}},
        ):
            with self.assertRaises(ValueError):
                EntryCopySpec.from_dict({**raw_copy, **changed})
        with self.assertRaises(ValueError):
            TokenCopyRecipeSpec(origin="target", exception=spec.exception)

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        cap = next(row for row in raw["capabilities"] if row["id"] == CONDITIONAL_VANISHING_COPY_CAPABILITY)
        cap.update(status="blocked", blockers=["Conditional copy authority withdrawn."])
        withdrawn = CapabilityRegistry(raw)
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        index = build_index(registry_value=raw, declaration_source=declarations,
                            discovered_test_ids=set(discover_tests(ROOT).values()))
        withdrawn.mark_evidence_verified(validate_capability_evidence_index(index, registry=withdrawn))
        self.assertNotEqual("exact", compile_card(record, withdrawn).status)
        for name in (self.echo, self.printed):
            self.assertEqual("exact", compile_card(self.db.lookup(name), withdrawn).status)
        with patch("quorune.compiler.entry_copy_ability_nodes._conditional_vanishing_copy_nodes", return_value=None):
            self.assertNotEqual("exact", compile_card(record, self.registry).status)
