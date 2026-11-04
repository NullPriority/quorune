"""Leading temporary duration preserves the existing quantity/result authority."""
import unittest
from quorune.oracle_ir import _reviewed_effect_template


class LeadingDurationQuantityCompilerTests(unittest.TestCase):
    def test_leading_duration_matches_existing_closed_quantity_and_keyword_forms(self):
        cases=(
            ('Until end of turn, target creature gets +1/+1 for each creature you control and gains trample.',
             'Target creature gets +1/+1 and gains trample until end of turn for each creature you control.'),
            ('Until end of turn, target creature gains trample and gets +1/+1 for each basic land type among lands you control.',
             'Target creature gets +1/+1 and gains trample until end of turn for each basic land type among lands you control.'),
            ('Until end of turn, this creature gets +1/+1 for each permanent card in your graveyard.',
             'This creature gets +1/+1 until end of turn for each permanent card in your graveyard.'),
            ('Until end of turn, target creature gets -2/-1 for each artifact you control and gains reach.',
             'Target creature gets -2/-1 and gains reach until end of turn for each artifact you control.'),
        )
        for leading,established in cases:
            with self.subTest(text=leading):
                expected=_reviewed_effect_template(established,card_name='Leading duration fixture')
                self.assertIsNotNone(expected[0])
                actual=_reviewed_effect_template(leading,card_name='Leading duration fixture')
                self.assertIsNotNone(actual[0])
                self.assertEqual(expected,actual)

    def test_leading_duration_does_not_absorb_unrepresented_conditions_or_keywords(self):
        for text in (
                'Until end of turn, target creature gets +1/+1 for each mystery you control.',
                'Until end of turn, target creature gets +1/+1 for each creature you control and gains rewrite history.',
                'Until end of turn, target creature gets +1/+1 for each creature you control if you won the game.',
                'Until end of turn, target creature gets +1/+1 for each creature you control until your next turn.',
                'Until end of turn, target creature gains trample and gets +1/+1 for each creature you control and gains reach.',
                'Until end of turn, target creature gets +1/+1 for each creature you control. Rewrite history.'):
            with self.subTest(text=text):
                self.assertIsNone(_reviewed_effect_template(text,card_name='Leading duration fixture')[0])

    def test_global_quantity_result_requires_composition_over_the_fixed_leaf(self):
        from quorune.compiler.fixed_public_characteristic_sets import fixed_public_characteristic_set_effect_template
        text='Until end of turn, creatures you control gain trample and get +1/+1 for each basic land type among lands you control.'
        self.assertIsNone(fixed_public_characteristic_set_effect_template(text))
        self.assertIsNotNone(_reviewed_effect_template(text,card_name='Leading duration fixture')[0])

    def test_normal_complete_program_requires_the_existing_public_quantity_owner(self):
        import json
        from common import ROOT
        from test_public_query_effect_amounts import query_amount_record, _NoRulingsDatabase
        from quorune.card_programs import compile_card_program,bind_card_program_runtime
        from quorune.rules.capabilities import CapabilityRegistry,load_default_capability_registry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        record=query_amount_record('Until end of turn, target creature gets +1/+1 for each creature you control and gains trample.',suffix=365211100)
        registry=load_default_capability_registry()
        program=compile_card_program(_NoRulingsDatabase(),record,capability_registry=registry,
            capability_profile='commander_review',trust_level='trusted')
        bound=bind_card_program_runtime(program,capability_registry=registry,profile='commander_review')
        self.assertTrue(bound['strict_capability_ready'],bound)
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding="utf-8"))
        cap='quantity_expression.public_query_effect_amount'
        next(row for row in raw['capabilities'] if row['id']==cap).update(status='blocked',blockers=['quantity producer withdrawn'])
        blocked=CapabilityRegistry(raw)
        index=build_index(registry_value=raw,declaration_source=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json'),
            discovered_test_ids=set(discover_tests(ROOT).values()))
        blocked.mark_evidence_verified(validate_capability_evidence_index(index,registry=blocked))
        with self.assertRaisesRegex(ValueError,'cannot be promoted to trusted'):
            compile_card_program(_NoRulingsDatabase(),record,capability_registry=blocked,
                capability_profile='commander_review',trust_level='trusted')
        unresolved=compile_card_program(_NoRulingsDatabase(),record,capability_registry=blocked,
            capability_profile='commander_review',trust_level='provisional')
        self.assertFalse(bind_card_program_runtime(unresolved,capability_registry=blocked,profile='commander_review')['strict_capability_ready'])

    def test_removing_leading_duration_grammar_is_killed(self):
        from unittest.mock import patch
        from quorune.compiler import public_query_effect_amounts as owner
        case=type(self)('test_leading_duration_matches_existing_closed_quantity_and_keyword_forms')
        baseline=unittest.TestResult();case.run(baseline);self.assertTrue(baseline.wasSuccessful())
        legacy=tuple((kind,pattern) for kind,pattern in owner._QUERY_CHARACTERISTIC_PATTERNS
            if 'Until end of turn' not in pattern.pattern)
        self.assertLess(len(legacy),len(owner._QUERY_CHARACTERISTIC_PATTERNS))
        with patch.object(owner,'_QUERY_CHARACTERISTIC_PATTERNS',legacy):
            mutant=unittest.TestResult();case.run(mutant)
        self.assertFalse(mutant.wasSuccessful());self.assertFalse(mutant.errors)


