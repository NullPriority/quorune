"""Resolution-price snapshots and Recover's additive half-life descriptor."""
from dataclasses import FrozenInstanceError
import json, unittest
from unittest.mock import patch
from quorune.fractional_life_payment_model import FractionalLifePaymentSpec, ResolvedFractionalLifePayment as Bound
from quorune.rules.fractional_life_payment import (
    FractionalLifePaymentQuote as Quote, FractionalLifeRecoverSpec as RecoverHalf,
    resolved_fractional_payment_fields, fractional_payment_record, validate_fractional_payment_quote,
)


class FractionalLifePaymentQuoteTests(unittest.TestCase):
    def test_signed_quote_round_trip_pins_the_price_and_rejects_changed_basis_even_if_rounded_amount_matches(self):
        for life, amount in ((13, 7), (12, 6), (1, 1), (0, 0), (-1, 0)):
            quote = Quote(life); self.assertEqual(quote, Quote.from_dict(json.loads(json.dumps(quote.to_dict()))))
            self.assertEqual(amount, quote.payment.amount); self.assertEqual(amount, fractional_payment_record(quote.payment)['amount'])
            self.assertEqual(Bound(amount), validate_fractional_payment_quote(quote, life))
            with self.assertRaises(ValueError):validate_fractional_payment_quote(quote, life + 1)
        with self.assertRaises(ValueError):Quote(True)
        with self.assertRaises(FrozenInstanceError):Quote(13).life = 14
        with self.assertRaises(ValueError):Quote.from_dict(Quote(13).to_dict() | {'actor': 'B'})

    def test_resolved_record_requires_exact_new_version_and_no_second_resource_or_selector(self):
        raw = fractional_payment_record(Bound(0)); self.assertEqual(Bound(0), resolved_fractional_payment_fields(raw))
        for change in ({'kind': 'life'}, {'schema_version': 2}, {'schema_version': True}, {'amount': -1},
                {'amount': False}, {'requirements': {}}, {'predicate': {}}, {'source_ref': 'other'}):
            with self.subTest(change=change), self.assertRaises(ValueError):resolved_fractional_payment_fields(raw | change)

    def test_separate_recover_descriptor_does_not_reinterpret_fixed_mana_recover_one(self):
        from quorune.recover_model import RecoverSpec
        spec = RecoverHalf(); self.assertEqual(spec, RecoverHalf.from_dict(json.loads(json.dumps(spec.to_dict()))))
        with self.assertRaises(ValueError):RecoverSpec.from_dict(spec.to_dict())
        for change in ({'schema_version': 1}, {'schema_version': True}, {'kind': 'recover_fixed_mana'}, {'cost': {}}):
            with self.assertRaises(ValueError):RecoverHalf.from_dict(spec.to_dict() | change)
        with self.assertRaises(ValueError):RecoverHalf(None)
        self.assertEqual(FractionalLifePaymentSpec(), spec.payment)


