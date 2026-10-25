from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import json,tempfile,unittest
from unittest.mock import patch
from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantic_runtime.hand_visibility import HAND_VISIBILITY_CAPABILITY as CAP
from scripts.build_test_database import build_fixture_database
import test_myrel_composition as controls
import test_ability_label_composition as journeys
import test_fixed_copy_type_exceptions as copies
from quorune.rules.evidence_storage import read_evidence_document

NAMES=('Revelation','Wandering Eye','Telepathy',"Seer's Vision")


class PersistentHandVisibilityTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=controls.MyrelCompositionTests.session
    add=controls.MyrelCompositionTests.add
    prepare=controls.MyrelCompositionTests.prepare
    checkpoint=controls.MyrelCompositionTests.checkpoint
    binding=controls.MyrelCompositionTests.binding
    resolve=controls.MyrelCompositionTests.resolve
    resolve_top=controls.MyrelCompositionTests.resolve_top
    replay=controls.MyrelCompositionTests.replay
    priority=controls.MyrelCompositionTests.priority
    actions=controls.MyrelCompositionTests.actions
    cast=controls.MyrelCompositionTests.cast
    reject=controls.MyrelCompositionTests.reject
    advance=journeys.AbilityLabelCompositionTests.advance
    choose_copy=copies.FixedCopyTypeExceptionRuntimeTests.choose_copy

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'persistent-hands.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/persistent-hand-visibility-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Persistent hands',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def top(self,s,name,ref,seat='B'):
        card=self.add(s.engine,name,seat=seat,zone='library',ref=ref)
        library=s.state.players[seat].zones['library'];library.remove(card.object_id);library.append(card.object_id)
        card.known_to.clear();card.revealed_to.clear();return card

    def visibility(self,s,card,viewers):
        self.visibility_many(s,[(card,viewers)])

    def visibility_many(self,s,expected):
        before=authoritative_state_hash(s.state)
        for viewer in (*'ABCD','spectator'):
            principal=viewer if viewer=='spectator' else 'pilot:'+viewer
            packet=s.packet(principal,full=True)
            if viewer=='spectator':self.assertIsNone(packet.get('decision'))
            rendered=json.dumps(packet,sort_keys=True)
            for card,viewers in expected:
                self.assertEqual(viewer in viewers,card.ref in rendered,(card.ref,viewer,sorted(viewers)))
        self.assertEqual(before,authoritative_state_hash(s.state),'Projection must not mutate knowledge or game state')

    def hands(self,s):
        return {seat:self.add(s.engine,'Fugitive Wizard',seat=seat,zone='hand',ref='private_'+seat) for seat in 'ABCD'}

    def test_closed_scopes_and_legacy_visibility_descriptor_remain_sealed(self):
        from quorune.semantic_runtime.hand_visibility import HAND_VISIBILITY_HANDLER,StaticHandVisibilityHandler
        from quorune.semantic_runtime.action_permissions import ACTION_PERMISSION_EVENT,ActionPermissionSourceContext,LibraryTopVisibilityHandler
        from quorune.semantic_runtime.context import SemanticNodeError
        handler=StaticHandVisibilityHandler();descriptor={'handler_id':HAND_VISIBILITY_HANDLER,'schema_version':1,'event':ACTION_PERMISSION_EVENT,'scope':'source_opponents'}
        context=ActionPermissionSourceContext('source','source-lid','program','B',tuple('ABCD'))
        self.assertEqual(['A','C','D'],[p.player for p in handler.lower(descriptor,context)])
        self.assertEqual(list('ABCD'),[p.player for p in handler.lower({**descriptor,'scope':'all_players'},context)])
        self.assertEqual((),handler.lower(descriptor,replace(context,source_controller='missing')))
        for change in ({'schema_version':True},{'scope':'controller'},{'scope':['all_players']},{'viewer':'admin'},{'event':'visibility.all'}):
            with self.assertRaises(SemanticNodeError):handler.validate({**descriptor,**change})
        with self.assertRaises(SemanticNodeError):LibraryTopVisibilityHandler().validate({'handler_id':'permission.action.library-top-visibility.v1','schema_version':1,'event':ACTION_PERMISSION_EVENT,'scope':'source_opponents','visibility':'public'})
        for text in ('Until end of turn, players play with their hands revealed.','Your opponents play with their libraries revealed.','Players play with their hands revealed unless you control an artifact.'):
            card=replace(self.db.lookup('Telepathy'),oracle_text=text)
            self.assertNotEqual('exact',compile_oracle_card(card,capability_registry=self.registry,capability_profile='commander_review').status)

    def test_originals_have_complete_normal_admission_and_undeclared_minimum_rejects(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        s=self.session(817000)
        for name in NAMES:
            source=self.add(s.engine,name,zone='hand');bound=self.binding(s,source);self.assertTrue(bound['strict_capability_ready'],(name,bound['blockers']))
            ir=compile_oracle_card(self.db.lookup(name),capability_registry=self.registry,capability_profile='commander_review');self.assertEqual('exact',ir.status,ir.material_residuals)
            program=next(p for p in s.engine.semantics.programs_for_oracle(source.oracle_id) if CAP in p.capability_dependencies)
            deps=tuple(c for c in program.capability_dependencies if c!=CAP)
            changed=replace(program,capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict() if deps else None)
            binding=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review')
            self.assertFalse(binding['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+CAP,binding['blockers'])

    def test_real_wandering_eye_reveals_new_hands_and_humble_revokes_new_information(self):
        from quorune.arena import SeatScopedPilotTools
        s=self.session(817010);source=self.add(s.engine,'Wandering Eye',zone='hand');humble=self.add(s.engine,'Humble',zone='hand')
        draw1=self.add(s.engine,'Reach Through Mists',seat='B',zone='hand',ref='b_draw1');draw2=self.add(s.engine,'Reach Through Mists',seat='B',zone='hand',ref='b_draw2')
        hands=self.hands(s);hidden=self.top(s,'Shock','hidden_after_loss');shown=self.top(s,'Unsummon','shown_while_active')
        s.state.players['B'].mana_pool['U']=2;self.prepare(s,mana={'C':3,'U':1,'W':1});self.checkpoint(s)
        self.cast(s,source,payment={'C':2,'U':1});self.assertIsNone(self.resolve(s))
        self.visibility_many(s,[(card,set('ABCD')|{'spectator'}) for card in hands.values()]+[(shown,set()),(hidden,set())])
        rows={row.ref:row for row in s.engine._semantic_choice_object_rows('A')};self.assertTrue(rows[hands['B'].ref].known_to_actor)
        self.assertTrue(SeatScopedPilotTools(s,'A')._rule_visible(hands['B'].ref))
        self.cast(s,draw1,seat='B',payment={'U':1});self.assertIsNone(self.resolve(s));self.visibility(s,shown,set('ABCD')|{'spectator'})
        s=self.replay(s,load=True);self.cast(s,humble,[source.ref],payment={'C':1,'W':1});self.assertIsNone(self.resolve(s))
        self.cast(s,draw2,seat='B',payment={'U':1});self.assertIsNone(self.resolve(s));self.visibility(s,hidden,{'B'})
        self.assertNotIn(hidden.ref,{row.ref for row in s.engine._semantic_choice_object_rows('A')});self.assertFalse(SeatScopedPilotTools(s,'A')._rule_visible(hidden.ref))
        self.replay(s,load=True)

    def test_real_telepathy_recomputes_opponents_after_actual_control_change(self):
        s=self.session(817020);source=self.add(s.engine,'Telepathy',zone='hand');steal=self.add(s.engine,'Steal Enchantment',seat='B',zone='hand')
        destroy=self.add(s.engine,'Disenchant',zone='hand');draw1=self.add(s.engine,'Reach Through Mists',seat='B',zone='hand',ref='steal_b_draw1');draw2=self.add(s.engine,'Reach Through Mists',seat='B',zone='hand',ref='steal_b_draw2')
        for i in range(2):self.add(s.engine,'Plains',ref='control_plains'+str(i))
        for i in range(4):self.add(s.engine,'Island',seat='B',ref='control_island'+str(i))
        hands=self.hands(s);public=self.top(s,'Shock','public_after_return');private=self.top(s,'Unsummon','private_under_b_control')
        self.prepare(s,mana={'U':1});self.checkpoint(s);self.cast(s,source,payment={'U':1});self.assertIsNone(self.resolve(s))
        self.visibility_many(s,[(hands['A'],{'A'})]+[(hands[seat],set('ABCD')|{'spectator'}) for seat in 'BCD'])
        self.advance(s,lambda x:x.state.active_player=='B' and x.state.phase=='precombat_main' and x.state.pending_decision.kind=='priority')
        self.cast(s,steal,[source.ref],seat='B');self.assertIsNone(self.resolve(s));self.assertEqual('B',s.state.cards[source.object_id].controller)
        self.visibility(s,hands['A'],set('ABCD')|{'spectator'})
        self.assertNotIn('cast:'+hands['A'].ref,{row['id'] for row in self.actions(s,'B')})
        self.reject(s,{'action_id':'cast:'+hands['A'].ref,'targets':[],'pay':'auto'},seat='B')
        self.cast(s,draw1,seat='B');self.assertIsNone(self.resolve(s));self.visibility(s,private,{'B'})
        s=self.replay(s,load=True);self.cast(s,destroy,[steal.ref]);self.assertIsNone(self.resolve(s));self.assertEqual('A',s.state.cards[source.object_id].controller)
        self.visibility(s,private,set('ABCD')|{'spectator'})
        self.cast(s,draw2,seat='B');self.assertIsNone(self.resolve(s));self.visibility(s,public,set('ABCD')|{'spectator'})
        self.replay(s,load=True)

    def test_real_revelation_world_replacement_ends_public_access(self):
        s=self.session(817030);source=self.add(s.engine,'Revelation',zone='hand');new_world=self.add(s.engine,'Concordant Crossroads',zone='hand')
        draw=self.add(s.engine,'Reach Through Mists',seat='B',zone='hand');hands=self.hands(s);private=self.top(s,'Shock','hidden_after_world_rule')
        s.state.players['B'].mana_pool['U']=1;self.prepare(s,mana={'G':2});self.checkpoint(s)
        self.cast(s,source,payment={'G':1});self.assertIsNone(self.resolve(s))
        self.visibility_many(s,[(card,set('ABCD')|{'spectator'}) for card in hands.values()])
        self.cast(s,new_world,payment={'G':1});self.assertIsNone(self.resolve(s));self.assertEqual('graveyard',s.state.cards[source.object_id].zone)
        self.assertEqual('battlefield',s.state.cards[new_world.object_id].zone);self.assertTrue(any(e.code=='state.world_rule' for e in s.state.events))
        self.cast(s,draw,seat='B',payment={'U':1});self.assertIsNone(self.resolve(s));self.visibility(s,private,{'B'});self.replay(s,load=True)

    def test_real_clone_keeps_copied_visibility_after_original_departure(self):
        s=self.session(817035);original=self.add(s.engine,'Wandering Eye',seat='B');clone=self.add(s.engine,'Clone',zone='hand')
        bounce=self.add(s.engine,'Unsummon',zone='hand');humble=self.add(s.engine,'Humble',zone='hand')
        draw1=self.add(s.engine,'Reach Through Mists',seat='B',zone='hand',ref='copy_draw1');draw2=self.add(s.engine,'Reach Through Mists',seat='B',zone='hand',ref='copy_draw2')
        self.hands(s);hidden=self.top(s,'Shock','hidden_after_copy_suppressed');shown=self.top(s,'Unsummon','shown_by_copy_only')
        s.state.players['B'].mana_pool['U']=2;self.prepare(s,mana={'C':4,'U':2,'W':1});self.checkpoint(s)
        self.cast(s,clone,payment={'C':3,'U':1});s=self.choose_copy(s,original)
        self.cast(s,bounce,[original.ref],payment={'U':1});self.assertIsNone(self.resolve(s));self.assertEqual('hand',s.state.cards[original.object_id].zone)
        self.cast(s,draw1,seat='B',payment={'U':1});self.assertIsNone(self.resolve(s));self.visibility(s,shown,set('ABCD')|{'spectator'})
        self.cast(s,humble,[clone.ref],payment={'C':1,'W':1});self.assertIsNone(self.resolve(s))
        self.cast(s,draw2,seat='B',payment={'U':1});self.assertIsNone(self.resolve(s));self.visibility(s,hidden,{'B'});self.replay(s,load=True)

    def test_real_seer_sacrifice_keeps_private_choice_after_new_draw(self):
        s=self.session(817040);source=self.add(s.engine,"Seer's Vision",zone='hand');own_draw=self.add(s.engine,'Reach Through Mists',zone='hand',ref='seer_own_draw')
        enemy_draw=self.add(s.engine,'Reach Through Mists',seat='B',zone='hand',ref='seer_enemy_draw');hands=self.hands(s);private=self.top(s,'Shock','seer_private_response_draw')
        s.state.players['B'].mana_pool['U']=1;self.prepare(s,mana={'C':2,'U':2,'B':1});self.checkpoint(s)
        self.cast(s,source,payment={'C':2,'U':1,'B':1});self.assertIsNone(self.resolve(s));self.visibility(s,hands['A'],{'A'})
        ability=next(row['id'] for row in self.actions(s) if row['id'].startswith('activate:'+source.ref+':'))
        self.cast(s,own_draw,payment={'U':1});self.assertNotIn(ability,{row['id'] for row in self.actions(s)})
        command={'action_id':ability,'targets':['B'],'pay':'manual','payment':{}};self.reject(s,command)
        self.assertIsNone(self.resolve(s));self.reject(s,command,seat='D');result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        self.assertEqual('graveyard',s.state.cards[source.object_id].zone)
        self.cast(s,enemy_draw,seat='B',payment={'U':1});self.assertIsNone(self.resolve_top(s));self.visibility(s,private,{'B'})
        pending=self.resolve(s);self.assertIsNotNone(pending);self.assertEqual('semantic.choice',pending.kind);self.visibility(s,private,{'A','B'})
        s=self.replay(s,load=True);choose={'action_id':'choose','card':hands['B'].ref};self.reject(s,choose,seat='B')
        result=s.act('pilot:A',choose);self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(s))
        self.assertEqual('graveyard',s.state.cards[hands['B'].object_id].zone);self.visibility(s,private,{'A','B'})
        known={row.ref:row for row in s.engine._semantic_choice_object_rows('A')};self.assertTrue(known[private.ref].known_to_actor)
        self.assertNotIn(private.ref,{row.ref for row in s.engine._semantic_choice_object_rows('C')});self.replay(s,load=True)

    def test_real_token_copy_keeps_visibility_until_its_own_departure(self):
        s=self.session(817045);original=self.add(s.engine,'Wandering Eye');copy=self.add(s.engine,'Cackling Counterpart',zone='hand')
        bounce1=self.add(s.engine,'Unsummon',zone='hand',ref='copy_token_bounce1');bounce2=self.add(s.engine,'Unsummon',zone='hand',ref='copy_token_bounce2')
        draw1=self.add(s.engine,'Reach Through Mists',seat='B',zone='hand',ref='token_b_draw1');draw2=self.add(s.engine,'Reach Through Mists',seat='B',zone='hand',ref='token_b_draw2')
        self.hands(s);hidden=self.top(s,'Shock','hidden_after_token_departure');shown=self.top(s,'Unsummon','shown_by_token_only')
        s.state.players['B'].mana_pool['U']=2;self.prepare(s,mana={'C':1,'U':4});self.checkpoint(s)
        before=set(s.state.cards);self.cast(s,copy,[original.ref],payment={'C':1,'U':2});self.assertIsNone(self.resolve(s))
        tokens=[card for oid,card in s.state.cards.items() if oid not in before and card.zone=='battlefield' and card.is_token]
        self.assertEqual(1,len(tokens));token=tokens[0]
        self.cast(s,bounce1,[original.ref],payment={'U':1});self.assertIsNone(self.resolve(s));self.assertEqual('hand',s.state.cards[original.object_id].zone)
        self.cast(s,draw1,seat='B',payment={'U':1});self.assertIsNone(self.resolve(s));self.visibility(s,shown,set('ABCD')|{'spectator'})
        s=self.replay(s,load=True);self.cast(s,bounce2,[token.ref],payment={'U':1});self.assertIsNone(self.resolve(s))
        self.assertFalse(any(c.object_id==token.object_id and c.zone=='battlefield' for c in s.state.cards.values()))
        self.cast(s,draw2,seat='B',payment={'U':1});self.assertIsNone(self.resolve(s));self.visibility(s,hidden,{'B'});self.replay(s,load=True)

    def test_verified_withdrawal_and_fresh_grant_omission_fail_closed(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from scripts.update_capability_evidence import build_index,discover_tests
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'));changed=deepcopy(raw)
        next(c for c in changed['capabilities'] if c['id']==CAP).update(status='blocked',blockers=['withdrawn hand visibility authority'],dependency_fail_closed_status='not_run')
        registry=CapabilityRegistry(changed);index=build_index(registry_value=changed,declaration_source=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json'),discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
        for name in NAMES:
            ir=compile_oracle_card(self.db.lookup(name),capability_registry=registry,capability_profile='commander_review');blockers=[b for r in ir.material_residuals for b in r.blockers]
            self.assertTrue(any('status:'+CAP+':blocked' in b for b in blockers),blockers);self.assertFalse(any('evidence_index:unverified' in b for b in blockers))
        from quorune.semantic_runtime.hand_visibility import StaticHandVisibilityHandler
        method='test_real_wandering_eye_reveals_new_hands_and_humble_revokes_new_information'
        with patch.object(StaticHandVisibilityHandler,'lower',return_value=()):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()
        original=StaticHandVisibilityHandler.lower
        def widen(handler,descriptor,context):return original(handler,{**descriptor,'scope':'all_players'},context)
        method='test_real_telepathy_recomputes_opponents_after_actual_control_change'
        with patch.object(StaticHandVisibilityHandler,'lower',new=widen):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()
        method='test_real_wandering_eye_reveals_new_hands_and_humble_revokes_new_information'
        with patch('quorune.semantic_runtime.action_permissions.program_has_current_ability_fragments',return_value=True):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()
