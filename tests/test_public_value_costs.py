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
    ABILITY_LOCAL_MANA_REDUCTION_CAPABILITY, PUBLIC_QUANTITY_ACTIVATION_COST_CAPABILITY, activation_cost_prices)
from quorune.activated_ability_descriptor import ABILITY_LOCAL_MANA_REDUCTION_FIELD
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
    def test_ability_local_public_value_roundtrips_and_rejects_open_conditions(self):
        from quorune.abilities import ActivatedAbility
        from quorune.activation_cost_modifiers import ABILITY_LOCAL_PUBLIC_VALUE_CAPABILITY
        from quorune.compiler.activated_costs import parse_compiled_activation_line
        from quorune.semantic_runtime.activated_abilities import (
            ability_local_mana_reduction_runtime_dependencies, activated_ability_catalog_descriptor,
        )

        body = '{4}{W}, {T}: Draw a card.'
        for discount in ('{2} less to activate if you control a legendary creature.',
                         '{X} less to activate, where X is the greatest power among creatures you control.'):
            with self.subTest(discount=discount):
                text = f'{body} This ability costs {discount}'
                ability, = parse_compiled_activation_line(text, card_name='Generic Query Observer', keywords=())[0]
                self.assertEqual('Draw a card.', ability.effect_text)
                self.assertEqual(ability.to_dict(), ActivatedAbility.from_dict(ability.to_dict()).to_dict())
                ir = compile_oracle_card(query_record(text), capability_registry=load_default_capability_registry())
                self.assertEqual('exact', ir.status, ir.material_residuals)
                node, = ir.faces[0].nodes
                catalog = activated_ability_catalog_descriptor(ability)
                program = SimpleNamespace(event='activate', active_zone='battlefield', cost_schema=node.cost, handlers=(catalog,))
                self.assertIn(ABILITY_LOCAL_PUBLIC_VALUE_CAPABILITY, node.capability_dependencies)
                self.assertIn(ABILITY_LOCAL_PUBLIC_VALUE_CAPABILITY, ability_local_mana_reduction_runtime_dependencies(program))
                program.active_zone = 'hand'
                with self.assertRaisesRegex(ValueError, 'origin'):
                    ability_local_mana_reduction_runtime_dependencies(program)

        for discount in ('{2} less to activate if you have two or more cards in hand.',
                         '{1} less to activate if it targets a creature with power 3 or less.',
                         '{X} less to activate, where X is the greatest power among creatures your opponents control.'):
            with self.subTest(unsupported=discount):
                text = f'{body} This ability costs {discount}'
                self.assertNotEqual('exact', compile_oracle_card(query_record(text),
                    capability_registry=load_default_capability_registry()).status)

    def test_ability_local_discount_closes_only_its_cost_and_catalog(self):
        from quorune.abilities import ActivatedAbility
        from quorune.compiler.activated_costs import parse_compiled_activation_line
        from quorune.semantic_runtime.activated_abilities import (
            ActivatedAbilityCatalogHandler, ability_local_mana_reduction_runtime_dependencies,
            activated_ability_catalog_descriptor,
        )

        field = ABILITY_LOCAL_MANA_REDUCTION_FIELD
        effect = '{4}{U}, {T}: Draw a card.'
        line = effect + ' This ability costs {1} less to activate for each other artifact you control.'
        old, = parse_compiled_activation_line(effect, card_name='Generic Query Observer', keywords=())[0]
        self.assertNotIn(field, old.to_dict())
        self.assertEqual(old.to_dict(), ActivatedAbility.from_dict(old.to_dict()).to_dict())
        ability, = parse_compiled_activation_line(line, card_name='Generic Query Observer', keywords=())[0]
        self.assertEqual('Draw a card.', ability.effect_text)
        self.assertEqual(line, ability.oracle_line)
        self.assertTrue(ability.ability_local_mana_reduction.exclude_source)
        self.assertEqual(ability.to_dict(), ActivatedAbility.from_dict(ability.to_dict()).to_dict())
        ir = compile_oracle_card(query_record(line), capability_registry=load_default_capability_registry())
        self.assertEqual('exact', ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        self.assertIn(ABILITY_LOCAL_MANA_REDUCTION_CAPABILITY, node.capability_dependencies)
        descriptor = activated_ability_catalog_descriptor(ability)
        self.assertEqual(ability.to_dict(), ActivatedAbilityCatalogHandler().validate(descriptor).to_dict())
        program = SimpleNamespace(event='activate', active_zone='battlefield', cost_schema=node.cost, handlers=(descriptor,))
        self.assertEqual((ABILITY_LOCAL_MANA_REDUCTION_CAPABILITY,),
                         ability_local_mana_reduction_runtime_dependencies(program))
        unbound = {**descriptor, 'ability': {**descriptor['ability'], field: {
            **descriptor['ability'][field], 'exclude_source': False,
        }}}
        with self.assertRaisesRegex(ValueError, 'differs'):
            ability_local_mana_reduction_runtime_dependencies(
                SimpleNamespace(event='activate', active_zone='battlefield', cost_schema=node.cost, handlers=(unbound,)))
        for unsupported in (line.replace('other artifact you control', 'card in your hand'),
                            line.replace('costs {1}', 'costs {2}'), line + ' Unrepresented sibling.'):
            with self.subTest(unsupported=unsupported):
                self.assertNotEqual('exact', compile_oracle_card(query_record(unsupported),
                    capability_registry=load_default_capability_registry()).status)

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
        raw = json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
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

    def test_actual_pteramander_uses_current_own_graveyard_and_keeps_blue_fee(self):
        session = self.session(118_861_001)
        engine = session.engine
        source = self.add(engine, 'Pteramander')
        self.add(engine, 'Murder', zone='graveyard')
        self.add(engine, 'Concentrate', zone='graveyard')
        self.add(engine, 'Inspiration', seat='B', zone='graveyard')
        self.add(engine, 'Memnite', zone='graveyard', ref='irrelevant-creature')
        hidden = self.add(engine, 'Concentrate', seat='D', zone='hand', ref='private-foreign-sorcery')
        growth = self.add(engine, 'Giant Growth', zone='hand')
        body = self.add(engine, 'Memnite')
        self.assert_closed(session, source)
        self.prepare(session, mana={'C': 7, 'U': 1, 'G': 1})
        self.checkpoint(session)
        price = self.activation(session, source)['cost_summary']['m']
        self.assertEqual({'GENERIC': 5, 'U': 1}, price)
        self.assertNotIn(hidden.ref, json.dumps(session.packet('pilot:A', full=True)))
        self.cast(session, 'A', growth, [body.ref])
        self.assertIsNone(self.resolve(session))
        offer = self.activation(session, source)
        self.assertEqual({'GENERIC': 4, 'U': 1}, offer['cost_summary']['m'])
        command = {'action_id': offer['id'], 'pay': 'auto'}
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:B', command).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        accepted = session.act('pilot:A', command)
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(4, session.state.cards[source.object_id].counters['+1/+1'])
        self.assertEqual(3, session.state.players['A'].mana_pool['C'])
        self.assertEqual(0, session.state.players['A'].mana_pool['U'])
        self.replay(session, load=True)

    def test_actual_mobilized_district_keeps_the_price_on_its_animation(self):
        session = self.session(118_861_002)
        engine = session.engine
        source = self.add(engine, 'Mobilized District')
        self.add(engine, 'Generic Bound Commander', ref='own-legend')
        self.add(engine, 'Generic Bound Commander', seat='B', ref='foreign-legend')
        self.add(engine, 'Memnite', ref='ordinary-creature')
        self.assert_closed(session, source)
        self.prepare(session, mana={'C': 3})
        self.checkpoint(session)
        mana_ability, animation = sorted(engine._activated_abilities(source), key=lambda value: value.line_index)
        self.assertTrue(mana_ability.mana_ability)
        self.assertIsNone(mana_ability.ability_local_mana_reduction)
        self.assertIsNotNone(animation.ability_local_mana_reduction)
        offer_id = f'activate:{source.ref}:{animation.ability_id}'
        rows = session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
        offer = next(row for row in rows if row['id'] == offer_id)
        self.assertEqual({'GENERIC': 3}, offer['cost_summary']['m'])
        accepted = session.act('pilot:A', {'action_id': offer_id, 'pay': 'auto'})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertIsNone(self.resolve(session))
        effective = engine._effective_card_data(session.state.cards[source.object_id])
        types, subtypes, _ = engine._type_parts(effective['type_line'])
        self.assertTrue({'land', 'creature'}.issubset(types))
        self.assertIn('citizen', subtypes)
        self.assertEqual(('3', '3'), (str(effective['power']), str(effective['toughness'])))
        self.replay(session, load=True)

    def test_actual_source_counter_discount_locks_before_sacrifice(self):
        session = self.session(118_861_003)
        source = self.add(session.engine, 'Vindictive Flamestoker')
        source.counters['oil'] = 5
        self.assert_closed(session, source)
        self.prepare(session, mana={'C': 1, 'R': 1})
        self.checkpoint(session)
        offer = self.activation(session, source)
        self.assertEqual({'GENERIC': 1, 'R': 1}, offer['cost_summary']['m'])
        accepted = session.act('pilot:A', {'action_id': offer['id'], 'pay': 'auto'})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual('graveyard', session.state.cards[source.object_id].zone)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(4, len(session.state.players['A'].zones['hand']))
        self.assertEqual(0, session.state.players['A'].mana_pool['C'])
        self.replay(session, load=True)

    def test_actual_domain_and_other_artifact_prices_use_distinct_public_objects(self):
        cases = ((118_861_004, "Jodah's Codex", {'C': 3}, {'GENERIC': 3}),
                 (118_861_005, "Tamiyo's Logbook", {'C': 4, 'U': 1}, {'GENERIC': 4, 'U': 1}))
        for seed, name, mana, price in cases:
            with self.subTest(name=name):
                session = self.session(seed)
                engine = session.engine
                source = self.add(engine, name)
                if name == "Jodah's Codex":
                    self.add(engine, 'Hallowed Fountain')
                    self.add(engine, 'Island', ref='duplicate-island')
                    self.add(engine, 'Swamp', seat='B')
                else:
                    self.add(engine, 'Memnite')
                    self.add(engine, 'Memnite', seat='B', ref='foreign-artifact')
                self.assert_closed(session, source)
                self.prepare(session, mana=mana)
                self.checkpoint(session)
                before = len(session.state.players['A'].zones['hand'])
                offer = self.activation(session, source)
                self.assertEqual(price, offer['cost_summary']['m'])
                accepted = session.act('pilot:A', {'action_id': offer['id'], 'pay': 'auto'})
                self.assertTrue(accepted.ok, accepted.summary)
                self.assertIsNone(self.resolve(session))
                self.assertTrue(session.state.cards[source.object_id].tapped)
                self.assertEqual(before + 1, len(session.state.players['A'].zones['hand']))
                self.replay(session, load=True)

    def test_ability_local_discount_is_killed_when_its_current_count_is_omitted(self):
        with patch('quorune.rules.activation.cost_modifiers._ability_local_mana_modifiers', return_value=[]):
            with self.assertRaisesRegex(AssertionError, 'GENERIC'):
                self.test_actual_pteramander_uses_current_own_graveyard_and_keeps_blue_fee()

    def test_actual_fixed_activation_reductions_read_own_current_permanents(self):
        for qualified in (False, True):
            with self.subTest(qualified=qualified):
                session = self.session(118_862_001 + qualified)
                engine = session.engine
                esquire = self.add(engine, 'Esquire of the King')
                security = self.add(engine, 'Starport Security')
                target = self.add(engine, 'Memnite', seat='B', ref='countered-foreign-creature')
                target.counters['+1/+1'] = 1
                self.add(engine, 'Generic Bound Commander', seat='A' if qualified else 'B', ref='price-legend')
                if qualified:
                    security.counters['+1/+1'] = 1
                for card in (esquire, security):
                    self.assert_closed(session, card)
                self.prepare(session, mana={'C': 3 if qualified else 7, 'W': 2})
                self.checkpoint(session)
                for card, generic in ((security, 1 if qualified else 3), (esquire, 2 if qualified else 4)):
                    offered = self.activation(session, card)
                    self.assertEqual({'GENERIC': generic, 'W': 1}, offered['cost_summary']['m'])
                    command = {'action_id': offered['id'], 'pay': 'auto'}
                    if card is security:
                        self.assertNotIn(security.ref, offered['target_schema']['legal_refs'])
                        command['targets'] = [target.ref]
                    accepted = session.act('pilot:A', command)
                    self.assertTrue(accepted.ok, accepted.summary)
                    self.assertIsNone(self.resolve(session))
                    self.priority(session, 'A')
                self.assertTrue(session.state.cards[target.object_id].tapped)
                self.assertEqual(0, session.state.players['A'].mana_pool['C'])
                self.assertEqual(0, session.state.players['A'].mana_pool['W'])
                self.replay(session, load=True)

    def test_actual_crocodile_uses_distinct_graveyard_values_and_locks_before_mana(self):
        for qualified in (False, True):
            with self.subTest(qualified=qualified):
                session = self.session(118_862_003 + qualified)
                engine = session.engine
                source = self.add(engine, 'Sewer Crocodile')
                for name in ('Hallowed Fountain', 'Agatha of the Vile Cauldron', 'Murder', 'Concentrate'):
                    self.add(engine, name, zone='graveyard')
                self.add(engine, 'Murder', zone='graveyard', ref='duplicate-mana-value')
                self.add(engine, 'Giant Growth', seat='B', zone='graveyard', ref='opponent-mana-value')
                donor = self.add(engine, 'Wild Cantor', zone='graveyard' if qualified else 'battlefield')
                blocker = self.add(engine, 'Memnite', seat='B', ref='public-blocker')
                for card in (source, donor):
                    self.assert_closed(session, card)
                self.prepare(session, mana={'U': 1} if qualified else {'C': 3})
                self.checkpoint(session)
                self.assertTrue(engine._can_block(source, blocker)[0])
                offered = self.activation(session, source)
                self.assertEqual({'U': 1} if qualified else {'GENERIC': 3, 'U': 1}, offered['cost_summary']['m'])
                accepted = session.act('pilot:A', {'action_id': offered['id'], 'pay': 'auto'})
                self.assertTrue(accepted.ok, accepted.summary)
                self.assertEqual('graveyard', session.state.cards[donor.object_id].zone)
                self.assertEqual(0, session.state.players['A'].mana_pool['C'])
                self.assertIsNone(self.resolve(session))
                self.assertFalse(engine._can_block(session.state.cards[source.object_id], session.state.cards[blocker.object_id])[0])
                self.replay(session, load=True)

    def test_actual_baru_reads_live_wurm_power_and_keeps_the_green_fee(self):
        for controlled in (False, True):
            with self.subTest(controlled=controlled):
                session = self.session(118_862_005 + controlled)
                engine = session.engine
                source = self.add(engine, 'Baru, Wurmspeaker')
                self.add(engine, 'Craw Wurm', seat='A' if controlled else 'B', ref='wurm-for-price')
                self.assert_closed(session, source)
                self.prepare(session, mana={'G': 1} if controlled else {'C': 7, 'G': 1})
                self.checkpoint(session)
                offered = self.activation(session, source)
                self.assertEqual({'G': 1} if controlled else {'GENERIC': 7, 'G': 1}, offered['cost_summary']['m'])
                accepted = session.act('pilot:A', {'action_id': offered['id'], 'pay': 'auto'})
                self.assertTrue(accepted.ok, accepted.summary)
                self.assertIsNone(self.resolve(session))
                wurms = [card for card in session.state.cards.values() if card.zone == 'battlefield'
                         and card.controller == 'A' and card.is_token
                         and 'wurm' in engine._type_parts(engine._effective_card_data(card)['type_line'])[1]]
                self.assertEqual(1, len(wurms))
                self.assertEqual(0, session.state.players['A'].mana_pool['G'])
                self.replay(session, load=True)

    def test_actual_eiganjo_keeps_hand_discard_target_authority_and_public_price(self):
        from quorune.engine import TURN_STEPS
        from quorune.model import CombatState

        session = self.session(118_862_007)
        engine = session.engine
        source = self.add(engine, 'Eiganjo, Seat of the Empire', zone='hand', ref='private-eiganjo-channel')
        legend = self.add(engine, 'Generic Bound Commander', ref='friendly-legend')
        self.add(engine, 'Generic Bound Commander', seat='B', ref='foreign-legend')
        attacker = self.add(engine, 'Memnite', seat='B', ref='current-attacker')
        self.assert_closed(session, source)
        self.prepare(session, active='B', mana={'C': 1, 'W': 1})
        engine.permissions.invalidate_current()
        session.state.pending_decision = None
        session.state.phase = 'combat'
        session.state.step = 'declare_attackers'
        session.state.phase_index = TURN_STEPS.index(('combat', 'declare_attackers'))
        attacker.attacking = 'A'
        session.state.combat = CombatState(attackers_declared=True, had_attacking_creature=True,
            attackers={attacker.object_id: 'A'}, defending_players=['A'])
        engine._grant_priority('A')
        engine.pump()
        self.checkpoint(session)
        offered = self.activation(session, source)
        self.assertEqual({'GENERIC': 1, 'W': 1}, offered['cost_summary']['m'])
        self.assertEqual([attacker.ref], offered['target_schema']['legal_refs'])
        self.assertNotIn(source.ref, json.dumps(session.packet('pilot:B', full=True)))
        command = {'action_id': offered['id'], 'pay': 'auto'}
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:A', {**command, 'targets': [legend.ref]}).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        accepted = session.act('pilot:A', {**command, 'targets': [attacker.ref]})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual('graveyard', session.state.cards[source.object_id].zone)
        self.assertIsNone(self.resolve(session))
        self.assertEqual('graveyard', session.state.cards[attacker.object_id].zone)
        self.replay(session, load=True)

    def test_ability_local_condition_mutant_is_killed_by_the_real_activation_price(self):
        with patch('quorune.rules.activation.public_state_conditions.current_activation_public_state_matches', return_value=True):
            with self.assertRaisesRegex(AssertionError, 'GENERIC'):
                self._assert_unmet_crocodile_price()

    def _assert_unmet_crocodile_price(self):
        session = self.session(118_862_008)
        source = self.add(session.engine, 'Sewer Crocodile')
        self.assert_closed(session, source)
        self.prepare(session, mana={'C': 3, 'U': 1})
        self.assertEqual({'GENERIC': 3, 'U': 1}, self.activation(session, source)['cost_summary']['m'])

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
