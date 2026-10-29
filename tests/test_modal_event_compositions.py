"""CR603.3c: typed modal event wrappers retain independent branch ownership."""
from copy import deepcopy
from dataclasses import replace
import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from common import ROOT
from test_qualified_zone_event_queries import query_record
import inherited_composition_fixture as actions
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.oracle_ir import compile_oracle_card
from scripts.build_test_database import build_fixture_database

MODAL='choice.modal.fixed_nonrepeating'
TEXT='Whenever this creature attacks or blocks, choose one —\n• Create a 3/3 green Beast creature token.\n• You gain 3 life.\n• Draw a card.'


class ModalEventCompilerTests(unittest.TestCase):
    def test_known_outer_event_wrappers_preserve_every_closed_branch(self):
        from quorune.rules.modal_node_capabilities import fixed_modal_capability_dependencies
        ir=compile_oracle_card(query_record(TEXT),capability_registry=load_default_capability_registry());self.assertEqual('exact',ir.status,ir.material_residuals)
        node,=ir.faces[0].nodes;self.assertEqual([],list(node.effects));self.assertEqual((TEXT,0,len(TEXT)),(node.text,node.span.start,node.span.end));self.assertEqual(['mode_1','mode_2','mode_3'],list(node.target_schema['modes']))
        self.assertTrue({MODAL,'trigger.event.normalized_public_action','trigger.subscription.fixed_multi_event'}<=set(node.capability_dependencies))
        self.assertIn(MODAL,fixed_modal_capability_dependencies(effects=node.effects,target_schema=node.target_schema,mechanics=node.mechanics))
        self.assertEqual((),fixed_modal_capability_dependencies(effects=node.effects,target_schema=node.target_schema,mechanics=(*node.mechanics,'unknown-wrapper')))
        broken=deepcopy(node.target_schema);broken['modes']['mode_3']['effects']=[{'op':'unknown'}]
        self.assertEqual((),fixed_modal_capability_dependencies(effects=node.effects,target_schema=broken,mechanics=node.mechanics))

    def test_verified_child_or_event_owner_withdrawal_rejects_the_complete_modal_trigger(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        original=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text());decl=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json');known=set(discover_tests(ROOT).values())
        for cap in (MODAL,'trigger.event.normalized_public_action','zone.draw.library_to_hand'):
            raw=deepcopy(original);next(r for r in raw['capabilities'] if r['id']==cap).update(status='blocked',blockers=['independent branch/event authority withdrawn'])
            registry=CapabilityRegistry(raw);index=build_index(registry_value=raw,declaration_source=decl,discovered_test_ids=known);registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
            self.assertNotEqual('exact',compile_oracle_card(query_record(TEXT),capability_registry=registry).status)
            self.assertEqual('exact',compile_oracle_card(replace(query_record('You gain 1 life.'),type_line='Sorcery'),capability_registry=registry).status)

    def test_missing_event_wrapper_mutant_is_killed(self):
        import quorune.rules.modal_capability_shapes as owner
        def witness():self.assertEqual('exact',compile_oracle_card(query_record(TEXT),capability_registry=load_default_capability_registry()).status)
        witness()
        with patch.object(owner,'FIXED_MODAL_WRAPPER_MECHANICS',owner.FIXED_MODAL_WRAPPER_MECHANICS-{'trigger-event-normalized-public-action'}),self.assertRaises(AssertionError):witness()


