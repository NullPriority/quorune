"""Door of Destinies through the ordinary chosen type and source-counter owners."""

from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.descriptor_requirements import typed_result_amount_runtime_dependencies
from quorune.carddb import CardDatabase
from quorune.compiler.entry_designation_templates import chosen_characteristics_handler
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.replacement.immutable import FrozenMap
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantic_runtime.chosen_characteristics import (
    CHOSEN_SOURCE_COUNTER_ANTHEM_CAPABILITY, SOURCE_COUNTER_ANTHEM_HANDLER,
    ChosenCharacteristicsHandler, chosen_source_counter_anthem_dependencies,
)
from quorune.semantic_runtime.continuous_components import ContinuousEffectSourceContext
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card
import test_chosen_type_events as existing_choice


ORIGINAL = "Door of Destinies"
PROFILE = "commander_review"
FIXTURES = ROOT / "tests" / "fixtures"


class ChosenTypeSourceCounterAnthemTests(InheritedNativeFixture, unittest.TestCase):
    entry_type = existing_choice.ChosenTypeEventNativeTests.entry_type
    reject = existing_choice.ChosenTypeEventNativeTests.reject

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        support = {
            "chosen-type-event-cards.json": {ORIGINAL, "Vanquisher's Banner", "Tarfire", "Raging Goblin"},
            "creature-subtype-addition-cards.json": {"Goblin Piker"},
            "attached-destruction-set-cards.json": {"Plains", "Island"},
            "bound-effect-program-cards.json": {"Generic Bound Plains", "Generic Bound Growth", "Disenchant"},
            "source-attack-alone-cards.json": {"Isamaru, Hound of Konda"},
        }
        parts = []
        for name, names in support.items():
            data = json.loads((FIXTURES / name).read_bytes())
            data["cards"] = [card for card in data["cards"] if card["name"] in names]
            assert names == {card["name"] for card in data["cards"]}, name
            ids = {card["oracle_id"] for card in data["cards"]}
            data["rulings"] = [ruling for ruling in data.get("rulings", ()) if ruling["oracle_id"] in ids]
            part = folder / name
            part.write_text(json.dumps(data), encoding="utf-8")
            parts.append(part)
        path = folder / "chosen-type-source-counter-anthem.sqlite3"
        build_fixture_database(parts, path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Source counter chosen type", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def _compiled(self):
        card = self.db.lookup(ORIGINAL, fuzzy=False)
        return card, compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)

    def _stats(self, game, card):
        return tuple(game.engine._numeric_stat(card.object_id, stat) for stat in ("power", "toughness"))

    def test_combined_wire_delegates_the_live_named_source_counter_to_the_chosen_query(self):
        _, descriptor, dependencies = chosen_characteristics_handler(
            "Creatures you control of the chosen type get +1/+1 for each charge counter on this artifact."
        )
        self.assertEqual(SOURCE_COUNTER_ANTHEM_HANDLER, descriptor["body"]["handler_id"])
        self.assertIn(CHOSEN_SOURCE_COUNTER_ANTHEM_CAPABILITY, dependencies)
        self.assertEqual((CHOSEN_SOURCE_COUNTER_ANTHEM_CAPABILITY,), chosen_source_counter_anthem_dependencies(descriptor))
        self.assertIn(CHOSEN_SOURCE_COUNTER_ANTHEM_CAPABILITY, typed_result_amount_runtime_dependencies(descriptor))
        handler = ChosenCharacteristicsHandler()
        context = ContinuousEffectSourceContext(
            source_object_id="artifact", source_ref="artifact-ref", source_controller="B",
            source_timestamp=10, component_id="anthem",
            source_designations=FrozenMap({"chosen_creature_type": "goblin"}),
            source_counters=FrozenMap({"charge": 2, "+1/+1": 5}),
        )
        effect, = handler.lower(descriptor, context)
        self.assertEqual(("B", ("goblin",)), (effect.applies.controller, effect.applies.subtypes_all))
        self.assertEqual((2, 2), tuple(effect.operations[0].value))
        for bad in (
            dict(descriptor, designation="color"),
            dict(descriptor, body={**descriptor["body"], "modifier": {
                **descriptor["body"]["modifier"], "power": 2,
            }}),
            dict(descriptor, body={**descriptor["body"], "condition": {
                **descriptor["body"]["condition"], "target_controller": "any",
            }}),
        ):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                chosen_source_counter_anthem_dependencies(bad)
        old = chosen_characteristics_handler("Creatures you control of the chosen type get +1/+1.")[1]
        self.assertEqual((), chosen_source_counter_anthem_dependencies(old))
        prior, = handler.lower(old, context)
        self.assertEqual((1, 1), tuple(prior.operations[0].value))

    def test_complete_original_keeps_public_entry_cast_counter_and_live_stat_components(self):
        card, ir = self._compiled()
        self.assertEqual("exact", ir.status, ir.material_residuals)
        nodes = ir.faces[0].nodes
        self.assertEqual(3, len(nodes))
        required = set(cap for node in nodes for cap in node.capability_dependencies)
        self.assertLessEqual({"zone.entry.public_designation", "trigger.event.chosen_creature_type",
                              CHOSEN_SOURCE_COUNTER_ANTHEM_CAPABILITY}, required)
        bound, semantics = register_card(self.db, self.registry, ORIGINAL)
        self.assertTrue(bound["strict_capability_ready"], bound["blockers"])
        self.assertEqual("capability_closed", bound["trust_basis"])
        self.assertEqual(3, len(tuple(semantics.programs_for_oracle(card.oracle_id))))

    def test_paid_door_counts_matching_creature_and_kindred_casts_in_time_for_entry_and_replay(self):
        game = self.session(6502170)
        door = self.add(game.engine, ORIGINAL, zone="hand")
        selected = self.add(game.engine, "Goblin Piker")
        foreign = self.add(game.engine, "Goblin Piker", seat="B", ref="door-foreign-goblin")
        entering = self.add(game.engine, "Raging Goblin", zone="hand")
        kindred = self.add(game.engine, "Tarfire", zone="hand")
        removal = self.add(game.engine, "Disenchant", seat="B", zone="hand")
        self.add(game.engine, "Plains", seat="B")
        self.add(game.engine, "Island", seat="B")
        private = self.add(game.engine, "Generic Bound Growth", seat="D", zone="hand", ref="door-private")
        for card in (door, selected, entering, kindred, removal):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 4, "R": 2})
        self.checkpoint(game)

        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        offer, = [action for action in actions if action["id"] == "cast:" + door.ref]
        receipt = game.act("pilot:A", {"action_id": offer["id"], "pay": "manual", "payment": {"C": 4}})
        self.assertTrue(receipt.ok, receipt.summary)
        self.assertEqual(2, game.state.players["A"].mana_pool["R"])
        self.assertEqual("replacement.order", self.resolve(game).kind)
        game = self.replay(game, load=True)
        self.entry_type(game, "A", "goblin")
        self.assertIsNone(self.resolve(game))
        self.assertEqual("goblin", game.state.cards[door.object_id].annotations["chosen_creature_type"])
        self.assertEqual((2, 1), self._stats(game, selected))

        self.cast(game, "A", entering)
        self.assertEqual(door.object_id, game.state.stack[-1].source_object_id)
        self.resolve_top(game)
        self.assertEqual(1, game.state.cards[door.object_id].counters["charge"])
        self.assertEqual("stack", game.state.cards[entering.object_id].zone)
        self.assertEqual((3, 2), self._stats(game, selected))
        observed = []
        dispatch = game.engine._dispatch_semantic_event

        def observe(event, context, **fields):
            if event == "creature.enter" and context.get("card") == game.state.cards[entering.object_id].ref:
                observed.append(self._stats(game, entering))
            return dispatch(event, context, **fields)

        with patch.object(game.engine, "_dispatch_semantic_event", side_effect=observe):
            self.assertIsNone(self.resolve(game))
        self.assertEqual([(2, 2)], observed)
        self.assertEqual((2, 2), self._stats(game, entering))

        self.cast(game, "A", kindred, ["C"])
        self.assertEqual(door.object_id, game.state.stack[-1].source_object_id)
        self.resolve_top(game)
        self.assertEqual(2, game.state.cards[door.object_id].counters["charge"])
        self.assertEqual((4, 3), self._stats(game, selected))
        self.assertEqual((3, 3), self._stats(game, entering))
        self.assertEqual((2, 1), self._stats(game, foreign))
        self.assertIsNone(self.resolve(game))
        self.assertEqual(0, sum(game.state.players["A"].mana_pool.values()))
        self.cast(game, "B", removal, [door.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[door.object_id].zone)
        self.assertEqual((2, 1), self._stats(game, selected))
        self.assertEqual((1, 1), self._stats(game, entering))
        for principal in ("pilot:A", "pilot:B", "pilot:C", "spectator"):
            self.assertNotIn(private.ref, json.dumps(game.packet(principal, full=True)))
        self.replay(game, load=True)

    def test_omission_restores_only_the_material_static_result(self):
        with patch("quorune.compiler.source_counter_anthems.query_characteristic_quantity", return_value=None):
            _, ir = self._compiled()
            residual, = ir.material_residuals
            self.assertEqual("static_ability", residual.kind)
            self.assertIn("charge counter", residual.text)
            deps = {cap for node in ir.faces[0].nodes for cap in node.capability_dependencies}
            self.assertLessEqual({"zone.entry.public_designation", "trigger.event.chosen_creature_type"}, deps)
            bound, _ = register_card(self.db, self.registry, ORIGINAL)
            self.assertFalse(bound["strict_capability_ready"], bound)

    def test_genuine_chosen_source_counter_authority_withdrawal_retains_unscaled_banner(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        known = set(discover_tests(ROOT).values())
        document = deepcopy(raw)
        next(row for row in document["capabilities"] if row["id"] == CHOSEN_SOURCE_COUNTER_ANTHEM_CAPABILITY).update(
            status="blocked", blockers=["Scoped chosen source counter anthem authority withdrawal"])
        registry = CapabilityRegistry(document)
        index = build_index(registry_value=document, declaration_source=declarations, discovered_test_ids=known)
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        closure = registry.closure((CHOSEN_SOURCE_COUNTER_ANTHEM_CAPABILITY,), profile=PROFILE)
        self.assertIn("status:" + CHOSEN_SOURCE_COUNTER_ANTHEM_CAPABILITY + ":blocked", closure.blockers)
        self.assertFalse(any("unverified" in blocker for blocker in closure.blockers))
        report, _ = register_card(self.db, registry, ORIGINAL)
        self.assertFalse(report["strict_capability_ready"], report)
        control, _ = register_card(self.db, registry, "Vanquisher's Banner")
        self.assertTrue(control["strict_capability_ready"], control["blockers"])


if __name__ == "__main__":
    unittest.main()
