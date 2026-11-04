"""Already-owned scalar result quantities remain scoped before delayed cleanup."""
import unittest
from quorune.oracle_ir import _reviewed_effect_template


class QuantityTokenCleanupCompilerTests(unittest.TestCase):
    def test_materialized_integer_token_stats_keep_strict_definition_validation(self):
        from unittest.mock import patch
        from quorune.created_token_cleanup import apply_fixed_token_cleanup_creation
        effect={'op':'create_token','controller':'A','name':'Phyrexian Horror','quantity':1,
            'characteristics':{'type_line':'Token Creature — Phyrexian Horror','power':2,'toughness':'1','colors':['R'],
                'keywords':['Trample','Haste']},
            'token_cleanup':{'kind':'created_token_cleanup','schema_version':1,'action':'sacrifice','timing':'next_end_step'},
            '_runtime_source':{'stack_ref':'S1','object_id':None,'logical_object_id':None,'card_ref':None}}
        with patch('quorune.token_creation.create_tokens',return_value=[]) as create:
            self.assertEqual([],apply_fixed_token_cleanup_creation(object(),effect,actor='A',reason='typed stats'))
            self.assertEqual(2,create.call_args.kwargs['characteristics']['power'])
        for invalid in (True,2.0,{},None):
            with self.subTest(invalid=invalid),self.assertRaises((ValueError,TypeError)):
                apply_fixed_token_cleanup_creation(object(),{**effect,'characteristics':{
                    **effect['characteristics'],'power':invalid}},actor='A',reason='malformed stats')

    def test_component_audit_preserves_validated_cleanup_and_attacking_owners(self):
        from quorune.rules.fixed_effect_clause_shapes import closed_effect_component_capabilities
        for text,expected in (
                ('Create a 1/1 red Goblin creature token. Sacrifice it at the beginning of the next end step.',
                    {'token.creation.fixed_definition','token.creation.fixed_definition_cleanup'}),
                ("Create a 1/1 red Goblin creature token that's tapped and attacking.",
                    {'token.creation.fixed_definition','token.creation.fixed_attacking_definition'}),
                ("Create a 1/1 red Goblin creature token that's tapped and attacking. Exile it at end of combat.",
                    {'token.creation.fixed_definition','token.creation.fixed_attacking_definition','token.creation.fixed_definition_cleanup'})):
            with self.subTest(text=text):
                compiled=_reviewed_effect_template(text,card_name='Quantity cleanup fixture')
                required=closed_effect_component_capabilities(compiled[1],target_schema=None,mechanics=set(compiled[3])|{'closed-effect-program'})
                self.assertTrue(expected.issubset(set(required)),required)
                effect=dict(compiled[1][0])
                if 'token_cleanup' in effect:
                    effect['token_cleanup']={**effect['token_cleanup'],'schema_version':True}
                    self.assertEqual((),closed_effect_component_capabilities((effect,),target_schema=None,mechanics=set(compiled[3])))
    def test_quantity_wrapper_does_not_project_new_queries_targets_or_duplicate_cleanup(self):
        for text in (
            'Create X 1/1 red Elemental creature tokens, where X is the number of secret plans you own. Exile them at the beginning of the next end step.',
            "Create X tokens that are copies of target creature, where X is this creature's power. Exile them at the beginning of the next end step.",
            "Create X 1/1 red Elemental creature tokens, where X is this creature's power. Exile them at the beginning of the next end step. Sacrifice them at end of combat.",
        ):
            with self.subTest(text=text):
                template,_,_,_=_reviewed_effect_template(text,card_name='Quantity cleanup fixture')
                self.assertIsNone(template)

    def test_collapsing_quantity_before_cleanup_is_killed(self):
        from unittest.mock import patch
        from quorune.compiler import created_token_cleanup_templates as owner
        case=type(self)('test_source_power_and_counter_result_definitions_before_cleanup_remain_typed')
        baseline=unittest.TestResult();case.run(baseline);self.assertTrue(baseline.wasSuccessful())
        with patch.object(owner,'scalar_created_token_cleanup_template',return_value=None):
            mutant=unittest.TestResult();case.run(mutant)
        self.assertFalse(mutant.wasSuccessful());self.assertFalse(mutant.errors)

    def test_source_power_and_counter_result_definitions_before_cleanup_remain_typed(self):
        from quorune.scalar_effect_amount_model import scalar_amount_specs
        for text in (
            "Create X 1/1 red Elemental creature tokens with haste, where X is this creature's power. Exile them at the beginning of the next end step.",
            'Create an X/1 red Phyrexian Horror creature token with trample and haste, where X is the number of oil counters on this artifact. Sacrifice that token at the beginning of the next end step.',
        ):
            with self.subTest(text=text):
                template,effects,target,mechanics=_reviewed_effect_template(text,card_name='Quantity cleanup fixture')
                self.assertIsNotNone(template)
                self.assertIsNone(target);self.assertEqual(1,len(effects))
                self.assertTrue(scalar_amount_specs(effects))
                self.assertEqual('create_token',effects[0]['op'])
                self.assertEqual('created_token_cleanup',effects[0]['token_cleanup']['kind'])


