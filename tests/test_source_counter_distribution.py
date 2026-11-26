"""Native witnesses for one nontarget upkeep movement and its original cast sibling."""

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
from quorune.choice_forms import build_action_form
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.source_counter_distribution import (
    SOURCE_COUNTER_DISTRIBUTION_CAPABILITY, source_counter_distribution_effect,
)
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


DONOR = "Generic Upkeep Donor"
SHROUDED = "Generic Shrouded Counter Recipient"
PROFILE = "commander_review"


class SourceCounterDistributionTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        db_path = Path(cls.temporary.name) / "source-counter-distribution.sqlite3"
        fixtures = ROOT / "tests" / "fixtures"
        build_fixture_database([
            fixtures / "fixed-one-counter-movement-cards.json",
            fixtures / "current-target-replacement-cards.json",
            fixtures / "source-counter-distribution-cards.json",
        ], db_path)
        cls.db = CardDatabase(db_path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Source counter distribution witness", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def reject(self, game, seat, command):
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:" + seat, command)
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def submit(self, game, seat, **choices):
        action = game.act("pilot:" + seat, {"action_id": "choose", **choices})
        self.assertTrue(action.ok, action.summary)

    def counts(self, game, *cards):
        return tuple(game.state.cards[card.object_id].counters.get("+1/+1", 0) for card in cards)

    def upkeep(self, game, source, *carriers):
        for card in (source, *carriers):
            self.assert_closed(game, card)
        game.state.config.semantic_policy = "trusted_only"
        previous = game.state.turn_order[game.state.turn_order.index("A") - 1]
        game.state.last_normal_turn_player = previous
        self.prepare(game, actor=previous, active=previous)
        self.checkpoint(game)
        self.until(game, lambda session: session.state.active_player == "A" and session.state.step == "upkeep")
        self.assertEqual(1, self.trigger_count(game, source))
        self.assertEqual("priority", game.state.pending_decision.kind)
        self.assertEqual([], game.state.stack[-1].targets)

    def test_compiler_requires_the_complete_upkeep_and_leaves_the_cast_sibling_exact(self):
        record = self.db.lookup(DONOR, fuzzy=False)
        result = compile_oracle_card(record, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", result.status, result.material_residuals)
        cast, upkeep = result.faces[0].nodes
        self.assertEqual(("spell.cast", "fixed-counter-spell-cast-trigger-optional-v1"),
                         (cast.event, cast.template_id))
        self.assertEqual(("step.begin", (source_counter_distribution_effect(),)), (upkeep.event, upkeep.effects))
        self.assertIsNone(upkeep.target_schema)
        with patch("quorune.compiler.counter_movement_templates.fixed_counter_movement_trigger_result", return_value=None):
            mutant = compile_oracle_card(record, capability_registry=self.registry, capability_profile=PROFILE)
            self.assertTrue(mutant.faces[0].nodes[0].exact)
            self.assertFalse(mutant.faces[0].nodes[1].exact)
        for change in ("onto other target creatures", "onto creatures you control", "onto other permanents"):
            narrowed = replace(record, oracle_text=record.oracle_text.replace("onto other creatures", change))
            alternate = compile_oracle_card(narrowed, capability_registry=self.registry, capability_profile=PROFILE)
            self.assertFalse(alternate.faces[0].nodes[1].exact, change)
            self.assertTrue(alternate.faces[0].nodes[0].exact, change)

        report, semantics = register_card(self.db, self.registry, DONOR)
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        capability = SOURCE_COUNTER_DISTRIBUTION_CAPABILITY
        program, = (p for p in semantics.programs_for_oracle(record.oracle_id) if p.event == "step.begin")
        dependencies = tuple(key for key in program.capability_dependencies if key != capability)
        withdrawn = replace(program, capability_dependencies=dependencies,
                            capability_closure=self.registry.closure(dependencies, profile=PROFILE).to_dict())
        check = bind_semantic_program_runtime(withdrawn, capability_registry=self.registry, profile=PROFILE)
        self.assertIn(f"capability:undeclared_runtime_dependency:{capability}", check["blockers"])

    def test_public_partition_waits_for_affected_replacement_order_then_commits_once(self):
        game = self.session(608204112)
        source = self.add(game.engine, DONOR, ref="counter-donor")
        source.counters.update({"+1/+1": 4, "charge": 1})
        own = self.add(game.engine, "Grizzly Bears", ref="counter-own")
        foreign = self.add(game.engine, SHROUDED, seat="B", ref="counter-opponent")
        land = self.add(game.engine, "Plains", seat="C", ref="counter-land")
        prior = game.state.turn_order[game.state.turn_order.index("A") - 1]
        hidden_seat = next(seat for seat in game.state.turn_order if seat not in {"A", "B", prior})
        hidden = self.add(game.engine, "Grizzly Bears", seat=hidden_seat, zone="hand", ref="counter-private-hidden")
        scales = self.add(game.engine, "Hardened Scales", seat="B", ref="counter-scales")
        season = self.add(game.engine, "Doubling Season", seat="B", ref="counter-double")
        self.upkeep(game, source, foreign, scales, season)
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["A"]), (pending.kind, pending.actors))
        decision = game.packet("pilot:A", full=True)["decision"]
        context = decision["ctx"]
        action, = context["legal_actions"]
        schema = action["choice_schema"]
        self.assertEqual(("counter_allocation", "quantity_map", 4, 1),
                         (schema["field"], schema["shape"], schema["maximum_total"], schema["minimum_each"]))
        self.assertEqual({own.ref, foreign.ref}, set(schema["legal_refs"]))
        self.assertNotIn(hidden.ref, json.dumps(context))
        self.assertIsNone(game.packet("pilot:B", full=True)["decision"])
        form = build_action_form(action, decision_kind="semantic.choice", context=context)
        self.assertEqual("quantity_map", form["fields"][0]["control"])
        choose = {"action_id": "choose", "counter_allocation": {own.ref: 1, foreign.ref: 2}}
        self.reject(game, "B", choose)
        for bad in ({source.ref: 1}, {land.ref: 1}, {hidden.ref: 1}, {own.ref: 5},
                    {own.ref: 3, foreign.ref: 2}, {own.ref: 0}, {own.ref: True},
                    {own.ref: -1}, [{"ref": own.ref, "amount": 1}]):
            self.reject(game, "A", {"action_id": "choose", "counter_allocation": bad})
        game = self.replay(game, load=True)
        self.submit(game, "A", counter_allocation=choose["counter_allocation"])
        pending = game.state.pending_decision
        self.assertEqual(("replacement.order", ["B"]), (pending.kind, pending.actors))
        self.assertEqual((4, 0, 0), self.counts(game, source, own, foreign))
        game = self.replay(game, load=True)
        options = game.packet("pilot:B", full=True)["decision"]["ctx"]["options"]
        selection, = (row["id"] for row in options if row["source"] == scales.ref)
        self.reject(game, "A", {"action_id": "choose", "replacement": selection})
        self.submit(game, "B", replacement=selection)
        self.assertIsNone(self.resolve(game))
        self.assertEqual((1, 1, 6), self.counts(game, source, own, foreign))
        self.assertEqual(1, game.state.cards[source.object_id].counters["charge"])
        self.replay(game, load=True)

    def test_decline_keeps_counters_and_cast_counter_resolves_before_the_spell(self):
        game = self.session(608204113)
        source = self.add(game.engine, DONOR)
        source.counters["+1/+1"] = 2
        recipient = self.add(game.engine, "Grizzly Bears")
        unsummon = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        self.add(game.engine, "Island", seat="B")
        self.upkeep(game, source, unsummon)
        pending = self.resolve(game)
        self.assertEqual("semantic.choice", pending.kind)
        self.submit(game, "A", counter_allocation={})
        self.assertIsNone(self.resolve(game))
        self.assertEqual((2, 0), self.counts(game, source, recipient))
        self.cast(game, "B", unsummon, [recipient.ref])
        self.assertEqual(1, self.trigger_count(game, source))
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["A"]), (pending.kind, pending.actors))
        self.submit(game, "A", choice="put")
        self.assertEqual((3, 0), self.counts(game, source, recipient))
        self.assertEqual("battlefield", game.state.cards[recipient.object_id].zone)
        self.assertEqual("spell", game.state.stack[-1].kind)
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[recipient.object_id].zone)
        self.replay(game, load=True)

    def test_reentered_donor_cannot_supply_its_prior_upkeep_trigger(self):
        game = self.session(608204114)
        source = self.add(game.engine, DONOR)
        source.counters["+1/+1"] = 3
        original = source.logical_object_id
        recipient = self.add(game.engine, "Grizzly Bears", seat="C", ref="reentry-eligible")
        displaced = self.add(game.engine, "Grizzly Bears", seat="D", ref="reentry-displaced")
        blink = self.add(game.engine, "Cloudshift", zone="hand")
        unsummon = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        self.add(game.engine, "Plains")
        self.add(game.engine, "Island", seat="B")
        self.upkeep(game, source, blink, unsummon)
        self.cast(game, "A", blink, [source.ref])
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["A"]), (pending.kind, pending.actors))
        self.submit(game, "A", choice="decline")
        self.resolve_top(game)
        current = game.state.cards[source.object_id]
        self.assertEqual("battlefield", current.zone)
        self.assertNotEqual(original, current.logical_object_id)
        self.assertEqual(1, self.trigger_count(game, current))
        self.cast(game, "B", unsummon, [displaced.ref])
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["A"]), (pending.kind, pending.actors))
        self.submit(game, "A", choice="put")
        self.resolve_top(game)
        self.assertEqual((1, 0), self.counts(game, source, recipient))
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual((1, 0), self.counts(game, source, recipient))
        self.replay(game, load=True)
