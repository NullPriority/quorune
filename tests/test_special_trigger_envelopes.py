"""CR603/701: closed special results keep their actual event dependencies."""
from copy import deepcopy
from dataclasses import replace
import json,unittest
from types import SimpleNamespace
from unittest.mock import patch
from common import ROOT
from test_qualified_zone_event_queries import query_record
import special_trigger_fixture as fixtures
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry

class SpecialTriggerCompilerTests(unittest.TestCase):
    def test_special_body_keeps_trigger_transport_and_invalid_body_stays_rejected(self):
        from quorune.rules.capabilities import capability_dependencies_for_node
        from quorune.rules.special_node_shapes import special_result_trigger_runtime_dependencies
        registry=load_default_capability_registry()
        for text,owner in (('Whenever a creature you control dies, manifest the top card of your library.','trigger.event.normalized_zone_change'),('Whenever this creature attacks, you lose half your life, rounded up.','trigger.event.normalized_self_attack'),('At the beginning of your upkeep, manifest dread.','keyword_action.manifest_dread')):
            ir=compile_oracle_card(query_record(text),capability_registry=registry);self.assertEqual('exact',ir.status,ir.material_residuals)
            self.assertTrue({'trigger.effect.fixed_event','trigger.placement.apnap',owner}<=set(ir.faces[0].nodes[0].capability_dependencies))
        effect={'op':'manifest_card','player':'$controller','mode':'top'};mechanics=('manifest','cr-701-manifest','fixed-typed-event-effect-trigger','cr-603-handling-triggered-abilities','trigger-event-normalized-zone-change')
        for change in ({'mode':'unknown'},{'player':'B'},{'extra':1}):self.assertEqual((),capability_dependencies_for_node(effects=({**effect,**change},),target_schema=None,mechanic_ids=mechanics))
        self.assertEqual(('keyword_action.manifest.card',),special_result_trigger_runtime_dependencies(SimpleNamespace(event='resolve',effects=(effect,))))
        self.assertNotEqual('exact',compile_oracle_card(query_record('When this creature dies, manifest dread. Rewrite history.'),capability_registry=registry).status)

    def test_verified_event_result_and_condition_withdrawal_keep_unrelated_spell(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        original=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text());declarations=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json');known=set(discover_tests(ROOT).values())
        for owner,text in (('trigger.effect.fixed_event','At the beginning of your upkeep, manifest dread.'),('keyword_action.manifest_dread','When this creature dies, manifest dread.'),('life.loss.half_current_player','Whenever this creature attacks, you lose half your life, rounded up.'),('trigger.condition.fixed_public_state','At the beginning of your second main phase, if this creature is tapped, manifest dread.')):
            raw=deepcopy(original);next(r for r in raw['capabilities'] if r['id']==owner).update(status='blocked',blockers=['required special trigger owner withdrawn'])
            registry=CapabilityRegistry(raw);index=build_index(registry_value=raw,declaration_source=declarations,discovered_test_ids=known);registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
            self.assertNotEqual('exact',compile_oracle_card(query_record(text),capability_registry=registry).status)
            self.assertEqual('exact',compile_oracle_card(replace(query_record('Draw a card.'),type_line='Sorcery'),capability_registry=registry).status)
            if owner=='trigger.effect.fixed_event':self.assertEqual('exact',compile_oracle_card(query_record('When this creature dies, manifest the top card of your library.'),capability_registry=registry).status)

    def test_omitted_special_trigger_envelope_mutant_is_killed(self):
        def witness():self.assertEqual('exact',compile_oracle_card(query_record('At the beginning of your upkeep, manifest dread.'),capability_registry=load_default_capability_registry()).status)
        witness()
        with patch('quorune.rules.special_node_shapes.special_trigger_envelope_capabilities',side_effect=lambda dependencies,mechanics:dependencies),self.assertRaises(AssertionError):witness()


