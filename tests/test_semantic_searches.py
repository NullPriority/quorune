from __future__ import annotations

import copy
import json
import tempfile
import unittest
import uuid
from pathlib import Path

from common import keep_all, load_assets, make_session
from quorune import CommanderSession, GameConfig, PilotResponse
from quorune.errors import GameRuleError
from quorune.model import StackItem
from quorune.record import (
    authoritative_state_hash,
    checkpoint_envelope,
    replay_record,
)
from quorune.semantics import SemanticProgram


class SemanticPrivateSearchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db, cls.mishra, cls.zimone = load_assets()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    @staticmethod
    def _card(engine, seat: str, name: str):
        return next(
            card
            for card in engine.state.cards.values()
            if card.owner == seat and card.printed_name == name
        )

    def _session(self):
        session = make_session(
            self.db,
            self.mishra,
            self.zimone,
            players=2,
            seed=606,
            auto_pass_empty=True,
        )
        keep_all(session)
        return session

    def _begin_spell(
        self,
        session,
        *,
        seat: str,
        name: str,
        program: SemanticProgram | None = None,
        x_value: int | None = None,
    ):
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.priority_player = None
        card = self._card(engine, seat, name)
        if card.zone != "hand":
            engine.move_card(card.object_id, "hand", log=False)
        engine._remove_from_zone(card)
        card.zone = "stack"
        card.known_to = list(engine.seats)
        card.revealed_to = list(engine.seats)
        if program is not None:
            engine.semantics.put(program)
            semantic_key = program.key
        else:
            semantic_key = f"{card.oracle_id}:spell:front"
        item = StackItem(
            stack_id=uuid.uuid4().hex,
            ref="S-search-test",
            kind="spell",
            controller=seat,
            label=name,
            card_object_id=card.object_id,
            semantic_key=semantic_key,
            x_value=x_value,
            default_destination="graveyard",
            visibility=list(engine.seats),
        )
        engine.state.stack.append(item)
        engine._prepare_stack_resolution()
        self.assertEqual(
            "semantic.search", engine.state.pending_decision.kind
        )
        session.commands.clear()
        session.decisions.clear()
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        return card, item

    def test_entomb_private_search_continuation_and_exact_replay(self):
        session = self._session()
        entomb, item = self._begin_spell(
            session, seat="B", name="Entomb"
        )
        bloodghast = self._card(
            session.engine, "B", "Bloodghast"
        )
        task = session.packet("pilot:B", full=True)
        candidates = task["decision"]["ctx"]["search_cards"]
        self.assertIn(
            bloodghast.ref, {item["id"] for item in candidates}
        )
        search_schema = task["decision"]["legal_actions"][0][
            "choice_schema"
        ]
        self.assertEqual("ref_array", search_schema["shape"])
        self.assertEqual("string", search_schema["element_type"])
        self.assertTrue(
            set(search_schema["example"]["search_cards"]).issubset(
                set(search_schema["legal_refs"])
            )
        )
        serialized_other = json.dumps(
            session.packet("pilot:A", full=True)
        )
        self.assertNotIn(bloodghast.ref, serialized_other)
        self.assertNotIn("search_cards", serialized_other)
        arbiter = json.dumps(session.packet("arbiter", full=True))
        self.assertNotIn(bloodghast.ref, arbiter)

        frame = session.state.pending_decision.continuation[
            "selection"
        ]["payload"]["semantic_frame"]
        self.assertEqual(item.ref, frame["stack_object"])
        self.assertEqual(
            item.semantic_key, frame["semantic_program_id"]
        )
        self.assertEqual(
            session.state.pending_decision.decision_id,
            frame["pending_choice_id"],
        )
        before_shuffle = session.state.players["B"].stats.get(
            "shuffle_count", 0
        )
        malformed = session.act(
            "pilot:B",
            {
                "action_id": "choose",
                "search_cards": [{"id": bloodghast.ref}],
                "plan": "DEVELOP_ENGINE",
                "reason": "Exercise strict private-search choice typing.",
            },
        )
        self.assertFalse(malformed.ok)
        self.assertIn(
            "array of card-ref strings",
            malformed.summary,
        )
        # A rejected transactional command restores fresh state objects.
        entomb = self._card(session.engine, "B", "Entomb")
        bloodghast = self._card(
            session.engine,
            "B",
            "Bloodghast",
        )
        result = session.act(
            "pilot:B",
            {
                "action_id": "choose",
                "search_card": bloodghast.ref,
                "plan": "DEVELOP_ENGINE",
                "reason": "Put Bloodghast into the graveyard for landfall recursion.",
            },
        )
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("graveyard", bloodghast.zone)
        self.assertEqual("graveyard", entomb.zone)
        self.assertFalse(session.state.stack)
        self.assertEqual(
            before_shuffle + 1,
            session.state.players["B"].stats["shuffle_count"],
        )
        public_search = next(
            event
            for event in session.state.events
            if event.code == "library.search"
        )
        self.assertEqual(bloodghast.ref, public_search.details["object"])
        with tempfile.TemporaryDirectory() as temporary:
            record = Path(temporary) / "entomb"
            session.save(record)
            command_path = record / "commands.jsonl"
            command = json.loads(command_path.read_text(encoding="utf-8"))
            used = command["semantics"]["card_programs_used"]
            self.assertTrue(used)
            self.assertEqual(
                next(iter(used.values())),
                command["semantics"]["programs_used"][0][
                    "card_program_fingerprint"
                ],
            )
            runtime_row = command["semantics"]["programs_used"][0]
            self.assertEqual(64, len(runtime_row["runtime_binding_fingerprint"]))
            self.assertEqual(
                "capability_closed", runtime_row["card_program_trust_basis"],
            )
            self.assertFalse(runtime_row["legacy_compatibility"])
            self.assertEqual([], runtime_row["runtime_component_ids"])
            replay = replay_record(record, self.db, verify=True)
            self.assertTrue(replay["ok"])
            original_binding = runtime_row["runtime_binding_fingerprint"]
            runtime_row["runtime_binding_fingerprint"] = "0" * 64
            command_path.write_text(json.dumps(command), encoding="utf-8")
            with self.assertRaisesRegex(
                ValueError, "Runtime binding provenance mismatch"
            ):
                replay_record(record, self.db, verify=True)
            runtime_row["runtime_binding_fingerprint"] = original_binding
            oracle_id = next(iter(used))
            command["semantics"]["card_programs_used"][oracle_id] = (
                "0" * 64
            )
            command_path.write_text(json.dumps(command), encoding="utf-8")
            with self.assertRaisesRegex(
                ValueError, "CardProgram fingerprint mismatch at command"
            ):
                replay_record(record, self.db, verify=True)

    def test_search_envelope_tampering_fails_before_mutation(self):
        session = self._session()
        self._begin_spell(session, seat="B", name="Entomb")
        bloodghast = self._card(session.engine, "B", "Bloodghast")
        original = session.state.pending_decision
        self.assertIsNotNone(original)
        assert original is not None
        before = authoritative_state_hash(session.state)

        mutations = {
            "actor": lambda value: value.__setitem__("actor", "A"),
            "revision": lambda value: value.__setitem__(
                "state_revision", value["state_revision"] + 1
            ),
            "source": lambda value: value.__setitem__("source_ref", "B999"),
            "visibility": lambda value: value.__setitem__(
                "visibility", "public"
            ),
            "legal refs": lambda value: value["payload"].__setitem__(
                "legal_refs", []
            ),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label):
                decision = copy.deepcopy(original)
                decision.responses["B"] = {
                    "search_card": bloodghast.ref,
                }
                mutate(decision.continuation["selection"])
                with self.assertRaises(GameRuleError):
                    with session.engine.transaction():
                        session.engine._complete_semantic_search(decision)
                self.assertEqual(before, authoritative_state_hash(session.state))

    def _three_visits(
        self,
        land_name: str,
        *,
        pay_life: bool = False,
    ):
        session = self._session()
        spell, _ = self._begin_spell(
            session, seat="B", name="Three Visits"
        )
        land = self._card(session.engine, "B", land_name)
        if land.zone != "library":
            session.engine.move_card(
                land.object_id, "library", log=False
            )
        # Reissue after making the deterministic candidate fixture.
        session.engine.permissions.invalidate_current()
        session.engine.state.pending_decision = None
        session.engine._prepare_stack_resolution()
        result = session.act(
            "pilot:B",
            {
                "action_id": "choose",
                "search_card": land.ref,
                "entry_pay_life": pay_life,
                "plan": "FIX_COLORS",
                "reason": f"Find {land_name} with the Forest search.",
            },
        )
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("graveyard", spell.zone)
        return session, land

    def test_three_visits_to_bayou_and_natures_lore_template(self):
        session, bayou = self._three_visits("Bayou")
        self.assertEqual("battlefield", bayou.zone)
        self.assertFalse(bayou.tapped)
        three = session.engine.semantics.get(
            "1b882a0e-0ede-4d1a-bd1a-9b7cffbcde8e:spell:front"
        )
        lore = session.engine.semantics.get(
            "78826359-fe63-44ad-adc4-a17ffcd710e4:spell:front"
        )
        self.assertEqual(three.effects, lore.effects)

    def test_three_visits_shockland_tapped_and_untapped(self):
        declined, tapped_pool = self._three_visits(
            "Breeding Pool", pay_life=False
        )
        self.assertTrue(tapped_pool.tapped)
        self.assertEqual(40, declined.state.players["B"].life)

        paid, untapped_pool = self._three_visits(
            "Breeding Pool", pay_life=True
        )
        self.assertFalse(untapped_pool.tapped)
        self.assertEqual(38, paid.state.players["B"].life)

    def test_restrictive_hidden_search_may_fail_to_find(self):
        session = self._session()
        spell, _ = self._begin_spell(
            session, seat="B", name="Three Visits"
        )
        result = session.act(
            "pilot:B",
            {
                "action_id": "choose",
                "search_cards": [],
                "plan": "FIX_COLORS",
                "reason": "Exercise the rules-permitted hidden-zone failure to find.",
            },
        )
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("graveyard", spell.zone)

    def test_green_sun_x_search_and_self_shuffle(self):
        session = self._session()
        shaman = self._card(
            session.engine, "B", "Deathrite Shaman"
        )
        if shaman.zone != "library":
            session.engine.move_card(
                shaman.object_id, "library", log=False
            )
        spell, _ = self._begin_spell(
            session,
            seat="B",
            name="Green Sun's Zenith",
            x_value=1,
        )
        result = session.act(
            "pilot:B",
            {
                "action_id": "choose",
                "search_card": shaman.ref,
                "plan": "DEVELOP_ENGINE",
                "reason": "Find the one-mana green creature.",
            },
        )
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("battlefield", shaman.zone)
        self.assertEqual("library", spell.zone)
        self.assertIn(
            spell.object_id,
            session.state.players["B"].zones["library"],
        )

    def test_finale_multi_zone_search_and_x10_pump(self):
        session = self._session()
        engine = session.engine
        bloodghast = self._card(engine, "B", "Bloodghast")
        fodder = self._card(engine, "B", "Elves of Deep Shadow")
        engine.move_card(bloodghast.object_id, "graveyard")
        engine.move_card(
            fodder.object_id,
            "battlefield",
            controller="B",
        )
        base_stats = {
            creature.ref: (
                engine._numeric_stat(creature.object_id, "power"),
                engine._numeric_stat(creature.object_id, "toughness"),
            )
            for creature in (bloodghast, fodder)
        }
        spell, _ = self._begin_spell(
            session,
            seat="B",
            name="Finale of Devastation",
            x_value=10,
        )
        result = session.act(
            "pilot:B",
            {
                "action_id": "choose",
                "search_card": bloodghast.ref,
                "plan": "WIN_ATTEMPT",
                "reason": "Return Bloodghast and apply Finale's X-ten bonus.",
            },
        )
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("battlefield", bloodghast.zone)
        self.assertEqual("graveyard", spell.zone)
        for creature in (bloodghast, fodder):
            base_power, base_toughness = base_stats[creature.ref]
            self.assertEqual(
                base_power + 10,
                engine._numeric_stat(creature.object_id, "power"),
            )
            self.assertEqual(
                base_toughness + 10,
                engine._numeric_stat(creature.object_id, "toughness"),
            )
            self.assertIn(
                "Haste",
                engine._effective_card_data(creature)["keywords"],
            )

    def test_chord_reshape_and_whir_x_search_filters(self):
        scenarios = [
            ("B", "Chord of Calling", "Deathrite Shaman", 1),
            ("A", "Reshape", "Sol Ring", 1),
            ("A", "Whir of Invention", "Sol Ring", 1),
        ]
        for seat, spell_name, target_name, x_value in scenarios:
            with self.subTest(spell=spell_name):
                session = self._session()
                target = self._card(
                    session.engine, seat, target_name
                )
                if target.zone != "library":
                    session.engine.move_card(
                        target.object_id, "library", log=False
                    )
                spell, _ = self._begin_spell(
                    session,
                    seat=seat,
                    name=spell_name,
                    x_value=x_value,
                )
                packet = session.packet(
                    f"pilot:{seat}", full=True
                )
                legal = {
                    option["id"]
                    for option in packet["decision"]["ctx"][
                        "search_cards"
                    ]
                }
                self.assertIn(target.ref, legal)
                result = session.act(
                    f"pilot:{seat}",
                    {
                        "action_id": "choose",
                        "search_card": target.ref,
                        "plan": "DEVELOP_ENGINE",
                        "reason": f"Resolve {spell_name}'s X-limited search.",
                    },
                )
                self.assertTrue(result.ok, result.summary)
                self.assertEqual("battlefield", target.zone)
                self.assertEqual("graveyard", spell.zone)

    def test_protean_hulk_aggregate_search_constraint(self):
        session = self._session()
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        hulk = self._card(engine, "B", "Protean Hulk")
        engine.move_card(
            hulk.object_id,
            "battlefield",
            controller="B",
        )
        engine.apply_effect(
            {"op": "sacrifice", "card": hulk.ref},
            actor="B",
        )
        self.assertFalse(engine._stabilize())
        trigger = next(
            item
            for item in engine.state.stack
            if item.label == "Protean Hulk dies"
        )
        self.assertEqual(hulk.object_id, trigger.source_object_id)
        expensive = self._card(engine, "B", "Seedborn Muse")
        endurance = self._card(engine, "B", "Endurance")
        shaman = self._card(engine, "B", "Deathrite Shaman")
        bloodghast = self._card(engine, "B", "Bloodghast")
        for candidate in (
            expensive,
            endurance,
            shaman,
            bloodghast,
        ):
            if candidate.zone != "library":
                engine.move_card(
                    candidate.object_id, "library", log=False
                )
        engine.state.priority_player = None
        engine._prepare_stack_resolution()
        self.assertEqual(
            "semantic.search", engine.state.pending_decision.kind
        )
        rejected = session.act(
            "pilot:B",
            {
                "action_id": "choose",
                "search_cards": [expensive.ref, endurance.ref],
                "plan": "DEVELOP_ENGINE",
                "reason": "Exercise the aggregate mana-value rejection.",
            },
        )
        self.assertFalse(rejected.ok)
        accepted = session.act(
            "pilot:B",
            {
                "action_id": "choose",
                "search_cards": [shaman.ref, bloodghast.ref],
                "plan": "DEVELOP_ENGINE",
                "reason": "Select creatures totaling no more than six mana value.",
            },
        )
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual(
            "battlefield",
            self._card(engine, "B", "Deathrite Shaman").zone,
        )
        self.assertEqual(
            "battlefield",
            self._card(engine, "B", "Bloodghast").zone,
        )

    def test_private_and_revealed_hand_search_visibility(self):
        for reveal in (False, True):
            with self.subTest(reveal=reveal):
                session = self._session()
                key = f"test:hand-search:{reveal}"
                program = SemanticProgram(
                    key=key,
                    label="Hand search",
                    effects=[
                        {
                            "op": "search",
                            "searching_player": "$controller",
                            "zone": "library",
                            "selector": {"types": ["Artifact"]},
                            "count": {"minimum": 1, "maximum": 1},
                            "destination": "hand",
                            "reveal": reveal,
                            "shuffle_after": True,
                        }
                    ],
                    destination="graveyard",
                )
                self._begin_spell(
                    session,
                    seat="B",
                    name="Entomb",
                    program=program,
                )
                option = session.packet("pilot:B", full=True)[
                    "decision"
                ]["ctx"]["search_cards"][0]
                result = session.act(
                    "pilot:B",
                    {
                        "action_id": "choose",
                        "search_card": option["id"],
                        "plan": "DEVELOP_ENGINE",
                        "reason": "Choose the artifact for the visibility test.",
                    },
                )
                self.assertTrue(result.ok, result.summary)
                opposing = json.dumps(
                    session.packet("pilot:A", full=True)
                )
                if reveal:
                    self.assertIn(option["id"], opposing)
                    self.assertIn(option["name"], opposing)
                else:
                    self.assertNotIn(option["id"], opposing)
                    self.assertNotIn(option["name"], opposing)
                public_event = next(
                    event
                    for event in session.state.events
                    if event.code == "library.search"
                )
                self.assertEqual(
                    reveal, "object" in public_event.details
                )

    def test_fabricate_reveals_artifact_and_moves_it_to_hand(self):
        session = self._session()
        engine = session.engine
        target = self._card(engine, "A", "Ichor Wellspring")
        if target.zone != "library":
            engine.move_card(target.object_id, "library", log=False)
        spell, _ = self._begin_spell(
            session,
            seat="A",
            name="Fabricate",
        )
        packet = session.packet("pilot:A", full=True)
        candidates = {
            item["id"]
            for item in packet["decision"]["ctx"]["search_cards"]
        }
        self.assertIn(target.ref, candidates)
        result = session.act(
            "pilot:A",
            {
                "action_id": "choose",
                "search_card": target.ref,
                "plan": "DEVELOP_ENGINE",
                "reason": "Find and reveal Ichor Wellspring with Fabricate.",
            },
        )
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("hand", target.zone)
        self.assertEqual("graveyard", spell.zone)
        opposing = json.dumps(session.packet("pilot:B", full=True))
        self.assertIn(target.ref, opposing)
        self.assertIn("Ichor Wellspring", opposing)

    def test_spellseeker_search_trigger(self):
        session = self._session()
        engine = session.engine
        seeker = self._card(engine, "B", "Spellseeker")
        target = self._card(engine, "B", "Entomb")
        engine.move_card(target.object_id, "library", log=False)
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None

        engine.move_card(
            seeker.object_id,
            "battlefield",
            controller="B",
            semantic_events=True,
            reason="Spellseeker scenario",
        )
        self.assertFalse(engine._stabilize())
        engine._prepare_stack_resolution()
        packet = session.packet("pilot:B", full=True)
        candidates = {
            row["id"]
            for row in packet["decision"]["ctx"]["search_cards"]
        }
        self.assertIn(target.ref, candidates)
        result = session.act(
            "pilot:B",
            {
                "action_id": "choose",
                "search_card": target.ref,
                "plan": "ASSEMBLE_ENGINE",
                "reason": "Find the one-mana instant.",
            },
        )

        self.assertTrue(result.ok, result.summary)
        self.assertEqual("hand", target.zone)
        self.assertEqual(set(engine.seats), set(target.known_to))

    def test_goblin_engineer_search_trigger(self):
        session = self._session()
        engine = session.engine
        engineer = self._card(engine, "A", "Goblin Engineer")
        target = self._card(engine, "A", "Sol Ring")
        engine.move_card(target.object_id, "library", log=False)
        target.known_to = ["A"]
        target.revealed_to = []
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None

        engine.move_card(
            engineer.object_id,
            "battlefield",
            controller="A",
            semantic_events=True,
            reason="Goblin Engineer scenario",
        )
        self.assertFalse(engine._stabilize())
        engine._prepare_stack_resolution()
        packet = session.packet("pilot:A", full=True)
        self.assertIn(
            target.ref,
            {
                row["id"]
                for row in packet["decision"]["ctx"]["search_cards"]
            },
        )
        opposing_before = json.dumps(
            session.packet("pilot:B", full=True)
        )
        self.assertNotIn(target.ref, opposing_before)
        result = session.act(
            "pilot:A",
            {
                "action_id": "choose",
                "search_card": target.ref,
                "plan": "SETUP_GRAVEYARD",
                "reason": "Put the mana artifact into the graveyard.",
            },
        )

        self.assertTrue(result.ok, result.summary)
        self.assertEqual("graveyard", target.zone)
        self.assertEqual(set(engine.seats), set(target.known_to))

    def test_survival_search_after_authoritative_cost(self):
        session = self._session()
        engine = session.engine
        survival = self._card(engine, "B", "Survival of the Fittest")
        discarded = self._card(engine, "B", "Faerie Mastermind")
        target = self._card(engine, "B", "Bloodghast")
        engine.move_card(
            survival.object_id,
            "battlefield",
            controller="B",
            log=False,
        )
        engine.move_card(discarded.object_id, "hand", log=False)
        engine.move_card(target.object_id, "library", log=False)
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.players["B"].mana_pool["G"] = 1
        engine.state.priority_player = "B"
        engine.state.priority_passes = []

        engine._activate(
            "B",
            {
                "source": survival.ref,
                "ability": "ab1",
                "cost_cards": [discarded.ref],
                "pay": "manual",
                "payment": {"G": 1},
            },
        )
        engine.state.priority_player = None
        engine._prepare_stack_resolution()
        result = session.act(
            "pilot:B",
            {
                "action_id": "choose",
                "search_card": target.ref,
                "plan": "ASSEMBLE_ENGINE",
                "reason": "Exchange the creature in hand for Bloodghast.",
            },
        )

        self.assertTrue(result.ok, result.summary)
        self.assertEqual("graveyard", discarded.zone)
        self.assertEqual("hand", target.zone)
        self.assertEqual(0, engine.state.players["B"].mana_pool["G"])

    def test_goblin_engineer_activation_pays_cost_and_returns_small_artifact(
        self,
    ):
        session = self._session()
        engine = session.engine
        engineer = self._card(engine, "A", "Goblin Engineer")
        sacrifice = self._card(engine, "A", "Sol Ring")
        target = self._card(engine, "A", "Ichor Wellspring")
        for card in (engineer, sacrifice):
            engine.move_card(
                card.object_id,
                "battlefield",
                controller="A",
                tapped=False,
                log=False,
            )
        engineer.acquired_control_turn_count = (
            engine.state.players["A"].turns_begun - 1
        )
        engine.move_card(target.object_id, "graveyard", log=False)
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.players["A"].mana_pool["R"] = 1
        engine.state.priority_player = "A"
        engine.state.priority_passes = []

        engine._activate(
            "A",
            {
                "source": engineer.ref,
                "ability": "ab2",
                "targets": [target.ref],
                "cost_cards": [sacrifice.ref],
                "pay": "manual",
                "payment": {"R": 1},
            },
        )
        engine.state.priority_player = None
        engine._prepare_stack_resolution()

        self.assertEqual("graveyard", sacrifice.zone)
        self.assertEqual("battlefield", target.zone)
        self.assertTrue(engineer.tapped)

    def test_ordered_plan_spans_fetch_entomb_and_private_search(self):
        session = CommanderSession.create(
            self.db,
            {"A": self.zimone, "B": self.mishra},
            first_player="A",
            seed=20260736,
            config=GameConfig(
                seed=20260736,
                profile="commander_duel",
                auto_pass_empty_priority=True,
            ),
        )
        keep_all(session)
        engine = session.engine
        foothills = self._card(engine, "A", "Wooded Foothills")
        bayou = self._card(engine, "A", "Bayou")
        entomb = self._card(engine, "A", "Entomb")
        bloodghast = self._card(engine, "A", "Bloodghast")
        for object_id in list(engine.state.players["A"].zones["hand"]):
            engine.move_card(object_id, "library", log=False)
        for card in (foothills, entomb):
            engine.move_card(card.object_id, "hand", log=False)
        for card in (bayou, bloodghast):
            engine.move_card(card.object_id, "library", log=False)
        engine.permissions.invalidate_current()
        engine.state.started = True
        engine.state.turn_sequence = 1
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.phase_index = 3
        engine.state.players["A"].land_plays_remaining = 1
        engine.state.stack = []
        engine._grant_priority("A")
        engine.pump()
        fetch = next(
            ability
            for ability in engine._activated_abilities(foothills)
            if ability.library_search_types
        )
        session.commands.clear()
        session.decisions.clear()
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        result = session.act(
            "pilot:A",
            PilotResponse.from_mapping({
                "actions": [
                    {"action_id": f"play-land:{foothills.ref}"},
                    {
                        "action_id": (
                            f"activate:{foothills.ref}:{fetch.ability_id}"
                        ),
                        "future_choices": {
                            "search_card_name": "Bayou",
                            "entry_pay_life": False,
                        },
                    },
                    {
                        "action_id": f"cast:{entomb.ref}",
                        "future_choices": {
                            "search_card_name": "Bloodghast"
                        },
                    },
                ],
                "plan": "DEVELOP_ENGINE",
                "reason": "Fetch Bayou, Entomb Bloodghast, and preserve blue access.",
                "confidence": 0.95,
            }).engine_response(),
        )
        self.assertTrue(result.ok, result.summary)
        session.next_task()
        self.assertNotIn("pilot:A", session.plans)
        self.assertEqual("battlefield", bayou.zone)
        self.assertEqual("graveyard", bloodghast.zone)
        self.assertEqual("graveyard", entomb.zone)
        self.assertGreaterEqual(
            sum(
                row["execution"] == "planned_automatic"
                for row in session.commands
            ),
            4,
        )
        with tempfile.TemporaryDirectory() as temporary:
            record = Path(temporary) / "ordered-search"
            session.save(record)
            self.assertTrue(
                replay_record(record, self.db, verify=True)["ok"]
            )


if __name__ == "__main__":
    unittest.main()
