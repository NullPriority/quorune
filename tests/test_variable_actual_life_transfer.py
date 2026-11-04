"""Actual aggregate loss uses the existing declared/public quantity owners."""
import unittest
from dataclasses import replace
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from test_granted_skulk import record


class VariableActualLifeTransferCompilerTests(unittest.TestCase):
    def test_mana_x_and_known_public_quantities_close_the_entire_actual_gain_result(self):
        registry=load_default_capability_registry()
        cases=((replace(record('Each opponent loses X life. You gain life equal to the life lost this way.','Sorcery'),mana_cost='{X}{B}{B}',mana_value=2),'resolve'),
            (record('When this creature enters, each opponent loses X life, where X is your devotion to black. You gain life equal to the life lost this way.','Creature — Zombie'),'permanent.enter.self'),
            (record('When this creature enters, each opponent loses life equal to the number of Vampires you control. You gain life equal to the life lost this way.','Creature — Vampire'),'permanent.enter.self'))
        for card,event in cases:
            with self.subTest(text=card.oracle_text):
                ir=compile_oracle_card(card,capability_registry=registry)
                self.assertEqual('exact',ir.status,ir.material_residuals)
                node=ir.faces[0].nodes[0];self.assertEqual(event,node.event)
                self.assertEqual('life_loss_then_gain_actual',node.effects[0]['op'])
                self.assertIsInstance(node.effects[0]['amount'],dict)
                self.assertIn('life.transfer.actual_opponent_loss',node.capability_dependencies)

    def test_zero_resolved_quantity_creates_neither_loss_nor_gain_events(self):
        from types import SimpleNamespace
        from quorune.semantic_choices.actual_life_transfer import ActualLifeTransferHandler
        context=SimpleNamespace(actor='A',query=SimpleNamespace(active_seats=tuple('ABCD')),source_ref='SOURCE',stack_label='Zero actual transfer')
        effect={'op':'life_loss_then_gain_actual','schema_version':1,'player':'A','amount':0}
        preparation=ActualLifeTransferHandler().prepare(effect,context)
        self.assertFalse(preparation.preparation_intents)
        self.assertIsNone(preparation.auto_continue.amount_from_result)
        gain=preparation.auto_continue.prepend_effects[0]
        self.assertEqual(0,gain['amount']);self.assertFalse(ActualLifeTransferHandler().prepare(gain,context).preparation_intents)

    def test_unknown_correlations_other_player_quantities_and_unowned_coefficients_remain_residual(self):
        registry=load_default_capability_registry()
        for text in ('Each opponent loses X life, where X is that player\'s life total. You gain life equal to the life lost this way.',
                'Each opponent loses life equal to the number of attacking creatures you control. You gain life equal to the life lost this way.',
                'Each opponent loses life equal to the number of Vampires you control. You gain life equal to the damage dealt this way.',
                'Each opponent loses life equal to the number of Vampires you control. You gain life equal to the life lost this way. Rewrite history.'):
            self.assertNotEqual('exact',compile_oracle_card(record(text,'Sorcery'),capability_registry=registry).status)
        debt=replace(record('Each opponent loses two times X life. You gain life equal to the life lost this way.','Sorcery'),mana_cost='{X}{W}{W}{B}{B}',mana_value=4)
        self.assertNotEqual('exact',compile_oracle_card(debt,capability_registry=registry).status)

    def test_verified_quantity_withdrawals_keep_fixed_actual_transfer_available(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        from quorune.query_effect_amount_model import DECLARED_AMOUNT_CAPABILITY,PUBLIC_QUERY_AMOUNT_CAPABILITY
        original=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text())
        declarations=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json');known=set(discover_tests(ROOT).values())
        for missing,text in ((DECLARED_AMOUNT_CAPABILITY,'Each opponent loses X life. You gain life equal to the life lost this way.'),
                (PUBLIC_QUERY_AMOUNT_CAPABILITY,'Each opponent loses X life, where X is your devotion to black. You gain life equal to the life lost this way.')):
            raw=json.loads(json.dumps(original));next(c for c in raw['capabilities'] if c['id']==missing).update(status='blocked',blockers=['Quantity authority withdrawn'])
            registry=CapabilityRegistry(raw);index=build_index(registry_value=raw,declaration_source=declarations,discovered_test_ids=known)
            registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
            card=replace(record(text,'Sorcery'),mana_cost='{X}{B}{B}',mana_value=2) if missing==DECLARED_AMOUNT_CAPABILITY else record(text,'Sorcery')
            self.assertNotEqual('exact',compile_oracle_card(card,capability_registry=registry).status)
            self.assertEqual('exact',compile_oracle_card(record('Each opponent loses 3 life. You gain life equal to the life lost this way.','Sorcery'),capability_registry=registry).status)

    def test_nonintegral_negative_and_wrong_actor_resolved_amounts_remain_rejected(self):
        from types import SimpleNamespace
        from quorune.semantic_choices.actual_life_transfer import ActualLifeTransferHandler
        context=SimpleNamespace(actor='A',query=SimpleNamespace(active_seats=tuple('ABCD')),source_ref='SOURCE',stack_label='Actual transfer')
        effect={'op':'life_loss_then_gain_actual','schema_version':1,'player':'A','amount':0}
        for change in ({'amount':True},{'amount':-1},{'amount':1.0},{'player':'B'},{'extra':1}):
            with self.subTest(change=change),self.assertRaises(ValueError):ActualLifeTransferHandler().prepare({**effect,**change},context)

    def test_actual_loss_amount_slot_omission_is_killed(self):
        from unittest.mock import patch
        from quorune.compiler.declared_effect_amounts import _RESULT_FIELDS
        fields={k:v for k,v in _RESULT_FIELDS.items() if k!='life_loss_then_gain_actual'}
        with patch.dict('quorune.compiler.declared_effect_amounts._RESULT_FIELDS',fields,clear=True):
            result=unittest.TestResult()
            VariableActualLifeTransferCompilerTests('test_mana_x_and_known_public_quantities_close_the_entire_actual_gain_result').run(result)
        self.assertFalse(result.errors,result.errors);self.assertTrue(result.failures)


