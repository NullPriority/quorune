from __future__ import annotations

"""CR 611.2c/613.1f: resolved removal locks incarnations, not a live set."""

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.compiler.temporary_keyword_removal_templates import temporary_keyword_removal_template
from quorune.continuous_effect_state import expire_end_of_turn_continuous_effects
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry,load_default_capability_registry
from quorune.rules.temporary_keyword_removal import CAPABILITY,temporary_keyword_removal_instruction
from scripts.build_test_database import build_fixture_database
from test_bound_effect_programs import record
import test_compound_damage_recipients as controls


class TemporaryKeywordRemovalCompilerTests(unittest.TestCase):
    def test_closed_fixed_removal_modifier_and_grants_keep_selection_and_legacy_boundaries(self):
        for text in ('Target creature loses flying until end of turn.',
                'Until end of turn, this creature loses defender and gains flying.',
                'This creature gains flying and loses trample until end of turn.',
                'Attacking creatures get -2/-2 and lose flying until end of turn.'):
            result=temporary_keyword_removal_template(text,source_name='Generic Witness',source_is_permanent=True,source_card_types=('creature',))
            self.assertIsNotNone(result,text);self.assertEqual(11,result[1][0]['schema_version'])
            temporary_keyword_removal_instruction(result[1][0])
        for text in ('Target creature loses your choice of flying or trample until end of turn.',
                'Target creature loses flying until your next turn.',
                'Target creature loses flying and cannot gain flying until end of turn.',
                'Target creature loses prowess until end of turn.',
                'Target creature loses an unrepresented keyword until end of turn.'):
            self.assertIsNone(temporary_keyword_removal_template(text,source_name='Generic Witness',source_is_permanent=True,source_card_types=('creature',)),text)
        result=temporary_keyword_removal_template('Target creature loses flying until end of turn.',source_name='Generic Witness',source_is_permanent=False,source_card_types=())
        for changed in ({**result[1][0],'schema_version':True},{**result[1][0],'remove_keywords':[]},
                {**result[1][0],'remove_keywords':[{}]},
                {**result[1][0],'power':1.5},{**result[1][0],'extra':True}):
            with self.assertRaises(ValueError):temporary_keyword_removal_instruction(changed)

    def test_source_and_target_owner_withdrawals_keep_exact_removal_residual(self):
        text='Target creature loses flying until end of turn.'
        registry=load_default_capability_registry()
        self.assertEqual('exact',compile_oracle_card(record(text),capability_registry=registry,capability_profile='commander_review').status)
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        for cap in (CAPABILITY,'continuous.resolution.fixed_characteristics_until_end_of_turn','target.revalidate_resolution','combat.block.flying'):
            changed=deepcopy(raw);next(row for row in changed['capabilities'] if row['id']==cap).update(status='blocked',blockers=['withdrawn'])
            self.assertNotEqual('exact',compile_oracle_card(record(text),capability_registry=CapabilityRegistry(changed),capability_profile='commander_review').status,cap)