import json,tempfile
from pathlib import Path
from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.rules.capabilities import load_default_capability_registry
from quorune.record import authoritative_state_hash
from scripts.build_test_database import build_fixture_database


class LeadingDurationQuantityNativeTests(InheritedNativeFixture,unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'leading-duration-quantity.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/leading-duration-quantity-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Leading duration quantities',[DeckEntry('Isamaru, Hound of Konda',1,'commander'),
            DeckEntry('Generic Bound Plains',20)],['Isamaru, Hound of Konda'])

    def stats(self,s,card):
        data=s.engine._effective_card_data(s.state.cards[card.object_id])
        return int(data['power']),int(data['toughness'])

    def test_actual_target_and_source_read_resolution_quantities_then_keep_fixed_results_until_cleanup(self):
        s=self.session(111400301)
        for oid in list(s.state.players['A'].zones['hand']):s.engine.move_card(oid,'library',log=False)
        bear=self.add(s.engine,'Grizzly Bears');root=self.add(s.engine,'Sunbathing Rootwalla')
        blocker=self.add(s.engine,'Grizzly Bears',seat='B',ref='duration-blocker')
        for name in ('Forest','Plains'):self.add(s.engine,name)
        island=self.add(s.engine,'Island')
        chorus=self.add(s.engine,'Chorus of Might',zone='hand')
        first=self.add(s.engine,'Raise the Alarm',zone='hand',ref='duration-alarm-before')
        later=self.add(s.engine,'Raise the Alarm',zone='hand',ref='duration-alarm-after')
        bounce=self.add(s.engine,'Boomerang',seat='B',zone='hand')
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand')
        for card in (bear,root,chorus,first,later,bounce):self.assert_closed(s,card)
        s.state.players['B'].mana_pool.update({'U':2})
        self.prepare(s,mana={'C':8,'G':2,'W':2});self.checkpoint(s)
        row=next(row for row in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
            if row['id'].startswith('activate:') and root.ref in row['id'])
        activation={'action_id':row['id'],'pay':'auto'};before=authoritative_state_hash(s.state)
        self.assertFalse(s.act('pilot:D',activation).ok);self.assertEqual(before,authoritative_state_hash(s.state))
        result=s.act('pilot:A',activation);self.assertTrue(result.ok,result.summary)
        self.cast(s,'B',bounce,[island.ref]);self.assertIsNone(self.resolve(s))
        self.assertEqual('hand',s.state.cards[island.object_id].zone);self.assertEqual((4,4),self.stats(s,root))
        self.priority(s,'A')
        self.assertFalse(any(row['id']==activation['action_id'] for row in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']))
        self.cast(s,'A',chorus,[bear.ref]);self.cast(s,'A',first);self.assertIsNone(self.resolve_top(s))
        self.assertEqual((2,2),self.stats(s,bear))
        self.assertEqual(2,sum(card.zone=='battlefield' and card.is_token and card.controller=='A' for card in s.state.cards.values()))
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'duration-pending';s.save(path);resumed=type(s).load(self.db,path)
            self.assertEqual(authoritative_state_hash(s.state),authoritative_state_hash(resumed.state));s=resumed
        self.assertIsNone(self.resolve(s));self.assertEqual((6,6),self.stats(s,bear))
        self.assertIn('trample',{name.casefold() for name in s.engine._combat_keywords(s.state.cards[bear.object_id])})
        self.cast(s,'A',later);self.assertIsNone(self.resolve(s));self.assertEqual((6,6),self.stats(s,bear))
        self.assertEqual(4,sum(card.zone=='battlefield' and card.is_token and card.controller=='A' for card in s.state.cards.values()))
        self.attack(s,[(s.state.cards[bear.object_id],'B'),(s.state.cards[root.object_id],'C')])
        self.until(s,lambda game:game.state.pending_decision is not None and game.state.pending_decision.kind=='combat.blockers')
        result=s.act('pilot:B',{'action_id':'block','blocks':{blocker.ref:bear.ref}});self.assertTrue(result.ok,result.summary)
        self.until(s,lambda game:game.state.pending_decision is not None and game.state.pending_decision.kind=='combat.damage')
        assignments=[{'source':bear.ref,'target':blocker.ref,'amount':2},
            {'source':bear.ref,'target':'B','amount':4},{'source':root.ref,'target':'C','amount':4}]
        damage={'action_id':'assign_damage','assignments':assignments};before=authoritative_state_hash(s.state)
        self.assertFalse(s.act('pilot:D',damage).ok);self.assertEqual(before,authoritative_state_hash(s.state))
        result=s.act('pilot:A',damage);self.assertTrue(result.ok,result.summary)
        self.until(s,lambda game:game.state.phase=='postcombat_main')
        self.assertEqual([40,36,36,40],[s.state.players[seat].life for seat in 'ABCD'])
        self.assertEqual('graveyard',s.state.cards[blocker.object_id].zone)
        self.until(s,lambda game:game.state.active_player=='B' and game.state.phase=='precombat_main')
        self.assertEqual((2,2),self.stats(s,bear));self.assertEqual((2,2),self.stats(s,root))
        self.assertNotIn('trample',{name.casefold() for name in s.engine._combat_keywords(s.state.cards[bear.object_id])})
        for seat in 'ABD':self.assertNotIn(private.ref,json.dumps(s.packet('pilot:'+seat,full=True)))
        self.replay(s)
