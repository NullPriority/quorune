"""Optional player results preserve target, chooser and affected-player roles."""
from pathlib import Path
import json,tempfile,unittest
from unittest.mock import patch
from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry,CapabilityRegistry
from quorune.compiler.optional_effect_templates import FIXED_OPTIONAL_EFFECT_CAPABILITY
from scripts.build_test_database import build_fixture_database
from test_fixed_optional_effect_choices import optional_record
import test_target_recipient_results as recipients


class OptionalPlayerResultCompilerTests(unittest.TestCase):
    def test_targeted_player_verbs_preserve_optional_ownership_source_span_and_exact_child(self):
        registry=load_default_capability_registry()
        for verb,operation in (('draw a card','draw'),('discard a card','choose_cards_apnap'),('mill two cards','mill')):
            text='When this creature enters, you may have target opponent '+verb+'.'
            ir=compile_oracle_card(optional_record(text,type_line='Creature'),capability_registry=registry)
            self.assertEqual('exact',ir.status,ir.material_residuals);node,=ir.faces[0].nodes
            self.assertEqual((0,len(text)),(node.span.start,node.span.end));self.assertEqual(text,node.text)
            self.assertEqual('opponent',node.target_schema['player_relation'])
            wrapper,=node.effects;self.assertEqual('offer_optional_effect',wrapper['op'])
            self.assertEqual('$controller',wrapper['player']);child,=wrapper['effects']
            self.assertEqual(operation,child['op']);self.assertIn(FIXED_OPTIONAL_EFFECT_CAPABILITY,node.capability_dependencies)

    def test_missing_optional_or_child_owner_and_unknown_siblings_do_not_admit_the_source(self):
        text='When this creature enters, you may have target opponent discard a card.'
        registry=load_default_capability_registry()
        from quorune.compiler import optional_effect_templates as owner
        def witness():self.assertEqual('exact',compile_oracle_card(optional_record(text,type_line='Creature'),capability_registry=registry).status)
        witness()
        with patch.object(owner,'_OPTIONAL_HAVE_RESULT') as parser:
            parser.fullmatch.return_value=None
            with self.assertRaises(AssertionError):witness()
        from copy import deepcopy
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        declarations=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        known=set(discover_tests(ROOT).values())
        def verified(value):
            result=CapabilityRegistry(value)
            index=build_index(registry_value=value,declaration_source=declarations,discovered_test_ids=known)
            result.mark_evidence_verified(validate_capability_evidence_index(index,registry=result))
            return result
        original=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        self.assertEqual('exact',compile_oracle_card(optional_record(text,type_line='Creature'),capability_registry=verified(original)).status)
        for missing in (FIXED_OPTIONAL_EFFECT_CAPABILITY,'choice.affected_player.fixed_discard','target.revalidate_resolution'):
            raw=deepcopy(original)
            next(row for row in raw['capabilities'] if row['id']==missing).update(status='blocked',blockers=['Optional player owner withdrawn'])
            ir=compile_oracle_card(optional_record(text,type_line='Creature'),capability_registry=verified(raw))
            self.assertNotEqual('exact',ir.status)

    def test_resolved_foreign_player_children_require_the_exact_single_player_and_closed_primitive(self):
        from quorune.semantic_choices.optional_effect import _represented_effect
        from quorune.semantic_choices.model import SemanticChoiceError
        from test_fixed_optional_effect_choices import choice_query
        registry=load_default_capability_registry();query=choice_query()
        def bind(value):
            if isinstance(value,dict):return {key:bind(child) for key,child in value.items()}
            if isinstance(value,(list,tuple)):return [bind(child) for child in value]
            return {'$controller':'A','$target.0':'B'}.get(value,value) if isinstance(value,str) else value
        for ending in ('discard a card','discard a card at random'):
            text='When this creature enters, you may have target opponent '+ending+'.'
            ir=compile_oracle_card(optional_record(text,type_line='Creature'),capability_registry=registry)
            child=bind(ir.faces[0].nodes[0].effects[0]['effects'][0])
            _represented_effect(child,actor='A',query=query)
            changes=({'actor':'D'},{'players':[]},{'players':['B','C']},{'players':['unknown']},
                     {'count':True},{'count':0},{'unknown':True})
            if 'target' in child:changes+=({'target':'C'},{'then':'sacrifice'})
            for change in changes:
                with self.subTest(ending=ending,change=change),self.assertRaises(SemanticChoiceError):
                    _represented_effect({**child,**change},actor='A',query=query)
            if 'target' in child:
                def witness():
                    with self.assertRaises(SemanticChoiceError):_represented_effect({**child,'target':'C'},actor='A',query=query)
                with patch('quorune.semantic_choices.optional_effect._represented_targeted_player_discard',return_value=None):
                    with self.assertRaises(AssertionError):witness()
        for body in ('you may have the chosen player discard a card.',
                     'you may have target opponent discard a card. If you do, draw a card.',
                     'you may have target player mill two cards. Invent a result.'):
            ir=compile_oracle_card(optional_record('When this creature enters, '+body,type_line='Creature'),capability_registry=registry)
            self.assertNotEqual('exact',ir.status)


