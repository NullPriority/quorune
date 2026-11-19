"""Pinned cumulative-upkeep choices preserve two-card graveyard units."""

from dataclasses import FrozenInstanceError, replace
import unittest

from quorune.cumulative_graveyard_upkeep import GraveyardLibraryCostSpec, GraveyardLibraryCostUnit


class CumulativeGraveyardUpkeepModelTests(unittest.TestCase):
    def test_three_ages_accept_four_plus_two_and_refuse_five_plus_one(self):
        spec = GraveyardLibraryCostSpec()
        self.assertTrue(spec.can_pay(3, {'A': ('a1', 'a2', 'a3', 'a4'), 'B': ('b1', 'b2')}))
        self.assertFalse(spec.can_pay(3, {'A': ('a1', 'a2', 'a3', 'a4', 'a5'), 'B': ('b1',)}))
        self.assertEqual(6, spec.total_cards(3))

    def test_per_age_units_keep_order_and_same_graveyard_repetition_without_reusing_cards(self):
        spec = GraveyardLibraryCostSpec(); pools = {'A': ('a1', 'a2', 'a3', 'a4'), 'B': ('b1', 'b2')}
        units = (GraveyardLibraryCostUnit('A', ('a2', 'a1')), GraveyardLibraryCostUnit('B', ('b2', 'b1')),
                 GraveyardLibraryCostUnit('A', ('a4', 'a3')))
        self.assertEqual(units, spec.validate_units(3, units, pools))
        with self.assertRaises(ValueError):
            spec.validate_units(3, (units[0], units[1], units[0]), pools)

    def test_two_single_card_graveyards_do_not_combine_into_one_cost_unit(self):
        spec = GraveyardLibraryCostSpec(); pools = {'A': ('a1',), 'B': ('b1',)}
        self.assertFalse(spec.can_pay(1, pools))
        with self.assertRaises(ValueError):
            spec.validate_units(1, (GraveyardLibraryCostUnit('A', ('a1', 'b1')),), pools)

    def test_whole_cost_and_live_graveyard_membership_are_required_before_any_result(self):
        spec = GraveyardLibraryCostSpec(); unit = GraveyardLibraryCostUnit('A', ('a1', 'a2'))
        with self.assertRaises(ValueError):
            spec.validate_units(2, (unit,), {'A': unit.card_refs})
        with self.assertRaises(ValueError):
            spec.validate_units(1, (unit,), {'A': ('a1',), 'B': ('a2',)})
        with self.assertRaises(ValueError):
            spec.validate_units(1, (unit,), {'B': unit.card_refs})

    def test_zero_age_needs_no_cards_and_capacity_has_no_enumeration_ceiling(self):
        spec = GraveyardLibraryCostSpec()
        self.assertTrue(spec.can_pay(0, {})); self.assertEqual((), spec.validate_units(0, (), {}))
        pool = tuple('card-' + str(i) for i in range(512))
        self.assertTrue(spec.can_pay(256, {'B': pool})); self.assertFalse(spec.can_pay(257, {'B': pool}))

    def test_immutable_versions_and_malformed_pools_or_units_fail_closed(self):
        spec = GraveyardLibraryCostSpec(); self.assertEqual(spec, GraveyardLibraryCostSpec.from_dict(spec.to_dict()))
        with self.assertRaises(FrozenInstanceError):
            spec.unit_size = 3
        for change in ({'unit_size': True}, {'unit_size': 0}, {'schema_version': 1.0}, {'schema_version': 2}):
            with self.assertRaises(ValueError):
                replace(spec, **change)
        with self.assertRaises(ValueError):
            GraveyardLibraryCostSpec.from_dict({**spec.to_dict(), 'owner': 'A'})
        for pools in ({'A': ('same',), 'B': ('same',)}, {'A': ('a', 'a')}, {'A': ['a']}, {'': ()}):
            with self.assertRaises(ValueError):
                spec.can_pay(1, pools)
        for age in (True, -1, 1.0):
            with self.assertRaises(ValueError):
                spec.total_cards(age)
        with self.assertRaises(ValueError):
            GraveyardLibraryCostUnit('A', ('same', 'same'))

    def test_fresh_flat_card_count_mutant_cannot_pay_five_plus_one(self):
        from unittest.mock import patch
        def flat(spec, age, graveyards):return sum(len(refs) for refs in graveyards.values()) >= spec.total_cards(age)
        with patch.object(GraveyardLibraryCostSpec, 'can_pay', flat):
            result = unittest.TestResult()
            type(self)('test_three_ages_accept_four_plus_two_and_refuse_five_plus_one').run(result)
        self.assertFalse(result.errors, result.errors); self.assertTrue(result.failures)


