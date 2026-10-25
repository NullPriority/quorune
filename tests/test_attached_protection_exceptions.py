from __future__ import annotations

"""CR 702.16n exemptions belong to one protection instance and one Aura."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_qualified_hexproof as controls
from quorune.rules.evidence_storage import read_evidence_document


class AttachedProtectionExceptionTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=controls.QualifiedHexproofTests.session
    add=controls.QualifiedHexproofTests.add
    prepare=controls.QualifiedHexproofTests.prepare
    checkpoint=controls.QualifiedHexproofTests.checkpoint
    resolve=controls.QualifiedHexproofTests.resolve
    replay=controls.QualifiedHexproofTests.replay
    binding=controls.QualifiedHexproofTests.binding
    priority=controls.QualifiedHexproofTests.priority
    cast=controls.QualifiedHexproofTests.cast
    private_replay=controls.QualifiedHexproofTests.private_replay
    actions=controls.QualifiedHexproofTests.actions
    advance=controls.QualifiedHexproofTests.advance
    attack=controls.QualifiedHexproofTests.attack
    blockers=controls.QualifiedHexproofTests.blockers
    block=controls.QualifiedHexproofTests.block

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'attached-protection.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/attached-protection-cards.json',
            ROOT/'tests/fixtures/chosen-aura-protection-cards.json',ROOT/'tests/fixtures/fixed-type-aura-protection-cards.json',
            ROOT/'tests/fixtures/fixed-resolution-payment-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Attached protection',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def test_actual_white_ward_cast_survives_its_own_grant_and_blue_bounce_ends_it(self):
        session=self.session(750300);body=self.add(session.engine,'Grizzly Bears')
        ward=self.add(session.engine,'White Ward',zone='hand')
        own=self.add(session.engine,'Humble',zone='hand')
        blue=self.add(session.engine,'Boomerang',seat='B',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.assertTrue(self.binding(session,ward)['strict_capability_ready'],'Complete original White Ward must admit before any gameplay claim')
        self.prepare(session,mana={'W':2,'C':1});session.state.players['B'].mana_pool['U']=2;self.checkpoint(session)
        before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:B',{'action_id':'cast:'+ward.ref,'targets':[body.ref],'pay':'auto'}).ok)
        self.assertEqual(before,authoritative_state_hash(session.state))
        self.cast(session,'A',ward,[body.ref],{'W':1});session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        current=session.state.cards[ward.object_id]
        self.assertEqual(('battlefield',body.object_id),(current.zone,current.attached_to))
        self.priority(session,'A');before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:A',{'action_id':'cast:'+own.ref,'targets':[body.ref],'pay':'auto'}).ok)
        self.assertEqual(before,authoritative_state_hash(session.state))
        self.cast(session,'B',blue,[body.ref],{'U':2});session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual(('hand','graveyard'),(session.state.cards[body.object_id].zone,session.state.cards[ward.object_id].zone))
        self.private_replay(session,private)

    def test_fixed_exception_codec_retains_only_its_aura_and_preserves_other_debt_checks(self):
        from dataclasses import replace
        from quorune.ability_fragments import ProtectionSpec,ProtectionQualityKind,ability_fragment_to_dict,ability_fragment_from_dict
        from quorune.protection import protection_verdict,ProtectionSource,ProtectionVerdict
        from quorune.compiler.continuous_templates import attached_fixed_characteristics_handler
        old=ProtectionSpec(ProtectionQualityKind.COLOR,'W')
        self.assertEqual({'schema_version':1,'quality_kind':'color','quality':'W'},old.to_dict())
        spec=ProtectionSpec(ProtectionQualityKind.COLOR,'W',3,exempt_aura_incarnation='ward@1')
        self.assertEqual(spec,ability_fragment_from_dict(ability_fragment_to_dict(spec)))
        from types import SimpleNamespace
        from quorune.protection_attachment_exceptions import attached_protection_runtime_dependencies,CAPABILITY
        raw=ability_fragment_to_dict(replace(spec,exempt_aura_incarnation='$source'))
        handler={'handler_id':'continuous.attached.fixed-characteristics.v1','modifier':{'add_ability_fragments':[raw]}}
        self.assertEqual({CAPABILITY},attached_protection_runtime_dependencies(SimpleNamespace(handlers=(handler,))))
        for program in (SimpleNamespace(effects=({'nested':[raw]},)),
                        SimpleNamespace(handlers=({**handler,'handler_id':'ability.static.protection.v1'},)),
                        SimpleNamespace(handlers=([raw],)),
                        SimpleNamespace(handlers=({**handler,'modifier':True,'extra':[raw]},)),
                        SimpleNamespace(handlers=({**handler,'modifier':{'add_ability_fragments':[ability_fragment_to_dict(spec)]}},))):
            with self.assertRaises(ValueError):attached_protection_runtime_dependencies(program)
        data={'ability_fragments':(ability_fragment_to_dict(spec),),'keywords':['Protection']};white=ProtectionSource(colors=frozenset({'W'}))
        self.assertIs(ProtectionVerdict.ALLOWED,protection_verdict(data,white,retaining_aura='ward@1'))
        for incarnation in (None,'other@1','ward@2'):
            self.assertIs(ProtectionVerdict.BLOCKED,protection_verdict(data,white,retaining_aura=incarnation))
        self.assertIs(ProtectionVerdict.BLOCKED,protection_verdict({**data,'ability_fragments':tuple(ability_fragment_to_dict(v) for v in (spec,old))},white,retaining_aura='ward@1'))
        self.assertIs(ProtectionVerdict.UNRESOLVED,protection_verdict({'keywords':['Protection']},white,retaining_aura='ward@1'))
        self.assertIs(ProtectionVerdict.UNRESOLVED,protection_verdict({**data,'ability_fragments':(ability_fragment_to_dict(replace(spec,exempt_aura_incarnation='$source')), )},white,retaining_aura='ward@1'))
        text="Enchanted creature has protection from white. This effect doesn't remove this Aura."
        self.assertIsNotNone(attached_fixed_characteristics_handler(text))
        from quorune.protection_attachment_exceptions import CHOSEN_AURA_PROTECTION_HANDLER
        chosen=attached_fixed_characteristics_handler(text.replace('white','the chosen color'))
        self.assertIsNotNone(chosen);self.assertEqual(CHOSEN_AURA_PROTECTION_HANDLER,chosen[1]['handler_id'])
        for bad in (text.replace('this Aura','Auras'),text.replace('white','the chosen mana value'),text.replace('Enchanted creature','Equipped creature')):
            self.assertIsNone(attached_fixed_characteristics_handler(bad))
        for changes in ({'schema_version':True},{'schema_version':2},{'quality_kind':'everything'},
                        {'exempt_aura_incarnation':None},{'unknown':0}):
            with self.assertRaises((ValueError,TypeError)):ProtectionSpec.from_dict({**spec.to_dict(),**changes})

    def test_fresh_compiler_binder_and_actual_retention_omissions_are_killed(self):
        method='test_actual_white_ward_cast_survives_its_own_grant_and_blue_bounce_ends_it'
        cases=(('quorune.compiler.attached_protection_exceptions.attached_protection_exception_handler',lambda *a,**k:None),
               ('quorune.protection_attachment_exceptions.bind_attached_protection_exceptions',lambda node,*a:node),
               ('quorune.protection_attachment_exceptions.protection_specs_for_retained_aura',lambda specs,*a:specs))
        for target,omitted in cases:
            with patch(target,omitted):
                with self.assertRaises(AssertionError):getattr(type(self)(method),method)()

    def test_five_original_fixed_color_wards_have_complete_cast_and_attachment_results(self):
        from quorune.ability_fragments import protection_specs,ability_fragment_from_dict
        for index,(name,color) in enumerate((('White Ward','W'),('Blue Ward','U'),('Black Ward','B'),('Red Ward','R'),('Green Ward','G'))):
            with self.subTest(name=name):
                session=self.session(750310+index);body=self.add(session.engine,'Grizzly Bears')
                ward=self.add(session.engine,name,zone='hand')
                self.assertTrue(self.binding(session,ward)['strict_capability_ready'])
                self.prepare(session,mana={'W':1});self.checkpoint(session)
                self.cast(session,'A',ward,[body.ref],{'W':1});self.assertIsNone(self.resolve(session))
                self.assertEqual(('battlefield',body.object_id),(session.state.cards[ward.object_id].zone,session.state.cards[ward.object_id].attached_to))
                data=session.engine._effective_card_data(session.state.cards[body.object_id])
                specs=protection_specs(ability_fragment_from_dict(v) for v in data.get('ability_fragments',()))
                spec,=specs;self.assertEqual((color,session.state.cards[ward.object_id].logical_object_id),(spec.quality,spec.exempt_aura_incarnation))
                self.replay(session)

    def test_verified_exception_and_owner_withdrawals_and_runtime_stripping_reject(self):
        import json
        from dataclasses import replace
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from scripts.update_capability_evidence import build_index,discover_tests
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.oracle_ir import compile_oracle_card
        cap='protection.attachment.source_aura_exception'
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        declarations=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        known=set(discover_tests(ROOT).values())
        def verified(value):
            registry=CapabilityRegistry(value)
            index=build_index(registry_value=value,declaration_source=declarations,discovered_test_ids=known)
            registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry));return registry
        self.assertEqual('exact',compile_oracle_card(self.db.lookup('White Ward'),capability_registry=verified(raw),capability_profile='commander_review').status)
        for withdrawn in (cap,'protection.typed.debt','continuous.attached.fixed_characteristics'):
            changed=json.loads(json.dumps(raw));next(r for r in changed['capabilities'] if r['id']==withdrawn).update(status='blocked',blockers=['withdrawn attached protection owner'])
            ir=compile_oracle_card(self.db.lookup('White Ward'),capability_registry=verified(changed),capability_profile='commander_review')
            self.assertNotEqual('exact',ir.status)
            blockers=[b for r in ir.material_residuals for b in r.blockers]
            self.assertTrue(any('status:'+withdrawn+':blocked' in b for b in blockers))
            self.assertFalse(any('evidence_index:unverified' in b for b in blockers))
        session=self.session(750319);ward=self.add(session.engine,'White Ward',zone='hand')
        program=next(p for p in session.engine.semantics.programs_for_oracle(ward.oracle_id) if cap in p.capability_dependencies)
        self.assertTrue(bind_semantic_program_runtime(program,capability_registry=self.registry,profile='commander_review')['strict'])
        deps=tuple(c for c in program.capability_dependencies if c!=cap)
        stripped=replace(program,capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict() if deps else None)
        result=bind_semantic_program_runtime(stripped,capability_registry=self.registry,profile='commander_review')
        self.assertFalse(result['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+cap,result['blockers'])

    def test_actual_colorless_steppe_protection_is_an_independent_instance_that_removes_white_ward(self):
        session=self.session(750320);body=self.add(session.engine,'Grizzly Bears')
        ward=self.add(session.engine,'White Ward',zone='hand');steppe=self.add(session.engine,'Sejiri Steppe',zone='hand')
        self.assertTrue(self.binding(session,steppe)['strict_capability_ready'])
        self.prepare(session,mana={'W':1});self.checkpoint(session)
        self.cast(session,'A',ward,[body.ref],{'W':1});self.assertIsNone(self.resolve(session))
        self.assertEqual('battlefield',session.state.cards[ward.object_id].zone)
        self.priority(session,'A')
        offered=next(r for r in self.actions(session) if r['id']=='play-land:'+steppe.ref)
        result=session.act('pilot:A',{'action_id':offered['id']});self.assertTrue(result.ok,result.summary)
        pending=self.resolve(session)
        if pending is not None and pending.kind=='semantic.target':
            result=session.act('pilot:A',{'action_id':'choose','targets':[body.ref]});self.assertTrue(result.ok,result.summary)
            pending=self.resolve(session)
        self.assertIsNotNone(pending);self.assertEqual('semantic.choice',pending.kind)
        result=session.act('pilot:A',{'action_id':'choose','choice':'W'});self.assertTrue(result.ok,result.summary)
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual('graveyard',session.state.cards[ward.object_id].zone)
        self.assertEqual('battlefield',session.state.cards[body.object_id].zone)
        self.replay(session)

    def test_actual_aura_families_finish_independent_protection_destination_choices_before_any_move(self):
        for index,(name,cost,chosen,boost) in enumerate((('White Ward',{'W':1},False,0),
                ('Pentarch Ward',{'W':1,'C':2},True,0),('Tattoo Ward',{'W':1,'C':2},False,1))):
            with self.subTest(name=name):
                session=self.session(750340+index);land_seat='B' if chosen else 'A'
                body=self.add(session.engine,'Grizzly Bears',seat=land_seat)
                aura=self.add(session.engine,name,zone='hand');steppe=self.add(session.engine,'Sejiri Steppe',seat=land_seat,zone='hand')
                if chosen:
                    ordinary=next(oid for oid in session.state.players['A'].zones['hand'] if oid!=aura.object_id)
                    session.engine.move_card(ordinary,'library',reason='fixture cleanup hand boundary')
                private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand',ref='private-composition-hand')
                for seat,control in (('B','Generic Payment Destination Replacement'),('C','Generic Payment Competing Replacement')):
                    source=self.add(session.engine,control,seat=seat)
                    self.assertTrue(self.binding(session,source)['strict_capability_ready'])
                for source in (aura,steppe):self.assertTrue(self.binding(session,source)['strict_capability_ready'])
                self.prepare(session,mana=cost);self.checkpoint(session)
                self.cast(session,'A',aura,[body.ref],cost)
                if chosen:
                    self.assertEqual('replacement.order',self.resolve(session).kind)
                    option=next(r['id'] for r in session.packet('pilot:A',full=True)['decision']['ctx']['options'] if r['id'].endswith(':W'))
                    result=session.act('pilot:A',{'action_id':'choose','choices':{'replacement':option}});self.assertTrue(result.ok,result.summary)
                self.assertIsNone(self.resolve(session))
                self.assertEqual(('battlefield',body.object_id),(session.state.cards[aura.object_id].zone,session.state.cards[aura.object_id].attached_to))
                self.assertEqual(2+boost,session.engine._numeric_stat(body.object_id,'power'))
                if chosen:self.advance(session,lambda s:s.state.active_player=='B' and s.state.phase=='precombat_main')
                self.priority(session,land_seat)
                action=next(r for r in session.packet('pilot:'+land_seat,full=True)['decision']['ctx']['legal']['actions'] if r['id']=='play-land:'+steppe.ref)
                result=session.act('pilot:'+land_seat,{'action_id':action['id']});self.assertTrue(result.ok,result.summary)
                pending=self.resolve(session)
                if pending is not None and pending.kind=='semantic.target':
                    result=session.act('pilot:'+land_seat,{'action_id':'choose','targets':[body.ref]});self.assertTrue(result.ok,result.summary)
                    pending=self.resolve(session)
                self.assertIsNotNone(pending);self.assertEqual('semantic.choice',pending.kind)
                result=session.act('pilot:'+land_seat,{'action_id':'choose','choice':'W'});self.assertTrue(result.ok,result.summary)
                self.assertEqual('replacement.order',self.resolve(session).kind)
                self.assertEqual('state_based_zone_change',session.state.pending_decision.continuation['replacement_resume_kind'])
                self.assertEqual(['pilot:A'],list(session.pending_principals()))
                self.assertEqual(('battlefield',body.object_id),(session.state.cards[aura.object_id].zone,session.state.cards[aura.object_id].attached_to))
                self.assertEqual(2+boost,session.engine._numeric_stat(body.object_id,'power'))
                session=self.replay(session,load=True)
                option=session.packet('pilot:A',full=True)['decision']['ctx']['options'][0]['id']
                before=authoritative_state_hash(session.state)
                self.assertFalse(session.act('pilot:B',{'action_id':'choose','replacement':option}).ok)
                self.assertEqual(before,authoritative_state_hash(session.state))
                self.assertFalse(session.act('pilot:A',{'action_id':'choose','replacement':'not-offered'}).ok)
                self.assertEqual(before,authoritative_state_hash(session.state))
                result=session.act('pilot:A',{'action_id':'choose','replacement':option});self.assertTrue(result.ok,result.summary)
                self.assertIsNone(self.resolve(session))
                current=session.state.cards[aura.object_id]
                self.assertEqual('exile',current.zone);self.assertIsNone(current.attached_to)
                self.assertEqual(1,sum(current.counters.values()))
                self.assertEqual(('battlefield',2),(session.state.cards[body.object_id].zone,session.engine._numeric_stat(body.object_id,'power')))
                self.assertNotIn(aura.object_id,session.state.cards[body.object_id].attachments)
                self.private_replay(session,private)

    def test_actual_red_ward_blocks_red_targeting_and_prevents_nontargeted_red_damage(self):
        session=self.session(750321);body=self.add(session.engine,'Grizzly Bears')
        other=self.add(session.engine,'Grizzly Bears',seat='B',ref='unprotected-foreign-bear')
        ward=self.add(session.engine,'Red Ward',zone='hand');pyro=self.add(session.engine,'Pyroclasm',zone='hand')
        bolt=self.add(session.engine,'Lightning Bolt',seat='B',zone='hand')
        self.prepare(session,mana={'W':1,'R':1,'C':1});session.state.players['B'].mana_pool['R']=1;self.checkpoint(session)
        self.cast(session,'A',ward,[body.ref],{'W':1});self.assertIsNone(self.resolve(session))
        self.cast(session,'A',pyro,payment={'R':1,'C':1});session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual(('battlefield',0),(session.state.cards[body.object_id].zone,session.state.cards[body.object_id].marked_damage))
        self.assertEqual('graveyard',session.state.cards[other.object_id].zone)
        self.assertEqual('battlefield',session.state.cards[ward.object_id].zone)
        self.priority(session,'B');before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:B',{'action_id':'cast:'+bolt.ref,'targets':[body.ref],'pay':'auto'}).ok)
        self.assertEqual(before,authoritative_state_hash(session.state));self.replay(session)

    def test_actual_bounce_and_recast_binds_the_returned_aura_instead_of_its_old_incarnation(self):
        from copy import deepcopy
        from quorune.protection import ProtectionSource,ProtectionVerdict,protection_verdict
        session=self.session(750322);body=self.add(session.engine,'Grizzly Bears')
        ward=self.add(session.engine,'White Ward',zone='hand');blue=self.add(session.engine,'Boomerang',seat='B',zone='hand')
        self.prepare(session,mana={'W':2});session.state.players['B'].mana_pool['U']=2;self.checkpoint(session)
        self.cast(session,'A',ward,[body.ref],{'W':1});self.assertIsNone(self.resolve(session))
        old=session.state.cards[ward.object_id].logical_object_id
        old_data=deepcopy(session.engine._effective_card_data(session.state.cards[body.object_id]))
        self.cast(session,'B',blue,[ward.ref],{'U':2});session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual('hand',session.state.cards[ward.object_id].zone)
        self.assertFalse(any(v.get('kind')=='protection' for v in session.engine._effective_card_data(session.state.cards[body.object_id]).get('ability_fragments',())))
        self.cast(session,'A',session.state.cards[ward.object_id],[body.ref],{'W':1});self.assertIsNone(self.resolve(session))
        current=session.state.cards[ward.object_id];self.assertNotEqual(old,current.logical_object_id)
        self.assertEqual(('battlefield',body.object_id),(current.zone,current.attached_to))
        source=ProtectionSource(colors=frozenset({'W'}))
        self.assertIs(ProtectionVerdict.BLOCKED,protection_verdict(old_data,source,retaining_aura=current.logical_object_id))
        self.assertIs(ProtectionVerdict.ALLOWED,protection_verdict(session.engine._effective_card_data(session.state.cards[body.object_id]),source,retaining_aura=current.logical_object_id))
        self.replay(session)

    def test_actual_red_ward_rejects_a_red_blocker_without_preventing_the_attack(self):
        session=self.session(750323);body=self.add(session.engine,'Grizzly Bears');body.acquired_control_turn_count=-1
        blocker=self.add(session.engine,'Raging Goblin',seat='B')
        legal=self.add(session.engine,'Grizzly Bears',seat='B',ref='legal-green-blocker')
        ward=self.add(session.engine,'Red Ward',zone='hand')
        self.prepare(session,mana={'W':1});self.checkpoint(session)
        self.cast(session,'A',ward,[body.ref],{'W':1});self.assertIsNone(self.resolve(session))
        self.attack(session,{body.ref:'B'});self.blockers(session)
        self.assertFalse(session.engine._can_block(session.state.cards[body.object_id],session.state.cards[blocker.object_id])[0])
        self.assertTrue(session.engine._can_block(session.state.cards[body.object_id],session.state.cards[legal.object_id])[0])
        self.block(session,{blocker.ref:[body.ref]},valid=False)
        self.block(session,{legal.ref:[body.ref]})
        self.advance(session,lambda s:s.state.phase=='postcombat_main')
        self.assertEqual(40,session.state.players['B'].life)
        self.assertTrue(all(session.state.cards[c.object_id].zone=='graveyard' for c in (body,legal,ward)))
        self.assertEqual('battlefield',session.state.cards[blocker.object_id].zone)
        self.replay(session)
