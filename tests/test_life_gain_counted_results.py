"""Pinned Cradle rulings: one event magnitude, target before optional payment."""
from types import SimpleNamespace as Row
import unittest
from unittest.mock import patch
from quorune.compiler.life_gain_counted_results import life_gain_counted_counter_result

TEXT = 'Put a +1/+1 counter on target creature for each 1 life you gained.'


def binding(**change):
    return Row(**({'event': Row(value='life.gained'), 'variant': 'controller_life_gain',
        'event_condition': {'field': 'player', 'op': 'eq', 'value': '$source.controller'}} | change))


def compile_result(text=TEXT, scope=None):
    from quorune.compiler.scalar_effect_amounts import trigger_scalar_effect_amount_template
    from quorune.oracle_ir import _effect_template
    return trigger_scalar_effect_amount_template(text, binding=scope or binding(), source_name='Life Event Source',
        compile_fixed=lambda value: _effect_template(value, card_name='Life Event Source'))


class LifeGainCountedCounterCompilerTests(unittest.TestCase):
    def test_exact_counted_controller_event_delegates_to_existing_that_many_counter_grammar(self):
        self.assertEqual('Put that many +1/+1 counters on target creature.', life_gain_counted_counter_result(TEXT, binding=binding()))
        for receiver in ('target creature', 'this creature', 'enchanted creature'):
            self.assertIn(receiver, life_gain_counted_counter_result(TEXT.replace('target creature', receiver), binding=binding()))
        self.assertEqual('Put that many charge counters on target creature.',
            life_gain_counted_counter_result(TEXT.replace('+1/+1', 'charge'), binding=binding()))

    def test_foreign_missing_batch_first_event_and_extra_condition_scopes_do_not_invent_your_gain(self):
        for change in ({'event': Row(value='damage.dealt')}, {'variant': 'opponent_life_gain'},
                {'variant': 'controller_first_life_gain'}, {'event_condition': None},
                {'event_condition': {'field': 'player', 'op': 'eq', 'value': 'B'}},
                {'event_condition': {'all': [binding().event_condition]}},
                {'event_condition': binding().event_condition | {'extra': True}}):
            with self.subTest(change=change): self.assertIsNone(life_gain_counted_counter_result(TEXT, binding=binding(**change)))

    def test_history_foreign_antecedent_arbitrary_receiver_multiplier_and_material_riders_stay_unrepresented(self):
        for bad in (TEXT.replace('you gained', 'you gained this turn'), TEXT.replace('you gained', 'that player gained'),
                TEXT.replace('each 1 life', 'each 2 life'), TEXT.replace('target creature', 'target player'),
                TEXT.replace('target creature', 'target creature. Draw a card'), TEXT + ' Draw a card.',
                TEXT.replace('for each 1 life', 'for each life')):
            with self.subTest(text=bad): self.assertIsNone(life_gain_counted_counter_result(bad, binding=binding()))

    def test_real_scalar_integration_keeps_event_amount_target_and_single_counter_instruction(self):
        from quorune.scalar_effect_amount_model import ScalarEffectAmountSpec, ScalarAmountOrigin
        result = compile_result(); self.assertIsNotNone(result)
        self.assertEqual(1, len(result[1])); effect, = result[1]
        self.assertEqual('place_counters', effect['op'])
        self.assertEqual(ScalarEffectAmountSpec(ScalarAmountOrigin.EVENT_AMOUNT).to_dict(), effect['amount'])
        self.assertEqual(1, result[2]['count']); self.assertEqual(['battlefield'], result[2]['zones'])

    def test_old_that_many_form_is_unchanged_and_wrong_event_still_rejects_new_surface(self):
        original = compile_result('Put that many +1/+1 counters on target creature.')
        self.assertEqual(original, compile_result())
        self.assertIsNone(compile_result(scope=binding(event=Row(value='creature.dies'))))

    def test_fresh_counted_production_omission_is_killed_after_positive_typed_integration(self):
        self.assertIsNotNone(compile_result())
        with patch('quorune.compiler.life_gain_counted_results.life_gain_counted_counter_result', return_value=None):
            with self.assertRaises(AssertionError): self.assertIsNotNone(compile_result())
            self.assertIsNotNone(compile_result('Put that many +1/+1 counters on target creature.'))


