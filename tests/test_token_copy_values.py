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
        build_fixture_database([ROOT / "tests/fixtures" / name for name in (
            "bound-effect-program-cards.json", "token-copy-recipe-cards.json",
            "copied-aura-token-entry-cards.json",
        )], path)
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

    def test_actual_aura_token_copies_choose_independent_attachments_before_entry(self):
        from dataclasses import replace
        import json

        from quorune.card_programs import bind_card_program_runtime
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.engine import TURN_STEPS
        from quorune.record import authoritative_state_hash
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as Helpers
        from test_self_public_zone_casting import SelfPublicZoneRuntimeTests

        session = Helpers.session(self, 743301)
        engine = session.engine
        replication = Helpers.add(self, engine, "Extravagant Replication")
        aura = Helpers.add(self, engine, "Mind Control", zone="hand")
        original = Helpers.add(self, engine, "Generic Bound Body", seat="B", ref="original-host")
        another = Helpers.add(self, engine, "Generic Bound Body", seat="B", ref="second-copy-host")
        shroud = Helpers.add(self, engine, "Elvish Lookout", seat="C")
        doubler = Helpers.add(self, engine, "Parallel Lives")
        private = session.state.cards[session.state.players["D"].zones["hand"][0]]
        engine.move_card(aura.object_id, "battlefield", aura_target_ref=original.ref)
        self.assertEqual("A", session.state.cards[original.object_id].controller)
        for card in (replication, aura, shroud, doubler):
            record = self.db.by_oracle_id(card.oracle_id)
            program = compile_best_available_card_program(
                self.db, record, semantic_registry=engine.semantics, capability_registry=self.registry,
                capability_profile="commander_review",
            )
            bound = bind_card_program_runtime(program, capability_registry=self.registry, profile="commander_review")
            self.assertTrue(bound["strict_capability_ready"], (card.printed_name, bound["blockers"]))

        SelfPublicZoneRuntimeTests.prepare(self, session, actor="D", active="D")
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.phase, engine.state.step = "ending", "end_step"
        engine.state.phase_index = TURN_STEPS.index(("ending", "end_step"))
        engine.state.last_normal_turn_player = "D"
        if engine.state.current_turn is not None:
            engine.state.current_turn = replace(engine.state.current_turn, player="D")
        engine._grant_priority("D")
        engine.pump()
        Helpers.checkpoint(self, session)
        for _ in range(4):
            principal, = session.pending_principals()
            result = session.act(principal, {"action_id": "pass"})
            self.assertTrue(result.ok, result.summary)
        self.assertEqual("semantic.target", session.state.pending_decision.kind)
        target = session.act("pilot:A", {"action_id": "choose", "targets": [aura.ref]})
        self.assertTrue(target.ok, target.summary)
        self.assertEqual([aura.ref], session.state.stack[-1].targets)
        for _ in range(4):
            principal, = session.pending_principals()
            result = session.act(principal, {"action_id": "pass"})
            self.assertTrue(result.ok, result.summary)

        decision = session.state.pending_decision
        self.assertEqual("aura.entry", decision.kind)
        self.assertEqual(["A"], decision.actors)
        self.assertEqual([aura.ref], session.state.stack[-1].targets)
        self.assertEqual([], [card.ref for card in session.state.cards.values() if card.is_token])
        packet = session.packet("pilot:A", full=True)
        self.assertNotIn(private.ref, json.dumps(packet))
        self.assertIsNone(session.packet("pilot:B", full=True)["decision"])
        self.assertIn(shroud.ref, packet["decision"]["ctx"]["target_schema"]["legal_refs"])

        before = authoritative_state_hash(session.state)
        for seat, ref in (("B", another.ref), ("A", replication.ref)):
            rejected = session.act(f"pilot:{seat}", {"action_id": "choose", "aura_target": ref})
            self.assertFalse(rejected.ok)
            self.assertEqual(before, authoritative_state_hash(session.state))
        first = session.act("pilot:A", {"action_id": "choose", "aura_target": shroud.ref})
        self.assertTrue(first.ok, first.summary)
        self.assertEqual("aura.entry", session.state.pending_decision.kind)
        self.assertEqual([], [card.ref for card in session.state.cards.values() if card.is_token])
        session = Helpers.replay(self, session, load=True)

        second = session.act("pilot:A", {"action_id": "choose", "aura_target": another.ref})
        self.assertTrue(second.ok, second.summary)
        self.assertFalse(session.state.stack)
        copies = [card for card in session.state.cards.values() if card.is_token and card.zone == "battlefield"]
        self.assertEqual(2, len(copies))
        self.assertEqual({shroud.object_id, another.object_id}, {card.attached_to for card in copies})
        self.assertEqual({"Mind Control"}, {session.engine.display_name(card.object_id) for card in copies})
        self.assertEqual(original.object_id, session.state.cards[aura.object_id].attached_to)
        for host in (original, shroud, another):
            self.assertEqual("A", session.state.cards[host.object_id].controller)
        self.assertEqual("A", session.state.priority_player)
        Helpers.replay(self, session, load=True)