import json,tempfile
from pathlib import Path
from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.rules.capabilities import load_default_capability_registry
from quorune.record import authoritative_state_hash
from scripts.build_test_database import build_fixture_database


class QuantityTokenCleanupNativeTests(InheritedNativeFixture,unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'quantity-token-cleanup.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/quantity-token-cleanup-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Quantity token cleanup',[DeckEntry('Isamaru, Hound of Konda',1,'commander'),
            DeckEntry('Generic Bound Plains',20)],['Isamaru, Hound of Konda'])

    def trim_hand(self,s):
        for oid in list(s.state.players['A'].zones['hand']):s.engine.move_card(oid,'library',log=False)

    def load_pending(self,s):
        # Save/load and final replay prove different contracts. Avoid replaying
        # the entire prefix here and then replaying it again at journey end.
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'quantity-cleanup-pending';s.save(path)
            resumed=type(s).load(self.db,path)
            self.assertEqual(authoritative_state_hash(s.state),authoritative_state_hash(resumed.state))
            return resumed

    def test_actual_optional_payment_accept_and_decline_gate_attacking_creation_and_cleanup(self):
        for seed,pay in ((608200104,False),(608200105,True)):
            with self.subTest(pay=pay):
                s=self.session(seed);self.trim_hand(s)
                source=self.add(s.engine,'Kavaron Harrier');fog=self.add(s.engine,'Fog',zone='hand')
                lands=[self.add(s.engine,'Forest',ref='payment-forest-'+str(i)) for i in range(3)]
                self.assert_closed(s,source);self.assert_closed(s,fog)
                self.prepare(s);self.checkpoint(s);self.attack(s,[(source,'B')]);self.priority(s,'A')
                for land in lands:
                    actions=s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
                    row=next(row for row in actions if row.get('mana_ability') and land.ref in row['id'])
                    self.assertTrue(s.act('pilot:A',{'action_id':row['id'],'pay':'auto'}).ok)
                decision=self.resolve(s);self.assertEqual('semantic.choice',decision.kind)
                command={'action_id':'choose','pay':pay};before=authoritative_state_hash(s.state)
                self.assertFalse(s.act('pilot:D',command).ok);self.assertEqual(before,authoritative_state_hash(s.state))
                result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
                if pay:
                    decision=self.resolve(s);self.assertEqual('semantic.choice',decision.kind)
                    refs=decision.payload_by_actor['A']['legal_actions'][0]['choice_schema']['legal_refs']
                    self.assertEqual(1,len(refs));s=self.load_pending(s)
                    result=s.act('pilot:A',{'action_id':'choose','attacking':{refs[0]:'C'}})
                    self.assertTrue(result.ok,result.summary)
                self.assertIsNone(self.resolve(s))
                tokens=[card for card in s.state.cards.values() if card.zone=='battlefield' and card.is_token and card.printed_name=='Robot']
                self.assertEqual(1 if pay else 0,len(tokens))
                self.assertEqual(1 if pay else 0,len([item for item in s.state.delayed_triggers if item.active]))
                self.cast(s,'A',fog,[]);self.resolve_top(s)
                self.until(s,lambda game:game.state.phase=='postcombat_main')
                self.assertTrue(all(s.state.cards[card.object_id].zone=='outside' for card in tokens))
                self.assertEqual([40,40,40,40],[s.state.players[seat].life for seat in 'ABCD'])
                self.replay(s)

    def test_actual_rosette_later_grant_includes_the_new_untapped_attacking_token(self):
        s=self.session(608200106);self.trim_hand(s)
        equipment=self.add(s.engine,'Inquisitorial Rosette');grantee=self.add(s.engine,'Grizzly Bears')
        self.assert_closed(s,equipment);self.prepare(s,mana={'C':3});self.checkpoint(s)
        actions=s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        row=next(row for row in actions if equipment.ref in row['id'] and not row.get('mana_ability'))
        result=s.act('pilot:A',{'action_id':row['id'],'targets':[grantee.ref],'pay':'auto'})
        self.assertTrue(result.ok,result.summary);self.resolve_top(s)
        self.attack(s,[(grantee,'B')])
        decision=self.resolve(s);self.assertEqual('semantic.choice',decision.kind)
        refs=decision.payload_by_actor['A']['legal_actions'][0]['choice_schema']['legal_refs'];self.assertEqual(1,len(refs))
        result=s.act('pilot:A',{'action_id':'choose','attacking':{refs[0]:'C'}})
        self.assertTrue(result.ok,result.summary);self.settle(s)
        token=next(card for card in s.state.cards.values() if card.zone=='battlefield' and card.is_token and card.printed_name=='Astartes Warrior')
        self.assertFalse(token.tapped);self.assertEqual('C',token.attacking)
        for card in (s.state.cards[grantee.object_id],token):
            self.assertIn('menace',{keyword.casefold() for keyword in s.engine._combat_keywords(card)})
        self.assertFalse(any(item.active for item in s.state.delayed_triggers))
        self.until(s,lambda game:game.state.phase=='postcombat_main')
        self.assertEqual([40,38,38,40],[s.state.players[seat].life for seat in 'ABCD'])
        self.replay(s)

    def test_actual_forge_counter_replacement_precedes_stats_and_distinct_token_replacement(self):
        s=self.session(608200101);self.trim_hand(s)
        forge=self.add(s.engine,"Urabrask's Forge");season=self.add(s.engine,'Doubling Season')
        self.assert_closed(s,forge)
        # This existing counter-replacement control has reviewed compatibility
        # provenance; it is not a strict whole-card support claim.
        from quorune.card_programs import bind_card_program_runtime
        from quorune.card_programs.adapters import compile_best_available_card_program
        program=compile_best_available_card_program(self.db,self.db.by_oracle_id(season.oracle_id),
            semantic_registry=s.engine.semantics,capability_registry=self.registry,capability_profile='commander_review')
        binding=bind_card_program_runtime(program,capability_registry=self.registry,profile='commander_review')
        self.assertTrue(binding['compatible_ready'] or binding['strict_capability_ready'],binding)
        self.prepare(s);self.checkpoint(s)
        self.until(s,lambda game:game.state.phase=='combat' and game.state.step=='beginning_combat')
        self.assertIsNone(self.resolve(s))
        tokens=[card for card in s.state.cards.values() if card.zone=='battlefield' and card.is_token and card.printed_name=='Phyrexian Horror']
        self.assertEqual(2,s.state.cards[forge.object_id].counters['oil'])
        self.assertEqual(2,len(tokens))
        for card in tokens:
            data=s.engine._effective_card_data(card);self.assertEqual(('2','1'),(str(data['power']),str(data['toughness'])))
        self.assertEqual(1,len([item for item in s.state.delayed_triggers if item.active]))
        self.attack(s,[(card,'B') for card in tokens])
        self.until(s,lambda game:game.state.phase=='postcombat_main')
        self.assertEqual(36,s.state.players['B'].life)
        self.until(s,lambda game:game.state.phase=='ending' and game.state.step=='end_step')
        s=self.replay(s,load=True);self.settle(s)
        self.assertTrue(all(s.state.cards[card.object_id].zone=='outside' for card in tokens))
        self.assertEqual('battlefield',s.state.cards[forge.object_id].zone)
        self.replay(s,load=True)

    def test_actual_elemental_mastery_uses_grantee_power_after_response_and_zero_for_negative_count(self):
        for seed,response_name,land_name,amount in ((608200102,'Giant Growth','Forest',5),
                (608200103,'Hydrosurge','Island',0)):
            with self.subTest(response=response_name):
                s=self.session(seed);self.trim_hand(s)
                aura=self.add(s.engine,'Elemental Mastery',zone='hand');grantee=self.add(s.engine,'Grizzly Bears')
                response=self.add(s.engine,response_name,zone='hand');land=self.add(s.engine,land_name)
                private=self.add(s.engine,'Counterspell',seat='C',zone='hand')
                for card in (aura,grantee,response):self.assert_closed(s,card)
                self.prepare(s,mana={'R':1,'C':3});self.checkpoint(s)
                self.cast(s,'A',aura,[grantee.ref]);self.resolve_top(s)
                self.priority(s,'A');actions=s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
                row=next(row for row in actions if grantee.ref in row['id'] and not row.get('mana_ability'))
                result=s.act('pilot:A',{'action_id':row['id']});self.assertTrue(result.ok,result.summary)
                self.assertTrue(s.state.cards[grantee.object_id].tapped)
                self.priority(s,'A');actions=s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
                mana=next(row for row in actions if row.get('mana_ability') and land.ref in row['id'])
                self.assertTrue(s.act('pilot:A',{'action_id':mana['id'],'pay':'auto'}).ok)
                self.cast(s,'A',response,[grantee.ref]);self.resolve_top(s)
                self.assertIsNone(self.resolve(s))
                tokens=[card for card in s.state.cards.values() if card.zone=='battlefield' and card.is_token and card.printed_name=='Elemental']
                self.assertEqual(amount,len(tokens))
                self.assertEqual(1,len([item for item in s.state.delayed_triggers if item.active]))
                cleanup=next(item for item in s.state.delayed_triggers if item.active)
                self.assertEqual(grantee.object_id,cleanup.source_object_id)
                for seat in 'ABD':self.assertNotIn(private.ref,json.dumps(s.packet('pilot:'+seat,full=True)))
                self.until(s,lambda game:game.state.phase=='ending' and game.state.step=='end_step')
                s=self.load_pending(s);self.settle(s)
                self.assertTrue(all(s.state.cards[card.object_id].zone=='outside' for card in tokens))
                self.assertEqual('battlefield',s.state.cards[grantee.object_id].zone)
                self.replay(s)


if __name__=='__main__':unittest.main()
