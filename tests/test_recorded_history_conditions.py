"""Current-turn conditions use sealed facts and ordinary trigger resolution."""

import json
from pathlib import Path
import tempfile
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.compiler.public_state_queries import fixed_public_state_condition
from quorune.compiler.token_templates import fixed_token_creation_effect_template
from quorune.continuous_conditions import FixedPublicStateConditionKind
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.public_history_query_model import PUBLIC_HISTORY_QUERY_CAPABILITY, PublicHistoryQuerySpec
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_bound_effect_programs import record
import test_public_history_queries as history_controls


class RecordedHistoryConditionCompilerTests(unittest.TestCase):
    def test_current_turn_printed_conditions_use_closed_history_leaves(self):
        registry = load_default_capability_registry()
        cases = (
            ("you didn't cast a spell this turn", ("spell_cast",)),
            ("you didn't attack with a creature this turn", ("creature_attacked",)),
            ("a player discarded a card this turn", ("card_discarded",)),
            ("a planeswalker entered the battlefield under your control this turn", ("permanent_entered",)),
            ("a creature card was put into your graveyard from anywhere this turn", ("card_arrived_graveyard",)),
            ("you gained and lost life this turn", ("player_gained_life", "player_lost_life")),
            ("you've cast both a creature spell and a noncreature spell this turn", ("spell_cast", "spell_cast")),
        )
        for text, events in cases:
            with self.subTest(text=text):
                condition = fixed_public_state_condition(text, source_name="History witness")
                self.assertIsNotNone(condition)
                leaves = condition.leaf_conditions()
                self.assertEqual(events, tuple(leaf.history_query.event for leaf in leaves))
                if len(events) == 2:
                    self.assertEqual(FixedPublicStateConditionKind.ALL, condition.kind)
                if "didn't" in text:
                    self.assertEqual(FixedPublicStateConditionKind.HISTORY_COUNT_AT_MOST, condition.kind)
                    self.assertEqual(0, condition.amount)
                ir = compile_oracle_card(
                    record(f"At the beginning of your end step, if {text}, draw a card.", "Creature"),
                    capability_registry=registry,
                )
                self.assertEqual("exact", ir.status, ir.material_residuals)
                node, = ir.faces[0].nodes
                self.assertIn(PUBLIC_HISTORY_QUERY_CAPABILITY, node.capability_dependencies)
        for unsupported in (
            "a creature card left your graveyard this turn",
            "you discarded a creature card this turn",
            "you didn't cast a spell last turn",
        ):
            self.assertIsNone(fixed_public_state_condition(unsupported, source_name="History witness"))
        with self.assertRaises(ValueError):
            PublicHistoryQuerySpec("card_discarded", types_all=("creature",))

    def test_lowercase_batch_verb_preserves_fixed_predefined_token_payloads(self):
        body = " a Clue token, a Food token, and a Treasure token."
        expected = fixed_token_creation_effect_template("Create" + body)
        observed = fixed_token_creation_effect_template("create" + body)
        self.assertIsNotNone(observed)
        self.assertEqual(expected, observed)
        self.assertEqual("create_token_batch", observed.effect["op"])
        self.assertEqual(["Clue", "Food", "Treasure"], [token["name"] for token in observed.effect["tokens"]])
        self.assertIsNone(fixed_token_creation_effect_template("create X Clue tokens and a Treasure token."))


