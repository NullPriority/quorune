"""A paid artifact's pre-move mana value bounds one private library entry."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from test_qualified_zone_event_queries import query_record
from quorune.card_programs import bind_card_program_runtime
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.library_search_model import project_semantic_search_result_publication
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, DEFAULT_CAPABILITY_REGISTRY, load_default_capability_registry
from quorune.rules.evidence import validate_capability_evidence_index
from quorune.rules.evidence_storage import read_evidence_document
from quorune.rules.paid_artifact_library_search import (
    PAID_ARTIFACT_SEARCH_CAPABILITY, PAID_ARTIFACT_SEARCH_CONTEXT, PAID_ARTIFACT_SEARCH_TEMPLATE,
    PaidArtifactSearchReceipt, paid_artifact_search_effect, paid_artifact_search_runtime_dependencies,
)
from quorune.semantics import SemanticRegistry
from scripts.build_test_database import build_fixture_database
from scripts.update_capability_evidence import build_index, discover_tests


PROFILE = "commander_review"
ORIGINAL = "Repurposing Bay"
RULE_TEXT = (
    "{2}, {T}, Sacrifice another artifact: Search your library for an artifact card with mana value equal to 1 plus "
    "the sacrificed artifact's mana value, put that card onto the battlefield, then shuffle. Activate only as a sorcery."
)


class PaidArtifactLibrarySearchTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        fixtures = ROOT / "tests" / "fixtures"
        path = Path(cls.temporary.name) / "paid-artifact-library-search.sqlite3"
        build_fixture_database([
            fixtures / "bound-effect-program-cards.json",
            fixtures / "scryfall-exact-lists.json",
            fixtures / "ashiok-nightmare-weaver-cards.json",
            fixtures / "library-search-rules-cards.json",
            fixtures / "announced-artifact-copy-cards.json",
            fixtures / "external-entry-tapped-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Paid artifact library search", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_oswald_fiddlebender_keeps_white_artifact_price_and_bay_separate(self):
        from quorune.compiler.activated_costs import parse_compiled_activation_line

        card = self.db.lookup("Oswald Fiddlebender", fuzzy=False)
        options = {"capability_registry": self.registry, "capability_profile": PROFILE}
        ir = compile_oracle_card(card, **options)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        self.assertEqual(PAID_ARTIFACT_SEARCH_TEMPLATE, node.template_id)
        self.assertEqual((paid_artifact_search_effect(),), tuple(node.effects))
        choices, timing = parse_compiled_activation_line(
            card.oracle_text, card_name=card.name, keywords=card.keywords)
        ability, = choices
        self.assertTrue(ability.sorcery_speed and ability.tap_source)
        self.assertFalse(ability.choices[0].another)
        self.assertEqual({"W": 1}, {key: amount for key, amount in ability.mana.items() if amount})
        self.assertIsNone(timing)
        self.assertEqual(1, node.cost["mana"]["W"])
        self.assertIn("q", node.cost["choices"][0])
        for revised in (
            card.oracle_text.replace("Sacrifice an artifact", "Sacrifice another artifact"),
            card.oracle_text.replace("{W}, {T}", "{2}, {T}"),
            card.oracle_text.replace("put it", "put that card"),
            card.oracle_text.replace(" Activate only as a sorcery.", ""),
        ):
            with self.subTest(text=revised):
                self.assertNotEqual("exact", compile_oracle_card(replace(card, oracle_text=revised), **options).status)
        bay = self.db.lookup(ORIGINAL, fuzzy=False)
        bay_program = compile_best_available_card_program(
            self.db, bay, semantic_registry=SemanticRegistry(), **options)
        self.assertTrue(bind_card_program_runtime(
            bay_program, capability_registry=self.registry, profile=PROFILE)["strict_capability_ready"])
        bay_ability, = (row for row in bay_program.abilities if row.event == "activate")
        self.assertEqual((paid_artifact_search_effect(),), tuple(bay_ability.effects))
        self.assertEqual(1, bay_ability.cost_schema["choices"][0]["other"])

    def test_oswald_fiddlebender_sacrifices_its_artifact_source_and_replays_private_search(self):
        game = self.session(802026)
        game.state.config.semantic_policy = "trusted_only"
        source = self.add(game.engine, "Oswald Fiddlebender")
        coating = self.add(game.engine, "Liquimetal Coating")
        found = self.add(game.engine, "Darksteel Ingot", zone="library")
        lower = self.add(game.engine, "Ichor Wellspring", zone="library")
        self.assert_closed(game, source)
        self.assert_closed(game, coating)
        self.prepare(game, mana={"W": 1, "C": 2})
        self.checkpoint(game)

        def offered(permanent):
            packet = game.packet("pilot:A", full=True)
            return next(action for action in packet["decision"]["ctx"]["legal"]["actions"]
                        if action["id"].startswith(f"activate:{permanent.ref}:"))

        activation = offered(source)
        self.assertEqual({"W": 1}, activation["cost_summary"]["m"])
        command = {"action_id": activation["id"], "cost_objects": [source.ref], "pay": "auto"}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:A", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        coat = game.act("pilot:A", {"action_id": offered(coating)["id"], "targets": [source.ref], "pay": "auto"})
        self.assertTrue(coat.ok, coat.summary)
        self.assertIsNone(self.resolve(game))
        self.assertIn("Artifact", game.engine._effective_card_data(source)["type_line"])
        self.assertEqual(activation["id"], offered(source)["id"])

        original = game.state.cards[source.object_id].logical_object_id
        shuffles = sum(event.code == "library.shuffle" for event in game.state.events)
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        self.assertEqual(0, game.state.players["A"].mana_pool["W"])
        self.assertEqual(2, game.state.players["A"].mana_pool["C"])
        item = game.state.stack[-1]
        receipt = PaidArtifactSearchReceipt.from_dict(item.context[PAID_ARTIFACT_SEARCH_CONTEXT])
        self.assertEqual((original, original, 2),
                         (receipt.source_logical_object_id, receipt.snapshot.logical_object_id,
                          receipt.snapshot.mana_value))
        self.assertEqual("semantic.search", self.resolve(game).kind)
        candidates = game.packet("pilot:A", full=True)["decision"]["ctx"]["search_cards"]
        self.assertEqual({found.ref}, {row["id"] for row in candidates})
        for seat in "BCD":
            projection = json.dumps(game.packet(f"pilot:{seat}", full=True))
            self.assertNotIn(found.ref, projection)
            self.assertNotIn(lower.ref, projection)
        game = self.replay(game, load=True)
        result = game.act("pilot:A", {"action_id": "choose", "search_cards": [found.ref]})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(("battlefield", "A"),
                         (game.state.cards[found.object_id].zone, game.state.cards[found.object_id].controller))
        self.assertEqual(shuffles + 1, sum(event.code == "library.shuffle" for event in game.state.events))
        self.replay(game, load=True)

    def test_paid_artifact_search_compiles_only_the_complete_price_and_exact_result(self):
        record = replace(query_record(RULE_TEXT), type_line="Artifact", power=None, toughness=None)
        options = {"capability_registry": self.registry, "capability_profile": PROFILE}
        ir = compile_oracle_card(record, **options)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        ability, = ir.faces[0].nodes
        self.assertEqual(PAID_ARTIFACT_SEARCH_TEMPLATE, ability.template_id)
        self.assertEqual((paid_artifact_search_effect(),), tuple(ability.effects))
        for revised in (
            RULE_TEXT.replace("equal to", "at most"),
            RULE_TEXT.replace("another artifact", "an artifact"),
            RULE_TEXT.replace(" Activate only as a sorcery.", ""),
            RULE_TEXT.replace("onto the battlefield", "into your hand"),
        ):
            with self.subTest(text=revised):
                self.assertNotEqual("exact", compile_oracle_card(replace(record, oracle_text=revised), **options).status)
        card = self.db.lookup(ORIGINAL)
        program = compile_best_available_card_program(self.db, card, semantic_registry=SemanticRegistry(), **options)
        result = bind_card_program_runtime(program, capability_registry=self.registry, profile=PROFILE)
        self.assertTrue(result["strict_capability_ready"], result["blockers"])
        native, = (row for row in program.abilities if row.event == "activate")
        wrong = replace(native, effects=[paid_artifact_search_effect(bound=2)])
        with self.assertRaises(ValueError):
            paid_artifact_search_runtime_dependencies(wrong)

        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        known_tests = set(discover_tests(ROOT).values())
        for dependency in (PAID_ARTIFACT_SEARCH_CAPABILITY, "activation.selected_zone_change.fixed"):
            raw = json.loads(DEFAULT_CAPABILITY_REGISTRY.read_bytes())
            affected = next(row for row in raw["capabilities"] if row["id"] == dependency)
            affected.update(status="blocked", blockers=["Test-only authority withdrawal"])
            registry = CapabilityRegistry(raw)
            index = build_index(registry_value=raw, declaration_source=declarations, discovered_test_ids=known_tests)
            registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
            ir = compile_oracle_card(card, capability_registry=registry, capability_profile=PROFILE)
            with self.subTest(dependency=dependency):
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(any(dependency in blocker for row in ir.material_residuals for blocker in row.blockers))

    def scene(self, seed, *, copied=True, prohibition=False, replacement=False):
        game = self.session(seed)
        game.state.config.semantic_policy = "trusted_only"
        source = self.add(game.engine, ORIGINAL)
        paid = self.add(game.engine, "Sol Ring", seat="B")
        game.engine.change_control(paid.object_id, "A", reason="Fixture separates the payer and object owner")
        copy_model = self.add(game.engine, "Panharmonicon")
        good = self.add(game.engine, "Gilded Lotus", zone="library")
        low = self.add(game.engine, "Ichor Wellspring", zone="library")
        high = self.add(game.engine, "Mindslaver", zone="library")
        answer = self.add(game.engine, "Disenchant", seat="B", zone="hand")
        if prohibition:
            self.add(game.engine, "Mindlock Orb", seat="B")
        if replacement:
            self.add(game.engine, "Orb of Dreams", ref="bay-entry-orb")
            self.add(game.engine, "Archelos, Lagoon Mystic", seat="C", ref="bay-entry-archelos")
        if copied:
            paid.annotations["copy_overrides"] = dict(game.engine._copyable_characteristics(copy_model))
        self.assert_closed(game, source)
        self.prepare(game, mana={"C": 2})
        game.state.players["B"].mana_pool.update({"C": 1, "W": 1})
        self.checkpoint(game)
        return game, source, paid, good, low, high, answer

    def activate_bay(self, game, source, paid, expected):
        activation = f"activate:{source.ref}:ab1"
        self.assertIn(activation, {row["id"] for row in game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]})
        attempted = {"action_id": activation, "cost_objects": [source.ref], "pay": "auto"}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:A", attempted).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        chosen = {"action_id": activation, "cost_objects": [paid.ref], "pay": "auto"}
        original = game.state.cards[paid.object_id].logical_object_id
        result = game.act("pilot:A", chosen)
        self.assertTrue(result.ok, result.summary)
        paid_after = game.state.cards[paid.object_id]
        self.assertEqual(("B", "graveyard"), (paid_after.owner, paid_after.zone))
        self.assertNotEqual(original, paid_after.logical_object_id)
        self.assertTrue(game.state.cards[source.object_id].tapped)
        self.assertEqual(0, game.state.players["A"].mana_pool["C"])
        item = game.state.stack[-1]
        receipt = PaidArtifactSearchReceipt.from_dict(item.context[PAID_ARTIFACT_SEARCH_CONTEXT])
        self.assertEqual(("A", original, expected), (receipt.payer, receipt.snapshot.logical_object_id, receipt.snapshot.mana_value))
        self.assertEqual(1.0, game.engine._effective_card_data(paid_after)["mana_value"])
        return item.ref

    def test_native_departed_artifact_uses_its_battlefield_value_and_replays(self):
        game, source, paid, good, low, high, answer = self.scene(802001)
        stack = self.activate_bay(game, source, paid, 4)
        self.cast(game, "B", answer, [source.ref])
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        pending = self.resolve(game)
        self.assertEqual("semantic.search", pending.kind)
        self.assertEqual({good.ref}, {row["id"] for row in game.packet("pilot:A", full=True)["decision"]["ctx"]["search_cards"]})
        for seat in "BCD":
            for hidden in (good.ref, low.ref, high.ref):
                self.assertNotIn(hidden, json.dumps(game.packet(f"pilot:{seat}", full=True)))
        game = self.replay(game, load=True)
        before = authoritative_state_hash(game.state)
        for wrong in (low.ref, high.ref):
            self.assertFalse(game.act("pilot:A", {"action_id": "choose", "search_cards": [wrong]}).ok)
            self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:A", {"action_id": "choose", "search_cards": [good.ref]})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(("battlefield", "A"), (game.state.cards[good.object_id].zone, game.state.cards[good.object_id].controller))
        self.assertFalse(any(item.ref == stack for item in game.state.stack))
        search = next(event for event in game.state.events if event.code == "library.search" and event.details.get("source") == stack)
        self.assertEqual(good.ref, search.details["object"])
        self.replay(game, load=True)

    def test_qualified_fail_to_find_and_forbidden_search_both_shuffle_once(self):
        for prohibited in (False, True):
            with self.subTest(prohibited=prohibited):
                game, source, paid, good, low, high, _answer = self.scene(802010 + prohibited, copied=False, prohibition=prohibited)
                before = sum(event.code == "library.shuffle" for event in game.state.events)
                stack = self.activate_bay(game, source, paid, 1)
                pending = self.resolve(game)
                if prohibited:
                    self.assertIsNone(pending)
                    self.assertTrue(any(event.code == "library.search.prohibited" and event.details.get("source") == stack for event in game.state.events))
                else:
                    self.assertEqual("semantic.search", pending.kind)
                    options = game.packet("pilot:A", full=True)["decision"]["ctx"]["search_cards"]
                    self.assertIn(low.ref, {row["id"] for row in options})
                    result = game.act("pilot:A", {"action_id": "choose", "search_cards": []})
                    self.assertTrue(result.ok, result.summary)
                self.assertEqual("library", game.state.cards[low.object_id].zone)
                self.assertEqual(before + 1, sum(event.code == "library.shuffle" for event in game.state.events))
                self.replay(game, load=True)

    def test_native_private_replacement_resumes_battlefield_entry_using_the_same_paid_bound(self):
        game, source, paid, good, _low, _high, _answer = self.scene(802014, replacement=True)
        before_shuffles = sum(event.code == "library.shuffle" for event in game.state.events)
        self.activate_bay(game, source, paid, 4)
        self.assertEqual("semantic.search", self.resolve(game).kind)
        result = game.act("pilot:A", {"action_id": "choose", "search_cards": [good.ref]})
        self.assertTrue(result.ok, result.summary)
        pending = game.state.pending_decision
        self.assertEqual(("replacement.order", ["A"]), (pending.kind, pending.actors))
        self.assertEqual("library", game.state.cards[good.object_id].zone)
        self.assertEqual(before_shuffles, sum(event.code == "library.shuffle" for event in game.state.events))
        for seat in "BCD":
            packet = game.packet(f"pilot:{seat}", full=True)
            self.assertIsNone(packet["decision"])
            self.assertNotIn(good.ref, json.dumps(packet))
        options = game.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
        orb = next(row["id"] for row in options if "bay-entry-orb" in row["id"])
        game = self.replay(game, load=True)
        command = {"action_id": "choose", "replacement": orb}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        arrival = game.state.cards[good.object_id]
        self.assertEqual(("A", "battlefield", False), (arrival.controller, arrival.zone, arrival.tapped))
        self.assertEqual(before_shuffles + 1, sum(event.code == "library.shuffle" for event in game.state.events))
        self.replay(game, load=True)

    def test_hidden_nonarrival_uses_actual_public_destinations(self):
        retained = SimpleNamespace(zone="library", ref="private-ref", face_down=False)
        face_down = SimpleNamespace(zone="exile", ref="face-down-ref", face_down=True)
        arrived = SimpleNamespace(zone="battlefield", ref="public-ref", face_down=False)
        result = project_semantic_search_result_publication(
            paid_artifact_search_effect(bound=5), source="s1", destination_spec="battlefield", destination="battlefield",
            reveal=False, moved=[retained, face_down, arrived], allocation_details={},
        )
        self.assertEqual(1, result.public_count)
        self.assertEqual(["public-ref"], result.public_details["objects"])
        self.assertEqual((arrived,), tuple(result.public_changed_objects))

    def test_pre_move_mana_value_mutation_is_detected(self):
        game, source, paid, *_rest = self.scene(802012)
        self.activate_bay(game, source, paid, 4)
        game, source, paid, *_rest = self.scene(802013)
        with patch("quorune.rules.paid_artifact_library_search.public_mana_value", return_value=1):
            with self.assertRaises(AssertionError):
                self.activate_bay(game, source, paid, 4)
