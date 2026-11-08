"""CR508.5: actual combat results reach the sealed defending player."""
import json,tempfile,unittest
from pathlib import Path
from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.attachments import attach_objects
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class CombatDefendingPlayerNativeTests(InheritedNativeFixture,unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'combat-defending-player.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/combat-defending-player-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Defending player results',[DeckEntry('Isamaru, Hound of Konda',1,'commander'),DeckEntry('Generic Bound Plains',14)],['Isamaru, Hound of Konda'])

    def attach(self, session, source, bearer):
        attach_objects(session.state.cards, source, bearer, source_timestamp=source.zone_timestamp,
                       players=session.state.players)

    def test_actual_aqueous_form_scries_for_the_aura_controller(self):
        s = self.session(508509702)
        aura = self.add(s.engine, 'Aqueous Form', seat='B')
        bearer = self.add(s.engine, 'Grizzly Bears', ref='aqueous-bearer')
        other = self.add(s.engine, 'Grizzly Bears', ref='aqueous-other')
        for card in (bearer, other):
            card.acquired_control_turn_count = -1
        self.attach(s, aura, bearer)
        self.assert_closed(s, aura)
        self.prepare(s)
        self.checkpoint(s)
        libraries = {seat: list(s.state.players[seat].zones['library']) for seat in 'ABCD'}
        top = s.state.cards[libraries['B'][-1]]
        self.attack(s, [(bearer, 'D'), (other, 'C')])
        self.assertEqual(1, self.trigger_count(s, aura))
        pending = self.resolve(s)
        self.assertEqual(('semantic.choice', ['B']), (pending.kind, list(pending.actors)))
        for seat in 'ACD':
            self.assertNotIn(top.ref, json.dumps(s.packet(f'pilot:{seat}', full=True)))
        command = {'action_id': 'choose', 'cards': {'top': [], 'bottom': [top.ref]}}
        before = authoritative_state_hash(s.state)
        self.assertFalse(s.act('pilot:A', command).ok)
        self.assertEqual(before, authoritative_state_hash(s.state))
        s = self.replay(s, load=True)
        result = s.act('pilot:B', command)
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(top.object_id, s.state.players['B'].zones['library'][0])
        for seat in 'ACD':
            self.assertEqual(libraries[seat], s.state.players[seat].zones['library'])
        self.replay(s, load=True)

    def test_actual_armory_and_seismic_count_the_original_attacker_at_resolution(self):
        s = self.session(508509703)
        armory = self.add(s.engine, 'Armory of Iroas')
        aura = self.add(s.engine, 'Seismic Tutelage', seat='B')
        bearer = self.add(s.engine, 'Grizzly Bears', ref='counter-bearer')
        other = self.add(s.engine, 'Grizzly Bears', ref='counter-other')
        veil = self.add(s.engine, 'Snakeskin Veil', zone='hand')
        self.add(s.engine, 'Forest')
        bearer.counters['+1/+1'] = 1
        for card in (bearer, other):
            card.acquired_control_turn_count = -1
        self.attach(s, aura, bearer)
        for card in (armory, aura):
            self.assert_closed(s, card)
        self.prepare(s, mana={'C': 2})
        self.checkpoint(s)
        actions = s.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
        equip = next(a for a in actions if a['id'].startswith(f'activate:{armory.ref}:'))
        result = s.act('pilot:A', {'action_id': equip['id'], 'targets': [bearer.ref], 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(s))
        self.assertEqual(bearer.object_id, s.state.cards[armory.object_id].attached_to)
        self.attack(s, [(bearer, 'D'), (other, 'C')])
        self.assertEqual([armory.object_id, aura.object_id], [item.source_object_id for item in s.state.stack])
        self.cast(s, 'A', veil, [bearer.ref])
        self.resolve_top(s)
        self.assertEqual(2, s.state.cards[bearer.object_id].counters['+1/+1'])
        s = self.replay(s, load=True)
        self.assertIsNone(self.resolve(s))
        self.assertEqual(5, s.state.cards[bearer.object_id].counters['+1/+1'])
        self.assertEqual({}, s.state.cards[other.object_id].counters)
        self.replay(s, load=True)

    def test_actual_bone_sabres_cannot_put_counters_on_a_returned_attacker(self):
        s = self.session(508509704)
        sabres = self.add(s.engine, 'Bone Sabres')
        bearer = self.add(s.engine, 'Grizzly Bears')
        bearer.acquired_control_turn_count = -1
        blink = self.add(s.engine, 'Cloudshift', zone='hand')
        self.add(s.engine, 'Plains')
        self.attach(s, sabres, bearer)
        self.assert_closed(s, sabres)
        self.prepare(s)
        self.checkpoint(s)
        identity = bearer.logical_object_id
        self.attack(s, [(bearer, 'B')])
        self.assertEqual(1, self.trigger_count(s, sabres))
        self.cast(s, 'A', blink, [bearer.ref])
        s = self.replay(s, load=True)
        self.assertIsNone(self.resolve(s))
        self.assertNotEqual(identity, s.state.cards[bearer.object_id].logical_object_id)
        self.assertIsNone(s.state.cards[sabres.object_id].attached_to)
        self.assertEqual({}, s.state.cards[bearer.object_id].counters)
        self.replay(s, load=True)

    def test_actual_bow_targets_the_attacked_planeswalkers_controller(self):
        s = self.session(508509705)
        bow = self.add(s.engine, 'Heart-Piercer Bow', seat='B')
        bearer = self.add(s.engine, 'Shivan Dragon')
        bearer.acquired_control_turn_count = -1
        walker = self.add(s.engine, 'Ral, Caller of Storms', seat='D')
        walker.counters['loyalty'] = 5
        legal = self.add(s.engine, 'Kor Firewalker', seat='D')
        protected = self.add(s.engine, 'Tel-Jilad Chosen', seat='D')
        unrelated = self.add(s.engine, 'Grizzly Bears', seat='C')
        removal = self.add(s.engine, 'Disenchant', seat='D', zone='hand')
        self.add(s.engine, 'Plains', seat='D')
        self.add(s.engine, 'Sol Ring', seat='D')
        self.attach(s, bow, bearer)
        self.assert_closed(s, bow)
        self.prepare(s)
        self.checkpoint(s)
        self.attack(s, [(bearer, walker.ref)])
        pending = s.state.pending_decision
        self.assertEqual(('semantic.target', ['B']), (pending.kind, list(pending.actors)))
        schema = s.packet('pilot:B', full=True)['decision']['ctx']['target_schema']
        self.assertEqual([legal.ref], schema['legal_refs'])
        before = authoritative_state_hash(s.state)
        for seat, target in (('A', legal), ('B', protected), ('B', unrelated)):
            self.assertFalse(s.act(f'pilot:{seat}', {'action_id': 'choose', 'targets': [target.ref]}).ok)
            self.assertEqual(before, authoritative_state_hash(s.state))
        result = s.act('pilot:B', {'action_id': 'choose', 'targets': [legal.ref]})
        self.assertTrue(result.ok, result.summary)
        self.cast(s, 'D', removal, [bow.ref])
        self.resolve_top(s)
        self.assertEqual('graveyard', s.state.cards[bow.object_id].zone)
        s = self.replay(s, load=True)
        self.assertIsNone(self.resolve(s))
        self.assertEqual(1, s.state.cards[legal.object_id].marked_damage)
        self.assertEqual(0, s.state.cards[protected.object_id].marked_damage)
        self.replay(s, load=True)

    def test_actual_ring_and_talisman_use_the_single_declared_attacker(self):
        for alone in (False, True):
            with self.subTest(alone=alone):
                s = self.session(508509706 + alone)
                ring = self.add(s.engine, "Bilbo's Ring", seat='B')
                talisman = self.add(s.engine, "Reaper's Talisman", seat='B')
                bearer = self.add(s.engine, 'Grizzly Bears', ref='alone-bearer')
                other = self.add(s.engine, 'Grizzly Bears', ref='alone-other')
                for card in (bearer, other):
                    card.acquired_control_turn_count = -1
                for card in (ring, talisman):
                    self.attach(s, card, bearer)
                    self.assert_closed(s, card)
                self.prepare(s)
                self.checkpoint(s)
                top = s.state.cards[s.state.players['B'].zones['library'][-1]]
                self.attack(s, [(bearer, 'D')] if alone else [(bearer, 'D'), (other, 'C')])
                self.assertEqual(int(alone), self.trigger_count(s, ring))
                self.assertEqual(1 + int(alone), self.trigger_count(s, talisman))
                self.assertIsNone(self.resolve(s))
                self.assertIn('deathtouch', s.engine._combat_keywords(s.state.cards[bearer.object_id]))
                self.assertNotIn('deathtouch', s.engine._combat_keywords(s.state.cards[other.object_id]))
                self.assertEqual((40, 41, 40, 38) if alone else (40, 40, 40, 40),
                                 tuple(s.state.players[seat].life for seat in 'ABCD'))
                self.assertEqual('hand' if alone else 'library', s.state.cards[top.object_id].zone)
                for seat in 'ACD':
                    self.assertNotIn(top.ref, json.dumps(s.packet(f'pilot:{seat}', full=True)))
                self.replay(s, load=True)

    def test_actual_thunderous_might_reads_controller_devotion_after_aura_departure(self):
        s = self.session(508509708)
        aura = self.add(s.engine, 'Thunderous Might', seat='B')
        bearer = self.add(s.engine, 'Grizzly Bears')
        bearer.acquired_control_turn_count = -1
        other = self.add(s.engine, 'Shivan Dragon', seat='B')
        removal = self.add(s.engine, 'Disenchant', zone='hand')
        self.add(s.engine, 'Plains')
        self.add(s.engine, 'Sol Ring')
        self.attach(s, aura, bearer)
        self.assert_closed(s, aura)
        self.prepare(s)
        self.checkpoint(s)
        self.attack(s, [(bearer, 'D')])
        self.assertEqual(1, self.trigger_count(s, aura))
        self.cast(s, 'A', removal, [aura.ref])
        self.resolve_top(s)
        self.assertEqual('graveyard', s.state.cards[aura.object_id].zone)
        s = self.replay(s, load=True)
        self.assertIsNone(self.resolve(s))
        self.assertEqual(4, s.engine._numeric_stat(bearer.object_id, 'power'))
        self.assertEqual(5, s.engine._numeric_stat(other.object_id, 'power'))
        self.replay(s, load=True)

    def test_actual_throwing_knife_chooses_target_before_its_optional_source_payment(self):
        for pay in (False, True):
            with self.subTest(pay=pay):
                s = self.session(508509709 + pay)
                knife = self.add(s.engine, 'Throwing Knife', seat='B')
                bearer = self.add(s.engine, 'Grizzly Bears')
                bearer.acquired_control_turn_count = -1
                self.attach(s, knife, bearer)
                self.assert_closed(s, knife)
                self.prepare(s)
                self.checkpoint(s)
                self.attack(s, [(bearer, 'D')])
                self.target(s, 'B', ['C'])
                self.assertEqual('battlefield', s.state.cards[knife.object_id].zone)
                pending = self.resolve(s)
                self.assertEqual(('semantic.choice', ['B']), (pending.kind, list(pending.actors)))
                s = self.replay(s, load=True)
                command = {'action_id': 'choose', 'pay': pay}
                before = authoritative_state_hash(s.state)
                if pay:
                    self.assertFalse(s.act('pilot:B', {**command, 'cards': [bearer.ref]}).ok)
                    self.assertEqual(before, authoritative_state_hash(s.state))
                    command['cards'] = [knife.ref]
                self.assertFalse(s.act('pilot:A', command).ok)
                self.assertEqual(before, authoritative_state_hash(s.state))
                result = s.act('pilot:B', command)
                self.assertTrue(result.ok, result.summary)
                self.assertEqual('graveyard' if pay else 'battlefield', s.state.cards[knife.object_id].zone)
                self.assertEqual(38 if pay else 40, s.state.players['C'].life)
                self.replay(s, load=True)

    def test_actual_sword_damage_and_draw_require_the_same_legal_target(self):
        for blink_target in (False, True):
            with self.subTest(blink_target=blink_target):
                s = self.session(508509712 + blink_target)
                sword = self.add(s.engine, 'Sword of Fire and Ice', seat='B')
                bearer = self.add(s.engine, 'Grizzly Bears', ref='sword-bearer')
                bearer.acquired_control_turn_count = -1
                target = self.add(s.engine, 'Grizzly Bears', seat='D', ref='sword-target')
                blink = self.add(s.engine, 'Cloudshift', seat='D', zone='hand')
                self.add(s.engine, 'Plains', seat='D')
                self.attach(s, sword, bearer)
                self.assert_closed(s, sword)
                self.prepare(s)
                self.checkpoint(s)
                top = s.state.cards[s.state.players['B'].zones['library'][-1]]
                self.attack(s, [(bearer, 'D')])
                self.target(s, 'B', [target.ref if blink_target else 'C'])
                self.assertEqual(36, s.state.players['D'].life)
                if blink_target:
                    self.cast(s, 'D', blink, [target.ref])
                    self.resolve_top(s)
                s = self.replay(s, load=True)
                self.assertIsNone(self.resolve(s))
                self.assertEqual(40 if blink_target else 38, s.state.players['C'].life)
                self.assertEqual(0, s.state.cards[target.object_id].marked_damage)
                self.assertEqual('library' if blink_target else 'hand', s.state.cards[top.object_id].zone)
                for seat in 'ACD':
                    self.assertNotIn(top.ref, json.dumps(s.packet(f'pilot:{seat}', full=True)))
                self.replay(s, load=True)

    def test_attached_attack_keeps_unowned_other_attacker_exclusion_residual(self):
        card = self.db.lookup('Sigil of Valor')
        ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile='commander_review')
        self.assertNotEqual('exact', ir.status)
        self.assertEqual(1, len(ir.material_residuals))
        self.assertIn('for each other creature you control', ir.material_residuals[0].text)

    def test_actual_blocked_results_keep_each_defender_private_choices_and_once_per_attacker(self):
        s=self.session(508509701)
        golem=self.add(s.engine,'Flint Golem',ref='actual-defender-mill')
        ghoul=self.add(s.engine,'Vedalken Ghoul',ref='actual-defender-life')
        grifters=self.add(s.engine,'Alley Grifters',ref='actual-defender-discard')
        beast=self.add(s.engine,'Thresher Beast',ref='actual-defender-sacrifice')
        b1=self.add(s.engine,'Phyrexian Walker',seat='B',ref='defender-b-first-blocker')
        b2=self.add(s.engine,'Phyrexian Walker',seat='B',ref='defender-b-second-blocker')
        c=self.add(s.engine,'Phyrexian Walker',seat='C',ref='defender-c-blocker')
        d1=self.add(s.engine,'Phyrexian Walker',seat='D',ref='defender-d-first-blocker')
        d2=self.add(s.engine,'Phyrexian Walker',seat='D',ref='defender-d-second-blocker')
        discard=self.add(s.engine,'Counterspell',seat='D',zone='hand',ref='actual-private-defender-discard')
        land=self.add(s.engine,'Plains',seat='D',ref='actual-defender-land-payment')
        for card in (golem,ghoul,grifters,beast):card.acquired_control_turn_count=-1;self.assert_closed(s,card)
        self.prepare(s);self.checkpoint(s)
        libraries={seat:len(s.state.players[seat].zones['library']) for seat in 'ABCD'}
        graves={seat:len(s.state.players[seat].zones['graveyard']) for seat in 'ABCD'}
        hands={seat:len(s.state.players[seat].zones['hand']) for seat in 'ABCD'}
        self.attack(s,[(golem,'B'),(ghoul,'C'),(grifters,'D'),(beast,'D')])
        self.until(s,lambda game:game.state.pending_decision is not None and game.state.pending_decision.kind=='combat.blockers')
        for seat,blocks in (('B',{b1.ref:golem.ref,b2.ref:golem.ref}),('C',{c.ref:ghoul.ref}),('D',{d1.ref:grifters.ref,d2.ref:beast.ref})):
            command={'action_id':'block','blocks':blocks};before=authoritative_state_hash(s.state)
            self.assertFalse(s.act('pilot:A',command).ok);self.assertEqual(before,authoritative_state_hash(s.state))
            result=s.act('pilot:'+seat,command);self.assertTrue(result.ok,result.summary)
        self.until(s,lambda game:len(game.state.stack)==4 and not game.state.pending_trigger_batches)
        seen=0
        while s.state.stack:
            decision=self.resolve(s)
            if decision is None:break
            self.assertEqual('choice.apnap',decision.kind);self.assertEqual(['D'],decision.actors)
            options=s.packet('pilot:D',full=True)['decision']['ctx']['options']
            before=authoritative_state_hash(s.state)
            command={'action_id':'choose','cards':[land.ref if land.ref in options else discard.ref]}
            self.assertFalse(s.act('pilot:A',command).ok);self.assertEqual(before,authoritative_state_hash(s.state))
            if s.state.cards[discard.object_id].zone=='hand':
                for viewer in 'ABC':self.assertNotIn(discard.ref,json.dumps(s.packet('pilot:'+viewer,full=True)))
            s=self.replay(s,load=True);result=s.act('pilot:D',command);self.assertTrue(result.ok,result.summary);seen+=1
        self.assertEqual(2,seen)
        self.assertEqual(libraries['B']-3,len(s.state.players['B'].zones['library']))
        for seat in 'ACD':self.assertEqual(libraries[seat],len(s.state.players[seat].zones['library']))
        self.assertEqual((40,40,36,40),tuple(s.state.players[seat].life for seat in 'ABCD'))
        self.assertEqual('graveyard',s.state.cards[discard.object_id].zone);self.assertEqual('graveyard',s.state.cards[land.object_id].zone)
        self.assertEqual(hands['D']-1,len(s.state.players['D'].zones['hand']))
        self.assertEqual(graves['B']+3,len(s.state.players['B'].zones['graveyard']))
        self.replay(s,load=True)
