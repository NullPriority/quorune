"""CR613.1: bounded public queries include their completed final layer."""
from pathlib import Path
from types import SimpleNamespace
import json,tempfile,unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.characteristic_query_boundary_model import characteristic_query_boundary_version
from quorune.continuous_effect_model import Layer
from quorune.deck import DeckDefinition,DeckEntry
from quorune.model import GameConfig,GameState
from quorune.record import authoritative_state_hash
from quorune.record_state_provenance import format_state_versions,validate_state_versions
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_bound_static_declarations as controls


class CompletedCharacteristicQueryOwnerTests(unittest.TestCase):
    def test_current_and_absent_boundary_round_trip_with_exact_record_provenance(self):
        state=GameState(game_id='completed-characteristic-query',config=GameConfig(),players={},cards={},
            deck_names={},commander_oracle_ids={},turn_order=[],current_turn=None,last_normal_turn_player=None)
        wire=state.to_dict();self.assertEqual(2,wire['characteristic_query_boundary_version'])
        self.assertEqual(wire,GameState.from_dict(wire).to_dict())
        current={'format':format_state_versions(state)};validate_state_versions(current,state)
        prior_wire={**wire,'characteristic_query_boundary_version':1}
        prior=GameState.from_dict(prior_wire);self.assertEqual(prior_wire,prior.to_dict())
        prior_manifest={'format':format_state_versions(prior)};validate_state_versions(prior_manifest,prior)
        old={k:v for k,v in wire.items() if k!='characteristic_query_boundary_version'}
        legacy=GameState.from_dict(old);self.assertIsNone(legacy.characteristic_query_boundary_version)
        self.assertEqual(old,legacy.to_dict());historical={'format':format_state_versions(legacy)}
        self.assertNotIn('characteristic_query_boundary_version',historical['format'])
        validate_state_versions(historical,legacy)
        for manifest,checkpoint in ((current,legacy),(historical,state),(current,prior),(prior_manifest,state)):
            with self.assertRaises(ValueError):validate_state_versions(manifest,checkpoint)
        for bad in (True,False,0,3,'1',{},[]):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):characteristic_query_boundary_version(bad)
                with self.assertRaises(ValueError):GameState.from_dict({**wire,'characteristic_query_boundary_version':bad})
                with self.assertRaises(ValueError):validate_state_versions(
                    {'format':{**current['format'],'characteristic_query_boundary_version':bad}},state)

    def test_source_cutoff_includes_only_completed_requested_layers(self):
        from quorune.card_programs import runtime
        program=SimpleNamespace(key='pure-static-source',handlers=({},),provenance={},
            active_zone='battlefield',event='characteristics.evaluate',ability_id='static:pure-source')
        semantics=SimpleNamespace(runtime_handler_programs_for_oracle=lambda *_a,**_k:[program],get=lambda key:program)
        source=SimpleNamespace(zone='battlefield',oracle_id='pure-source',annotations={})
        for start in (Layer.TYPE,Layer.COLOR):
            registry=SimpleNamespace(source_component_layer=lambda _descriptor,value=start:value)
            with patch.object(runtime,'default_continuous_effect_component_registry',return_value=registry):
                for current in (False,True):
                    for limit in (Layer.TEXT,Layer.TYPE,Layer.COLOR,Layer.ABILITY,None):
                        calls=[]
                        def before_start(card,layer):
                            self.assertIs(card,source);calls.append(layer);return (program.key,)
                        found=runtime._continuous_programs_for_source(semantics,source,lambda _s:(),
                            before_start,limit,completed_layer_queries=current)
                        expected=limit is None or limit>start or current and limit==start
                        with self.subTest(start=start,limit=limit,current=current):
                            self.assertEqual((program,) if expected else (),found)
                            self.assertEqual([start] if expected else [],calls)
                self.assertEqual((),runtime._continuous_programs_for_source(semantics,source,lambda _s:(),
                    lambda _s,_l:(),None,completed_layer_queries=True),'Absent source authority still excludes the effect')
                with self.assertRaises(ValueError):
                    runtime._continuous_programs_for_source(semantics,source,lambda _s:(),None,None,
                        completed_layer_queries=True)


class CompletedCharacteristicQueryNativeTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=controls.BoundStaticDeclarationNativeTests.session
    add=controls.BoundStaticDeclarationNativeTests.add
    prepare=controls.BoundStaticDeclarationNativeTests.prepare
    cast_for=controls.BoundStaticDeclarationNativeTests.cast_for
    action=controls.BoundStaticDeclarationNativeTests.action
    priority=controls.BoundStaticDeclarationNativeTests.priority
    checkpoint=controls.BoundStaticDeclarationNativeTests.checkpoint
    resolve=controls.BoundStaticDeclarationNativeTests.resolve
    replay=controls.BoundStaticDeclarationNativeTests.replay
    assert_closed=controls.BoundStaticDeclarationNativeTests.assert_closed
    stats=controls.BoundStaticDeclarationNativeTests.stats

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'completed-characteristic-queries.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/bound-static-declaration-cards.json',
            ROOT/'tests/fixtures/static-action-permission-cards.json',
            ROOT/'tests/fixtures/static-characteristic-setting-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Completed characteristic queries',[DeckEntry('Isamaru, Hound of Konda',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Isamaru, Hound of Konda'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()

    def test_original_dryad_counts_the_graveyard_front_and_keeps_historical_view(self):
        for index, (version, real_land, expected) in enumerate(((2, False, 1), (2, True, 3), (1, False, 3))):
            with self.subTest(version=version, real_land=real_land):
                session = self.session(712_008_310 + index)
                session.state.characteristic_query_boundary_version = version
                dryad = self.add(session.engine, 'Gnarlwood Dryad', zone='hand')
                modal = self.add(session.engine, 'Bala Ged Recovery // Bala Ged Sanctuary', zone='graveyard')
                self.add(session.engine, 'Generic Bound Growth', zone='graveyard')
                self.add(session.engine, 'Generic Bound Body', zone='graveyard')
                if real_land:
                    self.add(session.engine, 'Generic Bound Plains', zone='graveyard', ref='actual-graveyard-land')
                for card in (dryad, modal):
                    self.assert_closed(session, card)
                self.prepare(session, mana={'G': 1})
                self.checkpoint(session)
                self.cast_for(session, 'A', dryad, [])
                self.assertIsNone(self.resolve(session))
                self.assertEqual((expected, expected), self.stats(session, dryad))
                effective = session.engine._effective_card_data(session.state.cards[modal.object_id])
                self.assertEqual('Sorcery' if version == 2 else 'Sorcery // Land', effective['type_line'])
                self.assertEqual(3, effective['mana_value'])
                self.replay(session, load=True)

    def test_original_crucible_offers_modal_back_land_from_corrected_graveyard_view(self):
        session = self.session(712_012_310)
        crucible = self.add(session.engine, 'Crucible of Worlds')
        modal = self.add(session.engine, 'Bala Ged Recovery // Bala Ged Sanctuary', zone='graveyard')
        self.assert_closed(session, crucible)
        self.assert_closed(session, modal)
        self.prepare(session)
        self.checkpoint(session)
        self.assertEqual('Sorcery', session.engine._effective_card_data(modal)['type_line'])
        actions = session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
        offer = next(row for row in actions if row.get('card') == modal.ref and row.get('action') == 'play_land')
        result = session.act('pilot:A', {'action_id': offer['id']})
        self.assertTrue(result.ok, result.summary)
        current = session.state.cards[modal.object_id]
        self.assertEqual(('battlefield', 'Bala Ged Sanctuary'), (current.zone, current.active_face))
        self.assertEqual('Land', session.engine._effective_card_data(current)['type_line'])
        self.replay(session)

    def color_scene(self,*,current):
        session=self.session(613_105_106);engine=session.engine
        if not current:engine.state.characteristic_query_boundary_version=None
        body=self.add(engine,'Isamaru, Hound of Konda',ref='original-color-recipient')
        aura=self.add(engine,'Steel of the Godhead',zone='hand',ref='original-conditional-aura')
        darkness=self.add(engine,'Darkest Hour',zone='hand',ref='original-color-setter')
        bounce=self.add(engine,'Generic Setting Bounce',zone='hand',ref='color-source-return')
        private=self.add(engine,'Twiddle',seat='D',zone='hand',ref='private-color-control')
        for source in (body,aura,darkness):self.assert_closed(session,source)
        self.prepare(session,mana={'C':3,'W':2,'B':1});self.checkpoint(session)
        self.cast_for(session,'A',aura,[body.ref],reserve={'B':1});self.assertIsNone(self.resolve(session))
        self.assertEqual((3,3),self.stats(session,body));self.assertIn('lifelink',engine._combat_keywords(body))
        self.priority(session,'A');offered=self.action(session,'A',darkness)
        before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:B',{'action_id':offered['id'],'pay':'auto'}).ok)
        self.assertEqual(before,authoritative_state_hash(session.state))
        result=session.act('pilot:A',{'action_id':offered['id'],'pay':'auto'})
        self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(session))
        body=session.state.cards[body.object_id]
        self.assertEqual({'B'},set(engine._effective_card_data(body)['colors']))
        self.assertEqual((2,2) if current else (3,3),self.stats(session,body),
            'Completed color condition must end the white-dependent bonus')
        self.assertEqual({'B'} if current else {'W'},set(engine._public_object_query_result(body).colors))
        self.assertEqual(not current,'lifelink' in engine._combat_keywords(body))
        for seat in 'ABC':self.assertNotIn(private.ref,json.dumps(session.packet('pilot:'+seat,full=True)))
        resumed=self.replay(session,load=True)
        self.assertEqual(current,'characteristic_query_boundary_version' in resumed.state.to_dict())
        return (resumed,resumed.state.cards[body.object_id],
            resumed.state.cards[darkness.object_id],resumed.state.cards[bounce.object_id])

    def test_original_color_setter_ends_white_bonus_and_departure_restores_it_after_load(self):
        session,body,darkness,bounce=self.color_scene(current=True)
        self.cast_for(session,'A',bounce,[darkness.ref]);self.assertIsNone(self.resolve(session))
        body=session.state.cards[body.object_id]
        self.assertEqual('hand',session.state.cards[darkness.object_id].zone)
        self.assertEqual({'W'},set(session.engine._effective_card_data(body)['colors']))
        self.assertEqual((3,3),self.stats(session,body));self.assertIn('lifelink',session.engine._combat_keywords(body))
        self.replay(session,load=True)

    def test_historical_absence_retains_former_boundary_with_exact_replay(self):
        self.color_scene(current=False)

    def test_omitted_completed_boundary_is_killed_by_actual_color_consequences(self):
        with patch('quorune.card_programs.runtime.characteristic_query_boundary_version',return_value=None):
            with self.assertRaisesRegex(self.failureException,'Completed color condition'):
                self.color_scene(current=True)

    def test_completed_type_query_retains_actual_animation_before_ability_loss(self):
        session=self.session(613_104_106);engine=session.engine
        artifact=self.add(engine,'Generic Setting Artifact')
        aura=self.add(engine,'Ensoul Artifact',zone='hand')
        humility=self.add(engine,'Humility',zone='hand')
        for card in (aura,humility):self.assert_closed(session,card)
        self.prepare(session,mana={'C':3,'U':1,'W':2});self.checkpoint(session)
        self.cast_for(session,'A',aura,[artifact.ref],reserve={'C':2,'W':2});self.assertIsNone(self.resolve(session))
        current=engine._effective_card_data(artifact,maximum_layer=Layer.TYPE)
        self.assertIn('Creature',current['type_line']);self.assertEqual((5,5),self.stats(session,artifact))
        self.cast_for(session,'A',humility);self.assertIsNone(self.resolve(session))
        current=engine._effective_card_data(session.state.cards[artifact.object_id],maximum_layer=Layer.TYPE)
        self.assertIn('Creature',current['type_line']);self.assertEqual((1,1),self.stats(session,artifact))
        self.replay(session,load=True)


if __name__=='__main__':unittest.main()
