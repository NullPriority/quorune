"""An explicit you retains the resolving ability controller without targeting."""
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch
from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
import test_recipient_control as seat_actions
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.compiler.extra_turn_templates import fixed_extra_turn_effect_template
from scripts.build_test_database import build_fixture_database


class ExplicitControllerExtraTurnCompilerTests(unittest.TestCase):
    def test_you_is_the_untargeted_controller_for_each_fixed_count(self):
        for quantity, count in [('an', 1), ('two', 2), ('ten', 10)]:
            ending = ' extra turn'+('s' if count != 1 else '')+' after this one.'
            with self.subTest(count=count):
                imperative = fixed_extra_turn_effect_template('Take '+quantity+ending)
                explicit = fixed_extra_turn_effect_template('You take '+quantity+ending)
                self.assertEqual(imperative, explicit)
                self.assertIsNotNone(explicit)
                self.assertIsNone(explicit[2])
                self.assertEqual(count, len(explicit[1]))
                self.assertTrue(all(effect['player'] == '$controller' for effect in explicit[1]))

    def test_complete_opponent_extra_skip_and_three_draw_sources_use_closed_live_facts(self):
        from quorune.rules.event_subscriptions import FixedEventSubscriptionSet
        from quorune.semantic_runtime.context import SemanticNodeError
        from quorune.semantic_runtime.untap_steps import StaticExtraTurnSkipHandler
        from test_bound_effect_programs import record

        first = "If an opponent would begin an extra turn, that player skips that turn instead."
        second = ("Whenever an opponent attacks you with two or more creatures, draws their second card each turn, "
                  "or casts their second spell each turn, you draw a card.")
        registry = load_default_capability_registry()
        ir = compile_oracle_card(record(first + "\n" + second, "Enchantment"), capability_registry=registry)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        static, trigger = ir.faces[0].nodes
        self.assertEqual((first, second), (static.text, trigger.text))
        self.assertIn("turn.boundary.scheduled_skip", static.capability_dependencies)
        self.assertIn("trigger.event.direct_player_attack", trigger.capability_dependencies)
        subscriptions = FixedEventSubscriptionSet.from_condition(trigger.event_condition)
        self.assertEqual(("combat.player_attacked", "card.second_draw", "spell.cast"),
                         tuple(row.event for row in subscriptions.subscriptions))
        descriptor, = static.handlers
        for delta in ({"schema_version": 2}, {"scope": "all_players"}, {"requires_empty_hand": True}):
            with self.assertRaises(SemanticNodeError):
                StaticExtraTurnSkipHandler().validate({**descriptor, **delta})
        for change in (first.replace("an opponent", "a player") + "\n" + second,
                       first + "\n" + second.replace("casts their second", "casts their third")):
            self.assertNotEqual("exact", compile_oracle_card(record(change, "Enchantment"),
                                                            capability_registry=registry).status)

    def test_old_targeted_recipients_keep_their_existing_target_contract(self):
        for subject, relation in [('Target player', 'any'), ('Target opponent', 'opponent')]:
            with self.subTest(subject=subject):
                result = fixed_extra_turn_effect_template(subject+' takes an extra turn after this one.')
                self.assertEqual('$target.0', result[1][0]['player'])
                self.assertEqual(relation, result[2]['player_relation'])

    def test_ambiguous_dynamic_conditional_and_changed_timing_clauses_remain_unowned(self):
        for text in ('That player takes an extra turn after this one.',
                     'You take X extra turns after this one.',
                     'You take an extra turn after your next turn.',
                     'You take an extra turn after this one if you control a creature.',
                     'You take two extra turn after this one.',
                     'You take zero extra turns after this one.'):
            with self.subTest(text=text):
                self.assertIsNone(fixed_extra_turn_effect_template(text))


