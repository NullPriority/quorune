"""CR111.3/4 and 608.2d: choose one fixed definition during resolution."""
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
from quorune.ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from quorune.carddb import CardDatabase
from quorune.compiler.token_templates import fixed_token_creation_effect_template
from quorune.compiler.fixed_token_choices import TOKEN_CHOICE_CAPABILITY as CAPABILITY,TOKEN_CHOICE_MECHANIC
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.fixed_token_choices import fixed_token_choice_node_capabilities,fixed_token_choice_payload_dependencies
from quorune.semantic_choices.scalar import ChooseOptionHandler
from scripts.build_test_database import build_fixture_database


class FixedTokenChoiceCompilerTests(unittest.TestCase):
    def test_choices_delegate_to_existing_definitions_and_use_resolution_options(self):
        registry = load_default_capability_registry()
        for text,names in (('Create a Food token or a Treasure token.',('Food','Treasure')),
                ('Create your choice of a Clue token, a Food token, or a Treasure token.',('Clue','Food','Treasure'))):
            compiled = fixed_token_creation_effect_template(text)
            self.assertIsNotNone(compiled)
            choice = compiled.effect
            self.assertEqual('choose_option',choice['op'])
            self.assertEqual([{'id':name,'label':name} for name in names],choice['options'])
            for name in names:
                self.assertEqual([fixed_token_creation_effect_template(f'Create a {name} token.').effect],choice['then_by_choice'][name])
            self.assertIn(CAPABILITY,fixed_token_choice_node_capabilities(effects=(choice,),target_schema=None,mechanic_ids=compiled.mechanics))
            ir = compile_oracle_card(replace(query_record(text),type_line='Sorcery'),capability_registry=registry)
            self.assertEqual('exact',ir.status,ir.material_residuals)

    def test_unknown_optional_targeted_duplicate_and_extra_grammar_stays_residual(self):
        for text in ('Create a Food token or a Mystery token.','You may create a Food token or a Treasure token.',
                'Create a Food token or a Food token.','Create two Food tokens or a Treasure token.',
                'Target player creates a Food token or a Treasure token.','Create a tapped Food token or a Treasure token.',
                'Create a Food token or a Treasure token. Rewrite history.'):
            self.assertIsNone(fixed_token_creation_effect_template(text),text)
        self.assertIsNotNone(fixed_token_creation_effect_template('Create a Food token.'))

    def test_runtime_minimum_requires_complete_choice_and_each_definition(self):
        compiled = fixed_token_creation_effect_template('Create a Food token or a Treasure token.')
        self.assertIn(CAPABILITY,fixed_token_choice_payload_dependencies(compiled.effect,compiled.mechanics))
        coverage = (*compiled.mechanics,'generated_oracle_ir','triggered_ability',CURRENT_ABILITY_FRAGMENT_COVERAGE)
        self.assertIn(CAPABILITY,fixed_token_choice_payload_dependencies(compiled.effect,coverage))
        with self.assertRaises(ValueError):fixed_token_choice_payload_dependencies(compiled.effect,(*coverage,'unowned-semantic-mechanic'))
        for change in (lambda e:e.update(player='B'),lambda e:e.update(extra=True),
                lambda e:e['then_by_choice']['Food'][0].update(quantity=True),
                lambda e:e['then_by_choice']['Food'][0].update(controller='B'),
                lambda e:e['options'].append(e['options'][0]),
                lambda e:e['then_by_choice'].update(Other=e['then_by_choice']['Food'])):
            effect = deepcopy(compiled.effect);change(effect)
            self.assertEqual((),fixed_token_choice_node_capabilities(effects=(effect,),target_schema=None,mechanic_ids=compiled.mechanics))
            with self.assertRaises(ValueError):fixed_token_choice_payload_dependencies(effect,compiled.mechanics)
        with self.assertRaises(ValueError):
            fixed_token_choice_payload_dependencies(compiled.effect,tuple(m for m in compiled.mechanics if m!=TOKEN_CHOICE_MECHANIC))

    def test_verified_choice_withdrawal_keeps_ordinary_token_creation(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        raw = json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        next(row for row in raw['capabilities'] if row['id']==CAPABILITY).update(status='blocked',blockers=['choice owner withdrawn'])
        registry = CapabilityRegistry(raw)
        index = build_index(registry_value=raw,declaration_source=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json'),discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
        for text,expected in (('Create a Food token or a Treasure token.',False),('Create a Food token.',True),('Draw a card.',True)):
            self.assertEqual(expected,compile_oracle_card(replace(query_record(text),type_line='Sorcery'),capability_registry=registry).status=='exact')

    def test_reviewed_choice_retirement_requires_every_complete_branch_and_current_authority(self):
        from quorune.card_programs.trigger_result_equivalence import fixed_token_choice_results_equal
        compiled = fixed_token_creation_effect_template('Create a Food token or a Treasure token.')
        required = fixed_token_choice_node_capabilities(effects=(compiled.effect,),target_schema=None,mechanic_ids=compiled.mechanics)
        current = SimpleNamespace(effects=(compiled.effect,),coverage=compiled.mechanics,target_schema=None,capability_dependencies=required)
        old_effect = deepcopy(compiled.effect);old_effect['prompt'] = 'Choose an artifact token.'
        old_effect['options'] = [{'id':name.casefold(),'label':name} for name in ('Food','Treasure')]
        old_effect['then_by_choice'] = {name.casefold():[{**branch[0],'reason':'reviewed source'}] for name,branch in old_effect['then_by_choice'].items()}
        old = SimpleNamespace(effects=(old_effect,),target_schema=None)
        self.assertTrue(fixed_token_choice_results_equal(current,old))
        for mutate in (lambda e:e.update(player='B'),lambda e:e['then_by_choice']['food'][0].update(quantity=2),
                lambda e:e['then_by_choice']['food'][0].update(controller='B'),
                lambda e:e['then_by_choice']['food'][0]['characteristics'].update(display_text='Draw a card.'),
                lambda e:e['then_by_choice'].pop('treasure')):
            changed = deepcopy(old_effect);mutate(changed)
            self.assertFalse(fixed_token_choice_results_equal(current,SimpleNamespace(effects=(changed,),target_schema=None)))
        missing = SimpleNamespace(**{**vars(current),'capability_dependencies':tuple(cap for cap in required if cap!=CAPABILITY)})
        self.assertFalse(fixed_token_choice_results_equal(missing,old))


class FixedTokenChoiceNativeTests(InheritedNativeFixture,unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory();path = Path(cls.temporary.name)/'fixed-token-choice.sqlite3'
        basic = json.loads((ROOT/'tests/fixtures/bound-effect-program-cards.json').read_text(encoding='utf-8'))
        basic['cards'] = [row for row in basic['cards'] if row['name']=='Generic Bound Plains'];basic['rulings'] = []
        base = Path(cls.temporary.name)/'basic.json';base.write_text(json.dumps(basic),encoding='utf-8')
        build_fixture_database([base,ROOT/'tests/fixtures/fixed-token-choice-cards.json'],path)
        cls.db = CardDatabase(path);cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Fixed token choices',[DeckEntry('Sliver Hivelord',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Sliver Hivelord'])

    def tokens(self,session):
        return [card for card in session.state.cards.values() if card.is_token and card.zone=='battlefield']

    def choose(self,session,name,private):
        self.assertEqual('semantic.choice',session.state.pending_decision.kind)
        self.assertEqual(['A'],session.state.pending_decision.actors)
        before = authoritative_state_hash(session.state)
        for actor,choice in (('B',name),('A','Unknown'),('A','decline')):
            self.assertFalse(session.act('pilot:'+actor,{'action_id':'choose','choice':choice}).ok)
            self.assertEqual(before,authoritative_state_hash(session.state))
        for seat in 'ABD':self.assertNotIn(private.ref,json.dumps(session.packet('pilot:'+seat,full=True)))
        self.assertEqual([],self.tokens(session))
        session = self.replay(session,load=True)
        result = session.act('pilot:A',{'action_id':'choose','choice':name});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(session))
        tokens = self.tokens(session);self.assertEqual(1,len(tokens),'exactly one chosen token must be created')
        token = tokens[0];self.assertEqual(name+' Token',token.printed_name);self.assertEqual('A',token.controller)
        return session,token

    def activate_token(self,session,token,**fields):
        self.priority(session,'A')
        action = next(row for row in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
            if row['id'].startswith('activate:'+token.ref+':'))
        command = {'action_id':action['id'],'pay':'auto',**fields};before = authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:D',command).ok);self.assertEqual(before,authoritative_state_hash(session.state))
        result = session.act('pilot:A',command);self.assertTrue(result.ok,result.summary)

    def doctor_witness(self,seed):
        session = self.session(seed);source = self.add(session.engine,'The Third Doctor',zone='hand')
        private = self.add(session.engine,'Counterspell',seat='C',zone='hand',ref='doctor-choice-private')
        self.assert_closed(session,source);self.prepare(session,mana={'C':4,'G':1,'U':1});self.checkpoint(session)
        self.cast(session,'A',source);self.assertEqual('semantic.choice',self.resolve(session).kind)
        schema = session.packet('pilot:A',full=True)['decision']['ctx']['legal_actions'][0]['choice_schema']
        self.assertEqual(['Clue','Food','Treasure'],list(schema['legal_values']))
        session,token = self.choose(session,'Clue',private)
        self.assertEqual(3,session.engine._numeric_stat(source.object_id,'power'))
        hand = len(session.state.players['A'].zones['hand'])
        self.activate_token(session,token);session = self.replay(session,load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(hand+1,len(session.state.players['A'].zones['hand']))
        self.assertEqual(2,session.engine._numeric_stat(source.object_id,'power'));self.replay(session,load=True)

    def test_actual_doctor_chooses_one_clue_then_uses_its_draw_ability(self):
        self.doctor_witness(993100)

    def test_normal_binding_cannot_omit_choice_or_child_definition_authority(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        session = self.session(993105);source = self.add(session.engine,'The Third Doctor')
        self.assert_closed(session,source)
        program, = [program for program in session.engine.semantics.programs_for_oracle(source.oracle_id)
            if any(effect.get('op')=='choose_option' for effect in program.effects)]
        for cap in (CAPABILITY,'token.creation.fixed_definition','life.change.effect','mana.activated.fixed_output','zone.draw.library_to_hand'):
            dependencies = tuple(value for value in program.capability_dependencies if value!=cap)
            missing = replace(program,capability_dependencies=dependencies,
                capability_closure=self.registry.closure(dependencies,profile='commander_review').to_dict())
            bound = bind_semantic_program_runtime(missing,capability_registry=self.registry,profile='commander_review')
            self.assertFalse(bound['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+cap,bound['blockers'])

    def test_actual_ant_army_chooses_food_then_pays_its_real_gain_life_ability(self):
        session = self.session(993110);source = self.add(session.engine,"Ant-Man's Army",zone='hand')
        private = self.add(session.engine,'Counterspell',seat='C',zone='hand',ref='ant-choice-private')
        self.assert_closed(session,source);self.prepare(session,mana={'C':4,'G':1});self.checkpoint(session)
        self.cast(session,'A',source);self.assertEqual('semantic.choice',self.resolve(session).kind)
        session,token = self.choose(session,'Food',private);life = session.state.players['A'].life
        self.activate_token(session,token);session = self.replay(session,load=True)
        self.assertIsNone(self.resolve(session));self.assertEqual(life+3,session.state.players['A'].life)
        self.assertNotEqual('battlefield',session.state.cards[token.object_id].zone);self.replay(session,load=True)

    def play_land(self,session,seat,land):
        self.priority(session,seat)
        action = next(row for row in session.packet('pilot:'+seat,full=True)['decision']['ctx']['legal']['actions']
            if row.get('card')==land.ref and row['action']=='play_land')
        result = session.act('pilot:'+seat,{'action_id':action['id']});self.assertTrue(result.ok,result.summary)

    def test_actual_provisioner_ignores_foreign_land_and_choice_survives_borrowed_source_departure(self):
        session = self.session(993120)
        for seat in 'ABCD':
            for oid in tuple(session.state.players[seat].zones['hand'])[5:]:
                session.engine.move_card(oid,'library',log=False,semantic_events=False)
        source = self.add(session.engine,'Tireless Provisioner');source.owner = 'B'
        own = self.add(session.engine,'Forest',zone='hand',ref='own-landfall-land')
        foreign = self.add(session.engine,'Forest',seat='B',zone='hand',ref='foreign-landfall-land')
        bounce = self.add(session.engine,'Unsummon',seat='B',zone='hand');self.add(session.engine,'Island',seat='B')
        private = self.add(session.engine,'Counterspell',seat='C',zone='hand',ref='provisioner-choice-private')
        self.assert_closed(session,source);self.prepare(session,actor='B',active='B')
        session.state.current_turn = replace(session.state.current_turn,player='B');session.state.last_normal_turn_player = 'B'
        self.checkpoint(session);self.play_land(session,'B',foreign)
        self.assertEqual(0,self.trigger_count(session,source));self.assertEqual([],self.tokens(session))
        self.until(session,lambda s:s.state.active_player=='C' and s.state.phase=='precombat_main')
        self.until(session,lambda s:s.state.active_player=='A' and s.state.phase=='precombat_main')
        self.play_land(session,'A',own);self.assertEqual(1,self.trigger_count(session,source))
        self.cast(session,'B',bounce,targets=[source.ref]);self.assertEqual('semantic.choice',self.resolve(session).kind)
        self.assertEqual('hand',session.state.cards[source.object_id].zone)
        session,token = self.choose(session,'Treasure',private)
        self.activate_token(session,token,mana_choice='G')
        self.assertFalse(session.state.stack);self.assertEqual(1,session.state.players['A'].mana_pool['G'])
        self.assertNotEqual('battlefield',session.state.cards[token.object_id].zone);self.replay(session,load=True)

    def test_fresh_all_branches_instead_of_selected_branch_mutant_is_killed(self):
        original = ChooseOptionHandler.complete;observed = []
        def all_branches(handler,continuation,response,query):
            result = original(handler,continuation,response,query)
            if continuation.effect.get('prompt')=='Choose a token to create.':
                observed.append(True)
                result = replace(result,prepend_effects=tuple(leaf for branch in continuation.effect['then_by_choice'].values() for leaf in branch))
            return result
        with patch.object(ChooseOptionHandler,'complete',all_branches):
            with self.assertRaisesRegex(AssertionError,'exactly one chosen token'):self.doctor_witness(993130)
        self.assertTrue(observed)
