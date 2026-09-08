from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from common import advance_fixture_turn, keep_all, load_assets
from quorune import CommanderSession, GameConfig
from quorune.model import StackItem
from quorune.record import (
    authoritative_state_hash,
    checkpoint_envelope,
    replay_record,
)
from quorune.turn_step_owner import TURN_STEPS


class InteractionKernelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db, cls.mishra, cls.zimone = load_assets()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def make_session(self, seed: int) -> CommanderSession:
        session = CommanderSession.create(
            self.db,
            {"A": self.zimone, "B": self.mishra},
            first_player="A",
            seed=seed,
            config=GameConfig(
                seed=seed,
                profile="commander_duel",
                auto_pass_empty_priority=False,
            ),
        )
        keep_all(session)
        session.engine.permissions.invalidate_current()
        session.engine.state.pending_decision = None
        return session

    @staticmethod
    def card(engine, name: str, owner: str | None = None):
        return next(
            card
            for card in engine.state.cards.values()
            if card.printed_name == name
            and (owner is None or card.owner == owner)
        )

    def put_spell_on_stack(
        self,
        engine,
        name: str,
        *,
        owner: str,
        ref: str,
    ) -> StackItem:
        card = self.card(engine, name, owner)
        engine._remove_from_zone(card)
        card.zone = "stack"
        card.controller = owner
        card.known_to = list(engine.seats)
        card.revealed_to = list(engine.seats)
        item = StackItem(
            stack_id=f"test-{ref}",
            ref=ref,
            kind="spell",
            controller=owner,
            label=name,
            card_object_id=card.object_id,
            default_destination=(
                "battlefield"
                if self.db.lookup(name).is_permanent_spell
                else "graveyard"
            ),
            visibility=list(engine.seats),
        )
        engine.state.stack.append(item)
        return item

    @staticmethod
    def set_window(engine, seat: str, *, active: str | None = None):
        engine.state.active_player = active or seat
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.priority_player = seat
        engine.state.priority_passes = []

    @staticmethod
    def resolve_top(engine):
        engine.state.priority_player = None
        engine._prepare_stack_resolution()

    def test_force_of_negation_pitch_is_exact_and_turn_restricted(self):
        session = self.make_session(710)
        engine = session.engine
        force = self.card(engine, "Force of Negation", "A")
        pitch = self.card(engine, "An Offer You Can't Refuse", "A")
        target = self.put_spell_on_stack(
            engine, "Sol Ring", owner="B", ref="S-force-target"
        )
        engine.move_card(force.object_id, "hand")
        engine.move_card(pitch.object_id, "hand")
        self.set_window(engine, "A", active="B")

        action = next(
            row
            for row in engine._priority_action_hints("A")["actions"]
            if row.get("card") == force.ref
        )
        pitch_option = next(
            option
            for option in action["cost_options"]
            if option["id"] == "pitch"
        )
        self.assertEqual(
            [pitch.ref],
            pitch_option["choice_schema"]["exile_card"]["legal_refs"],
        )
        engine._cast(
            "A",
            {
                "card": force.ref,
                "targets": [target.ref],
                "cost_option": "pitch",
                "exile_card": pitch.ref,
            },
        )
        self.assertEqual("exile", pitch.zone)
        self.assertEqual("pitch", engine.state.stack[-1].context["cost_option"])
        self.resolve_top(engine)
        self.assertEqual("exile", self.card(engine, "Sol Ring", "B").zone)

        other = self.make_session(711)
        other_engine = other.engine
        other_force = self.card(other_engine, "Force of Negation", "A")
        other_pitch = self.card(
            other_engine, "An Offer You Can't Refuse", "A"
        )
        self.put_spell_on_stack(
            other_engine,
            "Sol Ring",
            owner="B",
            ref="S-own-turn-target",
        )
        other_engine.move_card(other_force.object_id, "hand")
        other_engine.move_card(other_pitch.object_id, "hand")
        self.set_window(other_engine, "A", active="A")
        hints = other_engine._priority_action_hints("A")
        diagnostic = next(
            row
            for row in hints["diagnostic"]["unpayable"]
            if row.get("card") == other_force.ref
        )
        self.assertEqual("mandatory_cost_unpayable", diagnostic["reason"])

    def test_mana_drain_schedules_exact_next_main_phase_mana(self):
        session = self.make_session(712)
        engine = session.engine
        drain = self.card(engine, "Mana Drain", "A")
        target = self.put_spell_on_stack(
            engine, "Panharmonicon", owner="B", ref="S-drain-target"
        )
        engine.move_card(drain.object_id, "hand")
        engine.state.players["A"].mana_pool["U"] = 2
        self.set_window(engine, "A", active="B")
        engine._cast("A", {"card": drain.ref, "targets": [target.ref]})
        self.resolve_top(engine)
        self.assertFalse(any(item.ref == target.ref for item in engine.state.stack))
        delayed = next(
            trigger
            for trigger in engine.state.delayed_triggers
            if trigger.label == "Mana Drain delayed mana"
        )
        self.assertEqual(
            ["precombat_main", "postcombat_main"],
            delayed.condition["phase"],
        )
        matches = engine._matching_delayed_triggers(
            "step.begin",
            {
                "player": "A",
                "phase": "postcombat_main",
                "step": "main",
            },
        )
        self.assertEqual([delayed.ref], [trigger.ref for trigger in matches])
        engine._start_trigger_batch(matches, after="grant_priority")
        self.resolve_top(engine)
        self.assertEqual(4, engine.state.players["A"].mana_pool["C"])

    def test_swan_song_filter_and_flying_bird(self):
        session = self.make_session(713)
        engine = session.engine
        swan = self.card(engine, "Swan Song", "A")
        target = self.put_spell_on_stack(
            engine, "Chaos Warp", owner="B", ref="S-swan-target"
        )
        engine.move_card(swan.object_id, "hand")
        engine.state.players["A"].mana_pool["U"] = 1
        self.set_window(engine, "A", active="B")
        engine._cast("A", {"card": swan.ref, "targets": [target.ref]})
        self.resolve_top(engine)
        bird = next(
            card
            for card in engine.state.cards.values()
            if card.zone == "battlefield"
            and card.controller == "B"
            and card.printed_name == "Bird"
        )
        self.assertEqual(
            {"Flying"},
            set(engine._effective_card_data(bird)["keywords"]),
        )

    def test_tear_asunder_and_vandalblast_cost_options_change_targets(self):
        session = self.make_session(714)
        engine = session.engine
        tear = self.card(engine, "Tear Asunder", "A")
        artifact = self.card(engine, "Sol Ring", "B")
        creature = self.card(engine, "Goblin Engineer", "B")
        for card in (artifact, creature):
            engine.move_card(card.object_id, "battlefield", controller="B")
        engine.move_card(tear.object_id, "hand")
        engine.state.players["A"].mana_pool.update(
            {"C": 2, "G": 1, "B": 1}
        )
        self.set_window(engine, "A")
        action = next(
            row
            for row in engine._priority_action_hints("A")["actions"]
            if row.get("card") == tear.ref
        )
        normal = next(
            option for option in action["cost_options"] if option["id"] == "normal"
        )
        kicked = next(
            option for option in action["cost_options"] if option["id"] == "kicked"
        )
        self.assertNotIn(
            creature.ref, normal["target_schema"]["legal_refs"]
        )
        self.assertIn(
            creature.ref, kicked["target_schema"]["legal_refs"]
        )

        blast_session = self.make_session(715)
        blast_engine = blast_session.engine
        blast = self.card(blast_engine, "Vandalblast", "B")
        first = self.card(blast_engine, "Sol Ring", "B")
        second = self.card(blast_engine, "Panharmonicon", "B")
        own = self.card(blast_engine, "Sensei's Divining Top", "B")
        blast_engine.move_card(first.object_id, "battlefield", controller="A")
        blast_engine.move_card(second.object_id, "battlefield", controller="A")
        blast_engine.move_card(own.object_id, "battlefield", controller="B")
        blast_engine.move_card(blast.object_id, "hand")
        blast_engine.state.players["B"].mana_pool.update(
            {"C": 4, "R": 1}
        )
        self.set_window(blast_engine, "B")
        blast_action = next(
            row
            for row in blast_engine._priority_action_hints("B")["actions"]
            if row.get("card") == blast.ref
        )
        overload = next(
            option
            for option in blast_action["cost_options"]
            if option["id"] == "overload"
        )
        self.assertEqual([], overload["target_schema"]["groups"])
        blast_engine._cast(
            "B",
            {
                "card": blast.ref,
                "cost_option": "overload",
                "targets": [],
            },
        )
        self.resolve_top(blast_engine)
        self.assertEqual("graveyard", first.zone)
        self.assertEqual("graveyard", second.zone)
        self.assertEqual("battlefield", own.zone)

    def test_toxic_deluge_pays_x_life_and_applies_until_cleanup(self):
        session = self.make_session(716)
        engine = session.engine
        deluge = self.card(engine, "Toxic Deluge", "A")
        small = self.card(engine, "Birds of Paradise", "A")
        large = self.card(engine, "Mishra, Eminent One", "B")
        engine.move_card(small.object_id, "battlefield", controller="A")
        engine.move_card(large.object_id, "battlefield", controller="B")
        engine.move_card(deluge.object_id, "hand")
        engine.state.players["A"].life = 10
        engine.state.players["A"].mana_pool.update({"C": 2, "B": 1})
        self.set_window(engine, "A")
        action = next(
            row
            for row in engine._priority_action_hints("A")["actions"]
            if row.get("card") == deluge.ref
        )
        normal = next(
            option for option in action["cost_options"] if option["id"] == "normal"
        )
        self.assertEqual(
            10, normal["choice_schema"]["x"]["maximum"]
        )
        engine._cast("A", {"card": deluge.ref, "x": 2})
        self.assertEqual(8, engine.state.players["A"].life)
        self.resolve_top(engine)
        self.assertEqual("graveyard", small.zone)
        self.assertEqual("battlefield", large.zone)
        self.assertEqual(2, engine._numeric_stat(large.object_id, "toughness"))
        engine._finish_cleanup()
        self.assertEqual(4, engine._numeric_stat(large.object_id, "toughness"))

    def test_removal_family_target_filters(self):
        session = self.make_session(717)
        engine = session.engine
        trophy = self.card(engine, "Assassin's Trophy", "A")
        abrade = self.card(engine, "Abrade", "B")
        feed = self.card(engine, "Feed the Swarm", "B")
        artifact = self.card(engine, "Sol Ring", "B")
        creature = self.card(engine, "Birds of Paradise", "A")
        own_enchantment = self.card(engine, "Mystic Remora", "A")
        for card, controller in (
            (artifact, "B"),
            (creature, "A"),
            (own_enchantment, "A"),
        ):
            engine.move_card(
                card.object_id, "battlefield", controller=controller
            )
        for card in (trophy,):
            engine.move_card(card.object_id, "hand")
        engine.state.players["A"].mana_pool.update({"B": 1, "G": 1})
        self.set_window(engine, "A")
        trophy_action = next(
            row
            for row in engine._priority_action_hints("A")["actions"]
            if row.get("card") == trophy.ref
        )
        self.assertEqual(
            [artifact.ref],
            trophy_action["target_schema"]["legal_refs"],
        )

        for card in (abrade, feed):
            engine.move_card(card.object_id, "hand")
        engine.state.players["B"].mana_pool.update(
            {"C": 2, "R": 1, "B": 1}
        )
        self.set_window(engine, "B")
        hints = engine._priority_action_hints("B")
        abrade_action = next(
            row for row in hints["actions"] if row.get("card") == abrade.ref
        )
        feed_action = next(
            row for row in hints["actions"] if row.get("card") == feed.ref
        )
        self.assertIn(
            artifact.ref,
            abrade_action["target_schema"]["mode_schemas"]["destroy"][
                "groups"
            ][0]["legal_refs"],
        )
        self.assertIn(
            creature.ref,
            abrade_action["target_schema"]["mode_schemas"]["damage"][
                "groups"
            ][0]["legal_refs"],
        )
        self.assertIn(
            creature.ref, feed_action["target_schema"]["legal_refs"]
        )
        self.assertIn(
            own_enchantment.ref,
            feed_action["target_schema"]["legal_refs"],
        )
        self.assertNotIn(
            artifact.ref, feed_action["target_schema"]["legal_refs"]
        )

    def test_pact_delayed_payment_failure_loses_game(self):
        session = self.make_session(718)
        engine = session.engine
        pact = self.card(engine, "Pact of Negation", "A")
        target = self.put_spell_on_stack(
            engine, "Panharmonicon", owner="B", ref="S-pact-target"
        )
        engine.move_card(pact.object_id, "hand")
        self.set_window(engine, "A", active="B")
        engine._cast("A", {"card": pact.ref, "targets": [target.ref]})
        self.resolve_top(engine)
        delayed = next(
            trigger
            for trigger in engine.state.delayed_triggers
            if trigger.label == "Pact of Negation delayed payment"
        )
        advance_fixture_turn(engine)
        matches = engine._matching_delayed_triggers(
            "step.begin",
            {
                "player": "A",
                "phase": "beginning",
                "step": "upkeep",
            },
        )
        self.assertEqual([delayed.ref], [trigger.ref for trigger in matches])
        engine._start_trigger_batch(matches, after="grant_priority")
        self.resolve_top(engine)
        result = session.act(
            "pilot:A",
            {
                "action_id": "choose",
                "pay": False,
                "plan": "ACCEPT_LOSS",
                "reason": "The delayed Pact payment is not payable.",
            },
        )
        self.assertTrue(result.ok, result.summary)
        self.assertFalse(engine.state.players["A"].in_game)
        self.assertEqual("B", engine.state.winner)

    def test_pact_delayed_payment_can_be_paid(self):
        session = self.make_session(7181)
        engine = session.engine
        pact = self.card(engine, "Pact of Negation", "A")
        target = self.put_spell_on_stack(
            engine, "Panharmonicon", owner="B", ref="S-paid-pact-target"
        )
        engine.move_card(pact.object_id, "hand")
        self.set_window(engine, "A", active="B")
        engine._cast("A", {"card": pact.ref, "targets": [target.ref]})
        self.resolve_top(engine)
        delayed = next(
            trigger
            for trigger in engine.state.delayed_triggers
            if trigger.label == "Pact of Negation delayed payment"
        )
        engine.state.players["A"].mana_pool.update(
            {"C": 3, "U": 2}
        )
        advance_fixture_turn(engine)
        matches = engine._matching_delayed_triggers(
            "step.begin",
            {
                "player": "A",
                "phase": "beginning",
                "step": "upkeep",
            },
        )
        self.assertEqual([delayed.ref], [trigger.ref for trigger in matches])
        engine._start_trigger_batch(matches, after="grant_priority")
        self.resolve_top(engine)

        result = session.act(
            "pilot:A",
            {
                "action_id": "choose",
                "pay": True,
                "plan": "PAY_PACT",
                "reason": "The delayed Pact cost is payable.",
            },
        )

        self.assertTrue(result.ok, result.summary)
        self.assertTrue(engine.state.players["A"].in_game)
        self.assertEqual(
            0, sum(engine.state.players["A"].mana_pool.values())
        )

    def test_active_player_pact_loss_skips_draw_and_replays_in_four_players(self):
        session = CommanderSession.create(
            self.db,
            {
                "A": self.zimone,
                "B": self.mishra,
                "C": self.zimone,
                "D": self.mishra,
            },
            first_player="A",
            seed=7182,
            config=GameConfig(
                seed=7182,
                profile="commander_multiplayer",
                semantic_policy="trusted_only",
                auto_pass_empty_priority=True,
            ),
        )
        keep_all(session)
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        pact = self.card(engine, "Pact of Negation", "A")
        target = self.put_spell_on_stack(
            engine,
            "Panharmonicon",
            owner="B",
            ref="S-four-player-pact-target",
        )
        engine.move_card(pact.object_id, "hand")
        self.set_window(engine, "A")
        engine._cast("A", {"card": pact.ref, "targets": [target.ref]})
        self.resolve_top(engine)
        delayed = next(
            trigger
            for trigger in engine.state.delayed_triggers
            if trigger.label == "Pact of Negation delayed payment"
        )
        advance_fixture_turn(engine)
        engine.state.active_player = "A"
        engine.state.phase_index = TURN_STEPS.index(("beginning", "upkeep"))
        engine.state.phase = "beginning"
        engine.state.step = "upkeep"
        matches = engine._matching_delayed_triggers(
            "step.begin",
            {
                "player": "A",
                "phase": "beginning",
                "step": "upkeep",
            },
        )
        self.assertEqual([delayed.ref], [trigger.ref for trigger in matches])
        engine._start_trigger_batch(matches, after="grant_priority")
        self.resolve_top(engine)
        self.assertEqual("semantic.choice", engine.state.pending_decision.kind)
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        result = session.act(
            "pilot:A",
            {
                "action_id": "choose",
                "pay": False,
                "plan": "ACCEPT_LOSS",
                "reason": "The delayed Pact payment is not payable.",
            },
        )

        self.assertTrue(result.ok, result.summary)
        self.assertFalse(engine.state.players["A"].in_game)
        self.assertFalse(engine.state.game_over)
        elimination = next(
            event
            for event in engine.state.events
            if event.code == "player.eliminated" and event.actor == "A"
        )
        skipped_draw = next(
            event
            for event in engine.state.events
            if event.code == "draw.skip"
            and event.actor == "A"
            and event.details.get("reason") == "active_player_left_game"
        )
        self.assertGreater(skipped_draw.event_id, elimination.event_id)
        self.assertFalse(
            any(
                event.code == "card.draw.private"
                and event.actor == "A"
                and event.event_id > elimination.event_id
                for event in engine.state.events
            )
        )
        expected_hash = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "four-player-active-pact-loss"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_pithing_needle_name_and_ability_suppression(self):
        session = self.make_session(719)
        engine = session.engine
        needle = self.card(engine, "Pithing Needle", "B")
        top = self.card(engine, "Sensei's Divining Top", "B")
        engine.move_card(top.object_id, "battlefield", controller="B")
        engine.move_card(needle.object_id, "hand")
        engine.state.players["B"].mana_pool["C"] = 1
        self.set_window(engine, "B")
        engine._cast("B", {"card": needle.ref})
        self.resolve_top(engine)
        result = session.act(
            "pilot:B",
            {
                "action_id": "choose",
                "card_name": "Sensei's Divining Top",
                "plan": "DISRUPT_ENGINE",
                "reason": "Name the visible activated-ability engine.",
            },
        )
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(
            "Sensei's Divining Top",
            needle.annotations["chosen_name"],
        )
        hints = engine._priority_action_hints("B")
        self.assertFalse(
            any(
                ability.get("s") == top.ref
                for ability in hints["abilities"]
            )
        )
        self.assertTrue(
            any(
                ability.get("s") == top.ref
                and ability.get("reason") == "named_ability_prohibition"
                for ability in hints["diagnostic"]["unpayable"]
                + hints["diagnostic"]["unresolved_cost_semantics"]
            )
            or not any(
                ability.get("s") == top.ref
                for ability in hints["abilities"]
            )
        )

    def test_cankerbloom_modes_and_proliferate(self):
        session = self.make_session(720)
        engine = session.engine
        canker = self.card(engine, "Cankerbloom", "A")
        artifact = self.card(engine, "Sol Ring", "B")
        engine.move_card(canker.object_id, "battlefield", controller="A")
        engine.move_card(artifact.object_id, "battlefield", controller="B")
        artifact.counters["charge"] = 1
        engine.state.players["A"].mana_pool["C"] = 1
        self.set_window(engine, "A")
        hints = engine._priority_action_hints("A")
        action = next(
            row
            for row in hints["actions"]
            if row.get("source") == canker.ref
        )
        self.assertEqual(
            {
                "destroy_artifact",
                "proliferate",
            },
            set(action["target_schema"]["legal_modes"]),
        )
        engine._activate(
            "A",
            {
                "source": canker.ref,
                "ability": "ab1",
                "modes": ["proliferate"],
                "targets": [],
            },
        )
        self.assertEqual("graveyard", canker.zone)
        self.resolve_top(engine)
        result = session.act(
            "pilot:A",
            {
                "action_id": "choose",
                "objects": [artifact.ref],
                "plan": "GROW_RESOURCES",
                "reason": "Increase the existing public charge counter.",
            },
        )
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(2, artifact.counters["charge"])

    def test_soul_guide_lantern_self_entry_target(self):
        session = self.make_session(721)
        engine = session.engine
        lantern = self.card(engine, "Soul-Guide Lantern", "B")
        grave_card = self.card(engine, "Birds of Paradise", "A")
        engine.move_card(grave_card.object_id, "graveyard")
        engine.move_card(lantern.object_id, "hand")
        engine.state.players["B"].mana_pool["C"] = 1
        self.set_window(engine, "B")
        engine._cast("B", {"card": lantern.ref})
        self.resolve_top(engine)
        trigger = next(
            item
            for item in engine.state.stack
            if item.semantic_key
            == f"{lantern.oracle_id}:trigger:etb"
        )
        self.assertEqual(lantern.object_id, trigger.source_object_id)
        self.assertEqual(
            "semantic.target", engine.state.pending_decision.kind
        )
        result = session.act(
            "pilot:B",
            {
                "action_id": "choose",
                "targets": [grave_card.ref],
                "plan": "EXILE_GRAVEYARD",
                "reason": "Exile the only legal graveyard card.",
            },
        )
        self.assertTrue(result.ok, result.summary)
        self.resolve_top(engine)
        self.assertEqual("exile", grave_card.zone)

    def test_flusterstorm_storm_and_target_reassignment(self):
        session = self.make_session(722)
        engine = session.engine
        fluster = self.card(engine, "Flusterstorm", "A")
        first = self.put_spell_on_stack(
            engine, "Chaos Warp", owner="B", ref="S-storm-first"
        )
        second = self.put_spell_on_stack(
            engine, "Abrade", owner="B", ref="S-storm-second"
        )
        engine._log(
            "B",
            "stack.cast",
            "Synthetic prior spell one.",
            {"stack": first.ref},
        )
        engine._log(
            "B",
            "stack.cast",
            "Synthetic prior spell two.",
            {"stack": second.ref},
        )
        for item in (first, second):
            engine._record_turn_history(
                "spell_cast",
                actor="B",
                object_incarnation=(
                    engine.state.cards[item.card_object_id].logical_object_id
                    if item.card_object_id is not None
                    else None
                ),
                types=("instant",),
            )
        engine.move_card(fluster.object_id, "hand")
        engine.state.players["A"].mana_pool["U"] = 1
        self.set_window(engine, "A", active="B")
        engine._cast(
            "A", {"card": fluster.ref, "targets": [first.ref]}
        )
        storm_trigger = engine.state.stack[-1]
        self.assertEqual("builtin:storm", storm_trigger.semantic_key)
        self.assertEqual(2, storm_trigger.context["copy_count"])
        self.resolve_top(engine)
        result = session.act(
            "pilot:A",
            {
                "action_id": "choose",
                "copy_targets": [[first.ref], [second.ref]],
                "plan": "COUNTER_WAR",
                "reason": "Assign one storm copy to each exposed spell.",
            },
        )
        self.assertTrue(result.ok, result.summary)
        copies = [
            item
            for item in engine.state.stack
            if item.kind == "spell_copy"
            and item.semantic_key
            == f"{fluster.oracle_id}:spell:front"
        ]
        self.assertEqual(2, len(copies))
        self.assertEqual(
            [[first.ref], [second.ref]],
            [copy_item.targets for copy_item in copies],
        )


if __name__ == "__main__":
    unittest.main()
