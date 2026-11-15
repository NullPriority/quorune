"""A modal spell and a separate keyword retain their independent owners."""
from pathlib import Path
from dataclasses import replace
import json,tempfile,unittest
from unittest.mock import patch
from common import ROOT,packet_without_decision_capability
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_fixed_optional_effect_choices import optional_record
import test_target_recipient_results as recipients


MODAL='Choose one —\n• Destroy target artifact.\n• Destroy target enchantment.'
REINFORCE='Reinforce 1—{W} ({W}, Discard this card: Put a +1/+1 counter on target creature.)'


class ModalKeywordCompilerTests(unittest.TestCase):
    def compile(self,text):
        keywords=tuple(keyword for keyword in ('Reinforce','Cycling','Convoke','Entwine')
                       if any(line.startswith(keyword) for line in text.splitlines()))
        return compile_oracle_card(replace(optional_record(text),keywords=keywords),capability_registry=load_default_capability_registry())

    def test_prefix_and_suffix_keywords_retain_separate_source_spans_and_original_line_identities(self):
        for text,modal_line,other_line,other_id in ((MODAL+'\n'+REINFORCE,1,4,'front:n4'),
                ('Convoke\n'+MODAL,2,1,'front:n1:convoke'),(MODAL+'\nCycling {2}',1,4,'front:n4')):
            with self.subTest(text=text):
                ir=self.compile(text);self.assertEqual('exact',ir.status,ir.material_residuals)
                parent,=tuple(node for node in ir.faces[0].nodes if node.kind=='spell_ability')
                self.assertEqual(modal_line,parent.span.line);self.assertEqual('front:n'+str(modal_line),parent.node_id)
                self.assertEqual(MODAL,parent.text);self.assertEqual(parent.text,text[parent.span.start:parent.span.end])
                other,=tuple(node for node in ir.faces[0].nodes if node is not parent)
                self.assertEqual(other_line,other.span.line);self.assertEqual(other_id,other.node_id)
                self.assertEqual(other.text,text[other.span.start:other.span.end])
                self.assertNotEqual(parent.node_id,other.node_id)

    def test_unknown_or_mandatory_siblings_multiple_blocks_and_unsupported_keyword_costs_stay_material(self):
        for text in (MODAL+'\nInvent a result.',MODAL+'\nDraw a card.',MODAL+'\n'+MODAL,
                     MODAL+'\nReinforce X—{X}{W}',MODAL+'\nEntwine {X}',MODAL+'\nCycling {X}'):
            with self.subTest(text=text):
                ir=self.compile(text);self.assertNotEqual('exact',ir.status);self.assertTrue(ir.material_residuals)

    def test_composition_and_keyword_omission_kill_the_positive_witness_but_ordinary_modal_output_is_unchanged(self):
        text=MODAL+'\n'+REINFORCE
        def witness():self.assertEqual('exact',self.compile(text).status)
        witness();ordinary=self.compile(MODAL)
        with patch('quorune.compiler.modal_spell_faces._modal_keyword_siblings',return_value=None):
            with self.assertRaises(AssertionError):witness()
            self.assertEqual(ordinary,self.compile(MODAL))
        with patch('quorune.oracle_ir._keyword_nodes',return_value=()):
            with self.assertRaises(AssertionError):witness()

    def test_withdrawing_either_existing_owner_keeps_the_complete_source_closed(self):
        from copy import deepcopy
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        declarations=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json');known=set(discover_tests(ROOT).values())
        original=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        record=replace(optional_record(MODAL+'\n'+REINFORCE),keywords=('Reinforce',))
        def compile_with(value):
            registry=CapabilityRegistry(value)
            index=build_index(registry_value=value,declaration_source=declarations,discovered_test_ids=known)
            registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
            return compile_oracle_card(record,capability_registry=registry)
        self.assertEqual('exact',compile_with(original).status)
        for missing in ('choice.modal.fixed_one','activation.counter_keyword.fixed','activation.source_zone_change.fixed'):
            value=deepcopy(original);next(row for row in value['capabilities'] if row['id']==missing).update(status='blocked',blockers=['Modal sibling owner withdrawn'])
            self.assertNotEqual('exact',compile_with(value).status)


