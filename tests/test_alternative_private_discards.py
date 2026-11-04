from __future__ import annotations

"""CR 118.12/609.3/701.9: optional quality discard or mandatory fallback."""
import unittest
from dataclasses import replace

from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from test_declared_effect_amounts import generic_spell


class AlternativeDiscardCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = load_default_capability_registry()

    def test_complete_controller_target_and_opponent_alternatives_lower_typed_predicates(self):
        for text in (
            'Draw three cards. Then discard two cards unless you discard an artifact card.',
            'Draw three cards. Then discard two cards unless you discard a basic land card.',
            'Target player discards two cards unless they discard an artifact card.',
            'Target player draws three cards. Then that player discards two cards unless they discard a land card.',
            'Each opponent discards two cards unless they discard a nonland card.',
            'Draw two cards. Then discard two cards unless you discard an instant or sorcery card or a creature card with flying.',
        ):
            with self.subTest(text=text):
                ir = compile_oracle_card(generic_spell(text,mana_cost='{2}{U}'),capability_registry=self.registry)
                self.assertEqual('exact',ir.status,ir.material_residuals)
                effects = ir.faces[0].nodes[0].effects
                self.assertEqual('choose_cards_apnap',effects[-1]['op'])
                self.assertEqual('alternative_discard_count',effects[-1]['count']['kind'])
                self.assertEqual(2,effects[-1]['count']['fallback_count'])

    def test_unknown_qualities_and_ambiguous_subjects_remain_residual(self):
        for text in (
            'Discard two cards unless you discard a secretly chosen card.',
            'Discard two cards unless you discard a card with an unrepresented quality.',
            'Discard two cards unless you discard an artifact card. Rewrite history.',
            'Target player draws three cards. Then they discard two cards unless you discard a land card.',
        ):
            with self.subTest(text=text):
                self.assertNotEqual('exact',compile_oracle_card(generic_spell(text),capability_registry=self.registry).status)

    def test_verified_alternative_and_discard_withdrawals_preserve_fixed_draw(self):
        import json
        from common import ROOT
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        original=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text())
        declarations=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        known=set(discover_tests(ROOT).values())
        for owner in ('choice.affected_player.alternative_discard','choice.affected_player.fixed_discard'):
            raw=json.loads(json.dumps(original))
            next(c for c in raw['capabilities'] if c['id']==owner).update(status='blocked',blockers=['Alternative discard authority withdrawn'])
            registry=CapabilityRegistry(raw)
            index=build_index(registry_value=raw,declaration_source=declarations,discovered_test_ids=known)
            registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
            self.assertNotEqual('exact',compile_oracle_card(generic_spell('Target player discards two cards unless they discard an artifact card.'),capability_registry=registry).status)
            self.assertEqual('exact',compile_oracle_card(generic_spell('Draw two cards.'),capability_registry=registry).status)


