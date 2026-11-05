"""Independent reveal-until prefix and existing partition-owner contracts."""
from copy import deepcopy
from types import SimpleNamespace
from pathlib import Path
import tempfile
import unittest

from quorune.compiler.reveal_until_library_selection_templates import reveal_until_library_selection_effect_template as compile_selection
from quorune.object_predicate import ObjectQuerySpec
from quorune.object_query import ObjectQueryResult
from quorune.reveal_until_library_selection_model import (
    fixed_selection_after_reveal_until,validate_reveal_until_selection,
    reveal_until_selection_payload_dependencies,
)
from quorune.semantic_choices.reveal_until_library_selection import RevealUntilLibrarySelectionHandler


def query_rows(kinds):
    rows = [ObjectQueryResult(object_id='scan:'+str(i),logical_object_id='scan:'+str(i)+'@0',
        ref='PREFIX_'+str(i),printed_name='Fixture',owner='A',controller='A',zone='library',
        types=tuple(types),supertypes=tuple(supertypes)) for i,(types,supertypes) in enumerate(kinds)]
    class Query:
        active_seats = ('A','B','C','D')
        def __init__(self):self.inspected=[]
        def library_refs(self,seat,*,top_first):
            if seat!='A' or top_first is not True:raise AssertionError('Wrong library scan')
            return tuple(row.ref for row in rows)
        def object(self,ref,*,zones):
            if zones!=('library',):raise AssertionError('Wrong source zone')
            self.inspected.append(ref)
            return next(row for row in rows if row.ref==ref)
    return Query(),rows