class TemporaryKeywordRemovalRuntimeTests(unittest.TestCase):
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

    def advance(self,session,predicate):
        for _ in range(100):
            session.engine.pump()
            if predicate(session):return
            decision=session.state.pending_decision;self.assertIsNotNone(decision)
            principal=session.pending_principals()[0]
            if decision.kind=='priority':command={'action_id':'pass'}
            elif decision.kind=='combat.attackers':command={'action_id':'attack','attacks':{}}
            elif decision.kind=='combat.blockers':command={'action_id':'block','blocks':{}}
            elif decision.kind=='trigger.order':command={'action_id':'order','triggers':[item.ref for item in session.state.pending_trigger_batches[0].items]}
            elif decision.kind=='cleanup.discard':
                ctx=session.packet(principal,full=True)['decision']['ctx']
                command={'a':'discard','cards':[row['id'] for row in ctx['hand'][:ctx['count']]]}
            else:self.fail('Unexpected temporary-removal journey decision '+decision.kind)
            result=session.act(principal,command);self.assertTrue(result.ok,result.summary)
        self.fail('Temporary-removal journey did not reach its expected state')

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'temporary-removal.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/temporary-keyword-removal-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Temporary keyword removal',[DeckEntry('Generic Bound Commander',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()

    def activation(self,session,source):
        for _ in range(16):
            if session.state.priority_player=='A':break
            self.assertTrue(session.act(session.pending_principals()[0],{'action_id':'pass'}).ok)
        return next(row for row in self.actions(session) if row['id'].startswith('activate:'+source.ref+':') and not row.get('mana_ability'))

    def test_original_claws_cast_and_flashback_remove_flying_without_changing_stats(self):
        session=self.session(730201);source=self.add(session.engine,'Canopy Claws',zone='hand')
        target=self.add(session.engine,'Generic Bound Body',seat='B',ref='temporary-claws-target');target.counters['flying']=1
        jump=self.add(session.engine,'Jump',seat='C',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        session.state.players['C'].mana_pool.update({'U':1})
        self.prepare(session,mana={'G':2});self.checkpoint(session)
        self.assertTrue(self.binding(session,source)['strict_capability_ready'])
        self.cast(session,'A',source,[target.ref]);session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertNotIn('flying',session.engine._combat_keywords(session.state.cards[target.object_id]))
        self.assertEqual((2,6),(session.engine._numeric_stat(target.object_id,'power'),session.engine._numeric_stat(target.object_id,'toughness')))
        self.cast(session,'C',jump,[target.ref]);self.assertIsNone(self.resolve(session))
        self.assertIn('flying',session.engine._combat_keywords(session.state.cards[target.object_id]))
        source=session.state.cards[source.object_id]
        for _ in range(16):
            if session.state.priority_player=='A':break
            self.assertTrue(session.act(session.pending_principals()[0],{'action_id':'pass'}).ok)
        action=self.action(session,source)
        option=next(row for row in action['cost_options'] if row['id']=='flashback')
        accepted=session.act('pilot:A',{'action_id':action['id'],'cost_option':option['id'],'targets':[target.ref],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertNotIn('flying',session.engine._combat_keywords(session.state.cards[target.object_id]))
        self.assertEqual('exile',session.state.cards[source.object_id].zone)
        self.private_replay(session,private)

    def test_original_sentinel_removes_defender_and_grants_flying_for_same_incarnation(self):
        session=self.session(730202);source=self.add(session.engine,'Gargoyle Sentinel')
        source.acquired_control_turn_count=-1
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,mana={'C':3});self.checkpoint(session)
        self.assertTrue(self.binding(session,source)['strict_capability_ready'])
        self.assertIn('defender',session.engine._combat_keywords(source))
        action=self.activation(session,source)
        accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        keywords=session.engine._combat_keywords(session.state.cards[source.object_id])
        self.assertNotIn('defender',keywords);self.assertIn('flying',keywords)
        self.assertEqual(0,sum(session.state.players['A'].mana_pool.values()))
        self.private_replay(session,private)

    def test_fresh_original_claws_kills_temporary_removal_producer_omission(self):
        method='test_original_claws_cast_and_flashback_remove_flying_without_changing_stats'
        with patch('quorune.compiler.temporary_keyword_removal_templates.temporary_keyword_removal_template',return_value=None):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()

    def test_original_whiteout_locks_existing_group_and_does_not_affect_later_drake(self):
        session=self.session(730203);source=self.add(session.engine,'Whiteout',zone='hand')
        first=self.add(session.engine,'Generic Bound Body',seat='B',ref='whiteout-first');first.counters['flying']=1
        later=self.add(session.engine,'Wind Drake',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,mana={'G':1,'U':1,'C':3});self.checkpoint(session)
        self.assertTrue(self.binding(session,source)['strict_capability_ready'])
        self.cast(session,'A',source);session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertNotIn('flying',session.engine._combat_keywords(session.state.cards[first.object_id]))
        self.cast(session,'A',later);self.assertIsNone(self.resolve(session))
        self.assertIn('flying',session.engine._combat_keywords(session.state.cards[later.object_id]))
        self.assertEqual((2,6),(session.engine._numeric_stat(first.object_id,'power'),session.engine._numeric_stat(first.object_id,'toughness')))
        self.private_replay(session,private)

    def test_originals_activate_source_remove_and_grant_with_their_complete_fixed_costs(self):
        for name,mana,removed,added,power,toughness in (
            ('Tidewater Minion',{'C':4},('defender',),(),4,4),
            ('Canopy Dragon',{'G':1,'C':1},('trample',),('flying',),4,4),
            ('Xathrid Slyblade',{'B':1,'C':3},('hexproof',),('first strike','deathtouch'),2,1),
            ('Leering Gargoyle',{},('flying',),(),0,4)):
            session=self.session(730210+len(name));source=self.add(session.engine,name)
            source.acquired_control_turn_count=-1
            private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
            self.prepare(session,mana=mana);self.checkpoint(session)
            self.assertTrue(self.binding(session,source)['strict_capability_ready'],name)
            for keyword in removed:self.assertIn(keyword,session.engine._combat_keywords(source))
            action=self.activation(session,source);before=authoritative_state_hash(session.state)
            denied=session.act('pilot:D',{'action_id':action['id'],'pay':'auto'})
            self.assertFalse(denied.ok);self.assertEqual(before,authoritative_state_hash(session.state))
            accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'})
            self.assertTrue(accepted.ok,(name,accepted.summary))
            session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
            current=session.state.cards[source.object_id];keywords=session.engine._combat_keywords(current)
            for keyword in removed:self.assertNotIn(keyword,keywords)
            for keyword in added:self.assertIn(keyword,keywords)
            self.assertEqual((power,toughness),(session.engine._numeric_stat(current.object_id,'power'),session.engine._numeric_stat(current.object_id,'toughness')),name)
            self.assertEqual(0,sum(session.state.players['A'].mana_pool.values()))
            self.private_replay(session,private)

    def test_original_radjan_tap_cost_removes_only_selected_flying_and_survives_source_departure(self):
        session=self.session(730220);source=self.add(session.engine,'Radjan Spirit')
        source.acquired_control_turn_count=-1
        target=self.add(session.engine,'Generic Bound Body',seat='B',ref='radjan-target');target.counters['flying']=1
        other=self.add(session.engine,'Generic Bound Body',seat='D',ref='radjan-other');other.counters['flying']=1
        bounce=self.add(session.engine,'Unsummon',seat='B',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        session.state.players['B'].mana_pool.update({'U':1})
        self.prepare(session);self.checkpoint(session)
        self.assertTrue(self.binding(session,source)['strict_capability_ready'])
        action=self.activation(session,source)
        accepted=session.act('pilot:A',{'action_id':action['id'],'targets':[target.ref],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.assertTrue(session.state.cards[source.object_id].tapped)
        self.cast(session,'B',bounce,[source.ref]);session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual('hand',session.state.cards[source.object_id].zone)
        self.assertNotIn('flying',session.engine._combat_keywords(session.state.cards[target.object_id]))
        self.assertIn('flying',session.engine._combat_keywords(session.state.cards[other.object_id]))
        self.private_replay(session,private)

    def test_original_moloch_sacrifices_exactly_three_controlled_lands_before_removing_defender(self):
        session=self.session(730221);source=self.add(session.engine,'Torpid Moloch')
        lands=[self.add(session.engine,'Generic Bound Plains',ref='moloch-land-'+str(i)) for i in range(3)]
        foreign=self.add(session.engine,'Generic Bound Plains',seat='B',ref='moloch-foreign')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session);self.checkpoint(session)
        self.assertTrue(self.binding(session,source)['strict_capability_ready'])
        action=self.activation(session,source);before=authoritative_state_hash(session.state)
        for refs in ([lands[0].ref],[lands[0].ref,lands[0].ref,lands[1].ref],
                [lands[0].ref,lands[1].ref,foreign.ref]):
            denied=session.act('pilot:A',{'action_id':action['id'],'cost_cards':refs,'pay':'auto'})
            self.assertFalse(denied.ok);self.assertEqual(before,authoritative_state_hash(session.state))
        accepted=session.act('pilot:A',{'action_id':action['id'],'cost_cards':[c.ref for c in lands],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary)
        self.assertTrue(all(session.state.cards[c.object_id].zone=='graveyard' for c in lands))
        self.assertIn('defender',session.engine._combat_keywords(session.state.cards[source.object_id]))
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertNotIn('defender',session.engine._combat_keywords(session.state.cards[source.object_id]))
        self.assertEqual('battlefield',session.state.cards[foreign.object_id].zone)
        self.private_replay(session,private)

    def test_original_landfall_and_white_cast_triggers_remove_defender_and_cleanup_restores_it(self):
        for name in ('Shoal Serpent','Loyal Gyrfalcon'):
            session=self.session(730230+len(name));source=self.add(session.engine,name)
            private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
            land=self.add(session.engine,'Generic Bound Plains',zone='hand',ref='removal-trigger-land')
            white=self.add(session.engine,'Generic Bound Commander',zone='hand',ref='white-trigger-commander') if name=='Loyal Gyrfalcon' else None
            self.prepare(session,mana={'W':1,'C':1});self.checkpoint(session)
            self.assertTrue(self.binding(session,source)['strict_capability_ready'])
            self.assertIn('defender',session.engine._combat_keywords(session.state.cards[source.object_id]))
            if name=='Shoal Serpent':
                action=next(row for row in self.actions(session) if row['id']=='play-land:'+land.ref)
                result=session.act('pilot:A',{'action_id':action['id']});self.assertTrue(result.ok,result.summary)
            else:
                # A white creature spell gives a real cast event without
                # depending on an illegal Disenchant target.
                record=self.db.lookup('Generic Bound Commander')
                self.assertIn('W',record.colors)
                self.checkpoint(session);self.cast(session,'A',white)
            session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
            self.assertNotIn('defender',session.engine._combat_keywords(session.state.cards[source.object_id]))
            turn=session.state.turn_sequence
            self.advance(session,lambda s:s.state.turn_sequence>turn)
            self.assertIn('defender',session.engine._combat_keywords(session.state.cards[source.object_id]))
            self.private_replay(session,private)

    def test_original_bonds_group_removal_locks_only_current_opponents(self):
        session=self.session(730241);source=self.add(session.engine,'Bonds of Mortality')
        own=self.add(session.engine,'Generic Bound Body',ref='bonds-own');own.counters.update({'hexproof':1,'indestructible':1})
        other=self.add(session.engine,'Generic Bound Body',seat='B',ref='bonds-other');other.counters.update({'hexproof':1,'indestructible':1})
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,mana={'G':1});self.checkpoint(session)
        self.assertTrue(self.binding(session,source)['strict_capability_ready'])
        accepted=session.act('pilot:A',{'action_id':self.activation(session,source)['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        for keyword in ('hexproof','indestructible'):
            self.assertIn(keyword,session.engine._combat_keywords(session.state.cards[own.object_id]))
            self.assertNotIn(keyword,session.engine._combat_keywords(session.state.cards[other.object_id]))
        self.private_replay(session,private)

    def test_original_snow_and_ordinary_target_activations_keep_tap_and_mana_obligations(self):
        for name,mana in (('Adarkar Windform',{'C':1}),('Downdraft',{'G':1})):
            session=self.session(730250+len(name));source=self.add(session.engine,name)
            source.acquired_control_turn_count=-1
            target=self.add(session.engine,'Generic Bound Body',seat='B',ref='activation-removal-target');target.counters['flying']=1
            if name=='Adarkar Windform':self.add(session.engine,'Snow-Covered Island',ref='removal-snow-island')
            private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
            self.prepare(session,mana=mana);self.checkpoint(session)
            self.assertTrue(self.binding(session,source)['strict_capability_ready'],name)
            actions=[row for row in self.actions(session) if row['id'].startswith('activate:'+source.ref+':')]
            action=next(row for row in actions if row.get('target_schema') is not None)
            accepted=session.act('pilot:A',{'action_id':action['id'],'targets':[target.ref],'pay':'auto'})
            self.assertTrue(accepted.ok,(name,accepted.summary));session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
            self.assertNotIn('flying',session.engine._combat_keywords(session.state.cards[target.object_id]))
            self.assertEqual(0,sum(session.state.players['A'].mana_pool.values()))
            self.private_replay(session,private)

    def test_original_second_draw_trigger_removes_defender_and_grants_vigilance(self):
        session=self.session(730260);source=self.add(session.engine,'Jaded Analyst')
        draw=self.add(session.engine,'Inspiration',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,mana={'U':1,'C':3});self.checkpoint(session)
        self.assertTrue(self.binding(session,source)['strict_capability_ready'])
        self.cast(session,'A',draw,['A']);session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        keywords=session.engine._combat_keywords(session.state.cards[source.object_id])
        self.assertNotIn('defender',keywords);self.assertIn('vigilance',keywords)
        self.private_replay(session,private)

    def test_source_departure_while_sentinel_activation_pending_does_not_modify_new_incarnation(self):
        session=self.session(730270);source=self.add(session.engine,'Gargoyle Sentinel')
        bounce=self.add(session.engine,'Unsummon',seat='B',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        session.state.players['B'].mana_pool.update({'U':1})
        self.prepare(session,mana={'C':6});self.checkpoint(session)
        original=source.logical_object_id
        accepted=session.act('pilot:A',{'action_id':self.activation(session,source)['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary)
        self.cast(session,'B',bounce,[source.ref]);session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        source=session.state.cards[source.object_id];self.assertEqual('hand',source.zone)
        self.assertNotEqual(original,source.logical_object_id)
        self.cast(session,'A',source);self.assertIsNone(self.resolve(session))
        current=session.state.cards[source.object_id]
        self.assertIn('defender',session.engine._combat_keywords(current));self.assertNotIn('flying',session.engine._combat_keywords(current))
        self.private_replay(session,private)

    def test_countered_claws_and_departed_sole_target_have_no_removal_or_mana_refund(self):
        for countered in (False,True):
            session=self.session(730271+int(countered));source=self.add(session.engine,'Canopy Claws',zone='hand')
            target=self.add(session.engine,'Wind Drake',seat='B',ref='temporary-invalid-target')
            response=self.add(session.engine,'Counterspell' if countered else 'Unsummon',seat='B',zone='hand')
            private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
            session.state.players['B'].mana_pool.update({'U':2 if countered else 1})
            self.prepare(session,mana={'G':1});self.checkpoint(session)
            self.cast(session,'A',source,[target.ref])
            self.cast(session,'B',response,[session.state.stack[-1].ref] if countered else [target.ref])
            session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
            self.assertEqual('battlefield' if countered else 'hand',session.state.cards[target.object_id].zone)
            self.assertIn('flying',session.engine._combat_keywords(session.state.cards[target.object_id]))
            self.assertEqual('graveyard',session.state.cards[source.object_id].zone)
            self.assertEqual(0,sum(session.state.players['A'].mana_pool.values()))
            self.private_replay(session,private)

    def test_all_fifteen_original_candidates_have_complete_admission_and_runtime_variant_authority(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.preflight import card_semantic_status
        names=('Canopy Claws','Torpid Moloch','Tidewater Minion','Gargoyle Sentinel','Shoal Serpent',
            'Xathrid Slyblade','Whiteout','Canopy Dragon','Downdraft','Radjan Spirit','Bonds of Mortality',
            'Adarkar Windform','Jaded Analyst','Loyal Gyrfalcon','Leering Gargoyle')
        session=self.session(730280)
        for name in names:
            source=self.add(session.engine,name,zone='hand')
            self.assertTrue(self.binding(session,source)['strict_capability_ready'],name)
            status=card_semantic_status(self.db.lookup(name),session.engine.semantics,db=self.db,
                capability_registry=self.registry,capability_profile='commander_review')
            self.assertEqual('fully_playable',status['status'],(name,status['unresolved']))
        source=self.db.lookup('Canopy Claws')
        program=next(p for p in session.engine.semantics.programs_for_oracle(source.oracle_id) if CAPABILITY in p.capability_dependencies)
        for cap in (CAPABILITY,'combat.block.flying'):
            dependencies=tuple(c for c in program.capability_dependencies if c!=cap)
            missing=replace(program,capability_dependencies=dependencies,
                capability_closure=self.registry.closure(dependencies,profile='commander_review').to_dict())
            bound=bind_semantic_program_runtime(missing,capability_registry=self.registry,profile='commander_review')
            self.assertFalse(bound['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+cap,bound['blockers'])

    def test_preflight_keyword_and_cost_coverage_requires_exact_existing_owners(self):
        from quorune.preflight import card_semantic_status,_printed_activated_abilities
        from quorune.compiler.preflight_keyword_coverage import static_lines_without_keyword_owners
        session=self.session(730281);source=self.add(session.engine,'Tidewater Minion',zone='hand')
        printed=self.db.lookup(source.printed_name)
        owner=next(p for p in session.engine.semantics.programs_for_oracle(source.oracle_id)
            if p.provenance.get('template_id')=='printed-keyword-list-v1')
        self.assertEqual([],static_lines_without_keyword_owners(['Defender'],record=printed,trusted_programs=[owner]))
        self.assertEqual(['Defender'],static_lines_without_keyword_owners(['Defender'],record=printed,trusted_programs=[]))
        self.assertEqual(['Defender'],static_lines_without_keyword_owners(['Defender'],record=printed,trusted_programs=[replace(owner,trust_level='provisional')]))
        self.assertEqual(['Invent an unrepresented static rule.'],static_lines_without_keyword_owners(
            ['Defender','Invent an unrepresented static rule.'],record=printed,trusted_programs=[owner]))
        session.engine.semantics.put(replace(owner,provenance={**owner.provenance,'source_oracle_hash':'0'*64}))
        status=card_semantic_status(printed,session.engine.semantics,db=self.db,capability_registry=self.registry,capability_profile='commander_review')
        self.assertNotEqual('fully_playable',status['status']);self.assertIn('semantic_source_hash_drift',status['unresolved'])
        for name in ('Adarkar Windform','Whiteout'):
            abilities=_printed_activated_abilities(self.db.lookup(name))
            self.assertTrue(all(ability.compiled_cost for ability in abilities),name)
        unknown=replace(printed,oracle_text='{X}{S}: Scry 1.',keywords=())
        self.assertFalse(_printed_activated_abilities(unknown)[0].compiled_cost)
