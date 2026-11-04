"""Copy-value semantics preserve historical absence and exact record hashes."""

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from quorune.model import CardInstance,GameConfig,GameState
from quorune.record_state_provenance import format_state_versions,validate_state_versions
from quorune.token_copy_value_model import TOKEN_COPY_VALUE_VERSION_FIELD as FIELD,token_copy_value_version


class TokenCopyValueVersionTests(unittest.TestCase):
    def state(self):
        return GameState(game_id='copy-value-version',config=GameConfig(),players={},cards={},
            deck_names={},commander_oracle_ids={},turn_order=[],current_turn=None,last_normal_turn_player=None)

    def test_new_state_and_manifest_pin_one_and_historical_absence_stays_absent(self):
        state=self.state();payload=state.to_dict()
        self.assertEqual(1,payload[FIELD]);self.assertEqual(1,format_state_versions(state)[FIELD])
        validate_state_versions({'format':format_state_versions(state)},state)
        payload.pop(FIELD);legacy=GameState.from_dict(payload)
        self.assertIsNone(legacy.token_copy_value_version)
        self.assertNotIn(FIELD,legacy.to_dict());self.assertNotIn(FIELD,format_state_versions(legacy))
        validate_state_versions({'format':format_state_versions(legacy)},legacy)
        self.assertEqual(payload,legacy.to_dict())

    def test_unknown_boolean_and_disagreeing_versions_reject(self):
        for bad in (True,False,0,2,'1',1.0):
            with self.subTest(value=bad),self.assertRaises(ValueError):token_copy_value_version(bad)
            with self.subTest(state=bad),self.assertRaises(ValueError):GameState.from_dict({**self.state().to_dict(),FIELD:bad})
        state=self.state();manifest={'format':format_state_versions(state)}
        for bad in (False,2,0):
            changed=deepcopy(manifest);changed['format'][FIELD]=bad
            with self.subTest(manifest=bad),self.assertRaisesRegex(ValueError,'Token copy value provenance'):
                validate_state_versions(changed,state)
        changed=deepcopy(manifest);changed['format'].pop(FIELD)
        with self.assertRaisesRegex(ValueError,'Token copy value provenance'):validate_state_versions(changed,state)

    def test_annotation_mode_selects_current_copy_values_or_original_legacy_definition(self):
        from quorune.defined_token_faces import defined_token_characteristic_values
        card=CardInstance(object_id='token',ref='T1',oracle_id='custom-token:one',printed_name='One',
            owner='A',controller='A',zone='battlefield',is_token=True,
            annotations={'token_characteristics':{'name':'Original','activated_abilities':[]},
                         'copy_overrides':{'name':'Copied','activated_abilities':[{'copied':True}]}})
        self.assertEqual('Original',defined_token_characteristic_values(card)['name'])
        card.annotations[FIELD]=1
        self.assertEqual('Copied',defined_token_characteristic_values(card)['name'])
        card.annotations[FIELD]=True
        with self.assertRaises(ValueError):defined_token_characteristic_values(card)

    def test_snapshot_and_explicit_copy_names_keep_the_selected_version(self):
        from types import SimpleNamespace
        from quorune.token_creation_identity import token_copy_snapshot,_copied_token_identity
        card=CardInstance(object_id='source',ref='S',oracle_id='custom-token:source',printed_name='Physical label',
            owner='A',controller='A',zone='battlefield',is_token=True)
        for mode,expected in ((None,'Physical label'),(1,'Current copied name')):
            host=SimpleNamespace(state=SimpleNamespace(token_copy_value_version=mode),
                _copyable_characteristics=lambda source:{'name':'Current copied name','type_line':'Artifact'})
            snapshot=token_copy_snapshot(host,card)
            self.assertEqual(expected,snapshot['printed_name'])
            identity=_copied_token_identity(host,'A',copy_of=card.ref,name='',characteristics={},ref='T1',copy_snapshot=snapshot)
            self.assertEqual(expected,identity[2])

    def test_prior_pinned_runtime_is_rejected_before_checkpoint_or_commands(self):
        import json
        from unittest.mock import patch
        from common import ROOT
        from quorune.record import ENGINE_VERSION,replay_record,semantics_fingerprint
        from quorune.record_trust import runtime_trust_provenance
        from quorune.semantics import SemanticRegistry
        recipe=json.loads((ROOT/'tests/fixtures/historical-token-copy-provenance-recipe.json').read_text(encoding='utf8'))
        semantics=SemanticRegistry();current=runtime_trust_provenance()
        historical=deepcopy(current);historical.update(recipe['runtime_trust_fingerprints'])
        self.assertNotEqual(current,historical)
        self.assertEqual(ENGINE_VERSION,recipe['engine_version'])
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)
            (path/'manifest.json').write_text(json.dumps({'schema_version':3,'engine_version':ENGINE_VERSION,
                'semantics_fingerprint':semantics_fingerprint(semantics),'runtime_trust':historical}),encoding='utf8')
            with patch('quorune.record_trust.SemanticRegistry',return_value=semantics),patch('quorune.record.read_initial_checkpoint') as checkpoint:
                with self.assertRaisesRegex(ValueError,recipe['current_pinned_replay_disposition']):
                    replay_record(path,None,verify=True)
                checkpoint.assert_not_called()


