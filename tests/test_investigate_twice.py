"""CR701.16a/111.10f: twice means two separate canonical Clue creations."""
import json,tempfile,unittest
from pathlib import Path
from common import ROOT
import test_fixed_creature_power_damage as records
import test_target_recipient_results as recipients
from inherited_composition_fixture import InheritedNativeFixture
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from scripts.build_test_database import build_fixture_database


class InvestigateTwiceCompilerTests(unittest.TestCase):
    def compile(self,text):
        return compile_oracle_card(records.card_record(text),capability_registry=load_default_capability_registry(),capability_profile='commander_review')

    def test_twice_preserves_the_existing_two_action_owner_and_source_span(self):
        ir=self.compile('Investigate twice.');self.assertEqual('exact',ir.status,ir.material_residuals)
        node,=ir.faces[0].nodes
        existing=self.compile('Investigate two times.').faces[0].nodes[0]
        self.assertEqual(existing.effects,node.effects)
        self.assertEqual('investigate_sequential',node.effects[0]['op'])
        self.assertEqual(2,node.effects[0]['count'])
        self.assertEqual('Investigate twice.',node.text)
        self.assertEqual(node.text,ir.faces[0].oracle_text[node.span.start:node.span.end])
        self.assertIn('keyword_action.investigate.fixed',node.capability_dependencies)

    def test_open_counts_and_following_unrepresented_clauses_stay_residual(self):
        for text in ('Investigate twice or more.','Investigate twice for each creature you control.',
                'Investigate twice twice.','Investigate zero times.','Investigate twice. Rewrite history.'):
            with self.subTest(text=text):self.assertNotEqual('exact',self.compile(text).status)

    def test_verified_investigate_withdrawal_blocks_twice_but_preserves_an_independent_food(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        next(r for r in raw['capabilities'] if r['id']=='keyword_action.investigate.fixed').update(status='blocked',blockers=['Withheld Investigate owner'])
        registry=CapabilityRegistry(raw)
        index=build_index(registry_value=raw,declaration_source=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json'),discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
        self.assertNotEqual('exact',compile_oracle_card(records.card_record('Investigate twice.'),capability_registry=registry).status)
        self.assertEqual('exact',compile_oracle_card(records.card_record('Create a Food token.'),capability_registry=registry).status)


class InvestigateTwiceNativeTests(InheritedNativeFixture,unittest.TestCase):
    reject=recipients.TargetRecipientNativeTests.reject
    finish=recipients.TargetRecipientNativeTests.finish

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();folder=Path(cls.temporary.name)
        base=json.loads((ROOT/'tests/fixtures/bound-effect-program-cards.json').read_text(encoding='utf-8'))
        base['cards']=[r for r in base['cards'] if r['name']=='Generic Bound Plains'];base['rulings']=[]
        (folder/'basic.json').write_text(json.dumps(base),encoding='utf-8')
        path=folder/'investigate.sqlite3'
        build_fixture_database([folder/'basic.json',ROOT/'tests/fixtures/investigate-twice-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Twice investigate',[DeckEntry('Sliver Hivelord',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Sliver Hivelord'])

    def scene(self,name,*,before=None,mana=None):
        s=self.session(2021019+len(name));source=self.add(s.engine,name,zone='hand')
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='investigate-private-C')
        if before:before(s,source)
        s.state.config.semantic_policy='trusted_only';self.assert_closed(s,source)
        self.prepare(s,mana=mana or {'C':30,**{c:10 for c in 'WUBRG'}});self.checkpoint(s)
        return s,source,private

    def tokens(self,s):
        return [c for c in s.state.cards.values() if c.is_token and c.zone=='battlefield']

    def test_both_originals_investigate_twice_and_their_clues_pay_the_full_draw_cost(self):
        for name in ('Wavesifter','Ezrim, Agency Chief'):
            with self.subTest(name=name):
                s,source,private=self.scene(name)
                self.cast(s,'A',source);self.assertIsNone(self.resolve(s))
                clues=self.tokens(s);self.assertEqual(2,len(clues))
                self.assertTrue(all(c.owner==c.controller=='A' for c in clues))
                self.assertTrue(all('Clue' in s.engine._effective_card_data(c)['type_line'] for c in clues))
                self.priority(s,'A');clue=clues[0]
                row=next(r for r in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if r['id'].startswith('activate:'+clue.ref+':'))
                command={'action_id':row['id'],'pay':'auto'}
                self.reject(s,'B',command)
                hand=set(s.state.players['A'].zones['hand']);mana=sum(s.state.players['A'].mana_pool.values())
                result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
                self.assertNotEqual('battlefield',s.state.cards[clue.object_id].zone)
                self.assertEqual(mana-2,sum(s.state.players['A'].mana_pool.values()))
                s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
                drawn=set(s.state.players['A'].zones['hand'])-hand;self.assertEqual(1,len(drawn))
                for oid in drawn:
                    for viewer in 'BCD':self.assertNotIn(s.state.cards[oid].ref,json.dumps(s.packet('pilot:'+viewer,full=True)))
                self.assertEqual(1,len(self.tokens(s)));self.finish(s,private)

    def test_evoked_wavesifter_keeps_both_investigations_in_either_trigger_order(self):
        for sacrifice_first in (False,True):
            with self.subTest(sacrifice_first=sacrifice_first):
                s,source,private=self.scene('Wavesifter',mana={'G':1,'U':1})
                row=self.action(s,'A',source)
                result=s.act('pilot:A',{'action_id':row['id'],'cost_option':'evoke','pay':'auto'})
                self.assertTrue(result.ok,result.summary)
                self.assertEqual(0,sum(s.state.players['A'].mana_pool.values()))
                self.assertEqual('trigger.order',self.resolve(s).kind)
                rows=s.packet('pilot:A',full=True)['decision']['ctx']['triggers']
                self.assertEqual(2,len(rows));evoke=next(r['id'] for r in rows if 'evoke sacrifice' in r['label'])
                investigate=next(r['id'] for r in rows if r['id']!=evoke)
                command={'action_id':'order','order':[investigate,evoke] if sacrifice_first else [evoke,investigate]}
                self.reject(s,'B',command);s=self.replay(s,load=True)
                result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
                self.assertIsNone(self.resolve(s))
                self.assertEqual('graveyard',s.state.cards[source.object_id].zone)
                self.assertEqual(2,len(self.tokens(s)));self.finish(s,private)

    def test_twice_reopens_token_replacements_after_the_first_clue_creation_and_saved_choice(self):
        extra={}
        def before(s,source):
            extra['took']=self.add(s.engine,'Peregrin Took')
            extra['doubler']=self.add(s.engine,'Anointed Procession')
            for card in extra.values():self.assert_closed(s,card)
        s,source,private=self.scene('Wavesifter',before=before)
        self.cast(s,'A',source)
        self.assertEqual('replacement.order',self.resolve(s).kind)
        for completed in range(2):
            self.assertEqual(4*completed,len(self.tokens(s)))
            rows=s.packet('pilot:A',full=True)['decision']['ctx']['options']
            self.assertEqual({extra['took'].ref,extra['doubler'].ref},{r['source'] for r in rows})
            if completed:s=self.replay(s,load=True)
            command={'action_id':'choose','replacement':next(r['id'] for r in rows if r['source']==extra['took'].ref)}
            self.reject(s,'B',command)
            result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(s));tokens=self.tokens(s)
        self.assertEqual(8,len(tokens))
        self.assertEqual(4,sum('Clue' in s.engine._effective_card_data(c)['type_line'] for c in tokens))
        self.assertEqual(4,sum('Food' in s.engine._effective_card_data(c)['type_line'] for c in tokens))
        self.finish(s,private)

    def test_fresh_original_witness_kills_losing_the_second_investigation(self):
        from unittest.mock import patch
        from quorune.fixed_token_production import FixedInvestigateSpec
        method='test_both_originals_investigate_twice_and_their_clues_pay_the_full_draw_cost'
        getattr(type(self)(method),method)()
        original=FixedInvestigateSpec.effect
        def only_once(spec):return original(FixedInvestigateSpec(1)) if spec.count==2 else original(spec)
        with patch.object(FixedInvestigateSpec,'effect',only_once):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()

    def test_ezrim_can_use_one_created_clue_as_its_artifact_cost_without_activating_the_clue(self):
        s,source,private=self.scene('Ezrim, Agency Chief')
        self.cast(s,'A',source);self.assertIsNone(self.resolve(s))
        clue=self.tokens(s)[0];hand=set(s.state.players['A'].zones['hand'])
        self.priority(s,'A')
        row=next(r for r in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if r['id'].startswith('activate:'+source.ref+':'))
        command={'action_id':row['id'],'pay':'auto','cost_cards':[clue.ref]}
        self.reject(s,'B',command)
        result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        self.assertNotEqual('battlefield',s.state.cards[clue.object_id].zone)
        self.assertEqual('semantic.choice',self.resolve(s).kind)
        command={'action_id':'choose','choice':'Lifelink'}
        self.reject(s,'B',command);s=self.replay(s,load=True)
        result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(s))
        self.assertIn('lifelink',s.engine._combat_keywords(s.state.cards[source.object_id]))
        self.assertEqual(hand,set(s.state.players['A'].zones['hand']))
        self.assertEqual(1,len(self.tokens(s)));self.finish(s,private)