class ModalKeywordNativeTests(InheritedNativeFixture,unittest.TestCase):
    reject=recipients.TargetRecipientNativeTests.reject
    finish=recipients.TargetRecipientNativeTests.finish

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'modal-keyword.sqlite3'
        base=json.loads((ROOT/'tests/fixtures/bound-effect-program-cards.json').read_text(encoding='utf-8'))
        base['cards']=[row for row in base['cards'] if row['name']=='Generic Bound Plains'];base['rulings']=[]
        basic=Path(cls.temporary.name)/'basic.json';basic.write_text(json.dumps(base),encoding='utf-8')
        build_fixture_database([basic,ROOT/'tests/fixtures/modal-keyword-composition-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Modal keyword composition',[DeckEntry('Sliver Hivelord',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Sliver Hivelord'])

    def ready(self,s,mana=None):
        s.state.config.semantic_policy='trusted_only'
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='modal-keyword-private')
        self.prepare(s,mana={'C':20,**{color:10 for color in 'WUBRG'}} if mana is None else mana);self.checkpoint(s)
        return private

    def cast_modes(self,s,source,modes,targets=(),**fields):
        self.priority(s,'A');row=self.action(s,'A',s.state.cards[source.object_id])
        command={'action_id':row['id'],'modes':list(modes),'targets':list(targets),'pay':'auto',**fields}
        self.reject(s,'D',command);result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)

    def test_actual_modal_cast_keeps_each_target_mode_separate_from_the_hand_activation(self):
        for mode,name,zone in (('mode_1','Sol Ring','battlefield'),('mode_2','Fervor','battlefield'),
                               ('mode_3','Grizzly Bears','graveyard')):
            with self.subTest(mode=mode):
                s=self.session(1005300+int(mode[-1]));source=self.add(s.engine,'Break Ties',zone='hand')
                target=self.add(s.engine,name,seat='D',zone=zone);self.assert_closed(s,source);private=self.ready(s)
                self.reject(s,'A',{'action_id':'cast:'+source.ref,'modes':['mode_4'],'targets':[target.ref],'pay':'auto'})
                self.cast_modes(s,source,[mode],[target.ref]);self.assertIsNone(self.resolve(s))
                self.assertIn(target.object_id,s.state.players['D'].zones['exile' if mode=='mode_3' else 'graveyard'])
                self.assertEqual('graveyard',s.state.cards[source.object_id].zone);self.finish(s,private)

    def test_actual_modal_source_reinforces_on_an_opponents_turn_without_casting_its_spell(self):
        s=self.session(1005310);source=self.add(s.engine,'Break Ties',zone='hand')
        body=self.add(s.engine,'Grizzly Bears',seat='B');body.owner='D';artifact=self.add(s.engine,'Sol Ring',seat='B')
        self.add(s.engine,'Plains');self.assert_closed(s,source);private=self.ready(s,mana={})
        row=next(row for row in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if row.get('action')=='play_land')
        result=s.act('pilot:A',{'action_id':row['id']});self.assertTrue(result.ok,result.summary)
        self.until(s,lambda current:current.state.active_player=='B' and current.state.phase=='precombat_main'
                   and current.state.pending_decision is not None and current.state.pending_decision.kind=='priority')
        self.priority(s,'A');rows=s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        action=next(row for row in rows if row['id'].startswith('activate:'+source.ref+':'))
        command={'action_id':action['id'],'targets':[body.ref],'pay':'auto'};self.reject(s,'D',command)
        result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(s))
        self.assertEqual(1,s.state.cards[body.object_id].counters['+1/+1']);self.assertEqual(3,s.engine._numeric_stat(body.object_id,'power'))
        self.assertEqual('graveyard',s.state.cards[source.object_id].zone);self.assertEqual('battlefield',s.state.cards[artifact.object_id].zone)
        self.assertFalse(any(event.code=='spell.cast' and event.details.get('card')==source.ref for event in s.state.events));self.finish(s,private)

    def test_actual_devoid_is_retained_for_both_modal_exile_and_damage(self):
        for mode in ('mode_1','mode_2'):
            with self.subTest(mode=mode):
                s=self.session(1005320+int(mode[-1]));source=self.add(s.engine,'Consuming Sinkhole',zone='hand')
                target=self.add(s.engine,'Dryad Arbor',seat='D');self.assert_closed(s,source);private=self.ready(s)
                self.cast_modes(s,source,[mode],[target.ref if mode=='mode_1' else 'B'])
                self.assertEqual([],list(s.engine._effective_card_data(s.state.cards[source.object_id])['colors']))
                self.assertIsNone(self.resolve(s))
                if mode=='mode_1':self.assertIn(target.object_id,s.state.players['D'].zones['exile'])
                else:self.assertEqual(36,s.state.players['B'].life)
                self.finish(s,private)

    def test_actual_cycling_and_both_spell_modes_use_the_same_complete_original_without_crossing_effects(self):
        for route in ('mode_1','mode_2','cycling'):
            with self.subTest(route=route):
                s=self.session(1005330+len(route));source=self.add(s.engine,'Shredded Sails',zone='hand')
                artifact=self.add(s.engine,'Sol Ring',seat='D');flyer=self.add(s.engine,'Wind Drake',seat='D')
                self.assert_closed(s,source);private=self.ready(s)
                if route=='cycling':
                    before=set(s.state.players['A'].zones['hand'])
                    actions=s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
                    row=next(row for row in actions if row['id'].startswith('activate:'+source.ref+':'))
                    result=s.act('pilot:A',{'action_id':row['id'],'pay':'auto'});self.assertTrue(result.ok,result.summary)
                    self.assertIsNone(self.resolve(s));drawn,=set(s.state.players['A'].zones['hand'])-(before-{source.object_id})
                    for viewer in 'BCD':
                        self.assertNotIn(s.state.cards[drawn].ref,json.dumps(packet_without_decision_capability(s.packet('pilot:'+viewer,full=True))))
                else:
                    self.cast_modes(s,source,[route],[artifact.ref if route=='mode_1' else flyer.ref]);self.assertIsNone(self.resolve(s))
                self.assertEqual('graveyard' if route=='mode_1' else 'battlefield',s.state.cards[artifact.object_id].zone)
                self.assertEqual('graveyard' if route=='mode_2' else 'battlefield',s.state.cards[flyer.object_id].zone)
                self.assertEqual('graveyard',s.state.cards[source.object_id].zone);self.finish(s,private)

    def test_actual_split_second_still_prevents_an_affordable_response_while_each_mode_resolves(self):
        for mode in ('mode_1','mode_2'):
            with self.subTest(mode=mode):
                s=self.session(1005340+int(mode[-1]));source=self.add(s.engine,'Siege Smash',zone='hand')
                artifact=self.add(s.engine,'Sol Ring',seat='D');body=self.add(s.engine,'Grizzly Bears',seat='D')
                answer=self.add(s.engine,'Counterspell',seat='B',zone='hand');s.state.players['B'].mana_pool.update({'U':2})
                self.assert_closed(s,source);private=self.ready(s)
                self.cast_modes(s,source,[mode],[artifact.ref if mode=='mode_1' else body.ref]);self.priority(s,'B')
                actions=s.packet('pilot:B',full=True)['decision']['ctx']['legal']['actions']
                self.assertFalse(any(row['id']=='cast:'+answer.ref for row in actions))
                spell=next(item for item in s.state.stack if item.card_object_id==source.object_id)
                self.reject(s,'B',{'action_id':'cast:'+answer.ref,'targets':[spell.ref],'pay':'auto'})
                self.assertIsNone(self.resolve(s))
                if mode=='mode_1':self.assertIn(artifact.object_id,s.state.players['D'].zones['graveyard'])
                else:
                    self.assertEqual((5,4),tuple(s.engine._numeric_stat(body.object_id,stat) for stat in ('power','toughness')))
                    self.assertIn('trample',{word.casefold() for word in s.engine._effective_card_data(s.state.cards[body.object_id])['keywords']})
                self.finish(s,private)

    def test_actual_convoke_pays_each_modal_original_with_creatures_and_keeps_unselected_effects_absent(self):
        for name,mode in ((name,mode) for name in ('Sprouting Renewal','Shatter the Source') for mode in ('mode_1','mode_2')):
            with self.subTest(name=name,mode=mode):
                s=self.session(1005350+len(name)+int(mode[-1]));source=self.add(s.engine,name,zone='hand')
                artifact=self.add(s.engine,'Sol Ring',seat='D');body=self.add(s.engine,'Wind Drake',seat='D')
                colored=self.add(s.engine,'Grizzly Bears' if name=='Sprouting Renewal' else 'Raging Goblin')
                helpers=[colored]+[self.add(s.engine,'Memnite',ref='modal-convoke-'+str(index))
                    for index in range(2 if name=='Sprouting Renewal' else 5)]
                self.assert_closed(s,source);private=self.ready(s,mana={})
                targets=[] if name=='Sprouting Renewal' and mode=='mode_1' else [artifact.ref if mode=='mode_2' else body.ref]
                self.cast_modes(s,source,[mode],targets,convoke_cards=[card.ref for card in helpers])
                self.assertTrue(all(s.state.cards[card.object_id].tapped for card in helpers))
                self.assertEqual(0,sum(s.state.players['A'].mana_pool.values()));self.assertIsNone(self.resolve(s))
                tokens=[card for card in s.state.cards.values() if card.is_token and card.zone=='battlefield']
                if name=='Sprouting Renewal' and mode=='mode_1':
                    token,=tokens;data=s.engine._effective_card_data(token)
                    self.assertEqual(('A','A'),(token.owner,token.controller));self.assertEqual({'G','W'},set(data['colors']))
                    self.assertTrue({'elf','knight'}<=set(s.engine._type_parts(data['type_line'])[1]))
                    self.assertIn('vigilance',{word.casefold() for word in data['keywords']})
                    self.assertEqual((2,2),tuple(s.engine._numeric_stat(token.object_id,stat) for stat in ('power','toughness')))
                else:self.assertFalse(tokens)
                self.assertEqual('graveyard' if mode=='mode_2' else 'battlefield',s.state.cards[artifact.object_id].zone)
                self.assertEqual('graveyard' if name=='Shatter the Source' and mode=='mode_1' else 'battlefield',s.state.cards[body.object_id].zone)
                self.finish(s,private)

    def test_actual_convoke_both_modes_counter_then_draw_and_discard_through_the_existing_hand_chooser(self):
        s=self.session(1005370);source=self.add(s.engine,'Artistic Refusal',zone='hand')
        opponent=self.add(s.engine,'Reach Through Mists',seat='B',zone='hand');s.state.players['B'].mana_pool.update({'U':1})
        helpers=[self.add(s.engine,'Merfolk of the Pearl Trident')]+[self.add(s.engine,'Memnite',ref='modal-refusal-convoke-'+str(index)) for index in range(3)]
        self.assert_closed(s,source);private=self.ready(s,mana={'C':1,'U':1});before=set(s.state.players['A'].zones['hand'])
        self.cast(s,'B',opponent);target=next(item for item in s.state.stack if item.card_object_id==opponent.object_id)
        self.cast_modes(s,source,['mode_1','mode_2'],[target.ref],convoke_cards=[card.ref for card in helpers])
        self.assertTrue(all(s.state.cards[card.object_id].tapped for card in helpers));self.assertEqual(0,sum(s.state.players['A'].mana_pool.values()))
        decision=self.resolve(s);self.assertIsNotNone(decision);self.assertEqual('choice.apnap',decision.kind)
        self.assertIn(opponent.object_id,s.state.players['B'].zones['graveyard'])
        drawn=set(s.state.players['A'].zones['hand'])-(before-{source.object_id});self.assertEqual(2,len(drawn))
        for viewer in 'BCD':
            packet=json.dumps(packet_without_decision_capability(s.packet('pilot:'+viewer,full=True)))
            for oid in drawn:self.assertNotIn(s.state.cards[oid].ref,packet)
        chosen=s.state.cards[next(iter(before-{source.object_id}))]
        command={'action_id':'choose','cards':[chosen.ref]};self.reject(s,'B',command)
        result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(s))
        self.assertEqual(len(before),len(s.state.players['A'].zones['hand']));self.finish(s,private)

    def test_actual_rebound_recasts_with_a_new_mode_and_target_without_repeating_hand_only_exile(self):
        s=self.session(1005380);source=self.add(s.engine,'Fantastic Elasticity',zone='hand')
        target=self.add(s.engine,'Grizzly Bears',seat='D');extra=self.add(s.engine,'Time Warp',zone='hand')
        self.assert_closed(s,source);private=self.ready(s)
        self.cast_modes(s,source,['mode_1'],[target.ref]);self.assertIsNone(self.resolve(s))
        self.assertIn(target.object_id,s.state.players['D'].zones['hand']);self.assertIn(source.object_id,s.state.players['A'].zones['exile'])
        self.cast(s,'A',extra,['A']);self.assertIsNone(self.resolve(s))
        self.until(s,lambda current:current.state.pending_decision is not None and current.state.pending_decision.kind=='selection.exile_cast')
        self.assertEqual('upkeep',s.state.step);self.assertEqual(0,sum(s.state.players['A'].mana_pool.values()))
        command={'action_id':'cast','modes':['mode_2'],'targets':[s.state.cards[extra.object_id].ref]}
        self.reject(s,'D',command);result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(s));self.assertIn(extra.object_id,s.state.players['A'].zones['hand'])
        self.assertIn(source.object_id,s.state.players['A'].zones['graveyard']);self.finish(s,private)

    def test_actual_sneak_locks_both_modes_and_pays_the_unblocked_return_in_the_real_blockers_window(self):
        s=self.session(1005390);source=self.add(s.engine,"Karai's Technique",zone='hand')
        attacker=self.add(s.engine,'Raging Goblin');friendly=self.add(s.engine,'Grizzly Bears',ref='modal-sneak-friendly')
        opposing=self.add(s.engine,'Grizzly Bears',seat='C',ref='modal-sneak-opposing');opposing.owner='D'
        self.add(s.engine,'Plains');self.add(s.engine,'Swamp');self.assert_closed(s,source);private=self.ready(s,mana={})
        command={'action_id':'cast:'+source.ref,'cost_option':'sneak','return_cards':[attacker.ref],
                 'modes':['mode_1','mode_2'],'targets':[friendly.ref,opposing.ref],'pay':'auto'}
        self.reject(s,'A',command)
        self.attack(s,[(attacker,'B')])
        self.until(s,lambda current:current.state.combat.blockers_declared and current.state.pending_decision is not None
                   and current.state.pending_decision.kind=='priority')
        self.priority(s,'A');row=self.action(s,'A',source)
        self.assertIn('sneak',[option['id'] for option in row['cost_options']])
        self.reject(s,'D',command);result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        self.assertIn(attacker.object_id,s.state.players['A'].zones['hand']);self.assertIsNone(self.resolve(s))
        self.assertEqual((5,5),tuple(s.engine._numeric_stat(friendly.object_id,stat) for stat in ('power','toughness')))
        self.assertIn(opposing.object_id,s.state.players['D'].zones['graveyard']);self.finish(s,private)
