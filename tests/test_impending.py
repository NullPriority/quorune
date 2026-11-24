"""Pinned printed Impending prices and the strict descriptor boundary."""

from pathlib import Path
import json
from types import SimpleNamespace
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from quorune.impending_model import (
    FixedManaImpendingSpec,
    PAID_IMPENDING_FIELD,
    impending_payment_context,
    parse_fixed_mana_impending,
)
from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.errors import StateInvariantError
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from quorune.model import CardInstance, StackItem
from quorune.rules.impending import impending_entry_counters
from quorune.selection.copy_targets import stack_copy_context
from quorune.zone_object_state import mark_resolving_cast_facts, reset_card_after_zone_change


class FixedManaImpendingTests(unittest.TestCase):
    def test_all_five_printed_prices_and_counter_counts(self):
        fixture = Path(__file__).parent / "fixtures/impending-cards.json"
        cards = [card for card in json.loads(fixture.read_bytes())["cards"]
                 if card["oracle_text"].startswith("Impending ")]
        expected = {
            "Overlord of the Balemurk": (5, "{1}{B}"),
            "Overlord of the Boilerbilges": (4, "{2}{R}{R}"),
            "Overlord of the Floodpits": (4, "{1}{U}{U}"),
            "Overlord of the Hauntwoods": (4, "{1}{G}{G}"),
            "Overlord of the Mistmoors": (4, "{2}{W}{W}"),
        }
        self.assertEqual(set(expected), {card["name"] for card in cards})
        for card in cards:
            with self.subTest(card=card["name"]):
                line = card["oracle_text"].splitlines()[0].split(" (", 1)[0]
                spec = parse_fixed_mana_impending(line)
                self.assertEqual(FixedManaImpendingSpec(*expected[card["name"]]), spec)
                self.assertEqual(spec, FixedManaImpendingSpec.from_dict(spec.to_dict()))

    def test_cost_payload_has_exact_fields_and_uses_the_fixed_mana_owner(self):
        spec = FixedManaImpendingSpec(4, "{2}{R}{R}")
        self.assertEqual({"GENERIC": 2, "W": 0, "U": 0, "B": 0, "R": 2, "G": 0, "C": 0}, dict(spec.mana_requirements))
        for line in ("Impending 4—{X}{R}", "Impending 4—{U/R}", "Impending 0—{R}",
                     "Impending 4—{R} if it is your turn"):
            self.assertIsNone(parse_fixed_mana_impending(line), line)
        for edited in (dict(spec.to_dict(), schema_version=True), dict(spec.to_dict(), time_counters=True),
                       dict(spec.to_dict(), extra=True)):
            with self.assertRaises(ValueError):
                FixedManaImpendingSpec.from_dict(edited)


    def test_only_the_selected_alternative_seals_payment_and_a_spell_copy_keeps_it(self):
        spec = FixedManaImpendingSpec(4, "{1}{G}{G}")
        option = spec.cast_cost_option()
        self.assertEqual({}, impending_payment_context({"id": "printed", "kind": "mana"}))
        context = impending_payment_context(option)
        self.assertEqual({PAID_IMPENDING_FIELD: spec.to_dict()}, context)
        with self.assertRaises(ValueError):
            impending_payment_context(dict(option, id="printed"))
        spell = SimpleNamespace(context=context, ref="S1", label="Original")
        copied = stack_copy_context(spell, {}, {}, permanent_spell=True, characteristics={})
        self.assertEqual(spec.to_dict(), copied[PAID_IMPENDING_FIELD])
        self.assertIsNot(context[PAID_IMPENDING_FIELD], copied[PAID_IMPENDING_FIELD])


    def test_paid_entry_yields_a_time_counter_instruction_for_the_resolving_incarnation(self):
        spec = FixedManaImpendingSpec(4, "{1}{U}{U}")
        context = impending_payment_context(spec.cast_cost_option())
        for kind in ("spell", "spell_copy"):
            with self.subTest(kind=kind):
                card = CardInstance(
                    object_id="card1", ref="public-card", oracle_id="fixture", printed_name="Fixture",
                    owner="A", controller="B", zone="stack", object_kind="spell_copy" if kind == "spell_copy" else "card",
                )
                item = StackItem("stack1", "S1", kind, "B", "Fixture", card_object_id=card.object_id, context=context)
                counter, = impending_entry_counters(item, card, "battlefield")
                self.assertEqual(("time", 4, "B", card.ref), (
                    counter.counter_name, counter.amount, counter.placing_player, counter.source_ref,
                ))
                self.assertEqual((), impending_entry_counters(item, card, "exile"))
                with self.assertRaises(StateInvariantError):
                    impending_entry_counters(item, None, "battlefield")
                card.controller = "C"
                with self.assertRaises(StateInvariantError):
                    impending_entry_counters(item, card, "battlefield")
                card.controller = "B"
                self.assertEqual({}, card.annotations)
                mark_resolving_cast_facts(card, item)
                card.zone = "battlefield"
                reset_card_after_zone_change(card, destination="battlefield", stack_to_battlefield=True)
                self.assertEqual(spec.to_dict(), card.annotations[PAID_IMPENDING_FIELD])
                card.zone = "graveyard"
                reset_card_after_zone_change(card, destination="graveyard", stack_to_battlefield=False)
                self.assertNotIn(PAID_IMPENDING_FIELD, card.annotations)


    def test_compiler_lowers_each_printed_price_and_lifetime(self):
        from quorune.carddb import CardDatabase
        from quorune.compiler.ir_model import OracleNode
        from quorune.impending_model import IMPENDING_CAPABILITY, IMPENDING_HANDLER_ID
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import load_default_capability_registry
        from quorune.semantic_runtime.impending import FixedManaImpendingHandler
        from quorune.semantic_runtime.context import SemanticNodeError
        from quorune.rules.counter_removal_capabilities import fixed_counter_removal_node_capabilities
        from quorune.rules.impending import impending_end_step_condition
        from tempfile import TemporaryDirectory
        from scripts.build_test_database import build_fixture_database

        fixture = Path(__file__).parent / "fixtures/impending-cards.json"
        originals = [card for card in json.loads(fixture.read_bytes())["cards"]
                     if card["oracle_text"].startswith("Impending ")]
        registry = load_default_capability_registry()
        with TemporaryDirectory() as directory:
            db_path = Path(directory) / "impending.sqlite3"
            build_fixture_database([fixture], db_path)
            with CardDatabase(db_path) as db:
                by_id = {record.oracle_id: record for record in db.iter_cards()}
                records = [by_id[original["oracle_id"]] for original in originals]
        for original, record in zip(originals, records, strict=True):
            with self.subTest(name=record.name):
                ir = compile_oracle_card(record, capability_registry=registry,
                                         capability_profile="commander_review")
                node, = (node for face in ir.faces for node in face.nodes
                         if node.template_id == "fixed-mana-impending-price-v1")
                self.assertIsInstance(node, OracleNode)
                self.assertTrue(node.lowerable)
                self.assertTrue(node.exact)
                self.assertIn(IMPENDING_CAPABILITY, node.capability_dependencies)
                layer, = (node for face in ir.faces for node in face.nodes
                          if node.template_id == "impending-creature-type-v1")
                self.assertEqual("characteristics.evaluate", layer.event)
                self.assertIn(IMPENDING_CAPABILITY, layer.capability_dependencies)
                self.assertTrue(layer.exact)
                end_step, = (node for face in ir.faces for node in face.nodes
                              if node.template_id == "impending-time-counter-end-step-v1")
                self.assertEqual(("step.begin", "battlefield"), (end_step.event, end_step.active_zone))
                self.assertEqual(impending_end_step_condition(), end_step.event_condition)
                self.assertTrue(end_step.exact)
                self.assertEqual(
                    {"op": "remove_counters", "card": "$source.zone_object", "counter": "time",
                     "amount": 1, "source": "$source"},
                    end_step.effects[0],
                )
                required = fixed_counter_removal_node_capabilities(
                    effects=end_step.effects, target_schema=end_step.target_schema, mechanic_ids=end_step.mechanics,
                )
                self.assertIn("counter.removal.fixed_source_effect", required)
                self.assertTrue(set(required).issubset(end_step.capability_dependencies))
                descriptor, = node.handlers
                self.assertEqual(IMPENDING_HANDLER_ID, descriptor["handler_id"])
                spec = FixedManaImpendingHandler().validate(descriptor)
                text = original["oracle_text"].splitlines()[0].split(" (", 1)[0]
                self.assertEqual(parse_fixed_mana_impending(text), spec)
                with self.assertRaises(SemanticNodeError):
                    FixedManaImpendingHandler().validate(dict(descriptor, requires_complete_card_program=False))

    def test_a_cast_price_requires_complete_admission_and_rejects_a_reserved_override(self):
        from quorune.card_programs.admission import CompleteCardProgramAdmission
        from quorune.impending_model import IMPENDING_COST_EVENT, IMPENDING_HANDLER_ID
        from quorune.rules.casting.costs import _with_impending_cost

        spec = FixedManaImpendingSpec(4, "{1}{U}{U}")
        component = {
            "handler_id": IMPENDING_HANDLER_ID, "schema_version": 1, "event": IMPENDING_COST_EVENT,
            "requires_complete_card_program": True, "impending": spec.to_dict(),
        }
        program = SimpleNamespace(handlers=(component,), provenance={"face_id": "front"})
        host = SimpleNamespace(
            semantic_program_is_current_trusted=lambda candidate: True,
            semantics=SimpleNamespace(runtime_handler_programs_for_oracle=lambda *args, **kwargs: (program,)),
        )
        record = SimpleNamespace(keywords=("Impending",), oracle_id="fixture")
        self.assertIsNone(_with_impending_cost(host, record, program, {}, suppress_source_costs=False))
        program.provenance["card_program_admission"] = CompleteCardProgramAdmission("exact", 0).to_dict()
        self.assertEqual({"alternate_costs": [spec.cast_cost_option()]},
                         _with_impending_cost(host, record, program, {}, suppress_source_costs=False))
        self.assertEqual({}, _with_impending_cost(host, record, program, {}, suppress_source_costs=True))
        self.assertIsNone(_with_impending_cost(
            host, record, program, {"alternate_costs": [spec.cast_cost_option()]},
            suppress_source_costs=False,
        ))


    def test_layer_condition_follows_live_paid_state_and_current_time_counters(self):
        from quorune.rules.impending import impending_time_counter_condition
        from quorune.continuous_conditions import FixedPublicStateConditionSnapshot
        from quorune.rules.impending import has_paid_impending_time_counter
        from quorune.semantic_runtime.continuous_components import ContinuousEffectSourceContext
        from quorune.semantic_runtime.fixed_characteristic_settings import (
            ConditionalCreatureTypeRemovalHandler,
            CREATURE_TYPE_REMOVAL_HANDLER,
        )

        source = CardInstance(
            object_id="impending", ref="public", oracle_id="fixture", printed_name="Fixture",
            owner="A", controller="B", zone="battlefield",
        )
        source.annotations[PAID_IMPENDING_FIELD] = FixedManaImpendingSpec(4, "{1}{U}{U}").to_dict()
        condition = impending_time_counter_condition()
        handler = ConditionalCreatureTypeRemovalHandler()
        descriptor = {
            "handler_id": CREATURE_TYPE_REMOVAL_HANDLER, "schema_version": 1,
            "event": "characteristics.evaluate", "source_condition": condition.to_dict(),
        }
        for time, paid, phased, expected in (
            (4, True, False, True), (0, True, False, False), (1, True, False, True),
            (4, False, False, False), (4, True, True, False),
        ):
            with self.subTest(time=time, paid=paid, phased=phased):
                source.counters = {"time": time} if time else {}
                source.phased_out = phased
                if paid:
                    source.annotations[PAID_IMPENDING_FIELD] = FixedManaImpendingSpec(4, "{1}{U}{U}").to_dict()
                else:
                    source.annotations.pop(PAID_IMPENDING_FIELD, None)
                quantity = int(has_paid_impending_time_counter(source))
                self.assertEqual(expected, bool(quantity))
                snapshot = FixedPublicStateConditionSnapshot(
                    "B", "A", 7, 0, 40, (40,), 1, 1, tuple(source.counters.items()),
                    condition_quantity=quantity,
                )
                context = ContinuousEffectSourceContext(
                    source_object_id=source.object_id, source_ref=source.ref, source_controller=source.controller,
                    source_timestamp=0, source_logical_object_id=source.logical_object_id,
                    component_id="impending-test", public_state=snapshot,
                )
                effects = handler.lower(descriptor, context)
                self.assertEqual(int(expected), len(effects))
                if expected:
                    self.assertEqual("remove_creature_card_type", effects[0].operations[0].op)
                    self.assertEqual(source.logical_object_id, effects[0].related_object.logical_object_id)


    def test_runtime_rejects_scope_condition_source_and_amount_drift(self):
        from quorune.ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
        from quorune.card_programs.impending import impending_program_dependencies
        from quorune.continuous_conditions import FIXED_PUBLIC_STATE_INTERVENING_COVERAGE
        from quorune.impending_model import IMPENDING_CAPABILITY
        from quorune.rules.impending import impending_end_step_condition, impending_end_step_effect

        program = {
            "event": "step.begin", "active_zone": "battlefield", "target_schema": None,
            "handlers": (), "event_condition": impending_end_step_condition(),
            "effects": (impending_end_step_effect(),),
            "coverage": (CURRENT_ABILITY_FRAGMENT_COVERAGE, "intervening_condition",
                         FIXED_PUBLIC_STATE_INTERVENING_COVERAGE),
        }
        self.assertIn(IMPENDING_CAPABILITY, impending_program_dependencies(SimpleNamespace(**program)))
        changed_timing = impending_end_step_condition()
        changed_timing["all"][1]["value"] = "upkeep"
        for change in (
            {"event": "permanent.enter"}, {"event_condition": changed_timing},
            {"active_zone": "stack"}, {"coverage": ()}, {"target_schema": {"count": 1}},
            {"effects": (dict(impending_end_step_effect(), amount=2),)},
            {"effects": (dict(impending_end_step_effect(), card="$target.0"),)},
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                impending_program_dependencies(SimpleNamespace(**{**program, **change}))


    def test_ordered_time_counter_result_changes_the_creature_entry_replacement(self):
        from quorune.replacement.entry_counter_types import ENTRY_TIME_COUNTER_TYPES_FIELD, EntryTimeCounterTypes
        from quorune.replacement.model import AffectedObject, ReplaceableEvent, ReplacementClass, ReplacementEffect
        from quorune.replacement.operations import CreateAffectedObjectCounter, HalveCounterPlacementAmount, SetField
        from quorune.replacement.ordering import apply_tree_replacement, replacement_tree_choice

        tags = EntryTimeCounterTypes(("avatar", "creature", "enchantment", "horror"), ("enchantment",))
        parent = ReplaceableEvent(
            event_id="entry", kind="zone.change", affected_player=None,
            affected_object=AffectedObject("object1", "A", "A"),
            payload={
                "destination": "battlefield", "destination_controller": "A", "object_ref": "obj",
                "object_types": tags.without_counter, "tapped": False,
                ENTRY_TIME_COUNTER_TYPES_FIELD: tags.to_dict(),
            },
        )
        impending = ReplacementEffect(
            effect_id="paid-entry", source_id="obj", event_kind="zone.change",
            replacement_class=ReplacementClass.SELF_REPLACEMENT,
            operations=(CreateAffectedObjectCounter("time", 1, "A", "obj"),),
        )
        halve = ReplacementEffect(
            effect_id="halve", source_id="half", event_kind="counter.place",
            replacement_class=ReplacementClass.OTHER, conditions={"counter_name": {"eq": "time"}},
            operations=(HalveCounterPlacementAmount(),),
        )
        tapped = ReplacementEffect(
            effect_id="creatures-tapped", source_id="tapper", event_kind="zone.change",
            replacement_class=ReplacementClass.OTHER, conditions={"object_types": {"contains": "creature"}},
            operations=(SetField("tapped", True),),
        )
        without_halve = (impending, tapped)
        first = replacement_tree_choice(parent, without_halve)
        self.assertEqual(("paid-entry",), first.choice.options)
        positive = apply_tree_replacement(parent, without_halve, first, "paid-entry")
        self.assertEqual(("enchantment",), positive.payload["object_types"])
        self.assertEqual(("enchantment",), positive.children[0].payload["target_types"])
        self.assertFalse(positive.payload["tapped"])
        self.assertIsNone(replacement_tree_choice(positive, without_halve))

        effects = (impending, halve, tapped)
        counter_choice = replacement_tree_choice(positive, effects)
        self.assertEqual(("halve",), counter_choice.choice.options)
        prevented = apply_tree_replacement(positive, effects, counter_choice, "halve")
        self.assertEqual(0, prevented.children[0].payload["amount"])
        self.assertEqual(tags.without_counter, prevented.payload["object_types"])
        final_choice = replacement_tree_choice(prevented, effects)
        self.assertEqual(("creatures-tapped",), final_choice.choice.options)
        tapped_entry = apply_tree_replacement(prevented, effects, final_choice, "creatures-tapped")
        self.assertTrue(tapped_entry.payload["tapped"])
        self.assertIsNone(replacement_tree_choice(tapped_entry, effects))


class ImpendingNativeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        db = Path(cls.temporary.name) / "impending-native.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/bound-effect-program-cards.json",
            ROOT / "tests/fixtures/impending-cards.json",
        ], db)
        cls.db = CardDatabase(db)
        cls.registry = load_default_capability_registry()
        commander = "Generic Bound Commander"
        cls.deck = DeckDefinition("Impending", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], [commander])

    def cast_printed_price(self, game, source, *, paid, reserve=None):
        self.priority(game, "A")
        action = self.action(game, "A", game.state.cards[source.object_id])
        prices = {option["id"] for option in action["cost_options"]}
        self.assertIn("impending", prices)
        price = "impending" if paid else next(name for name in prices if name != "impending")
        command = {"action_id": action["id"], "cost_option": price, "pay": "auto"}
        if reserve is not None:
            command["reserve"] = reserve
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:D", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        submitted = game.act("pilot:A", command)
        self.assertTrue(submitted.ok, submitted.summary)
        item = game.state.stack[-1]
        self.assertEqual(paid, PAID_IMPENDING_FIELD in item.context)
        self.assertIn("creature", self.types(game, game.state.cards[item.card_object_id]))
        return item

    def types(self, game, card):
        characteristics = game.engine._effective_card_data(game.state.cards[card.object_id])
        return game.engine._type_parts(characteristics["type_line"])[0]

    def assert_paid_entry(self, *, paid, prohibited, seed):
        game = self.session(seed)
        source = self.add(game.engine, "Overlord of the Mistmoors", zone="hand")
        tapper = self.add(game.engine, "Blind Obedience", seat="B")
        for card in (source, tapper):
            self.assert_closed(game, card)
        if prohibited:
            self.assert_closed(game, self.add(game.engine, "Solemnity", seat="C"))
        private = self.add(game.engine, "Generic Bound Plains", seat="D", zone="hand", ref="impending-private")
        self.prepare(game, mana={"C": 7, "W": 2})
        self.checkpoint(game)
        self.cast_printed_price(game, source, paid=paid)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        entrant = game.state.cards[source.object_id]
        has_counter = paid and not prohibited
        self.assertEqual("battlefield", entrant.zone)
        self.assertEqual(4 if has_counter else 0, entrant.counters.get("time", 0))
        self.assertEqual(paid, PAID_IMPENDING_FIELD in entrant.annotations)
        self.assertEqual(not has_counter, "creature" in self.types(game, entrant))
        self.assertEqual(not has_counter, entrant.tapped, "The paid type must govern the creature entry replacement")
        insects = [card for card in game.state.cards.values() if card.zone == "battlefield" and card.is_token
                   and card.controller == "A"]
        self.assertEqual(2, len(insects))
        self.assertTrue(all(card.tapped for card in insects))
        for seat in ("A", "B", "C"):
            self.assertNotIn(private.ref, json.dumps(game.packet("pilot:" + seat, full=True)))
        self.replay(game, load=True)

    def test_actual_impending_and_ordinary_entry_use_the_resolved_counter_and_type(self):
        for index, (paid, prohibited) in enumerate(((True, False), (True, True), (False, False))):
            with self.subTest(paid=paid, prohibited=prohibited):
                self.assert_paid_entry(paid=paid, prohibited=prohibited, seed=702176100 + index)

    def test_native_creature_entry_witness_kills_the_missing_type_projection(self):
        with patch("quorune.replacement.ordering.project_entry_time_counter_types", side_effect=lambda event: event):
            with self.assertRaisesRegex(AssertionError, "paid type must govern"):
                self.assert_paid_entry(paid=True, prohibited=False, seed=702176106)

    def test_actual_impending_spell_copy_retains_payment_and_survives_source_departure(self):
        game = self.session(702176111)
        source = self.add(game.engine, "Overlord of the Mistmoors", zone="hand")
        copier = self.add(game.engine, "Double Major", zone="hand")
        remover = self.add(game.engine, "Disenchant", seat="B", zone="hand")
        self.add(game.engine, "Generic Bound Plains", seat="B", ref="impending-white-one")
        self.add(game.engine, "Generic Bound Plains", seat="B", ref="impending-white-two")
        for card in (source, copier, remover):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 6, "W": 2, "U": 1, "G": 1})
        self.checkpoint(game)
        original = self.cast_printed_price(game, source, paid=True, reserve={"U": 1, "G": 1})
        self.cast_for(game, "A", copier, [original.ref])
        self.resolve_top(game)
        copied_spell = next(item for item in game.state.stack if item.kind == "spell_copy")
        self.assertEqual(original.context[PAID_IMPENDING_FIELD], copied_spell.context[PAID_IMPENDING_FIELD])
        copied_id = copied_spell.card_object_id
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        for object_id in (source.object_id, copied_id):
            entry = game.state.cards[object_id]
            self.assertEqual(("battlefield", 4), (entry.zone, entry.counters["time"]))
            self.assertEqual({"enchantment"}, self.types(game, entry))
        self.assertTrue(game.state.cards[copied_id].is_token)
        self.assertEqual(2, len(game.engine._current_turn_history("spell_cast")))

        self.until(game, lambda current: current.state.active_player == "A" and current.state.step == "end_step"
                   and current.state.pending_decision is not None and current.state.pending_decision.kind == "priority")
        self.assertEqual(2, sum(item.source_object_id in {source.object_id, copied_id}
                                for item in game.state.stack if item.kind == "triggered_ability"))
        self.cast_for(game, "B", remover, [game.state.cards[source.object_id].ref])
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        self.assertNotIn(PAID_IMPENDING_FIELD, game.state.cards[source.object_id].annotations)
        self.assertEqual(3, game.state.cards[copied_id].counters["time"])
        self.replay(game, load=True)
