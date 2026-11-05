"""Printed-order separators preserve subjects, targets and temporal effects."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
import quorune.compiler.fixed_effect_clause_sequences as owner
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from test_granted_skulk import record

CAPABILITY='resolution.effect_sequence.fixed_clauses'
BURNING='Each player draws three cards, then discards three cards at random.'


class OrderedClauseTests(unittest.TestCase):
    def test_top_level_delimiters_and_nested_text_keep_complete_clauses(self):
        self.assertEqual(('Draw a card.','gain 1 life.'),owner._two_sentence_clauses('Draw a card, then gain 1 life.'))
        self.assertEqual(('Draw a card.','gain 1 life.'),owner._two_sentence_clauses('Draw a card. Then gain 1 life.'))
        self.assertEqual(('Draw a card.','Gain 1 life.'),owner._two_sentence_clauses('Draw a card. Gain 1 life.'))
        quoted='Create a token with "Draw a card, then discard a card", then gain 1 life.'
        self.assertEqual(('Create a token with "Draw a card, then discard a card".','gain 1 life.'),owner._two_sentence_clauses(quoted))
        nested='Draw a card (then discard a card), then gain 1 life.'
        self.assertEqual(('Draw a card (then discard a card).','gain 1 life.'),owner._two_sentence_clauses(nested))

    def test_elided_player_subjects_are_not_silently_controller_only(self):
        parsed=owner._two_sentence_clauses(BURNING)
        self.assertIsNotNone(parsed)
        first,second=parsed
        self.assertEqual('Each player draws three cards.',first)
        self.assertEqual('Each player discards three cards at random.',second)
        self.assertEqual('Each opponent discards a card.',owner._two_sentence_clauses('Each opponent draws a card, then discards a card.')[1])
        ir=compile_oracle_card(record(BURNING,'Sorcery'),capability_registry=load_default_capability_registry(),capability_profile='commander_review')
        self.assertEqual('exact',ir.status,ir.material_residuals)
        self.assertEqual('draw_each_player',ir.faces[0].nodes[0].effects[0]['op'])
        self.assertEqual('all',ir.faces[0].nodes[0].effects[1]['players'])

    def test_linked_conditional_optional_and_partial_forms_remain_outside_new_grammar(self):
        for text in (
            'Draw a card, then discard that card.',
            'Exile a card, then you may cast it.',
            'Search your library for a card, then shuffle.',
            'You may draw a card, then discard a card.',
            'Draw a card. Then if you gained life, draw a card.',
            'Draw a card, then when you do, gain 1 life.',
            'Draw a card, then gain 1 life. trailing',
            'Draw a card, then discards a card.',
            'Draw a card, then gain 1 life',
            'Draw a card (then gain 1 life.',
        ):
            with self.subTest(text=text):self.assertIsNone(owner._two_sentence_clauses(text))

    def test_existing_one_owned_instruction_and_target_component_limits_remain(self):
        def compile_clause(text):
            return ('leaf',({'op':'draw','player':'$controller','count':1},),
                {'zones':['player'],'count':1} if text.casefold().startswith('target') else None,('cr-121-drawing-a-card',))
        self.assertIsNotNone(owner.fixed_effect_clause_sequence_template('Draw a card, then draw a card.',compile_clause=compile_clause))
        self.assertIsNone(owner.fixed_effect_clause_sequence_template('Target player draws a card, then target player draws a card.',compile_clause=compile_clause))
        self.assertIsNone(owner.fixed_effect_clause_sequence_template('Draw a card, then gain 1 life.',
            compile_clause=lambda text:('leaf',({'op':'draw'},{'op':'draw'}),None,('cr-121-drawing-a-card',))))
        self.assertIsNone(owner.fixed_effect_clause_sequence_template('Draw a card, then gain 1 life. Discard a card.',
            compile_clause=lambda text:('nested',({'op':'draw'},),None,(owner.FIXED_EFFECT_CLAUSE_SEQUENCE_MECHANIC,))))

    def test_verified_owner_withdrawal_preserves_single_draw(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding="utf-8"))
        next(row for row in raw['capabilities'] if row['id']==CAPABILITY).update(status='blocked',blockers=['sequence owner withdrawn'])
        registry=CapabilityRegistry(raw)
        index=build_index(registry_value=raw,declaration_source=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json'),
            discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
        self.assertNotEqual('exact',compile_oracle_card(record(BURNING,'Sorcery'),capability_registry=registry,capability_profile='commander_review').status)
        self.assertEqual('exact',compile_oracle_card(record('Draw a card.','Sorcery'),capability_registry=registry,capability_profile='commander_review').status)

    def test_separator_omission_and_actor_loss_implementation_mutants_are_killed(self):
        with patch.object(owner,'_two_sentence_clauses',return_value=None):
            with self.assertRaises(AssertionError):self.test_elided_player_subjects_are_not_silently_controller_only()
        original=owner._two_sentence_clauses
        def lose_subject(text):
            result=original(text)
            return (result[0],result[1].removeprefix('Each player ')) if result and result[1].startswith('Each player discards') else result
        with patch.object(owner,'_two_sentence_clauses',side_effect=lose_subject):
            with self.assertRaises(AssertionError):self.test_elided_player_subjects_are_not_silently_controller_only()


class OrderedClauseNativeTests(unittest.TestCase):
    register_fixture_cards_on_add=True

    @classmethod
    def setUpClass(cls):
        from quorune.carddb import CardDatabase
        from quorune.deck import DeckDefinition,DeckEntry
        from quorune.rules.capabilities import load_default_capability_registry
        from scripts.build_test_database import build_fixture_database
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'ordered-clauses.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/source-explore-cards.json',ROOT/'tests/fixtures/fixed-self-cost-surface-cards.json',
            ROOT/'tests/fixtures/ordered-clause-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Ordered clauses',[DeckEntry('Generic Bound Commander',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()

    from test_crime import CrimeNativeTests as _crime
    from test_bound_effect_programs import BoundEffectProgramRuntimeTests as _actions
    from test_explore_event_triggers import ExploreEventNativeTests as _explore
    session=_crime.session
    start=_crime.start
    cast=_crime.cast
    priority=_crime.priority_to
    assert_closed=_crime.assert_closed
    add=_actions.add
    checkpoint=_actions.checkpoint
    resolve=_actions.resolve
    replay=_actions.replay
    resolve_top=_explore.resolve_top

    def has_keyword(self,session,card,keyword):
        return keyword.casefold() in {value.casefold() for value in session.engine._effective_card_data(session.state.cards[card.object_id]).get('keywords',())}

    def test_actual_global_deathtouch_precedes_fight_and_target_legality_preserves_scope(self):
        for scene in ('fight','one_illegal','all_illegal'):
            with self.subTest(scene=scene):
                session=self.session(647010+len(scene))
                spell=self.add(session.engine,'Undercity Uprising',zone='hand',ref='ORDERED_UPRISING')
                fighter=self.add(session.engine,'Elvish Mystic',ref='ORDERED_FIGHTER')
                other=self.add(session.engine,'Grizzly Bears',seat='B',ref='ORDERED_OPPONENT')
                witness=self.add(session.engine,'Ornithopter',ref='ORDERED_BUFF_WITNESS')
                later=self.add(session.engine,'Grizzly Bears',zone='hand',ref='ORDERED_LATE_CREATURE')
                bounces=[self.add(session.engine,'Unsummon',zone='hand',ref='ORDERED_BOUNCE_'+str(i)) for i in range(2)]
                for card in (spell,fighter,other,witness,later,*bounces):self.assert_closed(session,card)
                self.start(session);self.cast(session,spell,(fighter.ref,other.ref))
                if scene!='fight':
                    self.cast(session,bounces[0],(fighter.ref,));self.resolve_top(session)
                    if scene=='all_illegal':self.cast(session,bounces[1],(other.ref,));self.resolve_top(session)
                self.resolve(session)
                if scene=='fight':
                    self.assertEqual(('graveyard','graveyard'),tuple(session.state.cards[card.object_id].zone for card in (fighter,other)))
                elif scene=='one_illegal':
                    self.assertEqual('battlefield',session.state.cards[other.object_id].zone)
                    self.assertEqual(0,session.state.cards[other.object_id].marked_damage)
                self.assertEqual(scene!='all_illegal',self.has_keyword(session,witness,'Deathtouch'))
                self.cast(session,later);self.resolve(session)
                self.assertFalse(self.has_keyword(session,later,'Deathtouch'))
                self.replay(session,load=True)

    def test_actual_each_player_draws_before_all_random_discards_in_continuing_order(self):
        session=self.session(647020,actor='C')
        spell=self.add(session.engine,'Burning Inquiry',seat='C',zone='hand',ref='ORDERED_INQUIRY')
        for seat in 'ABCD':
            for zone in ('hand','library'):
                for index,oid in enumerate(session.state.players[seat].zones[zone]):
                    session.state.cards[oid].ref='ORDERED_PRIVATE_'+seat+'_'+zone+'_'+str(index)
        self.assert_closed(session,spell)
        self.start(session,actor='C');self.cast(session,spell,actor='C')
        before={seat:len(session.state.players[seat].zones['hand']) for seat in 'ABCD'}
        event_start=len(session.state.events)
        self.resolve(session)
        self.assertEqual(before,{seat:len(session.state.players[seat].zones['hand']) for seat in 'ABCD'})
        draws=[event.actor for event in session.state.events[event_start:] if event.code=='card.draw']
        self.assertEqual(list('CCCDDDAAABBB'),draws)
        for seat in 'ABCD':self.assertEqual(3,len(session.state.players[seat].zones['graveyard'])-(seat=='C'))
        for viewer in 'ABCD':
            packet=json.dumps(session.packet('pilot:'+viewer,full=True))
            for seat in 'ABCD':
                if seat!=viewer:
                    for oid in session.state.players[seat].zones['hand']:
                        ref=session.state.cards[oid].ref
                        self.assertTrue(ref not in packet,(viewer,seat,ref))
        self.replay(session,load=True)

    def discard(self,session,card):
        from quorune.record import authoritative_state_hash
        self.assertEqual('choice.apnap',session.state.pending_decision.kind)
        self.assertEqual(['A'],session.state.pending_decision.actors)
        command={'action_id':'choose','cards':[card.ref]};before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:B',command).ok)
        self.assertEqual(before,authoritative_state_hash(session.state))
        session=self.replay(session,load=True)
        accepted=session.act('pilot:A',command);self.assertTrue(accepted.ok,accepted.summary)
        return session

    def test_actual_return_then_discard_uses_returned_hand_and_zero_target_distinction(self):
        session=self.session(647030)
        spells=[self.add(session.engine,'Macabre Waltz',zone='hand',ref='ORDERED_WALTZ_'+str(i)) for i in range(3)]
        first=self.add(session.engine,'Grizzly Bears',zone='graveyard',ref='ORDERED_GY_FIRST')
        second=self.add(session.engine,'Elvish Mystic',zone='graveyard',ref='ORDERED_GY_SECOND')
        exile=self.add(session.engine,'Cremate',seat='B',zone='hand',ref='ORDERED_EXILE_TARGET')
        for card in (*spells,first,second,exile):self.assert_closed(session,card)
        self.start(session);session.state.players['B'].mana_pool.update(B=4);self.checkpoint(session)
        self.cast(session,spells[0])
        self.assertEqual('choice.apnap',self.resolve(session).kind)
        discard_id=next(oid for oid in session.state.players['A'].zones['hand'] if session.state.cards[oid].printed_name=='Generic Bound Plains')
        session=self.discard(session,session.state.cards[discard_id]);self.resolve(session)
        self.cast(session,spells[1],(first.ref,second.ref))
        self.assertEqual('choice.apnap',self.resolve(session).kind)
        self.assertEqual(('hand','hand'),tuple(session.state.cards[c.object_id].zone for c in (first,second)))
        session=self.discard(session,session.state.cards[first.object_id]);self.resolve(session)
        self.cast(session,spells[2],(first.ref,))
        self.priority(session,'B');self.cast(session,exile,(first.ref,),actor='B');self.resolve_top(session)
        before=len(session.state.players['A'].zones['hand'])
        self.assertIsNone(self.resolve(session))
        self.assertEqual(before,len(session.state.players['A'].zones['hand']))
        self.assertEqual('exile',session.state.cards[first.object_id].zone)
        self.replay(session,load=True)

    def test_actual_created_tokens_join_later_counter_set_and_replacement_applies(self):
        session=self.session(647040)
        spell=self.add(session.engine,"The Crystal's Chosen",zone='hand',ref='ORDERED_CRYSTAL')
        scales=self.add(session.engine,'Hardened Scales',ref='ORDERED_SCALES')
        borrowed=self.add(session.engine,'Grizzly Bears',ref='ORDERED_BORROWED');borrowed.owner='B'
        foreign=self.add(session.engine,'Grizzly Bears',seat='B',ref='ORDERED_FOREIGN');foreign.owner='A'
        for card in (spell,scales,borrowed,foreign):self.assert_closed(session,card)
        self.start(session);self.cast(session,spell);self.resolve(session)
        tokens=[card for card in session.state.cards.values() if card.zone=='battlefield' and card.controller=='A'
            and card.is_token and card.printed_name=='Hero']
        self.assertEqual(4,len(tokens))
        for token in tokens:
            self.assertEqual(2,token.counters.get('+1/+1',0))
            self.assertEqual((3,3),tuple(session.engine._numeric_stat(token.object_id,key) for key in ('power','toughness')))
        self.assertEqual(2,session.state.cards[borrowed.object_id].counters.get('+1/+1',0))
        self.assertEqual(0,session.state.cards[foreign.object_id].counters.get('+1/+1',0))
        self.replay(session,load=True)
