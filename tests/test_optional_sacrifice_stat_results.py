"""CR118.12/608.2h: resolution-selected creature facts, distinct from cast prices."""
from dataclasses import FrozenInstanceError
import json, unittest
from quorune.optional_sacrifice_stat_model import ResolutionPaidCreatureStatSpec as Spec
from quorune.scalar_effect_amount_model import PaidCreatureStatSnapshot, ScalarEffectAmountSpec
from quorune.activation_paid_characteristic_model import ActivationPaidCreatureFact
from types import SimpleNamespace as Row

TEXT = "You may sacrifice a nontoken creature. If you do, create X 2/2 green Wolf creature tokens, where X is the sacrificed creature's toughness."


def compiled():
    from quorune.compiler.optional_sacrifice_stat_results import optional_sacrifice_stat_result
    from quorune.oracle_ir import _effect_template
    return optional_sacrifice_stat_result(TEXT, compile_fixed=lambda text: _effect_template(text, card_name='Optional Stat Source'))


class ResolutionPaidStatModelTests(unittest.TestCase):
    def test_new_codec_is_immutable_and_round_trips_without_reinterpreting_old_paid_origins(self):
        spec = Spec('toughness'); self.assertEqual(spec, Spec.from_dict(json.loads(json.dumps(spec.to_dict()))))
        with self.assertRaises(FrozenInstanceError): spec.characteristic = 'power'
        with self.assertRaises(ValueError): ScalarEffectAmountSpec.from_dict(spec.to_dict())
        with self.assertRaises(ValueError): ActivationPaidCreatureFact.from_dict(spec.to_dict())

    def test_toughness_and_power_use_distinct_signed_facts_of_the_paid_incarnation(self):
        paid = PaidCreatureStatSnapshot('selected@3', power=2, toughness=5)
        self.assertEqual(5, Spec('toughness').amount(paid)); self.assertEqual(2, Spec('power').amount(paid))
        self.assertEqual('selected@3', paid.logical_object_id)

    def test_zero_and_negative_stats_create_zero_results_without_erasing_a_paid_cost(self):
        for value in (0, -1, -9):
            paid = PaidCreatureStatSnapshot('selected@3', power=value, toughness=value)
            self.assertEqual(0, Spec('power').amount(paid)); self.assertEqual(0, Spec('toughness').amount(paid))
        self.assertEqual(17, Spec('toughness').amount(PaidCreatureStatSnapshot('selected@3', 2, 17)))

    def test_unknown_versions_characteristics_context_selectors_and_incomplete_stats_fail_closed(self):
        raw = Spec('toughness').to_dict()
        for change in ({'schema_version': True}, {'schema_version': 2}, {'characteristic': 'mana_value'},
                {'characteristic': 'current_life'}, {'actor': 'B'}, {'source': '$source'}, {'kind': 'scalar_effect_amount'}):
            with self.subTest(change=change), self.assertRaises(ValueError): Spec.from_dict(raw | change)
        for value in (None, {}, 3):
            with self.assertRaises(ValueError): Spec('toughness').amount(value)
        with self.assertRaises(ValueError): PaidCreatureStatSnapshot('selected@3', True, 5)

    def test_fresh_power_for_toughness_substitution_is_killed_after_positive_typed_snapshot(self):
        from unittest.mock import patch
        paid = PaidCreatureStatSnapshot('selected@3', power=2, toughness=5)
        self.assertEqual(5, Spec('toughness').amount(paid))
        with patch.object(Spec, 'amount', lambda spec, value: max(0, value.power)):
            with self.assertRaises(AssertionError): self.assertEqual(5, Spec('toughness').amount(paid))


