"""Pinned delayed cleanup is distinct from creation, control and departure."""
import unittest
from quorune.oracle_ir import _reviewed_effect_template


class OrdinaryTokenCleanupCompilerTests(unittest.TestCase):
    def test_withdrawn_cleanup_owner_keeps_unmarked_token_creation_available(self):
        import json
        from dataclasses import replace
        from common import ROOT
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        from test_qualified_zone_event_queries import query_record
        from quorune.oracle_ir import compile_oracle_card
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text())
        next(c for c in raw['capabilities'] if c['id']=='token.creation.fixed_definition_cleanup').update(
            status='blocked',blockers=['Created-token cleanup owner withdrawn'])
        registry=CapabilityRegistry(raw)
        index=build_index(registry_value=raw,declaration_source=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json'),
            discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
        for text,expected in (('Create two 1/1 red Goblin creature tokens.',True),
                ('Create two 1/1 red Goblin creature tokens. Sacrifice them at the beginning of the next end step.',False)):
            record=replace(query_record(text),type_line='Sorcery',mana_cost='{1}{R}')
            self.assertEqual(expected,compile_oracle_card(record,capability_registry=registry).status=='exact')

    def test_complete_cleanup_clauses_retain_action_timing_and_token_definition(self):
        cases=(
            ('Create two 3/1 red Elemental creature tokens with haste. Exile them at the beginning of the next end step.','exile','next_end_step',2,'create_token'),
            ('Create a 5/5 blue Wall creature token with defender. Sacrifice it at the beginning of the next end step.','sacrifice','next_end_step',1,'create_token'),
            ('Create a 6/12 colorless Construct artifact creature token with trample. Exile it at the beginning of your next end step.','exile','controller_next_end_step',1,'create_token'),
            ("Create a 4/4 white Angel creature token with flying that's tapped and attacking. Exile that token at end of combat.",'exile','end_of_combat',1,'choose_fixed_attacking_token_destinations'),
            ("Create a 1/1 red Warrior creature token that's tapped and attacking. Sacrifice it at the beginning of the next end step.",'sacrifice','next_end_step',1,'choose_fixed_attacking_token_destinations'),
        )
        for text,action,timing,quantity,operation in cases:
            with self.subTest(text=text):
                template,effects,target,mechanics=_reviewed_effect_template(text,card_name='Temporary token fixture')
                self.assertIsNotNone(template)
                self.assertEqual(1,len(effects));self.assertIsNone(target)
                self.assertEqual(operation,effects[0]['op']);self.assertEqual(quantity,effects[0]['quantity'])
                self.assertEqual({'kind':'created_token_cleanup','schema_version':1,'action':action,'timing':timing},effects[0]['token_cleanup'])
                self.assertIn('ordinary-created-token-cleanup',mechanics)

    def test_cleanup_does_not_swallow_extra_instructions_conditions_or_postcreation_grants(self):
        for text in (
            'Create a 1/1 red Goblin creature token. Exile it at the beginning of the next end step. Sacrifice it at end of combat.',
            'Create a 1/1 red Goblin creature token. Exile it at the beginning of the next end step unless you control an artifact.',
            'Create a 1/1 red Goblin creature token. It gains haste until end of turn. Sacrifice it at the beginning of the next end step.',
            "Create a token that's a copy of target creature. Sacrifice it at end of combat.",
        ):
            with self.subTest(text=text):
                _,effects,_,_=_reviewed_effect_template(text,card_name='Temporary token fixture')
                self.assertFalse(any('token_cleanup' in effect for effect in effects))


class CreatedTokenCleanupModelTests(unittest.TestCase):
    def test_closed_cleanup_specs_distinguish_action_and_applicable_step(self):
        from quorune.created_token_cleanup_model import CreatedTokenCleanupSpec
        for action in ('sacrifice','exile'):
            for timing in ('next_end_step','controller_next_end_step','end_of_combat'):
                spec=CreatedTokenCleanupSpec(action,timing)
                self.assertEqual(spec,CreatedTokenCleanupSpec.from_dict(spec.to_dict()))
                expected=({'phase':'combat','step':'end_combat'} if timing=='end_of_combat'
                    else {'phase':'ending','step':'end_step',**({'player':'controller'} if timing=='controller_next_end_step' else {})})
                self.assertEqual(expected,spec.condition())
        valid=CreatedTokenCleanupSpec('sacrifice','next_end_step').to_dict()
        for changed in ({'schema_version':True},{'schema_version':0},{'schema_version':2},
                {'action':'destroy'},{'timing':'cleanup'},{'unknown':1},{'kind':'another_cleanup'}):
            with self.subTest(changed=changed),self.assertRaises(ValueError):
                CreatedTokenCleanupSpec.from_dict({**valid,**changed})

    def test_plan_and_intent_codecs_retain_source_and_do_not_reinterpret_legacy_identities(self):
        from dataclasses import replace
        from quorune.created_token_cleanup import CreatedTokenCleanupPlan
        from quorune.created_token_cleanup_model import CreatedTokenCleanupSpec
        from quorune.semantic_runtime.context import SemanticSourceContext
        from quorune.semantic_runtime.intents import CreateTokenIntent
        from quorune.semantic_choices.intent_replacement import semantic_intent_identity,validate_semantic_intent_identity
        source=SemanticSourceContext('S1','source','source@0','source-ref',instruction_pointer=3)
        plan=CreatedTokenCleanupPlan(CreatedTokenCleanupSpec('sacrifice','next_end_step'),source)
        self.assertEqual(plan,CreatedTokenCleanupPlan.from_dict(plan.to_dict()))
        legacy=CreateTokenIntent(actor='A',controller='A',name='Soldier',quantity=1,reason='codec witness')
        kind,identity=semantic_intent_identity(legacy)
        self.assertNotIn('token_cleanup',identity)
        self.assertEqual(identity,validate_semantic_intent_identity(kind,identity))
        marked=replace(legacy,token_cleanup=plan)
        kind,identity=semantic_intent_identity(marked)
        self.assertEqual(plan.to_dict(),identity['token_cleanup'])
        self.assertEqual(identity,validate_semantic_intent_identity(kind,identity))
        with self.assertRaises(ValueError):replace(marked,sacrifice_at_end_step=True)
        with self.assertRaises(ValueError):CreatedTokenCleanupPlan.from_dict(plan.spec.to_dict())
        for changed in ({'schema_version':True},{'unknown':1},{'source':{'stack_ref':'S2'}},
                {'source':{**plan.source.to_dict(),'stack_ref':True}}):
            with self.assertRaises(ValueError):CreatedTokenCleanupPlan.from_dict({**plan.to_dict(),**changed})

    def test_cleanup_batch_pins_created_incarnations_and_sacrifice_requires_original_controller(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from quorune.created_token_cleanup import schedule_created_token_cleanup
        from quorune.created_token_cleanup_model import CreatedTokenCleanupSpec
        card=SimpleNamespace(object_id='token-object',ref='T1',logical_object_id='token-object@0',zone_change_counter=0)
        for action in ('sacrifice','exile'):
            with patch('quorune.trigger_processing.schedule_delayed_trigger') as schedule:
                schedule_created_token_cleanup(object(),'A',[card],CreatedTokenCleanupSpec(action,'next_end_step'),label='cleanup witness')
            data=schedule.call_args.kwargs;movement=data['stack_template']['context']['dynamic_effects'][0]
            self.assertEqual('A',data['controller']);self.assertEqual('battlefield',movement['from'])
            self.assertEqual([{'card':'T1','expected_zone_change_counter':0,'expected_object_identity':'token-object@0'}],movement['cards'])
            self.assertEqual('graveyard' if action=='sacrifice' else 'exile',movement['destination'])
            if action=='sacrifice':
                self.assertEqual('A',movement['required_controller']);self.assertEqual('sacrifice',movement['transition_kind'])
            else:self.assertNotIn('required_controller',movement)
        with patch('quorune.trigger_processing.schedule_delayed_trigger') as schedule:
            schedule_created_token_cleanup(object(),'A',[],CreatedTokenCleanupSpec('exile','end_of_combat'),label='empty')
        schedule.assert_not_called()

    def test_empty_marked_creation_still_creates_its_unconditional_delayed_ability(self):
        from unittest.mock import patch
        from quorune.created_token_cleanup import schedule_created_token_cleanup
        from quorune.created_token_cleanup_model import CreatedTokenCleanupSpec
        from quorune.semantic_runtime.context import SemanticSourceContext
        with patch('quorune.trigger_processing.schedule_delayed_trigger') as schedule:
            schedule_created_token_cleanup(object(),'A',[],CreatedTokenCleanupSpec('exile','next_end_step'),
                label='empty marked cleanup',source=SemanticSourceContext('S1','source','source@0','source-ref'))
        self.assertEqual([],schedule.call_args.kwargs['stack_template']['context']['dynamic_effects'])

    def test_using_one_end_step_for_all_cleanup_timings_is_killed(self):
        from unittest.mock import patch
        from quorune.created_token_cleanup_model import CreatedTokenCleanupSpec
        case=type(self)('test_closed_cleanup_specs_distinguish_action_and_applicable_step')
        baseline=unittest.TestResult();case.run(baseline);self.assertTrue(baseline.wasSuccessful())
        with patch.object(CreatedTokenCleanupSpec,'condition',lambda self:{'phase':'ending','step':'end_step'}):
            mutant=unittest.TestResult();case.run(mutant)
        self.assertFalse(mutant.wasSuccessful());self.assertFalse(mutant.errors)

    def test_v1_attacking_tokens_and_legacy_lifecycle_flags_remain_separate(self):
        from types import SimpleNamespace
        from dataclasses import replace
        from quorune.created_token_cleanup_model import CreatedTokenCleanupSpec
        from quorune.semantic_choices.context import SemanticChoiceContext,SnapshotSemanticChoiceQuery
        from quorune.semantic_choices.fixed_attacking_tokens import FixedAttackingTokenDestinationHandler
        from quorune.semantic_choices.model import SemanticChoiceError
        from quorune.semantic_runtime.context import SemanticSourceContext
        from quorune.replacement.immutable import FrozenMap
        text="Create a 1/1 red Warrior creature token that's tapped and attacking. Sacrifice it at end of combat."
        effect=dict(_reviewed_effect_template(text,card_name='Temporary token fixture')[1][0]);effect['player']='A'
        source=SemanticSourceContext('S1','source','source@0','source-ref')
        query=SnapshotSemanticChoiceQuery(seat_order=tuple('ABCD'),active_order=tuple('ABCD'),
            resolution_source=source,attacking_token_entry_allowed=True,
            materialized_token_creation_preview=(FrozenMap({'quantity':1,'_attacking_group':'any'}),))
        context=SemanticChoiceContext(actor='A',stack_ref='S1',stack_controller='A',stack_label='cleanup',
            source_ref='source-ref',card_ref=None,semantic_program_id='test',semantic_program_version=1,query=query)
        handler=FixedAttackingTokenDestinationHandler();prepared=handler.prepare(effect,context)
        continuation=SimpleNamespace(effect=prepared.continuation_effect,stack_ref='S1')
        result=handler.complete(continuation,{'attacking':{'attacking:0':'B'}},query)
        self.assertEqual(CreatedTokenCleanupSpec('sacrifice','end_of_combat'),result.intents[0].token_cleanup.spec)
        self.assertFalse(result.intents[0].sacrifice_at_end_step);self.assertFalse(result.intents[0].exile_at_end_of_combat)
        with self.assertRaises(SemanticChoiceError):handler.prepare({**effect,'token_entry_version':1},context)
        with self.assertRaises(SemanticChoiceError):handler.complete(continuation,{'attacking':{'attacking:0':'B'}},replace(query,resolution_source=replace(source,stack_ref='S2')))



import json,tempfile
from pathlib import Path
from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.rules.capabilities import load_default_capability_registry
from quorune.record import authoritative_state_hash
from scripts.build_test_database import build_fixture_database


class OrdinaryTokenCleanupNativeTests(InheritedNativeFixture,unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'ordinary-token-cleanup.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/ordinary-token-cleanup-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Ordinary token cleanup',[DeckEntry('Isamaru, Hound of Konda',1,'commander'),
            DeckEntry('Generic Bound Plains',20)],['Isamaru, Hound of Konda'])

    def trim_hand(self,s,seat):
        for oid in list(s.state.players[seat].zones['hand']):s.engine.move_card(oid,'library',log=False)

    def mana_from(self,s,seat,lands):
        self.priority(s,seat)
        for land in lands:
            actions=s.packet('pilot:'+seat,full=True)['decision']['ctx']['legal']['actions']
            row=next(r for r in actions if r.get('mana_ability') and land.ref in r['id'])
            result=s.act('pilot:'+seat,{'action_id':row['id'],'pay':'auto'});self.assertTrue(result.ok,result.summary)

    def test_owner_creator_scoped_end_step_matches_only_original_delayed_controller(self):
        from quorune.created_token_cleanup import schedule_created_token_cleanup
        from quorune.created_token_cleanup_model import CreatedTokenCleanupSpec
        from quorune.trigger_processing import matching_delayed_triggers
        s=self.session(603700404)
        refs=s.engine.create_token('A',name='Timing owner token',characteristics={
            'type_line':'Token Creature — Wall','power':'5','toughness':'5','colors':['U']})
        token=next(c for c in s.state.cards.values() if c.ref==refs[0])
        delayed=schedule_created_token_cleanup(s.engine,'A',[token],
            CreatedTokenCleanupSpec('exile','controller_next_end_step'),label='Creator step owner')
        self.assertEqual([],matching_delayed_triggers(s.engine,'step.begin',{'phase':'ending','step':'end_step','player':'B'}))
        matches=matching_delayed_triggers(s.engine,'step.begin',{'phase':'ending','step':'end_step','player':'A'})
        self.assertEqual([delayed.ref],[item.ref for item in matches])
        self.assertEqual('battlefield',s.state.cards[token.object_id].zone)

    def test_owner_empty_marked_cleanup_enters_and_resolves_as_an_empty_trigger(self):
        from quorune.created_token_cleanup import schedule_created_token_cleanup
        from quorune.created_token_cleanup_model import CreatedTokenCleanupSpec
        from quorune.semantic_runtime.context import SemanticSourceContext
        s=self.session(603700405);self.trim_hand(s,'A')
        schedule_created_token_cleanup(s.engine,'A',[],CreatedTokenCleanupSpec('exile','next_end_step'),
            label='Empty marked token cleanup',source=SemanticSourceContext('virtual-resolved-source'))
        self.prepare(s);self.checkpoint(s)
        self.until(s,lambda game:game.state.phase=='ending' and game.state.step=='end_step')
        self.assertEqual(1,len([item for item in s.state.stack if item.label=='Empty marked token cleanup']))
        s=self.replay(s,load=True);self.settle(s)
        self.assertFalse(any(trigger.active for trigger in s.state.delayed_triggers))
        self.assertEqual([40,40,40,40],[s.state.players[seat].life for seat in 'ABCD'])
        self.replay(s,load=True)

    def test_actual_hungry_for_more_doubled_batch_sacrifices_after_lifelink_combat(self):
        s=self.session(603700401);self.trim_hand(s,'A')
        spell=self.add(s.engine,'Hungry for More',zone='hand')
        self.add(s.engine,'Parallel Lives');celebrant=self.add(s.engine,'Cruel Celebrant')
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand')
        for card in (spell,celebrant):self.assert_closed(s,card)
        self.prepare(s,mana={'B':1,'R':1});self.checkpoint(s)
        self.cast(s,'A',spell,[]);self.resolve_top(s)
        tokens=[c for c in s.state.cards.values() if c.zone=='battlefield' and c.is_token and c.printed_name=='Vampire']
        self.assertEqual(2,len(tokens));active=[t for t in s.state.delayed_triggers if t.active]
        self.assertEqual(1,len(active));self.assertEqual('A',active[0].controller)
        self.assertEqual(spell.object_id,active[0].source_object_id)
        self.attack(s,[(token,'B') for token in tokens])
        self.until(s,lambda game:game.state.phase=='postcombat_main')
        self.assertEqual(46,s.state.players['A'].life);self.assertEqual(34,s.state.players['B'].life)
        self.until(s,lambda game:game.state.phase=='ending' and game.state.step=='end_step')
        self.assertTrue(s.state.stack)
        before=authoritative_state_hash(s.state)
        self.assertFalse(s.act('pilot:D',{'action_id':'pass'}).ok)
        self.assertEqual(before,authoritative_state_hash(s.state))
        s=self.replay(s,load=True);self.settle(s)
        self.assertTrue(all(s.state.cards[token.object_id].zone=='outside' for token in tokens))
        self.assertEqual([48,32,38,38],[s.state.players[seat].life for seat in 'ABCD'])
        self.assertFalse(any(t.active for t in s.state.delayed_triggers))
        for seat in 'ABD':self.assertNotIn(private.ref,json.dumps(s.packet('pilot:'+seat,full=True)))
        self.replay(s,load=True)

    def test_actual_tidal_wave_cleanup_cannot_sacrifice_token_controlled_by_an_opponent(self):
        s=self.session(603700402);self.trim_hand(s,'A');self.trim_hand(s,'B')
        wave=self.add(s.engine,'Tidal Wave',zone='hand');theft=self.add(s.engine,'Act of Treason',seat='B',zone='hand')
        islands=[self.add(s.engine,'Island',ref='cleanup-island-'+str(i)) for i in range(3)]
        mountains=[self.add(s.engine,'Mountain',seat='B',ref='cleanup-mountain-'+str(i)) for i in range(3)]
        self.assert_closed(s,wave);self.assert_closed(s,theft);self.prepare(s);self.checkpoint(s)
        self.until(s,lambda game:game.state.active_player=='B' and game.state.phase=='precombat_main')
        self.mana_from(s,'A',islands);self.cast(s,'A',wave,[]);self.resolve_top(s)
        token=next(c for c in s.state.cards.values() if c.zone=='battlefield' and c.is_token and c.printed_name=='Wall')
        self.mana_from(s,'B',mountains);self.cast(s,'B',theft,[token.ref]);self.resolve_top(s)
        self.assertEqual('B',s.state.cards[token.object_id].controller)
        self.until(s,lambda game:game.state.phase=='ending' and game.state.step=='end_step')
        cleanup=next(item for item in s.state.stack if item.label=='Token cleanup')
        self.assertEqual('A',cleanup.controller);self.assertEqual(wave.object_id,cleanup.source_object_id)
        self.assertEqual('B',s.state.active_player)
        s=self.replay(s,load=True);self.settle(s)
        self.assertEqual('battlefield',s.state.cards[token.object_id].zone)
        self.assertEqual('B',s.state.cards[token.object_id].controller)
        self.assertFalse(any(t.active for t in s.state.delayed_triggers))
        self.until(s,lambda game:game.state.active_player=='C' and game.state.step=='upkeep')
        self.assertEqual('A',s.state.cards[token.object_id].controller)
        self.assertEqual('battlefield',s.state.cards[token.object_id].zone)
        self.replay(s,load=True)

    def test_actual_geist_attacking_angel_exiles_at_combat_end_and_keeps_original_source(self):
        s=self.session(603700403);self.trim_hand(s,'A')
        geist=self.add(s.engine,'Geist of Saint Traft');self.assert_closed(s,geist)
        self.prepare(s);self.checkpoint(s);self.attack(s,[(geist,'B')])
        self.until(s,lambda game:game.state.pending_decision is not None and game.state.pending_decision.kind=='semantic.choice')
        refs=s.state.pending_decision.payload_by_actor['A']['legal_actions'][0]['choice_schema']['legal_refs']
        self.assertEqual(1,len(refs));s=self.replay(s,load=True)
        command={'action_id':'choose','attacking':{refs[0]:'C'}};before=authoritative_state_hash(s.state)
        self.assertFalse(s.act('pilot:D',command).ok);self.assertEqual(before,authoritative_state_hash(s.state))
        result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary);self.settle(s)
        angel=next(c for c in s.state.cards.values() if c.zone=='battlefield' and c.is_token and c.printed_name=='Angel')
        self.assertTrue(angel.tapped);self.assertEqual('C',angel.attacking)
        cleanup=next(t for t in s.state.delayed_triggers if t.active)
        self.assertEqual(geist.object_id,cleanup.source_object_id)
        self.assertEqual(s.state.cards[geist.object_id].logical_object_id,cleanup.source_logical_object_id)
        self.until(s,lambda game:game.state.phase=='postcombat_main')
        self.assertEqual([40,38,36,40],[s.state.players[seat].life for seat in 'ABCD'])
        self.assertEqual('outside',s.state.cards[angel.object_id].zone)
        self.assertEqual('battlefield',s.state.cards[geist.object_id].zone)
        self.assertFalse(any(t.active for t in s.state.delayed_triggers))
        self.replay(s,load=True)



class OrdinaryTokenCleanupCopyNativeTests(InheritedNativeFixture,unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'ordinary-token-cleanup-copy.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/ordinary-token-cleanup-cards.json',ROOT/'tests/fixtures/fixed-stack-spell-copy-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Copied token cleanup',[DeckEntry('Isamaru, Hound of Konda',1,'commander'),
            DeckEntry('Generic Bound Plains',20)],['Isamaru, Hound of Konda'])

    def test_actual_reverberate_copy_and_original_create_independent_source_correct_cleanup(self):
        s=self.session(603700406)
        for oid in list(s.state.players['A'].zones['hand']):s.engine.move_card(oid,'library',log=False)
        spell=self.add(s.engine,'Hungry for More',zone='hand');copy=self.add(s.engine,'Reverberate',zone='hand')
        self.assert_closed(s,spell);self.assert_closed(s,copy)
        self.prepare(s,mana={'B':1,'R':3});self.checkpoint(s)
        self.cast(s,'A',spell,[])
        original=next(item for item in s.state.stack if item.card_object_id==spell.object_id)
        self.cast(s,'A',copy,[original.ref]);self.resolve_top(s)
        copies=[item for item in s.state.stack if item.kind=='spell_copy'];self.assertEqual(1,len(copies))
        copied_source_id=copies[0].source_object_id or copies[0].card_object_id
        self.assertIsNotNone(copied_source_id);self.assertNotEqual(spell.object_id,copied_source_id)
        copied_source_identity=s.state.cards[copied_source_id].logical_object_id
        self.resolve_top(s)
        cleanup=[item for item in s.state.delayed_triggers if item.active];self.assertEqual(1,len(cleanup))
        self.assertEqual(copied_source_id,cleanup[0].source_object_id)
        self.assertEqual(copied_source_identity,cleanup[0].source_logical_object_id)
        self.resolve_top(s)
        cleanup=[item for item in s.state.delayed_triggers if item.active];self.assertEqual(2,len(cleanup))
        self.assertEqual({copied_source_id,spell.object_id},{item.source_object_id for item in cleanup})
        tokens=[card for card in s.state.cards.values() if card.zone=='battlefield' and card.is_token and card.printed_name=='Vampire']
        self.assertEqual(2,len(tokens))
        self.until(s,lambda game:game.state.phase=='ending' and game.state.step=='end_step')
        s=self.replay(s,load=True);self.settle(s)
        self.assertTrue(all(s.state.cards[token.object_id].zone=='outside' for token in tokens))
        self.assertFalse(any(trigger.active for trigger in s.state.delayed_triggers))
        self.replay(s,load=True)


if __name__=='__main__':unittest.main()
