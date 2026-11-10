"""Current-turn conditions use sealed facts and ordinary trigger resolution."""

import json
from pathlib import Path
import tempfile
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.attachments import attach_objects
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
    def test_movement_history_queries_require_their_exact_facts_and_version(self):
        from types import SimpleNamespace
        from quorune.model import TurnHistory, TurnHistoryEvent
        from quorune.public_history_query_model import PUBLIC_MOVEMENT_HISTORY_CAPABILITY, public_history_query_value

        rows = (
            TurnHistoryEvent("permanent_arrived_hand", actor="A", object_incarnation="owner-hand", types=("land",)),
            TurnHistoryEvent("card_arrived_exile", actor="B", object_incarnation="hidden-exile"),
            TurnHistoryEvent("artifact_subtype_sacrificed", actor="A", object_incarnation="food",
                             target="food", target_kind="artifact_subtype"),
            TurnHistoryEvent("permanent_died", actor="A", object_incarnation="current-source",
                             types=("creature",), creature_subtypes=("human",), token=False),
        )
        history = TurnHistory(turn_sequence=7, events=list(rows), public_movement_history_version=1,
                              permanent_death_history_version=1)
        self.assertEqual(history, TurnHistory.from_dict(history.to_dict()))
        with self.assertRaisesRegex(ValueError, "Public movement"):
            TurnHistory.from_dict({k: v for k, v in history.to_dict().items() if k != "public_movement_history_version"})
        with self.assertRaises(ValueError):
            TurnHistoryEvent("card_arrived_exile", actor="A", object_incarnation="hidden", types=("creature",))
        with self.assertRaises(ValueError):
            TurnHistoryEvent("artifact_subtype_sacrificed", actor="A", object_incarnation="other",
                             target="human", target_kind="artifact_subtype")
        state = SimpleNamespace(turn_sequence=7, turn_history=history,
                                players={seat: SimpleNamespace(in_game=True) for seat in "AB"})
        cases = (
            ("a permanent was put into your hand from the battlefield this turn", 1),
            ("one or more cards were put into exile this turn", 1),
            ("you sacrificed a Food this turn", 1),
            ("you sacrificed a Clue this turn", 0),
            ("another Human died under your control this turn", 0),
        )
        for text, expected in cases:
            with self.subTest(text=text):
                condition = fixed_public_state_condition(text, source_name="Movement witness")
                query = condition.history_query
                self.assertEqual(query, PublicHistoryQuerySpec.from_dict(query.to_dict()))
                self.assertIn(PUBLIC_MOVEMENT_HISTORY_CAPABILITY, query.capability_dependencies)
                self.assertEqual(expected, public_history_query_value(state, "A", query,
                                                                      source_incarnation="current-source"))
                if query.exclude_source:
                    self.assertIsNone(public_history_query_value(state, "A", query))
                    self.assertEqual(1, public_history_query_value(state, "A", query,
                                                                   source_incarnation="different-source"))
        state.turn_history = TurnHistory(turn_sequence=7)
        query = fixed_public_state_condition(cases[0][0], source_name="Movement witness").history_query
        self.assertIsNone(public_history_query_value(state, "A", query))

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
        self.assertEqual(["Clue Token", "Food Token", "Treasure Token"], [token["name"] for token in observed.effect["tokens"]])
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

    def test_original_movement_history_triggers_require_independent_runtime_authority(self):
        from dataclasses import replace
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.public_history_query_model import PUBLIC_MOVEMENT_HISTORY_CAPABILITY

        session = self.session(846001)
        for name in ("Ichor Shade", "White Glove Gourmand", "Barrin, Tolarian Archmage",
                     "Ennis, Debate Moderator", "Elanor Gardner", "Kami of Transience"):
            with self.subTest(card=name):
                source = self.add(session.engine, name)
                ir = compile_oracle_card(self.db.lookup(name), capability_registry=self.registry,
                                         capability_profile="commander_review")
                self.assertEqual("exact", ir.status, ir.material_residuals)
                self.assert_closed(session, source)
                trigger, = (p for p in session.engine.semantics.programs_for_oracle(source.oracle_id)
                            if PUBLIC_MOVEMENT_HISTORY_CAPABILITY in p.capability_dependencies)
                self.assertEqual("trusted", trigger.trust_level)
                self.assertEqual("graveyard" if name == "Kami of Transience" else "battlefield", trigger.active_zone)
                dependencies = tuple(cap for cap in trigger.capability_dependencies
                                     if cap != PUBLIC_MOVEMENT_HISTORY_CAPABILITY)
                changed = replace(trigger, capability_dependencies=dependencies,
                                  capability_closure=self.registry.closure(dependencies, profile="commander_review").to_dict())
                bound = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile="commander_review")
                self.assertFalse(bound["strict"], bound)
                self.assertIn("capability:undeclared_runtime_dependency:" + PUBLIC_MOVEMENT_HISTORY_CAPABILITY,
                              bound["blockers"])

    def test_actual_food_sacrifice_separates_exile_arrival_and_graveyard_triggers(self):
        for index, (is_token, replacement_present) in enumerate(((True, False), (True, True), (False, True))):
            with self.subTest(token=is_token, replacement_present=replacement_present):
                session = self.session(846002 + index)
                ichor = self.add(session.engine, "Ichor Shade")
                ennis = self.add(session.engine, "Ennis, Debate Moderator")
                elanor = self.add(session.engine, "Elanor Gardner", zone="hand" if is_token else "battlefield")
                private = self.add(session.engine, "Generic Bound Growth", seat="D", zone="hand")
                for source in (ichor, ennis, elanor):
                    self.assert_closed(session, source)
                if replacement_present:
                    self.add(session.engine, "Dauthi Voidwalker", seat="C")
                redirected = replacement_present and not is_token
                if not is_token:
                    food = self.add(session.engine, "Gingerbrute")
                    rites = self.add(session.engine, "Village Rites", zone="hand")
                self.prepare(session, mana={"G": 1, "C": 5, "B": 1})
                self.checkpoint(session)
                if is_token:
                    self.cast_for(session, "A", elanor)
                    self.assertIsNone(self.resolve(session))
                    self.priority(session, "A")
                    food, = (card for card in session.state.cards.values() if card.zone == "battlefield"
                             and card.is_token and card.printed_name == "Food Token")
                    offers = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
                    offer, = (row for row in offers if row["id"].startswith(f"activate:{food.ref}:"))
                    command = {"action_id": offer["id"], "pay": "auto"}
                else:
                    offers = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
                    offer, = (row for row in offers if row["id"] == "cast:" + rites.ref)
                    command = {"action_id": offer["id"], "sacrifice_cards": [food.ref], "pay": "auto"}
                original = food.logical_object_id
                before = authoritative_state_hash(session.state)
                self.assertFalse(session.act("pilot:D", command).ok)
                self.assertEqual(before, authoritative_state_hash(session.state))
                accepted = session.act("pilot:A", command)
                self.assertTrue(accepted.ok, accepted.summary)
                facts = session.state.turn_history.events
                qualified, = (event for event in facts if event.kind == "artifact_subtype_sacrificed")
                self.assertEqual(("A", original, "food"), (qualified.actor, qualified.object_incarnation, qualified.target))
                self.assertEqual(not redirected, any(event.kind == "permanent_arrived_graveyard"
                                                     and event.object_incarnation == original for event in facts))
                exiled = [event for event in facts if event.kind == "card_arrived_exile"]
                self.assertEqual(0 if is_token else 1, len(exiled))
                self.assertTrue(all(not event.types for event in exiled))
                self.assertIsNone(self.resolve(session))
                self.end_step(session)
                sources = {item.source_object_id for item in session.state.stack}
                expected = {elanor.object_id}
                if not redirected:
                    expected.add(ichor.object_id)
                if not is_token:
                    expected.add(ennis.object_id)
                self.assertEqual(expected, sources)
                optional = self.resolve(session)
                self.assertIsNotNone(optional)
                before = authoritative_state_hash(session.state)
                self.assertFalse(session.act("pilot:D", {"action_id": "choose", "choice": "decline"}).ok)
                self.assertEqual(before, authoritative_state_hash(session.state))
                result = session.act("pilot:A", {"action_id": "choose", "choice": "decline"})
                self.assertTrue(result.ok, result.summary)
                self.assertIsNone(self.resolve(session))
                self.assertEqual(int(not redirected), session.state.cards[ichor.object_id].counters.get("+1/+1", 0))
                self.assertEqual(int(not is_token), session.state.cards[ennis.object_id].counters.get("+1/+1", 0))
                self.assertNotIn(private.ref, json.dumps(session.packet("pilot:B", full=True)))
                self.replay(session)

    def test_actual_barrin_uses_hand_owner_instead_of_previous_controller(self):
        for index, owner in enumerate(("A", "B")):
            with self.subTest(owner=owner):
                session = self.session(846010 + index)
                barrin = self.add(session.engine, "Barrin, Tolarian Archmage")
                body = self.add(session.engine, "Grizzly Bears", seat=owner)
                caster = self.add(session.engine, "Boomerang", zone="hand")
                if owner == "A":
                    session.engine.change_control(body.object_id, "B", reason="Native owner/controller control")
                self.prepare(session, mana={"U": 2})
                self.checkpoint(session)
                self.cast_for(session, "A", caster, [body.ref])
                self.assertIsNone(self.resolve(session))
                fact, = (event for event in session.state.turn_history.events if event.kind == "permanent_arrived_hand")
                self.assertEqual(owner, fact.actor)
                self.assertIn(body.object_id, session.state.players[owner].zones["hand"])
                self.end_step(session)
                self.assertEqual(owner == "A", self._source_on_stack(session, barrin))
                hand = len(session.state.players["A"].zones["hand"])
                self.assertIsNone(self.resolve(session))
                self.assertEqual(hand + int(owner == "A"), len(session.state.players["A"].zones["hand"]))
                self.replay(session)

    def test_actual_white_glove_counts_prior_human_controller_at_death(self):
        for index, controller in enumerate(("A", "B")):
            with self.subTest(controller=controller):
                session = self.session(846020 + index)
                gourmand = self.add(session.engine, "White Glove Gourmand")
                human = self.add(session.engine, "Elite Vanguard", seat="B")
                shock = self.add(session.engine, "Shock", zone="hand")
                if controller == "A":
                    session.engine.change_control(human.object_id, "A", reason="Native owner/controller control")
                self.prepare(session, mana={"R": 1})
                self.checkpoint(session)
                self.cast_for(session, "A", shock, [human.ref])
                self.assertIsNone(self.resolve(session))
                fact, = (event for event in session.state.turn_history.events if event.kind == "permanent_died")
                self.assertEqual((controller, ("human", "soldier")), (fact.actor, fact.creature_subtypes))
                self.assertIn(human.object_id, session.state.players["B"].zones["graveyard"])
                self.end_step(session)
                self.assertEqual(controller == "A", self._source_on_stack(session, gourmand))
                self.assertIsNone(self.resolve(session))
                self.assertEqual(["Food Token"] if controller == "A" else [], self._token_names(session))
                self.replay(session)

    def test_actual_kami_requires_its_own_graveyard_and_an_enchantment_arrival(self):
        cases = (("graveyard", "A", True), ("graveyard", "B", False), ("battlefield", "A", False))
        for index, (kami_zone, enchantment_owner, returns) in enumerate(cases):
            with self.subTest(zone=kami_zone, enchantment_owner=enchantment_owner):
                session = self.session(846030 + index)
                kami = self.add(session.engine, "Kami of Transience", zone=kami_zone)
                enchantment = self.add(session.engine, "Fae Offering", seat=enchantment_owner)
                destroy = self.add(session.engine, "Naturalize", zone="hand")
                if enchantment_owner == "A":
                    session.engine.change_control(enchantment.object_id, "B", reason="Native owner/controller control")
                self.prepare(session, mana={"G": 1, "C": 1})
                self.checkpoint(session)
                self.cast_for(session, "A", destroy, [enchantment.ref])
                self.assertIsNone(self.resolve(session))
                self.assertIn(enchantment.object_id, session.state.players[enchantment_owner].zones["graveyard"])
                fact, = (event for event in session.state.turn_history.events if event.kind == "permanent_arrived_graveyard")
                self.assertEqual(enchantment_owner, fact.actor)
                self.assertIn("enchantment", fact.types)
                self.end_step(session)
                self.assertEqual(returns, self._source_on_stack(session, kami))
                if returns:
                    decision = self.resolve(session)
                    self.assertIsNotNone(decision)
                    before = authoritative_state_hash(session.state)
                    command = {"action_id": "choose", "choice": "apply"}
                    self.assertFalse(session.act("pilot:B", command).ok)
                    self.assertEqual(before, authoritative_state_hash(session.state))
                    accepted = session.act("pilot:A", command)
                    self.assertTrue(accepted.ok, accepted.summary)
                    self.assertIsNone(self.resolve(session))
                    self.assertIn(kami.object_id, session.state.players["A"].zones["hand"])
                self.replay(session)

    def test_actual_ennis_ignores_exiled_token_and_a_late_card_exile(self):
        session = self.session(846040)
        ennis = self.add(session.engine, "Ennis, Debate Moderator")
        producer = self.add(session.engine, "White Glove Gourmand", zone="hand")
        exile = self.add(session.engine, "Final Reward", zone="hand", ref="token-exile")
        late_exile = self.add(session.engine, "Final Reward", seat="B", zone="hand", ref="late-exile")
        card = self.add(session.engine, "Grizzly Bears")
        for index in range(5):
            self.add(session.engine, "Swamp", seat="B", ref=f"late-exile-source-{index}")
        self.prepare(session, mana={"W": 1, "B": 2, "C": 9})
        self.checkpoint(session)
        self.cast_for(session, "A", producer)
        self.assertIsNone(self.resolve(session))
        token = next(card for card in session.state.cards.values() if card.zone == "battlefield" and card.is_token)
        self.cast_for(session, "A", exile, [token.ref])
        self.assertIsNone(self.resolve(session))
        self.assertFalse(any(event.kind == "card_arrived_exile" for event in session.state.turn_history.events))
        self.end_step(session)
        self.assertFalse(self._source_on_stack(session, ennis))
        self.cast_for(session, "B", late_exile, [card.ref])
        self.assertIsNone(self.resolve(session))
        fact, = (event for event in session.state.turn_history.events if event.kind == "card_arrived_exile")
        self.assertEqual("A", fact.actor)
        self.assertEqual((), fact.types)
        self.assertEqual("exile", session.state.cards[card.object_id].zone)
        self.assertFalse(self._source_on_stack(session, ennis))
        self.assertEqual(0, session.state.cards[ennis.object_id].counters.get("+1/+1", 0))
        self.replay(session)

    def test_movement_condition_mutation_is_detected_by_native_barrin(self):
        from unittest.mock import patch
        from quorune.public_history_query_model import public_history_query_value

        def wrong_owner(state, controller, spec, **kwargs):
            if spec.event == "permanent_arrived_hand":
                controller = "B"
            return public_history_query_value(state, controller, spec, **kwargs)

        with patch("quorune.public_history_query_model.public_history_query_value", side_effect=wrong_owner):
            with self.assertRaises(AssertionError):
                method = "test_actual_barrin_uses_hand_owner_instead_of_previous_controller"
                getattr(type(self)(method), method)()

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
                self.assertEqual([] if responder == "A" else ["Wolf Token"], self._token_names(session))
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
                self.assertEqual(["Bird Token"] if discard else [], self._token_names(session))
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
        self.assertCountEqual(["Zombie Token", "Clue Token", "Food Token", "Treasure Token"], self._token_names(session))
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
                self.assertEqual(["Bat Token"] if target == "A" else [], self._token_names(session))
                self.replay(session)

    def test_actual_lunar_counts_simultaneous_damage_loss_and_lifelink_at_net_zero(self):
        session = self.session(603_694_152)
        engine = session.engine
        lunar = self.add(engine, "Lunar Convocation")
        pinger = self.add(engine, "Prodigal Sorcerer")
        collar = self.add(engine, "Basilisk Collar")
        attach_objects(session.state.cards, collar, pinger,
                       source_timestamp=collar.zone_timestamp, players=session.state.players)
        pinger.acquired_control_turn_count = -1
        for source in (lunar, pinger, collar):
            self.assert_closed(session, source)
        self.prepare(session)
        self.checkpoint(session)
        life = session.state.players["A"].life
        offer = next(row for row in session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
                     if row["id"].startswith(f"activate:{pinger.ref}:"))
        command = {"action_id": offer["id"], "targets": ["A"], "pay": "auto"}
        unchanged = authoritative_state_hash(session.state)
        self.assertFalse(session.act("pilot:B", command).ok)
        self.assertEqual(unchanged, authoritative_state_hash(session.state))
        accepted = session.act("pilot:A", command)
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(life, session.state.players["A"].life)
        facts = [(event.kind, event.amount) for event in session.state.turn_history.events
                 if event.target == "A" and event.kind in {"player_gained_life", "player_lost_life"}]
        self.assertCountEqual([("player_gained_life", 1), ("player_lost_life", 1)], facts)
        session = self.replay(session, load=True)
        self.end_step(session)
        triggers = sum(item.source_object_id == lunar.object_id for item in session.state.stack)
        self.assertEqual(2, triggers)
        opponent_life = session.state.players["B"].life
        self.assertIsNone(self.resolve(session))
        self.assertEqual(["Bat Token"], self._token_names(session))
        self.assertEqual(opponent_life - 1, session.state.players["B"].life)
        self.replay(session)
