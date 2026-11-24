"""The exact entry designation and named counters use one replacement choice."""

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.compiler import entry_designation_templates
from quorune.deck import DeckDefinition, DeckEntry
from quorune.entry_designations import (
    COUNTED_CREATURE_TYPE_ENTRY_CAPABILITY as CAPABILITY,
    COUNTED_CREATURE_TYPE_ENTRY_HANDLER_ID as HANDLER,
    EntryDesignationKind,
)
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.replacement.counted_entry_counters import CreateCountedEntryCounters
from quorune.replacement.operations import operation_from_dict
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantic_runtime.context import SemanticNodeError
from quorune.semantic_runtime.entry_designations import CountedCreatureTypeEntryDesignationHandler
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


BANNER = "Banner of Kinship"
RESTORATION = "Brilliant Restoration"
DOOR = "Door of Destinies"
PROFILE = "commander_review"
FIXTURES = ROOT / "tests" / "fixtures"


class ChosenTypeEntryCounterTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        focus = {
            "bound-effect-program-cards.json": {
                "Generic Bound Commander", "Generic Bound Growth", "Generic Bound Plains", "Unsummon",
            },
            "characteristic-query-union-cards.json": {"Island", "Universal Automaton"},
            "chosen-type-event-cards.json": {DOOR},
            "counter-replacement-cards.json": {"Doubling Season"},
            "creature-subtype-addition-cards.json": {"Boggart Shenanigans", "Goblin Piker"},
            "devour-cards.json": {"Caprichrome"},
            "static-land-animation-cards.json": {"Sculpting Steel"},
        }
        inputs = [FIXTURES / "chosen-type-entry-counter-cards.json"]
        for index, (name, names) in enumerate(sorted(focus.items())):
            document = json.loads((FIXTURES / name).read_bytes())
            document["cards"] = [row for row in document["cards"] if row["name"] in names]
            assert {row["name"] for row in document["cards"]} == names
            ids = {row["oracle_id"] for row in document["cards"]}
            document["rulings"] = [row for row in document.get("rulings", []) if row["oracle_id"] in ids]
            file = folder / f"support-{index}.json"
            file.write_text(json.dumps(document), encoding="utf-8")
            inputs.append(file)
        path = folder / "chosen-type-entry.sqlite3"
        build_fixture_database(inputs, path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Chosen Entry Count", [
            DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def test_the_entry_and_derived_counter_operations_are_closed(self):
        oracle = self.db.lookup(BANNER, fuzzy=False).oracle_text.splitlines()[0]
        _, descriptor, caps = entry_designation_templates.entry_designation_handler(oracle, source_name=BANNER)
        self.assertIn(CAPABILITY, caps)
        handler = CountedCreatureTypeEntryDesignationHandler()
        self.assertEqual(EntryDesignationKind.CREATURE_TYPE, handler.validate(descriptor))
        for update in ({"schema_version": True}, {"schema_version": 1}, {"counter_name": "+1/+1"},
                       {"counter_name": "Fellowship"}, {"counter_name": "flying"}, {"designation": "color"}, {"extra": "controller"}):
            with self.subTest(update=update), self.assertRaises(SemanticNodeError):
                handler.validate({**descriptor, **update})
        for text in (oracle + " Draw a card.", oracle.replace("creature you control", "permanent you control"),
                     oracle.replace("of the chosen type", "in your graveyard"),
                     oracle.replace("a fellowship", "two fellowship").replace("counter on", "counters on"),
                     oracle.replace("fellowship", "fellowshİp"), oracle.replace("fellowship", "flying"),
                     oracle.replace("fellowship", "first strike")):
            with self.subTest(text=text):
                self.assertIsNone(entry_designation_templates.entry_designation_handler(text, source_name=BANNER))
        op = CreateCountedEntryCounters("fellowship", "banner", "A", ("current", "leaving"), ("leaving",))
        self.assertEqual(op, operation_from_dict(op.to_dict()))
        for update in ({"schema_version": True}, {"matching_objects": []},
                       {"matching_objects": ["leaving", "current"]}, {"matching_objects": ["current", "current"]},
                       {"simultaneous_departures": ["another"]}, {"extra": 3}):
            with self.subTest(update=update), self.assertRaises(ValueError):
                operation_from_dict({**op.to_dict(), **update})

    def test_intact_banner_binds_entry_and_static_with_an_independent_runtime_minimum(self):
        card = self.db.lookup(BANNER, fuzzy=False)
        ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        self.assertEqual({"zone.change", "characteristics.evaluate"},
                         {node.event for node in ir.faces[0].nodes})
        report, semantics = register_card(self.db, self.registry, BANNER)
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        self.assertEqual("capability_closed", report["trust_basis"])
        program, = [value for value in semantics.programs_for_oracle(card.oracle_id)
                    if CAPABILITY in value.capability_dependencies]
        self.assertTrue(any(row["handler_id"] == HANDLER for row in program.handlers))
        unlabelled = replace(program, coverage=[])
        self.assertTrue(bind_semantic_program_runtime(
            unlabelled, capability_registry=self.registry, profile=PROFILE,
        )["strict"])
        deps = tuple(c for c in program.capability_dependencies if c != CAPABILITY)
        changed = replace(unlabelled, capability_dependencies=deps,
                          capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
        bound = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(bound["strict"])
        self.assertIn("capability:undeclared_runtime_dependency:" + CAPABILITY, bound["blockers"])

    def _start(self, game, mana):
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, mana=mana)
        self.checkpoint(game)

    def _options(self, game):
        pending = game.state.pending_decision
        self.assertEqual(("replacement.order", ["A"]), (pending.kind, pending.actors))
        return game.packet("pilot:A", full=True)["decision"]["ctx"]["options"]

    def _reject(self, game, actor, command):
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:" + actor, command)
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def _choose(self, game, replacement, **response):
        command = {"action_id": "choose", "replacement": replacement, **response}
        self._reject(game, "D", command)
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)

    def _type_option(self, game, card, subtype):
        option, = (row["id"] for row in self._options(game)
                   if HANDLER in row["id"] and card.ref in row["id"] and f":{subtype}" in row["id"])
        return option

    def _entry_state(self, game, source, subtype, count):
        source = game.state.cards[source.object_id]
        self.assertEqual("battlefield", source.zone)
        self.assertEqual(subtype, source.annotations["chosen_creature_type"])
        self.assertEqual(count, source.counters.get("fellowship", 0))

    def test_paid_banner_counts_only_current_controlled_creatures_and_doubles_the_entry_counter(self):
        game = self.session(61412080)
        banner = self.add(game.engine, BANNER, zone="hand")
        goblin = self.add(game.engine, "Goblin Piker", ref="banner-matching-goblin")
        other = self.add(game.engine, "Goblin Piker", ref="banner-leaving-goblin")
        foreign = self.add(game.engine, "Goblin Piker", seat="C", ref="banner-foreign-goblin")
        changeling = self.add(game.engine, "Universal Automaton")
        self.add(game.engine, "Boggart Shenanigans")
        self.add(game.engine, "Doubling Season")
        bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        self.add(game.engine, "Island", seat="B")
        secret = self.add(game.engine, "Generic Bound Growth", seat="D", zone="hand", ref="banner-private-card")
        self.assert_closed(game, banner)
        self._start(game, {"C": 5})
        self.cast(game, "A", banner)
        self.cast(game, "B", bounce, [other.ref])
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[other.object_id].zone)
        self.assertEqual("replacement.order", self.resolve(game).kind)
        self.assertEqual("stack", game.state.cards[banner.object_id].zone)
        self.assertFalse(game.state.cards[banner.object_id].counters)
        self.assertEqual(len(EntryDesignationKind.CREATURE_TYPE.values), len(self._options(game)))
        for actor in "BCD":
            self.assertIsNone(game.packet("pilot:" + actor, full=True)["decision"])
        self.assertNotIn(secret.ref, json.dumps(game.packet("pilot:A", full=True)))
        self._reject(game, "A", {"action_id": "choose", "replacement": "artifact"})
        game = self.replay(game, load=True)
        self._choose(game, self._type_option(game, banner, "goblin"))
        self.assertIsNone(self.resolve(game))
        self._entry_state(game, banner, "goblin", 4)
        self.assertEqual(6, game.engine._numeric_stat(goblin.object_id, "power"))
        self.assertEqual(5, game.engine._numeric_stat(changeling.object_id, "power"))
        self.assertEqual(2, game.engine._numeric_stat(foreign.object_id, "power"))
        self.replay(game, load=True)

    def test_actual_restore_counts_after_an_earlier_devour_and_excludes_simultaneous_entrants(self):
        game = self.session(61412081)
        spell = self.add(game.engine, RESTORATION, zone="hand")
        caprichrome = self.add(game.engine, "Caprichrome", zone="graveyard", ref="banner-devour-entry")
        banner = self.add(game.engine, BANNER, zone="graveyard", ref="banner-restore-entry")
        arriving = self.add(game.engine, "Universal Automaton", zone="graveyard", ref="banner-other-entry")
        sacrifice = self.add(game.engine, "Universal Automaton", ref="banner-current-artifact")
        goblin = self.add(game.engine, "Goblin Piker")
        for card in (spell, caprichrome, banner, arriving):
            self.assert_closed(game, card)
        self._start(game, {"C": 3, "W": 4})
        self.cast(game, "A", spell)
        self.assertEqual("replacement.order", self.resolve(game).kind)
        choice, = (row["id"] for row in self._options(game)
                   if "replacement.zone.devour-entry.v1:" in row["id"] and not row["decline"])
        self._reject(game, "A", {"action_id": "choose", "replacement": choice, "replacement_objects": [arriving.ref]})
        self._choose(game, choice, replacement_objects=[sacrifice.ref])
        self.assertEqual("graveyard", game.state.cards[banner.object_id].zone)
        self.assertEqual("graveyard", game.state.cards[arriving.object_id].zone)
        game = self.replay(game, load=True)
        self._choose(game, self._type_option(game, banner, "goblin"))
        self.assertIsNone(self.resolve(game))
        self._entry_state(game, banner, "goblin", 1)
        self.assertEqual("graveyard", game.state.cards[sacrifice.object_id].zone)
        self.assertEqual(1, game.state.cards[caprichrome.object_id].counters["+1/+1"])
        self.assertEqual("battlefield", game.state.cards[arriving.object_id].zone)
        self.assertEqual(2, game.engine._numeric_stat(arriving.object_id, "power"))
        self.assertEqual(3, game.engine._numeric_stat(goblin.object_id, "power"))
        self.replay(game, load=True)

    def test_actual_copy_gets_its_own_choice_and_zero_counter_count(self):
        game = self.session(61412082)
        original = self.add(game.engine, BANNER, seat="B")
        original.annotations["chosen_creature_type"] = "goblin"
        original.counters["fellowship"] = 3
        steel = self.add(game.engine, "Sculpting Steel", zone="hand")
        self.add(game.engine, "Goblin Piker", seat="B", ref="original-banner-member")
        own = self.add(game.engine, "Goblin Piker")
        self.assert_closed(game, original)
        self.assert_closed(game, steel)
        self._start(game, {"C": 3})
        self.cast(game, "A", steel)
        self.assertEqual("replacement.order", self.resolve(game).kind)
        copy, = (row["id"] for row in self._options(game) if original.ref in row["id"] and not row["decline"])
        self._choose(game, copy)
        self._choose(game, self._type_option(game, steel, "time lord"))
        self.assertIsNone(self.resolve(game))
        self._entry_state(game, steel, "time lord", 0)
        self._entry_state(game, original, "goblin", 3)
        self.assertEqual(2, game.engine._numeric_stat(own.object_id, "power"))
        self.replay(game, load=True)

    def test_omitting_combined_entry_lowering_leaves_the_original_static_result(self):
        with patch.object(entry_designation_templates, "_counted_creature_type_entry_handler", return_value=None):
            card = self.db.lookup(BANNER, fuzzy=False)
            ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
            residual, = ir.material_residuals
            self.assertEqual("replacement_effect", residual.kind)
            self.assertIn("fellowship counter on it", residual.text)
            self.assertIn("continuous.power_toughness.chosen_source_counter_anthem",
                          {cap for node in ir.faces[0].nodes for cap in node.capability_dependencies})
            report, _ = register_card(self.db, self.registry, BANNER)
            self.assertFalse(report["strict_capability_ready"], report)

    def test_genuine_entry_counter_withdrawal_preserves_the_exact_door_anthem_and_plain_designation(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = deepcopy(json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes()))
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        next(row for row in raw["capabilities"] if row["id"] == CAPABILITY).update(
            status="blocked", blockers=["Scoped counted designation authority withdrawal"],
        )
        registry = CapabilityRegistry(raw)
        index = build_index(registry_value=raw, declaration_source=declarations,
                            discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        closure = registry.closure((CAPABILITY,), profile=PROFILE)
        self.assertIn("status:" + CAPABILITY + ":blocked", closure.blockers)
        self.assertFalse(any("unverified" in blocker for blocker in closure.blockers))
        rejected, _ = register_card(self.db, registry, BANNER)
        retained, _ = register_card(self.db, registry, DOOR)
        self.assertFalse(rejected["strict_capability_ready"], rejected)
        self.assertTrue(retained["strict_capability_ready"], retained["blockers"])


if __name__ == "__main__":
    unittest.main()