class RevealUntilSelectionOwnerTests(unittest.TestCase):
    def effect(self, *, count=1, kind='land', selection='matching'):
        predicates = [ObjectQuerySpec(zones=('library',),types_any=(kind,)).to_dict()]
        return {'op':'reveal_until_library_selection','schema_version':1,'player':'A',
            'stop_count':count,'predicate_groups':[predicates],'selection':selection,
            'selected_destination':'hand','entry_tapped':False,
            'remainder_destination':'graveyard','remainder_order':'chosen'}

    def test_prefix_stops_at_the_required_match_and_does_not_read_later_cards(self):
        query,rows = query_rows([(('creature',),()),(('land',),()),(('land',),()),(('sorcery',),())])
        original = self.effect(count=2);before = deepcopy(original)
        fixed = fixed_selection_after_reveal_until(original,query)
        self.assertEqual(before,original)
        self.assertEqual([row.ref for row in rows[:3]],query.inspected)
        self.assertEqual(3,fixed['look_count'])
        self.assertEqual('all_matching',fixed['selection_policy'])
        self.assertEqual((0,3),(fixed['minimum'],fixed['maximum']))
        self.assertTrue(fixed['public_reveal'])
        fixed['predicate_groups'].clear()
        self.assertEqual(before,original)

    def test_short_no_match_and_empty_libraries_preserve_as_much_as_possible(self):
        for kinds,expected in (([],0),([(('creature',),())],1),
                ([(('creature',),()),(('land',),())],2)):
            with self.subTest(kinds=kinds):
                query,rows = query_rows(kinds)
                fixed = fixed_selection_after_reveal_until(self.effect(count=3),query)
                self.assertEqual([row.ref for row in rows],query.inspected)
                if expected:self.assertEqual(expected,fixed['look_count'])
                else:self.assertIsNone(fixed)
        query,rows = query_rows([(('land',),()),(('land',),())])
        fixed = fixed_selection_after_reveal_until(self.effect(kind='creature',selection='all_revealed'),query)
        self.assertEqual('fixed_any',fixed['selection_policy'])
        self.assertEqual((2,2),(fixed['minimum'],fixed['maximum']))
        self.assertEqual([],fixed['predicate_groups'])

    def test_compiler_preserves_stop_kind_count_result_and_tapped_entry(self):
        cases = (
            ('Reveal cards from the top of your library until you reveal a nonland card, then put all cards revealed this way into your hand.',1,'all_revealed','hand',False,'graveyard'),
            ('Reveal cards from the top of your library until you reveal three nonland cards. Put the nonland cards revealed this way into your hand, then put the rest of the revealed cards on the bottom of your library in any order.',3,'matching','hand',False,'library_bottom'),
            ('Reveal cards from the top of your library until you reveal a basic land card. Put that card into your hand and all other cards revealed this way into your graveyard.',1,'matching','hand',False,'graveyard'),
            ('Reveal cards from the top of your library until you reveal a land card. Put that card onto the battlefield tapped and the rest on the bottom of your library in a random order.',1,'matching','battlefield',True,'library_bottom'),
            ('Reveal cards from the top of your library until you reveal a nonlegendary creature card. Put that card onto the battlefield and the rest on the bottom of your library in a random order.',1,'matching','battlefield',False,'library_bottom'),
        )
        for text,count,selection,destination,tapped,remainder in cases:
            with self.subTest(text=text):
                result = compile_selection(text)
                self.assertIsNotNone(result)
                effect = result[1][0]
                self.assertEqual((count,selection,destination,tapped,remainder),
                    (effect['stop_count'],effect['selection'],effect['selected_destination'],effect['entry_tapped'],effect['remainder_destination']))
                required = reveal_until_selection_payload_dependencies(effect)
                self.assertIn('library.select.reveal_until_controller',required)
                self.assertEqual(destination=='battlefield','library.select.battlefield_controller' in required)
                self.assertEqual(tapped,'zone.entry.tapped_state' in required)
        nonlegendary = compile_selection(cases[-1][0])[1][0]
        predicate = ObjectQuerySpec.from_dict(nonlegendary['predicate_groups'][0][0])
        self.assertEqual(('legendary',),predicate.excluded_supertypes)

    def test_unknown_counts_players_predicates_and_companion_results_fail_closed(self):
        for change in ({'stop_count':True},{'stop_count':0},{'stop_count':-1},{'stop_count':{}},
                {'player':' A'},{'schema_version':True},{'predicate_groups':[]},
                {'selected_destination':'exile'},{'entry_tapped':True},{'extra':0}):
            with self.subTest(change=change),self.assertRaises((TypeError,ValueError)):
                validate_reveal_until_selection({**self.effect(),**change})
        base = 'Reveal cards from the top of your library until you reveal a land card. Put that card into your hand and the rest on the bottom of your library in a random order.'
        for text in (base.replace('your library',"target player's library"),
                base.replace('a land card','X land cards'),base.replace('a land card','a card with the chosen name'),
                base.replace('a land card','a land card with mana value 2 or less'),
                base+' Draw a card.',base.replace('into your hand','into exile'),
                base.replace('a land card','two land cards')):
            with self.subTest(text=text):self.assertIsNone(compile_selection(text))

    def test_bridge_requires_live_controller_and_never_accepts_a_submitted_scan(self):
        query,rows = query_rows([(('creature',),()),(('land',),()),(('creature',),())])
        handler = RevealUntilLibrarySelectionHandler()
        context = SimpleNamespace(actor='A',stack_controller='A',query=query)
        prepared = handler.prepare(self.effect(),context)
        self.assertIsNone(prepared.request)
        self.assertEqual((),prepared.preparation_intents)
        self.assertEqual(2,prepared.auto_continue.prepend_effects[0]['look_count'])
        self.assertEqual([rows[0].ref,rows[1].ref],query.inspected)
        with self.assertRaises(ValueError):handler.complete(None,{'stop_count':1},query)
        context.stack_label = 'Public prefix hand result'
        context.stack_ref = 'STACK_REVEAL_UNTIL'
        all_revealed = handler.prepare(self.effect(selection='all_revealed'),context)
        self.assertEqual((),all_revealed.auto_continue.prepend_effects)
        self.assertEqual(2,len(all_revealed.preparation_intents))
        self.assertEqual((rows[0].ref,rows[1].ref),all_revealed.preparation_intents[1].arrangement.selected_refs)
        for actor,controller in (('B','A'),('A','B'),('E','E')):
            with self.subTest(actor=actor),self.assertRaises(ValueError):
                handler.prepare(self.effect(),SimpleNamespace(actor=actor,stack_controller=controller,query=query))

    def test_verified_dependency_and_compiler_withdrawal_preserves_unrelated_draw(self):
        import json
        from dataclasses import replace
        from unittest.mock import patch
        from common import ROOT
        from test_qualified_zone_event_queries import query_record
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import CapabilityRegistry,load_default_capability_registry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        source = replace(query_record('Reveal cards from the top of your library until you reveal a nonland card, '
            'then put all cards revealed this way into your hand.'),type_line='Sorcery',power=None,toughness=None)
        control = replace(source,oracle_text='Draw a card.')
        registry = load_default_capability_registry()
        self.assertEqual('exact',compile_oracle_card(source,capability_registry=registry,
            capability_profile='commander_review').status)
        activated = replace(source,type_line='Creature — Human',power='1',toughness='1',
            oracle_text='{G}, {T}: '+source.oracle_text)
        activated_ir = compile_oracle_card(activated,capability_registry=registry,capability_profile='commander_review')
        self.assertEqual('exact',activated_ir.status,activated_ir.material_residuals)
        self.assertIn('library.select.reveal_until_controller',activated_ir.faces[0].nodes[0].capability_dependencies)
        declarations = read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        discovered = set(discover_tests(ROOT).values())
        baseline = json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding="utf-8"))
        for dependency in ('library.select.reveal_until_controller','library.select.fixed_controller'):
            raw = deepcopy(baseline)
            next(row for row in raw['capabilities'] if row['id']==dependency).update(
                status='blocked',blockers=['Focused reveal-until authority withdrawal'])
            blocked = CapabilityRegistry(raw)
            index = build_index(registry_value=raw,declaration_source=declarations,discovered_test_ids=discovered)
            blocked.mark_evidence_verified(validate_capability_evidence_index(index,registry=blocked))
            self.assertNotEqual('exact',compile_oracle_card(source,capability_registry=blocked,
                capability_profile='commander_review').status)
            self.assertEqual('exact',compile_oracle_card(control,capability_registry=blocked,
                capability_profile='commander_review').status)
        with patch('quorune.compiler.reveal_until_library_selection_templates.reveal_until_library_selection_effect_template',return_value=None):
            self.assertNotEqual('exact',compile_oracle_card(source,capability_registry=registry,
                capability_profile='commander_review').status)
        from quorune.card_programs.runtime_requirements import program_dynamic_runtime_requirements
        from quorune.semantics import SemanticProgram
        effect = self.effect();effect['player'] = '$controller'
        program = SemanticProgram(key='fixture:reveal-until',label='Revealed prefix',effects=[effect],coverage=[])
        required,blockers = program_dynamic_runtime_requirements(program)
        self.assertIn('library.select.reveal_until_controller',required)
        self.assertIn('library.select.fixed_controller',required)
        self.assertFalse(blockers)
        program.effects = [{**effect,'stop_count':True}]
        _required,blockers = program_dynamic_runtime_requirements(program)
        self.assertTrue(blockers)