class ExplicitControllerExtraTurnNativeTests(InheritedNativeFixture, unittest.TestCase):
    reject = seat_actions.RecipientControlNativeTests.reject
    finish = seat_actions.RecipientControlNativeTests.finish
    finish_turn = seat_actions.RecipientControlNativeTests.finish_turn

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        path = Path(cls.temp.name)/'explicit-controller-turns.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/explicit-controller-extra-turn-cards.json',
            ROOT/'tests/fixtures/attacking-creature-next-untap-cards.json',
            ROOT/'tests/fixtures/additional-conditional-modifier-cards.json',
            ROOT/'tests/fixtures/trouble-in-pairs-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Explicit controller turns', [
            DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 30)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temp.cleanup()

    def world(self, seed, *, donate=False):
        session = self.session(seed)
        source = self.add(session.engine, 'Wormfang Manta', zone='hand')
        bounce = self.add(session.engine, 'Boomerang', zone='hand')
        private = self.add(session.engine, 'Counterspell', seat='C', zone='hand')
        gift = None
        if donate:
            gift = self.add(session.engine, 'Donate', zone='hand')
            flash = self.add(session.engine, 'Vedalken Orrery')
            self.assert_closed(session, flash)
            self.assert_closed(session, gift)
        for card in (source, bounce):self.assert_closed(session, card)
        self.prepare(session, mana={'C': 10, 'U': 6})
        self.checkpoint(session)
        return session, source, bounce, private, gift

    def test_actual_trouble_checks_the_source_when_the_opponents_extra_turn_would_begin(self):
        for mode in ("present", "departed", "own turn"):
            with self.subTest(mode=mode):
                session = self.session(500750 + len(mode))
                source = self.add(session.engine, "Trouble in Pairs")
                warp = self.add(session.engine, "Time Warp", zone="hand")
                bounce = self.add(session.engine, "Boomerang", zone="hand")
                self.assert_closed(session, source)
                self.assert_closed(session, warp)
                self.prepare(session, mana={"C": 10, "U": 10})
                self.checkpoint(session)
                target = "A" if mode == "own turn" else "B"
                self.cast(session, "A", warp, [target])
                self.assertIsNone(self.resolve(session))
                entry, = session.state.extra_turns
                self.assertEqual(target, entry.player)
                if mode == "departed":
                    self.cast(session, "A", bounce, [source.ref])
                    self.assertIsNone(self.resolve(session))
                    self.assertEqual("hand", session.state.cards[source.object_id].zone)
                self.finish_turn(session)
                self.assertEqual(mode != "present", session.state.current_turn.extra)
                self.assertEqual(target, session.state.active_player)
                self.assertFalse(session.state.extra_turns)
                self.replay(session, load=True)

    def test_actual_trouble_lets_the_affected_player_choose_static_or_finite_skip(self):
        from quorune.record import authoritative_state_hash

        for static in (False, True):
            with self.subTest(static=static):
                session = self.session(500755 + static)
                source = self.add(session.engine, "Trouble in Pairs")
                manta = self.add(session.engine, "Wormfang Manta", seat="B", zone="hand")
                bounce = self.add(session.engine, "Boomerang", seat="B", zone="hand")
                self.assert_closed(session, source)
                original_hand = session.state.players["B"].zones["hand"]
                filler = next(oid for oid in original_hand if oid not in {manta.object_id, bounce.object_id})
                session.engine.move_card(filler, "library", log=False)
                self.prepare(session, actor="B", active="B", mana={"C": 10, "U": 10})
                session.state.last_normal_turn_player = "B"
                self.checkpoint(session)
                self.cast(session, "B", manta)
                self.assertIsNone(self.resolve(session))
                grant, = session.state.scheduled_turn_skips
                self.cast(session, "B", bounce, [manta.ref])
                self.assertIsNone(self.resolve(session))
                entry, = session.state.extra_turns
                self.assertEqual("B", entry.player)
                self.until(session, lambda game: game.state.pending_decision.kind == "replacement.order")
                self.assertEqual(["pilot:B"], session.pending_principals())
                decision = session.packet("pilot:B", full=True)["decision"]
                ids = [option["id"] for option in decision["ctx"]["options"]]
                chosen = next(option for option in ids if option.startswith("static-skip:") == static)
                before = authoritative_state_hash(session.state)
                command = {"action_id": "choose", "choices": {"replacement": chosen}}
                self.assertFalse(session.act("pilot:A", command).ok)
                self.assertEqual(before, authoritative_state_hash(session.state))
                session = self.replay(session, load=True)
                result = session.act("pilot:B", command)
                self.assertTrue(result.ok, result.summary)
                self.assertEqual("C", session.state.active_player)
                self.assertFalse(session.state.current_turn.extra)
                self.assertEqual([grant.grant_id] if static else [],
                                 [row.grant_id for row in session.state.scheduled_turn_skips])
                self.replay(session, load=True)

    def test_actual_trouble_draws_for_each_opponent_event_without_seeing_the_first(self):
        import json

        session = self.session(500759)
        source = self.add(session.engine, "Trouble in Pairs", zone="hand")
        self.add(session.engine, "Vedalken Orrery")
        first = self.add(session.engine, "Revitalize", seat="B", zone="hand", ref="pairs-first-spell")
        second = self.add(session.engine, "Revitalize", seat="B", zone="hand", ref="pairs-second-spell")
        attackers = [self.add(session.engine, "Grizzly Bears", seat="B", ref=f"pairs-attacker-{i}") for i in range(3)]
        for attacker in attackers:
            attacker.acquired_control_turn_count = -1
        self.assert_closed(session, source)
        self.prepare(session, actor="B", active="B", mana={"C": 2, "W": 2})
        session.state.players["A"].mana_pool.update({"C": 2, "W": 2})
        self.checkpoint(session)
        self.cast(session, "B", first)
        self.assertIsNone(self.resolve(session))
        self.cast(session, "A", source)
        self.assertIsNone(self.resolve(session))
        hand = set(session.state.players["A"].zones["hand"])
        self.cast(session, "B", second)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(2, len(set(session.state.players["A"].zones["hand"]) - hand))
        self.until(session, lambda game: game.state.pending_decision.kind == "combat.attackers")
        result = session.act("pilot:B", {"action_id": "attack", "attackers": {
            attackers[0].ref: "A", attackers[1].ref: "A", attackers[2].ref: "C",
        }})
        self.assertTrue(result.ok, result.summary)
        self.resolve(session)
        drawn = set(session.state.players["A"].zones["hand"]) - hand
        self.assertEqual(3, len(drawn))
        for seat in "BCD":
            projected = json.dumps(session.packet("pilot:" + seat, full=True))
            self.assertTrue(all(session.state.cards[oid].ref not in projected for oid in drawn))
        self.replay(session, load=True)

    def test_whole_original_admits_entry_skip_and_untargeted_departure_extra_turn(self):
        session = self.session(500701)
        source = self.add(session.engine, 'Wormfang Manta', zone='hand')
        self.assert_closed(session, source)
        ir = compile_oracle_card(self.db.lookup('Wormfang Manta'),
            capability_registry=self.registry, capability_profile='commander_review')
        self.assertEqual('exact', ir.status, ir.material_residuals)
        programs = session.engine.semantics.programs_for_oracle(source.oracle_id)
        departure, = [program for program in programs
                       if any(effect.get('op') == 'extra_turn' for effect in program.effects)]
        self.assertIsNone(departure.target_schema)
        self.assertEqual('$controller', departure.effects[0]['player'])

    def test_original_departure_extra_turn_is_consumed_by_its_entry_skip(self):
        session, source, bounce, private, _ = self.world(500710)
        self.cast(session, 'A', source)
        self.assertIsNone(self.resolve(session))
        self.assertEqual('battlefield', session.state.cards[source.object_id].zone)
        self.assertEqual(['A'], [grant.player for grant in session.state.scheduled_turn_skips])
        self.assertFalse(session.state.extra_turns)
        self.cast(session, 'A', bounce, [source.ref])
        self.resolve_top(session)
        self.assertEqual('hand', session.state.cards[source.object_id].zone)
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(['A'], [entry.player for entry in session.state.extra_turns])
        self.finish_turn(session)
        self.assertEqual('B', session.state.active_player)
        self.assertFalse(session.state.extra_turns)
        self.assertFalse(session.state.scheduled_turn_skips)
        self.finish(session, private)

    def test_donation_keeps_pending_skip_player_and_departure_last_controller_distinct(self):
        session, source, bounce, private, gift = self.world(500720, donate=True)
        self.cast(session, 'A', source)
        self.resolve_top(session)
        self.assertEqual('battlefield', session.state.cards[source.object_id].zone)
        self.cast(session, 'A', gift, ['B', source.ref])
        self.resolve_top(session)
        self.assertEqual('B', session.state.cards[source.object_id].controller)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(['A'], [grant.player for grant in session.state.scheduled_turn_skips])
        self.cast(session, 'A', bounce, [source.ref])
        self.resolve_top(session)
        self.assertEqual('A', session.state.cards[source.object_id].owner)
        self.assertEqual('hand', session.state.cards[source.object_id].zone)
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(['B'], [entry.player for entry in session.state.extra_turns])
        self.finish_turn(session)
        self.assertEqual('B', session.state.active_player)
        self.assertEqual(['A'], [grant.player for grant in session.state.scheduled_turn_skips])
        self.finish(session, private)

    def test_fresh_extra_turn_commit_omission_is_killed_by_original_actions(self):
        method = 'test_original_departure_extra_turn_is_consumed_by_its_entry_skip'
        import quorune.effect_runtime.damage_life_and_turns as owner
        with patch.dict(owner.HANDLERS, {'extra_turn': lambda *args, **kwargs: None}):
            with self.assertRaises(AssertionError):getattr(type(self)(method), method)()