import json,tempfile
from pathlib import Path
from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.record import authoritative_state_hash
from scripts.build_test_database import build_fixture_database


class VariableActualLifeTransferNativeTests(InheritedNativeFixture,unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'variable-actual-life.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/variable-actual-life-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Variable actual transfer',[DeckEntry('Isamaru, Hound of Konda',1,'commander'),DeckEntry('Generic Bound Plains',14)],['Isamaru, Hound of Konda'])

    def reject(self,s,seat,command):
        before=authoritative_state_hash(s.state);result=s.act('pilot:'+seat,command)
        self.assertFalse(result.ok,result.summary);self.assertEqual(before,authoritative_state_hash(s.state))

    def cast_x(self,s,spell,x):
        self.priority(s,'A');command={'action_id':'cast:'+spell.ref,'x':x,'targets':[],'pay':'auto'}
        self.reject(s,'B',command);result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)

    def test_actual_exsanguinate_zero_and_positive_x_preserve_events_mana_privacy_and_pending_replay(self):
        for x in (0,3):
            with self.subTest(x=x):
                s=self.session(119700510+x);spell=self.add(s.engine,'Exsanguinate',zone='hand')
                pride=self.add(s.engine,"Ajani's Pridemate");private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='variable-life-private')
                self.assert_closed(s,spell);self.assert_closed(s,pride)
                self.prepare(s,mana={'B':2,'C':3});self.checkpoint(s)
                self.reject(s,'A',{'action_id':'cast:'+spell.ref,'x':-1,'targets':[],'pay':'auto'})
                self.cast_x(s,spell,x);self.assertEqual(3-x,sum(s.state.players['A'].mana_pool.values()))
                s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
                self.assertEqual(40+3*x,s.state.players['A'].life)
                self.assertEqual((40-x,)*3,tuple(s.state.players[p].life for p in 'BCD'))
                self.assertEqual(int(x>0),s.state.cards[pride.object_id].counters.get('+1/+1',0))
                for viewer in 'ABD':self.assertNotIn(private.ref,json.dumps(s.packet('pilot:'+viewer,full=True)))
                self.replay(s,load=True)

    def test_actual_copied_exsanguinate_retains_x_and_transfers_to_the_copy_controller(self):
        s=self.session(119700520);spell=self.add(s.engine,'Exsanguinate',zone='hand')
        copy=self.add(s.engine,'Reverberate',seat='B',zone='hand');s.state.players['B'].mana_pool.update({'R':2})
        self.assert_closed(s,spell);self.assert_closed(s,copy)
        self.prepare(s,mana={'B':2,'C':3});self.checkpoint(s);self.cast_x(s,spell,3)
        self.cast(s,'B',copy,[s.state.stack[-1].ref]);self.resolve_top(s)
        self.assertEqual('spell_copy',s.state.stack[-1].kind);s=self.replay(s,load=True)
        self.resolve_top(s);self.assertEqual((37,49,37,37),tuple(s.state.players[p].life for p in 'ABCD'))
        self.assertIsNone(self.resolve(s));self.assertEqual((46,46,34,34),tuple(s.state.players[p].life for p in 'ABCD'))
        self.replay(s,load=True)

    def test_actual_merchant_reads_current_ability_controller_devotion_after_source_control_changes(self):
        s=self.session(119700530);merchant=self.add(s.engine,'Gray Merchant of Asphodel',zone='hand')
        arena=self.add(s.engine,'Phyrexian Arena');hexmage=self.add(s.engine,'Vampire Hexmage')
        theft=self.add(s.engine,'Act of Treason',seat='B',zone='hand');orrery=self.add(s.engine,'Vedalken Orrery',seat='B')
        s.state.players['B'].mana_pool.update({'R':1,'C':2})
        private=self.add(s.engine,'Murder',zone='hand',ref='devotion-private-black-card')
        other_private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='devotion-other-private')
        for card in (merchant,arena,hexmage,theft,orrery):self.assert_closed(s,card)
        self.prepare(s,mana={'B':2,'C':3});self.checkpoint(s);self.cast(s,'A',merchant,[]);self.resolve_top(s)
        self.assertTrue(s.state.stack);self.cast(s,'B',theft,[merchant.ref]);self.resolve_top(s)
        self.assertEqual('B',s.state.cards[merchant.object_id].controller)
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual((52,36,36,36),tuple(s.state.players[p].life for p in 'ABCD'))
        for viewer in 'BCD':self.assertNotIn(private.ref,json.dumps(s.packet('pilot:'+viewer,full=True)))
        for viewer in 'ABD':self.assertNotIn(other_private.ref,json.dumps(s.packet('pilot:'+viewer,full=True)))
        self.replay(s,load=True)
