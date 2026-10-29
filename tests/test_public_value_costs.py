"""Pinned CR107/118/601/602 and original public-value cost rulings."""

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
import test_job_select as actions
import test_public_target_spell_costs as cast_actions
import test_boast_restrictions as boast_actions
from test_qualified_zone_event_queries import query_record
from quorune.activation_cost_modifiers import (FixedActivationCostModifierSpec,
    PUBLIC_QUANTITY_ACTIVATION_COST_CAPABILITY, activation_cost_prices)
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.object_predicate import ObjectQuerySpec
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.activation.ability_kinds import activation_matches_cost_scope
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.public_value_costs import PUBLIC_SOURCE_POWER_COST_CAPABILITY
from quorune.self_cast_reductions import CastReductionMetric, CastReductionMetricKind, cast_reduction_multiplier
from quorune.target_cost_subject import TargetCostSubjectSpec
from quorune.semantic_runtime.activation_costs import (FixedActivationCostModifierHandler,
    TargetedActivationCostModifierHandler, PublicQuantityActivationCostModifierHandler)
from scripts.build_test_database import build_fixture_database


class PublicValueCostModelTests(unittest.TestCase):
    def test_source_power_metric_is_closed_and_historical_payloads_remain_exact(self):
        metric = CastReductionMetric(CastReductionMetricKind.SOURCE_POWER, schema_version=4)
        self.assertEqual(metric, CastReductionMetric.from_dict(metric.to_dict()))
        for changes in ({'schema_version': 1}, {'schema_version': 3}, {'minimum': 1},
                {'queries': [{}]}, {'counter_name': 'charge'}, {'aggregate': None}, {'unknown': 1}):
            with self.subTest(changes=changes), self.assertRaises((TypeError, ValueError, KeyError)):
                CastReductionMetric.from_dict({**metric.to_dict(), **changes})
        old = CastReductionMetric(CastReductionMetricKind.OPPONENT_COUNT).to_dict()
        self.assertEqual(old, CastReductionMetric.from_dict(old).to_dict())
        for version in (1, 2):
            spec = FixedActivationCostModifierSpec(-2, schema_version=version, subject_relation='any',
                **({'target_condition': TargetCostSubjectSpec('source')} if version == 2 else {}))
            value = spec.to_dict()
            self.assertNotIn('multiplier', value)
            self.assertEqual(value, FixedActivationCostModifierSpec.from_dict(value).to_dict())
        spec = FixedActivationCostModifierSpec(-1, schema_version=3, subject_relation='any', multiplier=metric.to_dict())
        descriptor = {'handler_id': PublicQuantityActivationCostModifierHandler().handler_id,
            'schema_version': 3, 'event': 'activation.mana_cost.modify', 'modifier': spec.to_dict()}
        self.assertEqual(spec, PublicQuantityActivationCostModifierHandler().validate(descriptor))
        hidden = {**metric.to_dict(), 'kind': 'opponent_hand_count', 'schema_version': 2, 'minimum': 1}
        with self.assertRaises(ValueError):
            PublicQuantityActivationCostModifierHandler().validate({**descriptor,
                'modifier': {**spec.to_dict(), 'multiplier': hidden}})
        for handler in (FixedActivationCostModifierHandler(), TargetedActivationCostModifierHandler()):
            with self.assertRaises(ValueError):
                handler.validate({**descriptor, 'handler_id': handler.handler_id, 'schema_version': handler.schema_version})
        for changes in ({'multiplier': None}, {'generic_adjustment': 1}, {'target_condition': None}, {'schema_version': 1}):
            with self.assertRaises((TypeError, ValueError)):
                FixedActivationCostModifierSpec.from_dict({**spec.to_dict(), **changes})

    def test_current_power_clamps_negative_and_refuses_unknown_or_stale_sources(self):
        source = SimpleNamespace(zone='battlefield', phased_out=False, annotations={}, counters={})
        data = {'power': '2', 'type_line': 'Creature'}
        host = SimpleNamespace(_effective_card_data=lambda card: data,
            _type_parts=lambda text: (('creature',), (), ()))
        metric = CastReductionMetric(CastReductionMetricKind.SOURCE_POWER, schema_version=4)
        self.assertEqual(2, cast_reduction_multiplier(host, 'A', metric, source=source))
        source.counters['+1/+1'] = 3
        self.assertEqual(5, cast_reduction_multiplier(host, 'A', metric, source=source))
        source.annotations['until_end_of_turn'] = {'power': -8}
        self.assertEqual(0, cast_reduction_multiplier(host, 'A', metric, source=source))
        data['power'] = '*'
        with self.assertRaises(ValueError): cast_reduction_multiplier(host, 'A', metric, source=source)
        for field, value in (('zone', 'graveyard'), ('phased_out', True)):
            with self.subTest(field=field):
                setattr(source, field, value)
                with self.assertRaises(ValueError): cast_reduction_multiplier(host, 'A', metric, source=source)
                setattr(source, field, 'battlefield' if field == 'zone' else False)
        source.zone = 'hand'; source.counters['charge'] = 2
        counter = CastReductionMetric(CastReductionMetricKind.SOURCE_COUNTER_COUNT,
            counter_name='charge', schema_version=2)
        self.assertEqual(2, cast_reduction_multiplier(host, 'A', counter, source=source))

    def test_boast_identity_and_generic_minimum_do_not_change_other_fees(self):
        from quorune.abilities import parse_activated_abilities
        boast, = parse_activated_abilities(card_name='Public count fixture',
            oracle_text='Boast — {4}{R}: You gain 1 life.', keywords=('Boast',))
        self.assertTrue(activation_matches_cost_scope(None, None, boast, 'boast'))
        ordinary = replace(boast, boast=False)
        self.assertFalse(activation_matches_cost_scope(None, None, ordinary, 'boast'))
        for version in (1, 2):
            with self.assertRaises(ValueError): FixedActivationCostModifierSpec(-1, ability_scope='boast', schema_version=version)
        spec = FixedActivationCostModifierSpec(-8, minimum_mana=1, subject_relation='any')
        for base, snow, expected in (({'GENERIC': 3}, 0, 1), ({'GENERIC': 3, 'R': 1}, 0, 0), ({'GENERIC': 3}, 1, 0)):
            price, = activation_cost_prices(base, (spec,), snow_payment=snow)
            self.assertEqual(expected, price.requirements['GENERIC'])
            self.assertEqual(base.get('R', 0), price.requirements['R'])


