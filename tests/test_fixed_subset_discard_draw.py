"""CR701.9/121: one private subset discard precedes its same-count draw."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from test_qualified_zone_event_queries import query_record
from quorune.carddb import CardDatabase
from quorune.compiler.discard_result_templates import discard_result_draw_template
from quorune.deck import DeckDefinition, DeckEntry
from quorune.discard_result_model import FIXED_SUBSET_DISCARD_DRAW_CAPABILITY as CAPABILITY
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.discard_result_shapes import discard_result_node_capabilities
from quorune.semantic_choices.library_and_hand import DiscardDrawChoiceHandler
from scripts.build_test_database import build_fixture_database


class FixedSubsetDiscardDrawCompilerTests(unittest.TestCase):
    def test_fixed_subset_route_is_versioned_and_rejects_adjacent_variants(self):
        registry=load_default_capability_registry()
        for text,card_type in (('Discard up to two cards, then draw that many cards.','Sorcery'),
                ('When this creature enters, discard up to two cards, then draw that many cards.','Creature'),
                ('{2}: Discard up to three cards, then draw that many cards.','Artifact')):
            ir=compile_oracle_card(replace(query_record(text),type_line=card_type),capability_registry=registry)
            self.assertEqual('exact',ir.status,ir.material_residuals)
            self.assertEqual('discard_draw_up_to',ir.faces[0].nodes[0].effects[0]['op'])
        compiled=discard_result_draw_template('Discard up to two cards, then draw that many cards.')
        self.assertEqual(({'op':'discard_draw_up_to','schema_version':2,'maximum':2},),compiled[1])
        variable=discard_result_draw_template('Discard any number of cards, then draw that many cards.')
        self.assertEqual(({'op':'discard_draw_up_to','schema_version':3,'maximum':'hand_size','draw_offset':0},),variable[1])
        self.assertEqual(('variable-subset-discard-draw',),variable[3])
        for text in ('Each player discards up to two cards, then draws that many cards.',
                'Discard up to X cards, then draw that many cards.',
                'Discard up to two cards, then draw that many cards. Rewrite history.'):
            self.assertIsNone(discard_result_draw_template(text))

    def test_closed_subset_shape_does_not_admit_malformed_bounds_or_recipient_fields(self):
        effect={'op':'discard_draw_up_to','schema_version':2,'maximum':2}
        def check(value,target=None):return discard_result_node_capabilities(effects=(value,),target_schema=target,
            mechanic_ids=('fixed-subset-discard-draw',))
        self.assertEqual((CAPABILITY,),check(effect))
        for changes in ({'maximum':True},{'maximum':0},{'maximum':-1},{'maximum':'two'},
                {'player':'$target.0'},{'unknown':'open'}):
            self.assertEqual((),check({**effect,**changes}))
        self.assertEqual((),check(effect,{'categories':['player'],'count':1}))

    def test_schema_two_choice_canonicalizes_hand_order_while_absent_marker_keeps_legacy(self):
        rows=(SimpleNamespace(ref='hand-Z',printed_name='last'),SimpleNamespace(ref='hand-A',printed_name='first'))
        handler=DiscardDrawChoiceHandler()
        for order in (rows,tuple(reversed(rows))):
            context=SimpleNamespace(actor='A',stack_ref='stack',stack_label='subset',
                query=SimpleNamespace(objects=lambda **kwargs:order))
            modern=handler.prepare({'op':'discard_draw_up_to','schema_version':2,'maximum':2},context)
            self.assertEqual(('hand-A','hand-Z'),tuple(modern.continuation_effect['_legal_refs']))
            legacy=handler.prepare({'op':'discard_draw_up_to','maximum':2},context)
            self.assertEqual(tuple(row.ref for row in order),tuple(legacy.continuation_effect['_legal_refs']))
        for altered in ({'schema_version':True},{'maximum':True},{'extra':'open'}):
            from quorune.semantic_choices.model import SemanticChoiceError
            with self.assertRaises(SemanticChoiceError):
                handler.prepare({'op':'discard_draw_up_to','schema_version':2,'maximum':2,**altered},context)

    def test_verified_subset_owner_withdrawal_preserves_independent_controller_draw(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        next(r for r in raw['capabilities'] if r['id']==CAPABILITY).update(status='blocked',blockers=['subset owner withdrawn'])
        registry=CapabilityRegistry(raw)
        index=build_index(registry_value=raw,declaration_source=read_evidence_document(
            ROOT/'platform/capability-evidence-declarations.json'),discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
        for text,expected in (('Discard up to two cards, then draw that many cards.',False),('Draw a card.',True)):
            ir=compile_oracle_card(replace(query_record(text),type_line='Sorcery'),capability_registry=registry)
            self.assertEqual(expected,ir.status=='exact')


class FixedSubsetDiscardDrawNativeTests(InheritedNativeFixture,unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory()
        base=json.loads((ROOT/'tests/fixtures/bound-effect-program-cards.json').read_text(encoding='utf-8'))
        base['cards']=[r for r in base['cards'] if r['name']=='Generic Bound Plains'];base['rulings']=[]
        basic=Path(cls.temporary.name)/'basic.json';basic.write_text(json.dumps(base),encoding='utf-8',newline='\n')
        path=Path(cls.temporary.name)/'subset.sqlite3'
        build_fixture_database([basic,ROOT/'tests/fixtures/fixed-subset-discard-draw-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Fixed subset',[DeckEntry('Sliver Hivelord',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Sliver Hivelord'])

    def choose(self,session,selected,private):
        self.assertEqual('semantic.choice',session.state.pending_decision.kind)
        self.assertEqual(['A'],session.state.pending_decision.actors)
        schema=session.packet('pilot:A',full=True)['decision']['ctx']['legal_actions'][0]['choice_schema']
        legal=list(schema['legal_refs'])
        before=authoritative_state_hash(session.state)
        bad=[('B',selected),('A',[private.ref])]
        if selected:bad.append(('A',[selected[0],selected[0]]))
        if len(legal)>2:bad.append(('A',legal[:3]))
        for actor,cards in bad:
            self.assertFalse(session.act('pilot:'+actor,{'action_id':'choose','cards':cards}).ok)
            self.assertEqual(before,authoritative_state_hash(session.state))
        for seat in 'ABD':self.assertNotIn(private.ref,json.dumps(session.packet('pilot:'+seat,full=True)))
        session=self.replay(session,load=True)
        result=session.act('pilot:A',{'action_id':'choose','cards':selected})
        self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(session))
        return session

    def augur_witness(self,seed,*,empty=False):
        session=self.session(seed)
        if empty:
            for oid in tuple(session.state.players['A'].zones['hand']):
                session.engine.move_card(oid,'library',log=False,semantic_events=False)
        source=self.add(session.engine,'Kinetic Augur',zone='hand')
        selected=[] if empty else [self.add(session.engine,name,zone='hand').ref for name in ('Shock','Ponder')]
        private=self.add(session.engine,'Counterspell',seat='C',zone='hand',ref='subset-private-hand')
        self.assert_closed(session,source)
        self.prepare(session,mana={'C':3,'R':1});self.checkpoint(session)
        self.cast(session,'A',source)
        hand=len(session.state.players['A'].zones['hand']);library=len(session.state.players['A'].zones['library'])
        decision=self.resolve(session)
        if empty:self.assertIsNone(decision)
        else:session=self.choose(session,selected,private)
        self.assertEqual(hand,len(session.state.players['A'].zones['hand']),
            'draw count must equal the chosen discard count')
        self.assertEqual(library-len(selected),len(session.state.players['A'].zones['library']))
        for ref in selected:self.assertEqual('graveyard',next(card.zone for card in session.state.cards.values() if card.ref==ref))
        self.assertEqual(len(selected),session.engine._numeric_stat(source.object_id,'power'))
        self.replay(session,load=True)

    def test_actual_augur_discards_before_same_count_draw_and_empty_hand_stays_empty(self):
        self.augur_witness(987100)
        self.augur_witness(987101,empty=True)

    def test_actual_borrowed_sokka_choice_and_artifact_counter_use_its_controller(self):
        session=self.session(987200)
        source=self.add(session.engine,'Sokka, Bold Boomeranger');source.owner='B'
        blink=self.add(session.engine,'Cloudshift',zone='hand')
        discard=self.add(session.engine,'Shock',zone='hand')
        artifact=self.add(session.engine,'Ornithopter',zone='hand')
        private=self.add(session.engine,'Counterspell',seat='C',zone='hand',ref='borrowed-subset-private')
        for card in (source,blink,artifact):self.assert_closed(session,card)
        self.prepare(session,mana={'W':1});self.checkpoint(session)
        self.cast(session,'A',blink,targets=[source.ref]);self.assertEqual('semantic.choice',self.resolve(session).kind)
        hands={seat:len(session.state.players[seat].zones['hand']) for seat in 'AB'}
        session=self.choose(session,[discard.ref],private)
        self.assertEqual(hands,{seat:len(session.state.players[seat].zones['hand']) for seat in 'AB'})
        self.assertEqual('A',session.state.cards[source.object_id].controller)
        self.cast(session,'A',artifact);self.assertIsNone(self.resolve(session))
        self.assertEqual(1,session.state.cards[source.object_id].counters.get('+1/+1',0))
        self.replay(session,load=True)

    def test_fresh_wrong_draw_count_mutant_is_killed_by_actual_hand_consequence(self):
        original=DiscardDrawChoiceHandler.complete;observed=[]
        def short_draw(handler,continuation,response,query):
            result=original(handler,continuation,response,query)
            if result.prepend_effects:
                observed.append(True)
                result=replace(result,prepend_effects=tuple({**dict(effect),'count':max(0,effect['count']-1)} for effect in result.prepend_effects))
            return result
        with patch.object(DiscardDrawChoiceHandler,'complete',short_draw):
            with self.assertRaisesRegex(AssertionError,'draw count must equal'):self.augur_witness(987300)
        self.assertTrue(observed)

    def test_actual_cathartic_modal_subset_uses_only_selected_mode_and_replays(self):
        session=self.session(987500)
        source=self.add(session.engine,'Cathartic Pyre',zone='hand')
        discard=self.add(session.engine,'Shock',zone='hand')
        private=self.add(session.engine,'Counterspell',seat='C',zone='hand',ref='modal-subset-private')
        self.assert_closed(session,source)
        self.prepare(session,mana={'C':1,'R':1});self.checkpoint(session)
        offered=self.action(session,'A',source)
        command={'action_id':offered['id'],'modes':['mode_2'],'pay':'auto'}
        before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:B',command).ok)
        self.assertEqual(before,authoritative_state_hash(session.state))
        result=session.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        self.assertEqual('semantic.choice',self.resolve(session).kind)
        hand=len(session.state.players['A'].zones['hand'])
        life={seat:session.state.players[seat].life for seat in 'ABCD'}
        session=self.choose(session,[discard.ref],private)
        self.assertEqual(hand,len(session.state.players['A'].zones['hand']))
        self.assertEqual(life,{seat:session.state.players[seat].life for seat in 'ABCD'})
        self.assertEqual('graveyard',session.state.cards[discard.object_id].zone)
        self.assertEqual('graveyard',session.state.cards[source.object_id].zone)
        self.replay(session,load=True)
