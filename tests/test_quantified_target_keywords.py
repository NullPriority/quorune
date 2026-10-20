from __future__ import annotations

"""CR115.3/608.2b/611.2c: each selected target receives one fixed result."""

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry,load_default_capability_registry
from quorune.rules.target_characteristic_sets import decode_target_characteristics,target_characteristic_set_capabilities
from scripts.build_test_database import build_fixture_database
from test_bound_effect_programs import record
import test_compound_damage_recipients as controls
import test_temporary_keyword_removal as journeys


class QuantifiedTargetKeywordCompilerTests(unittest.TestCase):
    def test_scoped_modal_reference_uses_only_the_server_captured_current_group(self):
        from types import SimpleNamespace
        from quorune.semantic_runtime.values import resolve_semantic_value
        from quorune.semantic_runtime.context import SemanticNodeError
        item=SimpleNamespace(context={'target_groups':{'target_0':['A','B']},
            'target_groups_current':{'target_0':['A']}},targets=['A',None],controller='A')
        self.assertEqual(['A'],resolve_semantic_value(None,'$target_group.target_0',item))
        with self.assertRaises(SemanticNodeError):resolve_semantic_value(None,'$target_group.target_1',item)
        with self.assertRaises(SemanticNodeError):resolve_semantic_value(None,'$target_group.target_0.0',item)
        item.context['target_groups_current']['target_0']=[{}]
        with self.assertRaises(SemanticNodeError):resolve_semantic_value(None,'$target_group.target_0',item)

    def test_modal_target_sets_use_the_same_implicit_group_ids_and_counts_as_selection(self):
        from copy import deepcopy
        from quorune.targets import mode_effects
        creature={'zones':['battlefield'],'categories':['permanent'],'types_any':['creature'],'count':2,
            'effects':[{'op':'test-group','cards':'$targets'}]}
        player={'zones':['player'],'categories':['player'],'count':1,
            'effects':[{'op':'test-player','player':'$target.0','controller':'$target.controller.0'}]}
        schema={'min_modes':1,'max_modes':2,'modes':{'pair':creature,'player':player}}
        before=deepcopy(schema)
        effects=mode_effects(schema,('pair','player'),target_groups={'target_0':['A','B'],'target_1':['C']})
        self.assertEqual('$target_group.target_0',effects[0]['cards'])
        self.assertEqual('$target.2',effects[1]['player']);self.assertEqual('$target.controller.2',effects[1]['controller'])
        from types import SimpleNamespace
        from quorune.engine import _stack_mode_effects
        item=SimpleNamespace(modes=('pair','player'),context={
            'target_groups':{'target_0':['A','B'],'target_1':['C']},
            'target_groups_current':{'target_0':['B'],'target_1':['C']}})
        surviving=_stack_mode_effects(schema,item)
        self.assertEqual('$target.2',surviving[1]['player'])
        self.assertEqual(before,schema)
        for invalid in (('player','pair'),('pair','pair'),('unknown',)):
            with self.assertRaises(ValueError):mode_effects(schema,invalid,target_groups={})
        optional={'mode_count':1,'modes':{'pair':{**creature,'count':None,'up_to':2}}}
        self.assertEqual('$target_group.target_0',mode_effects(optional,('pair',),target_groups={'target_0':[]})[0]['cards'])

    def test_plural_result_lowers_once_with_canonical_first_strike_authority(self):
        text='Up to two target creatures each get +1/+0 and gain first strike until end of turn.'
        ir=compile_oracle_card(record(text,'Instant'),capability_registry=load_default_capability_registry(),capability_profile='commander_review')
        self.assertEqual('exact',ir.status,ir.material_residuals)
        node=ir.faces[0].nodes[0];effect=node.effects[0]
        self.assertEqual((0,len(text)),(node.span.start,node.span.end))
        self.assertEqual(2,node.target_schema['up_to']);self.assertEqual(6,effect['schema_version'])
        self.assertEqual(['First Strike'],list(effect['keywords']));self.assertEqual((1,0),(effect['power'],effect['toughness']))
        self.assertIn('first-strike',node.mechanics)
        self.assertTrue(target_characteristic_set_capabilities(effects=node.effects,target_schema=node.target_schema,mechanic_ids=node.mechanics))

    def test_unknown_results_malformed_keywords_and_withdrawn_keyword_authority_remain_closed(self):
        caps=load_default_capability_registry()
        for tail in ('gain NotARealAbility','gain first strike or lifelink','gain toxic 1','gain flying and draw a card'):
            text='Up to two target creatures each get +1/+0 and '+tail+' until end of turn.'
            self.assertNotEqual('exact',compile_oracle_card(record(text,'Instant'),capability_registry=caps,capability_profile='commander_review').status,text)
        effect={'op':'apply_source_characteristics_until_end_of_turn','schema_version':6,'cards':'$targets',
            'maximum_targets':2,'power':1,'toughness':0,'keywords':[{}]}
        with self.assertRaises(ValueError):decode_target_characteristics(effect)
        from quorune.keyword_abilities import FIXED_CHARACTERISTIC_KEYWORD_CAPABILITIES
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        keyword_cap=FIXED_CHARACTERISTIC_KEYWORD_CAPABILITIES['First Strike'][0]
        next(row for row in raw['capabilities'] if row['id']==keyword_cap).update(status='blocked',blockers=['withdrawn'])
        text='Up to two target creatures each get +1/+0 and gain first strike until end of turn.'
        self.assertNotEqual('exact',compile_oracle_card(record(text,'Instant'),capability_registry=CapabilityRegistry(raw),capability_profile='commander_review').status)


class QuantifiedTargetKeywordRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=controls.CompoundDamageRuntimeTests.session
    add=controls.CompoundDamageRuntimeTests.add
    prepare=controls.CompoundDamageRuntimeTests.prepare
    checkpoint=controls.CompoundDamageRuntimeTests.checkpoint
    cast=controls.CompoundDamageRuntimeTests.cast
    resolve=controls.CompoundDamageRuntimeTests.resolve
    replay=controls.CompoundDamageRuntimeTests.replay
    binding=controls.CompoundDamageRuntimeTests.binding
    actions=controls.CompoundDamageRuntimeTests.actions
    action=controls.CompoundDamageRuntimeTests.action
    private_replay=controls.CompoundDamageRuntimeTests.private_replay
    advance=journeys.TemporaryKeywordRemovalRuntimeTests.advance

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'quantified-target-keywords.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/quantified-target-keyword-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Quantified target keywords',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()

    def test_original_coordinated_assault_accepts_zero_one_or_two_distinct_targets_and_applies_both_results(self):
        for count in (0,1,2):
            session=self.session(730501+count);spell=self.add(session.engine,'Coordinated Assault',zone='hand')
            bodies=[self.add(session.engine,'Generic Bound Body',seat=seat,ref='coordinated-'+seat) for seat in 'AB']
            unselected=self.add(session.engine,'Generic Bound Body',seat='D',ref='coordinated-unselected')
            private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
            self.prepare(session,mana={'R':1});self.checkpoint(session)
            self.assertTrue(self.binding(session,spell)['strict_capability_ready'])
            action=self.action(session,spell);before=authoritative_state_hash(session.state)
            for principal,refs in (('pilot:B',[bodies[0].ref]),('pilot:A',[bodies[0].ref,bodies[0].ref])):
                bad=session.act(principal,{'action_id':action['id'],'targets':refs,'pay':'auto'})
                self.assertFalse(bad.ok);self.assertEqual(before,authoritative_state_hash(session.state))
            self.cast(session,'A',spell,[body.ref for body in bodies[:count]])
            session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
            for index,body in enumerate(bodies):
                card=session.state.cards[body.object_id]
                self.assertEqual(3 if index<count else 2,session.engine._numeric_stat(card.object_id,'power'))
                self.assertEqual(6,session.engine._numeric_stat(card.object_id,'toughness'))
                self.assertEqual(index<count,'first strike' in session.engine._combat_keywords(card))
            self.assertEqual(2,session.engine._numeric_stat(unselected.object_id,'power'))
            self.assertEqual('graveyard',session.state.cards[spell.object_id].zone)
            self.private_replay(session,private)

    def test_existing_public_quantity_target_set_keeps_zero_and_nonzero_runtime_admission(self):
        for count in (0,2):
            session=self.session(730505+count);spell=self.add(session.engine,'Generic Scalar Target Group',zone='hand')
            target=self.add(session.engine,'Generic Bound Body',seat='B')
            for index in range(count):self.add(session.engine,'Forest',ref='quantity-forest-'+str(index))
            private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
            self.prepare(session,mana={'W':1});self.checkpoint(session)
            self.assertTrue(self.binding(session,spell)['strict_capability_ready'])
            self.cast(session,'A',spell,[target.ref]);session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
            self.assertEqual((2+count,6+count),(session.engine._numeric_stat(target.object_id,'power'),session.engine._numeric_stat(target.object_id,'toughness')))
            self.private_replay(session,private)

    def test_fresh_original_coordinated_assault_kills_omitted_target_set_application(self):
        method='test_original_coordinated_assault_accepts_zero_one_or_two_distinct_targets_and_applies_both_results'
        with patch('quorune.continuous_effect_state.apply_target_characteristics',return_value=()):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()

    def test_original_coordinated_first_strike_kills_a_real_blocker_before_normal_damage_and_expires(self):
        session=self.session(730510);spell=self.add(session.engine,'Coordinated Assault',zone='hand')
        attacker=self.add(session.engine,'Silvercoat Lion')
        blocker=self.add(session.engine,'Grizzly Bears',seat='B')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,mana={'R':1});self.checkpoint(session)
        self.cast(session,'A',spell,[attacker.ref]);self.assertIsNone(self.resolve(session))
        self.advance(session,lambda s:s.state.pending_decision is not None and s.state.pending_decision.kind=='combat.attackers')
        declared=session.act('pilot:A',{'action_id':'attack','attacks':{attacker.ref:'B'}})
        self.assertTrue(declared.ok,declared.summary)
        self.advance(session,lambda s:s.state.pending_decision is not None and s.state.pending_decision.kind=='combat.blockers')
        blocked=session.act('pilot:B',{'action_id':'block','blocks':{blocker.ref:[attacker.ref]}})
        self.assertTrue(blocked.ok,blocked.summary)
        self.advance(session,lambda s:s.state.cards[blocker.object_id].zone=='graveyard')
        self.assertEqual('battlefield',session.state.cards[attacker.object_id].zone)
        self.assertEqual(0,session.state.cards[attacker.object_id].marked_damage)
        turn=session.state.turn_sequence;self.advance(session,lambda s:s.state.turn_sequence>turn)
        self.assertEqual(2,session.engine._numeric_stat(attacker.object_id,'power'))
        self.assertNotIn('first strike',session.engine._combat_keywords(session.state.cards[attacker.object_id]))
        self.private_replay(session,private)

    def test_original_windborne_charge_keeps_exact_controlled_count_and_only_surviving_incarnation(self):
        session=self.session(730511);spell=self.add(session.engine,'Windborne Charge',zone='hand')
        bodies=[self.add(session.engine,'Generic Bound Body',ref='windborne-'+str(i)) for i in range(2)]
        foreign=self.add(session.engine,'Generic Bound Body',seat='C',ref='windborne-foreign')
        response=self.add(session.engine,'Unsummon',seat='B',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        session.state.players['B'].mana_pool.update({'U':1})
        self.prepare(session,mana={'W':2,'C':2});self.checkpoint(session)
        self.assertTrue(self.binding(session,spell)['strict_capability_ready'])
        action=self.action(session,spell);before=authoritative_state_hash(session.state)
        for refs in ([bodies[0].ref],[bodies[0].ref,bodies[0].ref],[bodies[0].ref,foreign.ref]):
            bad=session.act('pilot:A',{'action_id':action['id'],'targets':refs,'pay':'auto'})
            self.assertFalse(bad.ok);self.assertEqual(before,authoritative_state_hash(session.state))
        self.cast(session,'A',spell,[card.ref for card in bodies]);self.cast(session,'B',response,[bodies[1].ref])
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual('hand',session.state.cards[bodies[1].object_id].zone)
        self.assertEqual((4,8),(session.engine._numeric_stat(bodies[0].object_id,'power'),session.engine._numeric_stat(bodies[0].object_id,'toughness')))
        self.assertIn('flying',session.engine._combat_keywords(session.state.cards[bodies[0].object_id]))
        self.assertNotIn('flying',session.engine._combat_keywords(session.state.cards[bodies[1].object_id]))
        self.assertEqual(2,session.engine._numeric_stat(foreign.object_id,'power'))
        self.private_replay(session,private)

    def test_original_invoker_activation_applies_fixed_keywords_to_both_selected_targets(self):
        session=self.session(730512);source=self.add(session.engine,"Bane's Invoker")
        target=self.add(session.engine,'Generic Bound Body',seat='B')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,mana={'C':8});self.checkpoint(session)
        self.assertTrue(self.binding(session,source)['strict_capability_ready'])
        action=next(row for row in self.actions(session) if row['id'].startswith('activate:'+source.ref+':'))
        accepted=session.act('pilot:A',{'action_id':action['id'],'targets':[source.ref,target.ref],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual((4,4),(session.engine._numeric_stat(source.object_id,'power'),session.engine._numeric_stat(source.object_id,'toughness')))
        self.assertEqual((4,8),(session.engine._numeric_stat(target.object_id,'power'),session.engine._numeric_stat(target.object_id,'toughness')))
        for card in (source,target):self.assertIn('flying',session.engine._combat_keywords(session.state.cards[card.object_id]))
        self.assertEqual(0,sum(session.state.players['A'].mana_pool.values()))
        self.private_replay(session,private)

    def test_original_kennel_master_pending_targets_and_departed_source_keep_indestructible(self):
        session=self.session(730513);source=self.add(session.engine,'Karfell Kennel-Master',zone='hand')
        bodies=[self.add(session.engine,'Generic Bound Body',seat=seat,ref='kennel-'+seat) for seat in 'AB']
        bounce=self.add(session.engine,'Unsummon',seat='B',zone='hand')
        destroy=self.add(session.engine,'Murder',seat='B',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        session.state.players['B'].mana_pool.update({'U':1,'B':2,'C':1})
        self.prepare(session,mana={'B':1,'C':4});self.checkpoint(session)
        self.cast(session,'A',source);pending=self.resolve(session);self.assertEqual('semantic.target',pending.kind)
        session=self.replay(session,load=True);before=authoritative_state_hash(session.state)
        command={'action_id':'choose','targets':[card.ref for card in bodies]}
        bad=session.act('pilot:B',command);self.assertFalse(bad.ok);self.assertEqual(before,authoritative_state_hash(session.state))
        accepted=session.act('pilot:A',command);self.assertTrue(accepted.ok,accepted.summary)
        self.cast(session,'B',bounce,[source.ref]);self.assertIsNone(self.resolve(session))
        self.assertEqual('hand',session.state.cards[source.object_id].zone)
        for card in bodies:
            self.assertEqual(3,session.engine._numeric_stat(card.object_id,'power'))
            self.assertIn('indestructible',session.engine._combat_keywords(session.state.cards[card.object_id]))
        self.cast(session,'B',destroy,[bodies[0].ref]);self.assertIsNone(self.resolve(session))
        self.assertEqual('battlefield',session.state.cards[bodies[0].object_id].zone)
        self.private_replay(session,private)

    def test_original_invigorated_rampage_modes_keep_one_large_or_two_small_modifiers(self):
        for maximum,bonus in ((1,4),(2,2)):
            session=self.session(730520+maximum);spell=self.add(session.engine,'Invigorated Rampage',zone='hand')
            bodies=[self.add(session.engine,'Generic Bound Body',seat=seat,ref='rampage-'+seat) for seat in 'AB']
            private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
            self.prepare(session,mana={'R':1,'C':1});self.checkpoint(session)
            self.assertTrue(self.binding(session,spell)['strict_capability_ready'])
            action=self.action(session,spell);schema=action['target_schema']
            mode=next(key for key,value in schema['mode_schemas'].items() if value['groups'][0]['max']==maximum)
            accepted=session.act('pilot:A',{'action_id':action['id'],'modes':[mode],
                'targets':[card.ref for card in bodies[:maximum]],'pay':'auto'})
            self.assertTrue(accepted.ok,accepted.summary);session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
            for index,card in enumerate(bodies):
                self.assertEqual(2+bonus if index<maximum else 2,session.engine._numeric_stat(card.object_id,'power'))
                self.assertEqual(index<maximum,'trample' in session.engine._combat_keywords(session.state.cards[card.object_id]))
            self.private_replay(session,private)

    def test_original_burning_sun_convoke_pays_with_own_target_and_grants_haste_to_each(self):
        session=self.session(730523);spell=self.add(session.engine,"Burning Sun's Fury",zone='hand')
        own=self.add(session.engine,'Generic Bound Body')
        foreign=self.add(session.engine,'Generic Bound Body',seat='B',ref='foreign-convoke-target')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,mana={'R':1});self.checkpoint(session)
        self.assertTrue(self.binding(session,spell)['strict_capability_ready'])
        action=self.action(session,spell);command={'action_id':action['id'],'targets':[own.ref,foreign.ref],'pay':'auto'}
        before=authoritative_state_hash(session.state)
        bad=session.act('pilot:A',{**command,'convoke_cards':[foreign.ref]})
        self.assertFalse(bad.ok);self.assertEqual(before,authoritative_state_hash(session.state))
        accepted=session.act('pilot:A',{**command,'convoke_cards':[own.ref]});self.assertTrue(accepted.ok,accepted.summary)
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertTrue(session.state.cards[own.object_id].tapped)
        for card in (own,foreign):
            self.assertEqual(4,session.engine._numeric_stat(card.object_id,'power'))
            self.assertIn('haste',session.engine._combat_keywords(session.state.cards[card.object_id]))
        self.private_replay(session,private)

    def test_original_cutthroat_grants_real_lifelink_to_each_creatures_own_controller(self):
        session=self.session(730524);spell=self.add(session.engine,'Cutthroat Maneuver',zone='hand')
        fight=self.add(session.engine,'Prey Upon',zone='hand')
        self.add(session.engine,'Forest',ref='fight-green-source')
        bodies=[self.add(session.engine,'Generic Bound Body',seat=seat,ref='cutthroat-'+seat) for seat in 'AB']
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,mana={'B':1,'G':1,'C':3});self.checkpoint(session)
        self.assertTrue(self.binding(session,spell)['strict_capability_ready'])
        self.cast(session,'A',spell,[card.ref for card in bodies]);self.assertIsNone(self.resolve(session))
        self.cast(session,'A',fight,[card.ref for card in bodies]);session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        for card in bodies:
            self.assertEqual((3,7),(session.engine._numeric_stat(card.object_id,'power'),session.engine._numeric_stat(card.object_id,'toughness')))
            self.assertEqual(3,session.state.cards[card.object_id].marked_damage)
        self.assertEqual([43,43,40,40],[session.state.players[seat].life for seat in 'ABCD'])
        self.private_replay(session,private)

    def test_all_ten_originals_are_fully_playable_and_cannot_omit_keyword_or_set_authority(self):
        from quorune.preflight import card_semantic_status
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.keyword_abilities import FIXED_CHARACTERISTIC_KEYWORD_CAPABILITIES
        names=('Cutthroat Maneuver','Karfell Kennel-Master','Press the Advantage','Quickbeam, Upstart Ent',
            "Burning Sun's Fury",'Windborne Charge','Coordinated Assault','Invigorated Rampage',"Bane's Invoker",'Abandon Reason')
        session=self.session(730525)
        for name in names:
            source=self.add(session.engine,name,zone='hand');self.assertTrue(self.binding(session,source)['strict_capability_ready'],name)
            status=card_semantic_status(self.db.lookup(name),session.engine.semantics,db=self.db,
                capability_registry=self.registry,capability_profile='commander_review')
            self.assertEqual('fully_playable',status['status'],(name,status['unresolved']))
        source=self.db.lookup('Coordinated Assault')
        program=next(p for p in session.engine.semantics.programs_for_oracle(source.oracle_id) if any(e.get('schema_version')==6 for e in p.effects))
        for cap in ('continuous.resolution.fixed_target_characteristic_set',*FIXED_CHARACTERISTIC_KEYWORD_CAPABILITIES['First Strike']):
            deps=tuple(value for value in program.capability_dependencies if value!=cap)
            missing=replace(program,capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
            bound=bind_semantic_program_runtime(missing,capability_registry=self.registry,profile='commander_review')
            self.assertFalse(bound['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+cap,bound['blockers'])

    def test_original_rampage_modal_set_keeps_only_legal_original_targets_after_response(self):
        session=self.session(730526);spell=self.add(session.engine,'Invigorated Rampage',zone='hand')
        bodies=[self.add(session.engine,'Generic Bound Body',seat=seat,ref='modal-partial-'+seat) for seat in 'AB']
        response=self.add(session.engine,'Unsummon',seat='B',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        session.state.players['B'].mana_pool.update({'U':1})
        self.prepare(session,mana={'R':1,'C':1});self.checkpoint(session)
        action=self.action(session,spell)
        mode=next(key for key,value in action['target_schema']['mode_schemas'].items() if value['groups'][0]['max']==2)
        accepted=session.act('pilot:A',{'action_id':action['id'],'modes':[mode],
            'targets':[card.ref for card in bodies],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary)
        self.cast(session,'B',response,[bodies[1].ref]);session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual('hand',session.state.cards[bodies[1].object_id].zone)
        self.assertEqual(4,session.engine._numeric_stat(bodies[0].object_id,'power'))
        self.assertIn('trample',session.engine._combat_keywords(session.state.cards[bodies[0].object_id]))
        self.assertNotIn('trample',session.engine._combat_keywords(session.state.cards[bodies[1].object_id]))
        self.private_replay(session,private)

    def test_fresh_original_coordinated_assault_kills_canonical_first_strike_adoption_omission(self):
        method='test_original_coordinated_assault_accepts_zero_one_or_two_distinct_targets_and_applies_both_results'
        with patch('quorune.rules.target_characteristic_sets.keyword_counter_mechanic',return_value=None):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()

    def test_original_quickbeam_self_and_other_treefolk_entries_keep_each_selected_set(self):
        session=self.session(730530);source=self.add(session.engine,'Quickbeam, Upstart Ent',zone='hand')
        other=self.add(session.engine,'Redwood Treefolk',zone='hand')
        bodies=[self.add(session.engine,'Generic Bound Body',seat=seat,ref='quickbeam-'+seat) for seat in 'BC']
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,mana={'G':10,'C':10});self.checkpoint(session)
        self.assertTrue(self.binding(session,source)['strict_capability_ready'])
        self.cast(session,'A',source);pending=self.resolve(session);self.assertEqual('semantic.target',pending.kind)
        session=self.replay(session,load=True)
        accepted=session.act('pilot:A',{'action_id':'choose','targets':[source.ref,bodies[0].ref]})
        self.assertTrue(accepted.ok,accepted.summary);self.assertIsNone(self.resolve(session))
        self.assertEqual(7,session.engine._numeric_stat(source.object_id,'power'))
        self.cast(session,'A',other);pending=self.resolve(session);self.assertEqual('semantic.target',pending.kind)
        accepted=session.act('pilot:A',{'action_id':'choose','targets':[source.ref,bodies[1].ref]})
        self.assertTrue(accepted.ok,accepted.summary);self.assertIsNone(self.resolve(session))
        self.assertEqual((9,10),(session.engine._numeric_stat(source.object_id,'power'),session.engine._numeric_stat(source.object_id,'toughness')))
        for card in bodies:
            self.assertEqual((4,8),(session.engine._numeric_stat(card.object_id,'power'),session.engine._numeric_stat(card.object_id,'toughness')))
            self.assertIn('trample',session.engine._combat_keywords(session.state.cards[card.object_id]))
        self.assertEqual(3,session.engine._numeric_stat(other.object_id,'power'))
        self.private_replay(session,private)

    def test_original_abandon_reason_discards_through_actual_casting_cost_then_casts_for_madness(self):
        session=self.session(730531);spell=self.add(session.engine,'Abandon Reason',zone='hand')
        outlet=self.add(session.engine,'Tormenting Voice',zone='hand')
        bodies=[self.add(session.engine,'Generic Bound Body',seat=seat,ref='madness-target-'+seat) for seat in 'AB']
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,mana={'R':4,'C':4});self.checkpoint(session)
        for card in (spell,outlet):self.assertTrue(self.binding(session,card)['strict_capability_ready'])
        hand_before=len(session.state.players['A'].zones['hand'])
        action=self.action(session,outlet)
        activated=session.act('pilot:A',{'action_id':action['id'],'discard_cards':[spell.ref],'pay':'auto'})
        self.assertTrue(activated.ok,activated.summary);self.assertEqual('exile',session.state.cards[spell.object_id].zone)
        pending=self.resolve(session);self.assertEqual('semantic.choice',pending.kind)
        packet=session.packet('pilot:A',full=True)['decision']['ctx']
        self.assertTrue(any(option['id']=='madness' for option in packet['cast_options']))
        session=self.replay(session,load=True);before=authoritative_state_hash(session.state)
        command={'action_id':'choose','choice':'cast','targets':[card.ref for card in bodies],'pay':'auto'}
        bad=session.act('pilot:B',command);self.assertFalse(bad.ok);self.assertEqual(before,authoritative_state_hash(session.state))
        accepted=session.act('pilot:A',command);self.assertTrue(accepted.ok,accepted.summary);self.assertIsNone(self.resolve(session))
        self.assertEqual('graveyard',session.state.cards[spell.object_id].zone)
        self.assertEqual('graveyard',session.state.cards[outlet.object_id].zone)
        self.assertEqual(hand_before,len(session.state.players['A'].zones['hand']))
        for card in bodies:
            self.assertEqual(3,session.engine._numeric_stat(card.object_id,'power'))
            self.assertIn('first strike',session.engine._combat_keywords(session.state.cards[card.object_id]))
        self.private_replay(session,private)

    def test_fresh_original_rampage_kills_restored_implicit_mode_target_omission(self):
        from quorune.targets import _mode_target_frames
        def legacy_frames(*args,**kwargs):
            return tuple((definition,0,None) if definition.get('groups') is None else (definition,count,reference)
                for definition,count,reference in _mode_target_frames(*args,**kwargs))
        method='test_original_invigorated_rampage_modes_keep_one_large_or_two_small_modifiers'
        with patch('quorune.targets._mode_target_frames',legacy_frames):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()

    def test_actual_rampage_copy_preserves_mode_and_retargets_only_its_two_creature_group(self):
        session=self.session(730540);spell=self.add(session.engine,'Invigorated Rampage',zone='hand')
        copy=self.add(session.engine,'Reverberate',seat='B',zone='hand')
        bodies=[self.add(session.engine,'Generic Bound Body',seat=seat,ref='copied-rampage-'+seat) for seat in 'ABCD']
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        session.state.players['B'].mana_pool.update({'R':2})
        self.prepare(session,mana={'R':1,'C':1});self.checkpoint(session)
        action=self.action(session,spell)
        mode=next(key for key,value in action['target_schema']['mode_schemas'].items() if value['groups'][0]['max']==2)
        accepted=session.act('pilot:A',{'action_id':action['id'],'modes':[mode],
            'targets':[card.ref for card in bodies[:2]],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary)
        original=next(item for item in session.state.stack if item.card_object_id==spell.object_id)
        self.cast(session,'B',copy,[original.ref]);pending=self.resolve(session)
        self.assertEqual('semantic.choice',pending.kind);session=self.replay(session,load=True)
        before=authoritative_state_hash(session.state);command={'action_id':'choose','targets':[card.ref for card in bodies[2:]]}
        bad=session.act('pilot:A',command);self.assertFalse(bad.ok);self.assertEqual(before,authoritative_state_hash(session.state))
        accepted=session.act('pilot:B',command);self.assertTrue(accepted.ok,accepted.summary);self.assertIsNone(self.resolve(session))
        for card in bodies:
            self.assertEqual(4,session.engine._numeric_stat(card.object_id,'power'))
            self.assertIn('trample',session.engine._combat_keywords(session.state.cards[card.object_id]))
        self.assertEqual(2,len(session.engine._current_turn_history('spell_cast')))
        self.private_replay(session,private)
