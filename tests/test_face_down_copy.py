"""Pinned status-defined copy values preserve hidden physical identity."""
from pathlib import Path
import tempfile
import unittest

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.card_programs import bind_card_program_runtime
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.deck import DeckDefinition,DeckEntry
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class FaceDownCharacteristicVersionTests(unittest.TestCase):
    def test_codec_and_manifest_keep_independent_current_and_historical_modes(self):
        from quorune.model import GameConfig,GameState
        from quorune.record_state_provenance import format_state_versions,validate_state_versions
        state=GameState(game_id='fd-characteristic-version',config=GameConfig(),players={},cards={},
            deck_names={},commander_oracle_ids={},turn_order=[],current_turn=None,last_normal_turn_player=None)
        wire=state.to_dict();self.assertEqual(1,wire['face_down_characteristics_version'])
        self.assertEqual(wire,GameState.from_dict(wire).to_dict())
        modern={'format':format_state_versions(state)};validate_state_versions(modern,state)
        old={key:value for key,value in wire.items() if key!='face_down_characteristics_version'}
        legacy=GameState.from_dict(old)
        self.assertIsNone(legacy.face_down_characteristics_version)
        self.assertEqual((1,1),(legacy.transform_timestamp_version,legacy.face_status_timestamp_version))
        self.assertEqual(old,legacy.to_dict())
        historical={'format':format_state_versions(legacy)};validate_state_versions(historical,legacy)
        self.assertNotIn('face_down_characteristics_version',historical['format'])
        for manifest,value in ((modern,legacy),(historical,state)):
            with self.assertRaises(ValueError):validate_state_versions(manifest,value)
        for bad in (True,False,0,2,'1',{}):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):GameState.from_dict({**wire,'face_down_characteristics_version':bad})
                with self.assertRaises(ValueError):validate_state_versions(
                    {'format':{**modern['format'],'face_down_characteristics_version':bad}},state)


