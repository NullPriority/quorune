"""Independent CR118.7a/601.2f ordered cost examples and finite price property."""
from __future__ import annotations

from dataclasses import replace
import itertools
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from quorune.activation_cost_modifiers import (
    ActivationManaReduction, FixedActivationCostModifierSpec, activation_cost_prices,
)
from quorune.object_predicate import ObjectQuerySpec
from quorune.replacement.immutable import FrozenMap
from quorune.compiler.activation_cost_modifier_templates import static_activation_cost_modifier_handler
from quorune.semantic_runtime.activation_costs import FixedActivationCostModifierHandler
from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.capabilities import CapabilityRegistry
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.oracle_ir import compile_oracle_card
from test_granted_skulk import record
from scripts.build_test_database import build_fixture_database
from quorune.rules.activation.pricing import LOWEST_COST, MINIMUM_COST
import test_announced_activation_x as actions


class ActivationCostModifierModelTests(unittest.TestCase):
    def spec(self, amount, *, minimum=0):
        return FixedActivationCostModifierSpec(amount, minimum_mana=minimum, subject_relation='any')

    def test_generic_modifier_grammar_preserves_each_scope_and_floor(self):
        cases = (
            ("Activated abilities of creatures you control cost {2} less to activate. This effect can't reduce the mana in that cost to less than one mana.", -2, 1, 'query', 'source_controller', 'all'),
            ("Activated abilities of creatures cost {1} less to activate. This effect can't reduce the mana in that cost to less than one mana.", -1, 1, 'query', 'any', 'all'),
            ("Activated abilities cost {2} more to activate unless they're mana abilities.", 2, 0, 'any', 'any', 'nonmana'),
            ("Abilities you activate that aren't mana abilities cost {2} less to activate. This effect can't reduce the mana in that cost to less than one mana.", -2, 1, 'any', 'source_controller', 'nonmana'),
            ("Activated abilities of Foods you control cost {1} less to activate.", -1, 0, 'query', 'source_controller', 'all'),
            ("Enchanted artifact's activated abilities cost {2} less to activate. This effect can't reduce the mana in that cost to less than one mana.", -2, 1, 'enchanted_object', 'any', 'all'),
            ("Activated abilities of white enchantments cost {3} more to activate.", 3, 0, 'query', 'any', 'all'),
        )
        for text, amount, minimum, relation, controller, scope in cases:
            template = static_activation_cost_modifier_handler(text)
            self.assertIsNotNone(template, text)
            spec = FixedActivationCostModifierHandler().validate(template[1])
            self.assertEqual((amount, minimum, relation, controller, scope),
                (spec.generic_adjustment, spec.minimum_mana, spec.subject_relation, spec.controller_relation, spec.ability_scope))
            if 'white enchantments' in text:
                self.assertEqual(('W',), spec.predicate.colors_all)
                self.assertEqual(('enchantment',), spec.predicate.types_all)

    def test_modifier_grammar_and_handler_do_not_strip_unknown_qualifications(self):
        for text in ("Activated abilities of creatures you control cost {X} less to activate.",
                "Activated abilities of creatures you control cost {G} less to activate.",
                "Cycling abilities your opponents activate cost {X} less to activate.",
                "Activated abilities of other creatures you control cost {2} less to activate.",
                "Activated abilities of creatures you control cost {2} less to activate. Draw a card."):
            self.assertIsNone(static_activation_cost_modifier_handler(text), text)
        _, descriptor, _ = static_activation_cost_modifier_handler('Activated abilities cost {2} more to activate.')
        for changes in ({'schema_version': True}, {'event': 'cast.cost.modify'}, {'extra': True}):
            with self.assertRaises(ValueError): FixedActivationCostModifierHandler().validate({**descriptor, **changes})

    def test_withdrawn_static_cost_capability_keeps_an_otherwise_closed_source_residual(self):
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        next(r for r in raw['capabilities'] if r['id']=='activation.mana_cost.static_generic').update(
            status='blocked',blockers=['withdrawn static activation-cost authority'])
        text="Activated abilities of creatures you control cost {2} less to activate. This effect can't reduce the mana in that cost to less than one mana."
        ir=compile_oracle_card(record(text,'Enchantment'),capability_registry=CapabilityRegistry(raw),capability_profile='commander_review')
        self.assertNotEqual('exact',ir.status);self.assertTrue(ir.material_residuals)

    def test_public_modifier_codec_keeps_scope_and_rejects_unowned_predicates(self):
        spec = FixedActivationCostModifierSpec(-2, minimum_mana=1,
            controller_relation='source_controller', predicate=ObjectQuerySpec(
                zones=('battlefield',), types_all=('creature',)))
        self.assertEqual(spec, FixedActivationCostModifierSpec.from_dict(spec.to_dict()))
        for changes in ({'schema_version': True}, {'generic_adjustment': True},
                {'generic_adjustment': 0}, {'minimum_mana': 2}, {'ability_scope': 'loyalty'},
                {'predicate': ObjectQuerySpec(zones=('graveyard',))},
                {'predicate': ObjectQuerySpec(zones=('battlefield',), owner='A')}):
            with self.assertRaises(ValueError): replace(spec, **changes)
        with self.assertRaises(ValueError): self.spec(2, minimum=1)
        with self.assertRaises(ValueError):
            FixedActivationCostModifierSpec.from_dict({**spec.to_dict(), 'extra': True})

    def test_minimum_one_counts_colored_colorless_and_snow_without_creating_mana(self):
        reducer = self.spec(-2, minimum=1)
        for base, snow, expected in (({'GENERIC': 2}, 0, 1), ({'GENERIC': 0}, 0, 0),
                ({'GENERIC': 2, 'G': 1}, 0, 0), ({'GENERIC': 2, 'C': 1}, 0, 0),
                ({'GENERIC': 2}, 1, 0)):
            price, = activation_cost_prices(base, (reducer,), snow_payment=snow)
            self.assertEqual(expected, price.requirements['GENERIC'])
            self.assertEqual(base.get('G', 0), price.requirements['G'])
            self.assertEqual(base.get('C', 0), price.requirements['C'])

    def test_taxes_precede_reductions_and_both_legal_order_prices_remain_choices(self):
        prices = activation_cost_prices({'GENERIC': 1},
            (self.spec(2), self.spec(-2, minimum=1), self.spec(-1)))
        self.assertEqual([('minimum_first', 0), ('unrestricted_first', 1)],
            [(p.order, p.requirements['GENERIC']) for p in prices])
        price, = activation_cost_prices({'R': 1}, (self.spec(2), self.spec(-2, minimum=1)))
        self.assertEqual((0, 1), (price.requirements['GENERIC'], price.requirements['R']))

    def test_existing_colored_reduction_can_order_before_or_after_a_generic_minimum(self):
        own = ActivationManaReduction(FrozenMap({'R': 1}))
        prices = activation_cost_prices({'GENERIC': 2, 'R': 1},
            (self.spec(-2, minimum=1),), own_reductions=(own,))
        self.assertEqual({(0, 0), (1, 0)},
            {(p.requirements['GENERIC'], p.requirements['R']) for p in prices})
        with self.assertRaises(ValueError): ActivationManaReduction(FrozenMap({'R': 1}), 1)

    def test_every_bounded_reduction_permutation_has_a_represented_legal_price(self):
        # Independent rule fold: each effect sees the then-current mana vector;
        # a minimum-one effect cannot add a mana obligation that was absent.
        def rules_fold(base, snow, ordered):
            value = {'GENERIC': base[0], 'R': base[1]}
            for generic, red, minimum in ordered:
                before = dict(value)
                matched = min(value['R'], red)
                value['R'] -= matched
                value['GENERIC'] = max(0, value['GENERIC'] - generic - (red - matched))
                if minimum and not (before['R'] or snow) and before['GENERIC']:
                    value['GENERIC'] = max(1, value['GENERIC'])
            return value['GENERIC'], value['R']
        choices = ((1, 0, 0), (2, 0, 1), (0, 1, 0), (1, 1, 0))
        for count in range(5):
            for reductions in itertools.combinations_with_replacement(choices, count):
                own = tuple(ActivationManaReduction(FrozenMap({'GENERIC': g, 'R': r}), m)
                    for g, r, m in reductions)
                for generic, red, snow in itertools.product(range(6), range(3), range(2)):
                    expected = {rules_fold((generic, red), snow, order)
                        for order in set(itertools.permutations(reductions))}
                    prices = activation_cost_prices({'GENERIC': generic, 'R': red}, (),
                        own_reductions=own, snow_payment=snow)
                    self.assertEqual(expected, {(p.requirements['GENERIC'], p.requirements['R']) for p in prices},
                        (generic, red, snow, reductions))


class StaticActivationCostRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=actions.AnnouncedActivationXRuntimeTests.session
    add=actions.AnnouncedActivationXRuntimeTests.add
    prepare=actions.AnnouncedActivationXRuntimeTests.prepare
    checkpoint=actions.AnnouncedActivationXRuntimeTests.checkpoint
    cast=actions.AnnouncedActivationXRuntimeTests.cast
    resolve=actions.AnnouncedActivationXRuntimeTests.resolve
    replay=actions.AnnouncedActivationXRuntimeTests.replay
    binding=actions.AnnouncedActivationXRuntimeTests.binding
    private_replay=actions.AnnouncedActivationXRuntimeTests.private_replay
    action=actions.AnnouncedActivationXRuntimeTests.action

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'static-cost.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/announced-x-activation-cards.json',ROOT/'tests/fixtures/static-activation-cost-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Static costs',[DeckEntry('Generic Bound Commander',1,'commander'),
            DeckEntry('Generic Bound Plains',20)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def test_original_training_grounds_pays_one_for_x_three_and_retains_result(self):
        session=self.session(71501001);modifier=self.add(session.engine,'Training Grounds')
        source=self.add(session.engine,'Oracle of Nectars');source.acquired_control_turn_count=-1
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,mana={'C':3});self.checkpoint(session)
        for card in (modifier,source):self.assertTrue(self.binding(session,card)['strict_capability_ready'])
        action=self.action(session,source);command={'action_id':action['id'],'x':3,'pay':'auto'}
        before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:D',command).ok);self.assertEqual(before,authoritative_state_hash(session.state))
        result=session.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        self.assertEqual(2,session.state.players['A'].mana_pool['C']);self.assertEqual(3,session.state.stack[-1].x_value)
        self.assertTrue(session.state.cards[source.object_id].tapped)
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual(43,session.state.players['A'].life);self.private_replay(session,private)

    def test_original_suppression_field_taxes_zero_x_but_keeps_mana_abilities_free(self):
        session=self.session(71501002);modifier=self.add(session.engine,'Suppression Field')
        source=self.add(session.engine,'Oracle of Nectars');source.acquired_control_turn_count=-1
        forest=self.add(session.engine,'Forest');private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,mana={'C':3});self.checkpoint(session)
        self.assertTrue(self.binding(session,modifier)['strict_capability_ready'])
        action=self.action(session,source);result=session.act('pilot:A',{'action_id':action['id'],'x':0,'pay':'auto'})
        self.assertTrue(result.ok,result.summary);self.assertEqual(1,session.state.players['A'].mana_pool['C'])
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        rows=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        mana=next(r for r in rows if r['id'].startswith('activate:'+forest.ref+':'))
        result=session.act('pilot:A',{'action_id':mana['id']});self.assertTrue(result.ok,result.summary)
        self.assertEqual((1,1),tuple(session.state.players['A'].mana_pool[c] for c in ('C','G')))
        self.assertEqual(40,session.state.players['A'].life);self.private_replay(session,private)

    def test_both_legal_minimum_orders_are_offered_paid_and_replayed(self):
        for index,(choice,payment) in enumerate(((LOWEST_COST,0),(MINIMUM_COST,1))):
            session=self.session(71501010+index)
            modifiers=[self.add(session.engine,'Training Grounds'),self.add(session.engine,'Generic Unrestricted Food Reducer')]
            source=self.add(session.engine,'Generic Food Study');source.acquired_control_turn_count=-1
            private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
            self.prepare(session,mana={'C':1});self.checkpoint(session)
            for card in (*modifiers,source):self.assertTrue(self.binding(session,card)['strict_capability_ready'])
            rows=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
            action=next(r for r in rows if r['id'].startswith('activate:'+source.ref+':'))
            self.assertEqual([LOWEST_COST,MINIMUM_COST],action['choice_schema']['cost_reduction_order']['legal_values'])
            before=authoritative_state_hash(session.state)
            bad=session.act('pilot:A',{'action_id':action['id'],'cost_reduction_order':'invented','pay':'auto'})
            self.assertFalse(bad.ok);self.assertEqual(before,authoritative_state_hash(session.state))
            result=session.act('pilot:A',{'action_id':action['id'],'cost_reduction_order':choice,'pay':'auto'})
            self.assertTrue(result.ok,result.summary);self.assertEqual(1-payment,session.state.players['A'].mana_pool['C'])
            session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
            self.assertEqual(42,session.state.players['A'].life);self.private_replay(session,private)

    def test_original_training_reduction_counts_colored_mana_without_changing_x_damage(self):
        session=self.session(71501020);modifier=self.add(session.engine,'Training Grounds')
        source=self.add(session.engine,'Silklash Spider');flying=self.add(session.engine,'Wind Drake',seat='B')
        ground=self.add(session.engine,'Grizzly Bears',seat='B');private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,mana={'C':1,'G':2});self.checkpoint(session)
        self.assertTrue(self.binding(session,modifier)['strict_capability_ready'])
        action=self.action(session,source);result=session.act('pilot:A',{'action_id':action['id'],'x':2,'pay':'auto'})
        self.assertTrue(result.ok,result.summary)
        self.assertEqual((1,0),tuple(session.state.players['A'].mana_pool[c] for c in ('C','G')))
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual('graveyard',session.state.cards[flying.object_id].zone)
        self.assertEqual('battlefield',session.state.cards[ground.object_id].zone);self.private_replay(session,private)

    def test_minimum_one_does_not_create_a_price_for_original_zero_x(self):
        session=self.session(71501021);modifier=self.add(session.engine,'Training Grounds')
        source=self.add(session.engine,'Oracle of Nectars');source.acquired_control_turn_count=-1
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session);self.checkpoint(session)
        self.assertTrue(self.binding(session,modifier)['strict_capability_ready'])
        action=self.action(session,source);result=session.act('pilot:A',{'action_id':action['id'],'x':0,'pay':'auto'})
        self.assertTrue(result.ok,result.summary)
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual(0,sum(session.state.players['A'].mana_pool.values()))
        self.assertEqual(40,session.state.players['A'].life);self.assertTrue(session.state.cards[source.object_id].tapped)
        self.private_replay(session,private)

    def test_removing_original_reducer_does_not_reprice_paid_ability_but_changes_next_price(self):
        session=self.session(71501022);modifier=self.add(session.engine,'Training Grounds')
        first=self.add(session.engine,'Oracle of Nectars',ref='first-oracle');first.acquired_control_turn_count=-1
        second=self.add(session.engine,'Oracle of Nectars',ref='second-oracle');second.acquired_control_turn_count=-1
        removal=self.add(session.engine,'Disenchant',zone='hand')
        self.add(session.engine,'Generic Bound Plains',ref='cost-removal-plains')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,mana={'C':7});self.checkpoint(session)
        action=self.action(session,first);result=session.act('pilot:A',{'action_id':action['id'],'x':3,'pay':'auto'})
        self.assertTrue(result.ok,result.summary);self.assertEqual(6,session.state.players['A'].mana_pool['C'])
        self.cast(session,'A',removal,[modifier.ref]);session=self.replay(session,load=True)
        self.assertIsNone(self.resolve(session));self.assertEqual('graveyard',session.state.cards[modifier.object_id].zone)
        self.assertEqual(43,session.state.players['A'].life)
        action=self.action(session,second);result=session.act('pilot:A',{'action_id':action['id'],'x':3,'pay':'auto'})
        self.assertTrue(result.ok,result.summary);self.assertEqual(2,session.state.players['A'].mana_pool['C'])
        self.assertIsNone(self.resolve(session));self.assertEqual(46,session.state.players['A'].life)
        self.private_replay(session,private)

    def test_original_controller_scope_does_not_reduce_an_opponents_creature(self):
        session=self.session(71501023);modifier=self.add(session.engine,'Training Grounds')
        source=self.add(session.engine,'Oracle of Nectars',seat='B');source.acquired_control_turn_count=-1
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,actor='B',mana={'C':3});self.checkpoint(session)
        self.assertTrue(self.binding(session,modifier)['strict_capability_ready'])
        rows=session.packet('pilot:B',full=True)['decision']['ctx']['legal']['actions']
        action=next(r for r in rows if r['id'].startswith('activate:'+source.ref+':') and r.get('choice_schema',{}).get('x'))
        result=session.act('pilot:B',{'action_id':action['id'],'x':3,'pay':'auto'})
        self.assertTrue(result.ok,result.summary);self.assertEqual(0,session.state.players['B'].mana_pool['C'])
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual(43,session.state.players['B'].life);self.assertEqual(40,session.state.players['A'].life)
        self.private_replay(session,private)

    def test_omitting_static_price_application_is_killed_by_fresh_native_original(self):
        method='test_original_training_grounds_pays_one_for_x_three_and_retains_result'
        with patch('quorune.rules.activation.pricing.current_activation_cost_modifiers',return_value=()):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()

    def test_executable_modifier_rejects_an_undeclared_cost_capability(self):
        session=self.session(71501090);source=self.add(session.engine,'Training Grounds')
        program=next(p for p in session.engine.semantics.programs_for_oracle(source.oracle_id)
            if any(h.get('handler_id')=='modification.activation-cost.fixed-generic.v1' for h in p.handlers))
        self.assertTrue(bind_semantic_program_runtime(program,capability_registry=self.registry,profile='commander_review')['strict'])
        cap='activation.mana_cost.static_generic';dependencies=tuple(c for c in program.capability_dependencies if c!=cap)
        missing=replace(program,capability_dependencies=dependencies,
            capability_closure=self.registry.closure(dependencies,profile='commander_review').to_dict() if dependencies else None)
        bound=bind_semantic_program_runtime(missing,capability_registry=self.registry,profile='commander_review')
        self.assertFalse(bound['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+cap,bound['blockers'])

    def test_original_gloom_mana_tax_requires_payment_and_prevents_mana_undo(self):
        session=self.session(71501101);modifier=self.add(session.engine,'Gloom')
        source=self.add(session.engine,'Generic White Enchantment Mana Source')
        spell=self.add(session.engine,'Generic Bound Growth',zone='hand');target=self.add(session.engine,'Generic Bound Body')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand',ref='gloom-private-control')
        self.prepare(session,mana={'C':3});self.checkpoint(session)
        for card in (modifier,source):self.assertTrue(self.binding(session,card)['strict_capability_ready'])
        rows=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        self.assertFalse(any(r['id']=='cast:'+spell.ref for r in rows))
        action=next(r for r in rows if r['id'].startswith('activate:'+source.ref+':'))
        result=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(result.ok,result.summary)
        self.assertEqual((0,1),tuple(session.state.players['A'].mana_pool[c] for c in ('C','G')))
        rows=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        self.assertFalse(any(r['id'].startswith('undo-mana:') for r in rows))
        session=self.replay(session,load=True);self.cast(session,'A',spell,[target.ref])
        self.assertIsNone(self.resolve(session));self.assertEqual(4,session.engine._numeric_stat(target.object_id,'power'))
        self.private_replay(session,private)

    def test_original_sam_reduces_a_printed_mana_price_to_zero_for_automatic_payment(self):
        session=self.session(71501102);modifier=self.add(session.engine,'Sam, Loyal Attendant')
        source=self.add(session.engine,'Generic Food Mana Source');source.acquired_control_turn_count=-1
        spell=self.add(session.engine,'Generic Bound Growth',zone='hand');target=self.add(session.engine,'Generic Bound Body')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand',ref='sam-private-control')
        self.prepare(session);self.checkpoint(session)
        for card in (modifier,source):self.assertTrue(self.binding(session,card)['strict_capability_ready'])
        rows=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        action=next(r for r in rows if r['id']=='cast:'+spell.ref)
        result=session.act('pilot:A',{'action_id':action['id'],'targets':[target.ref],'pay':'auto'})
        self.assertTrue(result.ok,result.summary);self.assertTrue(session.state.cards[source.object_id].tapped)
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual(0,sum(session.state.players['A'].mana_pool.values()))
        self.assertEqual(4,session.engine._numeric_stat(target.object_id,'power'));self.private_replay(session,private)

    def test_original_power_artifact_applies_only_to_the_actual_enchanted_source(self):
        session=self.session(71501103);aura=self.add(session.engine,'Power Artifact',zone='hand')
        source=self.add(session.engine,'Panacea');other=self.add(session.engine,'Panacea',ref='unenchanted-panacea')
        self.add(session.engine,'Island',ref='cost-aura-island-one');self.add(session.engine,'Island',ref='cost-aura-island-two')
        target=self.add(session.engine,'Grizzly Bears',seat='B');private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,mana={'C':7});self.checkpoint(session)
        self.assertTrue(self.binding(session,aura)['strict_capability_ready'])
        self.cast(session,'A',aura,[source.ref]);self.assertIsNone(self.resolve(session))
        self.assertEqual(source.object_id,session.state.cards[aura.object_id].attached_to)
        for card,remaining in ((source,5),(other,1)):
            action=self.action(session,card)
            result=session.act('pilot:A',{'action_id':action['id'],'targets':[target.ref],'x':2,'pay':'auto'})
            self.assertTrue(result.ok,result.summary);self.assertEqual(remaining,session.state.players['A'].mana_pool['C'])
            self.assertIsNone(self.resolve(session))
        self.private_replay(session,private)

    def test_reduced_waterbend_minimum_can_be_paid_with_one_other_tapped_object(self):
        session=self.session(71501201);modifier=self.add(session.engine,'Training Grounds')
        source=self.add(session.engine,'Generic Waterbend Cost Study');source.acquired_control_turn_count=-1
        helper=self.add(session.engine,'Generic Food Study')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session);self.checkpoint(session)
        for card in (modifier,source):self.assertTrue(self.binding(session,card)['strict_capability_ready'])
        before=set(session.state.players['A'].zones['hand'])
        rows=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        action=next(r for r in rows if r['id'].startswith('activate:'+source.ref+':'))
        schema=action['choice_schema']['waterbend_cards']
        self.assertEqual(1,schema['maximum']);self.assertNotIn(source.ref,schema['legal_refs'])
        result=session.act('pilot:A',{'action_id':action['id'],'waterbend_cards':[helper.ref],'pay':'auto'})
        self.assertTrue(result.ok,result.summary)
        for card in (source,helper):self.assertTrue(session.state.cards[card.object_id].tapped)
        self.assertEqual(0,sum(session.state.players['A'].mana_pool.values()))
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        after=set(session.state.players['A'].zones['hand']);self.assertEqual(1,len(after-before))
        for oid in after-before:
            ref=session.state.cards[oid].ref
            for viewer in 'BCD':self.assertNotIn(ref,json.dumps(session.packet('pilot:'+viewer,full=True)))
        self.private_replay(session,private)

    def test_phyrexian_alternatives_keep_their_distinct_mana_minimum_and_life_price(self):
        for index,life_option in enumerate((False,True)):
            session=self.session(71501210+index);modifier=self.add(session.engine,'Training Grounds')
            source=self.add(session.engine,'Generic Phyrexian Cost Study');source.acquired_control_turn_count=-1
            private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
            self.prepare(session,mana={'C':1} if life_option else {'U':1});self.checkpoint(session)
            for card in (modifier,source):self.assertTrue(self.binding(session,card)['strict_capability_ready'])
            rows=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
            action=next(r for r in rows if r['id'].startswith('activate:'+source.ref+':'))
            option=next(r for r in action['cost_options'] if bool(r['life_payment'])==life_option)
            self.assertEqual(1 if life_option else 0,option['requirements']['GENERIC'])
            self.assertEqual(0 if life_option else 1,option['requirements']['U'])
            result=session.act('pilot:A',{'action_id':action['id'],'cost_option':option['id'],'pay':'auto'})
            self.assertTrue(result.ok,result.summary)
            self.assertEqual(38 if life_option else 40,session.state.players['A'].life)
            self.assertEqual(0,sum(session.state.players['A'].mana_pool.values()))
            session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
            self.assertEqual(40 if life_option else 42,session.state.players['A'].life)
            self.private_replay(session,private)

    def test_snow_obligation_satisfies_the_minimum_but_is_never_reduced(self):
        session=self.session(71501220);modifier=self.add(session.engine,'Training Grounds')
        source=self.add(session.engine,'Generic Snow Cost Study');source.acquired_control_turn_count=-1
        snow=self.add(session.engine,'Snow-Covered Forest');private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,mana={'C':1});self.checkpoint(session)
        for card in (modifier,source):self.assertTrue(self.binding(session,card)['strict_capability_ready'])
        rows=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        action=next(r for r in rows if r['id'].startswith('activate:'+source.ref+':'))
        option,=action['cost_options'];self.assertEqual(0,option['requirements']['GENERIC']);self.assertEqual(1,option['snow_payment'])
        result=session.act('pilot:A',{'action_id':action['id'],'cost_option':option['id'],'pay':'auto'})
        self.assertTrue(result.ok,result.summary);self.assertTrue(session.state.cards[snow.object_id].tapped)
        self.assertEqual(1,session.state.players['A'].mana_pool['C'])
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual(42,session.state.players['A'].life);self.private_replay(session,private)

    def test_power_up_source_reduction_orders_with_static_minimum_after_an_actual_cast(self):
        for index,payment in enumerate((0,1)):
            session=self.session(71501230+index);modifier=self.add(session.engine,'Training Grounds')
            source=self.add(session.engine,'Generic Powerup Cost Study',zone='hand')
            self.add(session.engine,'Mountain');private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
            self.prepare(session,mana={'C':1});self.checkpoint(session)
            for card in (modifier,source):self.assertTrue(self.binding(session,card)['strict_capability_ready'])
            self.cast(session,'A',source);self.assertIsNone(self.resolve(session))
            rows=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
            action=next(r for r in rows if r['id'].startswith('activate:'+source.ref+':'))
            self.assertEqual({0,1},{r['requirements']['GENERIC'] for r in action['cost_options']})
            option=next(r for r in action['cost_options'] if r['requirements']['GENERIC']==payment)
            result=session.act('pilot:A',{'action_id':action['id'],'cost_option':option['id'],'pay':'auto'})
            self.assertTrue(result.ok,result.summary);self.assertEqual(1-payment,session.state.players['A'].mana_pool['C'])
            session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
            self.assertEqual(1,session.state.cards[source.object_id].counters.get('+1/+1',0))
            rows=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
            self.assertFalse(any(r['id']==action['id'] for r in rows));self.private_replay(session,private)

    def test_original_forensic_investigation_creates_a_clue_with_reduced_draw_cost(self):
        session=self.session(71501301);modifier=self.add(session.engine,'Forensic Gadgeteer')
        spell=self.add(session.engine,'Sol Ring',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,mana={'C':2});self.checkpoint(session)
        self.assertTrue(self.binding(session,modifier)['strict_capability_ready'])
        self.cast(session,'A',spell);self.assertIsNone(self.resolve(session))
        clues=[card for card in session.state.cards.values() if card.zone=='battlefield' and card.controller=='A'
            and 'Clue' in str(session.engine._effective_card_data(card).get('type_line') or '')]
        self.assertEqual(1,len(clues));clue=clues[0]
        rows=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        mana=next(r for r in rows if r['id'].startswith('activate:'+spell.ref+':'))
        result=session.act('pilot:A',{'action_id':mana['id']});self.assertTrue(result.ok,result.summary)
        self.assertEqual(3,session.state.players['A'].mana_pool['C'])
        before=set(session.state.players['A'].zones['hand'])
        rows=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        action=next(r for r in rows if r['id'].startswith('activate:'+clue.ref+':'))
        result=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(result.ok,result.summary)
        self.assertEqual(2,session.state.players['A'].mana_pool['C'])
        self.assertNotEqual('battlefield',session.state.cards[clue.object_id].zone)
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual(1,len(set(session.state.players['A'].zones['hand'])-before))
        self.private_replay(session,private)

    def test_generic_nonmana_tax_includes_loyalty_mana_without_changing_counter_cost(self):
        session=self.session(71501302);modifier=self.add(session.engine,'Suppression Field')
        source=self.add(session.engine,'Generic Cost Walker',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,mana={'C':2});self.checkpoint(session)
        for card in (modifier,source):self.assertTrue(self.binding(session,card)['strict_capability_ready'])
        self.cast(session,'A',source);self.assertIsNone(self.resolve(session))
        self.assertEqual(3,session.state.cards[source.object_id].counters['loyalty'])
        rows=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        action=next(r for r in rows if r['id'].startswith('activate:'+source.ref+':'))
        result=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(result.ok,result.summary)
        self.assertEqual(0,session.state.players['A'].mana_pool['C'])
        self.assertEqual(4,session.state.cards[source.object_id].counters['loyalty'])
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual(42,session.state.players['A'].life);self.private_replay(session,private)