class GroupedGraveyardCostIntentTests(unittest.TestCase):
    def batch(self):
        from quorune.library_order_model import LibraryArrivalOrder
        from quorune.semantic_runtime.graveyard_library_cost_intent import GraveyardLibraryCostBatchIntent
        return GraveyardLibraryCostBatchIntent(actor='A', object_refs=('b2', 'b1', 'c1', 'c2'),
            expected_zones=('graveyard',), destination='library', reason='Grouped upkeep payment',
            orders=(LibraryArrivalOrder('B', 'bottom', ('b2', 'b1')),
                    LibraryArrivalOrder('C', 'bottom', ('c1', 'c2'))),
            incarnations={ref: ref + '@first-graveyard' for ref in ('b1', 'b2', 'c1', 'c2')})

    def test_identity_keeps_payer_owners_each_unit_order_and_exact_public_incarnations(self):
        from quorune.semantic_runtime.graveyard_library_cost_intent import GraveyardLibraryCostBatchIntent
        cost = self.batch(); wire = cost.identity()
        self.assertEqual(cost, GraveyardLibraryCostBatchIntent.from_identity(wire))
        self.assertEqual(('B', 'C'), tuple(order.chooser for order in cost.orders))
        self.assertEqual(('b2', 'b1'), cost.orders[0].members)
        self.assertEqual('A', wire['actor']); self.assertEqual(2, wire['unit_size'])
        with self.assertRaises(TypeError):
            cost.incarnations['b1'] = 'later'

    def test_wrong_size_overlapping_units_unpinned_members_and_changed_movement_scope_reject(self):
        cost = self.batch()
        for changes in ({'unit_size': True}, {'cost_batch_version': True}, {'unit_size': 3},
                        {'orders': ()}, {'orders': cost.orders * 2}, {'destination': 'exile'},
                        {'expected_zones': ('hand',)}, {'owned_only': True},
                        {'incarnations': {'b1': 'b1@0'}}, {'object_refs': ('b1', 'b2', 'c1', 'c2')}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(cost, **changes)

    def test_resumed_cost_requires_event_scoped_replacement_choices_and_closed_identity(self):
        from quorune.semantic_runtime.graveyard_library_cost_intent import GraveyardLibraryCostBatchIntent
        cost = self.batch()
        selected = replace(cost, replacement_selections=({'event_id': 'second-unit-card', 'effect_id': 'redirect'},))
        self.assertEqual(cost.identity(), selected.identity())
        for selections in (('unscoped-effect',), ({'effect_id': 'redirect'},), ({'event_id': ''},)):
            with self.assertRaises(ValueError):
                replace(cost, replacement_selections=selections)
        with self.assertRaises(ValueError):
            GraveyardLibraryCostBatchIntent.from_identity({**cost.identity(), 'source': 'other'})

    def test_single_pending_replacement_is_pinned_without_changing_old_unscoped_choices(self):
        from types import SimpleNamespace
        from quorune.replacement_decisions import _scope_replayed_event_choice
        host = SimpleNamespace(state=SimpleNamespace(effect_destruction_replacement_version=None))
        pending = SimpleNamespace(event_id='later-unit-event')
        cost = SimpleNamespace(resume_kind='semantic_intent_completion', semantic_intent_kind='graveyard_library_cost_batch')
        selected = _scope_replayed_event_choice(host, cost, pending, 'commander-option')
        self.assertEqual({'effect_id': 'commander-option', 'event_id': 'later-unit-event'}, selected)
        old = SimpleNamespace(resume_kind='semantic_intent_completion', semantic_intent_kind='move_objects_simultaneously')
        self.assertEqual('commander-option', _scope_replayed_event_choice(host, old, pending, 'commander-option'))

    def test_event_namespace_is_independent_of_private_permutations_and_changes_with_resource_incarnation(self):
        from quorune.library_order_model import LibraryArrivalOrder
        from quorune.semantic_choices.graveyard_library_cost import cost_event_namespace
        cost = self.batch()
        reversed_orders = tuple(LibraryArrivalOrder(order.chooser, 'bottom', tuple(reversed(order.members))) for order in cost.orders)
        equivalent = replace(cost, orders=reversed_orders,
            object_refs=tuple(ref for order in reversed_orders for ref in order.members))
        self.assertEqual(cost_event_namespace(cost), cost_event_namespace(equivalent))
        newer = replace(cost, incarnations={**dict(cost.incarnations), 'b1': 'b1@later'})
        self.assertNotEqual(cost_event_namespace(cost), cost_event_namespace(newer))


class GroupedGraveyardCostCommitTests(unittest.TestCase):
    batch = GroupedGraveyardCostIntentTests.batch
    def host(self):
        from contextlib import contextmanager
        from copy import deepcopy
        from types import SimpleNamespace
        cost = self.batch()
        class Host:
            def __init__(host):
                host.state = SimpleNamespace(history=[], cards={ref: SimpleNamespace(
                    object_id=ref, ref=ref, owner=ref[0].upper(), object_kind='card',
                    zone='graveyard', logical_object_id=cost.incarnations[ref]) for ref in cost.object_refs})
            @contextmanager
            def transaction(host):
                before = deepcopy(host.state)
                try:yield
                except Exception:
                    host.state = before; raise
            def _require_seat(host, seat, *, in_game):
                if seat not in ('A', 'B', 'C'):raise ValueError('Unknown seat')
            def _resolve_object(host, actor, ref, *, zones):
                card = host.state.cards[ref]
                if card.zone not in zones:raise ValueError('Unexpected zone')
                return card
        return Host(), cost

    def pipeline(self, host, *, pause_second=False, fault_second=False):
        from contextlib import ExitStack
        from types import SimpleNamespace
        from unittest.mock import patch
        class Pause(Exception):pass
        snapshots = []
        def capture(actual, changes, **kwargs):
            self.assertIs(actual, host)
            snapshot = SimpleNamespace(changes=changes, preceding=tuple(host.state.history))
            snapshots.append(snapshot); return snapshot
        def events(snapshot):
            return tuple(SimpleNamespace(event_id=ref) for ref, _ in snapshot.changes)
        def prepare(snapshot, *, selections, **kwargs):
            if pause_second and snapshot.preceding and not selections:raise Pause('Choose second-unit replacement')
            return {ref: 'prepared:' + ref for ref, _ in snapshot.changes}
        def move(changes, *, prepared_replacements, library_order, **kwargs):
            self.assertEqual({ref for ref, _ in changes}, set(prepared_replacements))
            self.assertEqual(tuple(sorted(library_order.members)), tuple(ref for ref, _ in changes))
            host.state.history.append((library_order.chooser, library_order.members))
            for ref, _ in changes:host.state.cards[ref].zone = 'library'
            if fault_second and len(host.state.history) == 2:raise RuntimeError('Second-unit commit fault')
        stack = ExitStack()
        stack.enter_context(patch('quorune.semantic_runtime.zone_replacements.capture_zone_change_replacement_snapshot', capture))
        stack.enter_context(patch('quorune.semantic_runtime.zone_replacements.zone_change_snapshot_events', events))
        stack.enter_context(patch('quorune.semantic_runtime.zone_replacements.prepare_zone_change_replacement_snapshot', prepare))
        owner = stack.enter_context(patch('quorune.zone_transitions.ZoneTransitionOwner'))
        owner.return_value.move_cards_simultaneously.side_effect = move
        return stack, snapshots, Pause

    def test_cost_uses_canonical_units_in_order_and_later_preparation_sees_prior_unit_results(self):
        from quorune.semantic_choices.graveyard_library_cost import commit_graveyard_library_cost
        host, cost = self.host(); stack, snapshots, _ = self.pipeline(host)
        with stack:self.assertEqual(('b1', 'b2', 'c1', 'c2'), commit_graveyard_library_cost(host, cost))
        self.assertEqual(2, len(snapshots)); self.assertEqual((), snapshots[0].preceding)
        self.assertEqual((('B', ('b2', 'b1')),), snapshots[1].preceding)
        self.assertEqual([('B', ('b2', 'b1')), ('C', ('c1', 'c2'))], host.state.history)

    def test_second_unit_pause_rolls_back_every_move_and_replays_whole_cost_with_its_saved_choice(self):
        from quorune.semantic_choices.graveyard_library_cost import commit_graveyard_library_cost
        from quorune.semantic_choices.graveyard_library_cost import cost_event_namespace
        host, cost = self.host(); stack, _, Pause = self.pipeline(host, pause_second=True)
        with stack, self.assertRaises(Pause):commit_graveyard_library_cost(host, cost)
        self.assertEqual([], host.state.history)
        self.assertTrue(all(card.zone == 'graveyard' for card in host.state.cards.values()))
        saved = replace(cost, replacement_selections=({'event_id': f'graveyard.cost:{cost_event_namespace(cost)}:1:c1', 'effect_id': 'redirect'},))
        stack, _, _ = self.pipeline(host, pause_second=True)
        with stack:commit_graveyard_library_cost(host, saved)
        self.assertEqual(2, len(host.state.history))

    def test_commit_fault_or_stale_original_or_unused_replacement_cannot_partially_pay(self):
        from quorune.errors import GameRuleError
        from quorune.semantic_choices.graveyard_library_cost import commit_graveyard_library_cost
        host, cost = self.host(); stack, _, _ = self.pipeline(host, fault_second=True)
        with stack, self.assertRaises(RuntimeError):commit_graveyard_library_cost(host, cost)
        self.assertEqual([], host.state.history)
        host.state.cards['c1'].logical_object_id = 'c1@later'
        stack, snapshots, _ = self.pipeline(host)
        with stack, self.assertRaises(GameRuleError):commit_graveyard_library_cost(host, cost)
        self.assertEqual([], snapshots)
        host, cost = self.host(); stack, _, _ = self.pipeline(host)
        stale = replace(cost, replacement_selections=({'event_id': 'other-card', 'effect_id': 'redirect'},))
        with stack, self.assertRaises(GameRuleError):commit_graveyard_library_cost(host, stale)
        self.assertEqual([], host.state.history)


class CumulativeGraveyardUpkeepChoiceTests(unittest.TestCase):
    def world(self, pools=None):
        from types import SimpleNamespace
        source = SimpleNamespace(ref='S1', logical_object_id='S1@1', zone='battlefield',
            phased_out=False, controller='A', counters={'age': 0})
        cards = {'S1': source}
        for owner, refs in (pools or {'A': ('a1', 'a2', 'a3', 'a4'), 'B': ('b1', 'b2')}).items():
            for ref in refs:
                cards[ref] = SimpleNamespace(ref=ref, logical_object_id=ref + '@graveyard', owner=owner,
                    controller=owner, zone='graveyard', token=False, phased_out=False, known_to_actor=True)
        query = SimpleNamespace(active_seats=('A', 'B', 'C', 'D'),
            object=lambda ref, zones=None: cards.get(ref) if ref in cards and (not zones or cards[ref].zone in zones) else None,
            objects=lambda *, zones: tuple(row for row in cards.values() if row.zone in zones))
        context = SimpleNamespace(actor='A', stack_controller='A', stack_ref='STACK1', stack_label='Upkeep',
            source_ref='S1', source_logical_object_id='S1@1', query=query)
        effect = {**GraveyardLibraryCostSpec().effect(), 'player': 'A', 'source': 'S1'}
        return context, source, cards, effect

    def continuation(self, prepared):
        from types import SimpleNamespace
        return SimpleNamespace(effect=prepared.continuation_effect, stack_ref='STACK1',
                               semantic_frame=SimpleNamespace(controller='A'))

    def offer(self, context, effect, *, actor=None):
        from types import SimpleNamespace
        from quorune.semantic_choices.cumulative_graveyard_upkeep import prepare_cumulative_graveyard_upkeep
        chooser = SimpleNamespace(**{**vars(context), 'actor': actor or effect['player']})
        return prepare_cumulative_graveyard_upkeep(effect, chooser)

    def choose(self, context, effect, response):
        from quorune.semantic_choices.cumulative_graveyard_upkeep import complete_cumulative_graveyard_upkeep
        offered = self.offer(context, effect)
        return complete_cumulative_graveyard_upkeep(self.continuation(offered), response, context.query)

    def price(self, context, source, effect, age):
        initial = self.offer(context, effect)
        self.assertEqual('age', initial.preparation_intents[0].counter_name)
        self.assertEqual(0, source.counters['age'])
        source.counters['age'] = age
        return self.offer(context, initial.auto_continue.prepend_effects[0]).continuation_effect

    def test_complete_three_unit_proposals_then_private_owner_orders_create_one_atomic_batch(self):
        from quorune.semantic_runtime.graveyard_library_cost_intent import GraveyardLibraryCostBatchIntent
        context, source, cards, effect = self.world(); effect = self.price(context, source, effect, 3)
        for owner, refs in (('A', ['a1', 'a2']), ('A', ['a3', 'a4']), ('B', ['b1', 'b2'])):
            proposal = self.choose(context, effect, {'choice': owner}); self.assertFalse(proposal.intents)
            proposal = self.choose(context, proposal.repeat_effect, {'cards': refs}); self.assertFalse(proposal.intents)
            self.assertTrue(all(card.zone == 'graveyard' for ref, card in cards.items() if ref != 'S1'))
            effect = proposal.repeat_effect
        source.counters['age'] = 50
        for owner, refs in (('A', ['a2', 'a1']), ('A', ['a4', 'a3']), ('B', ['b2', 'b1'])):
            offered = self.offer(context, effect); self.assertEqual(owner, offered.continuation_effect['player'])
            self.assertEqual('actor_private', offered.request.choice.visibility)
            proposal = self.choose(context, effect, {'cards': refs})
            if proposal.repeat_effect is not None:effect = proposal.repeat_effect
        intent, = proposal.intents; self.assertIsInstance(intent, GraveyardLibraryCostBatchIntent)
        self.assertEqual('A', intent.actor)
        self.assertEqual((('a2', 'a1'), ('a4', 'a3'), ('b2', 'b1')), tuple(order.members for order in intent.orders))
        self.assertEqual(('A', 'A', 'B'), tuple(order.chooser for order in intent.orders))

    def test_five_plus_one_and_two_singletons_offer_only_decline_for_unpayable_full_cost(self):
        for age, pools in ((3, {'A': ('a1', 'a2', 'a3', 'a4', 'a5'), 'B': ('b1',)}),
                           (1, {'A': ('a1',), 'B': ('b1',)})):
            context, source, _, effect = self.world(pools); effect = self.price(context, source, effect, age)
            offered = self.offer(context, effect)
            self.assertEqual(('decline',), offered.request.choice.legal_values)
            with self.assertRaises(ValueError):self.choose(context, effect, {'choice': 'A'})

    def test_decline_after_one_unit_does_not_move_graveyards_and_only_sacrifices_original_source(self):
        context, source, cards, effect = self.world(); effect = self.price(context, source, effect, 2)
        selected = self.choose(context, effect, {'choice': 'A'})
        selected = self.choose(context, selected.repeat_effect, {'cards': ['a1', 'a2']})
        declined = self.choose(context, selected.repeat_effect, {'choice': 'decline'})
        self.assertEqual(('S1',), declined.intents[0].object_refs)
        self.assertEqual('graveyard', declined.intents[0].destination)
        self.assertTrue(all(card.zone == 'graveyard' for ref, card in cards.items() if ref != 'S1'))
        source.logical_object_id = 'S1@recast'
        self.assertFalse(self.choose(context, selected.repeat_effect, {'choice': 'decline'}).intents)

    def test_zero_age_retains_free_payment_and_decline_without_any_graveyard_cards(self):
        context, source, cards, effect = self.world(); cards.clear(); cards['S1'] = source
        effect = self.price(context, source, effect, 0)
        self.assertFalse(self.choose(context, effect, {'pay': True}).intents)
        self.assertEqual(('S1',), self.choose(context, effect, {'pay': False}).intents[0].object_refs)
        with self.assertRaises(ValueError):self.choose(context, effect, {'pay': 1})

    def test_selected_owner_ref_and_incarnation_remain_bound_through_owner_handoff(self):
        context, source, cards, effect = self.world(); effect = self.price(context, source, effect, 1)
        selected = self.choose(context, effect, {'choice': 'B'})
        with self.assertRaises(ValueError):self.choose(context, selected.repeat_effect, {'cards': ['b1', 'a1']})
        selected = self.choose(context, selected.repeat_effect, {'cards': ['b1', 'b2']})
        with self.assertRaises(ValueError):self.offer(context, selected.repeat_effect, actor='A')
        with self.assertRaises(ValueError):self.choose(context, selected.repeat_effect, {'cards': ['b1', 'a1']})
        cards['b1'].logical_object_id = 'b1@later'
        with self.assertRaises(ValueError):self.offer(context, selected.repeat_effect)

    def test_grouped_upkeep_grammar_and_wrapper_refuse_targets_riders_versions_and_unknown_fields(self):
        from quorune.cumulative_graveyard_upkeep import compile_graveyard_library_upkeep
        text = "Cumulative upkeep—Put two cards from a single graveyard on the bottom of their owner's library."
        spec = compile_graveyard_library_upkeep(text); self.assertIsNotNone(spec)
        self.assertEqual(spec, GraveyardLibraryCostSpec.from_effect(spec.effect()))
        for version in (True, 1, 7, 8, 9, 10, 12):
            with self.assertRaises(ValueError):GraveyardLibraryCostSpec.from_effect({**spec.effect(), 'schema_version': version})
        for changed in (text.replace('two cards', 'two target cards'), text + ' Draw a card.',
                        text.replace('single graveyard', 'graveyard or hand'), text.replace('bottom', 'top')):
            self.assertIsNone(compile_graveyard_library_upkeep(changed))
        with self.assertRaises(ValueError):GraveyardLibraryCostSpec.from_effect({**spec.effect(), 'extra': True})

    def test_batch_replacement_identity_is_closed_and_preserves_every_private_order(self):
        from quorune.semantic_choices.intent_replacement import semantic_intent_identity, validate_semantic_intent_identity, with_replacement_selections
        cost = GroupedGraveyardCostIntentTests().batch(); kind, wire = semantic_intent_identity(cost)
        self.assertEqual('graveyard_library_cost_batch', kind)
        self.assertEqual(wire, validate_semantic_intent_identity(kind, wire))
        selected = with_replacement_selections(cost, ({'event_id': 'c1', 'effect_id': 'redirect'},))
        self.assertEqual(wire, semantic_intent_identity(selected)[1]); self.assertEqual(cost.orders, selected.orders)
        with self.assertRaises(ValueError):validate_semantic_intent_identity(kind, {**wire, 'extra': True})


from inherited_composition_fixture import InheritedNativeFixture
import test_target_recipient_results as recipients


class CumulativeGraveyardUpkeepFixture(InheritedNativeFixture):
    @classmethod
    def setUpClass(cls):
        import tempfile
        from pathlib import Path
        from common import ROOT
        from quorune.carddb import CardDatabase
        from quorune.deck import DeckDefinition, DeckEntry
        from quorune.rules.capabilities import load_default_capability_registry
        from scripts.build_test_database import build_fixture_database
        cls.temporary = tempfile.TemporaryDirectory(); path = Path(cls.temporary.name) / 'grouped-graveyard-upkeep.sqlite3'
        build_fixture_database([ROOT / 'tests/fixtures/cumulative-graveyard-upkeep-cards.json'], path)
        cls.db = CardDatabase(path); cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Grouped graveyard upkeep',
            [DeckEntry('Sliver Hivelord', 1, 'commander'), DeckEntry('Generic Bound Plains', 30)], ['Sliver Hivelord'])


