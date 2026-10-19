"""Independent CR 107.3a/k and 602.2b: each activation chooses and pays its own X."""
from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.abilities import ActivatedAbility, parse_activated_abilities, reduced_requirements
from quorune.activation_x import ANNOUNCED_ACTIVATION_X_CAPABILITY, chosen_activation_x
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.compiler.activation_x_costs import announced_x_activation_cost
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.semantic_runtime.activated_abilities import ActivatedAbilityCatalogHandler, activated_ability_catalog_descriptor
from scripts.build_test_database import build_fixture_database
from test_granted_skulk import record
import test_scalar_mill_wording as controls


class AnnouncedActivationXCompilerTests(unittest.TestCase):
    def test_cost_roundtrip_counts_every_symbol_and_keeps_legacy_absence(self):
        raw,=parse_activated_abilities(card_name='Generic Source',oracle_text='{X}{X}{R}, {T}: You gain X life.',keywords=())
        ability=announced_x_activation_cost(raw)
        self.assertTrue(ability.compiled_cost)
        self.assertEqual(2,ability.announced_x_symbols)
        self.assertEqual(4,reduced_requirements(ability,announced_x=2)['GENERIC'])
        self.assertEqual(1,reduced_requirements(ability,announced_x=0)['R'])
        self.assertEqual(ability,ActivatedAbility.from_dict(ability.to_dict()))
        legacy,=parse_activated_abilities(card_name='Generic Source',oracle_text='{2}, {T}: You gain 2 life.',keywords=())
        self.assertNotIn('announced_x_symbols',legacy.to_dict())
        for value in (None,True,-1,1.5,'2'):
            with self.assertRaises(ValueError):chosen_activation_x(ability,{'x':value})
        for changes in ({'announced_x_symbols':True},{'announced_x_symbols':1},
                {'mana':{'GENERIC':0,'R':0}}, {'tap_source':False}):
            with self.assertRaises(ValueError):ActivatedAbility.from_dict({**ability.to_dict(),**changes})
        missing=ability.to_dict();missing.pop('announced_x_symbols')
        with self.assertRaises(ValueError):ActivatedAbility.from_dict(missing)
        self.assertIsNone(chosen_activation_x(legacy,{'x':2}))
        reduced=replace(ability,generic_reduction_per_legendary_creature=1)
        self.assertEqual(2,reduced_requirements(reduced,announced_x=2,legendary_creatures=2)['GENERIC'])
        descriptor=activated_ability_catalog_descriptor(ability)
        self.assertEqual(ability,ActivatedAbilityCatalogHandler().validate(descriptor))
        descriptor.pop('requires_complete_card_program')
        with self.assertRaises(ValueError):ActivatedAbilityCatalogHandler().validate(descriptor)

    def test_defined_restricted_and_variable_target_domains_remain_residual(self):
        registry=load_default_capability_registry()
        for text in ('{X}, {T}: Draw a card. X is the number of cards in an opponent\'s hand.',
                '{X}: This creature deals X damage to each creature and each player. Spend only black mana on X.',
                '{X}{G}: This creature gets +X/+X until end of turn. X can\'t be 0.',
                '{X}, {T}: Put a +1/+1 counter on target creature with power X.',
                '{X}: Tap X target creatures and X target lands.'):
            with self.subTest(text=text):
                ir=compile_oracle_card(record(text,'Creature'),capability_registry=registry,capability_profile='commander_review')
                self.assertNotEqual('exact',ir.status)
                self.assertTrue(ir.material_residuals)

    def test_withdrawn_activation_x_authority_keeps_complete_body_residual(self):
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        next(row for row in raw['capabilities'] if row['id']==ANNOUNCED_ACTIVATION_X_CAPABILITY).update(status='blocked',blockers=['withdrawn ordinary activation X'])
        ir=compile_oracle_card(record('{X}, {T}: You gain X life.','Artifact'),
            capability_registry=CapabilityRegistry(raw),capability_profile='commander_review')
        self.assertNotEqual('exact',ir.status);self.assertTrue(ir.material_residuals)

    def test_announced_cost_x_keeps_fixed_results_and_invariant_target_domain(self):
        for text in ('{X}, {T}: You gain 1 life.',
                '{X}: Target creature gets +X/+X until end of turn.'):
            ir=compile_oracle_card(record(text,'Artifact'),capability_registry=load_default_capability_registry(),
                capability_profile='commander_review')
            self.assertEqual('exact',ir.status,ir.material_residuals)
            capabilities={c for face in ir.faces for node in face.nodes for c in node.capability_dependencies}
            self.assertIn(ANNOUNCED_ACTIVATION_X_CAPABILITY,capabilities)


class AnnouncedActivationXRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=controls.ScalarMillWordingRuntimeTests.session
    add=controls.ScalarMillWordingRuntimeTests.add
    prepare=controls.ScalarMillWordingRuntimeTests.prepare
    cast=controls.ScalarMillWordingRuntimeTests.cast
    checkpoint=controls.ScalarMillWordingRuntimeTests.checkpoint
    resolve=controls.ScalarMillWordingRuntimeTests.resolve
    replay=controls.ScalarMillWordingRuntimeTests.replay
    until=controls.ScalarMillWordingRuntimeTests.until
    binding=controls.ScalarMillWordingRuntimeTests.binding

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'activation-X.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/announced-x-activation-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Activation X',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',20)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close();cls.temp.cleanup()

    def action(self,session,source):
        return next(row for row in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
            if row['id'].startswith('activate:'+source.ref+':') and row.get('choice_schema',{}).get('x'))

    def private_replay(self,session,private):
        for viewer in 'ABD':self.assertNotIn(private.ref,json.dumps(session.packet('pilot:'+viewer,full=True)))
        self.assertIn(private.ref,json.dumps(session.packet('pilot:C',full=True)))
        return self.replay(session,load=True)

    def test_unknown_body_and_partial_sibling_cannot_expose_paid_x_activation(self):
        for index,name in enumerate(('Generic Unknown X','Generic Partial X','Ent-Draught Basin','Jiwari, the Earth Aflame')):
            with self.subTest(name=name):
                session=self.session(71201100+index);source=self.add(session.engine,name)
                source.acquired_control_turn_count=-1
                self.prepare(session,mana={'C':12,'G':4,'R':4});self.checkpoint(session)
                self.assertFalse(self.binding(session,source)['strict_capability_ready'])
                actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
                self.assertFalse(any(row['id'].startswith('activate:'+source.ref+':') and row.get('choice_schema',{}).get('x') for row in actions))
                before=authoritative_state_hash(session.state)
                result=session.act('pilot:A',{'action_id':'activate:'+source.ref+':ab1','x':2,'pay':'auto'})
                self.assertFalse(result.ok,result.summary);self.assertEqual(before,authoritative_state_hash(session.state))

    def test_omitted_cost_capability_cannot_bind_executable_catalog(self):
        session=self.session(71201110);source=self.add(session.engine,"Belbe's Armor")
        programs=session.engine.semantics.programs_for_oracle(source.oracle_id,event='activate')
        program=next(value for value in programs if value.effects)
        self.assertTrue(bind_semantic_program_runtime(program,capability_registry=self.registry,profile='commander_review')['strict'])
        dependencies=tuple(value for value in program.capability_dependencies if value!=ANNOUNCED_ACTIVATION_X_CAPABILITY)
        missing=replace(program,capability_dependencies=dependencies,
            capability_closure=self.registry.closure(dependencies,profile='commander_review').to_dict())
        bound=bind_semantic_program_runtime(missing,capability_registry=self.registry,profile='commander_review')
        self.assertFalse(bound['strict'])
        self.assertIn('capability:undeclared_runtime_dependency:'+ANNOUNCED_ACTIVATION_X_CAPABILITY,bound['blockers'])

    def test_original_panacea_double_x_shield_prevents_only_the_chosen_amount(self):
        session=self.session(71201120);source=self.add(session.engine,'Panacea')
        target=self.add(session.engine,'Grizzly Bears',seat='B')
        bolt=self.add(session.engine,'Lightning Bolt',seat='B',zone='hand')
        shock=self.add(session.engine,'Shock',seat='B',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        session.state.players['B'].mana_pool['R']=2
        self.prepare(session,mana={'C':5});self.checkpoint(session)
        self.assertTrue(self.binding(session,source)['strict_capability_ready'])
        action=self.action(session,source)
        accepted=session.act('pilot:A',{'action_id':action['id'],'targets':[target.ref],'x':2,'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.assertEqual(1,session.state.players['A'].mana_pool['C'])
        self.assertTrue(session.state.cards[source.object_id].tapped)
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.cast(session,'B',bolt,[target.ref]);self.assertIsNone(self.resolve(session))
        self.assertEqual('battlefield',session.state.cards[target.object_id].zone)
        self.assertEqual(1,session.state.cards[target.object_id].marked_damage)
        self.cast(session,'B',shock,[target.ref]);self.assertIsNone(self.resolve(session))
        self.assertEqual('graveyard',session.state.cards[target.object_id].zone)
        self.private_replay(session,private)

    def test_original_drana_uses_one_chosen_x_for_target_and_source_results(self):
        session=self.session(71201121);source=self.add(session.engine,'Drana, Kalastria Bloodchief')
        target=self.add(session.engine,'Grizzly Bears',seat='B')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,mana={'C':3,'B':2});self.checkpoint(session)
        self.assertTrue(self.binding(session,source)['strict_capability_ready'])
        action=self.action(session,source)
        accepted=session.act('pilot:A',{'action_id':action['id'],'targets':[target.ref],'x':2,'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual((1,0),tuple(session.state.players['A'].mana_pool[color] for color in ('C','B')))
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual('graveyard',session.state.cards[target.object_id].zone)
        self.assertEqual((6,4),tuple(session.engine._numeric_stat(source.object_id,stat) for stat in ('power','toughness')))
        self.private_replay(session,private)

    def test_original_sands_mills_exact_x_for_only_the_chosen_player(self):
        session=self.session(71201122);source=self.add(session.engine,'Sands of Delirium')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,mana={'C':3});self.checkpoint(session)
        libraries={seat:tuple(player.zones['library']) for seat,player in session.state.players.items()}
        self.assertTrue(self.binding(session,source)['strict_capability_ready'])
        action=self.action(session,source)
        accepted=session.act('pilot:A',{'action_id':action['id'],'targets':['B'],'x':2,'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary)
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual(1,session.state.players['A'].mana_pool['C'])
        for seat in 'ABCD':self.assertEqual(2 if seat=='B' else 0,len(libraries[seat])-len(session.state.players[seat].zones['library']))
        self.private_replay(session,private)

    def test_original_merfolk_draws_x_but_adds_one_counter_and_exhaust_stays_used(self):
        session=self.session(71201123);source=self.add(session.engine,'Mindspring Merfolk')
        merfolk=self.add(session.engine,'Merfolk of the Pearl Trident')
        other=self.add(session.engine,'Grizzly Bears')
        enemy=self.add(session.engine,'Merfolk of the Pearl Trident',seat='B',ref='enemy-merfolk')
        untap=self.add(session.engine,'Burst of Energy',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        source.acquired_control_turn_count=-1
        self.prepare(session,mana={'C':3,'U':2,'W':1});self.checkpoint(session)
        hand=set(session.state.players['A'].zones['hand'])
        self.assertTrue(self.binding(session,source)['strict_capability_ready'])
        action=self.action(session,source)
        accepted=session.act('pilot:A',{'action_id':action['id'],'x':2,'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary)
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        current=set(session.state.players['A'].zones['hand'])
        self.assertEqual(len(hand)+2,len(current))
        for oid in current-hand:
            ref=session.state.cards[oid].ref
            self.assertIn(ref,json.dumps(session.packet('pilot:A',full=True)))
            for viewer in 'BCD':self.assertNotIn(ref,json.dumps(session.packet('pilot:'+viewer,full=True)))
        for card in (source,merfolk):self.assertEqual(1,session.state.cards[card.object_id].counters.get('+1/+1',0))
        for card in (other,enemy):self.assertEqual(0,session.state.cards[card.object_id].counters.get('+1/+1',0))
        self.cast(session,'A',untap,[source.ref]);self.assertIsNone(self.resolve(session))
        self.assertFalse(session.state.cards[source.object_id].tapped)
        actions=session.packet(session.pending_principals()[0],full=True)['decision']['ctx']['legal']['actions']
        self.assertFalse(any(row['id']==action['id'] for row in actions))
        before=authoritative_state_hash(session.state)
        result=session.act('pilot:A',{'action_id':action['id'],'x':0,'pay':'auto'})
        self.assertFalse(result.ok,result.summary);self.assertEqual(before,authoritative_state_hash(session.state))
        self.private_replay(session,private)

    def test_original_ballista_uses_current_combat_target_domain(self):
        session=self.session(71201125);source=self.add(session.engine,'Ballista Squad')
        target=self.add(session.engine,'Grizzly Bears')
        lands=[self.add(session.engine,'Generic Bound Plains',ref='ballista-plains-'+str(i)) for i in range(3)]
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        source.acquired_control_turn_count=-1;target.acquired_control_turn_count=-1
        self.prepare(session);self.checkpoint(session)
        self.assertTrue(self.binding(session,source)['strict_capability_ready'])
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        self.assertFalse(any(row['id'].startswith('activate:'+source.ref+':') and row.get('choice_schema',{}).get('x') for row in actions))
        self.until(session,lambda s:s.state.pending_decision.kind=='combat.attackers')
        attacked=session.act('pilot:A',{'action_id':'attack','attackers':{target.ref:'B'}})
        self.assertTrue(attacked.ok,attacked.summary)
        self.until(session,lambda s:s.state.pending_decision.kind=='priority' and s.pending_principals()==['pilot:A'])
        action=self.action(session,source)
        result=session.act('pilot:A',{'action_id':action['id'],'targets':[target.ref],'x':2,'pay':'auto'})
        self.assertTrue(result.ok,result.summary)
        self.assertTrue(all(session.state.cards[land.object_id].tapped for land in lands))
        self.assertTrue(session.state.cards[source.object_id].tapped)
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual('graveyard',session.state.cards[target.object_id].zone)
        self.assertEqual(40,session.state.players['B'].life)
        self.private_replay(session,private)

    def test_announced_x_does_not_retarget_a_blinked_incarnation_or_refund_price(self):
        session=self.session(71201126);source=self.add(session.engine,"Belbe's Armor")
        target=self.add(session.engine,'Grizzly Bears')
        blink=self.add(session.engine,'Cloudshift',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,mana={'C':3,'W':1});self.checkpoint(session)
        action=self.action(session,source)
        accepted=session.act('pilot:A',{'action_id':action['id'],'targets':[target.ref],'x':2,'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);old=target.logical_object_id
        self.cast(session,'A',blink,[target.ref])
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        current=session.state.cards[target.object_id]
        self.assertEqual('battlefield',current.zone);self.assertNotEqual(old,current.logical_object_id)
        self.assertEqual((2,2),tuple(session.engine._numeric_stat(target.object_id,stat) for stat in ('power','toughness')))
        self.assertEqual(1,session.state.players['A'].mana_pool['C'])
        self.assertTrue(session.state.cards[source.object_id].tapped)
        self.private_replay(session,private)

    def test_original_whetwheel_hides_x_face_down_then_uses_artifact_tap_price(self):
        session=self.session(71201124);source=self.add(session.engine,'Whetwheel',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,mana={'C':12});self.checkpoint(session)
        self.assertTrue(self.binding(session,source)['strict_capability_ready'])
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        morph=next(row for row in actions if row['id']=='cast-morph:'+source.ref)
        accepted=session.act('pilot:A',{'action_id':morph['id'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
        self.assertIsNone(self.resolve(session))
        self.assertTrue(session.state.cards[source.object_id].face_down)
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        self.assertFalse(any(row['id'].startswith('activate:'+source.ref+':') and row.get('choice_schema',{}).get('x') for row in actions))
        turn=next(row for row in actions if row['id']=='turn-face-up:'+source.ref)
        result=session.act('pilot:A',{'action_id':turn['id'],'pay':'auto'});self.assertTrue(result.ok,result.summary)
        self.assertFalse(session.state.cards[source.object_id].face_down)
        self.assertEqual('Artifact',session.engine._effective_card_data(session.state.cards[source.object_id])['type_line'])
        before=len(session.state.players['B'].zones['library'])
        action=self.action(session,source)
        result=session.act('pilot:A',{'action_id':action['id'],'targets':['B'],'x':2,'pay':'auto'})
        self.assertTrue(result.ok,result.summary)
        self.assertEqual(2,session.state.players['A'].mana_pool['C'])
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual(before-2,len(session.state.players['B'].zones['library']))
        self.private_replay(session,private)

    def test_original_armor_prices_chosen_x_then_keeps_it_after_source_destruction(self):
        session=self.session(71201001)
        source=self.add(session.engine,"Belbe's Armor")
        target=self.add(session.engine,'Grizzly Bears',seat='B')
        removal=self.add(session.engine,'Disenchant',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,mana={'C':5,'W':2});self.checkpoint(session)
        self.assertTrue(self.binding(session,source)['strict_capability_ready'])
        action=self.action(session,source);self.assertEqual(0,action['choice_schema']['x']['minimum'])
        command={'action_id':action['id'],'targets':[target.ref],'x':2,'pay':'auto'}
        before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:D',command).ok);self.assertEqual(before,authoritative_state_hash(session.state))
        for bad in (None,-1,True,1.5,'2',100):
            with self.subTest(x=bad):
                self.assertFalse(session.act('pilot:A',{**command,'x':bad}).ok)
                self.assertEqual(before,authoritative_state_hash(session.state))
        missing={k:v for k,v in command.items() if k!='x'}
        self.assertFalse(session.act('pilot:A',missing).ok)
        self.assertEqual(before,authoritative_state_hash(session.state))
        accepted=session.act('pilot:A',command);self.assertTrue(accepted.ok,accepted.summary)
        self.assertTrue(session.state.cards[source.object_id].tapped)
        self.assertEqual(3,session.state.players['A'].mana_pool['C'])
        self.assertEqual(2,session.state.stack[-1].x_value)
        session=self.replay(session,load=True)
        self.cast(session,'A',removal,[source.ref])
        self.assertIsNone(self.resolve(session))
        self.assertEqual('graveyard',session.state.cards[source.object_id].zone)
        self.assertEqual((0,4),tuple(session.engine._numeric_stat(target.object_id,x) for x in ('power','toughness')))
        self.private_replay(session,private)

    def test_two_original_spider_activations_keep_separate_x_and_colored_payment(self):
        session=self.session(71201002)
        source=self.add(session.engine,'Silklash Spider');source.acquired_control_turn_count=-1
        flying=self.add(session.engine,'Wind Drake',seat='B')
        ground=self.add(session.engine,'Grizzly Bears',seat='C')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,mana={'C':5,'G':4});self.checkpoint(session)
        self.assertTrue(self.binding(session,source)['strict_capability_ready'])
        action=self.action(session,source)
        for x in (1,3):
            result=session.act('pilot:A',{'action_id':action['id'],'x':x,'pay':'auto'});self.assertTrue(result.ok,result.summary)
            session.engine.pump()
        self.assertEqual([1,3],[item.x_value for item in session.state.stack])
        self.assertEqual(1,session.state.players['A'].mana_pool['C'])
        self.assertEqual(0,session.state.players['A'].mana_pool['G'])
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual('graveyard',session.state.cards[flying.object_id].zone)
        self.assertEqual('battlefield',session.state.cards[ground.object_id].zone)
        self.assertEqual(0,session.state.cards[ground.object_id].marked_damage)
        self.private_replay(session,private)

    def test_double_x_price_and_zero_are_independent_paid_activations(self):
        session=self.session(71201003)
        source=self.add(session.engine,'Generic Double X Life')
        zero=self.add(session.engine,'Oracle of Nectars');zero.acquired_control_turn_count=-1
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,mana={'C':5});self.checkpoint(session)
        self.assertTrue(self.binding(session,source)['strict_capability_ready'])
        action=self.action(session,source);result=session.act('pilot:A',{'action_id':action['id'],'x':2,'pay':'auto'});self.assertTrue(result.ok,result.summary)
        self.assertEqual(1,session.state.players['A'].mana_pool['C'])
        action=self.action(session,zero);result=session.act('pilot:A',{'action_id':action['id'],'x':0,'pay':'auto'});self.assertTrue(result.ok,result.summary)
        self.assertTrue(session.state.cards[zero.object_id].tapped)
        self.assertEqual([2,0],[item.x_value for item in session.state.stack])
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual(42,session.state.players['A'].life)
        self.private_replay(session,private)

    def test_activation_x_payment_and_retention_omissions_are_killed(self):
        method='test_original_armor_prices_chosen_x_then_keeps_it_after_source_destruction'
        with patch('quorune.rules.activation.x_costs.activation_requirements',return_value=({'GENERIC':0},2)):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()
        from quorune.rules.activation import commit
        original=commit._activation_stack_item
        def omit(*args,**kwargs):
            item=original(*args,**kwargs);item.x_value=None;return item
        with patch.object(commit,'_activation_stack_item',side_effect=omit):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()

    def test_original_wolf_run_keeps_colored_zero_price_and_cannot_pay_its_own_tap(self):
        session=self.session(71201004)
        source=self.add(session.engine,'Kessig Wolf Run')
        target=self.add(session.engine,'Grizzly Bears',seat='B')
        forest=self.add(session.engine,'Forest',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,mana={'R':1});self.checkpoint(session)
        self.assertTrue(self.binding(session,source)['strict_capability_ready'])
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        self.assertFalse(any(row['id'].startswith('activate:'+source.ref+':') and row.get('choice_schema',{}).get('x') for row in actions))
        before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:A',{'action_id':'activate:'+source.ref+':ab2','targets':[target.ref],'x':0,'pay':'auto'}).ok)
        self.assertEqual(before,authoritative_state_hash(session.state))
        played=session.act('pilot:A',{'action_id':'play-land:'+forest.ref});self.assertTrue(played.ok,played.summary)
        action=self.action(session,source)
        self.assertEqual(1,action['cost_summary']['announced_x_symbols'])
        before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:A',{'action_id':action['id'],'targets':[target.ref],'x':1,'pay':'auto'}).ok)
        self.assertEqual(before,authoritative_state_hash(session.state))
        accepted=session.act('pilot:A',{'action_id':action['id'],'targets':[target.ref],'x':0,'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
        self.assertTrue(session.state.cards[source.object_id].tapped)
        self.assertTrue(session.state.cards[forest.object_id].tapped)
        self.assertEqual(0,session.state.players['A'].mana_pool['R'])
        self.assertEqual(0,session.state.stack[-1].x_value)
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual((2,2),tuple(session.engine._numeric_stat(target.object_id,x) for x in ('power','toughness')))
        self.assertIn('trample',session.engine._combat_keywords(session.state.cards[target.object_id]))
        self.private_replay(session,private)