class FractionalLifePaymentOwnerTests(unittest.TestCase):
    def query(self, life):
        from quorune.semantic_choices.context import SnapshotSemanticChoiceQuery
        from quorune.replacement.immutable import FrozenMap
        return SnapshotSemanticChoiceQuery(seat_order=('A', 'B'), active_order=('A', 'B'), life_by_seat=FrozenMap({'A': life, 'B': 40}))

    def context(self, life):
        from quorune.semantic_choices.context import SemanticChoiceContext
        return SemanticChoiceContext(actor='A', stack_ref='stack', stack_controller='A', stack_label='Recover price',
            source_ref='graveyard-source', card_ref=None, semantic_program_id='program', semantic_program_version=1,
            query=self.query(life), source_logical_object_id='original@4')

    def continuation(self, effect):
        from quorune.semantic_choices.model import SemanticChoiceContinuation, SemanticChoiceFrame
        return SemanticChoiceContinuation(handler_id='choice.payment.optional-fixed-effect.v1', handler_version=1,
            stack_ref='stack', effect=effect, remaining=(), destination=None, note='Recover price', semantic_frame=SemanticChoiceFrame(
                semantic_program_id='program', semantic_program_version=1, stack_object='stack', instruction_pointer=0, controller='A'))

    def test_new_fixed_cost_codec_accepts_zero_only_in_explicit_fraction_price_version(self):
        from quorune.fixed_effect_payment import FixedEffectPaymentSpec
        for amount in (0, 1, 7):
            spec = FixedEffectPaymentSpec.from_dict(fractional_payment_record(Bound(amount)))
            self.assertEqual('resolved_fractional_life', spec.kind); self.assertEqual(13, spec.schema_version)
            self.assertEqual(spec, FixedEffectPaymentSpec.from_dict(spec.to_dict()))
        with self.assertRaises(ValueError):FixedEffectPaymentSpec('life', amount=0)
        with self.assertRaises(ValueError):FixedEffectPaymentSpec('life', amount=7, schema_version=13)

    def test_shared_offer_and_command_emit_one_canonical_life_intent_even_for_zero_at_negative_life(self):
        from quorune.semantic_choices.fractional_life_payment import prepare_fractional_life_cost, complete_fractional_life_cost
        from quorune.semantic_runtime.intents import PayLifeIntent
        effect = {'op': 'offer_optional_mana_payment', 'player': 'A'}
        for life, amount in ((13, 7), (12, 6), (1, 1), (0, 0), (-1, 0)):
            context = self.context(life); prepared = prepare_fractional_life_cost(FractionalLifePaymentSpec(), effect, context)
            self.assertEqual((True, False), prepared.request.choice.legal_values)
            self.assertEqual(amount, prepared.request.public_context['life_cost'])
            completion = complete_fractional_life_cost(FractionalLifePaymentSpec(), prepared.continuation_effect, {'pay': True}, context.query)
            intent, = completion.intents; self.assertIsInstance(intent, PayLifeIntent)
            self.assertEqual(('A', 'A', amount), (intent.actor, intent.player, intent.amount))
            self.assertEqual((), complete_fractional_life_cost(FractionalLifePaymentSpec(), prepared.continuation_effect, {'pay': False}, context.query).intents)

    def test_changed_quote_and_client_amount_or_player_overrides_fail_closed(self):
        from quorune.semantic_choices.fractional_life_payment import prepare_fractional_life_cost, complete_fractional_life_cost
        prepared = prepare_fractional_life_cost(FractionalLifePaymentSpec(), {'op': 'offer_optional_mana_payment'}, self.context(13))
        with self.assertRaises(ValueError):complete_fractional_life_cost(FractionalLifePaymentSpec(), prepared.continuation_effect, {'pay': True}, self.query(14))
        for response in ({'pay': True, 'amount': 1}, {'pay': True, 'player': 'B'}, {'pay': 'true'}):
            with self.assertRaises(ValueError):complete_fractional_life_cost(FractionalLifePaymentSpec(), prepared.continuation_effect, response, self.query(13))
        self.assertEqual((), complete_fractional_life_cost(FractionalLifePaymentSpec(), prepared.continuation_effect, {'pay': False}, self.query(14)).intents)

    def test_half_life_recover_price_and_zero_paid_intent_return_original_while_decline_exiles(self):
        from quorune.compiler.recover_nodes import recover_spec_from_line
        from quorune.rules.recover import recover_payment_effect, recover_node_dependencies
        from quorune.semantic_choices.recover import prepare_recover, complete_recover
        from test_fractional_life_payment import ORIGINAL
        spec = recover_spec_from_line(ORIGINAL); self.assertEqual(RecoverHalf(), spec)
        symbolic = recover_payment_effect(spec); self.assertNotIn('cost', symbolic); self.assertEqual(12, symbolic['schema_version'])
        self.assertIn('keyword.recover.half_current_life', recover_node_dependencies(symbolic))
        for life in (13, 0, -1):
            prepared = prepare_recover(symbolic | {'player': 'A'}, self.context(life)); cont = self.continuation(prepared.continuation_effect)
            paid = complete_recover(cont, {'pay': True}, self.query(life)); self.assertEqual(1, len(paid.intents))
            self.assertEqual('hand', paid.prepend_effects[0]['destination']); self.assertEqual('original@4', paid.prepend_effects[0]['identity'])
            declined = complete_recover(cont, {'pay': False}, self.query(life))
            self.assertEqual((), declined.intents); self.assertEqual('exile', declined.prepend_effects[0]['destination'])