class TokenCopyValueNativeTests(unittest.TestCase):
    register_fixture_cards_on_add=True

    @classmethod
    def setUpClass(cls):
        from common import ROOT
        from quorune.carddb import CardDatabase
        from quorune.deck import DeckDefinition,DeckEntry
        from quorune.rules.capabilities import load_default_capability_registry
        from scripts.build_test_database import build_fixture_database
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'copy-values.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/token-copy-recipe-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Copy-value witness',[DeckEntry('Generic Bound Commander',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()

    def test_actual_populate_uses_selected_mode_and_round_trips_pending_record(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as Helpers
        for index,mode in enumerate((1,None)):
            with self.subTest(mode=mode):
                session=Helpers.session(self,919010+index);engine=session.engine
                engine.state.token_copy_value_version=mode
                ref=engine.create_token('A',name='Original token',characteristics={
                    'type_line':'Token Creature — Elf','power':'2','toughness':'2','colors':['G']})[0]
                original=engine._resolve_object('A',ref,zones={'battlefield'})
                original.annotations['until_end_of_turn']={'compatibility_witness':True}
                source=Helpers.add(self,engine,'Generic Recipe Populate',zone='hand')
                action=Helpers.ready(self,session,source,{'W':1});Helpers.checkpoint(self,session)
                accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'})
                self.assertTrue(accepted.ok,accepted.summary);Helpers.resolve(self,session)
                session=Helpers.replay(self,session,load=True);engine=session.engine
                self.assertEqual(mode,engine.state.token_copy_value_version)
                accepted=session.act('pilot:A',{'action_id':'choose','card':ref})
                self.assertTrue(accepted.ok,accepted.summary);Helpers.resolve(self,session)
                copied=next(card for card in engine.state.cards.values() if card.is_token
                    and card.zone=='battlefield' and card.object_id!=original.object_id)
                if mode==1:
                    self.assertEqual(1,copied.annotations[FIELD])
                    self.assertNotIn('until_end_of_turn',copied.annotations)
                    self.assertNotIn('token_characteristics',copied.annotations)
                else:
                    self.assertNotIn(FIELD,copied.annotations)
                    self.assertIn('until_end_of_turn',copied.annotations)
                    self.assertIn('token_characteristics',copied.annotations)
                    expected=deepcopy(engine.state.cards[original.object_id].annotations)
                    expected['copied_from']=original.object_id
                    expected['copy_overrides']={**expected['copy_overrides'],'name':'Original token'}
                    self.assertEqual(expected,copied.annotations)
                self.assertNotIn(FIELD,engine._copyable_characteristics(copied))
                Helpers.replay(self,session,load=True)