class AlternativeDiscardModelTests(unittest.TestCase):
    def spec(self):
        from quorune.alternative_discard_model import AlternativeDiscardCountSpec
        from quorune.object_predicate import ObjectQuerySpec
        return AlternativeDiscardCountSpec((ObjectQuerySpec(zones=('hand',),types_any=('artifact',),token=False),))

    def test_codec_rejects_malformed_counts_predicates_and_fields(self):
        from quorune.alternative_discard_model import AlternativeDiscardCountSpec
        old=self.spec().to_dict()
        self.assertEqual(old,AlternativeDiscardCountSpec.from_dict(old).to_dict())
        for changes in ({'schema_version':True},{'schema_version':2},{'fallback_count':True},
                {'fallback_count':0},{'fallback_count':4},{'extra':1},{'qualifying_queries':[]}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):
                AlternativeDiscardCountSpec.from_dict({**old,**changes})
        for query in (replace(self.spec().qualifying_queries[0],zones=('battlefield',)),
                replace(self.spec().qualifying_queries[0],owner='B'),
                replace(self.spec().qualifying_queries[0],known_to_actor=True)):
            with self.assertRaises(ValueError):AlternativeDiscardCountSpec((query,))

    def test_all_small_hands_offer_and_accept_exactly_the_independent_legal_subsets(self):
        from itertools import combinations
        from types import SimpleNamespace
        from quorune.selection.alternative_discard import alternative_discard_options,validate_alternative_discard_choice
        from quorune.errors import GameRuleError
        for size in range(5):
            hand=tuple('C'+str(i) for i in range(size))
            for mask in range(1<<size):
                matching={hand[i] for i in range(size) if mask & (1<<i)}
                host=SimpleNamespace(_choice_options=lambda seat,e:sorted(matching))
                effect={'count':self.spec().to_dict()}
                rows=alternative_discard_options(host,effect,'B',hand)
                offered=set()
                for row in rows:
                    q=row['choice_schema']['cards']
                    offered.update(frozenset(c) for c in combinations(q['legal_refs'],q['minimum']))
                expected={frozenset(c) for c in combinations(hand,min(2,size))}|{frozenset((r,)) for r in matching}
                self.assertEqual(expected,offered)
                for count in range(size+1):
                    for refs in combinations(hand,count):
                        if frozenset(refs) in expected:
                            self.assertTrue(validate_alternative_discard_choice(host,effect,'B',hand,refs))
                        else:
                            with self.assertRaises(GameRuleError):validate_alternative_discard_choice(host,effect,'B',hand,refs)

    def test_branch_forms_keep_private_refs_and_authorize_the_same_choice_fields(self):
        from quorune.choice_forms import build_action_form,delegated_choice_fields
        from quorune.selection.alternative_discard import alternative_discard_options
        from types import SimpleNamespace
        host=SimpleNamespace(_choice_options=lambda seat,e:['B-artifact'])
        rows=alternative_discard_options(host,{'count':self.spec().to_dict()},'B',('B-artifact','B-land','B-spell'))
        form=build_action_form({'id':'choose','action':'choose'},decision_kind='choice.apnap',
            context={'discard_options':rows,'options':['B-artifact','B-land','B-spell'],'count':2})
        self.assertEqual('discard_mode',form['variants']['field'])
        self.assertEqual(['normal','matching'],[r['value'] for r in form['variants']['options']])
        self.assertIn('discard_mode',delegated_choice_fields({'id':'choose','action':'choose'},
            decision_kind='choice.apnap',context={'discard_options':rows,'options':['B-artifact','B-land','B-spell'],'count':2}))

    def test_omitting_the_qualified_subset_guard_is_killed(self):
        from unittest.mock import patch
        import quorune.selection.alternative_discard as owner
        original=owner.alternative_discard_options
        def omitted(host,effect,seat,options):
            rows=original(host,effect,seat,options)
            if len(rows)>1:rows[1]['choice_schema']['cards']['legal_refs']=list(options)
            return rows
        with patch.object(owner,'alternative_discard_options',side_effect=omitted):
            result=unittest.TestResult()
            AlternativeDiscardModelTests('test_all_small_hands_offer_and_accept_exactly_the_independent_legal_subsets').run(result)
        self.assertFalse(result.errors,result.errors)
        self.assertTrue(result.failures)

    def test_payload_owned_minimum_rejects_changed_domain_without_annotations(self):
        from quorune.alternative_discard_model import alternative_discard_runtime_dependencies,ALTERNATIVE_DISCARD_CAPABILITY
        effect={'op':'choose_cards_apnap','zone':'hand','then':'discard','hidden':True,'count':self.spec().to_dict()}
        self.assertEqual((ALTERNATIVE_DISCARD_CAPABILITY,),alternative_discard_runtime_dependencies(effect))
        for changes in ({'zone':'battlefield'},{'then':'sacrifice'},{'hidden':False}):
            with self.assertRaises(ValueError):alternative_discard_runtime_dependencies({**effect,**changes})

    def test_actual_browser_validator_and_serializer_accept_both_private_branches(self):
        import json,shutil,subprocess
        from common import ROOT
        from types import SimpleNamespace
        from quorune.choice_forms import build_action_form
        from quorune.selection.alternative_discard import alternative_discard_options
        node=shutil.which('node')
        self.assertIsNotNone(node)
        rows=alternative_discard_options(SimpleNamespace(_choice_options=lambda seat,e:['artifact']),
            {'count':self.spec().to_dict()},'B',('artifact','land','spell'))
        form=build_action_form({'id':'choose','action':'choose'},decision_kind='choice.apnap',
            context={'discard_options':rows,'options':['artifact','land','spell'],'count':2})
        cases=[{'discard_mode':'matching','cards':['artifact']},
            {'discard_mode':'normal','cards':['land','spell']},
            {'discard_mode':'matching','cards':['land']},
            {'discard_mode':'normal','cards':['artifact']}]
        script="""import {validateChoices,executableChoices} from './web/src/choices.ts';
let input='';for await(const chunk of process.stdin)input+=chunk;
const {form,cases}=JSON.parse(input);
process.stdout.write(JSON.stringify(cases.map(values=>({errors:validateChoices(form,values),
values:executableChoices(form,values)}))));"""
        result=subprocess.run([node,'--experimental-strip-types','--input-type=module','-e',script],
            input=json.dumps({'form':form,'cases':cases}),text=True,capture_output=True,cwd=ROOT,check=True,timeout=30)
        answers=json.loads(result.stdout)
        for i in (0,1):
            self.assertEqual([],answers[i]['errors'])
            self.assertEqual(cases[i],answers[i]['values'])
        self.assertTrue(answers[2]['errors'])
        self.assertTrue(answers[3]['errors'])

    def test_old_counts_and_private_forms_keep_their_validation_and_encoding(self):
        from quorune.selection.fractional_counts import validate_apnap_count,apnap_required_count
        from quorune.errors import GameRuleError
        from quorune.choice_forms import build_action_form
        for count in (True,False,0,-1,'2',{'kind':'unknown'}):
            with self.assertRaises(GameRuleError):validate_apnap_count({'count':count})
        validate_apnap_count({'count':2})
        self.assertEqual(1,apnap_required_count({'effect':{'count':2}},'B',['one']))
        form=build_action_form({'id':'choose','action':'choose'},decision_kind='choice.apnap',
            context={'count':2,'options':['one','two','three']})
        self.assertNotIn('variants',form)
        self.assertEqual(('cards',2,2),tuple(form['fields'][0][key] for key in ('name','minimum','maximum')))

    def test_unknown_branch_values_and_wrong_cardinalities_reject_before_selection(self):
        from types import SimpleNamespace
        from quorune.selection.alternative_discard import validate_alternative_discard_choice
        from quorune.errors import GameRuleError
        host=SimpleNamespace(_choice_options=lambda seat,e:['good'])
        effect={'count':self.spec().to_dict()}
        for mode,refs in ((True,['good']),('unknown',['good']),('matching',['bad']),
                ('normal',['good']),('matching',['good','bad'])):
            with self.subTest(mode=mode,refs=refs),self.assertRaises(GameRuleError):
                validate_alternative_discard_choice(host,effect,'B',['good','bad'],refs,mode)

    def test_malformed_saved_counts_raise_the_authoritative_choice_error(self):
        from types import SimpleNamespace
        from quorune.selection.fractional_counts import validate_apnap_count,apnap_required_count
        from quorune.selection.alternative_discard import alternative_discard_options
        from quorune.errors import GameRuleError
        value={**self.spec().to_dict(),'schema_version':True}
        effect={'op':'choose_cards_apnap','count':value,'zone':'hand','then':'discard','hidden':True}
        for invoke in (lambda:validate_apnap_count(effect),
                lambda:apnap_required_count({'effect':effect},'B',['one']),
                lambda:alternative_discard_options(SimpleNamespace(),effect,'B',['one'])):
            with self.assertRaises(GameRuleError):invoke()