class OptionalSacrificeStatCompilerTests(unittest.TestCase):
    def test_complete_price_stat_and_fixed_token_definition_have_separate_typed_roles(self):
        from quorune.rules.optional_sacrifice_stats import optional_sacrifice_stat_spec
        result = compiled(); self.assertIsNotNone(result); self.assertIsNone(result[2])
        effect, = result[1]; payment, stat = optional_sacrifice_stat_spec(effect)
        self.assertEqual(13, effect['schema_version']); self.assertEqual('sacrifice', payment.kind)
        self.assertFalse(payment.predicate.token); self.assertEqual(('creature',), payment.predicate.types_all)
        self.assertEqual('toughness', stat.characteristic); self.assertEqual(1, effect['result']['quantity'])
        self.assertEqual('create_token', effect['result']['op'])

    def test_unknown_prices_history_source_characteristics_and_material_riders_stay_unrepresented(self):
        from quorune.compiler.optional_sacrifice_stat_results import optional_sacrifice_stat_result
        from quorune.oracle_ir import _effect_template
        for text in (TEXT.replace('a nontoken creature', 'a creature'), TEXT.replace('a nontoken creature', 'an artifact'),
                TEXT.replace("the sacrificed creature's toughness", "this creature's toughness"),
                TEXT.replace('toughness', 'mana value'), TEXT.replace('You may sacrifice', 'Sacrifice'),
                TEXT + ' Draw a card.', TEXT.replace('If you do', 'When you do')):
            with self.subTest(text=text):
                self.assertIsNone(optional_sacrifice_stat_result(text, compile_fixed=lambda value: _effect_template(value, card_name='Optional Stat Source')))

    def test_raw_frame_price_token_and_stat_markers_reject_without_using_coverage(self):
        from copy import deepcopy
        from quorune.rules.optional_sacrifice_stats import optional_sacrifice_stat_program_dependencies
        effect = compiled()[1][0]
        values = dict(event='step.begin', active_zone='battlefield', event_condition={'all': [
            {'field': 'player', 'op': 'eq', 'value': '$source.controller'}, {'field': 'step', 'op': 'eq', 'value': 'end_step'}]},
            effects=(effect,), target_schema=None, cost_schema=None, handlers=(), coverage=())
        required = optional_sacrifice_stat_program_dependencies(Row(**values))
        self.assertIn('quantity_expression.resolution_paid_creature_stat', required)
        for change in ({'event': 'creature.dies'}, {'active_zone': 'graveyard'}, {'event_condition': None},
                {'effects': (), 'handlers': ({'nested': effect},)}, {'target_schema': {'count': 1}}):
            with self.subTest(change=change), self.assertRaises(ValueError): optional_sacrifice_stat_program_dependencies(Row(**(values | change)))
        for mutate in (lambda e: e.update(schema_version=True), lambda e: e['stat'].update(schema_version=2),
                lambda e: e['payment']['predicate'].update(token=True), lambda e: e['result'].update(quantity=2)):
            bad = deepcopy(effect); mutate(bad)
            with self.assertRaises(ValueError): optional_sacrifice_stat_program_dependencies(Row(**(values | {'effects': (bad,)})))


