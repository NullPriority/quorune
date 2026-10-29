"""Pinned CR101.2/107.6/122.1d/502.3: giver-owned untap prohibitions."""

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from test_qualified_zone_event_queries import query_record
import test_type_replacement_grants as native
import test_ring_native_actions as turns
from quorune.ability_fragments import ability_fragment_from_dict,ability_fragment_to_dict
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash,checkpoint_state
from quorune.rules.capabilities import load_default_capability_registry
from quorune.untap_restraint_model import AttachedUntapRestraintSpec,UNTAP_RESTRAINT_CAPABILITY as CAPABILITY
from scripts.build_test_database import build_fixture_database


ABSOLUTE="Enchanted creature loses all abilities and can't become untapped."
STEP="Enchanted permanent loses all abilities and doesn't untap during its controller's untap step."


def catalog_rules_state(state):
    """Catalog target discovery increments this existing telemetry counter only."""
    value=checkpoint_state(state)
    for player in value['players'].values():
        metrics=player['stats'].get('decision_optimization',{})
        if 'target_candidates_generated' in metrics:metrics['target_candidates_generated']=0
    return value


class AttachedUntapRestraintCompilerTests(unittest.TestCase):
    def test_complete_spanned_compound_clauses_have_separate_typed_owners(self):
        registry=load_default_capability_registry()
        for text,mode,types in ((ABSOLUTE,'all_untaps',('creature',)),(STEP,'controller_step',()),
                ("Enchanted artifact can't become untapped.",'all_untaps',('artifact',))):
            ir=compile_oracle_card(query_record(text),capability_registry=registry,capability_profile='commander_review')
            self.assertEqual('exact',ir.status,ir.material_residuals)
            node,=ir.faces[0].nodes
            self.assertEqual((text,0,len(text)),(node.text,node.span.start,node.span.end))
            self.assertIn(CAPABILITY,node.capability_dependencies)
            spec=ability_fragment_from_dict(node.handlers[-1]['fragment'])
            self.assertEqual((mode,types),(spec.mode,spec.types_all))
            self.assertEqual(2 if 'loses all abilities' in text else 1,len(node.handlers))
        for text in (ABSOLUTE+' Draw a card.',ABSOLUTE.replace('Enchanted creature','Chosen creatures'),
                ABSOLUTE.replace("can't become untapped","can't become untapped this turn"),
                STEP.replace('loses all abilities','has flying')):
            self.assertNotEqual('exact',compile_oracle_card(query_record(text),capability_registry=registry).status)

    def test_closed_fragment_codec_and_legacy_descriptor_reject_unknown_fields(self):
        from quorune.compiler.untap_step_templates import static_untap_step_handler
        from quorune.semantic_runtime.untap_steps import StaticUntapStepParticipationHandler
        from quorune.semantic_runtime.context import SemanticNodeError
        value=AttachedUntapRestraintSpec('all_untaps',('creature',))
        self.assertEqual(value,ability_fragment_from_dict(ability_fragment_to_dict(value)))
        raw=value.to_dict()
        for change in ({'mode':[]},{'mode':'sometimes'},{'schema_version':True},{'types_all':['vehicle']},
                {'types_all':['creature','land']},{'types_all':('creature',)},{'unknown':None}):
            with self.assertRaises(ValueError):AttachedUntapRestraintSpec.from_dict({**raw,**change})
        descriptor=static_untap_step_handler("Enchanted artifact doesn't untap during its controller's untap step.",source_name='Fixture')[1]
        descriptor['condition']['turn_relation']='any'
        with self.assertRaises(SemanticNodeError):StaticUntapStepParticipationHandler().validate(descriptor)

    def test_verified_rule_characteristic_and_step_withdrawals_fail_closed(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        declarations=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json');known=set(discover_tests(ROOT).values())
        for owner in (CAPABILITY,'continuous.characteristics.fixed_public_setting','untap.step.static_participation','variant.monarch.designate'):
            value=deepcopy(raw);next(r for r in value['capabilities'] if r['id']==owner).update(status='blocked',blockers=['untap rule owner withdrawn'])
            registry=CapabilityRegistry(value)
            index=build_index(registry_value=value,declaration_source=declarations,discovered_test_ids=known)
            registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
            ir=compile_oracle_card(query_record(ABSOLUTE),capability_registry=registry,capability_profile='commander_review')
            self.assertEqual(owner=='variant.monarch.designate',ir.status=='exact',owner)


class AttachedUntapRestraintNativeTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=native.TypeReplacementGrantNativeTests.session
    add=native.TypeReplacementGrantNativeTests.add
    prepare=native.TypeReplacementGrantNativeTests.prepare
    cast=native.TypeReplacementGrantNativeTests.cast
    cast_for=native.TypeReplacementGrantNativeTests.cast_for
    action=native.TypeReplacementGrantNativeTests.action
    priority=native.TypeReplacementGrantNativeTests.priority
    checkpoint=native.TypeReplacementGrantNativeTests.checkpoint
    resolve=native.TypeReplacementGrantNativeTests.resolve
    replay=native.TypeReplacementGrantNativeTests.replay
    assert_closed=native.TypeReplacementGrantNativeTests.assert_closed
    until=turns.RingNativeActionTests.until

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'attached-untap.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/attached-untap-restraint-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Attached untap',[DeckEntry('Isamaru, Hound of Konda',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Isamaru, Hound of Konda'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()

    def twiddle(self,session,spell,target):
        self.cast(session,'B',spell,[target.ref])
        self.assertEqual('semantic.choice',self.resolve(session).kind)
        result=session.act('pilot:B',{'action_id':'choose','choice':'untap'});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(session))

    def fixture(self,seed,name,target_name,twiddle_count):
        session=self.session(seed)
        for oid in list(session.state.players['A'].zones['hand'])[4:]:session.engine.move_card(oid,'library',log=False)
        aura=self.add(session.engine,name,zone='hand');target=self.add(session.engine,target_name,seat='B',ref='untap-subject')
        target.counters['stun']=1
        removal=self.add(session.engine,'Disenchant',zone='hand')
        self.add(session.engine,'Plains',ref='giver-white');self.add(session.engine,'Wastes',ref='giver-colorless')
        spells=[]
        for index in range(twiddle_count):
            self.add(session.engine,'Island',seat='B',ref='manual-blue-'+str(index))
            spells.append(self.add(session.engine,'Twiddle',seat='B',zone='hand',ref='manual-untap-'+str(index)))
        private=self.add(session.engine,'Twiddle',seat='D',zone='hand',ref='private-untap')
        self.assert_closed(session,aura);self.prepare(session,mana={'C':3 if name=='Stop Cold' else 2,'U':1});self.checkpoint(session)
        return session,aura,target,removal,spells,private

    def test_actual_step_only_auras_block_controller_step_but_allow_manual_untapping(self):
        for index,(name,target_name) in enumerate((('Stop Cold','Llanowar Elves'),('Flood the Engine',"Cultivator's Caravan"))):
            with self.subTest(aura=name):
                session,aura,target,removal,spells,private=self.fixture(205620010+index,name,target_name,2)
                self.cast(session,'A',aura,[target.ref]);self.assertIsNone(self.resolve(session))
                self.assertTrue(session.state.cards[target.object_id].tapped)
                self.assertFalse(session.engine._activated_abilities(session.state.cards[target.object_id]))
                self.until(session,lambda s:s.state.active_player=='B' and s.state.step=='upkeep')
                current=session.state.cards[target.object_id];self.assertTrue(current.tapped);self.assertEqual(1,current.counters['stun'])
                self.twiddle(session,spells[0],target)
                current=session.state.cards[target.object_id];self.assertTrue(current.tapped);self.assertEqual(0,current.counters.get('stun',0))
                self.twiddle(session,spells[1],target);self.assertFalse(session.state.cards[target.object_id].tapped)
                self.cast(session,'A',removal,[aura.ref]);self.assertIsNone(self.resolve(session))
                self.assertTrue(session.engine._activated_abilities(session.state.cards[target.object_id]))
                for seat in 'ABC':self.assertNotIn(private.ref,json.dumps(session.packet('pilot:'+seat,full=True)))
                self.replay(session,load=True)

    def test_actual_frozen_blocks_effect_and_physical_step_before_stun_until_source_leaves(self):
        session,aura,target,removal,spells,private=self.fixture(205620020,'Frozen in Ice','Llanowar Elves',3)
        self.cast(session,'A',aura,[target.ref]);self.assertIsNone(self.resolve(session))
        self.twiddle(session,spells[0],target)
        current=session.state.cards[target.object_id];self.assertTrue(current.tapped);self.assertEqual(1,current.counters['stun'])
        self.until(session,lambda s:s.state.active_player=='B' and s.state.step=='upkeep')
        current=session.state.cards[target.object_id];self.assertTrue(current.tapped);self.assertEqual(1,current.counters['stun'])
        self.cast(session,'A',removal,[aura.ref]);self.assertIsNone(self.resolve(session))
        self.twiddle(session,spells[1],target)
        current=session.state.cards[target.object_id];self.assertTrue(current.tapped);self.assertEqual(0,current.counters.get('stun',0))
        self.twiddle(session,spells[2],target);self.assertFalse(session.state.cards[target.object_id].tapped)
        for seat in 'ABC':self.assertNotIn(private.ref,json.dumps(session.packet('pilot:'+seat,full=True)))
        self.replay(session,load=True)

    def test_actual_later_mantle_grant_obeys_shared_untap_cost_permission_and_atomic_rejection(self):
        from quorune.rules.action_catalog import build_priority_action_catalog
        from quorune.rules.activation.model import ActivationProposalError,ActivationProposalRequest
        from quorune.rules.activation.proposal import build_activation_proposal
        session=self.session(205620030)
        target=self.add(session.engine,'Llanowar Elves',seat='B',ref='untap-cost-subject');target.tapped=True;target.acquired_control_turn_count=-1
        aura=self.add(session.engine,'Frozen in Ice',ref='untap-cost-prohibition')
        equipment=self.add(session.engine,'Umbral Mantle',seat='B',ref='later-untap-cost-grant')
        for giver in (aura,equipment):giver.attached_to=target.object_id;target.attachments.append(giver.object_id)
        removal=self.add(session.engine,'Disenchant',seat='B',zone='hand')
        self.assert_closed(session,aura);self.assert_closed(session,equipment)
        self.prepare(session,actor='B',active='B',mana={'C':4,'W':1})
        ability=next(a for a in session.engine._activated_abilities(session.state.cards[target.object_id]) if a.untap_source)
        command={'action_id':'activate:'+target.ref+':'+ability.ability_id,'pay':'auto'}
        actions=session.packet('pilot:B',full=True)['decision']['ctx']['legal']['actions']
        self.assertNotIn(command['action_id'],[a['id'] for a in actions])
        rules_before=catalog_rules_state(session.state)
        self.assertEqual(build_priority_action_catalog.__wrapped__(session.engine,'B'),build_priority_action_catalog(session.engine,'B'))
        self.assertEqual(rules_before,catalog_rules_state(session.state))
        self.checkpoint(session)
        before=authoritative_state_hash(session.state)
        with self.assertRaises(ActivationProposalError) as rejected:
            build_activation_proposal(session.engine,ActivationProposalRequest('B',target.ref,('battlefield',),ability.ability_id))
        self.assertEqual('untap_cost_prohibited',rejected.exception.reason)
        self.assertEqual(before,authoritative_state_hash(session.state))
        self.assertFalse(session.act('pilot:A',command).ok);self.assertEqual(before,authoritative_state_hash(session.state))
        self.assertFalse(session.act('pilot:B',command).ok);self.assertEqual(before,authoritative_state_hash(session.state))
        self.cast_for(session,'B',removal,[aura.ref],reserve={'C':3});self.assertIsNone(self.resolve(session))
        self.priority(session,'B')
        actions=session.packet('pilot:B',full=True)['decision']['ctx']['legal']['actions'];self.assertIn(command['action_id'],[a['id'] for a in actions])
        result=session.act('pilot:B',command);self.assertTrue(result.ok,result.summary)
        self.assertFalse(session.state.cards[target.object_id].tapped);self.assertEqual(0,sum(session.state.players['B'].mana_pool.values()))
        self.assertIsNone(self.resolve(session));self.assertEqual(3,session.engine._numeric_stat(target.object_id,'power'))
        self.replay(session,load=True)
        rules_before=catalog_rules_state(session.state)
        self.assertEqual(build_priority_action_catalog.__wrapped__(session.engine,'B'),build_priority_action_catalog(session.engine,'B'))
        self.assertEqual(rules_before,catalog_rules_state(session.state))

    def test_current_giver_and_attachment_presence_govern_absolute_any_turn_participation(self):
        from quorune.rules.untap_permissions import untap_is_permitted
        from quorune.semantic_runtime.untap_steps import current_untap_step_plan
        from quorune.tap_state import set_permanent_tapped
        session=self.session(205620035);target=self.add(session.engine,'Llanowar Elves',seat='B');target.tapped=True
        aura=self.add(session.engine,'Frozen in Ice');aura.attached_to=target.object_id;target.attachments.append(aura.object_id)
        self.assertFalse(untap_is_permitted(session.engine,target))
        for seat in 'AB':self.assertIn(target.object_id,current_untap_step_plan(session.engine,seat).prohibited_object_ids)
        self.assertTrue(any(isinstance(f,AttachedUntapRestraintSpec) for f in session.engine._effective_ability_fragments(aura)))
        self.assertFalse(any(isinstance(f,AttachedUntapRestraintSpec) for f in session.engine._effective_ability_fragments(target)))
        reader=session.engine._effective_ability_fragments
        with patch.object(session.engine,'_effective_ability_fragments',side_effect=lambda card:() if card.object_id==aura.object_id else reader(card)):
            self.assertTrue(untap_is_permitted(session.engine,target))
        aura.phased_out=True;self.assertTrue(untap_is_permitted(session.engine,target));aura.phased_out=False
        aura.attached_to=None;self.assertTrue(untap_is_permitted(session.engine,target));aura.attached_to=target.object_id
        self.assertFalse(untap_is_permitted(session.engine,target))
        set_permanent_tapped(session.engine,target.ref,actor='B',tapped=False,reason='undo existing mana operation',revert=True,log=False)
        self.assertFalse(target.tapped)

    def test_runtime_restraint_owner_cannot_be_omitted(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        session=self.session(205620040);source=self.add(session.engine,'Frozen in Ice');checked=0
        for program in session.engine.semantics.programs_for_oracle(source.oracle_id):
            if CAPABILITY not in program.capability_dependencies:continue
            checked+=1;deps=tuple(cap for cap in program.capability_dependencies if cap!=CAPABILITY)
            changed=replace(program,capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
            bound=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review')
            self.assertFalse(bound['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+CAPABILITY,bound['blockers'])
        self.assertGreater(checked,0)

    def test_omitted_absolute_permission_mutant_is_killed(self):
        from quorune.rules.untap_permissions import untap_is_permitted
        from quorune.tap_state import untap_permanent
        session=self.session(205620050);target=self.add(session.engine,'Llanowar Elves',seat='B');target.tapped=True
        aura=self.add(session.engine,'Frozen in Ice');aura.attached_to=target.object_id;target.attachments.append(aura.object_id)
        def witness():
            self.assertFalse(untap_permanent(session.engine,session.state.cards[target.object_id],actor='B',reason='bounded blocked attempt'))
        witness();self.assertFalse(untap_is_permitted(session.engine,target))
        with patch('quorune.rules.untap_permissions.untap_is_permitted',return_value=True),self.assertRaises(AssertionError):witness()
