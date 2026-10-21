from __future__ import annotations

"""CR 107.3a: a known additional price can supply the stack's declared X."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_announced_hand_discard_costs as controls
import test_attached_protection_exceptions as combat_controls
from quorune.rules.evidence_storage import read_evidence_document


class CostDefinedXTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=controls.AnnouncedHandDiscardCostTests.session
    add=controls.AnnouncedHandDiscardCostTests.add
    prepare=controls.AnnouncedHandDiscardCostTests.prepare
    checkpoint=controls.AnnouncedHandDiscardCostTests.checkpoint
    resolve=controls.AnnouncedHandDiscardCostTests.resolve
    replay=controls.AnnouncedHandDiscardCostTests.replay
    binding=controls.AnnouncedHandDiscardCostTests.binding
    priority=controls.AnnouncedHandDiscardCostTests.priority
    cast=controls.AnnouncedHandDiscardCostTests.cast
    actions=controls.AnnouncedHandDiscardCostTests.actions
    private_replay=controls.AnnouncedHandDiscardCostTests.private_replay
    top=controls.AnnouncedHandDiscardCostTests.top
    assert_hand_private=controls.AnnouncedHandDiscardCostTests.assert_hand_private
    advance=combat_controls.AttachedProtectionExceptionTests.advance

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'cost-defined-x.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/attached-protection-cards.json',
            ROOT/'tests/fixtures/cost-defined-x-cards.json',ROOT/'tests/fixtures/announced-hand-discard-cards.json',
            ROOT/'tests/fixtures/announced-x-additional-cost-cards.json',ROOT/'tests/fixtures/announced-target-cardinality-cards.json',
            ROOT/'tests/fixtures/entry-copy-cards.json',ROOT/'tests/fixtures/fixed-stack-spell-copy-cards.json',
            ROOT/'tests/fixtures/targeted-spell-cost-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Cost defined X',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def test_actual_turbulent_dreams_uses_hand_price_x_without_adding_x_to_mana(self):
        session=self.session(750800);source=self.add(session.engine,'Turbulent Dreams',zone='hand')
        costs=[self.add(session.engine,name,zone='hand',ref='turbulent-price-'+str(i)) for i,name in enumerate(('Grizzly Bears','Humble'))]
        targets=[self.add(session.engine,'Grizzly Bears',seat='B'),self.add(session.engine,'Sol Ring',seat='C')]
        land=self.add(session.engine,'Generic Bound Plains',seat='D',ref='noneligible-land')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.assertTrue(self.binding(session,source)['strict_capability_ready'],'Complete original Turbulent Dreams must admit')
        self.prepare(session,mana={'U':2});self.checkpoint(session)
        command={'action_id':'cast:'+source.ref,'x':2,'discard_cards':[c.ref for c in costs],
            'targets':[c.ref for c in targets],'pay':'manual','payment':{'U':2}}
        before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:A',{**command,'targets':[targets[0].ref,land.ref]}).ok)
        self.assertEqual(before,authoritative_state_hash(session.state))
        action=next((r for r in self.actions(session) if r['id']==command['action_id']),None)
        self.assertIsNotNone(action,'The ordinary cost-defined X cast must be offered')
        self.assertTrue({c.ref for c in targets}<=set(action['target_schema']['legal_refs']))
        self.assertNotIn(land.ref,action['target_schema']['legal_refs'])
        normal=next(r for r in action['cost_options'] if r['id']=='normal')
        self.assertEqual(2,normal['choice_schema']['x']['maximum'])
        result=session.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        self.assertEqual(2,session.state.stack[-1].x_value);self.assertEqual(0,sum(session.state.players['A'].mana_pool.values()))
        self.assertTrue(all(session.state.cards[c.object_id].zone=='graveyard' for c in costs))
        self.assertTrue(all(session.state.cards[c.object_id].zone=='battlefield' for c in targets))
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertTrue(all(session.state.cards[c.object_id].zone=='hand' for c in targets))
        self.assertEqual('battlefield',session.state.cards[land.object_id].zone);self.private_replay(session,private)

    def test_actual_sickening_dreams_uses_price_x_for_nontarget_damage_to_every_player(self):
        session=self.session(750810);source=self.add(session.engine,'Sickening Dreams',zone='hand')
        costs=[self.add(session.engine,name,zone='hand',ref='sickening-price-'+str(i)) for i,name in enumerate(('Grizzly Bears','Humble'))]
        bodies=[self.add(session.engine,'Grizzly Bears',seat=seat,ref='mass-body-'+seat) for seat in 'ABC']
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.assertTrue(self.binding(session,source)['strict_capability_ready'],'Complete original Sickening Dreams must admit')
        self.prepare(session,mana={'B':1,'C':1});self.checkpoint(session)
        result=session.act('pilot:A',{'action_id':'cast:'+source.ref,'x':2,'discard_cards':[c.ref for c in costs],
            'pay':'manual','payment':{'B':1,'C':1}});self.assertTrue(result.ok,result.summary)
        self.assertEqual(2,session.state.stack[-1].x_value)
        self.assertTrue(all(session.state.cards[c.object_id].zone=='graveyard' for c in costs))
        self.assertTrue(all(session.state.cards[c.object_id].zone=='battlefield' for c in bodies))
        self.assertEqual([40]*4,[session.state.players[seat].life for seat in 'ABCD'])
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual([38]*4,[session.state.players[seat].life for seat in 'ABCD'])
        self.assertTrue(all(session.state.cards[c.object_id].zone=='graveyard' for c in bodies))
        self.assertEqual(0,sum(session.state.players['A'].mana_pool.values()));self.private_replay(session,private)

    def test_zero_cost_defined_x_is_legal_without_resources_and_does_no_damage(self):
        for index,(name,mana,target_kind) in enumerate((('Turbulent Dreams',{'U':2},None),('Sickening Dreams',{'B':1,'C':1},None),
                ('Harvest Pyre',{'R':1,'C':1},'creature'),('Haunting Misery',{'B':2,'C':1},'player'),
                ('Restless Dreams',{'B':1},None),('Vengeful Dreams',{'W':2},None))):
            with self.subTest(name=name):
                session=self.session(750820+index);source=self.add(session.engine,name,zone='hand')
                for oid in tuple(session.state.players['A'].zones['hand']):
                    if oid!=source.object_id:session.engine.move_card(oid,'library',reason='fixture zero price boundary')
                body=self.add(session.engine,'Grizzly Bears',seat='B')
                private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
                self.assertTrue(self.binding(session,source)['strict_capability_ready'])
                self.prepare(session,mana=mana);self.checkpoint(session)
                command={'action_id':'cast:'+source.ref,'x':0,'pay':'manual','payment':mana}
                if target_kind:command['targets']=[body.ref if target_kind=='creature' else 'B']
                result=session.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
                self.assertEqual(0,session.state.stack[-1].x_value)
                session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
                self.assertEqual([40]*4,[session.state.players[seat].life for seat in 'ABCD'])
                self.assertEqual(('battlefield',0),(session.state.cards[body.object_id].zone,session.state.cards[body.object_id].marked_damage))
                self.assertEqual('graveyard',session.state.cards[source.object_id].zone);self.private_replay(session,private)

    def test_actual_harvest_pyre_exiles_declared_graveyard_cards_before_creature_damage(self):
        session=self.session(750811);source=self.add(session.engine,'Harvest Pyre',zone='hand')
        costs=[self.add(session.engine,name,zone='graveyard',ref='pyre-price-'+str(i)) for i,name in enumerate(('Grizzly Bears','Humble'))]
        target=self.add(session.engine,'Grizzly Bears',seat='B');private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.assertTrue(self.binding(session,source)['strict_capability_ready'])
        self.prepare(session,mana={'R':1,'C':1});self.checkpoint(session)
        result=session.act('pilot:A',{'action_id':'cast:'+source.ref,'x':2,'exile_cards':[c.ref for c in costs],
            'targets':[target.ref],'pay':'manual','payment':{'R':1,'C':1}});self.assertTrue(result.ok,result.summary)
        self.assertEqual(2,session.state.stack[-1].x_value)
        self.assertTrue(all(session.state.cards[c.object_id].zone=='exile' for c in costs))
        self.assertEqual(('battlefield',0),(session.state.cards[target.object_id].zone,session.state.cards[target.object_id].marked_damage))
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual('graveyard',session.state.cards[target.object_id].zone)
        self.assertEqual(0,sum(session.state.players['A'].mana_pool.values()));self.private_replay(session,private)

    def test_actual_haunting_misery_uses_creature_only_price_and_player_or_loyalty_target(self):
        for index,walker_target in enumerate((False,True)):
            with self.subTest(walker_target=walker_target):
                session=self.session(750812+index);source=self.add(session.engine,'Haunting Misery',zone='hand')
                costs=[self.add(session.engine,'Grizzly Bears',zone='graveyard',ref='misery-price-'+str(i)) for i in range(2)]
                invalid=self.add(session.engine,'Humble',zone='graveyard');body=self.add(session.engine,'Grizzly Bears',seat='B')
                walker=self.add(session.engine,'Generic Cost X Loyalty Control',seat='C');walker.counters['loyalty']=3
                private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
                self.assertTrue(self.binding(session,source)['strict_capability_ready'])
                self.prepare(session,mana={'B':2,'C':1});self.checkpoint(session)
                command={'action_id':'cast:'+source.ref,'x':2,'exile_cards':[c.ref for c in costs],
                    'targets':[walker.ref if walker_target else 'B'],'pay':'manual','payment':{'B':2,'C':1}}
                before=authoritative_state_hash(session.state)
                for change in ({'exile_cards':[costs[0].ref,invalid.ref]},{'targets':[body.ref]}):
                    self.assertFalse(session.act('pilot:A',{**command,**change}).ok);self.assertEqual(before,authoritative_state_hash(session.state))
                result=session.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
                self.assertEqual(2,session.state.stack[-1].x_value)
                self.assertTrue(all(session.state.cards[c.object_id].zone=='exile' for c in costs))
                session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
                self.assertEqual(40 if walker_target else 38,session.state.players['B'].life)
                self.assertEqual(1 if walker_target else 3,session.state.cards[walker.object_id].counters['loyalty'])
                self.assertEqual('battlefield',session.state.cards[body.object_id].zone)
                self.assertEqual(0,sum(session.state.players['A'].mana_pool.values()));self.private_replay(session,private)

    def test_actual_restless_dreams_chooses_graveyard_targets_before_hand_discards(self):
        session=self.session(750814);source=self.add(session.engine,'Restless Dreams',zone='hand')
        costs=[self.add(session.engine,name,zone='hand',ref='restless-price-'+str(i)) for i,name in enumerate(('Grizzly Bears','Humble'))]
        targets=[self.add(session.engine,'Grizzly Bears',zone='graveyard',ref='restless-target-'+str(i)) for i in range(2)]
        foreign=self.add(session.engine,'Grizzly Bears',seat='B',zone='graveyard');private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.assertTrue(self.binding(session,source)['strict_capability_ready'])
        self.prepare(session,mana={'B':1});self.checkpoint(session)
        command={'action_id':'cast:'+source.ref,'x':2,'discard_cards':[c.ref for c in costs],
            'targets':[c.ref for c in targets],'pay':'manual','payment':{'B':1}}
        before=authoritative_state_hash(session.state)
        for change in ({'targets':[targets[0].ref,costs[0].ref]},{'targets':[targets[0].ref,foreign.ref]}):
            self.assertFalse(session.act('pilot:A',{**command,**change}).ok);self.assertEqual(before,authoritative_state_hash(session.state))
        result=session.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        self.assertEqual(2,session.state.stack[-1].x_value)
        self.assertTrue(all(session.state.cards[c.object_id].zone=='graveyard' for c in costs))
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertTrue(all(session.state.cards[c.object_id].zone=='hand' for c in targets))
        self.assertEqual('graveyard',session.state.cards[foreign.object_id].zone)
        returned={c.object_id for c in targets}
        self.assert_hand_private(session,'A',[oid for oid in session.state.players['A'].zones['hand'] if oid not in returned])
        for viewer in 'BCD':
            known=session.packet('pilot:'+viewer,full=True)['state']['players']['A'].get('known_hand',())
            self.assertTrue({c.ref for c in targets}<={row['id'] for row in known})
        self.private_replay(session,private)

    def test_actual_vengeful_dreams_casts_out_of_turn_and_only_exiles_attacking_creatures(self):
        session=self.session(750815);source=self.add(session.engine,'Vengeful Dreams',zone='hand')
        price=self.add(session.engine,'Humble',zone='hand')
        lands=[self.add(session.engine,'Generic Bound Plains',ref='vengeful-mana-'+str(i)) for i in range(2)]
        attacker=self.add(session.engine,'Grizzly Bears',seat='B',ref='declared-attacker');attacker.acquired_control_turn_count=-1
        other=self.add(session.engine,'Grizzly Bears',seat='B',ref='nonattacker');other.acquired_control_turn_count=-1
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.assertTrue(self.binding(session,source)['strict_capability_ready'])
        self.prepare(session,actor='B',active='B');self.checkpoint(session)
        self.advance(session,lambda s:s.state.pending_decision is not None and s.state.pending_decision.kind=='combat.attackers')
        result=session.act('pilot:B',{'action_id':'attack','attacks':{attacker.ref:'A'}});self.assertTrue(result.ok,result.summary)
        self.priority(session,'A')
        command={'action_id':'cast:'+source.ref,'x':1,'discard_cards':[price.ref],'targets':[attacker.ref],'pay':'auto'}
        before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:A',{**command,'targets':[other.ref]}).ok)
        self.assertEqual(before,authoritative_state_hash(session.state));self.assertTrue(all(not session.state.cards[c.object_id].tapped for c in lands))
        result=session.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        self.assertEqual(1,session.state.stack[-1].x_value)
        self.assertTrue(all(session.state.cards[c.object_id].tapped for c in lands));self.assertEqual('graveyard',session.state.cards[price.object_id].zone)
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual('exile',session.state.cards[attacker.object_id].zone)
        self.assertEqual('battlefield',session.state.cards[other.object_id].zone);self.private_replay(session,private)

    def test_actual_cascade_keeps_additional_x_positive_when_only_fixed_mana_is_waived(self):
        session=self.session(750830);party=self.add(session.engine,'Boarding Party',zone='hand')
        source=self.add(session.engine,'Turbulent Dreams',zone='library')
        library=session.state.players['A'].zones['library'];library.remove(source.object_id);library.insert(0,source.object_id)
        costs=[self.add(session.engine,name,zone='hand',ref='cascade-price-'+str(i)) for i,name in enumerate(('Grizzly Bears','Humble'))]
        targets=[self.add(session.engine,'Grizzly Bears',seat='B'),self.add(session.engine,'Sol Ring',seat='C')]
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        for card in (party,source):self.assertTrue(self.binding(session,card)['strict_capability_ready'])
        self.prepare(session,mana={'R':1,'C':5});self.checkpoint(session)
        self.cast(session,'A',party,payment={'R':1,'C':5});self.assertEqual('selection.exile_cast',self.resolve(session).kind)
        command={'action_id':'cast','x':2,'discard_cards':[c.ref for c in costs],'targets':[c.ref for c in targets]}
        self.assertTrue(session.packet('pilot:A',full=True)['decision']['ctx']['cast_options'])
        before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:B',command).ok);self.assertEqual(before,authoritative_state_hash(session.state))
        self.assertFalse(session.act('pilot:A',{**command,'discard_cards':[costs[0].ref]}).ok)
        self.assertEqual(before,authoritative_state_hash(session.state))
        result=session.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        self.assertEqual(2,next(item.x_value for item in session.state.stack if item.card_object_id==source.object_id))
        self.assertTrue(all(session.state.cards[c.object_id].zone=='graveyard' for c in costs))
        self.assertEqual(0,sum(session.state.players['A'].mana_pool.values()))
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertTrue(all(session.state.cards[c.object_id].zone=='hand' for c in targets))
        self.assertEqual('battlefield',session.state.cards[party.object_id].zone)
        self.assert_hand_private(session,'A',session.state.players['A'].zones['hand']);self.private_replay(session,private)

    def test_copied_sickening_dreams_keeps_x_without_repeating_the_hand_price(self):
        session=self.session(750840);source=self.add(session.engine,'Sickening Dreams',zone='hand')
        costs=[self.add(session.engine,name,zone='hand',ref='copy-price-'+str(i)) for i,name in enumerate(('Grizzly Bears','Humble'))]
        copy=self.add(session.engine,'Twincast',seat='B',zone='hand')
        body=self.add(session.engine,'Grizzly Bears',seat='D');private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        for card in (source,copy):self.assertTrue(self.binding(session,card)['strict_capability_ready'])
        self.prepare(session,mana={'B':1,'C':1});session.state.players['B'].mana_pool['U']=2;self.checkpoint(session)
        hands={seat:len(session.state.players[seat].zones['hand']) for seat in 'AB'}
        result=session.act('pilot:A',{'action_id':'cast:'+source.ref,'x':2,'discard_cards':[c.ref for c in costs],
            'pay':'manual','payment':{'B':1,'C':1}});self.assertTrue(result.ok,result.summary)
        self.cast(session,'B',copy,[session.state.stack[-1].ref],{'U':2});self.top(session)
        self.assertEqual([38]*4,[session.state.players[seat].life for seat in 'ABCD'])
        self.assertEqual(hands['A']-3,len(session.state.players['A'].zones['hand']))
        self.assertEqual(hands['B']-1,len(session.state.players['B'].zones['hand']))
        self.assertEqual('graveyard',session.state.cards[body.object_id].zone)
        self.assertEqual(2,session.state.stack[-1].x_value)
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual([36]*4,[session.state.players[seat].life for seat in 'ABCD'])
        self.assertTrue(all(session.state.cards[c.object_id].zone=='graveyard' for c in costs));self.private_replay(session,private)

    def test_target_tax_is_extra_mana_and_does_not_change_declared_price_x(self):
        session=self.session(750850);source=self.add(session.engine,'Turbulent Dreams',zone='hand')
        costs=[self.add(session.engine,name,zone='hand',ref='tax-price-'+str(i)) for i,name in enumerate(('Grizzly Bears','Humble'))]
        targets=[self.add(session.engine,'Boreal Elemental',seat='B'),self.add(session.engine,'Sol Ring',seat='C')]
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.assertTrue(self.binding(session,source)['strict_capability_ready'])
        self.prepare(session,mana={'U':2,'C':2});self.checkpoint(session)
        command={'action_id':'cast:'+source.ref,'x':2,'discard_cards':[c.ref for c in costs],'targets':[c.ref for c in targets],
            'pay':'manual','payment':{'U':2,'C':2}}
        before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:A',{**command,'payment':{'U':2}}).ok)
        self.assertEqual(before,authoritative_state_hash(session.state))
        result=session.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        self.assertEqual(2,session.state.stack[-1].x_value);self.assertEqual(0,sum(session.state.players['A'].mana_pool.values()))
        self.assertTrue(all(session.state.cards[c.object_id].zone=='graveyard' for c in costs))
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertTrue(all(session.state.cards[c.object_id].zone=='hand' for c in targets));self.private_replay(session,private)

    def test_countered_and_partially_illegal_cost_defined_targets_keep_the_paid_price(self):
        for index,countered in enumerate((False,True)):
            with self.subTest(countered=countered):
                session=self.session(750860+index);source=self.add(session.engine,'Turbulent Dreams',zone='hand')
                costs=[self.add(session.engine,name,zone='hand',ref='departure-price-'+str(i)) for i,name in enumerate(('Grizzly Bears','Humble'))]
                targets=[self.add(session.engine,'Grizzly Bears',seat='B'),self.add(session.engine,'Sol Ring',seat='C')]
                response=self.add(session.engine,'Counterspell' if countered else 'Boomerang',seat='B',zone='hand')
                private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
                self.prepare(session,mana={'U':2});session.state.players['B'].mana_pool['U']=2;self.checkpoint(session)
                result=session.act('pilot:A',{'action_id':'cast:'+source.ref,'x':2,'discard_cards':[c.ref for c in costs],
                    'targets':[c.ref for c in targets],'pay':'manual','payment':{'U':2}});self.assertTrue(result.ok,result.summary)
                self.cast(session,'B',response,[session.state.stack[-1].ref if countered else targets[0].ref],{'U':2})
                if not countered:
                    self.top(session);self.assertEqual(2,session.state.stack[-1].x_value)
                    self.assertEqual('battlefield',session.state.cards[targets[1].object_id].zone)
                session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
                self.assertTrue(all(session.state.cards[c.object_id].zone=='graveyard' for c in costs))
                self.assertTrue(all(session.state.cards[c.object_id].zone==('battlefield' if countered else 'hand') for c in targets))
                self.assertEqual(0,sum(session.state.players['A'].mana_pool.values()));self.private_replay(session,private)

    def test_unfunded_tax_keeps_the_untaxed_exact_x_quote_bounded_and_available(self):
        session=self.session(750852);source=self.add(session.engine,'Turbulent Dreams',zone='hand')
        price=self.add(session.engine,'Humble',zone='hand');taxed=self.add(session.engine,'Boreal Elemental',seat='B')
        untaxed=self.add(session.engine,'Sol Ring',seat='C');private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,mana={'U':2});self.checkpoint(session)
        action=next((r for r in self.actions(session) if r['id']=='cast:'+source.ref),None)
        self.assertIsNotNone(action)
        normal=next(row for row in action['cost_options'] if row['id']=='normal')
        prices=normal.get('target_costs',{}).get('prices',())
        self.assertTrue(prices,'The offer must retain its affordable target-signature prices')
        self.assertTrue(all(row['maximum_x']<=1 for row in prices),'An untaxed exact-X quote has only one eligible untaxed target')
        before=authoritative_state_hash(session.state)
        bad=session.act('pilot:A',{'action_id':action['id'],'x':1,'discard_cards':[price.ref],
            'targets':[taxed.ref],'pay':'auto'})
        self.assertFalse(bad.ok);self.assertEqual(before,authoritative_state_hash(session.state))
        result=session.act('pilot:A',{'action_id':action['id'],'x':1,'discard_cards':[price.ref],
            'targets':[untaxed.ref],'pay':'manual','payment':{'U':2}});self.assertTrue(result.ok,result.summary)
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual('hand',session.state.cards[untaxed.object_id].zone)
        self.assertEqual('battlefield',session.state.cards[taxed.object_id].zone);self.private_replay(session,private)

    def test_undefined_ambiguous_and_unrepresented_price_x_remains_residual(self):
        from dataclasses import replace
        from test_granted_skulk import record
        from quorune.oracle_ir import compile_oracle_card
        bodies=('Return X target nonland permanents to their owners\' hands.',
            'As an additional cost to cast this spell, discard X cards at random.\nReturn X target nonland permanents to their owners\' hands.',
            'As an additional cost to cast this spell, discard X cards.\nAs an additional cost to cast this spell, pay X life.\nDraw X cards.',
            'As an additional cost to cast this spell, discard X cards.\nDraw X cards, where X is the number of creatures you control.')
        for text in bodies:
            with self.subTest(text=text):
                card=replace(record(text,'Sorcery'),mana_cost='{U}{U}')
                ir=compile_oracle_card(card,capability_registry=self.registry,capability_profile='commander_review')
                self.assertNotEqual('exact',ir.status);self.assertTrue(ir.material_residuals)
        nonordinary=replace(record('As an additional cost to cast this spell, discard X cards.\nReturn X target nonland permanents to their owners\' hands.','Sorcery'),mana_cost='{X}{U/P}')
        self.assertNotEqual('exact',compile_oracle_card(nonordinary,capability_registry=self.registry,capability_profile='commander_review').status)

    def test_price_and_owned_result_are_required_separately_at_promotion_and_runtime(self):
        from dataclasses import replace
        from quorune.compiler.declared_effect_amounts import closed_additional_cost_x_result
        from quorune.compiler.program_generation import _closed_effect_recognizers
        from quorune.card_programs.binding import bind_semantic_program_runtime
        session=self.session(750870);source=self.add(session.engine,'Sickening Dreams',zone='hand')
        cap='quantity_expression.declared_effect_amount'
        program=next(p for p in session.engine.semantics.programs_for_oracle(source.oracle_id) if cap in p.capability_dependencies)
        recognizers=_closed_effect_recognizers()
        self.assertTrue(closed_additional_cost_x_result(program,recognizers=recognizers))
        for schema in ({'additional_costs':[0]},{'additional_costs':[]},{'additional_costs':program.cost_schema['additional_costs'],'unknown':0}):
            self.assertFalse(closed_additional_cost_x_result(replace(program,cost_schema=schema),recognizers=recognizers))
        self.assertFalse(closed_additional_cost_x_result(replace(program,provenance={**program.provenance,'template_id':'unknown+damage-fixed-simultaneous-set-v1'}),recognizers=recognizers))
        self.assertFalse(closed_additional_cost_x_result(replace(program,effects=[{**program.effects[0],'amount':0}]),recognizers=recognizers))
        broken=replace(program,effects=[{**program.effects[0],'amount':{**program.effects[0]['amount'],'coefficient':True}}])
        invalid=bind_semantic_program_runtime(broken,capability_registry=self.registry,profile='commander_review')
        self.assertFalse(invalid['strict']);self.assertTrue(any(b.startswith('runtime_cast_x_amount:invalid:') for b in invalid['blockers']))
        for missing in (cap,'casting.additional_cost.announced_x_hand_discard'):
            deps=tuple(c for c in program.capability_dependencies if c!=missing)
            stripped=replace(program,capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict() if deps else None)
            bound=bind_semantic_program_runtime(stripped,capability_registry=self.registry,profile='commander_review')
            self.assertFalse(bound['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+missing,bound['blockers'])
        target=self.add(session.engine,'Turbulent Dreams',zone='hand');target_cap='target.cardinality.mana_announced_x'
        target_program=next(p for p in session.engine.semantics.programs_for_oracle(target.oracle_id) if target_cap in p.capability_dependencies)
        deps=tuple(c for c in target_program.capability_dependencies if c!=target_cap)
        stripped=replace(target_program,capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict() if deps else None)
        bound=bind_semantic_program_runtime(stripped,capability_registry=self.registry,profile='commander_review')
        self.assertFalse(bound['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+target_cap,bound['blockers'])

    def test_fresh_actual_context_binder_bound_and_result_owner_omissions_are_killed(self):
        from quorune.rules.casting.target_cardinality import bind_target_count_option
        from quorune.rules.casting.targeted_costs import _announced_tax_assignments
        main='test_actual_turbulent_dreams_uses_hand_price_x_without_adding_x_to_mana'
        scalar='test_actual_sickening_dreams_uses_price_x_for_nontarget_damage_to_every_player'
        def missing_binding(program,option,response,*,has_mana_x,has_declared_cost_x=False):
            return bind_target_count_option(program,option,response,has_mana_x=has_mana_x)
        def missing_signature_bound(*args,**kwargs):
            for targets,groups,limit in _announced_tax_assignments(*args,**kwargs):yield targets,groups,999
        cases=(('quorune.compiler.declared_effect_amounts._additional_price_declares_x',lambda *a:False,main),
            ('quorune.rules.casting.target_cardinality.bind_target_count_option',missing_binding,main),
            ('quorune.rules.casting.target_cardinality.target_count_preview_maximum',lambda option,maximum:maximum,main),
            ('quorune.compiler.declared_effect_amounts.closed_additional_cost_x_result',lambda *a,**k:False,scalar),
            ('quorune.card_programs.runtime_requirements.cast_x_runtime_dependencies',lambda *a:(),
             'test_price_and_owned_result_are_required_separately_at_promotion_and_runtime'),
            ('quorune.rules.casting.targeted_costs._announced_tax_assignments',missing_signature_bound,
             'test_unfunded_tax_keeps_the_untaxed_exact_x_quote_bounded_and_available'))
        for owner,mutation,method in cases:
            with patch(owner,mutation):
                with self.assertRaises(AssertionError):getattr(type(self)(method),method)()

    def test_canonically_verified_price_amount_and_target_owner_withdrawals_reject(self):
        import json
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.oracle_ir import compile_oracle_card
        from scripts.update_capability_evidence import build_index,discover_tests
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        declarations=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        known=set(discover_tests(ROOT).values())
        def verified(value):
            registry=CapabilityRegistry(value);index=build_index(registry_value=value,declaration_source=declarations,discovered_test_ids=known)
            registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry));return registry
        for name in ('Turbulent Dreams','Sickening Dreams','Harvest Pyre','Haunting Misery','Restless Dreams','Vengeful Dreams'):
            self.assertEqual('exact',compile_oracle_card(self.db.lookup(name),capability_registry=verified(raw),capability_profile='commander_review').status)
        for name,dependency in (('Sickening Dreams','quantity_expression.declared_effect_amount'),
                ('Turbulent Dreams','target.cardinality.mana_announced_x'),
                ('Restless Dreams','casting.additional_cost.announced_x_hand_discard'),
                ('Harvest Pyre','casting.additional_cost.announced_x_graveyard_exile')):
            changed=json.loads(json.dumps(raw));next(row for row in changed['capabilities'] if row['id']==dependency).update(status='blocked',blockers=['withdrawn known cost-X owner'])
            ir=compile_oracle_card(self.db.lookup(name),capability_registry=verified(changed),capability_profile='commander_review')
            self.assertNotEqual('exact',ir.status);blockers=[b for residual in ir.material_residuals for b in residual.blockers]
            self.assertTrue(any('status:'+dependency+':blocked' in b for b in blockers));self.assertFalse(any('evidence_index:unverified' in b for b in blockers))
