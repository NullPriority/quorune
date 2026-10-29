from __future__ import annotations

import unittest
import uuid

from quorune.engine import GameRuleError
from quorune.model import StackItem
from common import keep_all, load_assets, make_session, pass_current


class MultiplayerRuleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db, cls.mishra, cls.zimone = load_assets()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def nameless_legend_fixture(self, seed):
        session=make_session(self.db,self.mishra,self.zimone,seed=seed)
        keep_all(session);engine=session.engine
        engine.permissions.invalidate_current();engine.state.pending_decision=None;engine.state.priority_player=None
        cards=[]
        for _ in range(2):
            ref=engine.create_token('A',name='Shared display label',characteristics={
                'type_line':'Legendary Creature — Hound','power':'2','toughness':'2'})[0]
            card=engine._resolve_object('A',ref,zones={'battlefield'})
            card.annotations['copy_overrides']={'name':'','type_line':'Legendary Creature — Hound',
                'power':'2','toughness':'2','mana_cost':'','colors':[],'ability_fragments':[]}
            cards.append(card)
        return session,cards

    def test_nameless_legendaries_never_group_by_their_display_label(self):
        from quorune.record import authoritative_state_hash
        session,cards=self.nameless_legend_fixture(20170401)
        self.assertEqual(['',''],[session.engine._effective_card_data(c)['name'] for c in cards])
        before=authoritative_state_hash(session.state)
        self.assertEqual([],session.engine._legend_groups())
        self.assertEqual(before,authoritative_state_hash(session.state))
        self.assertFalse(session.engine._stabilize())
        self.assertIsNone(session.state.pending_decision)
        self.assertTrue(all(session.state.cards[c.object_id].zone=='battlefield' for c in cards))

    def test_current_named_legends_group_after_projection_changes_and_ignore_phasing(self):
        from quorune.record import authoritative_state_hash
        session,cards=self.nameless_legend_fixture(20170402);engine=session.engine
        self.assertEqual([],engine._legend_groups())
        for card in cards:card.annotations['copy_overrides']['name']='Shared current name'
        groups=engine._legend_groups()
        self.assertEqual(1,len(groups));self.assertEqual(('A','Shared current name'),groups[0][:2])
        cards[1].phased_out=True
        self.assertEqual([],engine._legend_groups())
        cards[1].phased_out=False
        self.assertEqual(1,len(engine._legend_groups()))
        self.assertTrue(engine._stabilize());self.assertEqual('state.legend',session.state.pending_decision.kind)
        session.packet('pilot:A',full=True)
        before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:B',{'action_id':'choose','card':cards[0].ref}).ok)
        self.assertEqual(before,authoritative_state_hash(session.state))
        accepted=session.act('pilot:A',{'action_id':'choose','card':session.state.cards[cards[0].object_id].ref})
        self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual(1,sum(session.state.cards[c.object_id].zone=='battlefield' for c in cards))

    def test_extra_turns_are_lifo_then_normal_order_resumes(self):
        session = make_session(self.db, self.mishra, self.zimone, seed=31)
        keep_all(session)
        engine = session.engine
        engine.schedule_extra_turn("B", source="first")
        engine.schedule_extra_turn("C", source="second")
        self.assertEqual("C", engine._select_next_turn().player)
        self.assertEqual("B", engine._select_next_turn().player)
        self.assertEqual("B", engine._select_next_turn().player)

    def test_delayed_trigger_queues_at_matching_step(self):
        session = make_session(self.db, self.mishra, self.zimone, seed=32)
        keep_all(session)
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.priority_player = None
        trigger = engine.schedule_delayed_trigger(
            controller="A",
            label="Test upkeep trigger",
            event_kind="step.begin",
            condition={"phase": "beginning", "step": "upkeep", "player": "A"},
            stack_template={"label": "Test upkeep trigger", "semantic_key": "test:upkeep"},
        )
        matches = engine._matching_delayed_triggers("step.begin", {"phase": "beginning", "step": "upkeep", "player": "A"})
        self.assertEqual([trigger.trigger_id], [item.trigger_id for item in matches])
        engine._start_trigger_batch(matches, after="grant_priority")
        self.assertEqual("Test upkeep trigger", engine.state.stack[-1].label)
        self.assertEqual("A", engine.state.priority_player)

    def test_legend_rule_asks_controller_to_keep_one(self):
        session = make_session(self.db, self.mishra, self.zimone, seed=33)
        keep_all(session)
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.priority_player = None
        commander_id = engine.state.players["A"].zones["command"][0]
        engine.move_card(commander_id, "battlefield", controller="A", log=False)
        original_ref = engine.state.cards[commander_id].ref
        engine.create_token("A", name="Mishra, Eminent One", copy_of=original_ref, reason="test copy")
        self.assertTrue(engine._stabilize())
        self.assertEqual("state.legend", engine.state.pending_decision.kind)
        result = session.act("pilot:A", {"a": "choose", "card": original_ref})
        self.assertTrue(result.ok, result.summary)
        battlefield_legends = [oid for oid in engine.state.players["A"].zones["battlefield"] if engine.display_name(oid) == "Mishra, Eminent One"]
        self.assertEqual(1, len(battlefield_legends))

    def test_simultaneous_apnap_choices_are_collected_in_turn_order(self):
        session = make_session(self.db, self.mishra, self.zimone, seed=34)
        keep_all(session)
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.priority_player = None
        refs = {}
        for seat in "ABCD":
            refs[seat] = engine.create_token(seat, name="Test Bear", characteristics={"type_line": "Creature — Bear", "power": "2", "toughness": "2"})[0]
        item = StackItem(uuid.uuid4().hex, "S-test", "spell", "A", "Each player sacrifices", semantic_key="test:apnap", visibility=list("ABCD"))
        engine.state.stack.append(item)
        engine._issue_apnap_choice(
            effect={"op": "choose_cards_apnap", "players": "all", "zone": "battlefield", "filter": {"type": "creature"}, "count": 1, "then": "sacrifice"},
            continuation={"stack_ref": "S-test", "effects": [], "destination": None, "note": "test"},
        )
        seen = []
        for seat in "ABCD":
            self.assertEqual(f"pilot:{seat}", session.pending_principals()[0])
            seen.append(seat)
            self.assertTrue(session.act(f"pilot:{seat}", {"a": "choose", "cs": [refs[seat]]}).ok)
            if seat != "D":
                self.assertEqual("battlefield", next(card for card in engine.state.cards.values() if card.ref == refs["A"]).zone)
        self.assertEqual(list("ABCD"), seen)
        self.assertTrue(all(next(card for card in engine.state.cards.values() if card.ref == ref).zone != "battlefield" for ref in refs.values()))

    def test_multiple_defenders_block_sequentially(self):
        session = make_session(self.db, self.mishra, self.zimone, seed=35)
        keep_all(session)
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.priority_player = None
        a1, a2 = engine.create_token("A", name="Attacker", quantity=2, characteristics={"type_line": "Creature", "power": "2", "toughness": "2"}, temporary_keywords=["Haste"])
        engine.create_token("B", name="Blocker B", characteristics={"type_line": "Creature", "power": "2", "toughness": "2"})
        engine.create_token("C", name="Blocker C", characteristics={"type_line": "Creature", "power": "2", "toughness": "2"})
        engine.state.phase_index = 5
        engine._enter_step()
        self.assertEqual("combat.attackers", engine.state.pending_decision.kind)
        self.assertTrue(session.act("pilot:A", {"a": "attack", "atk": {a1: "B", a2: "C"}}).ok)
        for _ in range(4):
            pass_current(session)
        self.assertEqual("pilot:B", session.pending_principals()[0])
        self.assertTrue(session.act("pilot:B", {"a": "block", "blk": {}}).ok)
        self.assertEqual("pilot:C", session.pending_principals()[0])
        self.assertTrue(session.act("pilot:C", {"a": "block", "blk": {}}).ok)
        self.assertEqual("pilot:A", session.pending_principals()[0])
        self.assertEqual(
            {"B", "C", "D"},
            set(engine.state.combat.defending_players),
        )

    def test_structured_attack_list_reaches_authoritative_combat_state(self):
        session = make_session(self.db, self.mishra, self.zimone, seed=351)
        keep_all(session)
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.priority_player = None
        first, second = engine.create_token(
            "A",
            name="Structured Attacker",
            quantity=2,
            characteristics={
                "type_line": "Creature",
                "power": "2",
                "toughness": "2",
            },
            temporary_keywords=["Haste"],
        )
        engine.state.phase_index = 5
        engine._enter_step()
        self.assertEqual("combat.attackers", engine.state.pending_decision.kind)
        result = session.act(
            "pilot:A",
            {
                "a": "attack",
                "attacks": [
                    {"attacker": first, "defender": "B"},
                    {"attacker": second, "defender": "C"},
                ],
            },
        )
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(
            {first: "B", second: "C"},
            {
                engine.state.cards[object_id].ref: defender
                for object_id, defender in engine.state.combat.attackers.items()
            },
        )
        event = next(
            event
            for event in reversed(engine.state.events)
            if event.code == "combat.attack"
        )
        self.assertEqual({first: "B", second: "C"}, event.details["attackers"])

    def test_player_elimination_keeps_multiplayer_game_running(self):
        session = make_session(self.db, self.mishra, self.zimone, seed=36)
        keep_all(session)
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.players["B"].life = 0
        engine._stabilize()
        engine._assert_invariants()
        self.assertFalse(engine.state.players["B"].in_game)
        self.assertFalse(engine.state.game_over)
        self.assertEqual(["A", "C", "D"], engine.active_seats)

    def test_last_player_wins_after_simultaneous_losses(self):
        session = make_session(self.db, self.mishra, self.zimone, seed=37)
        keep_all(session)
        engine = session.engine
        engine.permissions.invalidate_current()
        for seat in "BCD":
            engine.state.players[seat].life = 0
        engine._stabilize()
        engine._assert_invariants()
        self.assertTrue(engine.state.game_over)
        self.assertEqual("A", engine.state.winner)

    def test_bond_land_uses_live_opponent_count(self):
        four = make_session(self.db, self.mishra, self.zimone, players=4, seed=38)
        two = make_session(self.db, self.mishra, self.zimone, players=2, seed=39)
        for session, expected_tapped in ((four, False), (two, True)):
            training_center = next(
                card
                for card in session.state.cards.values()
                if card.owner == "A"
                and card.printed_name == "Training Center"
            )
            session.engine.move_card(
                training_center.object_id,
                "battlefield",
                controller="A",
                log=False,
            )
            self.assertEqual(expected_tapped, training_center.tapped)

    def test_top_library_knowledge_and_reorder(self):
        session = make_session(self.db, self.mishra, self.zimone, seed=40)
        keep_all(session)
        engine = session.engine
        refs = engine.apply_effect({"op": "look_top", "player": "A", "viewer": "A", "count": 3}, actor="A")
        before = [engine.state.cards[oid].ref for oid in reversed(engine.state.players["A"].zones["library"][-3:])]
        self.assertEqual(refs, before)
        engine.apply_effect({"op": "reorder_top", "player": "A", "viewer": "A", "cards": list(reversed(refs))}, actor="A")
        after = [engine.state.cards[oid].ref for oid in reversed(engine.state.players["A"].zones["library"][-3:])]
        self.assertEqual(list(reversed(refs)), after)

    def test_advance_helper_fails_closed(self):
        session = make_session(self.db, self.mishra, self.zimone, seed=41)
        with self.assertRaises(ValueError):
            session.engine.advance_until("ending", "end")
        with self.assertRaises(GameRuleError):
            session.engine.advance_until("precombat_main", "main")


if __name__ == "__main__":
    unittest.main()