class PublicValueCostCompilerTests(unittest.TestCase):
    def test_complete_clauses_preserve_subjects_and_reject_foreign_or_unrepresented_values(self):
        registry = load_default_capability_registry()
        clauses = (
            "Activated abilities of creatures you control cost {X} less to activate, where X is this creature's power. This effect can't reduce the mana in that cost to less than one mana.",
            'Boast abilities you activate cost {1} less to activate for each Dragon you control.',
            "Aura and Equipment spells you cast cost {X} less to cast, where X is this creature's power.",
            "Instant and sorcery spells you cast with mana value 4 or greater cost {X} less to cast, where X is this creature's power.",
            "As long as this creature is tapped, spells you cast cost {X} less to cast, where X is this creature's power.",
        )
        for clause in clauses:
            ir = compile_oracle_card(query_record(clause), capability_registry=registry)
            self.assertEqual('exact', ir.status, ir.material_residuals)
        modifier = compile_oracle_card(query_record(clauses[2]), capability_registry=registry).faces[0].nodes[0].handlers[0]['modifier']
        self.assertEqual(['aura', 'equipment'], modifier['predicates_any'][0]['subtypes_any'])
        for text in (clauses[0].replace("this creature's", "another creature's"),
                clauses[2].replace('power', 'toughness'), clauses[2] + ' Draw a card.',
                clauses[1].replace('Dragon you control', 'Dragon in your hand'),
                clauses[1].replace('Boast', 'ordinary'), clauses[3].replace('4 or greater', 'chosen or greater')):
            with self.subTest(text=text):
                self.assertNotEqual('exact', compile_oracle_card(query_record(text), capability_registry=registry).status)

    def test_missing_quantity_power_or_base_capabilities_fail_closed_with_unrelated_control(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests
        raw = json.loads((ROOT/'quorune/rules/capability-registry.json').read_text())
        declarations = read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        discovered = set(discover_tests(ROOT).values())
        clauses = ("Spells you cast cost {X} less to cast, where X is this creature's power.",
            "Activated abilities of creatures you control cost {X} less to activate, where X is this creature's power.")
        for omitted in (PUBLIC_SOURCE_POWER_COST_CAPABILITY, PUBLIC_QUANTITY_ACTIVATION_COST_CAPABILITY,
                'casting.cost.modifier.fixed_query', 'activation.mana_cost.static_generic', 'variant.monarch.designate'):
            value = deepcopy(raw)
            next(row for row in value['capabilities'] if row['id'] == omitted).update(status='blocked', blockers=['public value owner omitted'])
            registry = CapabilityRegistry(value)
            index = build_index(registry_value=value, declaration_source=declarations, discovered_test_ids=discovered)
            registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
            for i, clause in enumerate(clauses):
                expected = omitted == 'variant.monarch.designate' or (i == 0 and omitted in {PUBLIC_QUANTITY_ACTIVATION_COST_CAPABILITY, 'activation.mana_cost.static_generic'}) or (i == 1 and omitted == 'casting.cost.modifier.fixed_query')
                self.assertEqual(expected, compile_oracle_card(query_record(clause), capability_registry=registry).status == 'exact', (omitted, i))


class PublicValueCostNativeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = actions.JobSelectRuntimeTests.session
    add = actions.JobSelectRuntimeTests.add
    prepare = actions.JobSelectRuntimeTests.prepare
    cast = actions.JobSelectRuntimeTests.cast
    checkpoint = actions.JobSelectRuntimeTests.checkpoint
    resolve = actions.JobSelectRuntimeTests.resolve
    replay = actions.JobSelectRuntimeTests.replay
    assert_closed = actions.JobSelectRuntimeTests.assert_closed
    priority = cast_actions.PublicTargetSpellCostNativeTests.priority
    cast_reserved = cast_actions.PublicTargetSpellCostNativeTests.cast_reserved
    action = cast_actions.PublicTargetSpellCostNativeTests.action

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(); path = Path(cls.temporary.name)/'public-values.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/public-value-cost-cards.json'], path)
        cls.db = CardDatabase(path); cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Public value costs', [DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 24)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls): cls.db.close(); cls.temporary.cleanup()

    def requirements(self, session, card):
        return next(row['requirements'] for row in self.action(session, 'A', card)['cost_options'] if row['id'] == 'normal')

    def activation(self, session, card):
        offered = session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
        return next((row for row in offered if row['id'].startswith('activate:' + session.state.cards[card.object_id].ref + ':')), None)

    def test_actual_trainer_uses_current_power_and_keeps_paid_cost_after_provider_removal(self):
        session = self.session(118_610_001)
        source = self.add(session.engine, 'Golden-Tail Trainer', zone='hand')
        armor = self.add(session.engine, 'Argentum Armor', zone='hand')
        aura = self.add(session.engine, 'Arrest', zone='hand')
        body = self.add(session.engine, 'Memnite', seat='B')
        growth = self.add(session.engine, 'Giant Growth', zone='hand')
        removal = self.add(session.engine, 'Murder', seat='B', zone='hand')
        for index, name in enumerate(('Swamp', 'Swamp', 'Wastes')): self.add(session.engine, name, seat='B', ref='trainer-removal-mana-' + str(index))
        private = self.add(session.engine, 'Giant Growth', seat='D', zone='hand', ref='trainer-private')
        self.assert_closed(session, source)
        self.prepare(session, mana={'C': 5, 'G': 2, 'W': 2}); self.checkpoint(session)
        self.cast_reserved(session, source, reserve={'G': 1, 'W': 1}); self.assertIsNone(self.resolve(session))
        self.assertEqual(5, self.requirements(session, armor)['GENERIC'])
        self.assertEqual((1, 1), (self.requirements(session, aura)['GENERIC'], self.requirements(session, aura)['W']))
        self.cast_reserved(session, growth, [source.ref], reserve={'W': 1}); self.assertIsNone(self.resolve(session))
        self.assertEqual(2, self.requirements(session, armor)['GENERIC'])
        self.assertEqual(0, self.requirements(session, aura)['GENERIC'])
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:C', {'action_id': 'cast:' + armor.ref, 'pay': 'auto'}).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        self.cast_reserved(session, armor, reserve={'W': 1})
        self.cast(session, 'B', removal, [source.ref])
        session = self.replay(session, load=True); self.assertIsNone(self.resolve(session))
        self.assertEqual('graveyard', session.state.cards[source.object_id].zone)
        self.assertEqual('battlefield', session.state.cards[armor.object_id].zone)
        self.assertEqual(2, self.requirements(session, aura)['GENERIC'])
        for seat in 'ABC': self.assertNotIn(private.ref, json.dumps(session.packet('pilot:' + seat, full=True)))
        self.replay(session, load=True)

    def test_actual_scarlet_witch_keeps_mana_value_spell_domain_and_opponent_costs(self):
        session = self.session(118_610_002)
        source = self.add(session.engine, 'The Scarlet Witch', zone='hand')
        high = self.add(session.engine, 'Concentrate', zone='hand')
        low = self.add(session.engine, 'Murder', zone='hand')
        equipment = self.add(session.engine, 'Argentum Armor', zone='hand')
        foreign = self.add(session.engine, 'Inspiration', seat='B', zone='hand')
        for index, name in enumerate(('Island', 'Wastes', 'Wastes', 'Wastes')): self.add(session.engine, name, seat='B', ref='witch-opponent-mana-' + str(index))
        self.assert_closed(session, source)
        self.prepare(session, mana={'C': 4, 'R': 1, 'U': 2, 'B': 2}); self.checkpoint(session)
        self.cast_reserved(session, source, reserve={'U': 2, 'B': 2}); self.assertIsNone(self.resolve(session))
        self.assertEqual(0, self.requirements(session, high)['GENERIC'])
        self.assertEqual(1, self.requirements(session, low)['GENERIC'])
        self.assertEqual(6, self.requirements(session, equipment)['GENERIC'])
        self.cast_reserved(session, high, reserve={'B': 2})
        self.priority(session, 'B')
        offered = self.action(session, 'B', foreign)
        self.assertEqual(3, next(row for row in offered['cost_options'] if row['id'] == 'normal')['requirements']['GENERIC'])
        self.cast(session, 'B', foreign, ['B']); self.assertIsNone(self.resolve(session)); self.replay(session, load=True)

    def test_actual_agatha_respects_battlefield_control_minimum_and_colored_fees(self):
        session = self.session(118_610_003)
        source = self.add(session.engine, 'Agatha of the Vile Cauldron', zone='hand')
        generic = self.add(session.engine, 'Generic Quantity Creature'); generic.owner = 'C'
        foreign = self.add(session.engine, 'Generic Quantity Creature', seat='B', ref='foreign-quantity-creature')
        colored = self.add(session.engine, 'Generic Quantity Colored Creature')
        artifact = self.add(session.engine, 'Generic Quantity Artifact')
        cycling = self.add(session.engine, 'Generic Quantity Cycling Creature', zone='hand')
        growth = self.add(session.engine, 'Giant Growth', zone='hand')
        private = self.add(session.engine, 'Giant Growth', seat='D', zone='hand', ref='agatha-private')
        self.assert_closed(session, source)
        self.prepare(session, mana={'C': 5, 'R': 2, 'G': 2}); self.checkpoint(session)
        self.cast_reserved(session, source, reserve={'R': 1, 'G': 1}); self.assertIsNone(self.resolve(session))
        from quorune.rules.activation.cost_modifiers import current_activation_cost_modifiers
        own_ability = session.engine._activated_abilities(session.state.cards[generic.object_id])[0]
        foreign_ability = session.engine._activated_abilities(session.state.cards[foreign.object_id])[0]
        self.assertEqual(-1, current_activation_cost_modifiers(session.engine, 'B',
            session.state.cards[generic.object_id], own_ability)[0].generic_adjustment)
        self.assertEqual((), current_activation_cost_modifiers(session.engine, 'A',
            session.state.cards[foreign.object_id], foreign_ability))
        self.assertEqual(2, self.activation(session, generic)['cost_summary']['m']['GENERIC'])
        self.assertEqual(3, self.activation(session, artifact)['cost_summary']['m']['GENERIC'])
        self.assertEqual(3, self.activation(session, cycling)['cost_summary']['m']['GENERIC'])
        self.cast_reserved(session, growth, [source.ref], reserve={'R': 1}); self.assertIsNone(self.resolve(session))
        self.assertEqual(1, self.activation(session, generic)['cost_summary']['m']['GENERIC'])
        self.assertEqual((0, 1), tuple(self.activation(session, colored)['cost_summary']['m'].get(key, 0) for key in ('GENERIC', 'R')))
        result = session.act('pilot:A', {'action_id': self.activation(session, generic)['id'], 'pay': 'auto', 'reserve': {'R': 1}})
        self.assertTrue(result.ok, result.summary); self.assertIsNone(self.resolve(session))
        self.assertEqual(41, session.state.players['A'].life)
        result = session.act('pilot:A', {'action_id': self.activation(session, colored)['id'], 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary); self.assertIsNone(self.resolve(session))
        for seat in 'ABC': self.assertNotIn(private.ref, json.dumps(session.packet('pilot:' + seat, full=True)))
        self.replay(session, load=True)

    def test_actual_berserker_counts_controlled_dragon_permanents_and_preserves_boast_restrictions(self):
        session = self.session(118_610_004)
        source = self.add(session.engine, 'Dragonkin Berserker', zone='hand')
        greaves = self.add(session.engine, 'Lightning Greaves', zone='hand')
        self.add(session.engine, 'Shivan Dragon')
        self.add(session.engine, 'Generic Quantity Dragon Kindred')
        self.add(session.engine, 'Shivan Dragon', seat='B', ref='enemy-count-excluded-dragon')
        for index in range(3): self.add(session.engine, 'Mountain', ref='boast-quantity-mana-' + str(index))
        self.assert_closed(session, source)
        self.prepare(session, mana={'C': 3, 'R': 1}); self.checkpoint(session)
        self.cast_reserved(session, source, reserve={'C': 2}); self.assertIsNone(self.resolve(session))
        offered = self.activation(session, source)
        self.assertTrue(offered is None or offered.get('status') != 'payable')
        self.cast(session, 'A', greaves); self.assertIsNone(self.resolve(session))
        accepted = session.act('pilot:A', {'action_id': self.activation(session, greaves)['id'], 'targets': [source.ref], 'pay': 'auto'})
        self.assertTrue(accepted.ok, accepted.summary); self.assertIsNone(self.resolve(session))
        boast_actions.BoastRestrictionTests.advance(self, session, lambda s: s.state.pending_decision.kind == 'combat.attackers')
        accepted = session.act('pilot:A', {'action_id': 'attack', 'attacks': {source.ref: 'C'}})
        self.assertTrue(accepted.ok, accepted.summary)
        boast_actions.BoastRestrictionTests.advance(self, session, lambda s: s.state.priority_player == 'A')
        offered = self.activation(session, source)
        self.assertIsNotNone(offered)
        self.assertEqual((2, 1), tuple(offered['cost_summary']['m'].get(key, 0) for key in ('GENERIC', 'R')))
        accepted = session.act('pilot:A', {'action_id': offered['id'], 'pay': 'auto'})
        self.assertTrue(accepted.ok, accepted.summary); self.assertIsNone(self.resolve(session))
        self.assertTrue(any(card.is_token and card.zone == 'battlefield' and 'Dragon' in card.printed_name for card in session.state.cards.values()))
        offered = self.activation(session, source)
        self.assertTrue(offered is None or offered.get('status') != 'payable'); self.replay(session, load=True)

    def test_constant_printed_power_mutant_is_killed_by_actual_trainer_growth(self):
        from quorune.semantic_runtime import cast_costs
        original = cast_costs.cast_reduction_multiplier
        def printed(host, seat, metric, **kwargs):
            return 1 if metric.kind is CastReductionMetricKind.SOURCE_POWER else original(host, seat, metric, **kwargs)
        with patch.object(cast_costs, 'cast_reduction_multiplier', printed), self.assertRaises(AssertionError):
            self.test_actual_trainer_uses_current_power_and_keeps_paid_cost_after_provider_removal()

    def test_constant_activation_power_mutant_is_killed_by_actual_agatha_growth(self):
        from quorune import self_cast_reductions
        original = self_cast_reductions.cast_reduction_multiplier
        def printed(host, seat, metric, **kwargs):
            return 1 if metric.kind is CastReductionMetricKind.SOURCE_POWER else original(host, seat, metric, **kwargs)
        with patch.object(self_cast_reductions, 'cast_reduction_multiplier', printed), self.assertRaises(AssertionError):
            self.test_actual_agatha_respects_battlefield_control_minimum_and_colored_fees()

    def test_runtime_descriptor_cannot_omit_power_quantity_or_boast_authority(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.rules.public_value_costs import public_value_cost_dependencies
        session = self.session(118_610_005)
        checked = set()
        for name in ('Agatha of the Vile Cauldron', 'Golden-Tail Trainer', 'Dragonkin Berserker'):
            source = self.add(session.engine, name)
            self.assert_closed(session, source)
            for program in session.engine.semantics.programs_for_oracle(source.oracle_id):
                for cap in public_value_cost_dependencies(program.handlers):
                    checked.add(cap)
                    deps = tuple(value for value in program.capability_dependencies if value != cap)
                    changed = replace(program, capability_dependencies=deps,
                        capability_closure=self.registry.closure(deps, profile='commander_review').to_dict())
                    binding = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile='commander_review')
                    self.assertFalse(binding['strict'], (name, cap))
                    self.assertIn('capability:undeclared_runtime_dependency:' + cap, binding['blockers'])
        self.assertEqual({PUBLIC_SOURCE_POWER_COST_CAPABILITY, PUBLIC_QUANTITY_ACTIVATION_COST_CAPABILITY,
            'activation.boast.fixed_restrictions'}, checked)


if __name__ == '__main__': unittest.main()
