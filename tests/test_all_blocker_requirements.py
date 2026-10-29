"""CR509.1c: giver-owned requirements maximize legal, freely available blocks."""

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from test_qualified_zone_event_queries import query_record
import test_granted_static_characteristics as native
import test_ring_native_actions as turns
from quorune.carddb import CardDatabase
from quorune.compiler.all_blocker_requirements import CAPABILITY, static_all_blocker_requirement_handler
from quorune.declaration_fragments import DeclarationRequirementTemplate
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class AllBlockerRequirementModelTests(unittest.TestCase):
    def test_old_codec_is_exact_and_bound_subjects_are_closed(self):
        old = DeclarationRequirementTemplate('intrinsic-all-able-blockers-v1','block','all_able_blockers')
        self.assertEqual({'template_id':old.template_id,'declaration':'block','kind':'all_able_blockers'},old.to_dict())
        current = replace(old,schema_version=2,scope='attached')
        self.assertEqual(CAPABILITY,current.capability_id)
        self.assertEqual(current,DeclarationRequirementTemplate.from_dict(current.to_dict()))
        for changes in ({'schema_version':True},{'schema_version':1},{'scope':'global'},
                {'scope':'self'},{'scope':[]},{'kind':'must_be_blocked'},{'unexpected':None}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                DeclarationRequirementTemplate.from_dict({**current.to_dict(),**changes})
        from quorune.semantic_runtime.ability_fragments import DeclarationRequirementComponentHandler
        from quorune.semantic_runtime.context import SemanticNodeError
        from quorune.ability_fragments import ability_fragment_to_dict
        descriptor={'handler_id':DeclarationRequirementComponentHandler().handler_id,'schema_version':1,
            'event':'characteristics.evaluate','fragment':ability_fragment_to_dict(old)}
        with self.assertRaises(SemanticNodeError):DeclarationRequirementComponentHandler().validate(descriptor)

    def test_resolved_codec_preserves_restrictions_and_locks_only_one_requirement_subject(self):
        from quorune.continuous_effect_model import ContinuousObjectIdentity, ContinuousEffectError
        from quorune.declaration_rule_effects import ResolutionDeclarationRuleEffect
        from quorune.rules.temporary_declaration_restrictions import temporary_declaration_restriction
        from quorune.temporary_all_blocker_model import TemporaryAllBlockerSpec
        identity=ContinuousObjectIdentity('body','body:zone:1')
        old=ResolutionDeclarationRuleEffect('old','source',1,temporary_declaration_restriction('cant_block'),(identity,))
        self.assertNotIn('schema_version',old.to_dict())
        self.assertEqual(old,ResolutionDeclarationRuleEffect.from_dict(old.to_dict()))
        rule=replace(old,effect_id='new',restriction=TemporaryAllBlockerSpec().to_requirement())
        value=rule.to_dict();self.assertEqual(3,value['schema_version']);self.assertNotIn('restriction',value)
        self.assertEqual(rule,ResolutionDeclarationRuleEffect.from_dict(value))
        for changes in ({'schema_version':True},{'schema_version':2},{'locked_objects':[]},
                {'restriction':old.restriction.to_dict()},{'live_query':None},
                {'requirement':DeclarationRequirementTemplate('intrinsic-must-be-blocked-if-able-v1','block','must_be_blocked').to_dict()}):
            with self.subTest(changes=changes),self.assertRaises(ContinuousEffectError):
                ResolutionDeclarationRuleEffect.from_dict({**value,**changes})
        for candidate in ({'schema_version':True,'kind':'all_able_blockers'},
                {'schema_version':1,'kind':'must_be_blocked'}, {**TemporaryAllBlockerSpec().to_dict(),'filter':'green'}):
            with self.assertRaises(ValueError):TemporaryAllBlockerSpec.from_dict(candidate)


class AllBlockerRequirementCompilerTests(unittest.TestCase):
    def test_attached_and_conditional_requirements_keep_their_giver(self):
        registry=load_default_capability_registry()
        for text in ('All creatures able to block enchanted creature do so.',
                'All creatures able to block equipped creature do so.',
                'Enchanted creature gets +4/+4 and has first strike, and all creatures able to block it do so.',
                'As long as enchanted creature is green, it gets +1/+1 and all creatures able to block it do so.',
                'Threshold — As long as there are seven or more cards in your graveyard, all creatures able to block enchanted creature do so.'):
            with self.subTest(text=text):
                compiled=static_all_blocker_requirement_handler(text,source_name='Generic Query Observer')
                self.assertIsNotNone(compiled)
                descriptors=compiled[1] if isinstance(compiled[1],tuple) else (compiled[1],)
                rule=descriptors[-1]
                self.assertEqual('attached',rule['fragment']['value']['scope'])
                self.assertEqual('characteristics.evaluate',rule['event'])
                ir=compile_oracle_card(query_record(text),capability_registry=registry,capability_profile='commander_review')
                self.assertEqual('exact',ir.status,ir.material_residuals)
                self.assertIn(CAPABILITY,ir.faces[0].nodes[0].capability_dependencies)
        for text in ('All creatures able to block another creature do so.',
                'All green creatures able to block enchanted creature do so.',
                'All creatures able to block enchanted creature this turn do so.',
                'Enchanted creature has flying and all creatures able to block that creature do so.',
                'As long as the top card of your library is green, all creatures able to block enchanted creature do so.'):
            with self.subTest(text=text):
                self.assertIsNone(static_all_blocker_requirement_handler(text,source_name='Generic Query Observer'))
                self.assertNotEqual('exact',compile_oracle_card(query_record(text),capability_registry=registry,capability_profile='commander_review').status)

    def test_verified_owner_withdrawal_fails_closed_with_unrelated_control(self):
        self.verified_withdrawal(
            'As long as enchanted creature is green, it gets +1/+1 and all creatures able to block it do so.',
            (CAPABILITY,'combat.declaration.typed_components','continuous.characteristics.fixed_public_state'))

    def verified_withdrawal(self,text,owners,*,type_line='Creature — Human'):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        declarations=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        known=set(discover_tests(ROOT).values())
        for omitted in (*owners,'variant.monarch.designate'):
            value=deepcopy(raw)
            next(r for r in value['capabilities'] if r['id']==omitted).update(status='blocked',blockers=['all-blocker owner withdrawn'])
            registry=CapabilityRegistry(value)
            index=build_index(registry_value=value,declaration_source=declarations,discovered_test_ids=known)
            registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
            ir=compile_oracle_card(replace(query_record(text),type_line=type_line),capability_registry=registry,capability_profile='commander_review')
            self.assertEqual(omitted=='variant.monarch.designate',ir.status=='exact',omitted)

    def test_temporary_verified_owners_fail_closed_with_unrelated_control(self):
        from quorune.temporary_all_blocker_model import TEMPORARY_ALL_BLOCKER_CAPABILITY
        self.verified_withdrawal('All creatures able to block target creature this turn do so.',
            (TEMPORARY_ALL_BLOCKER_CAPABILITY,'combat.declaration.typed_components',
             'continuous.resolution.declaration_rules_until_end_of_turn'),type_line='Sorcery')

    def test_temporary_target_and_source_use_closed_rule_payloads_and_sealed_subjects(self):
        from quorune.compiler.temporary_all_blockers import temporary_all_blocker_template
        from quorune.temporary_all_blocker_model import TEMPORARY_ALL_BLOCKER_CAPABILITY
        registry=load_default_capability_registry()
        for text,target in (('All creatures able to block target creature this turn do so.','$target.0'),
                ('{2}{G}: All creatures able to block this creature this turn do so.','$source.zone_object')):
            with self.subTest(text=text):
                record=query_record(text) if text.startswith('{') else replace(query_record(text),type_line='Sorcery')
                ir=compile_oracle_card(record,capability_registry=registry,capability_profile='commander_review')
                self.assertEqual('exact',ir.status,ir.material_residuals)
                node=ir.faces[0].nodes[0]
                self.assertIn(TEMPORARY_ALL_BLOCKER_CAPABILITY,node.capability_dependencies)
                self.assertEqual(target,node.effects[0]['card'])
                self.assertEqual({'schema_version':1,'kind':'all_able_blockers'},node.effects[0]['restriction'])
        for text in ('All green creatures able to block target creature this turn do so.',
                'All creatures able to block it this turn do so.',
                'All creatures able to block this creature next turn do so.',
                'All creatures able to block another creature this turn do so.'):
            self.assertIsNone(temporary_all_blocker_template(text,card_name='Generic Query Observer',allow_source=True))


class AllBlockerRequirementNativeTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=native.GrantedStaticCharacteristicNativeTests.session
    add=native.GrantedStaticCharacteristicNativeTests.add
    prepare=native.GrantedStaticCharacteristicNativeTests.prepare
    cast=native.GrantedStaticCharacteristicNativeTests.cast
    checkpoint=native.GrantedStaticCharacteristicNativeTests.checkpoint
    resolve=native.GrantedStaticCharacteristicNativeTests.resolve
    replay=native.GrantedStaticCharacteristicNativeTests.replay
    assert_closed=native.GrantedStaticCharacteristicNativeTests.assert_closed
    priority=native.GrantedStaticCharacteristicNativeTests.priority
    action=native.GrantedStaticCharacteristicNativeTests.action
    cast_reserved=native.GrantedStaticCharacteristicNativeTests.cast_reserved
    cast_for=native.GrantedStaticCharacteristicNativeTests.cast_for
    equip=native.GrantedStaticCharacteristicNativeTests.equip
    until=turns.RingNativeActionTests.until

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'all-blockers.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/all-blocker-requirement-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Blocking requirements',[DeckEntry('Isamaru, Hound of Konda',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Isamaru, Hound of Konda'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()

    def blockers(self,session,attackers):
        self.until(session,lambda s:s.state.pending_decision is not None and s.state.pending_decision.kind=='combat.attackers')
        result=session.act('pilot:A',{'action_id':'attack','attackers':{card.ref:'B' for card in attackers}})
        self.assertTrue(result.ok,result.summary)
        self.until(session,lambda s:s.state.pending_decision is not None and s.state.pending_decision.kind=='combat.blockers' and 'B' in s.state.pending_decision.actors)
        return session.state.pending_decision.payload_by_actor['B']['declaration_constraints']

    def test_actual_lure_survives_recipient_ability_loss_and_requires_every_legal_blocker(self):
        session=self.session(509612001)
        source=self.add(session.engine,'Lure',zone='hand')
        body=self.add(session.engine,'Hill Giant');other=self.add(session.engine,'Goblin Piker',ref='other-attacker')
        first=self.add(session.engine,'Goblin Piker',seat='B',ref='first-blocker')
        second=self.add(session.engine,'Hill Giant',seat='B',ref='second-blocker')
        tapped=self.add(session.engine,'Memnite',seat='B',ref='tapped-blocker');tapped.tapped=True
        loss=self.add(session.engine,'Dress Down',seat='B',zone='hand')
        private=self.add(session.engine,'Twiddle',seat='C',zone='hand')
        body.acquired_control_turn_count=other.acquired_control_turn_count=-1
        self.assert_closed(session,source);self.prepare(session,mana={'C':1,'G':2})
        session.state.players['B'].mana_pool.update({'C':1,'U':1})
        self.checkpoint(session);self.cast(session,'A',source,[body.ref]);self.assertIsNone(self.resolve(session))
        self.cast(session,'B',loss);self.assertIsNone(self.resolve(session))
        constraints=self.blockers(session,(body,other))
        self.assertEqual(2,constraints['maximum_requirements'])
        before=authoritative_state_hash(session.state)
        for actor,blocks in (('C',{first.ref:body.ref,second.ref:body.ref}),
                ('B',{}),('B',{first.ref:other.ref,second.ref:body.ref}),
                ('B',{first.ref:body.ref,second.ref:body.ref,tapped.ref:body.ref})):
            self.assertFalse(session.act('pilot:'+actor,{'action_id':'block','blocks':blocks}).ok)
            self.assertEqual(before,authoritative_state_hash(session.state))
        for seat in 'ABD':self.assertNotIn(private.ref,json.dumps(session.packet('pilot:'+seat,full=True)))
        session=self.replay(session,load=True)
        result=session.act('pilot:B',{'action_id':'block','blocks':{first.ref:body.ref,second.ref:body.ref}})
        self.assertTrue(result.ok,result.summary);self.replay(session,load=True)

    def test_recipient_ability_loss_mutant_is_killed(self):
        import quorune.rules.all_blocker_requirements as owner
        original=owner.current_attacker_block_requirements
        def mutant(host,attacker,**kwargs):
            return tuple(row for row in original(host,attacker,**kwargs) if row[0].startswith('self:'))
        with patch.object(owner,'current_attacker_block_requirements',side_effect=mutant),self.assertRaises(AssertionError):
            self.test_actual_lure_survives_recipient_ability_loss_and_requires_every_legal_blocker()

    def test_actual_lure_respects_menace_and_impossible_requirements(self):
        for count in (1,2):
            with self.subTest(blockers=count):
                session=self.session(509612010+count)
                source=self.add(session.engine,'Lure',zone='hand')
                body=self.add(session.engine,'Rograkh, Son of Rohgahh')
                body.acquired_control_turn_count=-1
                blockers=[self.add(session.engine,'Hill Giant',seat='B',ref='menace-blocker-'+str(i)) for i in range(count)]
                self.prepare(session,mana={'C':1,'G':2});self.checkpoint(session)
                self.cast(session,'A',source,[body.ref]);self.assertIsNone(self.resolve(session))
                constraints=self.blockers(session,(body,))
                self.assertEqual(0 if count==1 else 2,constraints['maximum_requirements'])
                before=authoritative_state_hash(session.state)
                self.assertFalse(session.act('pilot:B',{'action_id':'block','blocks':{blockers[0].ref:body.ref}}).ok)
                self.assertEqual(before,authoritative_state_hash(session.state))
                if count==2:
                    self.assertFalse(session.act('pilot:B',{'action_id':'block','blocks':{}}).ok)
                    self.assertEqual(before,authoritative_state_hash(session.state))
                result=session.act('pilot:B',{'action_id':'block','blocks':{b.ref:body.ref for b in blockers} if count==2 else {}})
                self.assertTrue(result.ok,result.summary)

    def requirement_count(self,session,card):
        from quorune.rules.all_blocker_requirements import current_attacker_block_requirements
        from quorune.errors import GameRuleError
        return len(current_attacker_block_requirements(session.engine,session.state.cards[card.object_id],error_type=GameRuleError))

    def test_actual_mask_follows_reattachment_and_stops_when_its_source_leaves(self):
        session=self.session(509612020)
        source=self.add(session.engine,'Nemesis Mask',zone='hand')
        body=self.add(session.engine,'Hill Giant');other=self.add(session.engine,'Goblin Piker')
        removal=self.add(session.engine,'Disenchant',seat='B',zone='hand')
        self.assert_closed(session,source);self.prepare(session,mana={'C':9})
        session.state.players['B'].mana_pool.update({'C':1,'W':1});self.checkpoint(session)
        self.cast(session,'A',source);self.assertIsNone(self.resolve(session))
        self.equip(session,source,body)
        self.assertEqual((1,0),(self.requirement_count(session,body),self.requirement_count(session,other)))
        self.equip(session,source,other)
        self.assertEqual((0,1),(self.requirement_count(session,body),self.requirement_count(session,other)))
        self.cast(session,'B',removal,[source.ref]);self.assertIsNone(self.resolve(session))
        self.assertEqual((0,0),(self.requirement_count(session,body),self.requirement_count(session,other)))
        self.replay(session,load=True)

    def test_actual_gift_rechecks_current_attached_color(self):
        session=self.session(509612030)
        source=self.add(session.engine,'Gift of the Deity',zone='hand')
        body=self.add(session.engine,'Llanowar Elves')
        moon=self.add(session.engine,'Turn to Frog',zone='hand')
        self.assert_closed(session,source);self.assert_closed(session,moon)
        self.prepare(session,mana={'C':5,'G':1,'U':1});self.checkpoint(session)
        self.cast_reserved(session,source,[body.ref],reserve={'C':1,'U':1});self.assertIsNone(self.resolve(session))
        self.assertEqual(1,self.requirement_count(session,body))
        self.assertEqual(2,session.engine._numeric_stat(body.object_id,'power'))
        self.cast(session,'A',moon,[body.ref]);self.assertIsNone(self.resolve(session))
        self.assertEqual(0,self.requirement_count(session,body))
        self.assertEqual(1,session.engine._numeric_stat(body.object_id,'power'))
        self.replay(session,load=True)

    def test_actual_umbra_keeps_characteristics_separate_from_its_blocking_rule(self):
        session=self.session(509613030)
        source=self.add(session.engine,'Indrik Umbra',zone='hand')
        body=self.add(session.engine,'Hill Giant')
        loss=self.add(session.engine,'Dress Down',seat='B',zone='hand')
        self.assert_closed(session,source);self.prepare(session,mana={'C':4,'G':1,'W':1})
        session.state.players['B'].mana_pool.update({'C':1,'U':1});self.checkpoint(session)
        self.cast(session,'A',source,[body.ref]);self.assertIsNone(self.resolve(session))
        self.assertEqual(7,session.engine._numeric_stat(body.object_id,'power'))
        self.assertEqual(1,self.requirement_count(session,body))
        self.assertIn('first strike',session.engine._combat_keywords(session.state.cards[body.object_id]))
        self.cast(session,'B',loss);self.assertIsNone(self.resolve(session))
        self.assertEqual(7,session.engine._numeric_stat(body.object_id,'power'))
        self.assertEqual(1,self.requirement_count(session,body))
        self.assertNotIn('first strike',session.engine._combat_keywords(session.state.cards[body.object_id]))
        self.replay(session,load=True)

    def test_actual_threshold_reads_giver_controller_and_refreshes_after_resolution(self):
        session=self.session(509612040)
        source=self.add(session.engine,"Seton's Desire",seat='B',zone='hand')
        body=self.add(session.engine,'Hill Giant')
        moon=self.add(session.engine,'Twiddle',seat='B',zone='hand')
        for i in range(6):self.add(session.engine,'Generic Bound Plains',seat='B',zone='graveyard',ref='threshold-grave-'+str(i))
        self.assert_closed(session,source);self.prepare(session,actor='B',active='B',mana={'C':2,'G':1,'U':1});self.checkpoint(session)
        self.cast_for(session,'B',source,[body.ref],reserve={'U':1});self.assertIsNone(self.resolve(session))
        self.assertEqual(0,self.requirement_count(session,body))
        self.cast(session,'B',moon,[body.ref]);self.assertEqual('semantic.choice',self.resolve(session).kind)
        result=session.act('pilot:B',{'action_id':'choose','choice':'untap'});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(7,len(session.state.players['B'].zones['graveyard']))
        self.assertEqual(0,len(session.state.players['A'].zones['graveyard']))
        self.assertEqual(1,self.requirement_count(session,body))
        self.replay(session,load=True)

    def test_actual_resolved_spell_survives_ability_loss_and_expires_at_cleanup(self):
        session=self.session(509613001)
        source=self.add(session.engine,'Alluring Scent',zone='hand')
        body=self.add(session.engine,'Hill Giant');body.acquired_control_turn_count=-1
        other=self.add(session.engine,'Goblin Piker');other.acquired_control_turn_count=-1
        blocker=self.add(session.engine,'Memnite',seat='B')
        private=self.add(session.engine,'Twiddle',seat='D',zone='hand')
        loss=self.add(session.engine,'Dress Down',seat='B',zone='hand')
        self.assert_closed(session,source);self.prepare(session,mana={'C':1,'G':2})
        session.state.players['B'].mana_pool.update({'C':1,'U':1});self.checkpoint(session)
        self.cast(session,'A',source,[body.ref]);self.assertIsNone(self.resolve(session))
        self.assertEqual('graveyard',session.state.cards[source.object_id].zone)
        self.cast(session,'B',loss);self.assertIsNone(self.resolve(session))
        self.assertEqual(1,self.requirement_count(session,body))
        identity=session.state.cards[body.object_id].logical_object_id
        constraints=self.blockers(session,(body,other));self.assertEqual(1,constraints['maximum_requirements'])
        for seat in 'ABC':self.assertNotIn(private.ref,json.dumps(session.packet('pilot:'+seat,full=True)))
        self.assertIn(private.ref,json.dumps(session.packet('pilot:D',full=True)))
        before=authoritative_state_hash(session.state)
        for actor,blocks in (('B',{}),('B',{blocker.ref:other.ref}),('C',{blocker.ref:body.ref})):
            self.assertFalse(session.act('pilot:'+actor,{'action_id':'block','blocks':blocks}).ok)
            self.assertEqual(before,authoritative_state_hash(session.state))
        session=self.replay(session,load=True)
        result=session.act('pilot:B',{'action_id':'block','blocks':{blocker.ref:body.ref}})
        self.assertTrue(result.ok,result.summary)
        self.until(session,lambda s:s.state.active_player=='B' and s.state.phase=='precombat_main')
        self.assertEqual(identity,session.state.cards[body.object_id].logical_object_id)
        self.assertEqual(0,self.requirement_count(session,body));self.replay(session,load=True)

    def test_actual_mortipede_source_binding_does_not_follow_a_returned_incarnation(self):
        for blink in (False,True):
            with self.subTest(blink=blink):
                session=self.session(509613010+blink)
                source=self.add(session.engine,'Mortipede')
                flicker=self.add(session.engine,'Flicker of Fate',seat='B',zone='hand')
                self.assert_closed(session,source);self.prepare(session,mana={'C':2,'G':1})
                session.state.players['B'].mana_pool.update({'C':1,'W':1});self.checkpoint(session)
                identity=source.logical_object_id
                offered=next(row for row in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
                    if row['id'].startswith('activate:'+source.ref+':'))
                result=session.act('pilot:A',{'action_id':offered['id'],'pay':'auto'})
                self.assertTrue(result.ok,result.summary)
                if blink:
                    self.cast(session,'B',flicker,[source.ref])
                self.assertIsNone(self.resolve(session))
                self.assertEqual(not blink,identity==session.state.cards[source.object_id].logical_object_id)
                self.assertEqual(0 if blink else 1,self.requirement_count(session,source))
                if blink:self.replay(session,load=True)

    def test_actual_dominance_composes_fixed_stats_and_one_locked_requirement(self):
        session=self.session(509613040)
        source=self.add(session.engine,'Declare Dominance',zone='hand')
        body=self.add(session.engine,'Hill Giant');other=self.add(session.engine,'Goblin Piker')
        loss=self.add(session.engine,'Dress Down',seat='B',zone='hand')
        self.assert_closed(session,source);self.prepare(session,mana={'C':3,'G':2})
        session.state.players['B'].mana_pool.update({'C':1,'U':1});self.checkpoint(session)
        self.cast(session,'A',source,[body.ref]);self.assertIsNone(self.resolve(session))
        self.assertEqual((6,2),tuple(session.engine._numeric_stat(c.object_id,'power') for c in (body,other)))
        self.assertEqual((1,0),tuple(self.requirement_count(session,c) for c in (body,other)))
        self.cast(session,'B',loss);self.assertIsNone(self.resolve(session))
        self.assertEqual(6,session.engine._numeric_stat(body.object_id,'power'))
        self.assertEqual(1,self.requirement_count(session,body));self.replay(session,load=True)

    def test_actual_channel_and_kicked_entry_reuse_the_same_target_rule(self):
        for index,name in enumerate(("Shinen of Life's Roar",'Taunting Arbormage','Taunting Arbormage')):
            with self.subTest(name=name,kicked=index==1):
                session=self.session(509613050+index)
                source=self.add(session.engine,name,zone='hand')
                body=self.add(session.engine,'Hill Giant',seat='B')
                self.assert_closed(session,source)
                self.prepare(session,mana={'C':5,'G':2});self.checkpoint(session)
                if index==0:
                    offered=next(row for row in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
                        if row['id'].startswith('activate:'+source.ref+':'))
                    result=session.act('pilot:A',{'action_id':offered['id'],'targets':[body.ref],'pay':'auto'})
                else:
                    offered=self.action(session,'A',source)
                    command={'action_id':offered['id'],'pay':'auto'}
                    if index==1:command['cost_option']='kicked'
                    result=session.act('pilot:A',command)
                self.assertTrue(result.ok,result.summary)
                if index==1:self.assertEqual('kicked',session.state.stack[-1].context['cost_option'])
                pending=self.resolve(session)
                if index==1:
                    self.assertEqual('semantic.target',pending.kind)
                    before=authoritative_state_hash(session.state)
                    self.assertFalse(session.act('pilot:B',{'action_id':'choose','targets':[body.ref]}).ok)
                    self.assertEqual(before,authoritative_state_hash(session.state))
                    result=session.act('pilot:A',{'action_id':'choose','targets':[body.ref]})
                    self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(session))
                else:self.assertIsNone(pending)
                self.assertEqual(0 if index==2 else 1,self.requirement_count(session,body))
                self.assertEqual('graveyard' if index==0 else 'battlefield',session.state.cards[source.object_id].zone)
                if index==1:self.replay(session,load=True)

    def test_bound_static_and_resolved_runtime_owners_cannot_be_omitted(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.temporary_all_blocker_model import TEMPORARY_ALL_BLOCKER_CAPABILITY
        session=self.session(509613020)
        for name,owners in (("Seton's Desire",(CAPABILITY,'continuous.characteristics.fixed_public_state')),
                ('Alluring Scent',(TEMPORARY_ALL_BLOCKER_CAPABILITY,'continuous.resolution.declaration_rules_until_end_of_turn'))):
            source=self.add(session.engine,name,zone='hand')
            for owner in owners:
                programs=[p for p in session.engine.semantics.programs_for_oracle(source.oracle_id) if owner in p.capability_dependencies]
                self.assertTrue(programs,(name,owner))
                for program in programs:
                    deps=tuple(cap for cap in program.capability_dependencies if cap!=owner)
                    changed=replace(program,capability_dependencies=deps,
                        capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
                    binding=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review')
                    self.assertFalse(binding['strict'],(name,owner))
                    self.assertIn('capability:undeclared_runtime_dependency:'+owner,binding['blockers'])

    def test_resolved_requirement_omission_mutant_is_killed(self):
        import quorune.rules.all_blocker_requirements as owner
        original=owner.current_attacker_block_requirements
        def mutant(host,attacker,**kwargs):
            return tuple(row for row in original(host,attacker,**kwargs) if not row[0].startswith('resolved:'))
        with patch.object(owner,'current_attacker_block_requirements',side_effect=mutant),self.assertRaises(AssertionError):
            self.test_actual_resolved_spell_survives_ability_loss_and_expires_at_cleanup()
