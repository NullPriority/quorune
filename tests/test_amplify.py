from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
import test_fixed_reveal_additional_costs as reveal
from test_granted_skulk import record

from quorune.rules.amplify import AMPLIFY_CAPABILITY, AMPLIFY_HANDLER, amplify_hand_reveals, AmplifyHandCard, AmplifySpec
from quorune.carddb import CardDatabase
from quorune.card_programs import bind_card_program_runtime
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.replacement.amplify import AmplifyEntryOperation
from quorune.replacement.model import (
    AffectedObject, EntryReplacementScope, ReplaceableEvent, ReplacementClass, ReplacementEffect,
    ReplacementEffectError, ReplacementEventBatch,
)
from quorune.replacement.operations import AddAffectedObjectCounter, MultiplyAmount, operation_from_dict
from quorune.replacement.ordering import (
    advance_replacement_batch, apply_batch_replacement, next_batch_replacement_choice, replacement_choice_payload,
)
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.semantics import SemanticRegistry
from scripts.build_test_database import build_fixture_database


ORIGINALS = (
    "Aven Warhawk", "Canopy Crawler", "Daru Stinger", "Feral Throwback",
    "Glowering Rogon", "Kilnmouth Dragon", "Zombie Brute",
)


class AmplifyCompilerTests(unittest.TestCase):
    def test_fixed_amplify_spans_and_repeated_instances(self):
        registry = load_default_capability_registry()
        text = "Amplify 1, amplify 2"
        ir = compile_oracle_card(record(text, "Creature — Bird Soldier"), capability_registry=registry)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        nodes = ir.faces[0].nodes
        self.assertEqual(["Amplify 1", "amplify 2"], [node.text for node in nodes])
        self.assertEqual(2, len({node.node_id for node in nodes}))
        for multiplier, node in enumerate(nodes, start=1):
            self.assertEqual(node.text, text[node.span.start:node.span.end])
            self.assertIn(AMPLIFY_CAPABILITY, node.capability_dependencies)
            self.assertEqual(AmplifySpec(multiplier).to_dict(), node.handlers[0]["amplify"])
        for invalid in ("Amplify X", "Amplify -1", "Amplify 1 if you control a Forest"):
            self.assertNotEqual("exact", compile_oracle_card(record(invalid, "Creature — Bird"),
                capability_registry=registry).status)

    def test_amplify_requires_normalized_entry_authority(self):
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        owner = next(row for row in raw["capabilities"] if row["id"] == "trigger.event.normalized_zone_change")
        owner.update(status="blocked", blockers=["withdrawn for the dependency witness"])
        registry = CapabilityRegistry(raw)
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        index = build_index(registry_value=raw, declaration_source=declarations,
            discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        with self.assertRaises(ValueError):
            AmplifySpec.from_dict({"schema_version": True, "multiplier": 1})
        self.assertNotEqual("exact", compile_oracle_card(record("Amplify 1", "Creature — Bird"),
            capability_registry=registry).status)
        self.assertEqual("exact", compile_oracle_card(record("Flying", "Creature — Bird"),
            capability_registry=registry).status)


class AmplifyReplacementTests(unittest.TestCase):
    def fixture(self):
        scope = EntryReplacementScope(("entrant", "simultaneous"), ())
        event = ReplaceableEvent(
            event_id="entry", kind="zone.change", affected_player=None,
            affected_object=AffectedObject(object_id="entrant", owner="A", controller="A"),
            payload={"origin": "hand", "destination": "battlefield", "destination_controller": "A",
                "logical_object_id": "entrant-old", "object_ref": "new-ref",
                "object_types": ["bird", "creature", "soldier"]},
            entry_scope=scope,
        )
        candidates = tuple(AmplifyHandCard(name, name + "-logical", name + "-ref", "A", name + "-oracle", name, types)
            for name, types in (("bird-soldier", ("bird", "soldier")), ("soldier", ("soldier",)),
                ("simultaneous", ("bird",)), ("beast", ("beast",))))
        effects = tuple(ReplacementEffect(effect_id=f"amp-{amount}", source_id="new-ref", event_kind="zone.change",
            replacement_class=ReplacementClass.OTHER, conditions={"destination": {"eq": "battlefield"}},
            operations=(AmplifyEntryOperation("A", AmplifySpec(amount), candidates),), optional=True)
            for amount in (1, 2))
        return ReplacementEventBatch("batch", (event,), ("A", "B", "C", "D")), effects

    def test_amplify_uses_prospective_types_and_excludes_simultaneous_entrants(self):
        batch, effects = self.fixture()
        pending = next_batch_replacement_choice(batch, effects)
        projected = replacement_choice_payload(pending, effects)
        self.assertEqual(["bird-soldier-ref", "soldier-ref"], projected["object_choices"]["amp-1"]["legal_refs"])
        for refs in (["simultaneous-ref"], ["beast-ref"], ["bird-soldier-ref", "bird-soldier-ref"]):
            with self.assertRaises(ReplacementEffectError):
                apply_batch_replacement(batch, effects, pending, {"effect_id": "amp-1", "objects": refs})
        event = batch.events[0]
        changed_types = replace(event, payload={**event.payload, "object_types": ["beast", "creature"]})
        operation, = effects[0].operations
        self.assertEqual(["beast-ref"], [card.ref for card in operation.eligible_cards(changed_types)])

    def test_independent_amplify_instances_share_the_entry_counter_occurrence(self):
        batch, effects = self.fixture()
        operation, = effects[0].operations
        self.assertEqual(operation, operation_from_dict(operation.to_dict()))
        for effect_id, refs in (("amp-1", ["bird-soldier-ref", "soldier-ref"]), ("amp-2", ["bird-soldier-ref"])):
            batch = apply_batch_replacement(batch, effects, next_batch_replacement_choice(batch, effects),
                {"effect_id": effect_id, "objects": refs})
        event = batch.events[0]
        counter, = event.children
        self.assertEqual(("counter.place", 4), (counter.kind, counter.payload["amount"]))
        self.assertEqual(["bird-soldier-ref", "soldier-ref", "bird-soldier-ref"],
            [card["ref"] for card in amplify_hand_reveals(event.payload)])
        empty, effects = self.fixture()
        for effect_id in ("amp-1", "decline:amp-2"):
            empty = apply_batch_replacement(empty, effects, next_batch_replacement_choice(empty, effects),
                {"effect_id": effect_id, "objects": []})
        self.assertEqual((), amplify_hand_reveals(empty.events[0].payload))
        self.assertEqual((), empty.events[0].children)

    def test_amplify_entrant_exclusion_mutation_is_killed(self):
        eligible = AmplifyEntryOperation.eligible_cards

        def omit_other_entrants(operation, event):
            scope = EntryReplacementScope((event.affected_object.object_id,), ())
            return eligible(operation, replace(event, entry_scope=scope))

        with patch.object(AmplifyEntryOperation, "eligible_cards", omit_other_entrants):
            with self.assertRaises(AssertionError):
                self.test_amplify_uses_prospective_types_and_excludes_simultaneous_entrants()

    def test_all_amplify_contributions_precede_nested_quantity_replacement(self):
        batch, effects = self.fixture()
        additional = ReplacementEffect(
            effect_id="additional", source_id="external", event_kind="zone.change",
            replacement_class=ReplacementClass.OTHER, conditions={"destination": {"eq": "battlefield"}},
            operations=(AddAffectedObjectCounter("+1/+1", 1, "A", "external", 0),),
        )
        quantity = ReplacementEffect(
            effect_id="double", source_id="external", event_kind="counter.place",
            replacement_class=ReplacementClass.OTHER, conditions={"counter_name": {"eq": "+1/+1"}},
            operations=(MultiplyAmount("amount", 2),),
        )
        effects = (*effects, additional, quantity)
        selections = (
            {"effect_id": "amp-1", "objects": ["soldier-ref"]},
            {"effect_id": "amp-2", "objects": ["soldier-ref"]},
        )
        progress = advance_replacement_batch(batch, effects, selections=selections[:1])
        self.assertIsNotNone(progress.pending)
        self.assertEqual({"additional", "amp-2"}, set(progress.pending.choice.options))
        final = advance_replacement_batch(batch, effects, selections=(*selections, "additional"))
        self.assertIsNone(final.pending)
        counter, = final.batch.events[0].children
        self.assertEqual(8, counter.payload["amount"])


class AmplifyRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = reveal.FixedRevealCostRuntimeTests.session
    add = reveal.FixedRevealCostRuntimeTests.add
    fund = reveal.FixedRevealCostRuntimeTests.fund
    offered = reveal.FixedRevealCostRuntimeTests.offered
    checkpoint = reveal.FixedRevealCostRuntimeTests.checkpoint
    resolve = reveal.FixedRevealCostRuntimeTests.resolve
    replay = reveal.FixedRevealCostRuntimeTests.replay
    reject = reveal.FixedRevealCostRuntimeTests.reject

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "amplify.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/bound-effect-program-cards.json",
            ROOT / "tests/fixtures/amplify-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Amplify witness", [DeckEntry("Generic Bound Commander", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30)], ["Generic Bound Commander"])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def cast_for_entry(self, session, card, mana):
        self.fund(session, mana)
        offer = self.offered(session, card)
        result = session.act("pilot:A", {"action_id": offer["id"], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        decision = self.resolve(session)
        self.assertIsNotNone(decision)
        self.assertEqual("replacement.order", decision.kind)

    def amplify_option(self, session):
        ctx = session.packet("pilot:A", full=True)["decision"]["ctx"]
        option = next(row for row in ctx["options"] if AMPLIFY_HANDLER in row["id"] and not row["decline"])
        return option["id"], ctx["object_choices"][option["id"]]["legal_refs"]

    def test_all_seven_original_amplify_cards_bind_strictly(self):
        for name in ORIGINALS:
            with self.subTest(name=name):
                card = self.db.lookup(name, fuzzy=False)
                program = compile_best_available_card_program(self.db, card, semantic_registry=SemanticRegistry(),
                    capability_registry=self.registry, capability_profile="commander_review")
                binding = bind_card_program_runtime(program, capability_registry=self.registry, profile="commander_review")
                self.assertTrue(binding["strict_capability_ready"], binding)

    def test_simultaneous_original_entries_exclude_both_hand_entrants(self):
        from quorune.replacement import ReplacementChoiceRequired
        from quorune.semantic_runtime.zone_replacements import prepare_zone_change_replacement_batch

        session = self.session(7023803)
        warhawk = self.add(session.engine, "Aven Warhawk", zone="hand")
        stinger = self.add(session.engine, "Daru Stinger", zone="hand")
        bird = self.add(session.engine, "Aven Riftwatcher", zone="hand")
        moves = ((warhawk.object_id, "battlefield"), (stinger.object_id, "battlefield"))
        selections = []
        for entrant in (warhawk, stinger):
            with self.assertRaises(ReplacementChoiceRequired) as raised:
                prepare_zone_change_replacement_batch(session.engine, moves, selections=selections)
            choice = raised.exception
            self.assertEqual(entrant.object_id, choice.pending.choice.event.affected_object.object_id)
            self.assertEqual({warhawk.object_id, stinger.object_id},
                set(choice.pending.choice.event.entry_scope.entering_objects))
            effect_id, = choice.pending.choice.options
            payload = replacement_choice_payload(choice.pending, choice.effects)
            self.assertEqual([bird.ref], payload["object_choices"][effect_id]["legal_refs"])
            selections.append({"effect_id": effect_id, "objects": [bird.ref]})
        prepared = prepare_zone_change_replacement_batch(session.engine, moves, selections=selections)
        self.assertEqual([1, 1], [entry.counter_events[0].payload["amount"] for entry in prepared.values()])
        self.assertEqual("hand", session.state.cards[bird.object_id].zone)

    def test_actual_warhawk_privately_reveals_only_selected_cards_and_combines_counters(self):
        session = self.session(7023801)
        source = self.add(session.engine, "Aven Warhawk", zone="hand")
        soldier = self.add(session.engine, "Daru Stinger", zone="hand")
        changeling = self.add(session.engine, "Changeling Outcast", zone="hand")
        bird = self.add(session.engine, "Aven Riftwatcher", zone="hand")
        beast = self.add(session.engine, "Canopy Crawler", zone="hand")
        foreign = self.add(session.engine, "Daru Stinger", seat="B", zone="hand", ref="foreign-soldier")
        self.add(session.engine, "Hardened Scales")
        self.cast_for_entry(session, source, {"C": 4, "W": 1})
        option, refs = self.amplify_option(session)
        self.assertEqual({soldier.ref, changeling.ref, bird.ref}, set(refs))
        for viewer in "BCD":
            packet = session.packet("pilot:" + viewer, full=True)
            self.assertIsNone(packet["decision"])
            self.assertNotIn(soldier.ref, json.dumps(packet))
            self.assertNotIn(changeling.ref, json.dumps(packet))
            self.assertNotIn(bird.ref, json.dumps(packet))
        stale = session
        session = self.replay(session, load=True)
        command = {"action_id": "choose", "replacement": option}
        self.reject(session, "B", **command, replacement_objects=[soldier.ref])
        for invalid in ([soldier.ref, soldier.ref], [foreign.ref], [beast.ref]):
            self.reject(session, **command, replacement_objects=invalid)
        result = session.act("pilot:A", {**command, "replacement_objects": [soldier.ref, changeling.ref]})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(session))
        current = session.state.cards[source.object_id]
        self.assertEqual(("battlefield", 3), (current.zone, current.counters.get("+1/+1")))
        self.assertEqual("hand", session.state.cards[soldier.object_id].zone)
        public = json.dumps(session.packet("pilot:D", full=True))
        for card in (soldier, changeling):
            self.assertIn(card.ref, public)
        for card in (bird, beast, foreign):
            self.assertNotIn(card.ref, public)
        self.replay(session, load=True)
        stale.engine.move_card(soldier.object_id, "exile", log=False)
        stale.engine.move_card(soldier.object_id, "hand", log=False)
        self.reject(stale, **command, replacement_objects=[soldier.ref])

    def test_copied_amplify_uses_the_copier_controller_and_current_types(self):
        session = self.session(7023802)
        original = self.add(session.engine, "Aven Warhawk", seat="B")
        source = self.add(session.engine, "Clone", zone="hand")
        soldier = self.add(session.engine, "Daru Stinger", zone="hand")
        foreign = self.add(session.engine, "Aven Riftwatcher", seat="B", zone="hand")
        self.cast_for_entry(session, source, {"C": 3, "U": 1})
        ctx = session.packet("pilot:A", full=True)["decision"]["ctx"]
        copy = next(row for row in ctx["options"] if original.ref in row["id"] and not row["decline"])
        result = session.act("pilot:A", {"action_id": "choose", "replacement": copy["id"]})
        self.assertTrue(result.ok, result.summary)
        option, refs = self.amplify_option(session)
        self.assertEqual([soldier.ref], refs)
        self.assertNotIn(foreign.ref, json.dumps(session.packet("pilot:A", full=True)))
        result = session.act("pilot:A", {"action_id": "choose", "replacement": option,
            "replacement_objects": [soldier.ref]})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(session))
        current = session.state.cards[source.object_id]
        self.assertEqual(("battlefield", 1), (current.zone, current.counters.get("+1/+1", 0)))
        self.assertEqual("Aven Warhawk", session.engine._effective_card_data(current)["name"])
        self.assertIn(soldier.ref, json.dumps(session.packet("pilot:D", full=True)))
        self.replay(session, load=True)