class SpecialTriggerNativeTests(fixtures.SpecialTriggerFixture,unittest.TestCase):
    def test_actual_soul_strike_uses_enchanted_death_and_aura_controllers_private_library(self):
        for dies in (True,False):
            with self.subTest(dies=dies):
                session=self.session(610650001+dies);source=self.add(session.engine,'Soul-Strike Technique',zone='hand');body=self.add(session.engine,'Grizzly Bears',seat='B');top=self.add(session.engine,'Viashino Firstblade',zone='library');spell=self.add(session.engine,'Murder' if dies else 'Unsummon',zone='hand')
                for name in ('Plains','Mountain','Wastes'):self.add(session.engine,name)
                self.top(session,[top]);self.assert_closed(session,source);self.prepare(session,mana={'C':2,'W':1,'B':2,'U':1});self.checkpoint(session);self.cast_for(session,'A',source,[body.ref],reserve={'C':1,'B':2,'U':1});self.settle(session)
                self.assertEqual(3,session.engine._numeric_stat(body.object_id,'power'));self.assertIn('vigilance',session.engine._combat_keywords(session.state.cards[body.object_id]));other_library=len(session.state.players['B'].zones['library'])
                self.cast(session,'A',spell,[body.ref]);self.settle(session);self.assertEqual('graveyard',session.state.cards[source.object_id].zone);self.assertEqual(other_library,len(session.state.players['B'].zones['library']))
                current=session.state.cards[top.object_id];self.assertEqual('battlefield' if dies else 'library',current.zone)
                if dies:
                    self.assertTrue(current.face_down);self.assertEqual(2,session.engine._numeric_stat(current.object_id,'power'))
                    for seat in 'BCD':self.assertNotIn('Viashino Firstblade',json.dumps(session.packet('pilot:'+seat,full=True)))
                    self.priority(session,'A');offered=next(a for a in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a['id']=='turn-face-up:'+current.ref+':manifest');command={'action_id':offered['id'],'pay':'auto'};before=authoritative_state_hash(session.state);self.assertFalse(session.act('pilot:B',command).ok);self.assertEqual(before,authoritative_state_hash(session.state));result=session.act('pilot:A',command);self.assertTrue(result.ok,result.summary);self.assertFalse(session.state.cards[current.object_id].face_down);self.assertEqual(2,session.engine._numeric_stat(current.object_id,'power'));self.assertFalse(session.state.stack)
                self.replay(session,load=True)

    def test_actual_survival_checks_tapped_at_discovery_and_again_on_resolution(self):
        for mode in ('untapped','untap_response','tapped'):
            with self.subTest(mode=mode):
                session=self.session(610650010+len(mode));source=self.add(session.engine,'Defiant Survivor',zone='hand');tap=self.add(session.engine,'Twiddle',zone='hand');untap=self.add(session.engine,'Twiddle',seat='B',zone='hand',ref='survival-response-untap');self.add(session.engine,'Island',seat='B');first=self.add(session.engine,'Memnite',zone='library');second=self.add(session.engine,'Shock',zone='library');self.top(session,[first,second]);self.assert_closed(session,source)
                self.prepare(session,mana={'C':2,'G':1,'U':1});self.checkpoint(session);self.cast_for(session,'A',source,reserve={'U':1});self.settle(session)
                if mode!='untapped':
                    self.cast(session,'A',tap,[source.ref]);self.assertEqual('semantic.choice',self.resolve(session).kind);result=session.act('pilot:A',{'action_id':'choose','choice':'tap'});self.assertTrue(result.ok,result.summary);self.settle(session)
                self.until(session,lambda s:s.state.phase=='postcombat_main' and s.state.pending_decision is not None);self.assertEqual(0 if mode=='untapped' else 1,self.trigger_count(session,source))
                if mode=='untap_response':
                    self.cast(session,'B',untap,[source.ref]);self.assertEqual('semantic.choice',self.resolve(session).kind);result=session.act('pilot:B',{'action_id':'choose','choice':'untap'});self.assertTrue(result.ok,result.summary)
                if mode=='tapped':
                    self.assertEqual('semantic.choice',self.resolve(session).kind)
                    for seat in 'BCD':
                        for card in (first,second):self.assertNotIn(card.ref,json.dumps(session.packet('pilot:'+seat,full=True)))
                    session=self.choose_dread(session,first,second)
                else:
                    self.settle(session);self.assertEqual('library',session.state.cards[first.object_id].zone);self.assertEqual('library',session.state.cards[second.object_id].zone)
                self.replay(session,load=True)

    def test_actual_oculus_pays_six_owned_graveyard_cards_then_triggers_each_opponent_upkeep(self):
        session=self.session(610650030);source=self.add(session.engine,'Abhorrent Oculus',zone='hand');cost=[self.add(session.engine,'Grizzly Bears',zone='graveyard',ref='oculus-cost-'+str(i)) for i in range(6)];foreign=self.add(session.engine,'Grizzly Bears',seat='B',zone='graveyard',ref='foreign-oculus-cost');bounce=self.add(session.engine,'Unsummon',seat='B',zone='hand');self.add(session.engine,'Island',seat='B');first=self.add(session.engine,'Memnite',zone='library');second=self.add(session.engine,'Shock',zone='library');self.top(session,[first,second]);self.assert_closed(session,source)
        self.prepare(session,mana={'C':2,'U':1});self.checkpoint(session);offered=self.action(session,'A',source);before=authoritative_state_hash(session.state)
        for refs in ([c.ref for c in cost[:5]],[foreign.ref,*[c.ref for c in cost[:5]]],[cost[0].ref]*6):
            self.assertFalse(session.act('pilot:A',{'action_id':offered['id'],'pay':'auto','exile_cards':refs}).ok);self.assertEqual(before,authoritative_state_hash(session.state))
        result=session.act('pilot:A',{'action_id':offered['id'],'pay':'auto','exile_cards':[c.ref for c in cost]});self.assertTrue(result.ok,result.summary);self.assertTrue(all(session.state.cards[c.object_id].zone=='exile' for c in cost));self.assertEqual('graveyard',session.state.cards[foreign.object_id].zone);self.settle(session);self.replay(session,load=True)
        for seat in 'ABCD':
            self.begin_prepared_turn(session,seat);self.until(session,lambda s:s.state.step=='upkeep' and s.state.pending_decision is not None);self.assertEqual(0 if seat=='A' else 1,self.trigger_count(session,source))
            if seat=='A':continue
            library=session.state.players['A'].zones['library'];top=[session.state.cards[oid] for oid in reversed(library[-2:])];other=len(session.state.players[seat].zones['library'])
            if seat=='D':self.cast(session,'B',bounce,[source.ref]);self.resolve_top(session);self.assertEqual('hand',session.state.cards[source.object_id].zone)
            self.assertEqual('semantic.choice',self.resolve(session).kind);session=self.choose_dread(session,top[0],top[1]);self.assertEqual(other,len(session.state.players[seat].zones['library']));self.replay(session,load=True)

    def test_special_trigger_runtime_owners_are_inferred_after_annotations_are_removed(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.rules.special_node_shapes import special_result_trigger_runtime_dependencies
        session=self.session(610650040);checked=0;removed=0
        for name in ('Soul-Strike Technique','Defiant Survivor','Ebonblade Reaper','Abhorrent Oculus'):
            source=self.add(session.engine,name)
            for program in session.engine.semantics.programs_for_oracle(source.oracle_id):
                required=special_result_trigger_runtime_dependencies(program)
                if not required:continue
                checked+=1
                for owner in required:
                    deps=tuple(c for c in program.capability_dependencies if c!=owner);changed=replace(program,coverage=[],capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
                    bound=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review');self.assertFalse(bound['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+owner,bound['blockers']);removed+=1
        self.assertEqual(5,checked);self.assertGreaterEqual(removed,21)

    def test_omitted_special_runtime_trigger_inference_mutant_is_killed(self):
        with patch('quorune.rules.special_node_shapes.special_result_trigger_runtime_dependencies',return_value=()),self.assertRaises(AssertionError):self.test_special_trigger_runtime_owners_are_inferred_after_annotations_are_removed()