import json
from pathlib import Path
import tempfile
from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.record import authoritative_state_hash
from scripts.build_test_database import build_fixture_database


class AlternativeDiscardNativeTests(InheritedNativeFixture,unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory()
        path=Path(cls.temporary.name)/'alternative-discards.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/alternative-private-discard-cards.json'],path)
        cls.db=CardDatabase(path)
        cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Alternative discards',[DeckEntry('Isamaru, Hound of Konda',1,'commander'),
            DeckEntry('Generic Bound Plains',14)],['Isamaru, Hound of Konda'])

    def reject(self,s,seat,command):
        before=authoritative_state_hash(s.state)
        result=s.act('pilot:'+seat,command)
        self.assertFalse(result.ok,result.summary)
        self.assertEqual(before,authoritative_state_hash(s.state))

    def choose(self,s,seat,refs,mode):
        self.assertEqual('choice.apnap',s.state.pending_decision.kind)
        self.assertEqual([seat],s.state.pending_decision.actors)
        command={'action_id':'choose','cards':list(refs),'discard_mode':mode}
        self.reject(s,'D' if seat!='D' else 'C',command)
        result=s.act('pilot:'+seat,command)
        self.assertTrue(result.ok,result.summary)

    def test_actual_thirst_offers_matching_and_fallback_after_draw_and_replays_privately(self):
        for fallback in (False,True):
            with self.subTest(fallback=fallback):
                s=self.session(118609910+int(fallback))
                spell=self.add(s.engine,'Thirst for Discovery',zone='hand')
                basic=self.add(s.engine,'Plains',zone='hand',ref='initial-basic')
                nonbasic=self.add(s.engine,'Evolving Wilds',zone='hand')
                body=self.add(s.engine,'Memnite',zone='hand')
                self.assert_closed(s,spell)
                self.prepare(s,mana={'U':1,'C':2})
                self.checkpoint(s)
                initial=set(s.state.players['A'].zones['hand'])
                self.cast(s,'A',spell,[])
                decision=self.resolve(s)
                self.assertEqual('choice.apnap',decision.kind)
                drawn=set(s.state.players['A'].zones['hand'])-initial
                self.assertEqual(3,len(drawn))
                ctx=s.packet('pilot:A',full=True)['decision']['ctx']
                self.assertEqual(['normal','matching'],[row['id'] for row in ctx['discard_options']])
                self.reject(s,'A',{'action_id':'choose','cards':[nonbasic.ref],'discard_mode':'matching'})
                self.reject(s,'A',{'action_id':'choose','cards':[body.ref,body.ref],'discard_mode':'normal'})
                for viewer in 'BCD':self.assertNotIn(nonbasic.ref,json.dumps(s.packet('pilot:'+viewer,full=True)))
                s=self.replay(s,load=True)
                refs=[basic.ref,body.ref] if fallback else [s.state.cards[sorted(drawn)[0]].ref]
                self.choose(s,'A',refs,'normal' if fallback else 'matching')
                self.assertIsNone(self.resolve(s))
                self.assertEqual(len(initial)+2-(2 if fallback else 1),len(s.state.players['A'].zones['hand']))
                self.replay(s,load=True)

    def test_actual_wrench_uses_the_target_hand_and_falls_back_when_short(self):
        for short in (True,False):
            with self.subTest(short=short):
                s=self.session(118609920+int(short))
                for oid in tuple(s.state.players['B'].zones['hand']):s.engine.move_card(oid,'library',log=False)
                spell=self.add(s.engine,'Wrench Mind',zone='hand')
                bad=self.add(s.engine,'Counterspell',seat='B',zone='hand')
                artifact=None
                if not short:
                    artifact=self.add(s.engine,'Memnite',seat='B',zone='hand')
                    self.add(s.engine,'Plains',seat='B',zone='hand')
                self.assert_closed(s,spell)
                self.prepare(s,mana={'B':2})
                self.checkpoint(s)
                self.cast(s,'A',spell,['B'])
                self.assertEqual('choice.apnap',self.resolve(s).kind)
                ctx=s.packet('pilot:B',full=True)['decision']['ctx']
                self.assertEqual(['normal'] if short else ['normal','matching'],[row['id'] for row in ctx['discard_options']])
                for viewer in 'ACD':self.assertNotIn(bad.ref,json.dumps(s.packet('pilot:'+viewer,full=True)))
                s=self.replay(s,load=True)
                self.choose(s,'B',[bad.ref] if short else [artifact.ref],'normal' if short else 'matching')
                self.assertIsNone(self.resolve(s))
                self.assertEqual(0 if short else 2,len(s.state.players['B'].zones['hand']))
                self.replay(s,load=True)

    def test_actual_compulsive_research_still_discards_after_all_three_draws_are_prevented(self):
        s=self.session(118609930)
        for oid in tuple(s.state.players['B'].zones['hand']):s.engine.move_card(oid,'library',log=False)
        spell=self.add(s.engine,'Compulsive Research',zone='hand')
        draw=self.add(s.engine,'Reach Through Mists',seat='B',zone='hand')
        private=self.add(s.engine,'Counterspell',seat='B',zone='hand')
        limit=self.add(s.engine,'Spirit of the Labyrinth')
        for card in (spell,draw,limit):self.assert_closed(s,card)
        s.state.players['B'].mana_pool.update({'U':1})
        self.prepare(s,mana={'U':1,'C':2})
        self.checkpoint(s)
        self.cast(s,'B',draw,[])
        self.assertIsNone(self.resolve(s))
        hand=set(s.state.players['B'].zones['hand'])
        self.assertEqual(2,len(hand))
        land=next(s.state.cards[oid] for oid in hand if s.state.cards[oid].ref!=private.ref)
        self.cast(s,'A',spell,['B'])
        self.assertEqual('choice.apnap',self.resolve(s).kind)
        self.assertEqual(hand,set(s.state.players['B'].zones['hand']))
        s=self.replay(s,load=True)
        self.choose(s,'B',[land.ref],'matching')
        self.assertIsNone(self.resolve(s))
        self.assertEqual([private.object_id],s.state.players['B'].zones['hand'])
        self.replay(s,load=True)

    def test_actual_scholar_accepts_flying_creature_and_rejects_ground_creature_singletons(self):
        s=self.session(118609940)
        spell=self.add(s.engine,'Steamcore Scholar',zone='hand')
        flying=self.add(s.engine,'Ornithopter',zone='hand')
        ground=self.add(s.engine,'Memnite',zone='hand')
        self.add(s.engine,'Counterspell',zone='hand')
        self.add(s.engine,'Divination',zone='hand')
        self.assert_closed(s,spell)
        self.prepare(s,mana={'U':1,'C':2})
        self.checkpoint(s)
        self.cast(s,'A',spell,[])
        self.assertEqual('choice.apnap',self.resolve(s).kind)
        self.reject(s,'A',{'action_id':'choose','cards':[ground.ref],'discard_mode':'matching'})
        s=self.replay(s,load=True)
        self.choose(s,'A',[flying.ref],'matching')
        self.assertIsNone(self.resolve(s))
        self.assertEqual('graveyard',s.state.cards[flying.object_id].zone)
        self.assertEqual('hand',s.state.cards[ground.object_id].zone)
        self.replay(s,load=True)

    def test_generated_group_collects_different_private_subsets_before_one_discard_commit(self):
        s=self.session(118609950)
        for seat in 'BCD':
            for oid in tuple(s.state.players[seat].zones['hand']):s.engine.move_card(oid,'library',log=False)
        spell=self.add(s.engine,'Generic Alternative Opponent Discard',zone='hand')
        hands={seat:[self.add(s.engine,name,seat=seat,zone='hand',ref=f'alternative-{seat}-{i}')
            for i,name in enumerate(('Counterspell','Plains','Memnite'))] for seat in 'BD'}
        hands['C']=[self.add(s.engine,'Plains',seat='C',zone='hand',ref='alternative-C-only')]
        self.assert_closed(s,spell)
        self.prepare(s,mana={'B':1,'C':1})
        self.checkpoint(s)
        self.cast(s,'A',spell,[])
        self.assertEqual('choice.apnap',self.resolve(s).kind)
        self.choose(s,'B',[hands['B'][0].ref],'matching')
        self.assertTrue(all(s.state.cards[c.object_id].zone=='hand' for rows in hands.values() for c in rows))
        self.assertEqual({},s.packet('pilot:C',full=True)['decision']['ctx']['prior_public_choices'])
        for card in hands['B']:self.assertNotIn(card.ref,json.dumps(s.packet('pilot:C',full=True)))
        s=self.replay(s,load=True)
        self.choose(s,'C',[hands['C'][0].ref],'normal')
        self.assertTrue(all(s.state.cards[c.object_id].zone=='hand' for rows in hands.values() for c in rows))
        self.choose(s,'D',[hands['D'][0].ref,hands['D'][1].ref],'normal')
        self.assertIsNone(self.resolve(s))
        self.assertEqual((2,0,1),tuple(len(s.state.players[p].zones['hand']) for p in 'BCD'))
        self.replay(s,load=True)