class OptionalPlayerResultNativeTests(InheritedNativeFixture,unittest.TestCase):
    reject=recipients.TargetRecipientNativeTests.reject
    finish=recipients.TargetRecipientNativeTests.finish

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'optional-player.sqlite3'
        base=json.loads((ROOT/'tests/fixtures/bound-effect-program-cards.json').read_text(encoding='utf-8'))
        base['cards']=[row for row in base['cards'] if row['name']=='Generic Bound Plains'];base['rulings']=[]
        basic=Path(cls.temporary.name)/'basic.json';basic.write_text(json.dumps(base),encoding='utf-8')
        build_fixture_database([basic,ROOT/'tests/fixtures/optional-player-result-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Optional player results',[DeckEntry('Sliver Hivelord',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Sliver Hivelord'])

    def ready(self,s):
        s.state.config.semantic_policy='trusted_only'
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='optional-player-private')
        self.prepare(s,mana={'C':30,'W':10,'U':10,'B':10,'R':10,'G':10});self.checkpoint(s)
        return private

    def choose(self,s,seat='A',**fields):
        command={'action_id':'choose',**fields};self.reject(s,'D' if seat!='D' else 'C',command)
        result=s.act('pilot:'+seat,command);self.assertTrue(result.ok,result.summary)

    def choose_target(self,s,seat='B',*,opponent=False):
        decision=self.resolve(s);self.assertIsNotNone(decision);self.assertEqual('semantic.target',decision.kind)
        if opponent:self.reject(s,'A',{'action_id':'choose','targets':['A']})
        self.choose(s,targets=[seat])

    def choose_optional(self,s,*,apply=True):
        decision=self.resolve(s);self.assertIsNotNone(decision);self.assertEqual('semantic.choice',decision.kind)
        self.reject(s,'B',{'action_id':'choose','choice':'apply' if apply else 'decline'})
        self.choose(s,choice='apply' if apply else 'decline')

    def choose_discard(self,s,seat='B'):
        decision=self.resolve(s);self.assertIsNotNone(decision);self.assertEqual('choice.apnap',decision.kind)
        from common import packet_without_decision_capability
        for viewer in 'ABCD':
            packet=json.dumps(packet_without_decision_capability(s.packet('pilot:'+viewer,full=True)))
            for oid in s.state.players[seat].zones['hand']:
                if viewer==seat:self.assertIn(s.state.cards[oid].ref,packet)
                else:self.assertNotIn(s.state.cards[oid].ref,packet)
        card=s.state.cards[s.state.players[seat].zones['hand'][0]]
        self.reject(s,'A' if seat!='A' else 'B',{'action_id':'choose','cards':[card.ref]})
        self.choose(s,seat,cards=[card.ref]);return card

    def apply_mill(self,s,count,*,apply=True):
        before=list(s.state.players['B'].zones['library']);expected=set(before[-count:])
        self.choose_optional(s,apply=apply)
        after=s.state.players['B'].zones['library']
        self.assertEqual(before[:-count] if apply else before,after)
        if apply:
            self.assertTrue(expected<=set(s.state.players['B'].zones['graveyard']))
            self.assertTrue(all(s.state.cards[oid].owner=='B' for oid in expected))

    def test_actual_entry_targets_before_optional_choice_and_only_the_affected_opponent_discards(self):
        for name,apply in (('Ebon Dragon',True),('Brutal Nightstalker',False)):
            with self.subTest(name=name):
                s=self.session(1004200+apply);source=self.add(s.engine,name,zone='hand')
                self.assert_closed(s,source);private=self.ready(s);before=set(s.state.players['B'].zones['hand'])
                self.cast(s,'A',source);self.choose_target(s,opponent=True)
                self.assertEqual(before,set(s.state.players['B'].zones['hand']))
                self.choose_optional(s,apply=apply)
                if apply:
                    card=self.choose_discard(s);self.assertEqual(before-{card.object_id},set(s.state.players['B'].zones['hand']))
                    self.assertEqual('graveyard',s.state.cards[card.object_id].zone)
                else:self.assertEqual(before,set(s.state.players['B'].zones['hand']))
                self.assertIsNone(self.resolve(s));self.finish(s,private)

    def test_actual_optional_trigger_offers_no_resolution_choice_after_its_only_target_concedes(self):
        s=self.session(1004205);source=self.add(s.engine,'Brutal Nightstalker',zone='hand')
        self.assert_closed(s,source);private=self.ready(s);self.cast(s,'A',source)
        self.choose_target(s,opponent=True);self.priority(s,'B')
        result=s.act('pilot:B',{'action_id':'concede','choices':{'confirm_concede':True}})
        self.assertTrue(result.ok,result.summary);self.assertNotIn('B',s.engine.active_seats)
        self.assertIsNone(self.resolve(s));self.assertFalse(s.state.stack);self.finish(s,private)

    def test_actual_conditional_entry_and_untap_triggers_preserve_their_independent_conditions(self):
        for name,enabled in (('Hag Hedge-Mage',True),('Hag Hedge-Mage',False),('Hollowsage',True),('Hollowsage',False)):
            with self.subTest(name=name,enabled=enabled):
                s=self.session(1004210+enabled+(name=='Hollowsage')*10)
                source=self.add(s.engine,name,zone='hand' if name=='Hag Hedge-Mage' else 'battlefield')
                if name=='Hag Hedge-Mage':
                    for index in range(2 if enabled else 1):self.add(s.engine,'Swamp',ref='optional-condition-swamp-'+str(index))
                else:
                    source.tapped=enabled;other=self.add(s.engine,'Grizzly Bears');other.tapped=True
                    untap=self.add(s.engine,'Vitalize',zone='hand')
                self.assert_closed(s,source);private=self.ready(s);before=set(s.state.players['B'].zones['hand'])
                if name=='Hag Hedge-Mage':
                    offered=self.action(s,'A',source);option=offered['cost_options'][0]
                    result=s.act('pilot:A',{'action_id':offered['id'],'cost_option':option['id'],'pay':'auto'})
                    self.assertTrue(result.ok,result.summary)
                else:self.cast(s,'A',untap)
                if enabled:
                    self.choose_target(s);self.choose_optional(s);card=self.choose_discard(s)
                    self.assertEqual(before-{card.object_id},set(s.state.players['B'].zones['hand']))
                self.assertIsNone(self.resolve(s))
                if not enabled:self.assertEqual(before,set(s.state.players['B'].zones['hand']))
                if name=='Hollowsage':self.assertFalse(s.state.cards[source.object_id].tapped)
                self.finish(s,private)

    def test_actual_colored_entry_keeps_blue_draw_and_black_affected_player_discard_distinct(self):
        s=self.session(1004240);provider=self.add(s.engine,'Dire Undercurrents')
        black=self.add(s.engine,'Scathe Zombies',zone='hand');blue=self.add(s.engine,'Merfolk of the Pearl Trident',zone='hand')
        green=self.add(s.engine,'Grizzly Bears',zone='hand');self.assert_closed(s,provider);private=self.ready(s)
        self.cast(s,'A',green);self.assertIsNone(self.resolve(s))
        before=set(s.state.players['B'].zones['hand']);self.cast(s,'A',black)
        self.choose_target(s);self.choose_optional(s);card=self.choose_discard(s)
        self.assertEqual(before-{card.object_id},set(s.state.players['B'].zones['hand']));self.assertIsNone(self.resolve(s))
        self.cast(s,'A',blue);self.choose_target(s,seat='A');before=set(s.state.players['A'].zones['hand'])
        self.choose_optional(s);self.assertIsNone(self.resolve(s))
        drawn,=set(s.state.players['A'].zones['hand'])-before
        from common import packet_without_decision_capability
        for viewer in 'BCD':
            self.assertNotIn(s.state.cards[drawn].ref,json.dumps(packet_without_decision_capability(s.packet('pilot:'+viewer,full=True))))
        self.finish(s,private)

    def test_actual_foreign_owned_upkeep_source_keeps_optional_random_discard_and_token_choices_with_its_controller(self):
        s=self.session(1004250);source=self.add(s.engine,'Nath of the Gilt-Leaf');source.owner='D'
        extra=self.add(s.engine,'Time Warp',zone='hand');self.assert_closed(s,source);private=self.ready(s)
        self.cast(s,'A',extra,['A']);self.assertIsNone(self.resolve(s))
        self.until(s,lambda current:current.state.pending_decision is not None and current.state.pending_decision.kind=='semantic.target')
        self.assertEqual('A',s.state.active_player);self.assertEqual('upkeep',s.state.step)
        self.choose_target(s,opponent=True);before=set(s.state.players['B'].zones['hand'])
        self.choose_optional(s)
        discarded,=before-set(s.state.players['B'].zones['hand'])
        self.assertIn(discarded,s.state.players['B'].zones['graveyard'])
        self.choose_optional(s);self.assertIsNone(self.resolve(s))
        tokens=[card for card in s.state.cards.values() if card.is_token and card.zone=='battlefield']
        token,=tokens;self.assertEqual(('A','A'),(token.owner,token.controller))
        self.assertTrue({'elf','warrior'}<=set(s.engine._type_parts(s.engine._effective_card_data(token)['type_line'])[1]))
        self.assertEqual((1,1),tuple(s.engine._numeric_stat(token.object_id,stat) for stat in ('power','toughness')))
        self.finish(s,private)

    def test_actual_other_creature_departure_queues_mill_after_bounce_and_retains_its_chooser_after_source_departure(self):
        for other_left in (True,False):
            with self.subTest(other_left=other_left):
                s=self.session(1004260+other_left);source=self.add(s.engine,'Extractor Demon')
                other=self.add(s.engine,'Grizzly Bears');bounce=self.add(s.engine,'Unsummon',zone='hand',ref='optional-bounce-other')
                response=self.add(s.engine,'Unsummon',zone='hand',ref='optional-bounce-provider')
                self.assert_closed(s,source);private=self.ready(s)
                self.cast(s,'A',bounce,[other.ref if other_left else source.ref])
                if other_left:
                    self.choose_target(s);self.cast(s,'A',response,[s.state.cards[source.object_id].ref])
                    self.apply_mill(s,2);self.assertEqual('hand',s.state.cards[source.object_id].zone)
                self.assertIsNone(self.resolve(s));self.finish(s,private)

    def test_actual_snow_entry_excludes_the_source_and_an_ordinary_permanent_before_milling(self):
        s=self.session(1004270);source=self.add(s.engine,'Iceberg Cancrix',zone='hand')
        ordinary=self.add(s.engine,'Memnite',zone='hand');snow=self.add(s.engine,'Snow-Covered Plains',zone='hand')
        self.assert_closed(s,source);private=self.ready(s)
        self.cast(s,'A',source);self.assertIsNone(self.resolve(s))
        self.cast(s,'A',ordinary);self.assertIsNone(self.resolve(s))
        actions=s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        offered=next(row for row in actions if row.get('action')=='play_land' and snow.ref in json.dumps(row))
        result=s.act('pilot:A',{'action_id':offered['id']});self.assertTrue(result.ok,result.summary)
        self.choose_target(s);self.apply_mill(s,2);self.assertIsNone(self.resolve(s));self.finish(s,private)

    def test_actual_draw_trigger_can_apply_or_decline_mill_without_revealing_the_drawn_card(self):
        from common import packet_without_decision_capability
        for apply in (True,False):
            with self.subTest(apply=apply):
                s=self.session(1004280+apply);source=self.add(s.engine,"Jace's Erasure")
                spell=self.add(s.engine,'Reach Through Mists',zone='hand');self.assert_closed(s,source);private=self.ready(s)
                before=set(s.state.players['A'].zones['hand']);self.cast(s,'A',spell)
                self.choose_target(s);self.apply_mill(s,1,apply=apply);self.assertIsNone(self.resolve(s))
                drawn,=set(s.state.players['A'].zones['hand'])-(before-{spell.object_id})
                for viewer in 'BCD':
                    self.assertNotIn(s.state.cards[drawn].ref,json.dumps(packet_without_decision_capability(s.packet('pilot:'+viewer,full=True))))
                self.finish(s,private)

    def test_actual_spirit_and_arcane_casts_use_the_cast_spell_mana_value_and_ignore_an_unrelated_creature(self):
        s=self.session(1004290);source=self.add(s.engine,'Cloudhoof Kirin')
        spirit=self.add(s.engine,'Sky Spirit',zone='hand');arcane=self.add(s.engine,'Reach Through Mists',zone='hand')
        ordinary=self.add(s.engine,'Grizzly Bears',zone='hand');self.assert_closed(s,source);private=self.ready(s)
        self.cast(s,'A',ordinary);self.assertIsNone(self.resolve(s))
        for spell,count in ((spirit,3),(arcane,1)):
            self.cast(s,'A',spell);self.choose_target(s);self.apply_mill(s,count);self.assertIsNone(self.resolve(s))
        self.assertEqual('battlefield',s.state.cards[spirit.object_id].zone)
        self.assertEqual('graveyard',s.state.cards[arcane.object_id].zone);self.finish(s,private)
