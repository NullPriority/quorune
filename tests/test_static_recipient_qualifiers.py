"""CR611.3/613.4c: each recipient's current qualification gates one fixed bonus."""
from dataclasses import replace
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from quorune.compiler.static_recipient_qualifiers import qualified_public_query, recipient_qualified_static_handler
from quorune.compiler.public_state_queries import fixed_battlefield_query_subject, fixed_public_state_condition
from quorune.object_predicate import ObjectQuerySpec, ObjectQueryError, object_query_capabilities
from quorune.object_query import ObjectQueryResult, object_matches_query
from quorune.battlefield_mana_value_query import PERMANENT_MANA_VALUE_QUERY_CAPABILITY
from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_target_recipient_results as recipients


class StaticRecipientQualifierModelTests(unittest.TestCase):
    def test_named_counter_minima_keep_controller_and_fixed_not_per_counter_semantics(self):
        for phrase,count in [('Each creature you control with a level counter on it',1),
                ('creature with three or more level counters on it you control',3)]:
            relation,query,exclude=fixed_battlefield_query_subject(phrase)
            self.assertEqual('source_controller',relation);self.assertFalse(exclude)
            self.assertEqual(('creature',),query.types_all)
            self.assertEqual('level',query.state_predicate.counter_name)
            self.assertEqual(count,query.state_predicate.minimum_counter_count)
        for phrase in ['creatures you control with two level counters on it',
                'creatures you control with zero level counters on it',
                'creatures you control with a level counter on the chosen creature']:
            self.assertIsNone(qualified_public_query(phrase,compile_base=fixed_battlefield_query_subject))

    def test_permanent_mana_value_bounds_have_an_explicit_new_wire_shape(self):
        _relation,query,_exclude=fixed_battlefield_query_subject('Creatures you control with mana value 3 or less')
        self.assertEqual(3,query.permanent_mana_value_max)
        wire=query.to_dict();self.assertIn('permanent_mana_value_max',wire);self.assertNotIn('mana_value_max',wire)
        self.assertEqual(query,ObjectQuerySpec.from_dict(wire))
        self.assertIn(PERMANENT_MANA_VALUE_QUERY_CAPABILITY,object_query_capabilities(wire))
        for value in [True,-1,21,None]:
            with self.assertRaises(ObjectQueryError):ObjectQuerySpec.from_dict({**wire,'permanent_mana_value_max':value})
        with self.assertRaises(ObjectQueryError):ObjectQuerySpec.from_dict({**wire,'zones':['hand']})
        old=ObjectQuerySpec(zones=('graveyard',),token=False,mana_value_max=3)
        self.assertIn('mana_value_max',old.to_dict());self.assertNotIn('permanent_mana_value_max',old.to_dict())
        self.assertEqual(old.to_dict(),ObjectQuerySpec.from_dict(old.to_dict()).to_dict())

    def test_recipient_conditions_are_permanent_sets_not_conditions_on_the_source(self):
        land=recipient_qualified_static_handler("Each land gets +2/+2 as long as it's a creature.")
        query=ObjectQuerySpec.from_dict(land[1]['condition']['predicate'])
        self.assertEqual(('creature','land'),query.types_all);self.assertEqual('any',land[1]['condition']['target_controller'])
        idle=recipient_qualified_static_handler("Each untapped creature you control gets +0/+2 as long as it's not attacking.")
        query=ObjectQuerySpec.from_dict(idle[1]['condition']['predicate'])
        self.assertIs(query.state_predicate.tapped,False);self.assertIs(query.state_predicate.attacking,False)
        for text in ["Each land gets +2/+2 as long as it's a creature or an artifact.",
                "Each untapped creature you control gets +0/+2 as long as another creature isn't attacking."]:
            self.assertIsNone(recipient_qualified_static_handler(text))

    def test_negative_source_combat_condition_requires_neither_attacking_nor_blocking(self):
        condition=fixed_public_state_condition("it isn't attacking or blocking",source_name='Generic Pilot')
        self.assertIs(condition.predicate.state_predicate.attacking,False)
        self.assertIs(condition.predicate.state_predicate.blocking,False)
        self.assertIsNone(fixed_public_state_condition("it isn't attacking or blocking another creature",source_name='Generic Pilot'))

    def test_counter_mana_value_and_combat_predicates_match_current_public_facts(self):
        row=ObjectQueryResult('body','body-ref','Generic Body','A','A','battlefield',types=('creature',),
            counters={'level':3},mana_value=3)
        for phrase,changes in [('creatures with three or more level counters on it',{'counters':{'level':2}}),
                ('creatures with mana value 3 or less',{'mana_value':4})]:
            _,query,_=fixed_battlefield_query_subject(phrase)
            self.assertTrue(object_matches_query(row,query))
            self.assertFalse(object_matches_query(replace(row,**changes),query))
        idle=recipient_qualified_static_handler("Each untapped creature you control gets +0/+2 as long as it's not attacking.")
        query=ObjectQuerySpec.from_dict(idle[1]['condition']['predicate'])
        self.assertTrue(object_matches_query(replace(row,blocking=True),query))
        self.assertFalse(object_matches_query(replace(row,attacking=True),query))
        self.assertFalse(object_matches_query(replace(row,tapped=True),query))

    def test_missing_one_negative_combat_term_mutant_is_killed(self):
        from quorune.compiler import static_recipient_qualifiers as owner
        original=owner.negative_source_combat_condition
        def witness():
            condition=fixed_public_state_condition("it isn't attacking or blocking",source_name='Generic Pilot')
            row=ObjectQueryResult('body','body-ref','Generic Pilot','A','A','battlefield',types=('creature',),blocking=True)
            self.assertFalse(object_matches_query(row,condition.predicate))
        witness()
        def omitted(text,*,source_name):
            condition=original(text,source_name=source_name)
            return replace(condition,predicate=replace(condition.predicate,
                state_predicate=replace(condition.predicate.state_predicate,blocking=None)))
        with patch.object(owner,'negative_source_combat_condition',side_effect=omitted),self.assertRaises(AssertionError):witness()


class StaticRecipientQualifierCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'static-qualification-compiler.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/static-recipient-qualifier-cards.json'],path)
        cls.db=CardDatabase(path)

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()

    def test_complete_original_static_clauses_keep_source_spans_and_all_siblings(self):
        registry=load_default_capability_registry()
        for name in ['Time of Heroes',"Champion's Drake",'Hero of the Dunes','Earth Surge','Intrepid Ace','Arcades Sabboth']:
            with self.subTest(name=name):
                card=self.db.lookup(name,fuzzy=False);ir=compile_oracle_card(card,capability_registry=registry,capability_profile='commander_review')
                self.assertEqual('exact',ir.status,ir.material_residuals)
                for face in ir.faces:
                    for node in face.nodes:
                        self.assertEqual(node.text,card.oracle_text[node.span.start:node.span.end])

    def test_all_six_whole_originals_admit_through_normal_registration(self):
        from quorune.oracle_ir import register_generated_programs
        from quorune.semantics import SemanticRegistry
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.card_programs.binding import bind_card_program_runtime
        registry=load_default_capability_registry();semantics=SemanticRegistry()
        cards=[self.db.lookup(name,fuzzy=False) for name in ['Time of Heroes',"Champion's Drake",'Hero of the Dunes','Earth Surge','Intrepid Ace','Arcades Sabboth']]
        register_generated_programs(self.db,semantics,cards,trust_level='provisional',capability_registry=registry,
            capability_profile='commander_review',promote_exact_runtime_handlers=True,promote_exact_trigger_programs=True,
            promote_exact_effect_programs=True,promote_exact_capability_declarations=True)
        for card in cards:
            program=compile_best_available_card_program(self.db,card,semantic_registry=semantics,capability_registry=registry,capability_profile='commander_review')
            bound=bind_card_program_runtime(program,capability_registry=registry,profile='commander_review')
            self.assertTrue(bound['strict_capability_ready'],(card.name,bound['blockers']))
            self.assertEqual('capability_closed',bound['trust_basis'])

    def test_verified_permanent_mana_value_authority_withdrawal_rejects_hero(self):
        from copy import deepcopy
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        changed=deepcopy(raw);next(r for r in changed['capabilities'] if r['id']==PERMANENT_MANA_VALUE_QUERY_CAPABILITY).update(status='blocked',blockers=['public permanent mana-value authority withdrawn'])
        registry=CapabilityRegistry(changed)
        index=build_index(registry_value=changed,declaration_source=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json'),discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
        hero=self.db.lookup('Hero of the Dunes',fuzzy=False)
        self.assertNotEqual('exact',compile_oracle_card(hero,capability_registry=registry,capability_profile='commander_review').status)

    def test_unrepresented_recipient_antecedents_and_comparisons_stay_residual(self):
        from test_qualified_zone_event_queries import query_record
        registry=load_default_capability_registry()
        for text in ['Creatures you control with mana value X or less get +1/+0.',
                'Each creature you control with exactly two level counters on it gets +2/+2.',
                "Each land gets +2/+2 as long as another land is a creature.",
                "Each untapped creature you control gets +0/+2 as long as it's not attacking you."]:
            self.assertNotEqual('exact',compile_oracle_card(query_record(text),capability_registry=registry,capability_profile='commander_review').status)


class StaticRecipientQualifierNativeTests(InheritedNativeFixture,unittest.TestCase):
    reject=recipients.TargetRecipientNativeTests.reject
    finish=recipients.TargetRecipientNativeTests.finish

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'static-recipient-qualifications.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/static-recipient-qualifier-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Static qualifications',[DeckEntry('Sliver Hivelord',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Sliver Hivelord'])

    def stats(self,s,card):
        return tuple(s.engine._numeric_stat(card.object_id,stat) for stat in ('power','toughness'))

    def activate(self,s,seat,card,*,targets=(),ability=None):
        self.priority(s,seat)
        abilities=s.engine._activated_abilities(s.state.cards[card.object_id])
        selected=ability or abilities[0]
        command={'action_id':'activate:'+card.ref+':'+selected.ability_id,'targets':list(targets),'pay':'auto'}
        self.reject(s,'D',command)
        result=s.act('pilot:'+seat,command);self.assertTrue(result.ok,result.summary)

    def test_actual_level_counters_enable_fixed_bonuses_without_multiplying_and_removal_ends_them(self):
        s=self.session(1116220);anthem=self.add(s.engine,'Time of Heroes',zone='hand')
        drake=self.add(s.engine,"Champion's Drake",zone='hand');student=self.add(s.engine,'Student of Warfare')
        bear=self.add(s.engine,'Grizzly Bears');bear.counters['level']=1
        opposing=self.add(s.engine,'Student of Warfare',seat='B',ref='opposing-leveler');opposing.counters['level']=3
        hexmage=self.add(s.engine,'Vampire Hexmage',seat='B')
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='hidden-level-witness-C')
        self.assert_closed(s,anthem);self.assert_closed(s,drake);self.assert_closed(s,student)
        self.prepare(s,mana={'C':3,'W':6,'U':1});self.checkpoint(s)
        self.cast(s,'A',anthem);self.assertIsNone(self.resolve(s));self.cast(s,'A',drake);self.assertIsNone(self.resolve(s))
        self.assertEqual((1,1),self.stats(s,drake));self.assertEqual((4,4),self.stats(s,bear))
        for level in range(1,4):
            ability=next(a for a in s.engine._activated_abilities(student) if 'level counter' in a.effect_text)
            self.activate(s,'A',student,ability=ability);self.assertIsNone(self.resolve(s))
            self.assertEqual(level,s.state.cards[student.object_id].counters['level'])
            self.assertEqual((4,4) if level==3 else (1,1),self.stats(s,drake))
            self.assertEqual((3,3) if level==1 else (5,5),self.stats(s,student))
            self.assertEqual((4,4),self.stats(s,bear))
        self.replay(s,load=True)
        self.activate(s,'B',hexmage,targets=[student.ref]);self.assertIsNone(self.resolve(s))
        self.assertEqual((1,1),self.stats(s,drake));self.assertEqual((1,1),self.stats(s,student))
        self.assertEqual((4,4),self.stats(s,bear));self.finish(s,private)

    def hero_witness(self,seed,*,lifecycle=False):
        s=self.session(seed);hero=self.add(s.engine,'Hero of the Dunes',zone='hand')
        artifact=self.add(s.engine,'Sol Ring',zone='graveyard');bear=self.add(s.engine,'Grizzly Bears')
        big=self.add(s.engine,'Colossal Dreadmaw');opposing=self.add(s.engine,'Grizzly Bears',seat='B',ref='opposing-hero-body')
        donate=self.add(s.engine,'Donate',zone='hand');alarm=self.add(s.engine,'Raise the Alarm',zone='hand')
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='hidden-hero-witness-C')
        self.assert_closed(s,hero);self.prepare(s,mana={'C':7,'W':3,'B':1,'U':1});self.checkpoint(s)
        self.cast_for(s,'A',hero,reserve={'W':1,'U':1});self.resolve_top(s);self.target(s,'A',[artifact.ref]);self.assertIsNone(self.resolve(s))
        self.assertEqual('battlefield',s.state.cards[artifact.object_id].zone)
        self.assertEqual((3,2),self.stats(s,bear));self.assertEqual((6,6),self.stats(s,big))
        self.assertEqual((3,2),self.stats(s,hero));self.assertEqual((2,2),self.stats(s,opposing))
        if lifecycle:
            self.cast_for(s,'A',alarm,reserve={'U':1});self.assertIsNone(self.resolve(s))
            tokens=[c for c in s.state.cards.values() if c.is_token and c.zone=='battlefield']
            self.assertEqual(2,len(tokens))
            for token in tokens:self.assertEqual((2,1),self.stats(s,token))
            self.cast(s,'A',donate,["B",hero.ref]);self.assertIsNone(self.resolve(s))
            self.assertEqual((2,2),self.stats(s,bear));self.assertEqual((3,2),self.stats(s,opposing))
            for token in tokens:self.assertEqual((1,1),self.stats(s,token))
        self.finish(s,private)

    def test_actual_hero_returns_an_artifact_and_uses_live_mana_value_control_and_zero_cost_tokens(self):
        self.hero_witness(1116230,lifecycle=True)

    def test_fresh_actual_hero_kills_missing_mana_value_filter_mutant(self):
        self.hero_witness(1116231)
        with patch('quorune.object_query.permanent_mana_value_matches',return_value=True),self.assertRaises(AssertionError):
            self.hero_witness(1116232)

    def test_actual_hero_requires_independent_permanent_mana_value_runtime_authority(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        s=self.session(1116233);hero=self.add(s.engine,'Hero of the Dunes');self.assert_closed(s,hero)
        programs=[p for p in s.engine.semantics.programs_for_oracle(hero.oracle_id) if PERMANENT_MANA_VALUE_QUERY_CAPABILITY in p.capability_dependencies]
        self.assertTrue(programs)
        for program in programs:
            deps=tuple(c for c in program.capability_dependencies if c!=PERMANENT_MANA_VALUE_QUERY_CAPABILITY)
            changed=replace(program,capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
            result=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review')
            self.assertFalse(result['strict'])
            self.assertIn('capability:undeclared_runtime_dependency:'+PERMANENT_MANA_VALUE_QUERY_CAPABILITY,result['blockers'])

    def test_actual_earth_surge_only_bonuses_current_land_creatures_for_all_players(self):
        s=self.session(1116240);source=self.add(s.engine,'Earth Surge',zone='hand')
        a=self.add(s.engine,'Dryad Arbor');b=self.add(s.engine,'Dryad Arbor',seat='B',ref='opposing-dryad')
        plain=self.add(s.engine,'Forest');bear=self.add(s.engine,'Grizzly Bears')
        factory=self.add(s.engine,"Mishra's Factory");destroy=self.add(s.engine,'Disenchant',seat='B',zone='hand')
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='hidden-earth-witness-C')
        self.assert_closed(s,source);self.prepare(s,mana={'C':4,'G':1});s.state.players['B'].mana_pool.update({'C':1,'W':1});self.checkpoint(s)
        self.cast(s,'A',source);self.assertIsNone(self.resolve(s))
        self.assertEqual((3,3),self.stats(s,a));self.assertEqual((3,3),self.stats(s,b));self.assertEqual((2,2),self.stats(s,bear))
        self.assertNotIn('creature',s.engine._type_parts(s.engine._effective_card_data(plain)['type_line'])[0])
        ability=next(a for a in s.engine._activated_abilities(factory) if 'becomes' in a.effect_text)
        self.activate(s,'A',factory,ability=ability);self.assertIsNone(self.resolve(s));self.assertEqual((4,4),self.stats(s,factory))
        self.cast(s,'B',destroy,[source.ref]);self.assertIsNone(self.resolve(s))
        self.assertEqual((1,1),self.stats(s,a));self.assertEqual((1,1),self.stats(s,b));self.assertEqual((2,2),self.stats(s,factory))
        self.finish(s,private)

    def test_actual_intrepid_ace_keeps_tapped_idle_bonus_and_loses_attack_bonus_until_combat_ends(self):
        s=self.session(1116250);source=self.add(s.engine,'Intrepid Ace',zone='hand')
        idle=self.add(s.engine,'Intrepid Ace',ref='tapped-idle-ace');idle.tapped=True
        blocker=self.add(s.engine,'Wall of Omens',seat='B');self.add(s.engine,'Fervor')
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='hidden-ace-witness-C')
        self.assert_closed(s,source);self.prepare(s,mana={'R':1});self.checkpoint(s)
        self.cast(s,'A',source);self.assertIsNone(self.resolve(s));self.assertEqual((4,1),self.stats(s,source));self.assertEqual((4,1),self.stats(s,idle))
        self.attack(s,[(source,'B')]);self.assertEqual((2,1),self.stats(s,source))
        self.until(s,lambda s:s.state.pending_decision is not None and s.state.pending_decision.kind=='combat.blockers')
        result=s.act('pilot:B',{'action_id':'block','blocks':{blocker.ref:[source.ref]}});self.assertTrue(result.ok,result.summary)
        self.until(s,lambda s:s.state.phase_index>=9)
        self.assertEqual((4,1),self.stats(s,source));self.finish(s,private)

    def test_actual_arcades_untapped_attacker_loses_bonus_and_ordinary_activation_remains(self):
        s=self.session(1116260);source=self.add(s.engine,'Arcades Sabboth',zone='hand')
        angel=self.add(s.engine,'Serra Angel');self.add(s.engine,'Fervor')
        tapped=self.add(s.engine,'Grizzly Bears');tapped.tapped=True
        opposing=self.add(s.engine,'Grizzly Bears',seat='B',ref='opposing-arcades-body')
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='hidden-arcades-witness-C')
        self.assert_closed(s,source);self.prepare(s,mana={'C':2,'G':2,'W':3,'U':2});self.checkpoint(s)
        self.cast_for(s,'A',source,reserve={'W':1});self.assertIsNone(self.resolve(s));self.assertEqual((7,9),self.stats(s,source))
        self.assertEqual((4,6),self.stats(s,angel));self.assertEqual((2,2),self.stats(s,tapped));self.assertEqual((2,2),self.stats(s,opposing))
        self.activate(s,'A',source);self.assertIsNone(self.resolve(s));self.assertEqual((7,10),self.stats(s,source))
        self.attack(s,[(angel,'B')]);self.assertFalse(s.state.cards[angel.object_id].tapped)
        self.assertEqual((4,4),self.stats(s,angel));self.assertEqual((7,10),self.stats(s,source));self.finish(s,private)

    def test_actual_blockers_keep_arcades_bonus_and_lose_intrepid_idle_power(self):
        s=self.session(1116261);source=self.add(s.engine,'Arcades Sabboth')
        ace=self.add(s.engine,'Intrepid Ace');wall=self.add(s.engine,'Wall of Omens')
        first=self.add(s.engine,'Grizzly Bears',seat='B',ref='first-block-witness-bear')
        second=self.add(s.engine,'Grizzly Bears',seat='B',ref='second-block-witness-bear');self.add(s.engine,'Fervor',seat='B')
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='hidden-block-witness-C')
        self.assert_closed(s,source);self.assert_closed(s,ace)
        self.prepare(s,actor='B',active='B');self.checkpoint(s)
        self.assertEqual((4,3),self.stats(s,ace));self.assertEqual((0,6),self.stats(s,wall))
        self.until(s,lambda s:s.state.pending_decision is not None and s.state.pending_decision.kind=='combat.attackers')
        result=s.act('pilot:B',{'action_id':'attack','attackers':{first.ref:'A',second.ref:'A'}});self.assertTrue(result.ok,result.summary)
        self.until(s,lambda s:s.state.pending_decision is not None and s.state.pending_decision.kind=='combat.blockers')
        command={'action_id':'block','blocks':{ace.ref:[first.ref],wall.ref:[second.ref]}}
        self.reject(s,'D',command);result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        self.assertEqual((2,3),self.stats(s,ace));self.assertEqual((0,6),self.stats(s,wall))
        self.replay(s,load=True);self.until(s,lambda s:s.state.phase_index>=9)
        self.assertEqual((4,3),self.stats(s,ace));self.assertEqual((0,6),self.stats(s,wall));self.finish(s,private)

    def test_actual_arcades_upkeep_payment_and_sacrifice_keep_the_static_lifetime(self):
        from quorune.engine import TURN_STEPS
        for pay in (False,True):
            with self.subTest(pay=pay):
                s=self.session(1116270+pay);source=self.add(s.engine,'Arcades Sabboth');bear=self.add(s.engine,'Grizzly Bears')
                for name in ['Forest','Island','Generic Bound Plains']:self.add(s.engine,name,ref='upkeep-'+name)
                private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='hidden-upkeep-witness-C')
                self.assert_closed(s,source);self.prepare(s,actor='D',active='D')
                s.engine.permissions.invalidate_current();s.state.pending_decision=None;s.state.last_normal_turn_player='D'
                if s.state.current_turn is not None:s.state.current_turn=replace(s.state.current_turn,player='D')
                s.state.phase_index=len(TURN_STEPS)-2;s.state.phase,s.state.step=TURN_STEPS[s.state.phase_index]
                s.engine._grant_priority('D');s.engine.pump();self.checkpoint(s)
                for _ in range(100):
                    s.engine.pump();decision=s.state.pending_decision
                    if s.state.active_player=='A' and s.state.step=='upkeep' and s.state.stack and decision is not None and decision.kind=='priority':break
                    principal=s.pending_principals()[0]
                    if decision.kind=='priority':command={'action_id':'pass'}
                    elif decision.kind=='cleanup.discard':
                        ctx=s.packet(principal,full=True)['decision']['ctx'];command={'action_id':'discard','cards':[row['id'] for row in ctx['hand'][:ctx['count']]]}
                    else:self.fail('Unexpected turn decision: '+decision.kind)
                    result=s.act(principal,command);self.assertTrue(result.ok,result.summary)
                else:self.fail('Actual next upkeep was not reached')
                self.assertEqual((2,4),self.stats(s,bear));decision=self.resolve(s);self.assertEqual('semantic.choice',decision.kind)
                self.replay(s,load=True);command={'action_id':'choose','pay':pay};self.reject(s,'B',command)
                result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(s))
                self.assertEqual('battlefield' if pay else 'graveyard',s.state.cards[source.object_id].zone)
                self.assertEqual((2,4) if pay else (2,2),self.stats(s,bear));self.finish(s,private)