class OptionalSacrificeStatChoiceTests(unittest.TestCase):
    def row(self, ref='good', *, actor='A', owner='B', token=False, creature=True, toughness=5, identity=None):
        from quorune.object_query import ObjectQueryResult
        return ObjectQueryResult(ref, ref, 'Public creature', owner, actor, 'battlefield',
            types=('creature',) if creature else ('artifact',), token=token, effective_power=2,
            effective_toughness=toughness, logical_object_id=identity or ref+'@1')

    def context(self, rows=None):
        from quorune.semantic_choices.context import SemanticChoiceContext, SnapshotSemanticChoiceQuery
        if rows is None: rows = (self.row(), self.row('token', token=True), self.row('foreign', actor='B'), self.row('artifact', creature=False))
        query = SnapshotSemanticChoiceQuery(seat_order=('A', 'B'), active_order=('A', 'B'), object_rows=tuple(rows))
        return SemanticChoiceContext(actor='A', stack_ref='stack', stack_controller='A', stack_label='Optional sacrifice',
            source_ref='source', card_ref=None, semantic_program_id='program', semantic_program_version=1, query=query)

    def effect(self):
        from copy import deepcopy
        from quorune.object_predicate import ObjectQuerySpec
        from quorune.fixed_effect_payment import FixedEffectPaymentSpec
        effect = deepcopy(compiled()[1][0]); effect['player'] = 'A'; effect['result']['controller'] = 'A'
        effect['payment'] = FixedEffectPaymentSpec('sacrifice', predicate=ObjectQuerySpec(zones=('battlefield',), controller='A', types_all=('creature',), token=False)).to_dict()
        return effect

    def continuation(self, effect):
        from quorune.semantic_choices.model import SemanticChoiceContinuation, SemanticChoiceFrame
        return SemanticChoiceContinuation(handler_id='test.stat', handler_version=1, stack_ref='stack', effect=effect,
            remaining=(), destination=None, note='Optional sacrifice', semantic_frame=SemanticChoiceFrame(
                semantic_program_id='program', semantic_program_version=1, stack_object='stack', instruction_pointer=0, controller='A'))

    def test_offer_and_completion_share_controlled_nontoken_creature_legality_and_last_known_stat(self):
        from quorune.semantic_choices.optional_sacrifice_stats import prepare_optional_sacrifice_stat_result, complete_optional_sacrifice_stat_result
        from quorune.zone_trigger_events import ZoneTransitionKind
        ctx = self.context(); prepared = prepare_optional_sacrifice_stat_result(self.effect(), ctx)
        self.assertEqual(('good',), prepared.request.choice.legal_refs)
        result = complete_optional_sacrifice_stat_result(self.continuation(prepared.continuation_effect), {'cards': ['good']}, ctx.query)
        self.assertEqual(1, len(result.intents)); intent, = result.intents
        self.assertEqual(ZoneTransitionKind.SACRIFICE, intent.transition_kind); self.assertTrue(intent.controlled_only); self.assertFalse(intent.owned_only)
        self.assertEqual(5, result.prepend_effects[0]['quantity']); self.assertEqual('A', result.prepend_effects[0]['controller'])
        self.assertEqual('good@1', ctx.query.object('good').logical_object_id)

    def test_decline_no_eligible_creature_and_invalid_selections_never_produce_a_result(self):
        from quorune.semantic_choices.optional_sacrifice_stats import prepare_optional_sacrifice_stat_result, complete_optional_sacrifice_stat_result
        ctx = self.context(); prepared = prepare_optional_sacrifice_stat_result(self.effect(), ctx); cont = self.continuation(prepared.continuation_effect)
        result = complete_optional_sacrifice_stat_result(cont, {'cards': []}, ctx.query)
        self.assertEqual((), result.intents); self.assertEqual((), result.prepend_effects)
        for response in ({'cards': ['token']}, {'cards': ['foreign']}, {'cards': ['artifact']}, {'cards': ['good', 'token']},
                {'cards': ['good'], 'pay': False}, {'cards': ['good'], 'amount': 99}):
            with self.subTest(response=response), self.assertRaises(ValueError): complete_optional_sacrifice_stat_result(cont, response, ctx.query)
        empty = self.context(rows=()); no = prepare_optional_sacrifice_stat_result(self.effect(), empty)
        self.assertEqual(0, no.request.choice.maximum)

    def test_current_stats_are_sealed_on_acceptance_and_zero_result_still_has_sacrifice_intent(self):
        from quorune.semantic_choices.optional_sacrifice_stats import prepare_optional_sacrifice_stat_result, complete_optional_sacrifice_stat_result
        original = self.context(); prepared = prepare_optional_sacrifice_stat_result(self.effect(), original)
        for value in (7, 0, -2):
            changed = self.context(rows=(self.row(toughness=value),))
            result = complete_optional_sacrifice_stat_result(self.continuation(prepared.continuation_effect), {'cards': ['good']}, changed.query)
            self.assertEqual(1, len(result.intents))
            self.assertEqual(value if value > 0 else 0, sum(e['quantity'] for e in result.prepend_effects))
        reentered = self.context(rows=(self.row(identity='good@2'),))
        with self.assertRaises(ValueError): complete_optional_sacrifice_stat_result(self.continuation(prepared.continuation_effect), {'cards': ['good']}, reentered.query)


