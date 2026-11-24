"""Icon of Ancestry retains its chosen type through private top-three selection."""

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
from quorune.compiler import fixed_library_selection_templates
from quorune.deck import DeckDefinition, DeckEntry
from quorune.designation_prevention import (
    DESIGNATION_INFORMATION_FIELD, UNAVAILABLE_DESIGNATION_KIND, chosen_type_library_predicate,
)
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.library_selection_capability_shapes import (
    CHOSEN_TYPE_LIBRARY_SELECTION_CAPABILITY, chosen_type_library_selection_effect,
    fixed_library_selection_node_capabilities, validate_chosen_type_library_selection,
)
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card
import test_chosen_type_events as existing_choice


ORIGINAL = "Icon of Ancestry"
PROFILE = "commander_review"
FIXTURES = ROOT / "tests" / "fixtures"


class ChosenTypeLibrarySelectionTests(InheritedNativeFixture, unittest.TestCase):
    entry_type = existing_choice.ChosenTypeEventNativeTests.entry_type
    reject = existing_choice.ChosenTypeEventNativeTests.reject

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        support = {
            "attached-destruction-set-cards.json": {"Forest", "Grizzly Bears", "Island", "Plains"},
            "characteristic-query-union-cards.json": {"Universal Automaton"},
            "creature-subtype-addition-cards.json": {"Boggart Shenanigans", "Goblin Piker"},
            "bound-effect-program-cards.json": {"Generic Bound Plains", "Generic Bound Growth", "Disenchant"},
            "library-selection-predicate-cards.json": {"Relentless Pursuit"},
            "source-attack-alone-cards.json": {"Isamaru, Hound of Konda"},
        }
        parts = [FIXTURES / "chosen-type-library-selection-cards.json"]
        for name, names in support.items():
            data = json.loads((FIXTURES / name).read_bytes())
            data["cards"] = [card for card in data["cards"] if card["name"] in names]
            assert names == {card["name"] for card in data["cards"]}, name
            ids = {card["oracle_id"] for card in data["cards"]}
            data["rulings"] = [ruling for ruling in data.get("rulings", ()) if ruling["oracle_id"] in ids]
            part = folder / name
            part.write_text(json.dumps(data), encoding="utf-8")
            parts.append(part)
        path = folder / "chosen-type-library-selection.sqlite3"
        build_fixture_database(parts, path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Chosen type library selection", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def _set_library(self, game, cards):
        library = game.state.players["A"].zones["library"]
        self.assertTrue({card.object_id for card in cards} <= set(library))
        for object_id in tuple(library):
            if object_id not in {card.object_id for card in cards}:
                game.engine.move_card(object_id, "outside", log=False)
        library[:] = [card.object_id for card in cards]

    def _play_and_activate(self, game, source):
        self.assert_closed(game, source)
        self.cast(game, "A", source)
        self.entry_type(game, "A", "goblin")
        self.assertIsNone(self.resolve(game))
        self.priority(game, "A")
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        activation, = [action for action in actions if action["id"].startswith(f"activate:{source.ref}:")]
        result = game.act("pilot:A", {"action_id": activation["id"], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertTrue(game.state.cards[source.object_id].tapped)
        self.assertEqual(0, sum(game.state.players["A"].mana_pool.values()))
        item = game.state.stack[-1]
        info = item.context[DESIGNATION_INFORMATION_FIELD]
        self.assertEqual(source.object_id, info["source_object_id"])
        self.assertEqual(item.context["source_logical_object_id"], info["source_logical_object_id"])
        self.assertEqual({"creature_type": "goblin"}, info["values"])
        return item

    def test_exact_library_wire_checks_source_type_actor_and_optional_private_partition(self):
        effect = chosen_type_library_selection_effect()
        self.assertIsNone(validate_chosen_type_library_selection(effect))
        cap = fixed_library_selection_node_capabilities(
            effects=(effect,), target_schema=None, mechanic_ids=("fixed-library-selection",),
        )
        self.assertIn(CHOSEN_TYPE_LIBRARY_SELECTION_CAPABILITY, cap)
        self.assertEqual(set(cap), set(typed_result_amount_runtime_dependencies(effect)))
        for bad in (dict(effect, maximum=True), dict(effect, minimum=1),
                    dict(effect, remainder_order="chosen"), dict(effect, public_reveal=True)):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_chosen_type_library_selection(bad)
        decoded = {**effect, "player": "A", "predicate_groups": [[chosen_type_library_predicate("goblin").to_dict()]]}
        self.assertEqual(chosen_type_library_predicate("goblin"), validate_chosen_type_library_selection(decoded, actor="A"))
        with self.assertRaises(ValueError):
            validate_chosen_type_library_selection(decoded, actor="B")
        unavailable = {**decoded, "predicate_groups": [[{"kind": UNAVAILABLE_DESIGNATION_KIND, "schema_version": 1}]]}
        self.assertIsNone(validate_chosen_type_library_selection(unavailable, actor="A"))

    def test_original_binds_the_entry_designation_static_bonus_and_exact_library_activation(self):
        card = self.db.lookup(ORIGINAL, fuzzy=False)
        ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        required = {cap for node in ir.faces[0].nodes for cap in node.capability_dependencies}
        self.assertLessEqual({"zone.entry.public_designation", "continuous.characteristics.chosen_designation",
                              CHOSEN_TYPE_LIBRARY_SELECTION_CAPABILITY}, required)
        report, semantics = register_card(self.db, self.registry, ORIGINAL)
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        self.assertEqual("capability_closed", report["trust_basis"])
        activation, = [program for program in semantics.programs_for_oracle(card.oracle_id)
                       if CHOSEN_TYPE_LIBRARY_SELECTION_CAPABILITY in program.capability_dependencies]
        self.assertIsNone(activation.target_schema)
        self.assertIsNone(validate_chosen_type_library_selection(activation.effects[0]))

    def test_paid_activation_after_real_departure_reveals_only_selected_actual_creature_and_replays(self):
        game = self.session(6503170)
        source = self.add(game.engine, ORIGINAL, zone="hand")
        removal = self.add(game.engine, "Disenchant", seat="B", zone="hand")
        self.add(game.engine, "Plains", seat="B")
        self.add(game.engine, "Island", seat="B")
        filler = self.add(game.engine, "Forest", zone="library", ref="icon-filler")
        goblin = self.add(game.engine, "Goblin Piker", zone="library", ref="icon-selected")
        kindred = self.add(game.engine, "Boggart Shenanigans", zone="library", ref="icon-kindred-near-miss")
        changeling = self.add(game.engine, "Universal Automaton", zone="library", ref="icon-changeling")
        private = self.add(game.engine, "Generic Bound Growth", seat="D", zone="hand", ref="icon-private")
        self._set_library(game, (filler, changeling, kindred, goblin))
        self.prepare(game, mana={"C": 6})
        self.checkpoint(game)

        self._play_and_activate(game, source)
        self.cast(game, "B", removal, [source.ref])
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["A"]), (pending.kind, pending.actors))
        game = self.replay(game, load=True)
        schema = game.state.pending_decision.payload_by_actor["A"]["legal_actions"][0]["choice_schema"]
        self.assertEqual({goblin.ref, changeling.ref}, set(schema["legal_refs"]))
        for seat in "BCD":
            packet = json.dumps(game.packet("pilot:" + seat, full=True))
            for card in (goblin, kindred, changeling):
                self.assertNotIn(card.ref, packet)
        command = {"action_id": "choose", "cards": [goblin.ref]}
        self.reject(game, "B", command)
        self.reject(game, "A", {**command, "cards": [kindred.ref]})
        self.reject(game, "A", {**command, "cards": [goblin.ref, changeling.ref]})
        accepted = game.act("pilot:A", command)
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("hand", game.state.cards[goblin.object_id].zone)
        self.assertEqual(set("ABCD"), set(game.state.cards[goblin.object_id].revealed_to))
        order = game.state.players["A"].zones["library"]
        self.assertEqual({kindred.object_id, changeling.object_id}, set(order[:2]))
        self.assertEqual(filler.object_id, order[-1])
        self.assertNotIn(private.ref, json.dumps(game.packet("pilot:A", full=True)))
        for seat in "BCD":
            packet = json.dumps(game.packet("pilot:" + seat, full=True))
            self.assertNotIn(kindred.ref, packet)
            self.assertNotIn(changeling.ref, packet)
        self.replay(game, load=True)

    def test_paid_activation_with_a_short_nonmatching_library_moves_no_cards_to_hand(self):
        game = self.session(6503180)
        source = self.add(game.engine, ORIGINAL, zone="hand")
        bear = self.add(game.engine, "Grizzly Bears", zone="library", ref="icon-short-bear")
        kindred = self.add(game.engine, "Boggart Shenanigans", zone="library", ref="icon-short-kindred")
        self._set_library(game, (bear, kindred))
        self.prepare(game, mana={"C": 6})
        self.checkpoint(game)

        self._play_and_activate(game, source)
        self.assertIsNone(self.resolve(game))
        self.assertEqual({bear.object_id, kindred.object_id}, set(game.state.players["A"].zones["library"]))
        for card in (bear, kindred):
            self.assertEqual("library", game.state.cards[card.object_id].zone)
            self.assertNotIn(card.ref, json.dumps(game.packet("pilot:B", full=True)))
        self.replay(game, load=True)

    def test_omitting_the_exact_library_result_restores_only_the_material_activation(self):
        with patch.object(fixed_library_selection_templates.ChosenTypeLibrarySelectionEffectTemplate,
                          "compiled", return_value=(None, (), None, ())):
            card = self.db.lookup(ORIGINAL, fuzzy=False)
            ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
            residual, = ir.material_residuals
            self.assertEqual("effect", residual.kind)
            self.assertIn("top three cards of your library", residual.text)
            deps = {cap for node in ir.faces[0].nodes for cap in node.capability_dependencies}
            self.assertLessEqual({"zone.entry.public_designation", "continuous.characteristics.chosen_designation"}, deps)
            report, _ = register_card(self.db, self.registry, ORIGINAL)
            self.assertFalse(report["strict_capability_ready"], report)

    def test_genuine_chosen_type_library_withdrawal_preserves_the_independent_fixed_selector(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        known = set(discover_tests(ROOT).values())
        document = deepcopy(raw)
        next(row for row in document["capabilities"] if row["id"] == CHOSEN_TYPE_LIBRARY_SELECTION_CAPABILITY).update(
            status="blocked", blockers=["Scoped source chosen creature library authority withdrawal"])
        registry = CapabilityRegistry(document)
        index = build_index(registry_value=document, declaration_source=declarations, discovered_test_ids=known)
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        closure = registry.closure((CHOSEN_TYPE_LIBRARY_SELECTION_CAPABILITY,), profile=PROFILE)
        self.assertIn("status:" + CHOSEN_TYPE_LIBRARY_SELECTION_CAPABILITY + ":blocked", closure.blockers)
        self.assertFalse(any("unverified" in blocker for blocker in closure.blockers))
        report, _ = register_card(self.db, registry, ORIGINAL)
        self.assertFalse(report["strict_capability_ready"], report)
        independent, _ = register_card(self.db, registry, "Relentless Pursuit")
        self.assertTrue(independent["strict_capability_ready"], independent["blockers"])


if __name__ == "__main__":
    unittest.main()
