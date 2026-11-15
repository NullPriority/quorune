"""Pinned original split, mutate and quantity compositions over recipient results."""
import json
from pathlib import Path
import tempfile
import unittest

from common import ROOT,packet_without_decision_capability
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_target_recipient_results as recipients
import test_mutate_cast_native as mutate_actions


class TargetRecipientAdditionalNativeTests(InheritedNativeFixture,unittest.TestCase):
    reject=recipients.TargetRecipientNativeTests.reject
    finish=recipients.TargetRecipientNativeTests.finish
    tokens=recipients.TargetRecipientNativeTests.tokens
    control_response=recipients.TargetRecipientNativeTests.control_response
    mutate=mutate_actions.MutateCastNativeTests.mutate
    choose_merge=mutate_actions.MutateCastNativeTests.choose_merge

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'additional-recipients.sqlite3'
        base=json.loads((ROOT/'tests/fixtures/bound-effect-program-cards.json').read_text(encoding='utf-8'))
        base['cards']=[row for row in base['cards'] if row['name']=='Generic Bound Plains'];base['rulings']=[]
        basic=Path(cls.temporary.name)/'basic.json';basic.write_text(json.dumps(base),encoding='utf-8')
        build_fixture_database([basic,ROOT/'tests/fixtures/target-recipient-additional-composition-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Additional recipient compositions',[DeckEntry('Sliver Hivelord',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Sliver Hivelord'])

    def cast_face(self,session,source,face,targets=()):
        self.priority(session,'A')
        offered=next(row for row in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
            if row.get('card')==source.ref and row.get('face')==face)
        command={'action_id':offered['id'],'targets':list(targets),'pay':'auto'}
        if offered.get('cost_options'):command['cost_option']=offered['cost_options'][0]['id']
        self.reject(session,'D',command)
        result=session.act('pilot:A',command);self.assertTrue(result.ok,result.summary)

    def test_actual_rapid_hybridization_keeps_regeneration_prohibition_and_controller_token(self):
        session=self.session(999101);source=self.add(session.engine,'Rapid Hybridization',zone='hand')
        target=self.add(session.engine,'Grizzly Bears',seat='C');target.owner='D'
        shield=self.add(session.engine,'Regenerate',seat='C',zone='hand');self.add(session.engine,'Forest',seat='C');self.add(session.engine,'Wastes',seat='C')
        private=self.add(session.engine,'Counterspell',seat='C',zone='hand',ref='additional-rapid-private-card')
        self.assert_closed(session,source);self.prepare(session,mana={'U':1});self.checkpoint(session)
        self.cast(session,'C',shield,targets=[target.ref]);self.assertIsNone(self.resolve(session))
        self.assertEqual(1,session.state.cards[target.object_id].regeneration_shields)
        self.cast(session,'A',source,targets=[target.ref]);session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertIn(target.object_id,session.state.players['D'].zones['graveyard']);self.assertEqual(1,len(self.tokens(session,'C')))
        token=self.tokens(session,'C')[0];self.assertEqual('C',token.owner)
        self.assertEqual((3,3),(session.engine._numeric_stat(token.object_id,'power'),session.engine._numeric_stat(token.object_id,'toughness')))
        self.assertIn('Frog',session.engine._effective_card_data(token)['type_line']);self.assertIn('Lizard',session.engine._effective_card_data(token)['type_line'])
        self.finish(session,private)

    def test_actual_incubation_privately_selects_a_creature_or_declines_then_bottoms_the_remainder(self):
        for select in (False,True):
            with self.subTest(select=select):
                session=self.session(999110+select);source=self.add(session.engine,'Incubation // Incongruity',zone='hand')
                creature=self.add(session.engine,'Grizzly Bears',zone='library')
                artifact_creature=self.add(session.engine,'Ornithopter',zone='library')
                land=self.add(session.engine,'Forest',zone='library');instant=self.add(session.engine,'Unsummon',zone='library')
                other_land=self.add(session.engine,'Island',zone='library')
                looked=[creature,artifact_creature,land,instant,other_land];ids={card.object_id for card in looked}
                library=session.state.players['A'].zones['library']
                rest=[oid for oid in library if oid not in ids];library[:]=rest+[card.object_id for card in reversed(looked)]
                private=self.add(session.engine,'Counterspell',seat='C',zone='hand',ref='additional-incubation-private-card')
                self.assert_closed(session,source);self.prepare(session,mana={'G':1,'U':1});self.checkpoint(session)
                self.cast_face(session,source,'Incubation');self.assertEqual(1,session.engine._effective_card_data(session.state.cards[source.object_id])['mana_value'])
                self.assertEqual(1,sum(session.state.players['A'].mana_pool.values()))
                decision=self.resolve(session);self.assertEqual('semantic.choice',decision.kind)
                for seat in 'BCD':
                    packet=packet_without_decision_capability(session.packet('pilot:'+seat,full=True))
                    for card in looked:self.assertNotIn(card.ref,json.dumps(packet))
                session=self.replay(session,load=True)
                self.reject(session,'B',{'action_id':'choose','cards':[artifact_creature.ref]})
                self.reject(session,'A',{'action_id':'choose','cards':[land.ref]})
                result=session.act('pilot:A',{'action_id':'choose','cards':[artifact_creature.ref] if select else []})
                self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(session))
                remaining=ids-({artifact_creature.object_id} if select else set())
                self.assertEqual(remaining,set(session.state.players['A'].zones['library'][:len(remaining)]))
                self.assertEqual(rest,session.state.players['A'].zones['library'][len(remaining):])
                if select:self.assertIn(artifact_creature.object_id,session.state.players['A'].zones['hand'])
                self.finish(session,private)

    def test_actual_incongruity_cast_uses_only_the_instant_half_and_its_controller_result(self):
        session=self.session(999120);source=self.add(session.engine,'Incubation // Incongruity',zone='hand')
        target=self.add(session.engine,'Grizzly Bears',seat='B');target.owner='D'
        private=self.add(session.engine,'Counterspell',seat='C',zone='hand',ref='additional-incongruity-private-card')
        self.assert_closed(session,source);self.prepare(session,mana={'C':1,'G':1,'U':1});self.checkpoint(session)
        before=len(session.state.players['A'].zones['library'])
        self.reject(session,'A',{'action_id':'cast-fuse:'+source.ref,'pay':'auto'})
        self.cast_face(session,source,'Incongruity',[target.ref])
        self.assertEqual(3,session.engine._effective_card_data(session.state.cards[source.object_id])['mana_value'])
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertIn(target.object_id,session.state.players['D'].zones['exile'])
        self.assertEqual(before,len(session.state.players['A'].zones['library']));self.assertEqual(1,len(self.tokens(session,'B')))
        self.finish(session,private)

    def test_actual_sawtusk_mutate_inherits_the_trigger_without_a_new_entry(self):
        session=self.session(999130);source=self.add(session.engine,'Sawtusk Demolisher',zone='hand')
        host=self.add(session.engine,'Grizzly Bears');human=self.add(session.engine,'Elite Vanguard')
        victim=self.add(session.engine,'Sol Ring',seat='B');warden=self.add(session.engine,'Soul Warden',seat='B')
        private=self.add(session.engine,'Counterspell',seat='C',zone='hand',ref='additional-sawtusk-private-card')
        self.assert_closed(session,source);self.assert_closed(session,warden)
        self.prepare(session,mana={'C':3,'G':1});self.checkpoint(session)
        identity=host.logical_object_id;entered=host.entered_battlefield_turn_sequence
        self.mutate(session,source,host,boundaries=(human,));session=self.choose_merge(session,'bottom',load=True)
        retained=session.state.cards[host.object_id]
        self.assertEqual((identity,entered),(retained.logical_object_id,retained.entered_battlefield_turn_sequence))
        self.assertEqual(1,retained.permanent_composition.topology.mutation_count)
        self.assertIn('trample',session.engine._combat_keywords(retained));self.assertEqual(40,session.state.players['B'].life)
        self.until(session,lambda s:s.state.pending_decision is not None and s.state.pending_decision.kind=='semantic.target')
        self.reject(session,'A',{'action_id':'choose','targets':[retained.ref]})
        self.target(session,'A',[victim.ref]);session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual('graveyard',session.state.cards[victim.object_id].zone);self.assertEqual(1,len(self.tokens(session,'B')))
        self.assertEqual(41,session.state.players['B'].life);self.assertEqual(identity,session.state.cards[host.object_id].logical_object_id)
        self.finish(session,private)

    def test_actual_crumble_uses_departure_controller_and_the_artifacts_mana_value(self):
        session=self.session(999140);source=self.add(session.engine,'Crumble',zone='hand')
        target=self.add(session.engine,'Myr Enforcer',seat='C');target.owner='D';response=self.control_response(session,target)
        private=self.add(session.engine,'Counterspell',seat='C',zone='hand',ref='additional-crumble-private-card')
        self.assert_closed(session,source);self.prepare(session,mana={'G':1});self.checkpoint(session)
        self.cast(session,'A',source,targets=[target.ref]);self.cast(session,'B',response,targets=[target.ref]);self.resolve_top(session)
        self.assertEqual('B',session.state.cards[target.object_id].controller)
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertIn(target.object_id,session.state.players['D'].zones['graveyard'])
        self.assertEqual([40,47,40,40],[session.state.players[seat].life for seat in 'ABCD']);self.finish(session,private)