class FractionalLifePaymentFrameTests(unittest.TestCase):
    def frame(self):
        from quorune.rules.recover import recover_payment_effect
        return dict(event='creature.dies', active_zone='graveyard', event_condition={'field': 'owner', 'op': 'eq', 'value': '$source.owner'},
            effects=(recover_payment_effect(RecoverHalf()),), target_schema=None, cost_schema=None, handlers=(), coverage=())

    def test_complete_fraction_price_requires_own_event_result_and_resource_owners_without_coverage(self):
        from types import SimpleNamespace as Row
        from quorune.rules.fractional_life_payment import fractional_life_payment_program_dependencies
        from quorune.rules.fixed_effect_payment_shapes import fixed_payment_event_player_runtime_dependencies
        from quorune.rules.recover import is_closed_recover_program
        values = self.frame(); required = fractional_life_payment_program_dependencies(Row(**values))
        self.assertTrue({'keyword.recover.half_current_life', 'cost.life.half_current_resolution', 'zone.move.recover_source'} <= set(required))
        self.assertTrue(set(required) <= set(fixed_payment_event_player_runtime_dependencies(Row(**values))))
        self.assertTrue(is_closed_recover_program(Row(**values, capability_dependencies=required)))
        for missing in required:
            self.assertFalse(is_closed_recover_program(Row(**values, capability_dependencies=tuple(c for c in required if c != missing))))

    def test_wrong_occurrence_target_cost_and_hidden_handlers_cannot_reuse_fractional_authority(self):
        from types import SimpleNamespace as Row
        from quorune.rules.fractional_life_payment import fractional_life_payment_program_dependencies
        values = self.frame()
        for change in ({'active_zone': 'battlefield'}, {'event': 'spell.cast'}, {'event_condition': None},
                {'target_schema': {'count': 1}}, {'cost_schema': {'amount': 1}}, {'handlers': ({'unowned': True},)},
                {'effects': (*values['effects'], {'op': 'draw', 'count': 1})}):
            with self.subTest(change=change), self.assertRaises(ValueError):fractional_life_payment_program_dependencies(Row(**(values | change)))

    def test_declared_bound_fraction_or_unbound_price_in_another_wrapper_is_rejected(self):
        from types import SimpleNamespace as Row
        from quorune.rules.fractional_life_payment import fractional_life_payment_program_dependencies
        values = self.frame()
        for price in (fractional_payment_record(Bound(7)), FractionalLifePaymentSpec().to_dict()):
            bad = values | {'effects': ({'op': 'offer_optional_mana_payment', 'schema_version': 2,
                'player': '$controller', 'payment': price, 'effects': [{'op': 'draw', 'count': 1}]},)}
            with self.assertRaises(ValueError):fractional_life_payment_program_dependencies(Row(**bad))


class FractionalLifePaymentKeywordTests(unittest.TestCase):
    def test_exact_em_dash_keyword_classification_uses_closed_fraction_production_and_preserves_riders(self):
        from quorune.compiler.keyword_templates import keyword_mechanics
        from test_fractional_life_payment import ORIGINAL
        self.assertEqual(('recover',), keyword_mechanics(ORIGINAL, ('Recover',)))
        self.assertEqual(('recover',), keyword_mechanics(ORIGINAL.split(' (')[0], ('Recover',)))
        for bad in (ORIGINAL + ' Draw a card.', ORIGINAL.replace('rounded up', 'rounded down'),
                ORIGINAL.replace('another creature', 'a creature')):
            self.assertIsNone(keyword_mechanics(bad, ('Recover',)))

    def test_locked_copy_controller_uses_its_own_current_life_in_typed_owner(self):
        from dataclasses import replace
        from quorune.rules.recover import recover_payment_effect
        from quorune.semantic_choices.recover import prepare_recover
        helper = FractionalLifePaymentOwnerTests(); context = replace(helper.context(13), actor='B', stack_controller='B')
        effect = recover_payment_effect(RecoverHalf()) | {'player': 'B'}
        prepared = prepare_recover(effect, context)
        self.assertEqual(20, prepared.request.public_context['life_cost'])
        self.assertEqual('B', prepared.continuation_effect['_choice_actor'])


from pathlib import Path
import tempfile
from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_turn_life_loss_recovery as recovery_helpers


