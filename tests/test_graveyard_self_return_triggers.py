"""CR113.6m/113.8/400.7: optional source returns from its graveyard."""
from dataclasses import replace
from types import SimpleNamespace
import unittest
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from test_qualified_zone_event_queries import query_record

CAPABILITY='zone.return.triggered_self_graveyard_hand'


class GraveyardSelfReturnCompilerTests(unittest.TestCase):
    def test_optional_mana_prefix_is_preserved_and_unknown_prefix_cannot_be_discarded(self):
        paid='Whenever a land you control enters, you may pay {2}{G}. If you do, return this card from your graveyard to your hand.'
        ir=compile_oracle_card(query_record(paid),capability_registry=load_default_capability_registry(),capability_profile='commander_review')
        self.assertEqual('exact',ir.status,ir.material_residuals)
        self.assertEqual('graveyard',ir.faces[0].nodes[0].active_zone)
        self.assertEqual('offer_optional_mana_payment',ir.faces[0].nodes[0].effects[0]['op'])
        unknown='Whenever a land you control enters, if the remembered prophecy is true, return this card from your graveyard to your hand.'
        self.assertNotEqual('exact',compile_oracle_card(query_record(unknown),capability_registry=load_default_capability_registry(),capability_profile='commander_review').status)
    def test_verified_return_authority_withdrawal_keeps_ordinary_upkeep_draw(self):
        import json
        from common import ROOT
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding="utf-8"))
        next(c for c in raw['capabilities'] if c['id']==CAPABILITY).update(status='blocked',blockers=['Source return authority withdrawn'])
        registry=CapabilityRegistry(raw);declarations=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        index=build_index(registry_value=raw,declaration_source=declarations,discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
        for text,expected in (('At the beginning of your upkeep, you may return this card from your graveyard to your hand.',False),
                ('At the beginning of your upkeep, draw a card.',True)):
            ir=compile_oracle_card(query_record(text),capability_registry=registry,capability_profile='commander_review')
            self.assertEqual(expected,ir.status=='exact')

    def test_existing_event_heads_keep_owner_relative_predicates_and_graveyard_zone(self):
        registry=load_default_capability_registry()
        for head in ('At the beginning of your upkeep','Whenever you cast a multicolored spell',
                'Whenever an artifact you control enters','Whenever a Swamp you control enters',
                'Whenever a nontoken Snake you control enters'):
            text=head+', you may return this card from your graveyard to your hand.'
            with self.subTest(head=head):
                ir=compile_oracle_card(replace(query_record(text),type_line='Creature — Snake'),
                    capability_registry=registry,capability_profile='commander_review')
                self.assertEqual('exact',ir.status,ir.material_residuals)
                node=ir.faces[0].nodes[0];self.assertEqual('graveyard',node.active_zone)
                self.assertIn(CAPABILITY,node.capability_dependencies)
                self.assertIn('offer_optional_effect',str(node.effects))

    def test_battlefield_other_card_and_aura_departure_exceptions_do_not_gain_this_contract(self):
        dungeon = compile_oracle_card(query_record('Whenever you complete a dungeon, you may return this card from your graveyard to your hand.'),
            capability_registry=load_default_capability_registry(),capability_profile='commander_review')
        self.assertEqual('exact', dungeon.status, dungeon.material_residuals)
        node = dungeon.faces[0].nodes[0]
        self.assertEqual(('graveyard','dungeon.completed'), (node.active_zone,node.event))
        self.assertEqual({'field':'player','op':'eq','value':'$source.owner'}, node.event_condition)
        for text in ('When enchanted land is put into a graveyard, you may return this card from your graveyard to your hand.',
                'At the beginning of your upkeep, you may return this card from your graveyard to the battlefield.',
                'Whenever one or more artifacts you control enter, you may return this card from your graveyard to your hand.',
                'At the beginning of your upkeep, you may return another card from your graveyard to your hand.'):
            with self.subTest(text=text):
                ir=compile_oracle_card(query_record(text),capability_registry=load_default_capability_registry(),capability_profile='commander_review')
                self.assertNotEqual('exact',ir.status)


class GraveyardSelfReturnModelTests(unittest.TestCase):
    def test_printed_graveyard_current_component_is_zone_scoped_and_requires_trust(self):
        from quorune.compiled_ability_fragments import compiled_static_ability_fragments
        from quorune.ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE,StaticComponentSpec
        program=SimpleNamespace(key='fixture:graveyard-trigger',provenance={'face_id':'front'},
            active_zone='graveyard',event='step.begin',ability_id='trigger:front:n1',
            coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE,),handlers=())
        class Registry:
            def runtime_handler_programs_for_oracle(self,*args,**kwargs):return ()
            def programs_for_oracle(self,*args,active_zone):return (program,) if active_zone=='graveyard' else ()
        card=SimpleNamespace(zone='graveyard',annotations={},active_face=None)
        record=SimpleNamespace(oracle_id='fixture:source',faces=())
        host=SimpleNamespace(semantics=Registry(),card_record=lambda c:record,semantic_program_is_current_trusted=lambda p:True)
        self.assertIn(StaticComponentSpec(program.key),compiled_static_ability_fragments(host,card))
        card.zone='hand';self.assertEqual((),compiled_static_ability_fragments(host,card))
        card.zone='graveyard';host.semantic_program_is_current_trusted=lambda p:False
        self.assertEqual((),compiled_static_ability_fragments(host,card))

    def test_marker_codec_is_closed_and_descriptor_owns_authority(self):
        from quorune.rules.graveyard_self_return import graveyard_self_return_dependencies,GRAVEYARD_SELF_RETURN_VERSION,graveyard_self_return_runtime_dependencies
        from quorune.card_programs.descriptor_requirements import typed_result_amount_runtime_dependencies
        effect={'op':'self_zone_move','origin':'graveyard','destination':'hand','tapped':False,'source_form':'card',GRAVEYARD_SELF_RETURN_VERSION:1}
        self.assertEqual((CAPABILITY,),typed_result_amount_runtime_dependencies(effect))
        self.assertEqual((CAPABILITY,),graveyard_self_return_runtime_dependencies(SimpleNamespace(effects=(effect,),event='step.begin',active_zone='graveyard')))
        for event,zone in (('activate','graveyard'),('step.begin','battlefield')):
            with self.assertRaises(ValueError):graveyard_self_return_runtime_dependencies(SimpleNamespace(effects=(effect,),event=event,active_zone=zone))
        self.assertEqual((),graveyard_self_return_dependencies({k:v for k,v in effect.items() if k!=GRAVEYARD_SELF_RETURN_VERSION}))
        for delta in ({GRAVEYARD_SELF_RETURN_VERSION:True},{'destination':'battlefield'},{'tapped':0},{'unknown':1}):
            with self.assertRaises(ValueError):graveyard_self_return_dependencies({**effect,**delta})

    def test_typed_existing_move_skips_a_new_incarnation(self):
        from quorune.self_zone_move import SelfZoneMoveIntent,resolve_self_zone_move
        card=SimpleNamespace(object_id='card1',ref='C1',logical_object_id='card1@2',zone='graveyard',phased_out=False,object_kind='card')
        moves=[]
        def move(*args,**kwargs):moves.append((args,kwargs));card.zone=args[1]
        host=SimpleNamespace(state=SimpleNamespace(cards={'card1':card}),move_card=move)
        intent=SelfZoneMoveIntent('A','S1','card1','C1','card1@1','graveyard','hand',False,'card')
        self.assertIsNone(resolve_self_zone_move(host,intent));self.assertEqual([],moves)

    def test_omitting_source_incarnation_check_is_killed(self):
        from unittest.mock import patch
        from quorune import self_zone_move as owner
        original=owner.resolve_self_zone_move
        def omit(host,intent):return original(host,replace(intent,logical_object_id=host.state.cards[intent.object_id].logical_object_id))
        method='test_typed_existing_move_skips_a_new_incarnation'
        baseline=unittest.TestResult();GraveyardSelfReturnModelTests(method).run(baseline)
        self.assertTrue(baseline.wasSuccessful(),baseline.errors+baseline.failures)
        with patch.object(owner,'resolve_self_zone_move',side_effect=omit):
            result=unittest.TestResult();GraveyardSelfReturnModelTests(method).run(result)
        self.assertFalse(result.errors,result.errors);self.assertEqual(1,len(result.failures))