class OptionalSacrificeStatIntegrationTests(unittest.TestCase):
    row = OptionalSacrificeStatChoiceTests.row
    context = OptionalSacrificeStatChoiceTests.context
    effect = OptionalSacrificeStatChoiceTests.effect
    continuation = OptionalSacrificeStatChoiceTests.continuation

    def test_registered_payment_handler_routes_new_version_to_same_offer_and_completion_owner(self):
        from quorune.semantic_choices.payments import PAYMENT_CHOICE_HANDLERS
        handler = next(h for h in PAYMENT_CHOICE_HANDLERS if h.operation == 'offer_optional_mana_payment')
        ctx = self.context(); prepared = handler.prepare(self.effect(), ctx)
        self.assertEqual(('good',), prepared.request.choice.legal_refs)
        result = handler.complete(self.continuation(prepared.continuation_effect), {'cards': ['good']}, ctx.query)
        self.assertEqual(1, len(result.intents)); self.assertEqual(5, result.prepend_effects[0]['quantity'])
        self.assertTrue({'stat', 'result'} <= set(handler.continuation_fields))

    def test_registered_handler_rejects_unknown_wrapper_and_forged_material_fields(self):
        from quorune.semantic_choices.payments import PAYMENT_CHOICE_HANDLERS
        handler = next(h for h in PAYMENT_CHOICE_HANDLERS if h.operation == 'offer_optional_mana_payment')
        for change in ({'schema_version': 14}, {'schema_version': True}, {'schema_version': 2},
                {'life_gain_counter_result_version': 1}, {'recover': {}}, {'effects': [{'op': 'draw', 'count': 1}]}):
            with self.subTest(change=change), self.assertRaises(ValueError): handler.prepare(self.effect() | change, self.context())

    def test_actual_binding_routes_only_controller_end_step_and_preserves_every_material_clause(self):
        from quorune.compiler.trigger_result_templates import binding_effect_template
        from quorune.oracle_ir import _effect_template
        condition = {'all': [{'field': 'player', 'op': 'eq', 'value': '$source.controller'},
            {'field': 'step', 'op': 'eq', 'value': 'end_step'}]}
        base = dict(event=Row(value='step.begin'), active_zone='battlefield', event_condition=condition, body=TEXT, variant='your end step')
        from quorune.compiler.optional_sacrifice_stat_results import optional_sacrifice_stat_binding_result
        good = binding_effect_template(Row(**base), TEXT, card_name='Optional Stat Source', effect_template=_effect_template)
        self.assertEqual(compiled(), good[0]); self.assertFalse(good[1])
        for change in ({'event': Row(value='creature.dies')}, {'active_zone': 'graveyard'}, {'event_condition': None},
                {'event_condition': {'all': [*condition['all'], {'field': 'extra', 'op': 'eq', 'value': True}]}}):
            self.assertIsNone(optional_sacrifice_stat_binding_result(Row(**(base | change)), TEXT,
                compile_fixed=lambda text: _effect_template(text, card_name='Optional Stat Source')))
        self.assertIsNone(optional_sacrifice_stat_binding_result(Row(**base), TEXT + ' Draw a card.',
            compile_fixed=lambda text: _effect_template(text, card_name='Optional Stat Source')))

    def test_payload_runtime_and_frozen_shapes_infer_fine_owners_without_declared_coverage(self):
        from quorune.fixed_effect_payment import fixed_payment_payload_dependencies
        from quorune.rules.optional_sacrifice_stats import optional_sacrifice_stat_node_capabilities
        from quorune.rules.fixed_effect_payment_shapes import fixed_payment_event_player_runtime_dependencies
        from quorune.replacement.immutable import FrozenMap
        effect = compiled()[1][0]; required = optional_sacrifice_stat_node_capabilities(effects=(FrozenMap(effect),), target_schema=None)
        self.assertIn('quantity_expression.resolution_paid_creature_stat', required)
        self.assertEqual(required, fixed_payment_payload_dependencies(effect))
        frame = dict(event='step.begin', active_zone='battlefield', event_condition={'all': [
            {'field': 'player', 'op': 'eq', 'value': '$source.controller'}, {'field': 'step', 'op': 'eq', 'value': 'end_step'}]},
            effects=(effect,), target_schema=None, cost_schema=None, handlers=(), coverage=())
        self.assertTrue(set(required) <= set(fixed_payment_event_player_runtime_dependencies(Row(**frame))))
        from quorune.rules.optional_sacrifice_stats import is_closed_optional_sacrifice_stat_program
        self.assertTrue(is_closed_optional_sacrifice_stat_program(Row(**frame, capability_dependencies=required)))
        for missing in required:
            self.assertFalse(is_closed_optional_sacrifice_stat_program(Row(**frame,
                capability_dependencies=tuple(c for c in required if c != missing))), missing)
        with self.assertRaises(ValueError): fixed_payment_event_player_runtime_dependencies(Row(**(frame | {'event': 'life.gained'})))
        with self.assertRaises(ValueError): fixed_payment_event_player_runtime_dependencies(Row(**(frame | {'handlers': ({'unowned': True},)})))