class FractionalLifeRecoverNativeTests(InheritedNativeFixture, unittest.TestCase):
    start = recovery_helpers.TurnLifeLossRecoveryNativeTests.start
    reject = recovery_helpers.TurnLifeLossRecoveryNativeTests.reject
    finish = recovery_helpers.TurnLifeLossRecoveryNativeTests.finish

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(); folder = Path(cls.temporary.name)
        base = json.loads((ROOT / 'tests/fixtures/bound-effect-program-cards.json').read_text(encoding='utf-8'))
        base['cards'] = [c for c in base['cards'] if c['name'] == 'Generic Bound Plains']; base['rulings'] = []
        basic = folder / 'basic.json'; basic.write_text(json.dumps(base), encoding='utf-8')
        build_fixture_database([basic, ROOT / 'tests/fixtures/fractional-life-payment-cards.json'], folder / 'fraction-price.sqlite3')
        cls.db = CardDatabase(folder / 'fraction-price.sqlite3'); cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Half-life Recover', [DeckEntry('Sliver Hivelord', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 30)], ['Sliver Hivelord'])

    def world(self, seed, *, life=13, extras=()):
        s, private = self.start(seed); source = self.add(s.engine, "Garza's Assassin", zone='hand')
        victim = self.add(s.engine, 'Grizzly Bears', ref='fractional-recover-own-creature')
        black = self.add(s.engine, "Garza's Assassin", seat='B', ref='fractional-recover-black-control')
        for card in (source, victim, black):self.assert_closed(s, card)
        for name, seat, zone, ref in extras:self.assert_closed(s, self.add(s.engine, name, seat=seat, zone=zone, ref=ref))
        s.state.players['A'].life = life; self.prepare(s, mana={'B': 3}); self.checkpoint(s)
        before = s.state.players['A'].mana_pool['B']; self.cast(s, 'A', source); self.assertIsNone(self.resolve(s))
        self.assertEqual(before - 3, s.state.players['A'].mana_pool['B'])
        return s, private, source, victim, black

    def activate_material(self, s, source, victim, black):
        self.priority(s, 'A'); current = s.state.cards[source.object_id]
        action = next(a for a in s.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
            if a['id'].startswith('activate:' + current.ref + ':'))
        self.reject(s, 'A', {'action_id': action['id'], 'targets': [s.state.cards[black.object_id].ref], 'pay': 'auto'})
        command = {'action_id': action['id'], 'targets': [s.state.cards[victim.object_id].ref], 'pay': 'auto'}
        self.reject(s, 'D', command); result = s.act('pilot:A', command); self.assertTrue(result.ok, result.summary)
        self.assertEqual('graveyard', s.state.cards[source.object_id].zone)
        self.assertEqual(1, len(s.state.stack)); self.assertEqual('battlefield', s.state.cards[victim.object_id].zone)
        self.assertEqual('semantic.choice', self.resolve(s).kind)
        self.assertEqual('graveyard', s.state.cards[victim.object_id].zone)

    def choose_price(self, s, pay, *, seat='A'):
        command = {'action_id': 'choose', 'pay': pay}; self.reject(s, 'D' if seat != 'D' else 'C', command)
        result = s.act('pilot:' + seat, command); self.assertTrue(result.ok, result.summary)

    def test_complete_original_both_material_abilities_bind_normally_with_each_fraction_price_owner_required(self):
        from dataclasses import replace
        from quorune.card_programs.binding import bind_semantic_program_runtime
        s, _ = self.start(734000); source = self.add(s.engine, "Garza's Assassin", zone='hand'); self.assert_closed(s, source)
        programs = s.engine.semantics.programs_for_oracle(source.oracle_id)
        self.assertTrue(any(p.event == 'activate' for p in programs))
        recover = next(p for p in programs if p.active_zone == 'graveyard')
        for missing in ('keyword.recover.half_current_life', 'cost.life.half_current_resolution',
                'effect.choice.optional_fixed_mana_payment', 'zone.move.recover_source', 'trigger.event.normalized_zone_change'):
            deps = tuple(c for c in recover.capability_dependencies if c != missing)
            changed = replace(recover, coverage=(), capability_dependencies=deps,
                capability_closure=self.registry.closure(deps, profile='commander_review').to_dict())
            bound = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile='commander_review')
            self.assertFalse(bound['strict'], missing); self.assertIn('capability:undeclared_runtime_dependency:' + missing, bound['blockers'])

    def test_actual_printed_bbb_cast_sacrifice_destroy_then_thirteen_life_costs_seven_and_recovers_privately(self):
        s, private, source, victim, black = self.world(734001)
        self.activate_material(s, source, victim, black)
        self.assertEqual(13, s.state.players['A'].life)
        self.reject(s, 'A', {'action_id': 'choose', 'pay': True, 'amount': 1})
        s = self.replay(s, load=True); self.choose_price(s, True); self.assertIsNone(self.resolve(s))
        self.assertEqual(6, s.state.players['A'].life); self.assertEqual('hand', s.state.cards[source.object_id].zone)
        self.assertEqual('battlefield', s.state.cards[black.object_id].zone); self.finish(s, private)


    def test_actual_even_life_pays_six_and_decline_never_pays_but_exiles_original(self):
        for index, paid in enumerate((True, False)):
            with self.subTest(paid=paid):
                s, private, source, victim, black = self.world(734010 + index, life=12)
                self.activate_material(s, source, victim, black); self.choose_price(s, paid); self.assertIsNone(self.resolve(s))
                self.assertEqual(6 if paid else 12, s.state.players['A'].life)
                self.assertEqual('hand' if paid else 'exile', s.state.cards[source.object_id].zone)
                self.finish(s, private)

    def test_actual_life_gain_response_before_resolution_quotes_current_life_instead_of_death_time_life(self):
        extras = (('Revitalize', 'A', 'hand', 'half-life-response'), ('Plains', 'A', 'battlefield', 'half-life-white'),
            ('Island', 'A', 'battlefield', 'half-life-generic'))
        s, private, source, victim, black = self.world(734012, life=13, extras=extras)
        self.priority(s, 'A'); action = next(a for a in s.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
            if a['id'].startswith('activate:' + s.state.cards[source.object_id].ref + ':'))
        result = s.act('pilot:A', {'action_id': action['id'], 'targets': [victim.ref], 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary); self.resolve_top(s)
        self.assertEqual('graveyard', s.state.cards[victim.object_id].zone)
        spell = next(c for c in s.state.cards.values() if c.ref == 'half-life-response')
        self.cast(s, 'A', spell); self.resolve_top(s); self.assertEqual(16, s.state.players['A'].life)
        self.assertEqual('semantic.choice', self.resolve(s).kind); self.choose_price(s, True); self.assertIsNone(self.resolve(s))
        self.assertEqual(8, s.state.players['A'].life); self.assertEqual('hand', s.state.cards[source.object_id].zone)
        self.finish(s, private)


    def activate_support(self, s, card, *, seat='B', targets=()):
        self.priority(s, seat); current = s.state.cards[card.object_id]
        action = next(a for a in s.packet('pilot:' + seat, full=True)['decision']['ctx']['legal']['actions']
            if a['id'].startswith('activate:' + current.ref + ':'))
        command = {'action_id': action['id'], 'pay': 'auto', 'targets': list(targets)}
        self.reject(s, 'D' if seat != 'D' else 'C', command)
        result = s.act('pilot:' + seat, command); self.assertTrue(result.ok, result.summary)

    def start_material_activation(self, s, source, victim):
        self.priority(s, 'A'); current = s.state.cards[source.object_id]
        action = next(a for a in s.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
            if a['id'].startswith('activate:' + current.ref + ':'))
        command = {'action_id': action['id'], 'targets': [s.state.cards[victim.object_id].ref], 'pay': 'auto'}
        self.reject(s, 'D', command); result = s.act('pilot:A', command); self.assertTrue(result.ok, result.summary)
        self.assertEqual('graveyard', s.state.cards[source.object_id].zone)

    def test_actual_stifle_of_material_activation_or_recover_never_spends_half_life_or_moves_original(self):
        for index, counter_activation in enumerate((True, False)):
            with self.subTest(counter_activation=counter_activation):
                extras = (('Stifle', 'B', 'hand', 'half-recover-stifle'), ('Island', 'B', 'battlefield', 'half-recover-blue'))
                s, private, source, victim, black = self.world(734020 + index, extras=extras)
                self.start_material_activation(s, source, victim)
                if not counter_activation:self.resolve_top(s)
                response = next(c for c in s.state.cards.values() if c.ref == 'half-recover-stifle')
                self.cast(s, 'B', response, [s.state.stack[-1].ref]); self.assertIsNone(self.resolve(s))
                self.assertEqual(13, s.state.players['A'].life); self.assertEqual('graveyard', s.state.cards[source.object_id].zone)
                self.assertEqual('battlefield' if counter_activation else 'graveyard', s.state.cards[victim.object_id].zone)
                self.assertFalse(s.state.stack); self.finish(s, private)

    def test_actual_exiled_and_reentered_physical_source_is_not_returned_or_exiled_by_old_recover(self):
        for index, paid in enumerate((True, False)):
            with self.subTest(paid=paid):
                extras = (("Tormod's Crypt", 'B', 'battlefield', 'half-graveyard-exile'),
                    ('Pull from Eternity', 'A', 'hand', 'half-graveyard-return'), ('Plains', 'A', 'battlefield', 'half-return-white'))
                s, private, source, victim, black = self.world(734022 + index, extras=extras)
                self.start_material_activation(s, source, victim); self.resolve_top(s)
                original = s.state.cards[source.object_id].logical_object_id
                crypt = next(c for c in s.state.cards.values() if c.ref == 'half-graveyard-exile')
                self.activate_support(s, crypt, targets=('A',)); self.resolve_top(s)
                self.assertEqual('exile', s.state.cards[source.object_id].zone)
                pull = next(c for c in s.state.cards.values() if c.ref == 'half-graveyard-return')
                self.cast(s, 'A', pull, [s.state.cards[source.object_id].ref]); self.resolve_top(s)
                current = s.state.cards[source.object_id]; self.assertEqual('graveyard', current.zone)
                self.assertNotEqual(original, current.logical_object_id); identity = current.logical_object_id
                self.assertEqual('semantic.choice', self.resolve(s).kind)
                self.choose_price(s, paid); self.assertIsNone(self.resolve(s))
                self.assertEqual(6 if paid else 13, s.state.players['A'].life)
                self.assertEqual(('graveyard', identity), (s.state.cards[source.object_id].zone, s.state.cards[source.object_id].logical_object_id))
                self.finish(s, private)


    def test_actual_fault_after_life_payment_or_return_restores_price_choice_life_and_source_then_retries(self):
        import quorune.life_state as cost_owner
        from quorune.errors import GameRuleError
        from quorune.record import authoritative_state_hash
        for index, after_return in enumerate((False, True)):
            with self.subTest(after_return=after_return):
                s, private, source, victim, black = self.world(734030 + index)
                self.activate_material(s, source, victim, black)
                before = authoritative_state_hash(s.state); observed = []
                if after_return:
                    owner = s.engine; name = 'move_objects_simultaneously_intent'; original = getattr(owner, name)
                    def fault(*args, **kwargs):
                        result = original(*args, **kwargs)
                        if s.state.cards[source.object_id].zone == 'hand':
                            observed.append((s.state.players['A'].life, 'hand'))
                            raise GameRuleError('Injected after half-life payment and return')
                        return result
                else:
                    owner = cost_owner; name = 'commit_life_payment'; original = getattr(owner, name)
                    def fault(*args, **kwargs):
                        result = original(*args, **kwargs)
                        observed.append((s.state.players['A'].life, s.state.cards[source.object_id].zone))
                        raise GameRuleError('Injected after half-life payment before return')
                with patch.object(owner, name, side_effect=fault):
                    result = s.act('pilot:A', {'action_id': 'choose', 'pay': True})
                self.assertFalse(result.ok); self.assertIn('Injected after half-life', result.summary)
                self.assertEqual([(6, 'hand' if after_return else 'graveyard')], observed)
                self.assertEqual(before, authoritative_state_hash(s.state)); self.assertEqual(13, s.state.players['A'].life)
                self.assertEqual('graveyard', s.state.cards[source.object_id].zone)
                s = self.replay(s, load=True); self.choose_price(s, True); self.assertIsNone(self.resolve(s))
                self.assertEqual((6, 'hand'), (s.state.players['A'].life, s.state.cards[source.object_id].zone))
                self.finish(s, private)

    def test_fresh_native_downward_half_price_is_killed_after_positive_complete_original_journey(self):
        from quorune.fractional_quantity_model import HalfQuantitySpec
        self.test_actual_printed_bbb_cast_sacrifice_destroy_then_thirteen_life_costs_seven_and_recovers_privately()
        observed = []
        def downward(spec, value):
            observed.append(value); return max(0, value) // 2
        with patch.object(HalfQuantitySpec, 'amount', downward):
            with self.assertRaises(AssertionError):
                self.test_actual_printed_bbb_cast_sacrifice_destroy_then_thirteen_life_costs_seven_and_recovers_privately()
        self.assertTrue(observed); self.assertEqual({13}, set(observed))

    def test_genuinely_verified_fraction_keyword_price_and_source_result_withdrawals_preserve_fixed_mana_recover(self):
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
        doc = read_evidence_document(ROOT / 'platform/capability-evidence-declarations.json'); known = set(discover_tests(ROOT).values())
        for missing in ('keyword.recover.half_current_life', 'cost.life.half_current_resolution', 'zone.move.recover_source'):
            changed = deepcopy(raw); next(c for c in changed['capabilities'] if c['id'] == missing).update(
                status='blocked', blockers=['Half-life Recover owner withdrawn'])
            registry = CapabilityRegistry(changed); index = build_index(registry_value=changed, declaration_source=doc, discovered_test_ids=known)
            registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
            controls = [("Garza's Assassin", False), ('Grizzly Bears', True)]
            if missing != 'zone.move.recover_source':controls.append(('Grim Harvest', True))
            for name, expected in controls:
                record = self.db.lookup(name); semantics = SemanticRegistry()
                register_generated_programs(self.db, semantics, (record,), capability_registry=registry, capability_profile='commander_review',
                    promote_exact_runtime_handlers=True, promote_exact_trigger_programs=True,
                    promote_exact_effect_programs=True, promote_exact_capability_declarations=True)
                parent = compile_best_available_card_program(self.db, record, semantic_registry=semantics,
                    capability_registry=registry, capability_profile='commander_review')
                bound = bind_card_program_runtime(parent, capability_registry=registry, profile='commander_review')
                self.assertEqual(expected, bound['strict_capability_ready'], (missing, name, bound['blockers']))


    def test_actual_legal_ability_copy_and_original_reprice_after_first_payment_without_moving_returned_card(self):
        extras = (('Strionic Resonator', 'A', 'battlefield', 'half-life-copy'), ('Island', 'A', 'battlefield', 'half-copy-mana1'),
            ('Forest', 'A', 'battlefield', 'half-copy-mana2'))
        s, private, source, victim, black = self.world(734040, life=13, extras=extras)
        self.start_material_activation(s, source, victim); self.resolve_top(s)
        original = next(i.ref for i in s.state.stack if i.source_object_id == source.object_id)
        copier = next(c for c in s.state.cards.values() if c.ref == 'half-life-copy')
        self.activate_support(s, copier, seat='A', targets=(original,))
        self.assertEqual('semantic.choice', self.resolve(s).kind); self.assertEqual(('A',), tuple(s.state.pending_decision.actors))
        self.choose_price(s, True)
        self.assertEqual(6, s.state.players['A'].life); self.assertEqual('hand', s.state.cards[source.object_id].zone)
        identity = s.state.cards[source.object_id].logical_object_id
        self.assertEqual('semantic.choice', self.resolve(s).kind); self.assertEqual(('A',), tuple(s.state.pending_decision.actors))
        self.choose_price(s, True); self.assertIsNone(self.resolve(s))
        self.assertEqual(3, s.state.players['A'].life); self.assertEqual(('hand', identity),
            (s.state.cards[source.object_id].zone, s.state.cards[source.object_id].logical_object_id))
        self.finish(s, private)


    def test_actual_one_life_payment_is_legal_and_returns_original_before_state_based_player_loss(self):
        s, private, source, victim, black = self.world(734041, life=1)
        self.activate_material(s, source, victim, black); observed = []
        original = s.engine.move_objects_simultaneously_intent
        def observe(*args, **kwargs):
            result = original(*args, **kwargs)
            card = s.state.cards.get(source.object_id)
            if card is not None and card.zone == 'hand':observed.append((s.state.players['A'].life, card.zone))
            return result
        with patch.object(s.engine, 'move_objects_simultaneously_intent', side_effect=observe):self.choose_price(s, True)
        self.assertIsNone(self.resolve(s)); self.assertEqual([(0, 'hand')], observed)
        self.assertFalse(s.state.players['A'].in_game); self.assertTrue(s.state.players['B'].in_game)
        self.finish(s, private)


if __name__ == '__main__':unittest.main()
