"""CR118.7a/601.2f: locked next-eligible-spell discounts, never preview consumption."""
from copy import deepcopy
from dataclasses import replace
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.model import CardInstance
from quorune.characteristic_evaluation import type_parts
from quorune.object_predicate import ObjectQuerySpec
from quorune.continuous_effect_state import commit_continuous_effect,consume_continuous_effects,expire_end_of_turn_continuous_effects
from quorune.declaration_rule_effects import continuous_journal_effect_from_dict
from quorune.next_spell_cost_model import ResolvedNextSpellCostReduction,validate_next_spell_cost_instruction
from quorune.next_spell_costs import matching_next_spell_cost_reductions,active_next_spell_cost_reductions,consume_next_spell_rules
from quorune.compiler.next_spell_cost_templates import next_spell_cost_reduction_template
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class NextSpellCostModelTests(unittest.TestCase):
    def test_preview_preparation_and_committed_cast_consume_only_matching_controller_rules(self):
        query=ObjectQuerySpec();instant=ObjectQuerySpec(types_all=('instant',))
        rules=[ResolvedNextSpellCostReduction(key,'stack-source',index,actor,predicate,amount)
            for index,(key,actor,predicate,amount) in enumerate((('any','A',query,2),
                ('instant','A',instant,4),('other','B',query,7),('zero','A',query,0)))]
        state=SimpleNamespace(turn_sequence=1,continuous_effects=[],players={'A':SimpleNamespace(stats={
            'next_spell_improvise':True,'next_spell_uncounterable':True})})
        from quorune.rules.next_spell_payment import grant_next_spell_payment
        grant_next_spell_payment(state.players['A'].stats,1,'convoke')
        for rule in rules:commit_continuous_effect(state,rule)
        card=CardInstance('cost-card','P-cost','fixture:cost','Cost card','B','A','hand')
        host=SimpleNamespace(state=state,_type_parts=type_parts)
        before=[r.to_dict() for r in state.continuous_effects]
        with patch('quorune.compiled_cast_timing.prospective_conditional_flash_spell_data',
                return_value={'type_line':'Artifact','colors':[],'cmc':2}):
            for _ in range(3):
                self.assertEqual(['any','zero'],[r.effect_id for r in matching_next_spell_cost_reductions(host,'A',card)])
                self.assertEqual(2,sum(r.generic_reduction for r in active_next_spell_cost_reductions(host,'A',card)))
            self.assertEqual((True,True,('convoke',)),consume_next_spell_rules(host,SimpleNamespace(seat='A',type_line='Artifact'),card,preparing=True))
            self.assertEqual(before,[r.to_dict() for r in state.continuous_effects])
            self.assertEqual((True,True,('convoke',)),consume_next_spell_rules(host,SimpleNamespace(seat='A',type_line='Artifact'),card,preparing=False))
        self.assertEqual(['instant','other'],[r.effect_id for r in state.continuous_effects])
        self.assertEqual({},state.players['A'].stats)
        self.assertEqual(2,expire_end_of_turn_continuous_effects(state));self.assertEqual([],state.continuous_effects)
        historical=SimpleNamespace(continuous_effects=None)
        self.assertEqual(0,expire_end_of_turn_continuous_effects(historical));self.assertIsNone(historical.continuous_effects)

    def test_journal_codec_and_stale_consumption_reject_without_partial_mutation(self):
        rule=ResolvedNextSpellCostReduction('next','source',1,'A',ObjectQuerySpec(),0)
        self.assertEqual(rule,continuous_journal_effect_from_dict(rule.to_dict()))
        for changed in ({'schema_version':True},{'amount':True},{'amount':-1},{'controller':''},
                {'duration':'forever'},{'unknown':True}):
            with self.subTest(changed=changed),self.assertRaises(ValueError):
                continuous_journal_effect_from_dict({**rule.to_dict(),**changed})
        state=SimpleNamespace(continuous_effects=[rule])
        for ids in (('missing',),('next','next'),(False,)):
            with self.subTest(ids=ids),self.assertRaises(ValueError):consume_continuous_effects(state,ids)
            self.assertEqual([rule],state.continuous_effects)

    def test_closed_fixed_and_resolution_locked_amount_grammar(self):
        cases=('The next spell you cast this turn costs {1} less to cast.',
            'The next Giant spell you cast this turn costs {2} less to cast.',
            'The next instant or sorcery spell you cast this turn costs {X} less to cast, where X is the number of Wizards you control as this ability resolves.',
            "The next instant or sorcery spell you cast this turn costs {X} less to cast, where X is this creature's power as this ability resolves.")
        for text in cases:
            with self.subTest(text=text):
                compiled=next_spell_cost_reduction_template(text,source_name='Source',source_is_permanent=True)
                self.assertIsNotNone(compiled);validate_next_spell_cost_instruction(compiled[1][0])
        for text in ('The next two spells you cast this turn cost {1} less to cast.',
                'The next spell an opponent casts this turn costs {1} less to cast.',
                'The next spell you cast next turn costs {1} less to cast.',cases[0]+' Rewrite history.'):
            self.assertIsNone(next_spell_cost_reduction_template(text,source_name='Source',source_is_permanent=True))
        valid=next_spell_cost_reduction_template(cases[0],source_name='Source')[1][0]
        for changed in ({'amount':True},{'amount':-1},{'schema_version':True},{'actor':'B'}):
            with self.subTest(changed=changed),self.assertRaises(ValueError):validate_next_spell_cost_instruction({**valid,**changed})

    def test_verified_owner_withdrawal_retains_ordinary_draw_and_static_reducers(self):
        from test_qualified_zone_event_queries import query_record
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        for owner in ('casting.cost.next_eligible_spell_reduction','casting.cost.modifier.fixed_query'):
            raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
            next(r for r in raw['capabilities'] if r['id']==owner).update(status='blocked',blockers=['next-cost owner withdrawn'])
            registry=CapabilityRegistry(raw);index=build_index(registry_value=raw,
                declaration_source=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json'),discovered_test_ids=set(discover_tests(ROOT).values()))
            registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
            for text,kind,expected in (('The next spell you cast this turn costs {1} less to cast.','Sorcery',False),
                    ('Draw a card.','Sorcery',True),('Artifact spells you cast cost {1} less to cast.','Creature',owner!='casting.cost.modifier.fixed_query')):
                self.assertEqual(expected,compile_oracle_card(replace(query_record(text),type_line=kind),capability_registry=registry).status=='exact')


class NextSpellCostNativeTests(InheritedNativeFixture,unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'next-cost.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/next-spell-cost-reduction-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Next spell cost',[DeckEntry('Generic Bound Commander',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @staticmethod
    def pending(session):
        return [r for r in session.state.continuous_effects or () if isinstance(r,ResolvedNextSpellCostReduction)]

    def offered_cast(self,session,card):
        return self.action(session,'A',card)

    def hardened_witness(self,seed,*,colored_only=False):
        session=self.session(seed);source=self.add(session.engine,'Hardened Berserker');source.owner='B'
        self.add(session.engine,'Fervor');self.add(session.engine,'Island');self.add(session.engine,'Mountain',seat='B')
        removal=self.add(session.engine,'Lightning Bolt',seat='B',zone='hand')
        private=self.add(session.engine,'Counterspell',seat='B',zone='hand',ref='next-cost-private')
        if colored_only:spell=self.add(session.engine,'Reach Through Mists',zone='hand')
        else:spell=self.add(session.engine,'Think Twice',zone='hand')
        self.assert_closed(session,source);self.prepare(session);self.checkpoint(session)
        self.attack(session,[(source,'B')]);self.assertIsNone(self.resolve(session))
        self.assertEqual([1],[r.amount for r in self.pending(session)])
        for seat in 'ACD':self.assertNotIn(private.ref,json.dumps(session.packet('pilot:'+seat,full=True)))
        self.cast(session,'B',removal,[source.ref]);self.assertIsNone(self.resolve(session))
        self.assertEqual('graveyard',session.state.cards[source.object_id].zone)
        self.assertEqual([1],[r.amount for r in self.pending(session)])
        session=self.replay(session,load=True);offered=self.offered_cast(session,spell)
        command={'action_id':offered['id'],'pay':'auto'};before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:B',command).ok);self.assertEqual(before,authoritative_state_hash(session.state))
        result=session.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        self.assertEqual([],self.pending(session),'the next matching cast must consume its pending reduction')
        self.assertIsNone(self.resolve(session));self.replay(session,load=True)

    def test_actual_hardened_reduction_survives_source_and_other_caster_then_is_used_once(self):
        self.hardened_witness(990100)

    def test_actual_colored_only_cast_consumes_a_zero_benefit_discount(self):
        self.hardened_witness(990110,colored_only=True)

    def test_actual_kaza_locks_wizard_count_and_retains_it_across_a_nonmatching_cast(self):
        session=self.session(990200);source=self.add(session.engine,'Kaza, Roil Chaser')
        wizard=self.add(session.engine,'Prodigal Sorcerer');self.add(session.engine,'Island',ref='kaza-island-one')
        self.add(session.engine,'Island',ref='kaza-island-two');self.add(session.engine,'Mountain',seat='B')
        removal=self.add(session.engine,'Lightning Bolt',seat='B',zone='hand')
        other=self.add(session.engine,'Ornithopter',zone='hand');spell=self.add(session.engine,'Concentrate',zone='hand')
        self.assert_closed(session,source);self.prepare(session);self.checkpoint(session)
        offered=next(a for a in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
            if a.get('source')==source.ref and a['id'].startswith('activate:'))
        result=session.act('pilot:A',{'action_id':offered['id'],'pay':'auto'});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(session));self.assertEqual([2],[r.amount for r in self.pending(session)])
        self.cast(session,'B',removal,[wizard.ref]);self.assertIsNone(self.resolve(session))
        self.cast(session,'A',other);self.assertIsNone(self.resolve(session))
        self.assertEqual([2],[r.amount for r in self.pending(session)])
        session=self.replay(session,load=True);self.cast(session,'A',spell);self.assertEqual([],self.pending(session))
        self.assertIsNone(self.resolve(session));self.replay(session,load=True)

    def test_actual_kaza_counts_wizards_after_a_response_before_resolution(self):
        session=self.session(990250);source=self.add(session.engine,'Kaza, Roil Chaser')
        wizard=self.add(session.engine,'Prodigal Sorcerer');self.add(session.engine,'Island',ref='response-island-one')
        self.add(session.engine,'Island',ref='response-island-two');self.add(session.engine,'Mountain',seat='B')
        removal=self.add(session.engine,'Lightning Bolt',seat='B',zone='hand')
        expensive=self.add(session.engine,'Concentrate',zone='hand');cheap=self.add(session.engine,'Think Twice',zone='hand')
        self.assert_closed(session,source);self.prepare(session);self.checkpoint(session)
        offered=next(a for a in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
            if a.get('source')==source.ref and a['id'].startswith('activate:'))
        result=session.act('pilot:A',{'action_id':offered['id'],'pay':'auto'});self.assertTrue(result.ok,result.summary)
        self.cast(session,'B',removal,[wizard.ref]);self.resolve_top(session)
        self.assertEqual([],self.pending(session));session=self.replay(session,load=True)
        self.assertIsNone(self.resolve(session));self.assertEqual([1],[r.amount for r in self.pending(session)])
        offered=self.offered_cast(session,cheap)
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        self.assertNotIn('cast:'+expensive.ref,{a['id'] for a in actions})
        self.assertEqual([1],[r.amount for r in self.pending(session)])
        result=session.act('pilot:A',{'action_id':offered['id'],'pay':'auto'});self.assertTrue(result.ok,result.summary)
        self.assertEqual([],self.pending(session));self.assertIsNone(self.resolve(session));self.replay(session,load=True)

    def test_actual_maelstrom_locks_last_known_pumped_power_before_the_discount(self):
        session=self.session(990300);source=self.add(session.engine,'Maelstrom Muse')
        self.add(session.engine,'Fervor');self.add(session.engine,'Island',ref='muse-island-one')
        self.add(session.engine,'Island',ref='muse-island-two')
        self.add(session.engine,'Forest',seat='B');self.add(session.engine,'Swamp',seat='B')
        self.add(session.engine,'Mountain',seat='B')
        growth=self.add(session.engine,'Giant Growth',seat='B',zone='hand');kill=self.add(session.engine,'Infernal Grasp',seat='B',zone='hand')
        spell=self.add(session.engine,'Opportunity',zone='hand');self.assert_closed(session,source)
        self.prepare(session);self.checkpoint(session);self.attack(session,[(source,'B')])
        self.cast(session,'B',growth,[source.ref]);self.resolve_top(session)
        self.cast(session,'B',kill,[source.ref]);self.resolve_top(session)
        self.assertEqual('graveyard',session.state.cards[source.object_id].zone)
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual([5],[r.amount for r in self.pending(session)],'the resolved discount must use last-known pumped power')
        offered=self.offered_cast(session,spell);before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:A',{'action_id':offered['id'],'pay':'auto'}).ok)
        self.assertEqual(before,authoritative_state_hash(session.state));self.assertEqual([5],[r.amount for r in self.pending(session)])
        self.cast(session,'A',spell,['A']);self.assertEqual([],self.pending(session));self.assertIsNone(self.resolve(session))
        self.replay(session,load=True)

    def test_actual_peerless_uses_another_controlled_warrior_attacking_alone(self):
        session=self.session(990400);source=self.add(session.engine,'Peerless Samurai')
        attacker=self.add(session.engine,'Goblin Piker');self.add(session.engine,'Fervor');self.add(session.engine,'Island')
        spell=self.add(session.engine,'Think Twice',zone='hand');self.assert_closed(session,source)
        self.prepare(session);self.checkpoint(session);self.attack(session,[(attacker,'B')])
        self.assertIsNone(self.resolve(session));self.assertEqual([1],[r.amount for r in self.pending(session)])
        self.cast(session,'A',spell);self.assertEqual([],self.pending(session));self.assertIsNone(self.resolve(session))
        self.replay(session,load=True)

    def test_actual_unused_discount_expires_through_real_turn_cleanup(self):
        session=self.session(990500);source=self.add(session.engine,'Kaza, Roil Chaser')
        for oid in tuple(session.state.players['A'].zones['hand']):session.engine.move_card(oid,'library',log=False,semantic_events=False)
        self.assert_closed(session,source);self.prepare(session);self.checkpoint(session)
        offered=next(a for a in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
            if a.get('source')==source.ref and a['id'].startswith('activate:'))
        result=session.act('pilot:A',{'action_id':offered['id'],'pay':'auto'});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(session));self.assertEqual([1],[r.amount for r in self.pending(session)])
        session=self.replay(session,load=True);self.until(session,lambda s:s.state.active_player=='B')
        self.assertEqual([],self.pending(session));self.replay(session,load=True)

    def test_fresh_discount_consumption_omission_is_killed(self):
        import quorune.continuous_effect_state as owner
        observed=[]
        def omitted(state,identities):observed.extend(identities);return 0
        with patch.object(owner,'consume_continuous_effects',omitted):
            with self.assertRaisesRegex(AssertionError,'next matching cast must consume'):
                self.hardened_witness(990600)
        self.assertTrue(observed)
