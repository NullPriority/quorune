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
        offer = H.ready(self,session,source,{'U':5,'G':5,'C':8,'B':1})
        H.checkpoint(self,session)
        return session,cards,source,offer

    def act(self, session, offer):
        from quorune.record import authoritative_state_hash
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:B',{'action_id':offer['id'],'pay':'auto'}).ok)
        self.assertEqual(before,authoritative_state_hash(session.state))
        result = session.act('pilot:A',{'action_id':offer['id'],'pay':'auto'})
        self.assertTrue(result.ok,result.summary)

    def _concede_during_ordinary_priority(self, game, seat):
        principal = 'pilot:' + seat
        for _ in range(len(game.engine.active_seats)):
            actors = game.pending_principals()
            if principal in actors:
                result = game.act(principal, {'action_id': 'concede', 'choices': {'confirm_concede': True}})
                self.assertTrue(result.ok, result.summary)
                return
            self.assertEqual('priority', game.state.pending_decision.kind)
            result = game.act(actors[0], {'action_id': 'pass'})
            self.assertTrue(result.ok, result.summary)
        self.fail('Ordinary priority never reached the departing player')

    def test_open_the_way_native_cast_cap_and_committed_x_survive_a_player_leaving(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        from quorune.record import authoritative_state_hash

        main_library = ["Generic Bound Growth", "Generic Bound Plains", "Generic Bound Body",
                        "Generic Bound Plains", "Generic Bound Plains", "Generic Bound Growth", "Generic Bound Plains"]
        for departure_before_cast in (False, True):
            with self.subTest(departure_before_cast=departure_before_cast):
                game, cards, source, offer = self.prepare("Open the Way", main_library, seed=648030 + departure_before_cast)
                maximum = 4
                if departure_before_cast:
                    self._concede_during_ordinary_priority(game, "D")
                    maximum -= 1
                    actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
                    offer = next(row for row in actions if row.get("card") == source.ref)
                self.assertEqual(maximum, offer["cost_options"][0]["choice_schema"]["x"]["maximum"])
                before = authoritative_state_hash(game.state)
                for seat, amount in (("A", maximum + 1), ("A", True), ("A", -1), ("B", maximum)):
                    refused = game.act("pilot:"+seat, {"action_id": offer["id"], "x": amount, "pay": "auto"})
                    self.assertFalse(refused.ok, refused.summary)
                    self.assertEqual(before, authoritative_state_hash(game.state))
                accepted = game.act("pilot:A", {"action_id": offer["id"], "x": maximum, "pay": "auto"})
                self.assertTrue(accepted.ok, accepted.summary)
                self.assertEqual(maximum, game.state.stack[-1].x_value)
                if not departure_before_cast:
                    self._concede_during_ordinary_priority(game, "D")
                self.assertEqual(["A", "B", "C"], game.engine.active_seats)
                game = H.replay(self, game, load=True)
                self.assertEqual(maximum, game.state.stack[-1].x_value)
                shuffle_count = game.state.players["A"].stats.get("shuffle_count", 0)
                event_count = len(game.state.events)
                untouched_count = 3 if maximum == 3 else 1
                visibility = {card.object_id: (list(card.known_to), list(card.revealed_to))
                              for card in (game.state.cards[row.object_id] for row in cards[:untouched_count])}
                self.assertIsNone(H.resolve(self, game))
                looked, = (event for event in game.state.events[event_count:] if event.code == "library.look")
                self.assertEqual(len(cards) - untouched_count, looked.details["count"])
                self.assertEqual({card.object_id for card in cards[untouched_count:]}, set(looked.changed_objects))
                for object_id, expected in visibility.items():
                    live = game.state.cards[object_id]
                    self.assertEqual(expected, (live.known_to, live.revealed_to))
                selected = (3, 4, 6) if maximum == 3 else (1, 3, 4, 6)
                lands = [game.state.cards[cards[i].object_id] for i in selected]
                self.assertTrue(all(card.zone == "battlefield" and card.tapped for card in lands))
                library = game.state.players["A"].zones["library"]
                if maximum == 3:
                    self.assertEqual([cards[i].object_id for i in (5, 0, 1, 2)], library)
                else:
                    self.assertEqual(cards[0].object_id, library[-1])
                    self.assertEqual({cards[i].object_id for i in (2, 5)}, set(library[:2]))
                for seat in "BC":
                    public = game.packet("pilot:"+seat, full=True)["state"]["players"]["A"]
                    self.assertFalse(public.get("known_top"))
                self.assertEqual(shuffle_count, game.state.players["A"].stats.get("shuffle_count", 0))
                H.replay(self, game, load=True)

    def test_open_the_way_zero_and_short_library_use_the_native_partition_and_replay(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H

        for amount in (0, 4):
            with self.subTest(amount=amount):
                game, cards, source, offer = self.prepare("Open the Way",
                    ["Generic Bound Body", "Generic Bound Plains", "Generic Bound Growth"], seed=648040 + amount)
                prior = list(game.state.players["A"].zones["library"])
                accepted = game.act("pilot:A", {"action_id": offer["id"], "x": amount, "pay": "auto"})
                self.assertTrue(accepted.ok, accepted.summary)
                event_count = len(game.state.events)
                self.assertIsNone(H.resolve(self, game))
                library = game.state.players["A"].zones["library"]
                if amount == 0:
                    self.assertEqual(prior, library)
                    self.assertFalse(any(event.code.startswith("library.") for event in game.state.events[event_count:]))
                else:
                    self.assertEqual({cards[0].object_id, cards[2].object_id}, set(library))
                    land = game.state.cards[cards[1].object_id]
                    self.assertEqual(("battlefield", True), (land.zone, land.tapped))
                self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
                self.assertTrue(game.state.players["A"].in_game)
                H.replay(self, game, load=True)

    def test_open_the_way_requires_a_whole_spell_and_the_announced_x_tapped_land_partition(self):
        from dataclasses import replace
        from unittest.mock import patch
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.oracle_ir import compile_oracle_card
        from quorune.reveal_until_library_selection_model import player_capped_land_reveal_effect

        game, _, source, _ = self.prepare("Open the Way", [], seed=648050)
        program, = game.engine.semantics.programs_for_oracle(source.oracle_id)
        self.assertEqual((player_capped_land_reveal_effect(),), tuple(program.effects))
        for delta in ({"stop_count": 4}, {"stop_count": "$controller"}, {"entry_tapped": False},
                      {"remainder_order": "chosen"}, {"schema_version": 3.0}):
            changed = replace(program, effects=({**program.effects[0], **delta},))
            self.assertFalse(bind_semantic_program_runtime(changed, capability_registry=self.registry,
                                                          profile="commander_review")["strict"])
        self.assertFalse(bind_semantic_program_runtime(replace(program, active_zone="battlefield", event="activate"),
            capability_registry=self.registry, profile="commander_review")["strict"])
        record = self.db.lookup("Open the Way", fuzzy=False)
        with patch("quorune.compiler.player_capped_land_reveal.player_capped_land_reveal_template", return_value=None):
            self.assertNotEqual("exact", compile_oracle_card(record, capability_registry=self.registry).status)

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

    def _cast_and_name(self, names, chosen, *, seed, inspect_order=False):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        from quorune.record import authoritative_state_hash
        import json

        session, cards, source, offer = self.prepare("Demonic Consultation", list(reversed(names)), seed=seed)
        cards.reverse()
        self.act(session, offer)
        self.assertEqual("semantic.choice", H.resolve(self, session).kind)
        self.assertEqual(["pilot:A"], session.pending_principals())
        before = authoritative_state_hash(session.state)
        original_library = list(session.state.players["A"].zones["library"])
        for principal, name in (("pilot:B", chosen), ("pilot:A", "Unknown Bog Creature ZZZ")):
            rejected = session.act(principal, {"action_id": "choose", "card_name": name})
            self.assertFalse(rejected.ok)
            self.assertEqual(before, authoritative_state_hash(session.state))
        for seat in "BCD":
            packet = session.packet("pilot:" + seat, full=True)
            self.assertIsNone(packet["decision"])
            for card in cards:
                self.assertNotIn(card.ref, json.dumps(packet))
        self.assertEqual(original_library, session.state.players["A"].zones["library"])
        session = H.replay(self, session, load=True)
        start = len(session.state.events)
        if inspect_order:
            from unittest.mock import patch

            move = session.engine.move_objects_simultaneously_intent
            reveal = session.engine.reveal_library_cards_intent
            observed = []

            def observe_move(intent, *args, **kwargs):
                observed.append(("exile", intent.object_refs, session.state.cards[cards[7].object_id].zone))
                return move(intent, *args, **kwargs)

            def observe_reveal(intent, *args, **kwargs):
                observed.append(("reveal", intent.refs_top_first,
                                 tuple(session.state.cards[card.object_id].zone for card in cards[:6])))
                return reveal(intent, *args, **kwargs)

            with (patch.object(session.engine, "move_objects_simultaneously_intent", side_effect=observe_move),
                  patch.object(session.engine, "reveal_library_cards_intent", side_effect=observe_reveal)):
                answer = session.act("pilot:A", {"action_id": "choose", "card_name": chosen})
            self.assertEqual([
                ("exile", tuple(card.ref for card in cards[:6]), "library"),
                ("reveal", tuple(card.ref for card in cards[6:8]), ("exile",) * 6),
                ("exile", (cards[6].ref,), "hand"),
            ], observed)
        else:
            answer = session.act("pilot:A", {"action_id": "choose", "card_name": chosen})
        self.assertTrue(answer.ok, answer.summary)
        self.assertIsNone(H.resolve(self, session))
        return session, cards, session.state.events[start:]

    def test_demonic_consultation_names_on_resolution_and_reveals_exact_prefix(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        import json

        session, cards, events = self._cast_and_name(
            ["Forest"] * 6 + ["Forest", "Treasure Hunt", "Forest"], "tREaSure HuNT", seed=648091, inspect_order=True,
        )
        chosen, = (event for event in events if event.code == "card.name.chosen")
        self.assertEqual("Treasure Hunt", chosen.details["card_name"])
        reveal, = (event for event in events if event.code == "library.reveal")
        self.assertEqual([card.ref for card in cards[6:8]], [card["ref"] for card in reveal.details["cards"]])
        self.assertEqual(["exile"] * 7 + ["hand", "library"],
                         [session.state.cards[card.object_id].zone for card in cards])
        self.assertEqual(list("ABCD"), session.state.cards[cards[7].object_id].known_to)
        for seat in "BCD":
            packet = json.dumps(session.packet("pilot:" + seat, full=True))
            for card in cards[:8]:
                self.assertIn(card.ref, packet)
            self.assertNotIn(cards[-1].ref, packet)
        H.replay(self, session, load=True)

    def test_demonic_consultation_miss_and_short_library_exile_every_available_card(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H

        for i, names in enumerate((
            ["Treasure Hunt"] + ["Forest"] * 8,
            ["Forest"] * 9,
            ["Forest"] * 3,
            [],
        )):
            with self.subTest(names=names):
                session, cards, events = self._cast_and_name(names, "Treasure Hunt", seed=648092 + i)
                self.assertEqual([], session.state.players["A"].zones["library"])
                self.assertTrue(session.state.players["A"].in_game)
                self.assertEqual(["exile"] * len(cards),
                                 [session.state.cards[card.object_id].zone for card in cards])
                reveals = [event for event in events if event.code == "library.reveal"]
                self.assertEqual(int(len(cards) > 6), len(reveals))
                if reveals:
                    self.assertEqual([card.ref for card in cards[6:]],
                                     [row["ref"] for row in reveals[0].details["cards"]])
                H.replay(self, session, load=True)

class ChosenNameRevealCompilerTests(unittest.TestCase):

    def test_chosen_name_reveal_requires_the_complete_printed_procedure(self):
        from dataclasses import replace
        from unittest.mock import patch
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import load_default_capability_registry
        from test_qualified_zone_event_queries import query_record

        text = ("Choose a card name. Exile the top three cards of your library, "
                "then reveal cards from the top of your library until you reveal a card with the chosen name. "
                "Put that card into your hand and exile all other cards revealed this way.")

        def compile_text(value):
            return compile_oracle_card(replace(query_record(value), type_line="Instant"),
                                       capability_registry=load_default_capability_registry())

        def witness():
            ir = compile_text(text)
            self.assertEqual("exact", ir.status, ir.material_residuals)
            node = ir.faces[0].nodes[0]
            self.assertIsNone(node.target_schema)
            self.assertEqual(3, node.effects[0]["exile_count"])

        witness()
        with patch("quorune.compiler.reveal_until_library_selection_templates.chosen_name_reveal_template",
                   return_value=None):
            with self.assertRaises(AssertionError):
                witness()
        for altered in (
            text.replace("your library", "target player's library"),
            text.replace("three cards", "X cards"),
            text.replace("exile all other", "shuffle all other"),
            text.replace("your hand", "exile"),
            text + " Draw a card.",
        ):
            self.assertNotEqual("exact", compile_text(altered).status)
