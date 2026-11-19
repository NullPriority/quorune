"""CR509.3g: actual blocker completion precedes unblocked triggers and damage."""
import json,tempfile,unittest
from pathlib import Path
from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class UnblockedAttackerNativeTests(InheritedNativeFixture,unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'unblocked-attacker.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/unblocked-attacker-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Unblocked timing',[DeckEntry('Isamaru, Hound of Konda',1,'commander'),DeckEntry('Generic Bound Plains',14)],['Isamaru, Hound of Konda'])

    def test_actual_zero_block_assignments_still_create_one_trigger_and_source_blink_keeps_it(self):
        s=self.session(509300702);source=self.add(s.engine,'Eternal of Harsh Truths',ref='actual-zero-block-eternal')
        blink=self.add(s.engine,'Cloudshift',zone='hand',ref='actual-unblocked-source-blink')
        self.add(s.engine,'Plains',ref='zero-block-blink-mana')
        self.add(s.engine,'Counterspell',seat='D',zone='hand',ref='zero-block-private-hand')
        source.acquired_control_turn_count=-1;self.assert_closed(s,source)
        self.prepare(s);self.checkpoint(s);hands=len(s.state.players['A'].zones['hand'])
        self.attack(s,[(source,'B')]);self.assertFalse(s.state.stack)
        self.until(s,lambda game:bool(game.state.stack) and not game.state.pending_trigger_batches)
        self.assertEqual(1,len(s.state.stack));self.assertFalse(s.state.combat.blockers)
        original=s.state.cards[source.object_id].logical_object_id
        self.cast(s,'A',blink,[source.ref]);self.resolve_top(s)
        self.assertNotEqual(original,s.state.cards[source.object_id].logical_object_id)
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual(hands,len(s.state.players['A'].zones['hand']),'One blink spent and the already-created draw retained')
        self.assertEqual(40,s.state.players['B'].life)
        self.until(s,lambda game:game.state.phase=='combat' and game.state.step=='end_combat' and not game.state.stack)
        self.assertEqual(40,s.state.players['B'].life)
        for seat in 'ABC':self.assertNotIn('zero-block-private-hand',json.dumps(s.packet('pilot:'+seat,full=True)))
        self.replay(s,load=True)

    def test_actual_unblocked_trigger_waits_for_all_blockers_and_blocker_departure_does_not_retrigger(self):
        s=self.session(509300701)
        open_attacker=self.add(s.engine,'Eternal of Harsh Truths',ref='actual-unblocked-eternal')
        blocked=self.add(s.engine,'Eternal of Harsh Truths',ref='actual-blocked-eternal')
        blocker=self.add(s.engine,'Phyrexian Walker',seat='B',ref='actual-declared-walker')
        self.add(s.engine,'Phyrexian Walker',seat='C',ref='actual-declined-walker')
        bounce=self.add(s.engine,'Unsummon',seat='B',zone='hand',ref='actual-later-blocker-removal')
        self.add(s.engine,'Island',seat='B',ref='unblocked-bounce-mana')
        self.add(s.engine,'Counterspell',seat='D',zone='hand',ref='unblocked-private-hand')
        for card in (open_attacker,blocked):card.acquired_control_turn_count=-1
        self.assert_closed(s,open_attacker);self.prepare(s);self.checkpoint(s)
        hand=len(s.state.players['A'].zones['hand'])
        self.attack(s,[(open_attacker,'C'),(blocked,'B')])
        self.assertFalse(s.state.stack,'Unblocked event must not fire at attacker declaration')
        self.until(s,lambda game:game.state.pending_decision is not None and game.state.pending_decision.kind=='combat.blockers')
        command={'action_id':'block','blocks':{blocker.ref:blocked.ref}}
        before=authoritative_state_hash(s.state);self.assertFalse(s.act('pilot:D',command).ok);self.assertEqual(before,authoritative_state_hash(s.state))
        result=s.act('pilot:B',command);self.assertTrue(result.ok,result.summary)
        self.assertEqual(hand,len(s.state.players['A'].zones['hand']))
        result=s.act('pilot:C',{'action_id':'block','blocks':{}});self.assertTrue(result.ok,result.summary)
        self.until(s,lambda game:bool(game.state.stack) and not game.state.pending_trigger_batches)
        self.assertEqual(2,len(s.state.stack),'One unblocked draw and the blocked attacker\'s Afflict')
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual(hand+1,len(s.state.players['A'].zones['hand']))
        self.assertEqual((38,40),(s.state.players['B'].life,s.state.players['C'].life))
        self.cast(s,'B',bounce,[blocker.ref]);self.assertIsNone(self.resolve(s))
        self.assertEqual('hand',s.state.cards[blocker.object_id].zone)
        self.until(s,lambda game:game.state.phase=='combat' and game.state.step=='end_combat' and not game.state.stack)
        self.assertEqual(hand+1,len(s.state.players['A'].zones['hand']))
        self.assertEqual((38,39),(s.state.players['B'].life,s.state.players['C'].life))
        for seat in 'ABC':self.assertNotIn('unblocked-private-hand',json.dumps(s.packet('pilot:'+seat,full=True)))
        self.replay(s,load=True)


    def _poison(self, session):
        return {seat: session.state.players[seat].poison for seat in "ABCD"}

    def _blockers_finished(self, session):
        self.until(session, lambda game: bool(game.state.stack) and not game.state.pending_trigger_batches)

    def _assert_poison_stack(self, session, expected):
        stack = {item.source_object_id: item.context["defending_player"] for item in session.state.stack}
        self.assertEqual(expected, stack)
        self.assertEqual(len(expected), len(session.state.stack))
        self.assertTrue(all(item.targets == [] for item in session.state.stack))

    def test_actual_all_three_paid_poison_sources_keep_their_own_defenders(self):
        session = self.session(509300711)
        engine = session.engine
        mosquito = self.add(engine, "Swamp Mosquito", zone="hand")
        cobra = self.add(engine, "Crypt Cobra", zone="hand")
        assassin = self.add(engine, "Suq'Ata Assassin", zone="hand")
        walker = self.add(engine, "Jace Beleren", seat="B")
        reference, = engine.create_token(
            "C", name="Generic Defended Siege", battle_protector="D",
            characteristics={"type_line": "Token Battle — Siege", "defense": "3"},
        )
        battle = engine._resolve_object("A", reference, zones={"battlefield"})
        walker.counters["loyalty"] = 3
        self.add(engine, "Fervor")
        for source in (mosquito, cobra, assassin, walker):
            self.assert_closed(session, source)
        self.prepare(session, mana={"C": 5, "B": 4})
        self.checkpoint(session)
        for source, reserve in zip((mosquito, cobra, assassin), ({"B": 3}, {"B": 2}, None), strict=True):
            self.cast_for(session, "A", source, reserve=reserve)
            self.assertIsNone(self.resolve(session))
            self.assertEqual("battlefield", session.state.cards[source.object_id].zone)
        self.assertEqual((0, 0), tuple(session.state.players["A"].mana_pool.get(color, 0) for color in "CB"))

        self.attack(session, [(mosquito, "C"), (cobra, walker.ref), (assassin, battle.ref)])
        self.assertFalse(session.state.stack)
        self.assertEqual(dict.fromkeys("ABCD", 0), self._poison(session))
        self._blockers_finished(session)
        defenders = {mosquito.object_id: "C", cobra.object_id: "B", assassin.object_id: "D"}
        self._assert_poison_stack(session, defenders)
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual({"A": 0, "B": 1, "C": 1, "D": 1}, self._poison(session))
        self.assertEqual(3, session.state.cards[walker.object_id].counters["loyalty"])
        self.assertEqual(3, session.state.cards[battle.object_id].counters["defense"])
        self.replay(session, load=True)

    def test_actual_blocker_departure_does_not_create_poison_and_source_blink_keeps_it(self):
        session = self.session(509300712)
        engine = session.engine
        open_source = self.add(engine, "Crypt Cobra", ref="poison-open-cobra")
        blocked_source = self.add(engine, "Crypt Cobra", ref="poison-blocked-cobra")
        blocker = self.add(engine, "Phyrexian Walker", seat="B", ref="poison-declared-blocker")
        self.add(engine, "Phyrexian Walker", seat="C", ref="poison-declined-blocker")
        self.add(engine, "Island", seat="B", ref="poison-bounce-island")
        self.add(engine, "Plains", ref="poison-source-plains")
        bounce = self.add(engine, "Unsummon", seat="B", zone="hand")
        blink = self.add(engine, "Cloudshift", zone="hand")
        private = self.add(engine, "Counterspell", seat="D", zone="hand", ref="poison-hidden-spell")
        for source in (open_source, blocked_source):
            source.acquired_control_turn_count = -1
            self.assert_closed(session, source)
        original = open_source.logical_object_id
        self.prepare(session)
        self.checkpoint(session)
        self.attack(session, [(open_source, "C"), (blocked_source, "B")])
        self.until(session, lambda game: game.state.pending_decision is not None
                   and game.state.pending_decision.kind == "combat.blockers")
        block = {"action_id": "block", "blocks": {blocker.ref: blocked_source.ref}}
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act("pilot:D", block).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        accepted = session.act("pilot:B", block)
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertFalse(session.state.stack)
        accepted = session.act("pilot:C", {"action_id": "block", "blocks": {}})
        self.assertTrue(accepted.ok, accepted.summary)
        self._blockers_finished(session)
        self._assert_poison_stack(session, {open_source.object_id: "C"})
        self.cast_for(session, "A", blink, [open_source.ref])
        self.resolve_top(session)
        self.assertNotEqual(original, session.state.cards[open_source.object_id].logical_object_id)
        self.cast_for(session, "B", bounce, [blocker.ref])
        self.resolve_top(session)
        self.assertEqual("hand", session.state.cards[blocker.object_id].zone)
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.until(session, lambda game: game.state.phase == "combat" and game.state.step == "end_combat"
                   and not game.state.stack)
        self.assertEqual({"A": 0, "B": 0, "C": 1, "D": 0}, self._poison(session))
        for seat in "ABC":
            self.assertNotIn(private.ref, json.dumps(session.packet("pilot:" + seat, full=True)))
        self.replay(session, load=True)

    def test_actual_departed_defender_cannot_stall_or_redirect_poison(self):
        session = self.session(509300713)
        engine = session.engine
        first = self.add(engine, "Swamp Mosquito")
        second = self.add(engine, "Crypt Cobra")
        for source in (first, second):
            source.acquired_control_turn_count = -1
            self.assert_closed(session, source)
        self.prepare(session)
        self.checkpoint(session)
        self.attack(session, [(first, "B"), (second, "C")])
        self._blockers_finished(session)
        self._assert_poison_stack(session, {first.object_id: "B", second.object_id: "C"})
        self.priority(session, "B")
        accepted = session.act("pilot:B", {"action_id": "concede", "choices": {"confirm_concede": True}})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertFalse(session.state.players["B"].in_game)
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual({"A": 0, "B": 0, "C": 1, "D": 0}, self._poison(session))
        self.replay(session, load=True)

    def test_actual_poison_commit_rolls_back_before_the_loss_and_retries(self):
        from unittest.mock import patch

        from quorune.errors import GameRuleError
        from quorune.semantic_choices.intent_host import SemanticChoiceIntentHostMixin

        session = self.session(509300714)
        mosquito = self.add(session.engine, "Swamp Mosquito")
        mosquito.acquired_control_turn_count = -1
        session.state.players["B"].poison = 9
        self.assert_closed(session, mosquito)
        self.prepare(session)
        self.checkpoint(session)
        self.attack(session, [(mosquito, "B")])
        self._blockers_finished(session)
        self._assert_poison_stack(session, {mosquito.object_id: "B"})
        original = SemanticChoiceIntentHostMixin.place_player_counters_intent
        reached = []

        def fail_after_commit(host, intent):
            original(host, intent)
            reached.append(host.state.players["B"].poison)
            raise GameRuleError("Injected failure after the poison counter commit")

        actor = None
        with patch.object(SemanticChoiceIntentHostMixin, "place_player_counters_intent", fail_after_commit):
            for _ in range(8):
                actor = session.pending_principals()[0]
                before = authoritative_state_hash(session.state)
                resolution = session.act(actor, {"action_id": "pass"})
                if not resolution.ok:
                    self.assertIn("Injected failure after the poison counter commit", resolution.summary)
                    self.assertEqual(before, authoritative_state_hash(session.state))
                    break
            else:
                self.fail("The poison result did not reach its counter commit")
        self.assertEqual([10], reached)
        self.assertEqual(9, self._poison(session)["B"])
        self.assertTrue(session.state.players["B"].in_game)
        session = self.replay(session, load=True)
        accepted = session.act(actor, {"action_id": "pass"})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual(10, self._poison(session)["B"])
        self.assertFalse(session.state.players["B"].in_game)
        self.assertTrue(session.state.players["C"].in_game)
        self.replay(session, load=True)