class CumulativeGraveyardUpkeepCompilerTests(CumulativeGraveyardUpkeepFixture, unittest.TestCase):
    def test_complete_original_closes_and_raw_data_retains_both_cost_and_source_owners(self):
        from quorune.oracle_ir import compile_oracle_card, register_generated_programs
        from quorune.semantics import SemanticRegistry
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.card_programs.binding import bind_card_program_runtime, bind_semantic_program_runtime
        from quorune.cumulative_graveyard_upkeep import CAPABILITY, COST_CAPABILITY
        card = self.db.lookup('Jötun Grunt', fuzzy=False); semantics = SemanticRegistry()
        register_generated_programs(self.db, semantics, [card], trust_level='provisional',
            capability_registry=self.registry, capability_profile='commander_review',
            promote_exact_runtime_handlers=True, promote_exact_trigger_programs=True,
            promote_exact_effect_programs=True, promote_exact_capability_declarations=True)
        ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile='commander_review')
        self.assertEqual('exact', ir.status, ir.material_residuals)
        program = compile_best_available_card_program(self.db, card, semantic_registry=semantics,
            capability_registry=self.registry, capability_profile='commander_review')
        bound = bind_card_program_runtime(program, capability_registry=self.registry, profile='commander_review')
        self.assertTrue(bound['strict_capability_ready'], bound['blockers'])
        trigger = next(p for p in semantics.programs_for_oracle(card.oracle_id) if p.event == 'step.begin')
        for missing in (CAPABILITY, COST_CAPABILITY):
            deps = tuple(c for c in trigger.capability_dependencies if c != missing)
            changed = replace(trigger, coverage=(), handlers=(), capability_dependencies=deps,
                capability_closure=self.registry.closure(deps, profile='commander_review').to_dict())
            result = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile='commander_review')
            self.assertFalse(result['strict']); self.assertTrue(any(missing in b for b in result['blockers']), result['blockers'])

    def test_genuine_grouped_cost_withdrawal_preserves_old_costs_and_unrelated_ordinary_cards(self):
        import json
        from common import ROOT
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests
        from quorune.cumulative_graveyard_upkeep import COST_CAPABILITY
        raw = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        next(row for row in raw['capabilities'] if row['id'] == COST_CAPABILITY).update(status='blocked', blockers=['grouped library cost withdrawn'])
        registry = CapabilityRegistry(raw)
        index = build_index(registry_value=raw, declaration_source=read_evidence_document(ROOT / 'platform/capability-evidence-declarations.json'),
                            discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        self.assertNotEqual('exact', compile_oracle_card(self.db.lookup('Jötun Grunt'), capability_registry=registry, capability_profile='commander_review').status)
        for name in ('Memnite', 'Boomerang', 'Tormod, the Desecrator'):
            self.assertEqual('exact', compile_oracle_card(self.db.lookup(name), capability_registry=registry, capability_profile='commander_review').status)


class GroupedGraveyardCostSnapshotTests(CumulativeGraveyardUpkeepFixture, unittest.TestCase):
    def test_explicit_replacement_event_ids_keep_current_payload_and_coordinates_and_old_default(self):
        from quorune.errors import GameRuleError
        from quorune.record import authoritative_state_hash
        from quorune.semantic_runtime.zone_replacements import (
            capture_zone_change_replacement_snapshot, prepare_zone_change_replacement_snapshot,
        )
        s = self.session(70224901)
        cards = [self.add(s.engine, name, seat='B', zone='graveyard', ref=f'stable-event-{i}')
                 for i, name in enumerate(('Memnite', 'Ornithopter'))]
        self.prepare(s); before = authoritative_state_hash(s.state)
        snapshot = capture_zone_change_replacement_snapshot(s.engine, tuple((c.object_id, 'library') for c in cards))
        old = prepare_zone_change_replacement_snapshot(snapshot)
        stable = prepare_zone_change_replacement_snapshot(snapshot,
            event_ids={c.ref: f'cost-public-event-{i}' for i, c in enumerate(cards)})
        for i, card in enumerate(cards):
            self.assertTrue(old[card.object_id].event.event_id.startswith('zone.change:'))
            self.assertEqual(f'cost-public-event-{i}', stable[card.object_id].event.event_id)
            self.assertEqual(old[card.object_id].event.payload, stable[card.object_id].event.payload)
            self.assertEqual(s.state.revision, stable[card.object_id].state_revision)
        for ids in ({}, {c.ref: 'duplicate' for c in cards}, {cards[0].ref: True, cards[1].ref: 'valid'}):
            with self.assertRaises(GameRuleError):
                prepare_zone_change_replacement_snapshot(snapshot, error_type=GameRuleError, event_ids=ids)
        self.assertEqual(before, authoritative_state_hash(s.state))

    def test_explicit_single_commander_event_accepts_its_exact_scoped_choice_and_keeps_old_default(self):
        from quorune.commander_zones import pending_commander_zone_state_choices, commit_commander_zone_choice_decline
        from quorune.replacement_effects import ReplacementChoiceRequired
        from quorune.semantic_runtime.zone_replacements import capture_zone_change_replacement_snapshot, prepare_zone_change_replacement_snapshot
        s = self.session(70224902); player = s.state.players['B']
        commander = s.state.cards[player.zones['command'].pop()]
        commander.zone = 'graveyard'; player.zones['graveyard'].append(commander.object_id)
        choice, = pending_commander_zone_state_choices((commander,), active_seats=s.engine.active_seats, apnap_order=s.engine.apnap_order())
        commit_commander_zone_choice_decline(commander, choice)
        card = self.add(s.engine, 'Memnite', seat='B', zone='graveyard', ref='singleton-cost-control')
        self.prepare(s)
        snapshot = capture_zone_change_replacement_snapshot(s.engine,
            ((commander.object_id, 'library'), (card.object_id, 'library')))
        ids = {commander.ref: 'public-cost-commander', card.ref: 'public-cost-control'}
        with self.assertRaises(ReplacementChoiceRequired) as caught:
            prepare_zone_change_replacement_snapshot(snapshot, event_ids=ids)
        selected = next(value for value in caught.exception.pending.choice.legal_selections if not value.startswith('decline:'))
        prepared = prepare_zone_change_replacement_snapshot(snapshot, event_ids=ids,
            selections=({'event_id': ids[commander.ref], 'effect_id': selected},))
        self.assertEqual('command', prepared[commander.object_id].destination)
        old = prepare_zone_change_replacement_snapshot(snapshot, selections=(selected,))
        self.assertEqual('command', old[commander.object_id].destination)
        self.assertEqual('graveyard', commander.zone)


class CumulativeGraveyardUpkeepNativeTests(CumulativeGraveyardUpkeepFixture, unittest.TestCase):
    reject = recipients.TargetRecipientNativeTests.reject
    finish = recipients.TargetRecipientNativeTests.finish

    def world(self, seed, *, age=0, cast=False, pools=None):
        from quorune.model import TurnEntry
        s = self.session(seed); source = self.add(s.engine, 'Jötun Grunt', zone='hand' if cast else 'battlefield')
        source.counters['age'] = age; private = self.add(s.engine, 'Counterspell', seat='C', zone='hand', ref='graveyard-cost-private-C')
        cards = {}; names = ('Memnite', 'Ornithopter', 'Steel Wall', 'Phyrexian Walker', 'Shield Sphere', 'Ornithopter of Paradise')
        for owner, count in (pools if pools is not None else {'B': 2}).items():
            cards[owner] = [self.add(s.engine, names[i], seat=owner, zone='graveyard', ref=f'cost-{owner}-{i}') for i in range(count)]
        s.state.current_turn = TurnEntry('graveyard-cost-initial-D', 'D'); s.state.last_normal_turn_player = 'D'
        self.assert_closed(s, source); return s, source, private, cards

    def start(self, s, source, *, cast=False):
        self.prepare(s, actor='D', active='D'); s.state.players['A'].mana_pool.update({'C': 3, 'W': 2})
        self.checkpoint(s)
        if cast:self.cast_for(s, 'A', source); self.assertIsNone(self.resolve(s))
        self.until(s, lambda ss: ss.state.active_player == 'A' and ss.state.phase == 'beginning' and ss.state.step == 'upkeep')

    def choose(self, s, seat, **response):
        command = {'action_id': 'choose', **response}; self.reject(s, 'D', command)
        result = s.act('pilot:' + seat, command); self.assertTrue(result.ok, result.summary)
        return self.resolve(s)

    def select_units(self, s, units):
        all_cards = tuple(card for _, cards in units for card in cards)
        for owner, cards in units:
            self.assertEqual(['A'], s.state.pending_decision.actors); self.choose(s, 'A', choice=owner)
            self.choose(s, 'A', cards=[card.ref for card in cards])
            self.assertTrue(all(s.state.cards[card.object_id].zone == 'graveyard' for card in all_cards))

    def order_units(self, s, units, *, reverse=True):
        decision = None
        for owner, cards in units:
            self.assertEqual([owner], s.state.pending_decision.actors)
            ordered = list(reversed(cards)) if reverse else list(cards)
            decision = self.choose(s, owner, cards=[card.ref for card in ordered])
        return decision

    def test_actual_printed_cast_pays_nontarget_opponent_graveyard_and_owner_orders_privately(self):
        s, source, private, cards = self.world(70224600, cast=True)
        self.add(s.engine, 'Vedalken Orrery'); seal = self.add(s.engine, 'Ground Seal'); self.assert_closed(s, seal)
        before = len(s.state.players['B'].zones['library']); self.start(s, source, cast=True)
        self.assertEqual('semantic.choice', self.resolve(s).kind); units = [('B', cards['B'])]
        self.select_units(s, units); self.assertIsNone(s.packet('pilot:A', full=True)['decision'])
        s = self.replay(s, load=True); self.assertIsNone(self.order_units(s, units))
        self.assertEqual(before + 2, len(s.state.players['B'].zones['library']))
        self.assertEqual([c.object_id for c in reversed(cards['B'])], s.state.players['B'].zones['library'][:2])
        self.assertEqual('battlefield', s.state.cards[source.object_id].zone); self.finish(s, private)

    def test_actual_three_ages_accept_four_plus_two_with_owner_handoffs_and_no_partial_payment(self):
        import json
        s, source, private, cards = self.world(70224610, age=2, pools={'B': 4, 'C': 2})
        self.start(s, source); self.assertEqual('semantic.choice', self.resolve(s).kind)
        units = [('B', cards['B'][:2]), ('C', cards['C']), ('B', cards['B'][2:])]
        self.select_units(s, units); s = self.replay(s, load=True)
        self.choose(s, 'B', cards=[card.ref for card in reversed(cards['B'][:2])])
        self.assertTrue(all(s.state.cards[c.object_id].zone == 'graveyard' for rows in cards.values() for c in rows))
        self.assertNotIn(json.dumps([c.ref for c in reversed(cards['B'][:2])]), json.dumps(s.packet('pilot:A', full=True)))
        self.choose(s, 'C', cards=[card.ref for card in reversed(cards['C'])])
        self.assertIsNone(self.choose(s, 'B', cards=[card.ref for card in reversed(cards['B'][2:])]))
        expected = [c.object_id for c in reversed(cards['B'][2:])] + [c.object_id for c in reversed(cards['B'][:2])]
        self.assertEqual(expected, s.state.players['B'].zones['library'][:4]); self.finish(s, private)

    def test_actual_five_plus_one_is_unpayable_and_decline_does_not_move_any_cost_cards(self):
        s, source, private, cards = self.world(70224620, age=2, pools={'A': 5, 'B': 1})
        self.start(s, source); self.assertEqual('semantic.choice', self.resolve(s).kind)
        self.reject(s, 'A', {'action_id': 'choose', 'choice': 'A'})
        self.assertIsNone(self.choose(s, 'A', choice='decline'))
        self.assertEqual('graveyard', s.state.cards[source.object_id].zone)
        self.assertTrue(all(s.state.cards[c.object_id].zone == 'graveyard' for rows in cards.values() for c in rows)); self.finish(s, private)

    def test_actual_decline_after_a_unit_proposal_pays_nothing(self):
        s, source, private, cards = self.world(70224630, age=1, pools={'A': 2, 'B': 2})
        self.start(s, source); self.assertEqual('semantic.choice', self.resolve(s).kind)
        self.choose(s, 'A', choice='A'); self.choose(s, 'A', cards=[c.ref for c in cards['A']])
        self.assertIsNone(self.choose(s, 'A', choice='decline'))
        self.assertEqual('graveyard', s.state.cards[source.object_id].zone)
        self.assertTrue(all(s.state.cards[c.object_id].zone == 'graveyard' for rows in cards.values() for c in rows)); self.finish(s, private)

    def test_actual_zero_age_still_offers_free_payment_or_original_source_sacrifice(self):
        for pay in (True, False):
            with self.subTest(pay=pay):
                s, source, private, cards = self.world(70224640 + int(pay), pools={})
                ban = self.add(s.engine, 'Solemnity'); self.assert_closed(s, ban)
                self.start(s, source); self.assertEqual('semantic.choice', self.resolve(s).kind)
                self.assertIsNone(self.choose(s, 'A', pay=pay))
                self.assertEqual('battlefield' if pay else 'graveyard', s.state.cards[source.object_id].zone)
                self.assertEqual(0, s.state.cards[source.object_id].counters.get('age', 0)); self.finish(s, private)

    def test_actual_later_unit_commander_replacement_suspends_before_all_payment_and_replays(self):
        from quorune.commander_zones import pending_commander_zone_state_choices, commit_commander_zone_choice_decline
        s, source, private, cards = self.world(70224650, age=1, pools={'A': 2, 'B': 1})
        player = s.state.players['B']; commander = s.state.cards[player.zones['command'].pop()]
        commander.zone = 'graveyard'; player.zones['graveyard'].append(commander.object_id)
        decline, = pending_commander_zone_state_choices((commander,), active_seats=s.engine.active_seats, apnap_order=s.engine.apnap_order())
        commit_commander_zone_choice_decline(commander, decline); cards['B'].append(commander)
        self.start(s, source); self.assertEqual('semantic.choice', self.resolve(s).kind)
        units = [('A', cards['A']), ('B', cards['B'])]; self.select_units(s, units)
        self.assertEqual('replacement.order', self.order_units(s, units).kind)
        self.assertTrue(all(s.state.cards[c.object_id].zone == 'graveyard' for rows in cards.values() for c in rows))
        s = self.replay(s, load=True); self.assertEqual(['B'], s.state.pending_decision.actors)
        for _ in range(6):
            if s.state.pending_decision.kind != 'replacement.order':break
            option = next(o for o in s.packet('pilot:B', full=True)['decision']['ctx']['options'] if not o['id'].startswith('decline:'))
            result = s.act('pilot:B', {'action_id': 'choose', 'replacement': option['id']}); self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(s)); self.assertEqual('command', s.state.cards[commander.object_id].zone)
        self.assertTrue(all(s.state.cards[c.object_id].zone == 'library' for c in cards['A']))
        self.assertEqual('battlefield', s.state.cards[source.object_id].zone); self.finish(s, private)

    def test_actual_second_unit_fault_rolls_back_all_units_and_retries(self):
        from unittest.mock import patch
        from quorune.zone_transitions import ZoneTransitionOwner
        from quorune.record import authoritative_state_hash
        from quorune.errors import GameRuleError
        s, source, private, cards = self.world(70224660, age=1, pools={'A': 2, 'B': 2})
        self.start(s, source); self.resolve(s); units = [('A', cards['A']), ('B', cards['B'])]
        self.select_units(s, units); self.choose(s, 'A', cards=[c.ref for c in cards['A']])
        before = authoritative_state_hash(s.state); original = ZoneTransitionOwner.move_cards_simultaneously; calls = []
        def fault(owner, changes, **kwargs):
            result = original(owner, changes, **kwargs)
            if kwargs.get('library_order') is not None:
                calls.append(True)
                if len(calls) == 2:raise GameRuleError('Second graveyard cost unit fault')
            return result
        command = {'action_id': 'choose', 'cards': [c.ref for c in cards['B']]}
        with patch.object(ZoneTransitionOwner, 'move_cards_simultaneously', fault):
            result = s.act('pilot:B', command); self.assertFalse(result.ok); self.assertEqual(before, authoritative_state_hash(s.state))
        self.assertEqual(2, len(calls)); self.assertIsNone(self.choose(s, 'B', cards=command['cards'])); self.finish(s, private)

    def test_actual_separate_same_graveyard_units_create_separate_one_or_more_leave_triggers(self):
        s, source, private, cards = self.world(70224670, age=1, pools={'B': 4})
        watcher = self.add(s.engine, 'Tormod, the Desecrator', seat='B'); self.assert_closed(s, watcher)
        self.start(s, source); self.resolve(s); units = [('B', cards['B'][:2]), ('B', cards['B'][2:])]
        self.select_units(s, units); decision = self.order_units(s, units)
        self.assertIsNotNone(decision); self.assertEqual('trigger.order', decision.kind); self.assertEqual(['B'], decision.actors)
        triggers = s.packet('pilot:B', full=True)['decision']['ctx']['triggers']; self.assertEqual(2, len(triggers))
        result = s.act('pilot:B', {'action_id': 'order', 'triggers': [row['id'] for row in triggers]}); self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(s))
        tokens = [c for c in s.state.cards.values() if c.object_kind == 'token' and c.controller == 'B' and c.zone == 'battlefield']
        self.assertEqual(2, len(tokens)); self.finish(s, private)

    def test_actual_countered_or_bounced_recast_source_is_not_charged_by_the_old_upkeep(self):
        for i, answer in enumerate(('Stifle', 'Boomerang')):
            with self.subTest(answer=answer):
                s, source, private, cards = self.world(70224680 + i)
                reply = self.add(s.engine, answer, seat='B', zone='hand')
                for j in range(2):self.add(s.engine, 'Island', seat='B', ref='graveyard-reply-island-' + str(j))
                if answer == 'Boomerang':
                    self.add(s.engine, 'Vedalken Orrery'); self.add(s.engine, 'Plains'); self.add(s.engine, 'Wastes')
                self.start(s, source); old = source.logical_object_id
                target = next(t.ref for t in s.state.stack if t.source_object_id == source.object_id) if answer == 'Stifle' else source.ref
                self.cast_for(s, 'B', reply, [target]); self.resolve_top(s)
                if answer == 'Boomerang':self.cast_for(s, 'A', s.state.cards[source.object_id]); self.resolve_top(s); self.assertNotEqual(old, s.state.cards[source.object_id].logical_object_id)
                self.assertIsNone(self.resolve(s)); self.assertEqual(0, s.state.cards[source.object_id].counters.get('age', 0))
                self.assertTrue(all(s.state.cards[c.object_id].zone == 'graveyard' for c in cards['B'])); self.finish(s, private)

    def test_fresh_native_merged_cost_units_mutant_is_killed(self):
        from unittest.mock import patch
        import quorune.semantic_choices.cumulative_graveyard_upkeep as helper
        from quorune.library_order_model import LibraryArrivalOrder
        from quorune.semantic_runtime.graveyard_library_cost_intent import GraveyardLibraryCostBatchIntent
        def merged(**kwargs):
            orders = kwargs['orders']
            if len({o.chooser for o in orders}) == 1:
                refs = tuple(ref for order in orders for ref in order.members)
                kwargs = {**kwargs, 'orders': (LibraryArrivalOrder(orders[0].chooser, 'bottom', refs),), 'unit_size': len(refs)}
            return GraveyardLibraryCostBatchIntent(**kwargs)
        with patch.object(helper, 'GraveyardLibraryCostBatchIntent', merged):
            result = unittest.TestResult()
            type(self)('test_actual_separate_same_graveyard_units_create_separate_one_or_more_leave_triggers').run(result)
        self.assertFalse(result.errors, result.errors); self.assertTrue(result.failures)
