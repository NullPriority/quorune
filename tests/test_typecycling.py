from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from common import keep_all, load_assets, make_session
from quorune.carddb import CardRecord
from quorune.compiler import cycling_nodes as cycling_nodes_module
from quorune.cycling_abilities import (
    CyclingAbilityError,
    TypecyclingAbilitySpec,
    compile_typecycling_ability,
    typecycling_handler_descriptor,
)
from quorune.model import CardInstance
from quorune.oracle_ir import compile_oracle_card, register_generated_programs
from quorune.projection import StateProjector
from quorune.record import (
    authoritative_state_hash,
    checkpoint_envelope,
    replay_record,
)
from quorune.rules.capabilities import (
    CapabilityRegistry,
    DEFAULT_CAPABILITY_REGISTRY,
    load_default_capability_registry,
)
from quorune.rules.library_search_capability_shapes import (
    FIXED_TYPE_TO_HAND_SEARCH_CAPABILITY_ID,
    fixed_type_to_hand_search_node_capabilities,
)
from quorune.semantic_runtime.cycling_abilities import (
    cycling_specs_from_descriptors,
)
from quorune.semantics import SemanticRegistry
from quorune.session import CommanderSession


def typecycling_record(text: str) -> CardRecord:
    return CardRecord(
        oracle_id="00000000-0000-4000-8000-000000000229",
        name="Fixed Typecycling Fixture",
        mana_cost="{3}",
        mana_value=3.0,
        type_line="Creature — Wizard",
        oracle_text=text,
        power="2",
        toughness="2",
        loyalty=None,
        defense=None,
        colors=(),
        color_identity=(),
        keywords=("Typecycling", "Cycling"),
        produced_mana=(),
        layout="normal",
        released_at="2026-08-24",
        legalities={"commander": "legal"},
        faces=(),
        raw={},
    )


class TypecyclingModelTests(unittest.TestCase):
    def test_typecycling_descriptor_is_strict_immutable_and_round_trips(self):
        spec = compile_typecycling_ability(
            material_line="Basic landcycling {1}",
            oracle_line="Basic landcycling {1}",
            line_index=0,
        )
        self.assertIsNotNone(spec)
        assert spec is not None
        payload = spec.to_dict()
        payload["search_selector"]["types"].append("creature")
        self.assertEqual(("land",), spec.search_selector["types"])
        self.assertEqual(spec, TypecyclingAbilitySpec.from_dict(spec.to_dict()))
        ability = spec.to_activated_ability()
        self.assertEqual(("hand",), ability.zones)
        self.assertTrue(ability.discard_source)
        descriptor = typecycling_handler_descriptor(spec)
        self.assertEqual((spec,), cycling_specs_from_descriptors([descriptor]))
        malformed = spec.to_dict()
        malformed["search_selector"] = {"types": ["creature"]}
        with self.assertRaises(CyclingAbilityError):
            TypecyclingAbilitySpec.from_dict(malformed)


class TypecyclingCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.capabilities = load_default_capability_registry()
        cls.registry_value = json.loads(
            DEFAULT_CAPABILITY_REGISTRY.read_text(encoding="utf-8")
        )

    def compile(
        self,
        text: str,
        *,
        capabilities: CapabilityRegistry | None = None,
    ):
        return compile_oracle_card(
            typecycling_record(text),
            capability_registry=capabilities or self.capabilities,
            capability_profile="commander_review",
        )

    def test_fixed_typecycling_categories_compile_source_spanned_searches(self):
        categories = {
            "Basic land": {"types": ["land"], "supertypes": ["basic"]},
            "Plains": {"types": ["land"], "subtypes_any": ["plains"]},
            "Island": {"types": ["land"], "subtypes_any": ["island"]},
            "Swamp": {"types": ["land"], "subtypes_any": ["swamp"]},
            "Mountain": {"types": ["land"], "subtypes_any": ["mountain"]},
            "Forest": {"types": ["land"], "subtypes_any": ["forest"]},
            "Artifact land": {"types": ["artifact", "land"]},
            "Wizard": {"subtypes_any": ["wizard"]},
            "Sliver": {"subtypes_any": ["sliver"]},
        }
        for label, selector in categories.items():
            with self.subTest(label=label):
                text = f"{label}cycling {{2}}{{U}}"
                ir = self.compile(text)
                self.assertEqual("exact", ir.status)
                self.assertEqual(1, len(ir.faces[0].nodes))
                node = ir.faces[0].nodes[0]
                self.assertEqual(
                    "fixed-typecycling-activation-v1", node.template_id
                )
                self.assertEqual("hand", node.active_zone)
                self.assertEqual("activate", node.event)
                self.assertEqual(
                    ("activation.typecycling.hand",),
                    node.capability_dependencies,
                )
                self.assertTrue(node.cost["discard_source"])
                self.assertEqual(2, node.cost["mana"]["GENERIC"])
                self.assertEqual(1, node.cost["mana"]["U"])
                self.assertEqual(selector, node.effects[0]["selector"])
                self.assertEqual(text, text[node.span.start : node.span.end])
                self.assertEqual(
                    "ability.activated.typecycling.v1",
                    node.handlers[0]["handler_id"],
                )

    def test_typecycling_variants_and_dependencies_fail_closed(self):
        unsupported = (
            "Plains Islandcycling {2}",
            "Affinitycycling {2}",
            "Goblincycling {2}",
            "Forestcycling {X}",
            "Islandcycling {W/U}",
            "Basic landcycling—Pay 2 life.",
            "Cards in your hand have basic landcycling {1}.",
        )
        for text in unsupported:
            with self.subTest(text=text):
                ir = self.compile(text)
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)

        effect = self.compile("Basic landcycling {1}").faces[0].nodes[0].effects[
            0
        ]
        self.assertEqual(
            (FIXED_TYPE_TO_HAND_SEARCH_CAPABILITY_ID,),
            fixed_type_to_hand_search_node_capabilities(
                effects=(effect,),
                target_schema=None,
                mechanic_ids=("cycling",),
            ),
        )
        for malformed in (
            {**effect, "extra": True},
            {**effect, "destination": "battlefield"},
            {**effect, "reveal": False},
            {**effect, "shuffle_after": False},
            {**effect, "count": {"minimum": 0, "maximum": 1}},
            {**effect, "selector": {"subtypes_any": ["goblin"]}},
            {**effect, "selector": {"abilities": ["Affinity"]}},
        ):
            with self.subTest(malformed=malformed):
                self.assertFalse(
                    fixed_type_to_hand_search_node_capabilities(
                        effects=(malformed,),
                        target_schema=None,
                        mechanic_ids=("cycling",),
                    )
                )

        for blocked in (
            "activation.source_zone_change.fixed",
            FIXED_TYPE_TO_HAND_SEARCH_CAPABILITY_ID,
            "variant.commander.zone_return",
            "zone.change.destination_replacement",
        ):
            with self.subTest(blocked=blocked):
                value = deepcopy(self.registry_value)
                dependency = next(
                    row
                    for row in value["capabilities"]
                    if row["id"] == blocked
                )
                dependency["status"] = "blocked"
                dependency["blockers"] = ["focused Typecycling mutation"]
                registry = CapabilityRegistry(value)
                registry.mark_evidence_verified("0" * 64)
                ir = self.compile(
                    "Basic landcycling {1}", capabilities=registry
                )
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)

    def test_typecycling_compiler_mutant_is_killed(self):
        def assert_exact() -> None:
            ir = self.compile("Basic landcycling {1}")
            self.assertEqual("exact", ir.status)
            self.assertTrue(ir.faces[0].nodes[0].cost["discard_source"])

        assert_exact()
        with mock.patch.object(
            cycling_nodes_module,
            "compile_typecycling_ability",
            return_value=None,
        ):
            with self.assertRaises(AssertionError):
                assert_exact()


class TypecyclingRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db, cls.mishra, cls.zimone = load_assets()
        cls.capabilities = load_default_capability_registry()
        cls.record = cls.db.lookup("Ash Barrens")

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def session(self, seed: int, *, players: int = 4):
        session = make_session(
            self.db,
            self.mishra,
            self.zimone,
            players=players,
            seed=seed,
        )
        keep_all(session)
        return session

    @staticmethod
    def resolve_until(session, predicate, *, limit: int = 32) -> None:
        for _ in range(limit):
            if predicate():
                return
            principals = session.pending_principals()
            if not principals:
                raise AssertionError("Typecycling resolution stopped")
            result = session.act(principals[0], {"action_id": "pass"})
            if not result.ok:
                raise AssertionError(result.summary)
        raise AssertionError("Typecycling did not resolve within the bounded loop")

    def install_source(
        self,
        session,
        *,
        mana: int = 1,
        record: CardRecord | None = None,
        ability_id: str = "ab2",
    ):
        engine = session.engine
        source_record = record or self.record
        source = CardInstance(
            object_id=(
                f"fixture:typecycling:{source_record.oracle_id}:"
                f"{engine.state.game_id}"
            ),
            ref="A-typecycling-source",
            oracle_id=source_record.oracle_id,
            printed_name=source_record.name,
            owner="A",
            controller="A",
            zone="hand",
            zone_timestamp=engine.state.event_sequence + 1,
            known_to=["A"],
            revealed_to=["A"],
        )
        engine.state.cards[source.object_id] = source
        engine.state.players["A"].zones["hand"].append(source.object_id)
        registry = SemanticRegistry(include_builtin_packs=False)
        result = register_generated_programs(
            self.db,
            registry,
            (source_record,),
            capability_registry=self.capabilities,
            capability_profile="commander_review",
            promote_exact_runtime_handlers=True,
        )
        self.assertGreaterEqual(result["runtime_handlers_promoted"], 1)
        engine.semantics = registry
        engine._semantic_trust_cache.clear()
        engine.state.players["A"].mana_pool["C"] = mana
        engine.state.active_player = "A"
        engine.state.started = True
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.stack.clear()
        engine.state.priority_passes = []
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine.permissions.invalidate_current()
        engine._grant_priority("A")
        engine.pump()
        return source, f"activate:{source.ref}:{ability_id}"

    def begin_search(self, session):
        source, action_id = self.install_source(session)
        result = session.act("pilot:A", {"action_id": action_id})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("graveyard", source.zone)
        self.assertTrue(session.engine.state.stack)
        self.resolve_until(
            session,
            lambda: (
                session.engine.state.pending_decision is not None
                and session.engine.state.pending_decision.kind == "semantic.search"
            ),
        )
        return source

    def test_typecycling_descriptor_only_activates_from_hand(self):
        session = self.session(7022911, players=2)
        engine = session.engine
        source, action_id = self.install_source(session)
        abilities = engine._activated_abilities(source)
        typecycling = next(
            ability for ability in abilities if ability.ability_id == "ab2"
        )
        self.assertEqual(("hand",), typecycling.zones)
        actions = session.packet("pilot:A", full=True)["decision"]["ctx"][
            "legal"
        ]["actions"]
        self.assertIn(action_id, {action["id"] for action in actions})

        for zone in ("battlefield", "graveyard", "exile", "library"):
            with self.subTest(zone=zone):
                engine.move_card(source.object_id, zone, log=False)
                abilities = engine._activated_abilities(source)
                typecycling = next(
                    ability for ability in abilities if ability.ability_id == "ab2"
                )
                self.assertEqual(("hand",), typecycling.zones)
                engine.state.pending_decision = None
                engine.state.priority_player = None
                engine.permissions.invalidate_current()
                engine._grant_priority("A")
                engine.pump()
                actions = session.packet("pilot:A", full=True)["decision"][
                    "ctx"
                ]["legal"]["actions"]
                self.assertNotIn(
                    action_id, {action["id"] for action in actions}
                )

    def test_typecycling_search_is_private_revealed_and_replays_exactly(self):
        session = self.session(7022912)
        engine = session.engine
        self.begin_search(session)
        packet = session.packet("pilot:A", full=True)
        decision = packet["decision"]
        candidates = decision["ctx"]["search_cards"]
        self.assertTrue(candidates)
        schema = decision["legal_actions"][0]["choice_schema"]
        self.assertEqual(0, schema["minimum"])
        self.assertTrue(schema["rules_may_fail_to_find"])
        candidate_refs = {candidate["id"] for candidate in candidates}
        for seat in ("B", "C", "D"):
            opposing = json.dumps(session.packet(f"pilot:{seat}", full=True))
            self.assertNotIn("search_cards", opposing)
            for candidate_ref in candidate_refs:
                self.assertNotIn(candidate_ref, opposing)
        chosen = candidates[0]
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        result = session.act(
            "pilot:A",
            {"action_id": "choose", "search_card": chosen["id"]},
        )
        self.assertTrue(result.ok, result.summary)
        selected = next(
            card for card in engine.state.cards.values() if card.ref == chosen["id"]
        )
        self.assertEqual("hand", selected.zone)
        for seat in ("B", "C", "D"):
            opposing = json.dumps(session.packet(f"pilot:{seat}", full=True))
            self.assertIn(chosen["id"], opposing)
            self.assertIn(chosen["name"], opposing)
        expected_hash = authoritative_state_hash(engine.state)

        with tempfile.TemporaryDirectory() as temporary:
            game_dir = Path(temporary) / "typecycling-replay"
            session.save(game_dir)
            replay = replay_record(game_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_unpayable_typecycling_is_not_offered_and_is_atomic(self):
        session = self.session(7022913, players=2)
        engine = session.engine
        source, action_id = self.install_source(session, mana=0)
        actions = session.packet("pilot:A", full=True)["decision"]["ctx"][
            "legal"
        ]["actions"]
        self.assertNotIn(action_id, {action["id"] for action in actions})
        before = authoritative_state_hash(engine.state)

        result = session.act("pilot:A", {"action_id": action_id})

        self.assertFalse(result.ok)
        self.assertEqual(before, authoritative_state_hash(engine.state))
        self.assertEqual("hand", source.zone)

    def test_typecycling_discard_replacement_resumes_before_private_search(self):
        session = self.session(7022914)
        engine = session.engine
        source, action_id = self.install_source(session)
        voidwalker_record = self.db.lookup("Dauthi Voidwalker")
        voidwalker = CardInstance(
            object_id="fixture:typecycling-voidwalker",
            ref="B-typecycling-voidwalker",
            oracle_id=voidwalker_record.oracle_id,
            printed_name=voidwalker_record.name,
            owner="B",
            controller="B",
            zone="battlefield",
            zone_timestamp=engine.state.event_sequence + 1,
            acquired_control_turn_count=-1,
            known_to=list(engine.seats),
            revealed_to=list(engine.seats),
        )
        engine.state.cards[voidwalker.object_id] = voidwalker
        engine.state.players["B"].zones["battlefield"].append(
            voidwalker.object_id
        )
        register_generated_programs(
            self.db,
            engine.semantics,
            (voidwalker_record,),
            trust_level="provisional",
            capability_registry=self.capabilities,
            capability_profile=engine.state.config.review_profile,
            promote_exact_runtime_handlers=True,
            promote_exact_effect_programs=True,
            promote_exact_capability_declarations=True,
        )
        engine.create_token(
            "B",
            name="",
            copy_of=voidwalker.ref,
            reason="Typecycling discard replacement ordering witness",
        )
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine.state.priority_passes = []
        engine._grant_priority("A")
        engine.pump()
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        mana_before = sum(engine.state.players["A"].mana_pool.values())

        result = session.act("pilot:A", {"action_id": action_id})

        self.assertTrue(result.ok, result.summary)
        self.assertEqual("replacement.order", engine.state.pending_decision.kind)
        self.assertEqual("hand", source.zone)
        self.assertFalse(engine.state.stack)
        self.assertEqual(
            mana_before, sum(engine.state.players["A"].mana_pool.values())
        )
        projected = StateProjector(self.db, engine.state)._decision("pilot:A")
        self.assertIsNotNone(projected)
        for seat in ("B", "C", "D"):
            self.assertIsNone(
                StateProjector(self.db, engine.state)._decision(f"pilot:{seat}")
            )
        assert projected is not None
        selected = projected["ctx"]["options"][0]["id"]

        with tempfile.TemporaryDirectory() as temporary:
            game_dir = Path(temporary) / "typecycling-replacement-replay"
            session.save(game_dir)
            restarted = CommanderSession.load(self.db, game_dir)
            result = restarted.act(
                "pilot:A", {"a": "choose", "replacement": selected}
            )
            self.assertTrue(result.ok, result.summary)
            self.assertEqual(
                "exile", restarted.engine.state.cards[source.object_id].zone
            )
            self.resolve_until(
                restarted,
                lambda: (
                    restarted.engine.state.pending_decision is not None
                    and restarted.engine.state.pending_decision.kind
                    == "semantic.search"
                ),
            )
            packet = restarted.packet("pilot:A", full=True)
            chosen = packet["decision"]["ctx"]["search_cards"][0]["id"]
            result = restarted.act(
                "pilot:A", {"action_id": "choose", "search_card": chosen}
            )
            self.assertTrue(result.ok, result.summary)
            expected_hash = authoritative_state_hash(restarted.engine.state)
            restarted.save(game_dir)
            replay = replay_record(game_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_typecycling_search_destination_replacement_resumes_atomically(self):
        session = self.session(7022915, players=2)
        engine = session.engine
        wizardcycling = self.db.lookup("Vedalken Aethermage")
        source, action_id = self.install_source(
            session,
            mana=3,
            record=wizardcycling,
            ability_id="ab3",
        )
        commander = next(
            card
            for card in engine.state.cards.values()
            if card.owner == "A" and card.is_commander
        )
        wizard = self.db.lookup("Azami, Lady of Scrolls")
        engine._remove_from_zone(commander)
        commander.oracle_id = wizard.oracle_id
        commander.printed_name = wizard.name
        commander.controller = "A"
        commander.zone = "library"
        commander.zone_change_counter += 1
        commander.zone_timestamp = engine.state.event_sequence + 1
        commander.known_to = []
        commander.revealed_to = []
        engine.state.players["A"].zones["library"].append(commander.object_id)
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine._grant_priority("A")
        engine.pump()

        result = session.act("pilot:A", {"action_id": action_id})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("graveyard", source.zone)
        self.resolve_until(
            session,
            lambda: (
                engine.state.pending_decision is not None
                and engine.state.pending_decision.kind == "semantic.search"
            ),
        )
        candidates = session.packet("pilot:A", full=True)["decision"]["ctx"][
            "search_cards"
        ]
        self.assertIn(
            commander.ref, {candidate["id"] for candidate in candidates}
        )
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        result = session.act(
            "pilot:A",
            {"action_id": "choose", "search_card": commander.ref},
        )

        self.assertTrue(result.ok, result.summary)
        self.assertEqual("replacement.order", engine.state.pending_decision.kind)
        self.assertEqual("library", commander.zone)
        projected = StateProjector(self.db, engine.state)._decision("pilot:A")
        self.assertIsNotNone(projected)
        self.assertIsNone(
            StateProjector(self.db, engine.state)._decision("pilot:B")
        )
        assert projected is not None
        selected = next(
            option["id"]
            for option in projected["ctx"]["options"]
            if not option.get("decline")
        )
        result = session.act(
            "pilot:A", {"action_id": "choose", "replacement": selected}
        )
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("command", commander.zone)
        expected_hash = authoritative_state_hash(engine.state)

        with tempfile.TemporaryDirectory() as temporary:
            game_dir = Path(temporary) / "typecycling-search-replacement-replay"
            session.save(game_dir)
            replay = replay_record(game_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_typecycling_search_continuation_tampering_is_atomic(self):
        mutations = (
            ("actor", lambda selection: selection.__setitem__("actor", "B")),
            (
                "revision",
                lambda selection: selection.__setitem__(
                    "state_revision", selection["state_revision"] + 1
                ),
            ),
            (
                "source",
                lambda selection: selection.__setitem__(
                    "source_ref", "tampered-source"
                ),
            ),
            (
                "candidates",
                lambda selection: selection["payload"].__setitem__(
                    "legal_refs", ["tampered-card"]
                ),
            ),
        )
        for index, (label, mutate) in enumerate(mutations):
            with self.subTest(label=label):
                session = self.session(7022920 + index, players=2)
                self.begin_search(session)
                decision = session.engine.state.pending_decision
                assert decision is not None
                mutate(decision.continuation["selection"])
                before = authoritative_state_hash(session.engine.state)
                result = session.act(
                    "pilot:A", {"action_id": "choose", "search_cards": []}
                )
                self.assertFalse(result.ok)
                self.assertEqual(
                    before, authoritative_state_hash(session.engine.state)
                )


if __name__ == "__main__":
    unittest.main()