class FaceDownCopyNativeTests(unittest.TestCase):
    register_fixture_cards_on_add=True

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'face-down-copy.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/morph-rules-cards.json',ROOT/'tests/fixtures/face-down-method-cards.json',
            ROOT/'tests/fixtures/face-down-copy-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Face-down copy',[DeckEntry('Generic Bound Commander',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()

    def assert_closed(self,session,card):
        program=compile_best_available_card_program(self.db,self.db.by_oracle_id(card.oracle_id),
            semantic_registry=session.engine.semantics,capability_registry=self.registry,capability_profile='commander_review')
        result=bind_card_program_runtime(program,capability_registry=self.registry,profile='commander_review')
        self.assertTrue(result['strict_capability_ready'],result['blockers'])
        self.assertEqual('capability_closed',result['trust_basis'])

    def test_actual_morph_spell_copy_exposes_only_anonymous_status_values_to_opponents(self):
        self._morph_copy_journey(current=True)

    def test_historical_absence_preserves_the_original_copy_representation_and_replay(self):
        self._morph_copy_journey(current=False)

    def _morph_copy_journey(self,*,current):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        from quorune.record import authoritative_state_hash
        session=H.session(self,70802701);engine=session.engine
        if not current:engine.state.face_down_characteristics_version=None
        observer=H.add(self,engine,'Generic Face Down Copy Observer',zone='hand',ref='OBSERVER')
        source=H.add(self,engine,'Lumithread Field',zone='hand',ref='HIDDEN_SOURCE')
        for card in (observer,source):self.assert_closed(session,card)
        offer=H.ready(self,session,observer,{'C':5,'U':1,'W':1});H.checkpoint(self,session)
        self.assertTrue(session.act('pilot:A',{'action_id':offer['id'],'pay':'auto','reserve':{'C':4,'W':1}}).ok)
        self.assertIsNone(H.resolve(self,session))
        self.assertTrue(session.act('pilot:A',{'action_id':'cast-morph:'+source.ref,'pay':'auto','reserve':{'C':1,'W':1}}).ok)
        self.assertFalse('Lumithread Field' in str(session.packet('pilot:B',full=True)['state']))
        session=H.replay(self,session,load=True);engine=session.engine
        for _ in range(12):
            if any(item.kind=='spell_copy' for item in engine.state.stack):break
            self.assertTrue(session.act(session.pending_principals()[0],{'action_id':'pass'}).ok)
        copied=next(item for item in engine.state.stack if item.kind=='spell_copy')
        public=str(session.packet('pilot:B',full=True)['state'])
        self.assertEqual(not current,'Lumithread Field' in public,'Current copy must conceal the hidden original; historical absence keeps its original representation')
        self.assertEqual(not current,source.oracle_id[:8] in public)
        session=H.replay(self,session,load=True);engine=session.engine
        self.assertIsNone(H.resolve(self,session))
        token=engine.state.cards[copied.card_object_id];values=engine._effective_card_data(token)
        self.assertFalse(token.face_down)
        if not current:
            self.assertEqual(('Lumithread Field','Enchantment'),(values['name'],values['type_line']))
            self.assertNotIn('face_down_characteristics_version',engine.state.to_dict())
            H.replay(self,session,load=True);return
        self.assertEqual(('',0,'Creature',[],[]),(values['name'],values['mana_value'],values['type_line'],values['colors'],values['keywords']))
        self.assertEqual((2,2),(engine._numeric_stat(token.object_id,'power'),engine._numeric_stat(token.object_id,'toughness')))
        self.assertFalse(engine._activated_abilities(token))
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        self.assertFalse(any(row['id'].startswith('turn-face-up:'+token.ref) for row in actions))
        offer=next(row for row in actions if row['id']=='turn-face-up:'+source.ref)
        before=authoritative_state_hash(engine.state)
        self.assertFalse(session.act('pilot:B',{'action_id':offer['id'],'pay':'auto'}).ok)
        self.assertEqual(before,authoritative_state_hash(engine.state))
        self.assertFalse(session.act('pilot:A',{'action_id':'turn-face-up:'+token.ref,'pay':'auto'}).ok)
        self.assertEqual(before,authoritative_state_hash(engine.state))
        self.assertTrue(session.act('pilot:A',{'action_id':offer['id'],'pay':'auto'}).ok)
        self.assertFalse(engine.state.cards[source.object_id].face_down)
        self.assertEqual('Creature',engine._effective_card_data(engine.state.cards[token.object_id])['type_line'])
        H.replay(self,session,load=True)

    def test_actual_face_down_token_copy_keeps_listed_ward_and_pays_its_real_tax(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        from quorune.record import authoritative_state_hash
        session=H.session(self,70802703);engine=session.engine
        source=H.add(self,engine,'Defenestrated Phantom',zone='hand',ref='DISGUISE_SOURCE')
        copy_spell=H.add(self,engine,'Cackling Counterpart',zone='hand',ref='FD_PERMANENT_COPY')
        frog=H.add(self,engine,'Turn to Frog',seat='B',zone='hand',ref='WARD_TARGETING_SPELL')
        for card in (source,copy_spell,frog):self.assert_closed(session,card)
        H.ready(self,session,source,{'C':4,'U':2})
        engine.state.players['B'].mana_pool.update({'C':3,'U':1});H.checkpoint(self,session)
        self.assertTrue(session.act('pilot:A',{'action_id':'cast-disguise:'+source.ref,'pay':'auto','reserve':{'C':1,'U':2}}).ok)
        self.assertIsNone(H.resolve(self,session))
        self.assertTrue(session.act('pilot:A',{'action_id':'cast:'+copy_spell.ref,'targets':[source.ref],'pay':'auto'}).ok)
        session=H.replay(self,session,load=True);engine=session.engine
        self.assertIsNone(H.resolve(self,session))
        token=next(card for card in engine.state.cards.values() if card.is_token and card.zone=='battlefield')
        values=engine._effective_card_data(token)
        self.assertEqual(('', 'Creature',['Ward {2}']),(values['name'],values['type_line'],values['keywords']))
        self.assertFalse(token.face_down)
        public=str(session.packet('pilot:B',full=True)['state'])
        self.assertFalse('Defenestrated Phantom' in public);self.assertFalse(source.oracle_id[:8] in public)
        self.assertTrue(session.act('pilot:A',{'action_id':'pass'}).ok)
        result=session.act('pilot:B',{'action_id':'cast:'+frog.ref,'targets':[token.ref],'pay':'auto','reserve':{'C':2}})
        self.assertTrue(result.ok,result.summary)
        decision=H.resolve(self,session)
        self.assertEqual(('semantic.choice',['B']),(decision.kind,decision.actors))
        before=authoritative_state_hash(engine.state)
        self.assertFalse(session.act('pilot:C',{'action_id':'choose','pay':True}).ok)
        self.assertEqual(before,authoritative_state_hash(engine.state))
        session=H.replay(self,session,load=True);engine=session.engine
        self.assertTrue(session.act('pilot:B',{'action_id':'choose','pay':True}).ok)
        self.assertIsNone(H.resolve(self,session))
        self.assertEqual(0,sum(engine.state.players['B'].mana_pool.values()))
        self.assertEqual((1,1),(engine._numeric_stat(token.object_id,'power'),engine._numeric_stat(token.object_id,'toughness')))
        self.assertEqual([],engine._effective_card_data(engine.state.cards[token.object_id])['keywords'])
        H.replay(self,session,load=True)

    def test_actual_morph_land_resolves_as_current_creature_then_turns_up_as_land(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        session=H.session(self,70802702);engine=session.engine
        card=H.add(self,engine,'Zoetic Cavern',zone='hand',ref='MORPH_LAND')
        self.assert_closed(session,card)
        H.ready(self,session,card,{'C':5});H.checkpoint(self,session)
        self.assertTrue(session.act('pilot:A',{'action_id':'cast-morph:'+card.ref,'pay':'auto','reserve':{'C':2}}).ok)
        self.assertIsNone(H.resolve(self,session),'A represented Morph cast resolves as the creature on the stack')
        identity=engine.state.cards[card.object_id].logical_object_id
        self.assertEqual('Creature',engine._effective_card_data(engine.state.cards[card.object_id])['type_line'])
        self.assertTrue(session.act('pilot:A',{'action_id':'turn-face-up:'+card.ref,'pay':'auto'}).ok)
        current=engine.state.cards[card.object_id]
        self.assertEqual(identity,current.logical_object_id)
        self.assertEqual('Land',engine._effective_card_data(current)['type_line'])
        action=next(row for row in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
            if row['id'].startswith('activate:'+card.ref+':') and 'Add {C}' in row['label'])
        self.assertTrue(session.act('pilot:A',{'action_id':action['id'],'pay':'auto'}).ok)
        self.assertEqual(1,engine.state.players['A'].mana_pool['C'])
        H.replay(self,session,load=True)

    def test_current_status_reads_are_pure_and_malformed_method_frames_fail_closed(self):
        from copy import deepcopy
        from dataclasses import replace
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        from quorune.compiled_morph import current_face_down_spell_is_trusted
        from quorune.errors import GameRuleError
        from quorune.record import authoritative_state_hash
        from quorune.stack_resolution import trusted_generic_empty_resolution
        from quorune.token_creation import token_copy_snapshot
        session=H.session(self,70802704);engine=session.engine
        card=H.add(self,engine,'Defenestrated Phantom',zone='hand',ref='METHOD_SOURCE')
        H.ready(self,session,card,{'C':3})
        self.assertTrue(session.act('pilot:A',{'action_id':'cast-disguise:'+card.ref,'pay':'auto'}).ok)
        item=next(row for row in engine.state.stack if row.card_object_id==card.object_id)
        before=authoritative_state_hash(engine.state)
        values=engine._copyable_characteristics(card);snapshot=token_copy_snapshot(engine,card)
        self.assertEqual(('', 'Creature',['Ward {2}']),(values['name'],values['type_line'],values['keywords']))
        self.assertEqual('custom-copy:face-down',snapshot['oracle_id']);self.assertEqual('',snapshot['printed_name'])
        self.assertTrue(current_face_down_spell_is_trusted(engine,item,card))
        self.assertIsNone(trusted_generic_empty_resolution(engine,replace(item,semantic_key='builtin:unknown-face-down'),None))
        for field,value in (('schema_version',True),('kind','unknown'),('spec_fingerprint','stale')):
            annotations=deepcopy(card.annotations);annotations['face_down_method'][field]=value
            self.assertFalse(current_face_down_spell_is_trusted(engine,item,replace(card,annotations=annotations)))
        malformed=replace(card,annotations={key:value for key,value in card.annotations.items() if key!='face_down_characteristics'})
        with self.assertRaises(GameRuleError):engine._copyable_characteristics(malformed)
        self.assertEqual(before,authoritative_state_hash(engine.state))

    def test_anonymous_spell_copy_keeps_builtin_execution_provenance(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        from quorune.record_trust import semantic_execution_provenance_row
        from quorune.stack_resolution import physical_stack_record
        session=H.session(self,70802705);engine=session.engine
        card=H.add(self,engine,'Lumithread Field',zone='hand',ref='PROVENANCE_SOURCE')
        H.ready(self,session,card,{'C':3})
        self.assertTrue(session.act('pilot:A',{'action_id':'cast-morph:'+card.ref,'pay':'auto'}).ok)
        original=next(row for row in engine.state.stack if row.card_object_id==card.object_id)
        copied=engine._copy_stack_item(controller='A',target=original,targets=[],target_groups={},reason='anonymous provenance control')
        copy_card=engine.state.cards[copied.card_object_id]
        self.assertIsNone(physical_stack_record(engine,copied))
        current=semantic_execution_provenance_row(engine,copied.semantic_key,capability_registry=self.registry,profile='commander_review',stack_item=copied)
        copy_card.oracle_id=card.oracle_id
        prior=semantic_execution_provenance_row(engine,copied.semantic_key,capability_registry=self.registry,profile='commander_review',stack_item=copied)
        self.assertEqual(prior,current)
        self.assertTrue(current['builtin']);self.assertIsNone(current['oracle_id'])