class RevealUntilSelectionNativeTests(unittest.TestCase):
    register_fixture_cards_on_add = True

    @classmethod
    def setUpClass(cls):
        from common import ROOT
        from quorune.carddb import CardDatabase
        from quorune.deck import DeckDefinition,DeckEntry
        from quorune.rules.capabilities import load_default_capability_registry
        from scripts.build_test_database import build_fixture_database
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name)/'reveal-until.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/reveal-until-library-selection-cards.json'],path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Reveal until',[DeckEntry('Generic Bound Commander',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def prepare(self, name, library, *, zone='hand', seed=648001):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        from quorune.card_programs import bind_card_program_runtime
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.turn_step_owner import TURN_STEPS
        session = H.session(self,seed)
        engine = session.engine
        for oid in tuple(session.state.players['A'].zones['library']):
            engine.move_card(oid,'exile',reason='Prepare reveal-until prefix')
        cards = [H.add(self,engine,title,zone='library',ref='REVEAL_UNTIL_CARD_'+str(i))
            for i,title in enumerate(library)]
        source = H.add(self,engine,name,zone=zone,ref='REVEAL_UNTIL_SOURCE')
        program = compile_best_available_card_program(self.db,self.db.by_oracle_id(source.oracle_id),
            semantic_registry=engine.semantics,capability_registry=self.registry,capability_profile='commander_review')
        binding = bind_card_program_runtime(program,capability_registry=self.registry,profile='commander_review')
        self.assertTrue(binding['strict_capability_ready'],binding['blockers'])
        self.assertEqual('capability_closed',binding['trust_basis'])
        engine.state.phase_index = TURN_STEPS.index(('precombat_main','main'))
        offer = H.ready(self,session,source,{'U':5,'G':5,'C':8})
        H.checkpoint(self,session)
        return session,cards,source,offer

    def act(self, session, offer):
        from quorune.record import authoritative_state_hash
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:B',{'action_id':offer['id'],'pay':'auto'}).ok)
        self.assertEqual(before,authoritative_state_hash(session.state))
        result = session.act('pilot:A',{'action_id':offer['id'],'pay':'auto'})
        self.assertTrue(result.ok,result.summary)

    def test_original_treasure_hunt_stops_at_nonland_and_moves_whole_no_match_prefix(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        for names,selected_count in ((['Generic Bound Growth','Generic Bound Body','Generic Bound Plains','Generic Bound Plains'],3),
                (['Generic Bound Plains','Generic Bound Plains'],2)):
            with self.subTest(names=names):
                session,cards,source,offer = self.prepare('Treasure Hunt',names)
                self.act(session,offer)
                self.assertIsNone(H.resolve(self,session))
                selected = cards[-selected_count:]
                for card in selected:
                    self.assertEqual('hand',session.state.cards[card.object_id].zone)
                    self.assertEqual(list('ABCD'),session.state.cards[card.object_id].known_to)
                self.assertEqual([card.object_id for card in cards[:-selected_count]],session.state.players['A'].zones['library'])
                self.assertTrue(session.state.players['A'].in_game)
                H.replay(self,session,load=True)

    def test_original_fathom_trawl_short_prefix_keeps_matching_cards_and_private_bottom_order(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        from quorune.record import authoritative_state_hash
        session,cards,source,offer = self.prepare('Fathom Trawl',
            ['Generic Bound Plains','Generic Bound Growth','Generic Bound Plains','Generic Bound Body'])
        self.act(session,offer)
        self.assertEqual('semantic.choice',H.resolve(self,session).kind)
        for seat in 'ABCD':
            packet = session.packet('pilot:'+seat,full=True)['state']['players']['A']
            self.assertEqual(4,len(packet['known_top']))
        session = H.replay(self,session,load=True)
        selected = [cards[1].ref,cards[3].ref]
        bottom = [cards[2].ref,cards[0].ref]
        before = authoritative_state_hash(session.state)
        for principal,hand in (('pilot:C',selected),('pilot:A',[cards[0].ref,cards[3].ref])):
            self.assertFalse(session.act(principal,{'action_id':'choose','cards':{'hand':hand,'bottom':bottom}}).ok)
            self.assertEqual(before,authoritative_state_hash(session.state))
        self.assertTrue(session.act('pilot:A',{'action_id':'choose','cards':{'hand':selected,'bottom':bottom}}).ok)
        self.assertIsNone(H.resolve(self,session))
        for card in (cards[1],cards[3]):self.assertEqual('hand',session.state.cards[card.object_id].zone)
        owner = session.packet('pilot:A',full=True)['state']['players']['A']['known_top']
        self.assertEqual(list(reversed(bottom)),[row['id'] for row in owner])
        for seat in 'BCD':self.assertFalse(session.packet('pilot:'+seat,full=True)['state']['players']['A'].get('known_top'))
        H.replay(self,session,load=True)

    def test_original_clifftop_lookout_puts_stop_land_in_tapped_and_no_match_is_not_a_shuffle(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        for names in (['Generic Bound Plains','Generic Bound Growth','Generic Bound Body'],
                ['Generic Bound Growth','Generic Bound Body','Generic Bound Growth']):
            with self.subTest(names=names):
                session,cards,source,offer = self.prepare('Clifftop Lookout',names)
                before = session.state.players['A'].stats.get('shuffle_count',0)
                self.act(session,offer)
                self.assertIsNone(H.resolve(self,session))
                self.assertEqual('battlefield',session.state.cards[source.object_id].zone)
                has_land = names[0]=='Generic Bound Plains'
                if has_land:
                    land = session.state.cards[cards[0].object_id]
                    self.assertEqual('battlefield',land.zone)
                    self.assertTrue(land.tapped)
                remainder = cards[1:] if has_land else cards
                for card in remainder:
                    live = session.state.cards[card.object_id]
                    self.assertEqual('library',live.zone)
                    self.assertEqual([],live.known_to)
                    self.assertEqual([],live.revealed_to)
                self.assertEqual(before,session.state.players['A'].stats.get('shuffle_count',0))
                for seat in 'ABCD':self.assertFalse(session.packet('pilot:'+seat,full=True)['state']['players']['A'].get('known_top'))
                H.replay(self,session,load=True)

    def test_original_hermit_druid_activates_through_real_cost_and_keeps_basic_land_only(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        session,cards,source,offer = self.prepare('Hermit Druid',
            ['Generic Bound Plains','Generic Bound Body','Generic Bound Growth'],zone='battlefield')
        self.act(session,offer)
        self.assertEqual('semantic.choice',H.resolve(self,session).kind)
        session = H.replay(self,session,load=True)
        self.assertTrue(session.act('pilot:A',{'action_id':'choose',
            'cards':{'hand':[cards[0].ref],'graveyard':[cards[1].ref,cards[2].ref]}}).ok)
        self.assertIsNone(H.resolve(self,session))
        self.assertTrue(session.state.cards[source.object_id].tapped)
        self.assertEqual('hand',session.state.cards[cards[0].object_id].zone)
        for card in cards[1:]:self.assertEqual('graveyard',session.state.cards[card.object_id].zone)
        self.assertEqual([],session.state.players['A'].zones['library'])
        H.replay(self,session,load=True)