from pathlib import Path
import tempfile
from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_turn_life_loss_recovery as recovery_helpers


class OptionalSacrificeStatNativeTests(InheritedNativeFixture, unittest.TestCase):
    start = recovery_helpers.TurnLifeLossRecoveryNativeTests.start
    reject = recovery_helpers.TurnLifeLossRecoveryNativeTests.reject
    finish = recovery_helpers.TurnLifeLossRecoveryNativeTests.finish

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(); folder = Path(cls.temporary.name)
        base = json.loads((ROOT / 'tests/fixtures/bound-effect-program-cards.json').read_text(encoding='utf-8'))
        base['cards'] = [r for r in base['cards'] if r['name'] == 'Generic Bound Plains']; base['rulings'] = []
        basic = folder / 'basic.json'; basic.write_text(json.dumps(base), encoding='utf-8')
        build_fixture_database([basic, ROOT / 'tests/fixtures/optional-sacrifice-stat-cards.json'], folder / 'optional-stat.sqlite3')
        cls.db = CardDatabase(folder / 'optional-stat.sqlite3'); cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Resolution sacrifice stats', [DeckEntry('Sliver Hivelord', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 30)], ['Sliver Hivelord'])

    def wolves(self, s):
        return [c for c in s.state.cards.values() if c.zone == 'battlefield' and not c.is_card_object
            and c.printed_name == 'Wolf Token']

    def world(self, seed, *, extras=(), mana=None, creature_name='Grizzly Bears'):
        s, private = self.start(seed); source = self.add(s.engine, 'Feed the Pack', zone='hand')
        creature = self.add(s.engine, creature_name, ref='optional-stat-creature') if creature_name else None
        foreign = self.add(s.engine, 'Walking Corpse', seat='B', ref='optional-stat-foreign')
        for card in (source, creature, foreign):
            if card is not None:self.assert_closed(s, card)
        for name, seat, zone, ref in extras: self.assert_closed(s, self.add(s.engine, name, seat=seat, zone=zone, ref=ref))
        self.prepare(s, mana=mana or {'C': 5, 'G': 1}); self.checkpoint(s)
        before = sum(s.state.players['A'].mana_pool.values()); self.cast(s, 'A', source)
        self.assertIsNone(self.resolve(s)); self.assertEqual(before - 6, sum(s.state.players['A'].mana_pool.values()))
        return s, private, source, creature, foreign

    def end_step_trigger(self, s):
        self.until(s, lambda current: current.state.step == 'end_step' and bool(current.state.stack))
        self.assertEqual('A', s.state.active_player); self.assertEqual('priority', s.state.pending_decision.kind)

    def choose_sacrifice(self, s, cards, *, seat='A'):
        command = {'action_id': 'choose', 'cards': list(cards)}
        self.reject(s, 'D' if seat != 'D' else 'C', command)
        result = s.act('pilot:' + seat, command); self.assertTrue(result.ok, result.summary)

    def test_complete_original_admits_normally_and_each_price_stat_token_owner_is_independently_required(self):
        from dataclasses import replace
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.card_programs import bind_card_program_runtime
        s, _ = self.start(732000); original = self.add(s.engine, 'Feed the Pack', zone='hand'); self.assert_closed(s, original)
        program = next(p for p in s.engine.semantics.programs_for_oracle(original.oracle_id) if p.effects)
        self.assertEqual('step.begin', program.event); self.assertEqual(13, program.effects[0]['schema_version'])
        for missing in ('quantity_expression.resolution_paid_creature_stat', 'effect.choice.optional_fixed_mana_payment',
                'choice.affected_player.fixed_sacrifice', 'zone.change.destination_replacement', 'token.creation.fixed_definition'):
            deps = tuple(c for c in program.capability_dependencies if c != missing)
            changed = replace(program, coverage=(), capability_dependencies=deps,
                capability_closure=self.registry.closure(deps, profile='commander_review').to_dict())
            bound = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile='commander_review')
            self.assertFalse(bound['strict'], missing); self.assertIn('capability:undeclared_runtime_dependency:' + missing, bound['blockers'])
        unsupported = self.db.lookup('Disciple of Bolas')
        parent = compile_best_available_card_program(self.db, unsupported, semantic_registry=s.engine.semantics,
            capability_registry=self.registry, capability_profile='commander_review')
        self.assertFalse(bind_card_program_runtime(parent, capability_registry=self.registry,
            profile='commander_review')['strict_capability_ready'])

    def test_actual_printed_cast_end_step_sacrifice_creates_two_wolves_and_pending_choice_reloads(self):
        s, private, source, creature, foreign = self.world(732001)
        self.end_step_trigger(s); decision = self.resolve(s); self.assertIsNotNone(decision)
        self.assertEqual([], self.wolves(s)); self.assertEqual('battlefield', s.state.cards[creature.object_id].zone)
        self.reject(s, 'A', {'action_id': 'choose', 'cards': [foreign.ref]})
        s = self.replay(s, load=True)
        self.choose_sacrifice(s, [s.state.cards[creature.object_id].ref]); self.assertIsNone(self.resolve(s))
        self.assertEqual('graveyard', s.state.cards[creature.object_id].zone)
        wolves = self.wolves(s); self.assertEqual(2, len(wolves))
        for wolf in wolves:
            self.assertEqual('A', wolf.controller); self.assertEqual('A', wolf.owner)
            row = s.engine._public_object_query_result(wolf)
            self.assertEqual((2, 2), (row.effective_power, row.effective_toughness))
        self.finish(s, private)

    def test_actual_response_modifier_and_zero_power_creature_use_current_toughness_last_known_before_sacrifice(self):
        extras = (('Giant Growth', 'A', 'hand', 'stat-growth'), ('Forest', 'A', 'battlefield', 'stat-green'))
        s, private, source, creature, _ = self.world(732010, creature_name='Wall of Wood', extras=extras)
        self.end_step_trigger(s)
        growth = next(c for c in s.state.cards.values() if c.ref == 'stat-growth')
        self.cast(s, 'A', growth, [creature.ref]); self.resolve_top(s)
        row = s.engine._public_object_query_result(s.state.cards[creature.object_id], _maximum_layer=None)
        self.assertEqual((3, 6), (row.effective_power, row.effective_toughness))
        self.assertEqual('semantic.choice', self.resolve(s).kind)
        self.choose_sacrifice(s, [s.state.cards[creature.object_id].ref]); self.assertIsNone(self.resolve(s))
        self.assertEqual('graveyard', s.state.cards[creature.object_id].zone); self.assertEqual(6, len(self.wolves(s)))
        self.finish(s, private)

    def test_actual_borrowed_control_is_eligible_and_sacrifice_moves_to_original_owners_graveyard(self):
        extras = (('Control Magic', 'A', 'hand', 'stat-borrow'),)
        s, private, source, own, foreign = self.world(732011, extras=extras, mana={'C': 7, 'G': 1, 'U': 2})
        spell = next(c for c in s.state.cards.values() if c.ref == 'stat-borrow')
        self.cast(s, 'A', spell, [foreign.ref]); self.assertIsNone(self.resolve(s))
        self.assertEqual(('B', 'A'), (s.state.cards[foreign.object_id].owner, s.state.cards[foreign.object_id].controller))
        self.end_step_trigger(s); self.assertEqual('semantic.choice', self.resolve(s).kind)
        self.choose_sacrifice(s, [s.state.cards[foreign.object_id].ref]); self.assertIsNone(self.resolve(s))
        self.assertEqual('graveyard', s.state.cards[foreign.object_id].zone)
        self.assertIn(foreign.object_id, s.state.players['B'].zones['graveyard'])
        self.assertEqual('battlefield', s.state.cards[own.object_id].zone); self.assertEqual(2, len(self.wolves(s)))
        self.finish(s, private)

    def test_actual_decline_and_no_eligible_creature_keep_cost_unpaid_and_create_no_tokens(self):
        for index, name in enumerate(('Grizzly Bears', None)):
            with self.subTest(creature=name):
                s, private, source, own, foreign = self.world(732012 + index, creature_name=name)
                self.end_step_trigger(s); self.assertEqual('semantic.choice', self.resolve(s).kind)
                self.reject(s, 'A', {'action_id': 'choose', 'cards': [foreign.ref]})
                self.choose_sacrifice(s, []); self.assertIsNone(self.resolve(s)); self.assertEqual([], self.wolves(s))
                if own:self.assertEqual('battlefield', s.state.cards[own.object_id].zone)
                self.finish(s, private)

    def test_actual_created_tokens_and_noncreature_source_are_excluded_from_sacrifice_price(self):
        extras = (('Raise the Alarm', 'A', 'hand', 'stat-token-producer'),)
        s, private, source, own, _ = self.world(732014, extras=extras, mana={'C': 6, 'G': 1, 'W': 1})
        spell = next(c for c in s.state.cards.values() if c.ref == 'stat-token-producer')
        self.cast(s, 'A', spell); self.assertIsNone(self.resolve(s))
        tokens = [c for c in s.state.cards.values() if c.zone == 'battlefield' and not c.is_card_object]
        self.assertEqual(2, len(tokens)); self.end_step_trigger(s); self.assertEqual('semantic.choice', self.resolve(s).kind)
        for card in (*tokens, s.state.cards[source.object_id]):
            self.reject(s, 'A', {'action_id': 'choose', 'cards': [card.ref]})
        self.choose_sacrifice(s, [s.state.cards[own.object_id].ref]); self.assertIsNone(self.resolve(s))
        self.assertEqual(2, len(self.wolves(s)))
        self.assertTrue(all(s.state.cards[c.object_id].zone == 'battlefield' for c in tokens)); self.finish(s, private)

    def test_actual_source_destruction_keeps_pending_price_result_and_stifle_suppresses_both(self):
        for index, name in enumerate(('Disenchant', 'Stifle')):
            with self.subTest(response=name):
                extras = ((name, 'B', 'hand', 'stat-response'), ('Island', 'B', 'battlefield', 'stat-blue'),
                    ('Plains', 'B', 'battlefield', 'stat-white'))
                s, private, source, own, _ = self.world(732020 + index, extras=extras)
                self.end_step_trigger(s); spell = next(c for c in s.state.cards.values() if c.ref == 'stat-response')
                self.cast(s, 'B', spell, [source.ref if index == 0 else s.state.stack[-1].ref])
                if index == 0:
                    self.assertEqual('semantic.choice', self.resolve(s).kind)
                    self.assertEqual('graveyard', s.state.cards[source.object_id].zone)
                    self.choose_sacrifice(s, [s.state.cards[own.object_id].ref]); self.assertIsNone(self.resolve(s))
                    self.assertEqual('graveyard', s.state.cards[own.object_id].zone); self.assertEqual(2, len(self.wolves(s)))
                else:
                    self.assertIsNone(self.resolve(s)); self.assertFalse(s.state.stack)
                    self.assertEqual('battlefield', s.state.cards[own.object_id].zone); self.assertEqual([], self.wolves(s))
                self.finish(s, private)

    def test_actual_sacrifice_replacement_exiles_the_creature_and_still_creates_toughness_tokens(self):
        extras = (('Samurai of the Pale Curtain', 'D', 'battlefield', 'stat-exile-replacement'),)
        s, private, source, own, _ = self.world(732022, extras=extras)
        self.end_step_trigger(s); self.assertEqual('semantic.choice', self.resolve(s).kind)
        self.choose_sacrifice(s, [s.state.cards[own.object_id].ref]); self.assertIsNone(self.resolve(s))
        self.assertEqual('exile', s.state.cards[own.object_id].zone); self.assertEqual(2, len(self.wolves(s)))
        self.finish(s, private)

    def test_actual_death_target_choice_waits_until_sacrifice_and_token_result_are_complete(self):
        extras = (('Blood Artist', 'A', 'battlefield', 'stat-death-observer'),)
        s, private, source, own, _ = self.world(732023, extras=extras)
        self.end_step_trigger(s); self.assertEqual('semantic.choice', self.resolve(s).kind)
        self.choose_sacrifice(s, [s.state.cards[own.object_id].ref])
        self.assertEqual('graveyard', s.state.cards[own.object_id].zone); self.assertEqual(2, len(self.wolves(s)))
        self.assertEqual((40, 40), (s.state.players['A'].life, s.state.players['B'].life))
        self.assertEqual('semantic.target', self.resolve(s).kind)
        self.target(s, 'A', ['B']); self.assertIsNone(self.resolve(s))
        self.assertEqual((41, 39), (s.state.players['A'].life, s.state.players['B'].life)); self.finish(s, private)

    def test_actual_post_cost_and_post_token_faults_restore_creature_choice_tokens_and_replay_then_retry(self):
        import quorune.token_creation as owner
        from unittest.mock import patch
        from quorune.errors import GameRuleError
        from quorune.record import authoritative_state_hash
        for index, after_tokens in enumerate((False, True)):
            with self.subTest(after_tokens=after_tokens):
                s, private, source, own, _ = self.world(732030 + index)
                self.end_step_trigger(s); self.assertEqual('semantic.choice', self.resolve(s).kind)
                original = owner._commit_token_specs; observed = []; before = authoritative_state_hash(s.state)
                def fault(*args, **kwargs):
                    if after_tokens:original(*args, **kwargs)
                    observed.append((s.state.cards[own.object_id].zone, len(self.wolves(s))))
                    raise GameRuleError('Injected after resolution sacrifice cost or token commit')
                with patch.object(owner, '_commit_token_specs', side_effect=fault):
                    result = s.act('pilot:A', {'action_id': 'choose', 'cards': [s.state.cards[own.object_id].ref]})
                self.assertFalse(result.ok); self.assertIn('Injected after resolution sacrifice', result.summary)
                self.assertEqual([('graveyard', 2 if after_tokens else 0)], observed)
                self.assertEqual(before, authoritative_state_hash(s.state))
                self.assertEqual('battlefield', s.state.cards[own.object_id].zone); self.assertEqual([], self.wolves(s))
                s = self.replay(s, load=True); self.choose_sacrifice(s, [s.state.cards[own.object_id].ref])
                self.assertIsNone(self.resolve(s)); self.assertEqual(2, len(self.wolves(s))); self.finish(s, private)

    def test_fresh_native_power_for_toughness_substitution_is_killed_after_positive_actual_modified_original(self):
        from unittest.mock import patch
        self.test_actual_response_modifier_and_zero_power_creature_use_current_toughness_last_known_before_sacrifice()
        observed = []
        def wrong_origin(spec, snapshot):
            observed.append((snapshot.power, snapshot.toughness)); return max(0, snapshot.power)
        with patch.object(Spec, 'amount', wrong_origin):
            with self.assertRaises(AssertionError):
                self.test_actual_response_modifier_and_zero_power_creature_use_current_toughness_last_known_before_sacrifice()
        self.assertEqual([(3, 6)], observed)

    def test_genuinely_verified_stat_payment_sacrifice_and_token_withdrawals_block_original_and_preserve_old_origins(self):
        from copy import deepcopy
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests
        from quorune.semantics import SemanticRegistry
        from quorune.oracle_ir import register_generated_programs
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.card_programs import bind_card_program_runtime
        raw = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        doc = read_evidence_document(ROOT / 'platform/capability-evidence-declarations.json')
        known = set(discover_tests(ROOT).values())
        for missing in ('quantity_expression.resolution_paid_creature_stat', 'effect.choice.optional_fixed_mana_payment',
                'choice.affected_player.fixed_sacrifice', 'token.creation.fixed_definition'):
            changed = deepcopy(raw); next(c for c in changed['capabilities'] if c['id'] == missing).update(
                status='blocked', blockers=['Resolution sacrifice stat owner withdrawn'])
            registry = CapabilityRegistry(changed)
            index = build_index(registry_value=changed, declaration_source=doc, discovered_test_ids=known)
            registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
            controls = [('Feed the Pack', False), ('Grizzly Bears', True)]
            if missing == 'quantity_expression.resolution_paid_creature_stat':controls += [("Life's Legacy", True)]
            for name, expected in controls:
                record = self.db.lookup(name); semantics = SemanticRegistry()
                register_generated_programs(self.db, semantics, (record,), capability_registry=registry, capability_profile='commander_review',
                    promote_exact_runtime_handlers=True, promote_exact_trigger_programs=True,
                    promote_exact_effect_programs=True, promote_exact_capability_declarations=True)
                parent = compile_best_available_card_program(self.db, record, semantic_registry=semantics,
                    capability_registry=registry, capability_profile='commander_review')
                bound = bind_card_program_runtime(parent, capability_registry=registry, profile='commander_review')
                self.assertEqual(expected, bound['strict_capability_ready'], (missing, name, bound['blockers']))


if __name__ == '__main__':
    unittest.main()
