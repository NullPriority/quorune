from __future__ import annotations

"""CR 702.131: a persistent public designation precedes trigger checks."""

from dataclasses import replace
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_granted_skulk import record
import test_bound_effect_programs as actions
import test_self_public_zone_casting as zone_actions


class AscendCompilerTests(unittest.TestCase):
    def test_withdrawn_designation_owner_prevents_keyword_and_condition_promotion(self):
        from quorune.rules.capabilities import CapabilityRegistry
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        next(cap for cap in raw['capabilities'] if cap['id']=='player.designation.citys_blessing').update(
            status='blocked',blockers=['withdrawn'])
        for text in ('Ascend','This creature has flying as long as you have the city\'s blessing.'):
            ir=compile_oracle_card(replace(record(text,'Creature — Human'),keywords=('Ascend',) if text=='Ascend' else ()),
                capability_registry=CapabilityRegistry(raw),capability_profile='commander_review')
            self.assertNotEqual('exact',ir.status)

    def test_designation_is_not_a_counter_and_old_absent_state_remains_unchanged(self):
        from quorune.city_blessing_model import player_has_citys_blessing
        player=SimpleNamespace(stats={},counters={})
        self.assertFalse(player_has_citys_blessing(player));self.assertEqual({},player.stats)
        player.stats['citys_blessing']=True
        self.assertTrue(player_has_citys_blessing(player));self.assertEqual({},player.counters)
        for malformed in (1,'true',None):
            player.stats['citys_blessing']=malformed
            with self.assertRaises(ValueError):player_has_citys_blessing(player)

    def test_original_simple_ascend_condition_cards_and_spell_keyword_are_exact(self):
        registry=load_default_capability_registry()
        with _fixture_database() as db:
            for name in ['Skymarcher Aspirant','Dusk Charger','Spire Winder','Storm Fleet Swashbuckler','Generic Ascend Sorcery']:
                with self.subTest(name=name):
                    ir=compile_oracle_card(db.lookup(name,fuzzy=False),capability_registry=registry,capability_profile='commander_review')
                    self.assertEqual('exact',ir.status,ir.material_residuals)
                    node=next(n for n in ir.faces[0].nodes if n.text.startswith('Ascend'))
                    self.assertIn('player.designation.citys_blessing',node.capability_dependencies)
                    if name=='Generic Ascend Sorcery':
                        self.assertEqual('spell_ability',node.kind)
                        self.assertEqual('ascend',node.effects[0]['op'])
                    else:self.assertEqual('static_ability',node.kind)

    def test_unknown_ascend_parameters_and_unestablished_status_stay_residual(self):
        registry=load_default_capability_registry()
        for text in ('Ascend 2','As long as a player secretly chose the city, this creature has flying.'):
            ir=compile_oracle_card(replace(record(text,'Creature — Human'),keywords=('Ascend',)),
                capability_registry=registry,capability_profile='commander_review')
            self.assertNotEqual('exact',ir.status)


class _fixture_database:
    def __enter__(self):
        self.temp=tempfile.TemporaryDirectory()
        path=Path(self.temp.name)/'ascend-compiler.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/ascend-city-blessing-cards.json'],path)
        self.db=CardDatabase(path);return self.db

    def __exit__(self,*args):self.db.close();self.temp.cleanup()


class AscendRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=actions.BoundEffectProgramRuntimeTests.session
    add=actions.BoundEffectProgramRuntimeTests.add
    checkpoint=actions.BoundEffectProgramRuntimeTests.checkpoint
    resolve=actions.BoundEffectProgramRuntimeTests.resolve
    replay=actions.BoundEffectProgramRuntimeTests.replay
    prepare=zone_actions.SelfPublicZoneRuntimeTests.prepare
    cast=zone_actions.SelfPublicZoneRuntimeTests.cast
    assert_closed=zone_actions.SelfPublicZoneRuntimeTests.assert_closed

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory()
        path=Path(cls.temp.name)/'ascend-native.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/event-player-subject-cards.json',
            ROOT/'tests/fixtures/fixed-damage-source-lki-cards.json',
            ROOT/'tests/fixtures/ascend-city-blessing-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Ascend witness',[DeckEntry('Generic Bound Commander',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def lands(self,engine,count,seat='A'):
        return [self.add(engine,'Generic Bound Plains',seat=seat,ref=f'ascend-{seat}-land-{i}') for i in range(count)]

    def blessed(self,session,seat='A'):
        return session.state.players[seat].stats.get('citys_blessing',False)

    def test_actual_aspirant_tenth_entry_seals_flying_before_zone_trigger_discovery(self):
        import quorune.zone_transitions as zones
        session=self.session(408000);engine=session.engine
        self.lands(engine,9)
        source=self.add(engine,'Skymarcher Aspirant',zone='hand',ref='actual-ascend-aspirant')
        self.prepare(session,mana={'W':1});self.checkpoint(session)
        self.assertFalse(self.blessed(session));seen=[];original=zones.dispatch_zone_change_occurrence
        def observe(host,occurrence,card,**kwargs):
            if card.object_id==source.object_id and occurrence.destination=='battlefield':
                seen.append((self.blessed(session),[k.casefold() for k in occurrence.current_characteristics.get('keywords',())]))
            return original(host,occurrence,card,**kwargs)
        with patch.object(zones,'dispatch_zone_change_occurrence',side_effect=observe):
            self.cast(session,'A',source);self.resolve(session)
        self.assertEqual(1,len(seen));self.assertTrue(seen[0][0]);self.assertIn('flying',seen[0][1])
        self.assert_closed(session,session.state.cards[source.object_id])
        self.assertTrue(session.packet('pilot:D',full=True)['state']['players']['A']['citys_blessing'])
        self.replay(session,load=True)

    def test_tenth_zero_toughness_token_awards_before_sba_and_source_departure_does_not_revoke(self):
        session=self.session(408010);engine=session.engine
        self.lands(engine,8)
        source=self.add(engine,'Skymarcher Aspirant',ref='ascend-existing-aspirant')
        token=self.add(engine,'Generic Ascend Empty Token',zone='hand',ref='ascend-brief-tenth-permanent')
        kill=self.add(engine,'Generic Damage Source Kill',zone='hand',ref='ascend-source-removal')
        self.prepare(session);self.checkpoint(session)
        self.assertFalse(self.blessed(session));self.cast(session,'A',token);self.resolve(session)
        self.assertTrue(self.blessed(session))
        self.assertEqual(9,len(session.state.players['A'].zones['battlefield']))
        self.assert_closed(session,session.state.cards[source.object_id])
        self.cast(session,'A',kill,[source.ref]);self.resolve(session)
        self.assertEqual('graveyard',session.state.cards[source.object_id].zone)
        self.assertTrue(self.blessed(session));self.assertNotIn('citys_blessing',session.state.players['A'].counters)
        self.replay(session,load=True)

    def test_spell_ascend_waits_for_resolution_and_countered_spell_does_not_award(self):
        for counter in (False,True):
            with self.subTest(counter=counter):
                session=self.session(408020+counter);engine=session.engine
                self.lands(engine,10)
                source=self.add(engine,'Generic Ascend Sorcery',zone='hand',ref='ascend-spell-source')
                denial=self.add(engine,'Counterspell',seat='B',zone='hand',ref='ascend-spell-counter')
                private=self.add(engine,'Generic Bound Growth',seat='D',zone='hand',ref='ascend-private-hand')
                self.assert_closed(session,source);self.prepare(session);engine.state.players['B'].mana_pool['U']=2
                self.checkpoint(session);hand=len(session.state.players['A'].zones['hand'])
                self.assertFalse(self.blessed(session));self.cast(session,'A',source)
                self.assertFalse(self.blessed(session));spell=session.state.stack[-1]
                if counter:self.cast(session,'B',denial,[spell.ref])
                session=self.replay(session,load=True);self.resolve(session)
                self.assertEqual(not counter,self.blessed(session))
                self.assertEqual(hand-1+(not counter),len(session.state.players['A'].zones['hand']))
                for viewer in 'ABC':self.assertNotIn(private.ref,json.dumps(session.packet('pilot:'+viewer,full=True)))
                if not counter:
                    drawn=session.state.cards[session.state.players['A'].zones['hand'][-1]].ref
                    for viewer in 'BCD':self.assertNotIn(drawn,json.dumps(session.packet('pilot:'+viewer,full=True)))
                self.replay(session,load=True)

    def test_actual_ability_loss_prevents_award_until_real_source_restoration(self):
        session=self.session(408030);engine=session.engine
        self.lands(engine,9)
        suppression=self.add(engine,'Dress Down',seat='D',ref='ascend-live-ability-suppression')
        source=self.add(engine,'Skymarcher Aspirant',zone='hand',ref='ascend-suppressed-source')
        release=self.add(engine,'Disenchant',zone='hand',ref='ascend-remove-suppression')
        self.prepare(session,mana={'C':1,'W':2});self.checkpoint(session)
        self.cast(session,'A',source);self.resolve(session)
        self.assertFalse(self.blessed(session))
        self.cast(session,'A',release,[suppression.ref]);self.resolve(session)
        self.assertTrue(self.blessed(session))
        self.assertIn('flying',[k.casefold() for k in session.engine._effective_card_data(session.state.cards[source.object_id])['keywords']])
        self.replay(session,load=True)

    def test_real_control_change_awards_current_controller_and_allows_multiple_persistent_holders(self):
        session=self.session(408040);engine=session.engine
        self.lands(engine,8);self.lands(engine,9,'D')
        original=self.add(engine,'Skymarcher Aspirant',ref='ascend-before-control')
        control=self.add(engine,'Generic Event Subject Control',seat='D',zone='hand',ref='ascend-real-control-change')
        bodies=[self.add(engine,'Generic Bound Body',zone='hand',ref='ascend-no-source-body-'+str(i)) for i in range(2)]
        fresh=self.add(engine,'Skymarcher Aspirant',zone='hand',ref='ascend-second-current-source')
        bounce=self.add(engine,'Generic Damage Source Bounce',zone='hand',ref='ascend-return-stolen-source')
        self.prepare(session,mana={'W':3,'U':1});engine.state.players['D'].mana_pool['W']=1
        self.checkpoint(session);self.assertFalse(self.blessed(session));self.assertFalse(self.blessed(session,'D'))
        self.cast(session,'D',control,[original.ref]);self.resolve(session)
        self.assertTrue(self.blessed(session,'D'));self.assertFalse(self.blessed(session))
        self.assertIn('flying',[k.casefold() for k in session.engine._effective_card_data(session.state.cards[original.object_id])['keywords']])
        for body in bodies:self.cast(session,'A',body);self.resolve(session)
        self.assertEqual(10,len(session.state.players['A'].zones['battlefield']));self.assertFalse(self.blessed(session))
        self.cast(session,'A',fresh);self.resolve(session)
        self.assertTrue(self.blessed(session));self.assertTrue(self.blessed(session,'D'))
        self.cast(session,'A',bounce,[original.ref]);self.resolve(session)
        self.assertEqual(9,len(session.state.players['D'].zones['battlefield']));self.assertTrue(self.blessed(session,'D'))
        packet=session.packet('pilot:B',full=True)['state']['players']
        self.assertTrue(packet['A']['citys_blessing']);self.assertTrue(packet['D']['citys_blessing'])
        self.replay(session,load=True)

    def test_actual_clone_copied_ascend_uses_receiver_controller_before_entry_facts(self):
        session=self.session(408050);engine=session.engine
        self.lands(engine,9)
        original=self.add(engine,'Skymarcher Aspirant',seat='B',ref='ascend-public-copy-donor')
        source=self.add(engine,'Clone',zone='hand',ref='ascend-real-clone')
        self.assert_closed(session,source);self.assert_closed(session,original)
        self.prepare(session,mana={'C':3,'U':1});self.checkpoint(session)
        self.cast(session,'A',source);self.assertEqual('replacement.order',self.resolve(session).kind)
        self.assertFalse(self.blessed(session));self.assertFalse(self.blessed(session,'B'))
        session=self.replay(session,load=True)
        options=session.packet('pilot:A',full=True)['decision']['ctx']['options']
        chosen=next(row for row in options if original.ref in row['id'])
        before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:C',{'action_id':'choose','replacement':chosen['id']}).ok)
        self.assertEqual(before,authoritative_state_hash(session.state))
        accepted=session.act('pilot:A',{'action_id':'choose','replacement':chosen['id']})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        self.assertTrue(self.blessed(session));self.assertFalse(self.blessed(session,'B'))
        self.assertEqual(source.oracle_id,session.state.cards[source.object_id].oracle_id)
        self.assertIn('flying',[k.casefold() for k in session.engine._effective_card_data(session.state.cards[source.object_id])['keywords']])
        self.replay(session,load=True)

    def test_phased_out_source_cannot_award_despite_ten_other_permanents(self):
        session=self.session(408060);engine=session.engine
        self.lands(engine,10)
        source=self.add(engine,'Skymarcher Aspirant',ref='ascend-phased-source');source.phased_out=True
        spell=self.add(engine,'Generic Ascend Draw',zone='hand',ref='ascend-no-active-source-draw')
        self.assert_closed(session,source);self.prepare(session);self.checkpoint(session)
        self.assertFalse(self.blessed(session));self.cast(session,'A',spell);self.resolve(session)
        self.assertFalse(self.blessed(session))
        self.assertNotIn('citys_blessing',session.state.players['A'].stats)
        self.replay(session,load=True)


if __name__=='__main__':unittest.main()