import json,tempfile
from pathlib import Path
from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.record import authoritative_state_hash
from scripts.build_test_database import build_fixture_database


class GraveyardSelfReturnNativeTests(InheritedNativeFixture,unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'graveyard-self-return.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/graveyard-self-return-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Graveyard source returns',[DeckEntry('Isamaru, Hound of Konda',1,'commander'),DeckEntry('Generic Bound Plains',20)],['Isamaru, Hound of Konda'])

    def optional(self,s,seat,*,accept):
        self.assertEqual('semantic.choice',s.state.pending_decision.kind)
        before=authoritative_state_hash(s.state)
        wrong=next(player for player in 'ABCD' if player!=seat)
        command={'action_id':'choose','choice':'apply' if accept else 'decline'}
        self.assertFalse(s.act('pilot:'+wrong,command).ok);self.assertEqual(before,authoritative_state_hash(s.state))
        result=s.act('pilot:'+seat,command);self.assertTrue(result.ok,result.summary)

    def test_returned_original_cast_targets_players_and_revalidates_a_bounced_creature(self):
        from quorune.turn_step_owner import TURN_STEPS

        for target_kind, respond in (('player', False), ('creature', False), ('creature', True)):
            with self.subTest(target=target_kind, response=respond):
                seed = 113600211 + int(target_kind == 'creature') + int(respond)
                s = self.session(seed)
                source = self.add(s.engine, 'Rekindled Flame', zone='graveyard')
                body = self.add(s.engine, 'Grizzly Bears', seat='B', ref='returned-spell-target')
                bounce = self.add(s.engine, 'Unsummon', seat='B', zone='hand', ref='returned-spell-response')
                private = self.add(s.engine, 'Counterspell', seat='C', zone='hand', ref='returned-spell-private')
                lands = [self.add(s.engine, 'Mountain', ref='returned-spell-red-' + str(i)) for i in range(4)]
                island = self.add(s.engine, 'Island', seat='B', ref='returned-spell-blue')
                for card in (source, body, bounce, *lands, island):
                    self.assert_closed(s, card)
                source_counter = source.zone_change_counter
                body_counter = body.zone_change_counter
                for uid in tuple(s.state.players['D'].zones['hand']):
                    s.engine.move_card(uid, 'library', log=False)
                self.prepare(s, mana={})
                s.engine.permissions.invalidate_current()
                s.state.pending_decision = None
                s.state.active_player = 'D'
                s.state.last_normal_turn_player = 'D'
                if s.state.current_turn is not None:
                    s.state.current_turn = replace(s.state.current_turn, player='D')
                s.state.phase_index = TURN_STEPS.index(('ending', 'end_step'))
                s.state.phase, s.state.step = 'ending', 'end_step'
                s.state.priority_passes = []
                s.engine._grant_priority('D')
                s.engine.pump()
                self.checkpoint(s)
                self.until(s, lambda game: game.state.active_player == 'A' and game.state.step == 'upkeep')
                self.assertEqual('semantic.choice', self.resolve(s).kind)
                self.optional(s, 'A', accept=True)
                self.assertIsNone(self.resolve(s))
                self.assertEqual('hand', s.state.cards[source.object_id].zone)
                self.assertEqual(source_counter + 1, s.state.cards[source.object_id].zone_change_counter)
                s = self.replay(s, load=True)
                self.until(s, lambda game: game.state.active_player == 'A' and game.state.phase == 'precombat_main')
                self.priority(s, 'A')
                actions = s.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
                action = next(row for row in actions if row['id'] == 'cast:' + source.ref)
                legal = action['target_schema']['legal_refs']
                self.assertIn('B', legal)
                self.assertIn(body.ref, legal)
                self.assertNotIn(lands[0].ref, legal)
                target = 'B' if target_kind == 'player' else body.ref
                command = {'action_id': action['id'], 'targets': [target], 'pay': 'auto'}
                before = authoritative_state_hash(s.state)
                self.assertFalse(s.act('pilot:B', command).ok)
                self.assertFalse(s.act('pilot:A', {**command, 'targets': [lands[0].ref]}).ok)
                self.assertEqual(before, authoritative_state_hash(s.state))
                accepted = s.act('pilot:A', command)
                self.assertTrue(accepted.ok, accepted.summary)
                if respond:
                    self.priority(s, 'B')
                    actions = s.packet('pilot:B', full=True)['decision']['ctx']['legal']['actions']
                    response = next(row for row in actions if row['id'] == 'cast:' + bounce.ref)
                    accepted = s.act('pilot:B', {
                        'action_id': response['id'], 'targets': [body.ref], 'pay': 'auto',
                    })
                    self.assertTrue(accepted.ok, accepted.summary)
                    self.resolve_top(s)
                    self.assertEqual('hand', s.state.cards[body.object_id].zone)
                    self.assertEqual(body_counter + 1, s.state.cards[body.object_id].zone_change_counter)
                s = self.replay(s, load=True)
                self.assertIsNone(self.resolve(s))
                self.assertEqual('graveyard', s.state.cards[source.object_id].zone)
                self.assertEqual(source_counter + 3, s.state.cards[source.object_id].zone_change_counter)
                self.assertEqual(36 if target_kind == 'player' else 40, s.state.players['B'].life)
                self.assertEqual('hand' if respond else 'graveyard' if target_kind == 'creature' else 'battlefield',
                    s.state.cards[body.object_id].zone)
                self.assertEqual(4, sum(s.state.cards[land.object_id].tapped for land in lands))
                self.assertEqual(respond, s.state.cards[island.object_id].tapped)
                self.assertFalse(s.state.stack)
                for viewer in 'ABD':
                    self.assertNotIn(private.ref, json.dumps(s.packet('pilot:' + viewer, full=True)))
                self.replay(s, load=True)

    def test_actual_owner_cast_trigger_returns_privately_and_decline_keeps_graveyard(self):
        s=self.session(113600201)
        source=self.add(s.engine,'Enigma Eidolon',seat='B',zone='graveyard')
        declined=self.add(s.engine,'Sandstorm Eidolon',seat='B',zone='graveyard')
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand')
        spell=self.add(s.engine,'Terminate',seat='B',zone='hand')
        target=self.add(s.engine,'Grizzly Bears',seat='C')
        white=self.add(s.engine,'Chaplain\'s Blessing',seat='A',zone='hand')
        land1=self.add(s.engine,'Swamp',seat='B');land2=self.add(s.engine,'Mountain',seat='B')
        self.assert_closed(s,source);self.assert_closed(s,declined);self.assert_closed(s,spell)
        self.prepare(s,mana={'W':1});self.checkpoint(s)
        self.cast(s,'A',white,[]);self.assertIsNone(self.resolve(s))
        self.assertEqual('graveyard',s.state.cards[source.object_id].zone)
        self.priority(s,'B')
        for land in (land1,land2):
            rows=s.packet('pilot:B',full=True)['decision']['ctx']['legal']['actions']
            mana=next(row for row in rows if row.get('mana_ability') and land.ref in row['id'])
            self.assertTrue(s.act('pilot:B',{'action_id':mana['id'],'pay':'auto'}).ok)
        self.cast(s,'B',spell,[target.ref])
        self.assertEqual('trigger.order',s.state.pending_decision.kind)
        ctx=s.packet('pilot:B',full=True)['decision']['ctx'];ids=[row['id'] for row in ctx['triggers']]
        self.assertEqual(2,len(ids));self.assertTrue(s.act('pilot:B',{'action_id':'order','triggers':ids}).ok)
        self.resolve(s)
        self.assertEqual('semantic.choice',s.state.pending_decision.kind)
        s=self.replay(s,load=True)
        first=s.state.stack[-1].source_object_id
        self.optional(s,'B',accept=first==source.object_id);self.resolve(s)
        self.assertEqual('semantic.choice',s.state.pending_decision.kind)
        self.optional(s,'B',accept=s.state.stack[-1].source_object_id==source.object_id)
        self.assertIsNone(self.resolve(s))
        self.assertEqual('hand',s.state.cards[source.object_id].zone)
        self.assertEqual('graveyard',s.state.cards[declined.object_id].zone)
        self.assertIn(source.ref,json.dumps(s.packet('pilot:B',full=True)['state']['players']['B']['hand']))
        for seat in 'ABD':self.assertNotIn(private.ref,json.dumps(s.packet('pilot:'+seat,full=True)))
        self.replay(s,load=True)

    def test_actual_entry_trigger_cannot_return_a_source_exiled_in_response(self):
        s=self.session(113600202)
        source=self.add(s.engine,'Ovalchase Daredevil',zone='graveyard')
        artifact=self.add(s.engine,'Memnite',zone='hand')
        exile=self.add(s.engine,"Tormod's Crypt",seat='B')
        self.assert_closed(s,source);self.assert_closed(s,artifact);self.assert_closed(s,exile)
        self.prepare(s,mana={});self.checkpoint(s)
        self.cast(s,'A',artifact,[]);self.resolve_top(s)
        self.assertTrue(s.state.stack)
        old=s.state.cards[source.object_id].logical_object_id
        self.priority(s,'B');rows=s.packet('pilot:B',full=True)['decision']['ctx']['legal']['actions']
        activated=next(row for row in rows if row['id'].startswith('activate:'+exile.ref+':') and not row.get('mana_ability'))
        self.assertTrue(s.act('pilot:B',{'action_id':activated['id'],'targets':['A'],'pay':'auto'}).ok)
        self.resolve_top(s)
        self.assertEqual('exile',s.state.cards[source.object_id].zone)
        self.assertNotEqual(old,s.state.cards[source.object_id].logical_object_id)
        self.assertEqual('semantic.choice',self.resolve(s).kind)
        s=self.replay(s,load=True);self.optional(s,'A',accept=True)
        self.assertIsNone(self.resolve(s));self.assertEqual('exile',s.state.cards[source.object_id].zone)
        self.replay(s,load=True)

    def test_two_queued_entry_triggers_do_not_return_a_new_graveyard_incarnation(self):
        s=self.session(113600203)
        source=self.add(s.engine,'Ovalchase Daredevil',zone='graveyard')
        artifacts=[self.add(s.engine,'Memnite',zone='hand'),self.add(s.engine,'Goblin Firebomb',zone='hand')]
        loot=self.add(s.engine,'Frantic Search',zone='hand')
        self.assert_closed(s,source);self.assert_closed(s,loot)
        self.assert_closed(s,artifacts[1]);self.prepare(s,mana={'U':1,'C':3});self.checkpoint(s)
        self.cast(s,'A',artifacts[0],[]);self.resolve_top(s)
        self.cast(s,'A',artifacts[1],[]);self.resolve_top(s)
        self.assertEqual(2,len(s.state.stack));original=source.logical_object_id
        self.assertEqual('semantic.choice',self.resolve(s).kind)
        self.optional(s,'A',accept=True)
        self.assertEqual('hand',s.state.cards[source.object_id].zone)
        self.cast(s,'A',loot,[]);self.assertEqual('choice.apnap',self.resolve(s).kind)
        hand=[s.state.cards[uid].ref for uid in s.state.players['A'].zones['hand']]
        other=next(ref for ref in hand if ref!=source.ref)
        self.assertTrue(s.act('pilot:A',{'action_id':'choose','cards':[source.ref,other]}).ok)
        self.assertEqual('graveyard',s.state.cards[source.object_id].zone)
        self.assertNotEqual(original,s.state.cards[source.object_id].logical_object_id)
        pending=self.resolve(s)
        if pending is not None and pending.kind=='choice.apnap':
            self.assertTrue(s.act(s.pending_principals()[0],{'action_id':'choose','cards':[]}).ok)
            pending=self.resolve(s)
        self.assertEqual('semantic.choice',pending.kind)
        s=self.replay(s,load=True);self.optional(s,'A',accept=True)
        self.assertIsNone(self.resolve(s));self.assertEqual('graveyard',s.state.cards[source.object_id].zone)
        self.replay(s,load=True)

    def test_actual_upkeep_owner_and_intervening_condition_recheck_after_opponent_draw(self):
        from quorune.turn_step_owner import TURN_STEPS
        for empty in (False,True):
            with self.subTest(empty=empty):
                s=self.session(113600204+int(empty))
                squee=self.add(s.engine,'Squee, Goblin Nabob',zone='graveyard')
                conditional=self.add(s.engine,'Rekindled Flame',zone='graveyard')
                top=self.add(s.engine,'Jayemdae Tome',seat='B')
                if empty:
                    for i in range(4):self.add(s.engine,'Wastes',seat='B',ref='upkeep-wastes-'+str(i))
                for uid in tuple(s.state.players['B'].zones['hand'])[int(not empty):]:s.engine.move_card(uid,'library',log=False)
                for uid in tuple(s.state.players['D'].zones['hand'])[3:]:s.engine.move_card(uid,'library',log=False)
                for card in (squee,conditional,top):self.assert_closed(s,card)
                self.prepare(s,mana={});s.engine.permissions.invalidate_current();s.state.pending_decision=None
                s.state.active_player='D';s.state.last_normal_turn_player='D'
                if s.state.current_turn is not None:s.state.current_turn=replace(s.state.current_turn,player='D')
                s.state.phase_index=TURN_STEPS.index(('ending','end_step'))
                s.state.phase='ending';s.state.step='end_step';s.state.priority_passes=[]
                s.engine._grant_priority('D');s.engine.pump();self.checkpoint(s)
                self.until(s,lambda current:current.state.active_player=='A' and current.state.step=='upkeep')
                if s.state.pending_decision.kind=='trigger.order':
                    ctx=s.packet('pilot:A',full=True)['decision']['ctx']
                    self.assertTrue(s.act('pilot:A',{'action_id':'order','triggers':[r['id'] for r in ctx['triggers']]}).ok)
                self.assertEqual(1,sum(item.source_object_id==squee.object_id for item in s.state.stack))
                self.assertEqual(int(empty),sum(item.source_object_id==conditional.object_id for item in s.state.stack),
                    (len(s.state.players['B'].zones['hand']),[(item.label,item.controller) for item in s.state.stack]))
                if empty:
                    self.priority(s,'B')
                    for land in [c for c in s.state.cards.values() if c.owner=='B' and c.zone=='battlefield' and c.printed_name=='Wastes']:
                        rows=s.packet('pilot:B',full=True)['decision']['ctx']['legal']['actions']
                        mana=next(row for row in rows if row.get('mana_ability') and land.ref in row['id'])
                        self.assertTrue(s.act('pilot:B',{'action_id':mana['id'],'pay':'auto'}).ok)
                    self.priority(s,'B');rows=s.packet('pilot:B',full=True)['decision']['ctx']['legal']['actions']
                    draw=next(row for row in rows if row['id'].startswith('activate:'+top.ref+':') and not row.get('mana_ability'))
                    self.assertTrue(s.act('pilot:B',{'action_id':draw['id'],'pay':'auto'}).ok);self.resolve_top(s)
                    self.assertEqual(1,len(s.state.players['B'].zones['hand']))
                self.assertEqual('semantic.choice',self.resolve(s).kind);self.optional(s,'A',accept=True)
                self.assertIsNone(self.resolve(s));self.assertEqual('hand',s.state.cards[squee.object_id].zone)
                self.assertEqual('graveyard',s.state.cards[conditional.object_id].zone)
                self.replay(s,load=True)