class RecordedHistoryConditionNativeTests(InheritedNativeFixture, unittest.TestCase):
    end_step = history_controls.PublicHistoryQueryNativeTests.end_step

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "recorded-history-conditions.sqlite3"
        fixtures = (
            "bound-effect-program-cards", "public-history-query-cards",
            "static-characteristic-labels", "recorded-history-condition-cards",
        )
        build_fixture_database([ROOT / "tests/fixtures" / f"{name}.json" for name in fixtures], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Recorded history", [
            DeckEntry("Generic Bound Commander", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def _source_on_stack(self, session, source):
        return any(item.source_object_id == source.object_id for item in session.state.stack)

    def _token_names(self, session):
        return [card.printed_name for card in session.state.cards.values()
                if card.zone == "battlefield" and card.is_token]

    def test_actual_nightpack_rechecks_its_controllers_cast_after_triggering(self):
        for index, responder in enumerate(("A", "B")):
            with self.subTest(responder=responder):
                session = self.session(603_694_101 + index)
                engine = session.engine
                source = self.add(engine, "Nightpack Ambusher")
                target = self.add(engine, "Generic Bound Body", seat="B" if responder == "A" else "A")
                response = self.add(engine, "Unsummon", seat=responder, zone="hand")
                self.add(engine, "Island", seat=responder)
                self.assert_closed(session, source)
                self.prepare(session)
                self.checkpoint(session)
                self.end_step(session)
                self.assertTrue(self._source_on_stack(session, source))
                self.cast_for(session, responder, response, [target.ref])
                self.resolve_top(session)
                self.assertTrue(self._source_on_stack(session, source))
                session = self.replay(session, load=True)
                self.assertIsNone(self.resolve(session))
                self.assertEqual("hand", session.state.cards[target.object_id].zone)
                self.assertEqual([] if responder == "A" else ["Wolf"], self._token_names(session))
                self.replay(session)

    def test_actual_see_red_counts_an_unenchanted_attacker_and_sacrifices_without_one(self):
        for index, attacked in enumerate((False, True)):
            with self.subTest(attacked=attacked):
                session = self.session(603_694_111 + index)
                engine = session.engine
                aura = self.add(engine, "See Red", zone="hand")
                recipient = self.add(engine, "Generic Bound Body", ref="history-aura-recipient")
                attacker = self.add(engine, "Generic Bound Body", ref="history-unenchanted-attacker")
                attacker.acquired_control_turn_count = -1
                nightpack = self.add(engine, "Nightpack Ambusher")
                self.assert_closed(session, aura)
                self.assert_closed(session, nightpack)
                self.prepare(session, mana={"C": 1, "R": 1})
                self.checkpoint(session)
                self.cast(session, "A", aura, [recipient.ref])
                self.assertIsNone(self.resolve(session))
                self.assertEqual(recipient.object_id, session.state.cards[aura.object_id].attached_to)
                self.attack(session, [(attacker, "B")] if attacked else [])
                self.end_step(session)
                self.assertEqual(not attacked, self._source_on_stack(session, aura))
                self.assertFalse(self._source_on_stack(session, nightpack))
                self.assertIsNone(self.resolve(session))
                self.assertEqual("battlefield" if attacked else "graveyard", session.state.cards[aura.object_id].zone)
                self.replay(session)

    def test_actual_discard_and_mill_distinguish_any_player_and_controller_history(self):
        for index, spell_name in enumerate(("Mind Sculpt", "Mind Rot")):
            with self.subTest(spell=spell_name):
                session = self.session(603_694_121 + index)
                engine = session.engine
                raven = self.add(engine, "The Raven Man")
                mirage = self.add(engine, "Mysterio's Mirage")
                cause = self.add(engine, spell_name, zone="hand")
                chosen = [
                    self.add(engine, "Generic Bound Plains", seat="B", zone="hand", ref=f"history-discard-{i}")
                    for i in range(2)
                ]
                private = self.add(engine, "Generic Bound Growth", seat="B", zone="hand", ref="history-untouched")
                for source in (raven, mirage, cause):
                    self.assert_closed(session, source)
                discard = spell_name == "Mind Rot"
                self.prepare(session, mana={"C": 2, "B": 1} if discard else {"C": 1, "U": 1})
                self.checkpoint(session)
                self.cast(session, "A", cause, ["B"])
                pending = self.resolve(session)
                if discard:
                    self.assertIsNotNone(pending)
                    self.assertEqual(("pilot:B",), tuple(session.pending_principals()))
                    opponent = session.packet("pilot:A", full=True)
                    self.assertIsNone(opponent["decision"])
                    self.assertNotIn(private.ref, json.dumps(opponent))
                    self.assertNotIn(chosen[0].ref, json.dumps(opponent))
                    session = self.replay(session, load=True)
                    unchanged = authoritative_state_hash(session.state)
                    command = {"action_id": "choose", "cards": [card.ref for card in chosen]}
                    self.assertFalse(session.act("pilot:C", command).ok)
                    self.assertEqual(unchanged, authoritative_state_hash(session.state))
                    result = session.act("pilot:B", command)
                    self.assertTrue(result.ok, result.summary)
                    self.assertIsNone(self.resolve(session))
                else:
                    self.assertIsNone(pending)
                self.end_step(session)
                self.assertEqual(discard, self._source_on_stack(session, raven))
                self.assertFalse(self._source_on_stack(session, mirage))
                self.assertIsNone(self.resolve(session))
                self.assertEqual(["Bird"] if discard else [], self._token_names(session))
                self.replay(session)

    def test_actual_macabre_discount_requires_a_creature_arrival_in_its_own_graveyard(self):
        session = self.session(603_694_131)
        engine = session.engine
        spell = self.add(engine, "Macabre Reconstruction", zone="hand")
        old = self.add(engine, "Generic Bound Body", zone="graveyard", ref="history-old-arrival")
        own = self.add(engine, "Grizzly Bears", ref="history-own-new-arrival")
        foreign = self.add(engine, "Grizzly Bears", seat="C", ref="history-foreign-arrival")
        shocks = [self.add(engine, "Shock", seat="B", zone="hand", ref=f"history-shock-{i}") for i in range(2)]
        self.assert_closed(session, spell)
        self.prepare(session, mana={"C": 1, "B": 1})
        session.state.players["B"].mana_pool["R"] = 2
        self.checkpoint(session)
        self.cast_for(session, "B", shocks[0], [foreign.ref])
        self.assertIsNone(self.resolve(session))
        self.assertEqual("graveyard", session.state.cards[foreign.object_id].zone)
        self.priority(session, "A")
        before = authoritative_state_hash(session.state)
        rejected = session.act("pilot:A", {"action_id": f"cast:{spell.ref}", "targets": [old.ref], "pay": "auto"})
        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        self.cast_for(session, "B", shocks[1], [own.ref])
        self.assertIsNone(self.resolve(session))
        self.priority(session, "A")
        current = session.state.cards[own.object_id]
        self.assertEqual("graveyard", current.zone)
        self.cast(session, "A", spell, [current.ref])
        self.assertIsNone(self.resolve(session))
        self.assertEqual("hand", session.state.cards[own.object_id].zone)
        self.assertEqual("graveyard", session.state.cards[old.object_id].zone)
        self.assertEqual(0, sum(session.state.players["A"].mana_pool.values()))
        self.replay(session)

    def test_actual_spell_kind_and_planeswalker_entry_conditions_keep_separate_facts(self):
        session = self.session(603_694_141)
        engine = session.engine
        eshki = self.add(engine, "Eshki Dragonclaw")
        fae = self.add(engine, "Fae Offering")
        oath = self.add(engine, "Oath of Liliana")
        foreign_oath = self.add(engine, "Oath of Chandra", seat="B")
        walker = self.add(engine, "Garruk Wildspeaker", zone="hand")
        creature = self.add(engine, "Memnite", zone="hand")
        for source in (eshki, fae, oath, foreign_oath, walker, creature):
            self.assert_closed(session, source)
        self.prepare(session, mana={"C": 2, "G": 2})
        self.checkpoint(session)
        self.cast(session, "A", creature)
        self.assertIsNone(self.resolve(session))
        self.cast(session, "A", walker)
        self.assertIsNone(self.resolve(session))
        self.until(session, lambda s: s.state.step == "beginning_combat")
        self.assertTrue(self._source_on_stack(session, eshki))
        hand = len(session.state.players["A"].zones["hand"])
        self.assertIsNone(self.resolve(session))
        self.assertEqual(hand + 1, len(session.state.players["A"].zones["hand"]))
        self.assertEqual(2, session.state.cards[eshki.object_id].counters.get("+1/+1"))
        self.end_step(session)
        self.assertTrue(self._source_on_stack(session, oath))
        self.assertTrue(self._source_on_stack(session, fae))
        self.assertFalse(self._source_on_stack(session, foreign_oath))
        self.assertIsNone(self.resolve(session))
        self.assertCountEqual(["Zombie", "Clue", "Food", "Treasure"], self._token_names(session))
        self.replay(session)

    def test_actual_lunar_requires_both_life_directions_from_its_controller(self):
        for index, target in enumerate(("A", "B")):
            with self.subTest(loss_target=target):
                session = self.session(603_694_151 + index)
                engine = session.engine
                lunar = self.add(engine, "Lunar Convocation")
                gain = self.add(engine, "Chaplain's Blessing", zone="hand")
                loss = self.add(engine, "Sign in Blood", zone="hand")
                self.assert_closed(session, lunar)
                self.prepare(session, mana={"W": 1, "B": 2})
                self.checkpoint(session)
                self.cast_for(session, "A", gain, reserve={"B": 2})
                self.assertIsNone(self.resolve(session))
                self.cast(session, "A", loss, [target])
                self.assertIsNone(self.resolve(session))
                self.end_step(session)
                triggers = sum(item.source_object_id == lunar.object_id for item in session.state.stack)
                self.assertEqual(2 if target == "A" else 1, triggers)
                self.assertIsNone(self.resolve(session))
                self.assertEqual(["Bat"] if target == "A" else [], self._token_names(session))
                self.replay(session)