def paid_result():
    from quorune.compiler.life_gain_counted_results import life_gain_counted_counter_payment
    return life_gain_counted_counter_payment('You may pay {1}{W}. If you do, ' + TEXT, binding=binding(),
        compile_result=lambda child: (compile_result(child), False))[0]


class LifeGainCounterPaymentTests(unittest.TestCase):
    def test_explicit_marker_preserves_fixed_price_target_and_one_event_scalar(self):
        from quorune.rules.life_gain_counted_results import LIFE_GAIN_COUNTER_RESULT_FIELD as FIELD, life_gain_counter_payment_projection
        result = paid_result(); effect, = result[1]
        self.assertEqual(1, effect[FIELD]); self.assertEqual(2, effect['schema_version'])
        self.assertEqual('mana', effect['payment']['kind']); self.assertEqual(1, effect['cost']['GENERIC']); self.assertEqual(1, effect['cost']['W'])
        projected = life_gain_counter_payment_projection(effect)
        self.assertNotIn(FIELD, projected); self.assertIn(FIELD, effect)
        self.assertEqual(effect['effects'], projected['effects']); self.assertEqual(effect['payment'], projected['payment'])

    def test_unknown_marker_price_target_and_scalar_variants_fail_closed(self):
        from copy import deepcopy
        from quorune.rules.life_gain_counted_results import LIFE_GAIN_COUNTER_RESULT_FIELD as FIELD, life_gain_counter_payment_projection
        result = paid_result(); original = result[1][0]
        for change in ({FIELD: True}, {FIELD: 2}, {'schema_version': 1}, {'extra': None}, {'cost': original['cost'] | {'W': 2}}):
            with self.subTest(change=change), self.assertRaises(ValueError): life_gain_counter_payment_projection(original | change)
        for change in ({'card': '$target.1'}, {'source': '$controller'}, {'extra': None}):
            changed = deepcopy(original); changed['effects'][0].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError): life_gain_counter_payment_projection(changed)

    def test_whole_frame_and_each_dependency_are_required_without_coverage(self):
        from quorune.rules.life_gain_counted_results import life_gain_counter_payment_program_dependencies
        result = paid_result(); values = dict(event='life.gained', active_zone='battlefield', event_condition=binding().event_condition,
            effects=result[1], target_schema=result[2], cost_schema=None, handlers=(), coverage=())
        required = life_gain_counter_payment_program_dependencies(Row(**values))
        for cap in ('quantity_expression.scalar_effect_amount', 'effect.choice.optional_fixed_mana_payment', 'counter.producer.fixed_effect', 'trigger.event.normalized_life_gain'):
            self.assertIn(cap, required)
        for change in ({'event': 'damage.dealt'}, {'active_zone': 'graveyard'}, {'event_condition': None},
                {'event_condition': {'field': 'player', 'op': 'eq', 'value': 'B'}}, {'target_schema': None},
                {'effects': (), 'handlers': ({'nested': result[1][0]},)}):
            with self.subTest(change=change), self.assertRaises(ValueError): life_gain_counter_payment_program_dependencies(Row(**(values | change)))

    def test_omitted_marker_cannot_turn_into_an_unproved_v2_scalar_payment(self):
        from quorune.rules.life_gain_counted_results import LIFE_GAIN_COUNTER_RESULT_FIELD as FIELD, life_gain_counter_payment_program_dependencies
        result = paid_result(); effect = {k: v for k, v in result[1][0].items() if k != FIELD}
        values = dict(event='life.gained', active_zone='battlefield', event_condition=binding().event_condition,
            effects=(effect,), target_schema=result[2], cost_schema=None, handlers=(), coverage=())
        with self.assertRaises(ValueError): life_gain_counter_payment_program_dependencies(Row(**values))

    def test_resolved_projection_keeps_sealed_positive_count_and_rejects_unresolved_amount(self):
        from copy import deepcopy
        from quorune.rules.life_gain_counted_results import LIFE_GAIN_COUNTER_RESULT_FIELD as FIELD, life_gain_counter_payment_projection
        effect = deepcopy(paid_result()[1][0]); effect['player'] = 'B'
        effect['effects'][0].update(card='public-target', source='old-source', amount=3)
        projected = life_gain_counter_payment_projection(effect, resolved=True)
        self.assertEqual('B', projected['player']); self.assertEqual(3, projected['effects'][0]['amount']); self.assertNotIn(FIELD, projected)
        for value in (True, 0, -1, 3.0):
            invalid = deepcopy(effect); invalid['effects'][0]['amount'] = value
            with self.assertRaises(ValueError): life_gain_counter_payment_projection(invalid, resolved=True)