class ModalEventNativeTests(actions.InheritedNativeFixture,unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'modal-events.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/modal-event-composition-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry();cls.deck=DeckDefinition('Modal event composition',[DeckEntry('Isamaru, Hound of Konda',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Isamaru, Hound of Konda'])

    def test_runtime_event_mode_and_child_requirements_survive_removed_coverage(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        session=self.session(603647090);checked=0
        for name,event_owner in (('Elder Gargaroth','trigger.event.normalized_public_action'),('Relic Bind','trigger.event.normalized_public_action'),('Radagast, Wizard of Wilds','trigger.event.normalized_spell_cast'),('Cosmogrand Zenith','trigger.event.normalized_spell_cast'),('Scaretiller','trigger.event.normalized_public_action')):
            source=self.add(session.engine,name)
            for program in session.engine.semantics.programs_for_oracle(source.oracle_id):
                if MODAL not in program.capability_dependencies:continue
                owners=[MODAL,event_owner]
                if name=='Elder Gargaroth':owners.append('zone.draw.library_to_hand')
                for cap in owners:
                    deps=tuple(c for c in program.capability_dependencies if c!=cap);changed=replace(program,coverage=[],capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
                    result=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review');self.assertFalse(result['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+cap,result['blockers']);checked+=1
        self.assertEqual(11,checked)

    def test_missing_runtime_event_inference_mutant_is_killed(self):
        with patch('quorune.rules.modal_node_capabilities.modal_event_runtime_dependencies',return_value=()),self.assertRaises(AssertionError):
            self.test_runtime_event_mode_and_child_requirements_survive_removed_coverage()

    def choose_mode(self,session,seat,mode,targets=()):
        self.until(session,lambda s:s.state.pending_decision is not None and s.state.pending_decision.kind=='semantic.target')
        command={'action_id':'choose','modes':[mode],'targets':list(targets)};before=authoritative_state_hash(session.state);foreign='D' if seat!='D' else 'C'
        self.assertFalse(session.act('pilot:'+foreign,command).ok);self.assertEqual(before,authoritative_state_hash(session.state));result=session.act('pilot:'+seat,command);self.assertTrue(result.ok,result.summary)
        self.assertEqual('priority',session.state.pending_decision.kind)

    def test_actual_elder_modes_are_chosen_before_priority_and_survive_source_departure(self):
        for mode in ('mode_1','mode_3'):
            with self.subTest(mode=mode):
                session=self.session(603647001+len(mode));source=self.add(session.engine,'Elder Gargaroth',zone='hand');self.add(session.engine,'Fervor');bounce=self.add(session.engine,'Unsummon',seat='B',zone='hand');self.add(session.engine,'Island',seat='B')
                self.assert_closed(session,source);self.prepare(session,mana={'C':3,'G':2});self.checkpoint(session);self.cast(session,'A',source);self.settle(session);self.attack(session,[(source,'B')]);self.assertFalse(session.state.cards[source.object_id].tapped)
                hand=len(session.state.players['A'].zones['hand']);life=session.state.players['A'].life;self.choose_mode(session,'A',mode)
                item=next(i for i in session.state.stack if i.source_object_id==source.object_id);self.assertEqual([mode],list(item.modes))
                self.cast(session,'B',bounce,[source.ref]);self.resolve_top(session);session=self.replay(session,load=True);self.settle(session)
                tokens=[c for c in session.state.cards.values() if c.is_token and c.zone=='battlefield'];self.assertEqual(1 if mode=='mode_1' else 0,len(tokens));self.assertEqual(hand+1+(1 if mode=='mode_3' else 0),len(session.state.players['A'].zones['hand']));self.assertEqual(life,session.state.players['A'].life)
                if tokens:
                    token=tokens[0];self.assertEqual((3,3),tuple(session.engine._numeric_stat(token.object_id,k) for k in ('power','toughness')));self.assertNotIn(token.object_id,session.state.combat.attackers);self.assertFalse(any(token.object_id in ids for ids in session.state.combat.blockers.values()))
                self.replay(session,load=True)

    def test_actual_relic_bind_uses_aura_controller_and_only_the_selected_mode_target(self):
        for mode in ('mode_1','mode_2'):
            with self.subTest(mode=mode):
                session=self.session(603647010+len(mode));source=self.add(session.engine,'Relic Bind',zone='hand');artifact=self.add(session.engine,'Sol Ring',seat='B');creature=self.add(session.engine,'Grizzly Bears',seat='B');remove=self.add(session.engine,'Disenchant',seat='B',zone='hand');self.add(session.engine,'Plains',seat='B')
                self.assert_closed(session,source);self.prepare(session,mana={'C':2,'U':1});self.checkpoint(session);self.cast(session,'A',source,[artifact.ref]);self.settle(session);self.priority(session,'B')
                mana=next(a for a in session.packet('pilot:B',full=True)['decision']['ctx']['legal']['actions'] if a['id'].startswith('activate:'+artifact.ref+':'));result=session.act('pilot:B',{'action_id':mana['id'],'pay':'auto'});self.assertTrue(result.ok,result.summary)
                self.assertEqual('semantic.target',session.state.pending_decision.kind);before=authoritative_state_hash(session.state);self.assertFalse(session.act('pilot:A',{'action_id':'choose','modes':[mode],'targets':[creature.ref]}).ok);self.assertEqual(before,authoritative_state_hash(session.state))
                self.choose_mode(session,'A',mode,['C']);life=session.state.players['C'].life
                if mode=='mode_1':self.cast(session,'B',remove,[source.ref]);self.resolve_top(session)
                session=self.replay(session,load=True);self.settle(session);self.assertEqual(life+(-1 if mode=='mode_1' else 1),session.state.players['C'].life);self.replay(session,load=True)

    def test_actual_radagast_uses_cast_mana_value_and_keeps_queued_bird_mode(self):
        session=self.session(603647020);source=self.add(session.engine,'Radagast, Wizard of Wilds');spell=self.add(session.engine,'Blaze',zone='hand');bounce=self.add(session.engine,'Unsummon',zone='hand');self.add(session.engine,'Island')
        self.assert_closed(session,source);self.prepare(session,mana={'C':4,'R':1});self.checkpoint(session);offered=self.action(session,'A',spell)
        result=session.act('pilot:A',{'action_id':offered['id'],'x':4,'targets':['B'],'pay':'auto'});self.assertTrue(result.ok,result.summary);self.choose_mode(session,'A','mode_2')
        self.cast(session,'A',bounce,[source.ref]);self.resolve_top(session);self.assertEqual('hand',session.state.cards[source.object_id].zone)
        session=self.replay(session,load=True);self.settle(session);tokens=[c for c in session.state.cards.values() if c.is_token and c.zone=='battlefield'];self.assertEqual(1,len(tokens));token=tokens[0]
        self.assertEqual((2,2),tuple(session.engine._numeric_stat(token.object_id,k) for k in ('power','toughness')));self.assertIn('flying',session.engine._combat_keywords(token));self.assertEqual(['U'],session.engine._effective_card_data(token)['colors']);self.assertEqual(36,session.state.players['B'].life);self.replay(session,load=True)

    def test_actual_zenith_counter_mode_resolves_before_the_second_creature_spell(self):
        session=self.session(603647030);source=self.add(session.engine,'Cosmogrand Zenith',zone='hand');body=self.add(session.engine,'Grizzly Bears',zone='hand');self.assert_closed(session,source)
        self.prepare(session,mana={'C':3,'W':1,'G':1});self.checkpoint(session);self.cast_for(session,'A',source,reserve={'C':1,'G':1});self.settle(session);self.assertEqual({},session.state.cards[source.object_id].counters)
        self.cast(session,'A',body);self.choose_mode(session,'A','mode_2');self.resolve_top(session)
        self.assertEqual('stack',session.state.cards[body.object_id].zone);self.assertEqual(1,session.state.cards[source.object_id].counters.get('+1/+1'));session=self.replay(session,load=True);self.settle(session)
        self.assertEqual({},session.state.cards[body.object_id].counters);self.assertFalse([c for c in session.state.cards.values() if c.is_token and c.zone=='battlefield']);self.replay(session,load=True)

    def test_actual_scaretiller_keeps_hand_entry_private_and_graveyard_mode_targeted(self):
        for mode in ('mode_1','mode_2'):
            with self.subTest(mode=mode):
                session=self.session(603647040+len(mode));source=self.add(session.engine,'Scaretiller');land=self.add(session.engine,'Mountain',zone='hand' if mode=='mode_1' else 'graveyard');wrong=self.add(session.engine,'Mountain',seat='B',zone='graveyard',ref='scare-foreign-land');tap=self.add(session.engine,'Twiddle',zone='hand');self.add(session.engine,'Island');self.assert_closed(session,source)
                self.prepare(session);self.checkpoint(session);self.cast(session,'A',tap,[source.ref]);self.assertEqual('semantic.choice',self.resolve(session).kind);result=session.act('pilot:A',{'action_id':'choose','choice':'tap'});self.assertTrue(result.ok,result.summary)
                if mode=='mode_2':
                    before=authoritative_state_hash(session.state);self.assertFalse(session.act('pilot:A',{'action_id':'choose','modes':[mode],'targets':[wrong.ref]}).ok);self.assertEqual(before,authoritative_state_hash(session.state))
                self.choose_mode(session,'A',mode,[land.ref] if mode=='mode_2' else []);plays=session.state.players['A'].land_plays_remaining;decision=self.resolve(session)
                if mode=='mode_1':
                    self.assertIsNotNone(decision);self.assertIsNone(session.packet('pilot:B',full=True)['decision']);result=session.act('pilot:A',{'action_id':'choose','card':land.ref});self.assertTrue(result.ok,result.summary)
                session=self.replay(session,load=True);self.settle(session);self.assertEqual('battlefield',session.state.cards[land.object_id].zone);self.assertTrue(session.state.cards[land.object_id].tapped);self.assertEqual(plays,session.state.players['A'].land_plays_remaining);self.assertEqual('graveyard',session.state.cards[wrong.object_id].zone);self.replay(session,load=True)
