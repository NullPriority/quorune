from __future__ import annotations

from types import SimpleNamespace
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from quorune.compiler.counter_templates import targeted_counter_effect_template
from quorune.model import StackItem
from quorune.rules.node_capability_shapes import targeted_counter_node_capabilities
from quorune.targets import TargetGroup
import quorune.target_predicates as predicates
from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_life_gain_prohibitions as native


class MixedStackCounterTargetTests(unittest.TestCase):
    def matches(self, text, *, kind='spell', types=('instant',), colors=('U',), supertypes=()):
        template = targeted_counter_effect_template(text)
        self.assertIsNotNone(template)
        group = TargetGroup.from_mapping(template.target_schema)
        item = StackItem(stack_id='test',ref='test',kind=kind,controller='B',label='Stack target')
        row = dict(ref=item.ref,zone='stack',category='spell' if kind in {'spell','spell_copy'} else 'ability',
            stack_item=item,card=None,stack_source_types={'artifact','creature'})
        return predicates.target_predicate_matches(SimpleNamespace(),group,row,types=set(types),
            supertypes=set(supertypes),colors=set(colors),derived={})

    def test_mixed_counter_predicates_keep_each_restriction_on_its_own_branch(self):
        consign='Counter target triggered ability or colorless spell.'
        self.assertTrue(self.matches(consign,kind='triggered_ability',colors=('G',)))
        self.assertTrue(self.matches(consign,colors=()))
        self.assertTrue(self.matches(consign,kind='spell_copy',colors=()))
        self.assertFalse(self.matches(consign,kind='activated_ability',colors=()))
        self.assertFalse(self.matches(consign,colors=('U',)))
        noncreature='Counter target activated ability, triggered ability, or noncreature spell.'
        for kind in ('activated_ability','triggered_ability'):
            self.assertTrue(self.matches(noncreature,kind=kind,types=('creature',),colors=('G',)))
        self.assertTrue(self.matches(noncreature,types=('instant',)))
        self.assertFalse(self.matches(noncreature,types=('creature',)))
        legendary='Counter target activated ability, triggered ability, or legendary spell.'
        for kind in ('activated_ability','triggered_ability'):
            self.assertTrue(self.matches(legendary,kind=kind,supertypes=()))
        self.assertTrue(self.matches(legendary,types=('creature',),supertypes=('legendary',)))
        self.assertFalse(self.matches(legendary,types=('creature',),supertypes=()))

    def test_blue_instant_counter_requires_both_properties_on_the_same_spell(self):
        text='Counter target blue instant spell.'
        self.assertTrue(self.matches(text,types=('instant',),colors=('U',)))
        self.assertTrue(self.matches(text,kind='spell_copy',types=('instant',),colors=('U','R')))
        self.assertFalse(self.matches(text,types=('instant',),colors=('R',)))
        self.assertFalse(self.matches(text,types=('sorcery',),colors=('U',)))
        self.assertFalse(self.matches(text,kind='triggered_ability',types=('instant',),colors=('U',)))

    def test_mixed_stack_counter_shapes_reject_category_predicate_and_source_near_misses(self):
        forms=(
            'triggered ability or colorless spell',
            'activated ability, triggered ability, or noncreature spell',
            'activated ability, triggered ability, or legendary spell','blue instant spell')
        for form in forms:
            template=targeted_counter_effect_template('Counter target '+form+'.')
            self.assertIsNotNone(template)
            required=targeted_counter_node_capabilities(effects=template.effects,
                target_schema=template.target_schema,mechanic_ids=template.mechanics)
            self.assertEqual(('stack.counter.effect','target.revalidate_resolution'),required)
            for change in ({'categories':['permanent']},{'predicate':'unknown_stack_union'},
                    {'source_exclusion':False},{'count':True}):
                self.assertFalse(targeted_counter_node_capabilities(effects=template.effects,
                    target_schema={**template.target_schema,**change},mechanic_ids=template.mechanics))
        for text in ('Counter target activated ability from an artifact source you control.',
                'Counter target activated or triggered ability from a noncreature source with mana value 2.',
                'Counter target blue instant spell unless its controller pays {2}. Draw a card.',
                'Counter target legendary activated ability.'):
            self.assertIsNone(targeted_counter_effect_template(text))

    def test_stack_followup_requires_one_bound_target_and_preserves_deferred_operands(self):
        from quorune.compiler.stack_counter_followup import stack_counter_followup_template
        from quorune.oracle_ir import _reviewed_effect_template
        from quorune.rules.stack_counter_followup import (
            STACK_COUNTER_FOLLOWUP_CAPABILITY, STACK_TARGET_CONTROLLER, bound_stack_counter_instructions,
            materialize_stack_counter_followup, stack_counter_followup_node_capabilities,
        )

        def compile_text(value):
            return stack_counter_followup_template(
                value, compile_component=lambda clause: _reviewed_effect_template(clause, card_name='Followup Fixture'),
            )

        _, effects, schema, mechanics = compile_text(
            'Counter target spell. Its controller loses life equal to the number of creatures you control.',
        )
        raw, = effects
        self.assertIn(STACK_COUNTER_FOLLOWUP_CAPABILITY, stack_counter_followup_node_capabilities(
            effects=effects, target_schema=schema, mechanic_ids=mechanics,
        ))
        calls = []

        def resolve(reference):
            calls.append(reference)
            return {'$target.0': 'S7', STACK_TARGET_CONTROLLER: 'C'}[reference]

        bound = materialize_stack_counter_followup(raw, resolve=resolve)
        counter, loss = bound_stack_counter_instructions(bound, active_seats=('A', 'B', 'C'))
        self.assertEqual(['$target.0', STACK_TARGET_CONTROLLER], calls)
        self.assertEqual({'op': 'counter_stack_target', 'stack': 'S7'}, counter)
        self.assertEqual('C', loss['player'])
        self.assertEqual(raw['followups'][0]['amount'], loss['amount'])
        self.assertEqual({'op': raw['op'], 'stack': None}, materialize_stack_counter_followup(raw, resolve=lambda _: None))
        for forged in ({**raw, 'schema_version': True}, {**raw, 'stack': '$target.1'},
                       {**raw, 'followups': [{**raw['followups'][0], 'player': '$controller'}]}):
            self.assertEqual((), stack_counter_followup_node_capabilities(
                effects=(forged,), target_schema=schema, mechanic_ids=mechanics,
            ))
        for near in ('Counter target spell. If it was countered this way, its controller mills two cards.',
                     'Counter target spell. Its owner draws a card.',
                     'Counter target spell. Its controller mills two cards. Draw a card.'):
            self.assertIsNone(compile_text(near))

    def test_accepting_every_mixed_stack_predicate_is_detected(self):
        method='test_mixed_counter_predicates_keep_each_restriction_on_its_own_branch'
        with patch.object(predicates,'target_predicate_matches',return_value=True):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()


class MixedStackCounterRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=native.LifeGainProhibitionRuntimeTests.session
    add=native.LifeGainProhibitionRuntimeTests.add
    prepare=native.LifeGainProhibitionRuntimeTests.prepare
    checkpoint=native.LifeGainProhibitionRuntimeTests.checkpoint
    resolve=native.LifeGainProhibitionRuntimeTests.resolve
    replay=native.LifeGainProhibitionRuntimeTests.replay
    private_replay=native.LifeGainProhibitionRuntimeTests.private_replay
    priority=native.LifeGainProhibitionRuntimeTests.priority
    actions=native.LifeGainProhibitionRuntimeTests.actions
    cast=native.LifeGainProhibitionRuntimeTests.cast
    reject=native.LifeGainProhibitionRuntimeTests.reject
    binding=native.LifeGainProhibitionRuntimeTests.binding
    activation_command=native.LifeGainProhibitionRuntimeTests.activation_command
    activate=native.LifeGainProhibitionRuntimeTests.activate
    resume=native.LifeGainProhibitionRuntimeTests.resume
    settle=native.LifeGainProhibitionRuntimeTests.settle

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'mixed-stack-counters.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/mixed-stack-counter-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Mixed stack counter targets',[
            DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def fixture(self,seed,name):
        s=self.session(seed);counter=self.add(s.engine,name,zone='hand')
        private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.add(s.engine,'Vedalken Orrery',seat='B')
        s.state.players['B'].mana_pool.update({'C':12,'U':8,'R':4,'G':4})
        return s,counter,private

    def counter(self,s,card,target,*,payment=None,sacrifice=None,replicate=None):
        self.priority(s,'A')
        row=next((row for row in self.actions(s) if row['id']=='cast:'+card.ref),None)
        self.assertIsNotNone(row,card.printed_name)
        self.assertTrue(self.binding(s,card)['strict_capability_ready'])
        command={'action_id':row['id'],'targets':[target],'pay':'auto'}
        options=row['cost_options']
        if sacrifice is not None:
            option=next(option for option in options if 'sacrifice_cards' in option.get('choice_schema',{}))
            command.update(cost_option=option['id'],sacrifice_cards=[sacrifice.ref])
        elif replicate is not None:
            option=next(option for option in options if '+replicate:' in option['id'])
            command.update(cost_option=option['id'],replicate_count=replicate)
        elif card.printed_name=="Louisoix's Sacrifice":
            option=next(option for option in options if not option.get('choice_schema'))
            command['cost_option']=option['id']
        elif card.printed_name=='Guttural Response' and payment is not None:
            color=next(iter(payment))
            option=next(option for option in options if option['requirements'].get(color)==1)
            command['cost_option']=option['id']
        else:
            option=next((option for option in options if option['id']=='normal'),options[0])
            command['cost_option']=option['id']
        if payment is not None:command.update(pay='manual',payment=payment)
        self.reject(s,command,seat='D')
        result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)

    def test_psychic_strike_mills_after_ordinary_and_protected_counter_attempts(self):
        for i, (name, payment, zone) in enumerate((
            ('Opt', {'U': 1}, 'graveyard'), ('Carnage Tyrant', {'C': 4, 'G': 2}, 'battlefield'),
        )):
            with self.subTest(target=name):
                s, counter, private = self.fixture(115100250 + i, 'Psychic Strike')
                target = self.add(s.engine, name, seat='B', zone='hand')
                self.assertTrue(self.binding(s, target)['strict_capability_ready'])
                self.prepare(s, mana={'C': 1, 'U': 1, 'B': 1})
                self.checkpoint(s)
                self.cast(s, target, seat='B', payment=payment)
                libraries = {seat: len(s.state.players[seat].zones['library']) for seat in 'ABCD'}
                self.counter(s, counter, s.state.stack[-1].ref, payment={'C': 1, 'U': 1, 'B': 1})
                self.assertIsNone(self.settle(s))
                self.assertEqual(zone, s.state.cards[target.object_id].zone)
                libraries['B'] -= 2
                self.assertEqual(libraries, {seat: len(s.state.players[seat].zones['library']) for seat in 'ABCD'})
                self.private_replay(s, private)

    def test_countermand_skips_followup_when_its_original_stack_target_disappears(self):
        for i, (name, payment) in enumerate((('Countermand', {'C': 2, 'U': 2}), ('Dream Fracture', {'C': 1, 'U': 2}))):
            with self.subTest(card=name):
                s, counter, private = self.fixture(115100252 + i, name)
                target = self.add(s.engine, 'Opt', seat='B', zone='hand')
                response = self.add(s.engine, 'Counterspell', seat='C', zone='hand')
                s.state.players['C'].mana_pool.update({'U': 2})
                self.prepare(s, mana=payment)
                self.checkpoint(s)
                self.cast(s, target, seat='B', payment={'U': 1})
                target_ref = s.state.stack[-1].ref
                libraries = {seat: len(s.state.players[seat].zones['library']) for seat in 'AB'}
                self.counter(s, counter, target_ref, payment=payment)
                self.cast(s, response, [target_ref], seat='C', payment={'U': 2})
                self.assertIsNone(self.settle(s))
                self.assertEqual(libraries, {seat: len(s.state.players[seat].zones['library']) for seat in 'AB'})
                self.assertEqual('graveyard', s.state.cards[target.object_id].zone)
                self.assertEqual('graveyard', s.state.cards[counter.object_id].zone)
                self.private_replay(s, private)

    def test_dismal_failure_discard_choice_belongs_to_the_countered_objects_controller(self):
        s, counter, private = self.fixture(115100253, 'Dismal Failure')
        target = self.add(s.engine, 'Opt', seat='B', zone='hand')
        hidden = self.add(s.engine, 'Fugitive Wizard', seat='B', zone='hand', ref='bound-discard-choice')
        self.prepare(s, mana={'C': 2, 'U': 2})
        self.checkpoint(s)
        self.cast(s, target, seat='B', payment={'U': 1})
        self.counter(s, counter, s.state.stack[-1].ref, payment={'C': 2, 'U': 2})
        decision = self.resolve(s)
        self.assertIsNotNone(decision)
        self.assertEqual(['pilot:B'], s.pending_principals())
        self.assertEqual('graveyard', s.state.cards[target.object_id].zone)
        self.assertIn(hidden.ref, json.dumps(s.packet('pilot:B', full=True)))
        for seat in 'ACD':
            self.assertNotIn(hidden.ref, json.dumps(s.packet('pilot:' + seat, full=True)))
        command = {'action_id': 'choose', 'cards': [hidden.ref]}
        self.reject(s, command, seat='A')
        s = self.replay(s, load=True)
        result = s.act('pilot:B', command)
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.settle(s))
        self.assertEqual('graveyard', s.state.cards[hidden.object_id].zone)
        self.private_replay(s, private)

    def test_deny_the_witch_counts_resolvers_creatures_and_binds_an_ability_controller(self):
        for count in (0, 2):
            with self.subTest(creatures=count):
                s, counter, private = self.fixture(115100254 + count, 'Deny the Witch')
                rod = self.add(s.engine, 'Rod of Ruin', seat='B')
                for i in range(count):
                    self.add(s.engine, 'Fugitive Wizard', ref=f'counter-query-creature-{i}')
                self.prepare(s, mana={'C': 1, 'W': 1, 'U': 1, 'B': 1})
                self.checkpoint(s)
                self.activate(s, rod, seat='B', targets=['C'])
                self.assertEqual('activated_ability', s.state.stack[-1].kind)
                self.counter(s, counter, s.state.stack[-1].ref, payment={'C': 1, 'W': 1, 'U': 1, 'B': 1})
                self.assertIsNone(self.settle(s))
                self.assertEqual([40, 40 - count, 40, 40], [s.state.players[seat].life for seat in 'ABCD'])
                self.private_replay(s, private)

    def test_dream_fracture_draws_for_the_stack_controller_then_the_resolver(self):
        s, counter, private = self.fixture(115100255, 'Dream Fracture')
        target = self.add(s.engine, 'Opt', seat='B', zone='hand')
        self.prepare(s, mana={'C': 1, 'U': 2})
        self.checkpoint(s)
        self.cast(s, target, seat='B', payment={'U': 1})
        self.counter(s, counter, s.state.stack[-1].ref, payment={'C': 1, 'U': 2})
        hands = {seat: set(s.state.players[seat].zones['hand']) for seat in 'ABCD'}
        event_start = len(s.state.events)
        self.assertIsNone(self.settle(s))
        self.assertEqual(['B', 'A'], [event.actor for event in s.state.events[event_start:] if event.code == 'card.draw'])
        for seat in 'AB':
            drawn, = set(s.state.players[seat].zones['hand']) - hands[seat]
            ref = s.state.cards[drawn].ref
            for other in 'ABCD':
                self.assertEqual(seat == other, ref in json.dumps(s.packet('pilot:' + other, full=True)))
        self.assertEqual('graveyard', s.state.cards[target.object_id].zone)
        self.private_replay(s, private)

    def test_counter_followup_withdrawal_and_omitted_compiler_rule_are_detected(self):
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        record = self.db.lookup('Psychic Strike')

        def witness():
            self.assertEqual('exact', compile_oracle_card(record, capability_registry=self.registry).status)

        witness()
        with patch('quorune.compiler.stack_counter_followup.stack_counter_followup_template', return_value=None):
            with self.assertRaises(AssertionError):
                witness()
        value = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        dependency = 'stack.counter.effect'
        next(row for row in value['capabilities'] if row['id'] == dependency).update(
            status='blocked', blockers=['withdrawn counter authority'], dependency_fail_closed_status='not_run',
        )
        registry = CapabilityRegistry(value)
        index = build_index(registry_value=value,
            declaration_source=read_evidence_document(ROOT / 'platform/capability-evidence-declarations.json'),
            discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        ir = compile_oracle_card(record, capability_registry=registry)
        self.assertNotEqual('exact', ir.status)
        self.assertTrue(any(dependency in blocker for residual in ir.material_residuals for blocker in residual.blockers))

    def test_actual_guttural_requires_blue_instant_and_pays_either_hybrid_color(self):
        for i,(target_name,allowed,color) in enumerate((('Opt',True,'R'),('Opt',True,'G'),
                ('Divination',False,'R'),('Shock',False,'G'))):
            with self.subTest(target=target_name,payment=color):
                s,counter,private=self.fixture(115100100+i,'Guttural Response')
                target=self.add(s.engine,target_name,seat='B',zone='hand')
                self.prepare(s,mana={'R':1,'G':1})
                self.checkpoint(s)
                payment={'C':2,'U':1} if target_name=='Divination' else {'R':1} if target_name=='Shock' else {'U':1}
                self.cast(s,target,['A'] if target_name=='Shock' else [],seat='B',payment=payment)
                original=s.state.stack[-1].ref
                before=set(s.state.players['B'].zones['hand'])
                if allowed:self.counter(s,counter,original,payment={color:1})
                else:
                    self.priority(s,'A')
                    self.reject(s,{'action_id':'cast:'+counter.ref,'targets':[original],
                        'pay':'manual','payment':{color:1}})
                s=self.resume(s);self.assertIsNone(self.settle(s))
                self.assertEqual('graveyard',s.state.cards[target.object_id].zone)
                self.assertEqual('graveyard' if allowed else 'hand',s.state.cards[counter.object_id].zone)
                self.assertEqual(38 if target_name=='Shock' else 40,s.state.players['A'].life)
                drawn=set(s.state.players['B'].zones['hand'])-before
                self.assertEqual(2 if target_name=='Divination' else 0,len(drawn))
                for object_id in drawn:
                    ref=s.state.cards[object_id].ref
                    for seat in 'ACD':self.assertNotIn(ref,json.dumps(s.packet('pilot:'+seat,full=True)))
                self.private_replay(s,private)

    def test_actual_consign_distinguishes_trigger_colorless_spell_and_colorless_activated_source(self):
        for i,case in enumerate(('trigger','colorless_spell','activated','colored_spell')):
            with self.subTest(case=case):
                s,counter,private=self.fixture(115100120+i,'Consign to Memory')
                name='Elvish Visionary' if case=='trigger' else 'Rod of Ruin' if case=='activated' else 'Ornithopter' if case=='colorless_spell' else 'Fugitive Wizard'
                target=self.add(s.engine,name,seat='B',zone='battlefield' if case=='activated' else 'hand')
                target.acquired_control_turn_count=-1
                self.prepare(s,mana={'U':1})
                self.checkpoint(s)
                hand=set(s.state.players['B'].zones['hand'])
                if case=='activated':self.activate(s,target,seat='B',targets=['A'])
                else:
                    payment={'C':1,'G':1} if case=='trigger' else {} if case=='colorless_spell' else {'U':1}
                    self.cast(s,target,seat='B',payment=payment)
                    if case=='trigger':
                        while s.state.cards[target.object_id].zone!='battlefield':
                            result=s.act(s.pending_principals()[0],{'action_id':'pass'})
                            self.assertTrue(result.ok,result.summary)
                original=s.state.stack[-1].ref
                allowed=case in {'trigger','colorless_spell'}
                if allowed:self.counter(s,counter,original,payment={'U':1})
                else:
                    self.priority(s,'A')
                    self.reject(s,{'action_id':'cast:'+counter.ref,'targets':[original],'pay':'manual','payment':{'U':1}})
                s=self.resume(s);self.assertIsNone(self.settle(s))
                self.assertEqual('graveyard' if allowed else 'hand',s.state.cards[counter.object_id].zone)
                self.assertEqual('graveyard' if case=='colorless_spell' else 'battlefield',s.state.cards[target.object_id].zone)
                self.assertEqual(39 if case=='activated' else 40,s.state.players['A'].life)
                self.assertFalse(set(s.state.players['B'].zones['hand'])-hand)
                self.private_replay(s,private)

    def test_actual_tales_end_ignores_ability_source_legendary_but_restricts_spells(self):
        for i,case in enumerate(('activated','legendary_spell','ordinary_spell')):
            with self.subTest(case=case):
                s,counter,private=self.fixture(115100140+i,"Tale's End")
                name='Rod of Ruin' if case=='activated' else 'Jace Beleren' if case=='legendary_spell' else 'Fugitive Wizard'
                target=self.add(s.engine,name,seat='B',zone='battlefield' if case=='activated' else 'hand')
                self.prepare(s,mana={'C':1,'U':1});self.checkpoint(s)
                if case=='activated':self.activate(s,target,seat='B',targets=['A'])
                else:self.cast(s,target,seat='B',payment={'C':1,'U':2} if case=='legendary_spell' else {'U':1})
                original=s.state.stack[-1].ref
                if case!='ordinary_spell':self.counter(s,counter,original,payment={'C':1,'U':1})
                else:
                    self.priority(s,'A');self.reject(s,{'action_id':'cast:'+counter.ref,'targets':[original],'pay':'auto'})
                s=self.resume(s);self.assertIsNone(self.settle(s))
                self.assertEqual('graveyard' if case!='ordinary_spell' else 'hand',s.state.cards[counter.object_id].zone)
                self.assertEqual('graveyard' if case=='legendary_spell' else 'battlefield',s.state.cards[target.object_id].zone)
                self.assertEqual(40,s.state.players['A'].life)
                self.private_replay(s,private)

    def test_actual_louisoix_keeps_both_cost_branches_and_does_not_filter_creature_abilities(self):
        for i,sacrifice in enumerate((False,True)):
            with self.subTest(sacrifice=sacrifice):
                s,counter,private=self.fixture(115100160+i,"Louisoix's Sacrifice")
                target=self.add(s.engine,'Elvish Visionary',seat='B',zone='hand')
                price=self.add(s.engine,'Isamaru, Hound of Konda') if sacrifice else None
                self.prepare(s,mana={'C':2,'U':1});self.checkpoint(s)
                self.cast(s,target,seat='B',payment={'C':1,'G':1})
                while s.state.cards[target.object_id].zone!='battlefield':
                    result=s.act(s.pending_principals()[0],{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
                before=set(s.state.players['B'].zones['hand'])
                self.counter(s,counter,s.state.stack[-1].ref,payment={'U':1} if sacrifice else {'C':2,'U':1},sacrifice=price)
                self.assertEqual(2 if sacrifice else 0,s.state.players['A'].mana_pool['C'])
                if price:self.assertEqual('graveyard',s.state.cards[price.object_id].zone)
                s=self.resume(s);self.assertIsNone(self.settle(s))
                self.assertEqual(before,set(s.state.players['B'].zones['hand']))
                self.assertEqual('battlefield',s.state.cards[target.object_id].zone)
                self.assertEqual('graveyard',s.state.cards[counter.object_id].zone)
                self.private_replay(s,private)

    def test_consign_replicate_retargets_independent_copies_across_both_legal_branches(self):
        s,counter,private=self.fixture(115100180,'Consign to Memory')
        visionary=self.add(s.engine,'Elvish Visionary',seat='B',zone='hand')
        artifact=self.add(s.engine,'Ornithopter',seat='B',zone='hand')
        self.prepare(s,mana={'C':1,'U':1});self.checkpoint(s)
        self.cast(s,visionary,seat='B',payment={'C':1,'G':1})
        while s.state.cards[visionary.object_id].zone!='battlefield':
            result=s.act(s.pending_principals()[0],{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
        trigger=s.state.stack[-1].ref
        self.cast(s,artifact,seat='B',payment={})
        spell=s.state.stack[-1].ref
        before=set(s.state.players['B'].zones['hand'])
        self.counter(s,counter,spell,payment={'C':1,'U':1},replicate=1)
        decision=self.resolve(s)
        self.assertIsNotNone(decision);self.assertEqual('semantic.storm',decision.kind)
        command={'action_id':'choose','copy_targets':[[trigger]]}
        self.reject(s,command,seat='D')
        s=self.resume(s);result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        self.assertTrue(any(item.kind=='spell_copy' for item in s.state.stack))
        self.assertIsNone(self.settle(s))
        self.assertEqual(before,set(s.state.players['B'].zones['hand']))
        self.assertEqual('battlefield',s.state.cards[visionary.object_id].zone)
        self.assertEqual('graveyard',s.state.cards[artifact.object_id].zone)
        self.assertEqual('graveyard',s.state.cards[counter.object_id].zone)
        self.assertEqual(0,sum(s.state.players['A'].mana_pool.values()))
        self.private_replay(s,private)

    def test_louisoix_noncreature_spell_branch_rejects_a_creature_spell_without_paying(self):
        for i,allowed in enumerate((True,False)):
            with self.subTest(allowed=allowed):
                s,counter,private=self.fixture(115100190+i,"Louisoix's Sacrifice")
                target=self.add(s.engine,'Divination' if allowed else 'Fugitive Wizard',seat='B',zone='hand')
                self.prepare(s,mana={'C':2,'U':1});self.checkpoint(s)
                self.cast(s,target,seat='B',payment={'C':2,'U':1} if allowed else {'U':1})
                original=s.state.stack[-1].ref
                before=set(s.state.players['B'].zones['hand'])
                if allowed:self.counter(s,counter,original,payment={'C':2,'U':1})
                else:
                    self.priority(s,'A')
                    row=next((row for row in self.actions(s) if row['id']=='cast:'+counter.ref),None)
                    command={'action_id':'cast:'+counter.ref,'targets':[original],'pay':'manual','payment':{'C':2,'U':1}}
                    if row:
                        command['cost_option']=next(option['id'] for option in row['cost_options'] if not option.get('choice_schema'))
                    self.reject(s,command)
                s=self.resume(s);self.assertIsNone(self.settle(s))
                self.assertEqual(before,set(s.state.players['B'].zones['hand']))
                self.assertEqual('graveyard' if allowed else 'battlefield',s.state.cards[target.object_id].zone)
                self.assertEqual('graveyard' if allowed else 'hand',s.state.cards[counter.object_id].zone)
                self.assertEqual(0 if allowed else 2,s.state.players['A'].mana_pool['C'])
                self.private_replay(s,private)

    def test_mixed_stack_counter_authority_withdrawals_are_specific_rules_blocks(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from quorune.oracle_ir import compile_oracle_card
        from scripts.update_capability_evidence import build_index,discover_tests
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        declarations=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        tests=set(discover_tests(ROOT).values())
        names=('Consign to Memory',"Louisoix's Sacrifice","Tale's End",'Guttural Response')
        for cap in ('stack.counter.effect','target.revalidate_resolution'):
            changed=deepcopy(raw)
            next(row for row in changed['capabilities'] if row['id']==cap).update(status='blocked',
                blockers=['withdrawn mixed-stack target authority'],dependency_fail_closed_status='not_run')
            registry=CapabilityRegistry(changed)
            index=build_index(registry_value=changed,declaration_source=declarations,discovered_test_ids=tests)
            registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
            for name in names:
                ir=compile_oracle_card(self.db.lookup(name),capability_registry=registry,capability_profile='commander_review')
                blockers=[blocker for residual in ir.material_residuals for blocker in residual.blockers]
                self.assertTrue(any('status:'+cap+':blocked' in blocker for blocker in blockers),blockers)
                self.assertFalse(any('evidence_index:unverified' in blocker for blocker in blockers),blockers)

    def test_mixed_counter_programs_require_runtime_and_retain_transitive_target_authority(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        for i,name in enumerate(('Consign to Memory',"Louisoix's Sacrifice","Tale's End",'Guttural Response')):
            s,card,private=self.fixture(115100210+i,name)
            program=next(program for program in s.engine.semantics.programs_for_oracle(card.oracle_id)
                if any(effect.get('op')=='counter_stack_target' for effect in program.effects))
            for cap in ('stack.counter.effect','target.revalidate_resolution'):
                dependencies=tuple(value for value in program.capability_dependencies if value!=cap)
                missing=replace(program,capability_dependencies=dependencies,
                    capability_closure=self.registry.closure(dependencies,profile='commander_review').to_dict())
                bound=bind_semantic_program_runtime(missing,capability_registry=self.registry,profile='commander_review')
                if cap=='stack.counter.effect':
                    self.assertFalse(bound['strict'])
                    self.assertTrue(any(cap in blocker for blocker in bound['blockers']),bound['blockers'])
                else:
                    # Target authority is still required transitively by the
                    # declared stack-counter capability; it was not removed.
                    self.assertIn(cap,self.registry.closure(dependencies,profile='commander_review').reachable)
                    self.assertTrue(bound['strict'],bound['blockers'])


if __name__=='__main__':
    unittest.main()
