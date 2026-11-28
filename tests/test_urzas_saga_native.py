from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT, keep_all
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.engine import TURN_STEPS
from quorune.library_search_model import PRINTED_MANA_COST_ARTIFACT_SEARCH_CAPABILITY_ID
from quorune.model import CardInstance, GameConfig
from quorune.oracle_ir import compile_oracle_card, register_generated_programs
from quorune.record import authoritative_state_hash, checkpoint_envelope, replay_record
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.saga_chapter_grants import SAGA_CHAPTER_GRANT_CAPABILITY
from quorune.saga_progression import advance_active_player_sagas
from quorune.session import CommanderSession
from scripts.build_test_database import build_fixture_database


PINNED_CARDS = ROOT / "tests" / "fixtures" / "urza-saga-pinned-cards.json"


class UrzasSagaNativeWitnessTests(unittest.TestCase):
    """Native command witnesses for Urza's Saga's three printed chapters."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory()
        database = Path(cls.temporary.name) / "urza-saga-witness.sqlite3"
        build_fixture_database(
            [
                ROOT / "tests" / "fixtures" / "bound-effect-program-cards.json",
                PINNED_CARDS,
            ],
            database,
        )
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.typed_saga_path = cls.registry.closure(
            (SAGA_CHAPTER_GRANT_CAPABILITY, PRINTED_MANA_COST_ARTIFACT_SEARCH_CAPABILITY_ID),
            profile="commander_review",
        ).trusted
        cls.deck = DeckDefinition(
            "Urza's Saga native witness",
            [
                DeckEntry("Generic Bound Commander", 1, "commander"),
                DeckEntry("Generic Bound Plains", 30),
            ],
            ["Generic Bound Commander"],
        )
        cls.records = {name: cls.db.lookup(name, fuzzy=False) for name in (
            "Urza's Saga",
            "Mishra's Bauble",
            "Sol Ring",
            "Aerial Doombot",
            "Engineered Explosives",
            "Arcane Signet",
        )}

    @classmethod
    def tearDownClass(cls) -> None:
        cls.db.close()
        cls.temporary.cleanup()

    def test_printed_search_lowering_is_required_for_chapter_three(self):
        record = self.records["Urza's Saga"]
        options = {"capability_registry": self.registry, "capability_profile": "commander_review"}
        chapter = compile_oracle_card(record, **options).faces[0].nodes[-1]
        self.assertEqual("saga.chapter.3", chapter.event)
        self.assertTrue(chapter.lowerable)
        with patch("quorune.compiler.saga_chapter_nodes.printed_mana_cost_artifact_search_template", return_value=None):
            mutant = compile_oracle_card(record, **options)
        self.assertEqual("unresolved", mutant.faces[0].nodes[-1].event)
        self.assertTrue(any(
            residual.kind == "saga_chapter" and residual.span.line == chapter.span.line
            for residual in mutant.material_residuals
        ))

    def new_session(self, seed: int) -> CommanderSession:
        session = CommanderSession.create(
            self.db,
            {seat: deepcopy(self.deck) for seat in "AB"},
            first_player="A",
            seed=seed,
            config=GameConfig(seed=seed, auto_pass_empty_priority=False),
        )
        keep_all(session)
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine.state.priority_passes = []
        return session

    def add(self, session: CommanderSession, name: str, *, zone: str, ref: str) -> CardInstance:
        engine = session.engine
        record = self.records[name]
        register_generated_programs(
            self.db,
            engine.semantics,
            (record,),
            capability_registry=self.registry,
            capability_profile="commander_review",
            promote_exact_runtime_handlers=True,
            promote_exact_trigger_programs=True,
            promote_exact_effect_programs=True,
            promote_exact_capability_declarations=True,
        )
        if name == "Urza's Saga" and self.typed_saga_path:
            chapters = [
                (program.event, program.ability_id)
                for program in engine.semantics.programs_for_oracle(record.oracle_id)
                if program.event.startswith("saga.chapter.")
            ]
            self.assertEqual(
                [("saga.chapter.1", "trigger:front:n2"),
                 ("saga.chapter.2", "trigger:front:n3"),
                 ("saga.chapter.3", "trigger:front:n4")],
                sorted(chapters),
            )
        card = CardInstance(
            object_id=f"saga-witness:{ref}",
            ref=ref,
            oracle_id=record.oracle_id,
            printed_name=record.name,
            owner="A",
            controller="A",
            zone=zone,
            zone_timestamp=engine._next_zone_timestamp(),
            known_to=["A"] if zone in {"hand", "library"} else list(engine.seats),
            revealed_to=["A"] if zone in {"hand", "library"} else list(engine.seats),
        )
        engine.state.cards[card.object_id] = card
        engine.state.players["A"].zones[zone].append(card.object_id)
        return card

    def prepare_main(self, session: CommanderSession) -> None:
        engine = session.engine
        engine.state.started = True
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.phase_index = TURN_STEPS.index(("precombat_main", "main"))
        engine.state.turn_sequence = max(1, engine.state.turn_sequence)
        engine._grant_priority("A")
        engine.pump()

    def checkpoint(self, session: CommanderSession) -> None:
        session.initial_checkpoint = checkpoint_envelope(session.state)
        session.commands.clear()
        session.decisions.clear()

    def resolve_stack(self, session: CommanderSession):
        for _ in range(48):
            pending = session.state.pending_decision
            if pending is not None and pending.kind != "priority":
                return pending
            if not session.state.stack:
                return None
            principals = session.pending_principals()
            self.assertTrue(principals, "a stack item must have an active priority principal")
            result = session.act(principals[0], {"action_id": "pass"})
            self.assertTrue(result.ok, result.summary)
        self.fail("Saga chapter ability did not finish resolving")

    def play_saga_as_land(self, session: CommanderSession, saga: CardInstance) -> None:
        actions = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        action_id = f"play-land:{saga.ref}"
        self.assertIn(action_id, [row["id"] for row in actions])
        result = session.act("pilot:A", {"action_id": action_id})
        self.assertTrue(result.ok, result.summary)

    def pass_to_player(self, session: CommanderSession, seat: str) -> None:
        engine = session.engine
        for _ in range(8):
            engine.pump()
            if engine.state.priority_player == seat:
                return
            principals = session.pending_principals()
            self.assertTrue(principals, "priority must remain with an actionable player")
            result = session.act(principals[0], {"action_id": "pass"})
            self.assertTrue(result.ok, result.summary)
        self.fail(f"Seat {seat} did not receive priority")

    def refresh_priority(self, session: CommanderSession, seat: str = "A") -> None:
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine.state.priority_passes = []
        engine._grant_priority(seat)
        engine.pump()

    def ability_offer(self, session: CommanderSession, saga: CardInstance, *, construct: bool):
        engine = session.engine
        saga = engine.state.cards[saga.object_id]
        self.pass_to_player(session, "A")
        abilities = engine._activated_abilities(saga)
        if construct:
            ability = next(
                item for item in abilities
                if item.tap_source and not item.mana_ability
                and item.cost_text.replace(" ", "") == "{2},{T}"
                and "Construct" in item.effect_text
            )
        else:
            ability = next(item for item in abilities if item.mana_ability and item.tap_source)
        action_id = f"activate:{saga.ref}:{ability.ability_id}"
        actions = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        offer = next((row for row in actions if row["id"] == action_id), None)
        return ability, offer

    def advance_to_chapter_three_search(self, session: CommanderSession, saga: CardInstance):
        self.prepare_main(session)
        self.checkpoint(session)
        self.play_saga_as_land(session, saga)
        self.assertIsNone(self.resolve_stack(session))
        saga = session.state.cards[saga.object_id]
        self.assertEqual(1, saga.counters.get("lore"))
        for chapter in (2, 3):
            advance_active_player_sagas(session.engine, "A")
            session.engine._stabilize()
            pending = self.resolve_stack(session)
            saga = session.state.cards[saga.object_id]
            if chapter == 2:
                self.assertIsNone(pending)
                self.assertEqual(2, saga.counters.get("lore"))
        saga = session.state.cards[saga.object_id]
        self.assertEqual(3, saga.counters.get("lore"))
        self.assertEqual("semantic.search", session.state.pending_decision.kind)
        self.assertEqual("battlefield", saga.zone)
        return session.packet("pilot:A", full=True)["decision"]

    def assert_search_and_sacrifice_replay(
        self,
        session: CommanderSession,
        saga: CardInstance,
        target: CardInstance | None,
        *,
        expected_legal_refs: set[str],
    ) -> None:
        decision = session.packet("pilot:A", full=True)["decision"]
        schema = decision["legal_actions"][0]["choice_schema"]
        self.assertEqual(expected_legal_refs, set(schema["legal_refs"]))
        hidden = json.dumps(session.packet("pilot:B", full=True), sort_keys=True)
        for card in (self.records[name] for name in (
            "Mishra's Bauble", "Sol Ring", "Aerial Doombot",
            "Engineered Explosives", "Arcane Signet",
        )):
            self.assertNotIn(card.name, hidden)
        before = authoritative_state_hash(session.state)
        player = session.state.players["A"]
        shuffle_count = player.stats.get("shuffle_count", 0)
        event_sequence = session.state.event_sequence
        command = {"action_id": "choose", "search_cards": [] if target is None else [target.ref]}
        if target is not None:
            rejected = session.act(
                "pilot:A", {"action_id": "choose", "search_cards": ["aerial-doombot"]}
            )
            self.assertFalse(rejected.ok)
            self.assertEqual(before, authoritative_state_hash(session.state))
        self.checkpoint(session)
        result = session.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        player_after = session.state.players["A"]
        saga_after = session.state.cards[saga.object_id]
        self.assertEqual(shuffle_count + 1, player_after.stats.get("shuffle_count", 0))
        self.assertEqual("graveyard", saga_after.zone)
        if target is not None:
            self.assertEqual("battlefield", session.state.cards[target.object_id].zone)
        else:
            self.assertTrue(all(
                session.state.cards[object_id].zone == "library"
                for object_id in player_after.zones["library"]
            ))
        codes = [
            event.code for event in session.state.events
            if event.event_id > event_sequence
            and event.code in {"library.search", "library.shuffle", "stack.resolve"}
        ]
        self.assertEqual(["library.search", "library.shuffle", "stack.resolve"], codes)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "urza-saga-search"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(authoritative_state_hash(session.state), replay["final_state_hash"])

    def test_chapter_i_and_ii_grants_support_repeatable_paid_construct_creation(self):
        session = self.new_session(71430101)
        engine = session.engine
        saga = self.add(session, "Urza's Saga", zone="hand", ref="native-saga")
        self.prepare_main(session)
        self.checkpoint(session)
        self.play_saga_as_land(session, saga)
        self.assertIsNone(self.resolve_stack(session))
        saga = session.state.cards[saga.object_id]
        self.assertEqual(1, saga.counters.get("lore"))
        mana_ability, mana_offer = self.ability_offer(session, saga, construct=False)
        expected_mana_key = "ability:granted:front:n2" if self.typed_saga_path else "saga_mana"
        self.assertEqual(expected_mana_key, mana_ability.ability_id)
        self.assertIsNotNone(mana_offer, "Chapter I must grant its tap-for-colorless ability")

        advance_active_player_sagas(engine, "A")
        engine._stabilize()
        self.assertIsNone(self.resolve_stack(session))
        saga = session.state.cards[saga.object_id]
        self.assertEqual(2, saga.counters.get("lore"))
        construct_ability, _ = self.ability_offer(session, saga, construct=True)
        expected_construct_key = "ability:granted:front:n3" if self.typed_saga_path else "saga_construct"
        self.assertEqual(expected_construct_key, construct_ability.ability_id)
        self.assertEqual("{2}, {T}", construct_ability.cost_text)
        engine.state.players["A"].mana_pool["C"] = 2
        self.refresh_priority(session)
        _, construct_offer = self.ability_offer(session, saga, construct=True)
        self.assertIsNotNone(
            construct_offer,
            "Chapter II must grant the paid Construct ability; "
            f"ability={construct_ability.ability_id}, priority={engine.state.priority_player}, "
            f"mana={engine.state.players['A'].mana_pool}, "
            f"offers={[row['id'] for row in session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']]}",
        )

        tokens = []
        for index in range(2):
            saga = engine.state.cards[saga.object_id]
            saga.tapped = False
            engine.state.players["A"].mana_pool["C"] = 2
            self.refresh_priority(session)
            ability, offer = self.ability_offer(session, saga, construct=True)
            self.assertIsNotNone(offer)
            if index == 0:
                engine.state.players["A"].mana_pool["C"] = 1
                self.refresh_priority(session)
                _, unaffordable = self.ability_offer(session, saga, construct=True)
                self.assertIsNone(unaffordable, "{2}, {T} must not be offered with only one mana")
                engine.state.players["A"].mana_pool["C"] = 2
                self.refresh_priority(session)
                _, offer = self.ability_offer(session, saga, construct=True)
                before = authoritative_state_hash(session.state)
                denied = session.act(
                    "pilot:A", {"action_id": offer["id"], "pay": "manual", "payment": {"C": 1}}
                )
                self.assertFalse(denied.ok)
                self.assertEqual(before, authoritative_state_hash(session.state))
                _, offer = self.ability_offer(session, saga, construct=True)
            result = session.act(
                "pilot:A", {"action_id": offer["id"], "pay": "manual", "payment": {"C": 2}}
            )
            self.assertTrue(result.ok, result.summary)
            self.assertTrue(session.state.cards[saga.object_id].tapped)
            self.assertEqual(0, engine.state.players["A"].mana_pool.get("C", 0))
            self.assertIsNone(self.resolve_stack(session))
            tokens = [
                card for card in engine.state.cards.values()
                if card.is_token
                and "construct" in engine._effective_card_data(card)["type_line"].casefold()
                and card.controller == "A"
            ]
            self.assertEqual(index + 1, len(tokens))

    def test_chapter_iii_finds_only_exact_zero_or_one_and_replays_resolution(self):
        for seed, target_name in ((71430102, "Mishra's Bauble"), (71430103, "Sol Ring")):
            with self.subTest(target=target_name):
                session = self.new_session(seed)
                saga = self.add(session, "Urza's Saga", zone="hand", ref=f"saga-{seed}")
                targets = {
                    name: self.add(session, name, zone="library", ref=f"{seed}-{name.lower().replace(' ', '-')}")
                    for name in (
                        "Mishra's Bauble", "Sol Ring", "Aerial Doombot",
                        "Engineered Explosives", "Arcane Signet",
                    )
                }
                self.advance_to_chapter_three_search(session, saga)
                self.assert_search_and_sacrifice_replay(
                    session,
                    saga,
                    targets[target_name],
                    expected_legal_refs={targets["Mishra's Bauble"].ref, targets["Sol Ring"].ref},
                )

    def test_chapter_iii_can_fail_to_find_and_still_shuffles_before_sacrifice(self):
        session = self.new_session(71430104)
        saga = self.add(session, "Urza's Saga", zone="hand", ref="saga-fail-find")
        targets = {
            name: self.add(session, name, zone="library", ref="fail-find-" + name.lower().replace(" ", "-"))
            for name in ("Mishra's Bauble", "Aerial Doombot", "Engineered Explosives", "Arcane Signet")
        }
        self.advance_to_chapter_three_search(session, saga)
        self.assert_search_and_sacrifice_replay(
            session,
            saga,
            None,
            expected_legal_refs={targets["Mishra's Bauble"].ref},
        )


if __name__ == "__main__":
    unittest.main()