from pathlib import Path
from dataclasses import replace
import json, tempfile
from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_turn_life_loss_recovery as recovery_helpers


class LifeGainCountedCounterNativeTests(InheritedNativeFixture, unittest.TestCase):
    start = recovery_helpers.TurnLifeLossRecoveryNativeTests.start
    reject = recovery_helpers.TurnLifeLossRecoveryNativeTests.reject
    finish = recovery_helpers.TurnLifeLossRecoveryNativeTests.finish

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(); folder = Path(cls.temporary.name)
        base = json.loads((ROOT / 'tests/fixtures/bound-effect-program-cards.json').read_text(encoding='utf-8'))
        base['cards'] = [r for r in base['cards'] if r['name'] == 'Generic Bound Plains']; base['rulings'] = []
        basic = folder / 'basic.json'; basic.write_text(json.dumps(base), encoding='utf-8')
        build_fixture_database([basic, ROOT / 'tests/fixtures/life-gain-counted-result-cards.json'], folder / 'life-gain.sqlite3')
        cls.db = CardDatabase(folder / 'life-gain.sqlite3'); cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Life event counted counters', [DeckEntry('Sliver Hivelord', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 30)], ['Sliver Hivelord'])

    def world(self, seed, *, mana=None, extras=()):
        s, private = self.start(seed); source = self.add(s.engine, 'Cradle of Vitality', zone='hand')
        gain = self.add(s.engine, 'Revitalize', zone='hand', ref='initial-life-gain')
        own = self.add(s.engine, 'Grizzly Bears', ref='own-life-counter-target')
        foreign = self.add(s.engine, 'Walking Corpse', seat='B', ref='foreign-life-counter-target')
        for card in (source, gain, own, foreign): self.assert_closed(s, card)
        for name, seat, zone, ref in extras: self.assert_closed(s, self.add(s.engine, name, seat=seat, zone=zone, ref=ref))
        self.prepare(s, mana=mana or {'C': 5, 'W': 3}); self.checkpoint(s)
        before = sum(s.state.players['A'].mana_pool.values()); self.cast(s, 'A', source); self.assertIsNone(self.resolve(s))
        self.assertEqual(before - 4, sum(s.state.players['A'].mana_pool.values()))
        return s, private, source, gain, own, foreign

    def gain_and_target(self, s, gain, target, *, draws=1):
        hand = len(s.state.players['A'].zones['hand'])
        self.cast(s, 'A', gain); decision = self.resolve(s); self.assertEqual('semantic.target', decision.kind)
        self.assertEqual('graveyard', s.state.cards[gain.object_id].zone)
        self.assertEqual(hand - 1 + draws, len(s.state.players['A'].zones['hand']))
        self.target(s, 'A', [target.ref]); self.assertEqual(0, s.state.cards[target.object_id].counters.get('+1/+1', 0))

    def choose_payment(self, s, pay):
        command = {'action_id': 'choose', 'pay': pay}; self.reject(s, 'D', command)
        result = s.act('pilot:A', command); self.assertTrue(result.ok, result.summary)

    def test_full_original_and_existing_granted_magnitude_controls_admit_normally_with_each_owner_required(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        s, _ = self.start(728000)
        for name in ('Cradle of Vitality', 'Sunbond', 'Light of Promise'):
            self.assert_closed(s, self.add(s.engine, name, zone='hand'))
        source = self.db.lookup('Cradle of Vitality')
        program = next(p for p in s.engine.semantics.programs_for_oracle(source.oracle_id) if p.effects)
        for cap in ('counter.producer.fixed_event_trigger', 'counter.producer.fixed_effect',
                'effect.choice.optional_fixed_mana_payment', 'quantity_expression.scalar_effect_amount',
                'trigger.event.normalized_life_gain', 'target.revalidate_resolution'):
            deps = tuple(c for c in program.capability_dependencies if c != cap)
            changed = replace(program, coverage=(), capability_dependencies=deps,
                capability_closure=self.registry.closure(deps, profile='commander_review').to_dict())
            bound = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile='commander_review')
            self.assertFalse(bound['strict']); self.assertIn('capability:undeclared_runtime_dependency:' + cap, bound['blockers'])

    def test_actual_printed_cast_and_gain_choose_target_before_one_white_mana_payment_and_replay_privately(self):
        s, private, source, gain, own, foreign = self.world(728001)
        self.gain_and_target(s, gain, foreign); self.assertEqual(43, s.state.players['A'].life)
        before = sum(s.state.players['A'].mana_pool.values()); self.assertEqual('semantic.choice', self.resolve(s).kind)
        self.assertEqual(before, sum(s.state.players['A'].mana_pool.values()))
        s = self.replay(s, load=True); self.choose_payment(s, True); self.assertIsNone(self.resolve(s))
        self.assertEqual(before - 2, sum(s.state.players['A'].mana_pool.values()))
        self.assertEqual(3, s.state.cards[foreign.object_id].counters.get('+1/+1', 0))
        self.assertEqual(0, s.state.cards[own.object_id].counters.get('+1/+1', 0)); self.finish(s, private)

    def test_actual_decline_or_unaffordable_price_places_no_counters_and_never_spends_mana(self):
        for index, available in enumerate((True, False)):
            with self.subTest(available=available):
                s, private, source, gain, own, _ = self.world(728010 + index,
                    mana={'C': 5 if available else 4, 'W': 3 if available else 2})
                self.gain_and_target(s, gain, own); self.assertEqual('semantic.choice', self.resolve(s).kind)
                before = sum(s.state.players['A'].mana_pool.values())
                if not available: self.reject(s, 'A', {'action_id': 'choose', 'pay': True})
                self.choose_payment(s, False); self.assertIsNone(self.resolve(s))
                self.assertEqual(before, sum(s.state.players['A'].mana_pool.values()))
                self.assertEqual(0, s.state.cards[own.object_id].counters.get('+1/+1', 0)); self.finish(s, private)

    def test_actual_two_unequal_life_events_keep_independent_targets_amounts_and_prices(self):
        extras = (('Sacred Nectar', 'A', 'hand', 'second-life-gain'), ('Vedalken Orrery', 'A', 'battlefield', 'life-response-flash'))
        s, private, source, gain, own, foreign = self.world(728020, mana={'C': 7, 'W': 5}, extras=extras)
        self.gain_and_target(s, gain, own)
        second = next(c for c in s.state.cards.values() if c.ref == 'second-life-gain')
        self.gain_and_target(s, second, foreign, draws=0); self.assertEqual(47, s.state.players['A'].life)
        self.assertEqual(2, self.trigger_count(s, source)); self.assertEqual('semantic.choice', self.resolve(s).kind)
        self.choose_payment(s, True); self.assertEqual(4, s.state.cards[foreign.object_id].counters.get('+1/+1', 0))
        self.assertEqual(0, s.state.cards[own.object_id].counters.get('+1/+1', 0))
        self.assertEqual('semantic.choice', self.resolve(s).kind); self.choose_payment(s, True); self.assertIsNone(self.resolve(s))
        self.assertEqual(3, s.state.cards[own.object_id].counters.get('+1/+1', 0)); self.assertEqual(4, s.state.cards[foreign.object_id].counters.get('+1/+1', 0))
        self.assertEqual(0, sum(s.state.players['A'].mana_pool.values())); self.finish(s, private)

    def test_actual_counter_replacement_applies_once_to_one_event_sized_placement(self):
        s, private, source, gain, own, foreign = self.world(728021,
            extras=(('Hardened Scales', 'A', 'battlefield', 'single-placement-replacement'),))
        self.gain_and_target(s, gain, own); self.assertEqual('semantic.choice', self.resolve(s).kind)
        self.choose_payment(s, True); self.assertIsNone(self.resolve(s))
        self.assertEqual(4, s.state.cards[own.object_id].counters.get('+1/+1', 0))
        self.assertEqual(0, s.state.cards[foreign.object_id].counters.get('+1/+1', 0)); self.finish(s, private)

    def test_actual_target_bounce_before_resolution_removes_trigger_without_offering_or_paying_price(self):
        extras = (('Unsummon', 'B', 'hand', 'invalidate-counter-target'), ('Island', 'B', 'battlefield', 'invalidate-blue'))
        s, private, source, gain, own, foreign = self.world(728022, extras=extras)
        self.gain_and_target(s, gain, own); before = dict(s.state.players['A'].mana_pool)
        response = next(c for c in s.state.cards.values() if c.ref == 'invalidate-counter-target')
        self.cast(s, 'B', response, [own.ref]); self.assertIsNone(self.resolve(s))
        self.assertFalse(s.state.stack); self.assertEqual('hand', s.state.cards[own.object_id].zone)
        self.assertEqual(before, dict(s.state.players['A'].mana_pool)); self.assertEqual(0, s.state.cards[foreign.object_id].counters.get('+1/+1', 0))
        self.finish(s, private)

    def test_actual_source_destruction_keeps_paid_result_while_stifle_suppresses_payment_and_counters(self):
        for index, name in enumerate(('Disenchant', 'Stifle')):
            with self.subTest(name=name):
                extras = ((name, 'B', 'hand', 'life-counter-response'), ('Island', 'B', 'battlefield', 'counter-response-blue'),
                    ('Plains', 'B', 'battlefield', 'counter-response-white'))
                s, private, source, gain, own, _ = self.world(728030 + index, extras=extras)
                self.gain_and_target(s, gain, own); before = sum(s.state.players['A'].mana_pool.values())
                response = next(c for c in s.state.cards.values() if c.ref == 'life-counter-response')
                self.cast(s, 'B', response, [source.ref if index == 0 else s.state.stack[-1].ref])
                if index == 0:
                    self.assertEqual('semantic.choice', self.resolve(s).kind)
                    self.assertEqual('graveyard', s.state.cards[source.object_id].zone)
                    self.choose_payment(s, True); self.assertIsNone(self.resolve(s))
                    self.assertEqual(3, s.state.cards[own.object_id].counters.get('+1/+1', 0))
                    self.assertEqual(before - 2, sum(s.state.players['A'].mana_pool.values()))
                else:
                    self.assertIsNone(self.resolve(s)); self.assertFalse(s.state.stack)
                    self.assertEqual(0, s.state.cards[own.object_id].counters.get('+1/+1', 0))
                    self.assertEqual(before, sum(s.state.players['A'].mana_pool.values()))
                self.finish(s, private)

    def test_actual_opponent_gain_and_prevented_controller_gain_do_not_offer_a_counter_price(self):
        for index, prohibited in enumerate((False, True)):
            with self.subTest(prohibited=prohibited):
                extras = (('Havoc Festival', 'B', 'battlefield', 'no-life-gain'),) if prohibited else (
                    ('Revitalize', 'B', 'hand', 'foreign-life-gain'), ('Plains', 'B', 'battlefield', 'foreign-gain-white'),
                    ('Island', 'B', 'battlefield', 'foreign-gain-generic'))
                s, private, source, gain, own, foreign = self.world(728040 + index, extras=extras)
                spell = gain if prohibited else next(c for c in s.state.cards.values() if c.ref == 'foreign-life-gain')
                self.cast(s, 'A' if prohibited else 'B', spell); self.assertIsNone(self.resolve(s))
                self.assertFalse(s.state.stack); self.assertEqual(0, self.trigger_count(s, source))
                self.assertEqual(40, s.state.players['A'].life)
                if not prohibited: self.assertEqual(43, s.state.players['B'].life)
                for card in (own, foreign): self.assertEqual(0, s.state.cards[card.object_id].counters.get('+1/+1', 0))
                self.finish(s, private)

    def test_actual_post_counter_commit_fault_restores_payment_choice_mana_and_counters_then_retries(self):
        import quorune.counter_placement as owner
        from quorune.errors import GameRuleError
        from quorune.record import authoritative_state_hash
        s, private, source, gain, own, _ = self.world(728050)
        self.gain_and_target(s, gain, own); self.assertEqual('semantic.choice', self.resolve(s).kind)
        original = owner.commit_counter_placement_plan; observed = []
        before = authoritative_state_hash(s.state); mana = dict(s.state.players['A'].mana_pool)
        def fault(*args, **kwargs):
            result = original(*args, **kwargs)
            observed.append((sum(s.state.players['A'].mana_pool.values()), s.state.cards[own.object_id].counters.get('+1/+1', 0)))
            raise GameRuleError('Injected after paid life-event counter commit')
        with patch.object(owner, 'commit_counter_placement_plan', side_effect=fault):
            result = s.act('pilot:A', {'action_id': 'choose', 'pay': True})
        self.assertFalse(result.ok); self.assertIn('Injected after paid', result.summary)
        self.assertEqual(before, authoritative_state_hash(s.state)); self.assertEqual([(0, 3)], observed)
        self.assertEqual(mana, dict(s.state.players['A'].mana_pool)); self.assertEqual(0, s.state.cards[own.object_id].counters.get('+1/+1', 0))
        s = self.replay(s, load=True); self.choose_payment(s, True); self.assertIsNone(self.resolve(s))
        self.assertEqual(3, s.state.cards[own.object_id].counters.get('+1/+1', 0)); self.assertEqual(0, sum(s.state.players['A'].mana_pool.values()))
        self.finish(s, private)

    def test_fresh_native_event_amount_increment_is_killed_after_positive_complete_original_journey(self):
        import quorune.semantic_choices.life_gain_counted_results as owner
        self.test_actual_printed_cast_and_gain_choose_target_before_one_white_mana_payment_and_replay_privately()
        original = owner.life_gain_counter_payment_projection; observed = []
        def increment(effect, **kwargs):
            result = original(effect, **kwargs); observed.append(result['effects'][0]['amount'])
            result['effects'][0]['amount'] += 1; return result
        with patch.object(owner, 'life_gain_counter_payment_projection', side_effect=increment):
            with self.assertRaises(AssertionError):
                self.test_actual_printed_cast_and_gain_choose_target_before_one_white_mana_payment_and_replay_privately()
        self.assertTrue(observed); self.assertEqual({3}, set(observed))

    def test_genuinely_verified_scalar_price_counter_and_life_event_withdrawals_block_whole_cradle(self):
        from copy import deepcopy
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests
        from quorune.oracle_ir import register_generated_programs
        from quorune.semantics import SemanticRegistry
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.card_programs import bind_card_program_runtime
        raw = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        doc = read_evidence_document(ROOT / 'platform/capability-evidence-declarations.json'); known = set(discover_tests(ROOT).values())
        for missing in ('quantity_expression.scalar_effect_amount', 'effect.choice.optional_fixed_mana_payment',
                'counter.producer.fixed_effect', 'trigger.event.normalized_life_gain'):
            changed = deepcopy(raw); next(c for c in changed['capabilities'] if c['id'] == missing).update(status='blocked', blockers=['Paid life-event counter owner withdrawn'])
            registry = CapabilityRegistry(changed); index = build_index(registry_value=changed, declaration_source=doc, discovered_test_ids=known)
            registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
            controls = [('Cradle of Vitality', False), ('Grizzly Bears', True)]
            if missing == 'effect.choice.optional_fixed_mana_payment': controls += [('Sunbond', True), ('Light of Promise', True)]
            for name, expected in controls:
                card = self.db.lookup(name); semantics = SemanticRegistry()
                register_generated_programs(self.db, semantics, (card,), capability_registry=registry, capability_profile='commander_review',
                    promote_exact_runtime_handlers=True, promote_exact_trigger_programs=True, promote_exact_effect_programs=True, promote_exact_capability_declarations=True)
                program = compile_best_available_card_program(self.db, card, semantic_registry=semantics, capability_registry=registry, capability_profile='commander_review')
                result = bind_card_program_runtime(program, capability_registry=registry, profile='commander_review')
                self.assertEqual(expected, result['strict_capability_ready'], (missing, name, result['blockers']))


if __name__ == '__main__':
    unittest.main()
