from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT, keep_all, make_session, pass_current
from quorune.carddb import CardDatabase
from quorune.compiler.keyword_event_effect_nodes import (
    FIXED_KEYWORD_EVENT_EFFECT_MECHANIC,
    fixed_keyword_event_effect_spec,
)
from quorune.continuous_effect_state import commit_continuous_effect
from quorune.continuous_effects import (
    ContinuousEffect,
    ContinuousEffectDuration,
    ContinuousEffectOrigin,
    ContinuousObjectIdentity,
    ContinuousOperation,
    Layer,
)
from quorune.mana_provenance import (
    ManaProvenanceLot,
    add_mana,
    clear_step_mana,
    mana_provenance_lots,
    spend_mana,
)
from quorune.damage import damage_proposal, resolve_damage_batch
from quorune.deck import DeckLoader
from quorune.engine import TURN_STEPS
from quorune.model import CardInstance, CombatState, PlayerState
from quorune.object_predicate import ObjectQuerySpec
from quorune.oracle_ir import compile_oracle_card, generated_programs
from quorune.projection import StateProjector
from quorune.record import (
    authoritative_state_hash,
    checkpoint_envelope,
    replay_record,
)
from quorune.rules.capabilities import (
    CapabilityRegistry,
    load_default_capability_registry,
)
from quorune.replacement.immutable import FrozenMap
from quorune.semantic_choices.intent_replacement import (
    semantic_intent_identity,
    validate_semantic_intent_identity,
)
from quorune.semantic_runtime.intents import CreateTokenIntent
from scripts.build_test_database import build_fixture_database


REGISTRY_PATH = ROOT / "quorune" / "rules" / "capability-registry.json"
FIXTURE_PATH = (
    ROOT / "tests" / "fixtures" / "fixed-keyword-event-effect-cards.json"
)
TEMPLATE_IDS = {
    f"fixed-keyword-{keyword}-event-effect-v1"
    for keyword in (
        "afflict",
        "annihilator",
        "firebending",
        "ingest",
        "mobilize",
        "soulshift",
    )
}


def focused_database(directory: str) -> CardDatabase:
    database = Path(directory) / "fixed-keyword-event-effects.sqlite3"
    build_fixture_database(
        [
            ROOT / "tests" / "fixtures" / "scryfall-exact-lists.json",
            FIXTURE_PATH,
        ],
        database,
    )
    return CardDatabase(database)


class FixedKeywordEventEffectCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory()
        cls.db = focused_database(cls.temporary.name)
        cls.capabilities = load_default_capability_registry()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.db.close()
        cls.temporary.cleanup()

    def test_keyword_event_effect_contract_matrix_and_exclusions(self):
        cases = (
            ("Afflict Event Fixture", "afflict", 2, "creature.becomes_blocked"),
            ("Annihilator Event Fixture", "annihilator", 2, "creature.attacks"),
            ("Firebending Event Fixture", "firebending", 2, "creature.attacks"),
            ("Ingest Event Fixture", "ingest", 1, "damage.dealt.self"),
            ("Mobilize Event Fixture", "mobilize", 2, "creature.attacks"),
            ("Soulshift Event Fixture", "soulshift", 3, "permanent.graveyard.self"),
        )
        for name, keyword, amount, event in cases:
            with self.subTest(name=name):
                record = self.db.lookup(name)
                spec = fixed_keyword_event_effect_spec(
                    record.oracle_text.split(" (", 1)[0]
                )
                self.assertIsNotNone(spec)
                self.assertEqual((keyword, amount), (spec.keyword, spec.amount))
                ir = compile_oracle_card(
                    record,
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                node = next(
                    node
                    for node in ir.faces[0].nodes
                    if node.template_id in TEMPLATE_IDS
                )
                self.assertTrue(node.exact)
                self.assertEqual(event, node.event)
                self.assertIn(keyword, node.mechanics)
                self.assertIn(
                    FIXED_KEYWORD_EVENT_EFFECT_MECHANIC,
                    node.mechanics,
                )
                self.assertEqual(
                    (f"trigger.keyword.{keyword}.fixed",),
                    node.capability_dependencies,
                )

        base = self.db.lookup("Afflict Event Fixture")
        for index, text in enumerate(
            (
                "Afflict X",
                "Afflict 0",
                "Afflict 21",
                "Afflict 2, afflict 2",
                "Ingest 2",
                "Mobilize X, where X is this creature's power.",
                "Soulshift X",
            )
        ):
            with self.subTest(text=text):
                keyword = text.split()[0].rstrip(",")
                ir = compile_oracle_card(
                    replace(
                        base,
                        oracle_id=f"25000000-0000-4000-8000-{index + 100:012d}",
                        name="Excluded Keyword Fixture",
                        oracle_text=text,
                        keywords=(keyword,),
                    ),
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                self.assertFalse(
                    any(
                        node.template_id in TEMPLATE_IDS
                        for node in ir.faces[0].nodes
                    )
                )
                self.assertTrue(ir.material_residuals)

        value = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        capability = next(
            row
            for row in value["capabilities"]
            if row["id"] == "trigger.keyword.afflict.fixed"
        )
        capability["status"] = "blocked"
        capability["blockers"] = ["test mutation"]
        blocked = compile_oracle_card(
            base,
            capability_registry=CapabilityRegistry(value),
            capability_profile="commander_review",
        )
        blocked_node = next(
            node
            for node in blocked.faces[0].nodes
            if node.template_id == "fixed-keyword-afflict-event-effect-v1"
        )
        self.assertFalse(blocked_node.exact)
        self.assertTrue(blocked_node.residual_ids)

        with patch(
            "quorune.oracle_ir.fixed_keyword_event_effect_node",
            return_value=None,
        ):
            mutated = compile_oracle_card(
                base,
                capability_registry=self.capabilities,
                capability_profile="commander_review",
            )
        self.assertFalse(
            any(node.template_id in TEMPLATE_IDS for node in mutated.faces[0].nodes)
        )


class FixedKeywordEventEffectRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory()
        cls.db = focused_database(cls.temporary.name)
        loader = DeckLoader(cls.db)
        cls.mishra = loader.load(
            ROOT / "examples" / "mishra-eminent-one.txt",
            commander="Mishra, Eminent One",
            deck_name="Mishra",
        )
        cls.zimone = loader.load(
            ROOT / "examples" / "zimone-and-dina.txt",
            commander="Zimone and Dina",
            deck_name="Zimone",
        )
        cls.capabilities = load_default_capability_registry()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.db.close()
        cls.temporary.cleanup()

    def session(self, seed: int, *, players: int = 2):
        session = make_session(
            self.db,
            self.mishra,
            self.zimone,
            players=players,
            seed=seed,
            auto_pass_empty=False,
        )
        keep_all(session)
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine.state.priority_passes = []
        engine.state.pending_trigger_batches.clear()
        engine.state.stack.clear()
        session.commands.clear()
        session.decisions.clear()
        return session

    def add_card(
        self,
        engine,
        *,
        seat: str,
        name: str,
        ref: str,
        zone: str,
        controller: str | None = None,
    ) -> CardInstance:
        record = self.db.lookup(name)
        public = zone == "battlefield"
        card = CardInstance(
            object_id=f"fixture:{ref}",
            ref=ref,
            oracle_id=record.oracle_id,
            printed_name=record.name,
            owner=seat,
            controller=controller or seat,
            zone=zone,
            zone_timestamp=engine.state.event_sequence + 1,
            acquired_control_turn_count=-1,
            known_to=list(engine.seats) if public else [seat],
            revealed_to=list(engine.seats) if public else [],
        )
        engine.state.cards[card.object_id] = card
        engine.state.players[seat].zones[zone].append(card.object_id)
        return card

    def register(self, engine, source: CardInstance):
        programs = [
            program
            for program in generated_programs(
                self.db,
                self.db.by_oracle_id(source.oracle_id),
                trust_level="trusted",
                capability_registry=self.capabilities,
                capability_profile="commander_review",
            )
            if program.provenance.get("template_id") in TEMPLATE_IDS
        ]
        self.assertEqual(1, len(programs))
        engine.semantics.put(programs[0])
        return programs[0]

    @staticmethod
    def resolve_top(engine) -> None:
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine._prepare_stack_resolution()

    def declare_attack(self, session, source: CardInstance, target: str = "B"):
        engine = session.engine
        engine.state.active_player = "A"
        engine.state.phase_index = 5
        engine.state.phase = "combat"
        engine.state.step = "declare_attackers"
        engine.state.combat = CombatState()
        engine._issue_attackers()
        result = session.act(
            "pilot:A",
            {"a": "attack", "atk": {source.ref: target}},
        )
        self.assertTrue(result.ok, result.summary)

    def test_keyword_event_effect_runtime_boundaries(self):
        player = PlayerState(seat="A", name="A")
        add_mana(player, {"R": 2}, retention="end_of_combat")
        add_mana(player, {"R": 2})
        spend_mana(
            player,
            {"R": 3},
            snow_payment=None,
            restriction_allows=lambda _restriction, _context: True,
            spend_context=None,
        )
        self.assertEqual(1, player.mana_pool["R"])
        self.assertEqual(
            (
                ManaProvenanceLot.create(
                    {"R": 1},
                    retention="end_of_combat",
                ),
            ),
            mana_provenance_lots(player),
        )
        self.assertEqual(
            {},
            clear_step_mana(
                player,
                phase="combat",
                step="declare_blockers",
            ),
        )
        self.assertEqual(
            {"R": 1},
            clear_step_mana(
                player,
                phase="combat",
                step="end_combat",
            ),
        )
        self.assertEqual(0, player.mana_pool["R"])

        afflict_session = self.session(250101)
        afflict_engine = afflict_session.engine
        afflict = self.add_card(
            afflict_engine,
            seat="A",
            name="Afflict Event Fixture",
            ref="afflict-source",
            zone="battlefield",
        )
        afflict_program = self.register(afflict_engine, afflict)
        blocker_ref = afflict_engine.create_token(
            "B",
            name="Afflict blocker",
            characteristics={
                "type_line": "Token Creature — Soldier",
                "power": "2",
                "toughness": "2",
            },
        )[0]
        blocker = afflict_engine._resolve_object("B", blocker_ref)
        afflict.attacking = "B"
        afflict_engine.state.active_player = "A"
        afflict_engine.state.phase_index = 6
        afflict_engine.state.phase = "combat"
        afflict_engine.state.step = "declare_blockers"
        afflict_engine.state.combat = CombatState(
            attackers_declared=True,
            had_attacking_creature=True,
            attackers={afflict.object_id: "B"},
            attack_target_context={
                afflict.object_id: {
                    "target": "B",
                    "kind": "player",
                    "defending_player": "B",
                }
            },
            defending_players=["B"],
        )
        life_before = afflict_engine.state.players["B"].life
        afflict_engine._begin_blocker_decisions()
        blocked = afflict_session.act(
            "pilot:B",
            {"a": "block", "blk": {blocker.ref: afflict.ref}},
        )
        self.assertTrue(blocked.ok, blocked.summary)
        afflict_item = next(
            item
            for item in afflict_engine.state.stack
            if item.semantic_key == afflict_program.key
        )
        self.assertEqual("B", afflict_item.context["defending_player"])
        self.resolve_top(afflict_engine)
        self.assertEqual(
            life_before - 2,
            afflict_engine.state.players["B"].life,
        )

        annihilator_session = self.session(250102)
        annihilator_engine = annihilator_session.engine
        annihilator = self.add_card(
            annihilator_engine,
            seat="A",
            name="Annihilator Event Fixture",
            ref="annihilator-source",
            zone="battlefield",
        )
        annihilator_program = self.register(annihilator_engine, annihilator)
        sacrifice_refs = tuple(
            annihilator_engine.create_token(
                "B",
                name=f"Annihilator permanent {index}",
                characteristics={"type_line": "Token Artifact"},
            )[0]
            for index in range(2)
        )
        self.declare_attack(annihilator_session, annihilator)
        self.assertTrue(
            any(
                item.semantic_key == annihilator_program.key
                for item in annihilator_engine.state.stack
            )
        )
        self.resolve_top(annihilator_engine)
        self.assertIsNotNone(annihilator_engine.state.pending_decision)
        chosen = annihilator_session.act(
            "pilot:B",
            {"action_id": "choose", "cards": list(sacrifice_refs)},
        )
        self.assertTrue(chosen.ok, chosen.summary)
        self.assertTrue(
            all(
                all(
                    card.ref != ref or card.zone != "battlefield"
                    for card in annihilator_engine.state.cards.values()
                )
                for ref in sacrifice_refs
            )
        )

        firebending_session = self.session(250103)
        firebending_engine = firebending_session.engine
        firebending = self.add_card(
            firebending_engine,
            seat="A",
            name="Firebending Event Fixture",
            ref="firebending-source",
            zone="battlefield",
        )
        firebending_program = self.register(firebending_engine, firebending)
        self.declare_attack(firebending_session, firebending)
        self.assertTrue(
            any(
                item.semantic_key == firebending_program.key
                for item in firebending_engine.state.stack
            )
        )
        self.resolve_top(firebending_engine)
        self.assertEqual(2, firebending_engine.state.players["A"].mana_pool["R"])
        firebending_engine._clear_mana(reason="test combat step")
        self.assertEqual(2, firebending_engine.state.players["A"].mana_pool["R"])
        firebending_engine.state.step = "end_combat"
        firebending_engine._clear_mana(reason="test end of combat")
        self.assertEqual(0, firebending_engine.state.players["A"].mana_pool["R"])

        removed_session = self.session(250105)
        removed_engine = removed_session.engine
        removed = self.add_card(
            removed_engine,
            seat="A",
            name="Firebending Event Fixture",
            ref="removed-firebending-source",
            zone="battlefield",
        )
        removed_program = self.register(removed_engine, removed)
        commit_continuous_effect(
            removed_engine.state,
            ContinuousEffect(
                effect_id="fixture:remove-fixed-keyword-event-effect",
                source_id="fixture:remove-fixed-keyword-event-effect-owner",
                layer=Layer.ABILITY,
                sublayer="6",
                timestamp=removed_engine._next_zone_timestamp(),
                operations=(ContinuousOperation("remove_all_abilities"),),
                origin=ContinuousEffectOrigin.RESOLUTION,
                duration=ContinuousEffectDuration.UNTIL_END_OF_TURN,
                applies=ObjectQuerySpec(zones=("battlefield",)),
                locked_objects=(
                    ContinuousObjectIdentity(
                        object_id=removed.object_id,
                        logical_object_id=removed.logical_object_id,
                    ),
                ),
            ),
        )
        self.declare_attack(removed_session, removed)
        self.assertFalse(
            any(
                item.semantic_key == removed_program.key
                for item in removed_engine.state.stack
            )
        )

        ingest_session = self.session(250104, players=4)
        ingest_engine = ingest_session.engine
        ingest = self.add_card(
            ingest_engine,
            seat="A",
            name="Ingest Event Fixture",
            ref="ingest-source",
            zone="battlefield",
        )
        ingest_program = self.register(ingest_engine, ingest)
        top_id = ingest_engine.state.players["C"].zones["library"][-1]
        top = ingest_engine.state.cards[top_id]
        resolve_damage_batch(
            ingest_engine,
            (
                damage_proposal(
                    ingest_engine,
                    proposal_id="ingest-combat-player",
                    actor="A",
                    source_ref=ingest.ref,
                    target="C",
                    amount=1,
                    combat=True,
                    reason="Ingest positive witness",
                ),
            ),
        )
        ingest_engine._stabilize()
        self.assertTrue(
            any(
                item.semantic_key == ingest_program.key
                for item in ingest_engine.state.stack
            )
        )
        self.resolve_top(ingest_engine)
        self.assertEqual("exile", top.zone)
        stack_count = len(ingest_engine.state.stack)
        resolve_damage_batch(
            ingest_engine,
            (
                damage_proposal(
                    ingest_engine,
                    proposal_id="ingest-noncombat-player",
                    actor="A",
                    source_ref=ingest.ref,
                    target="C",
                    amount=1,
                    combat=False,
                    reason="Ingest negative witness",
                ),
            ),
        )
        ingest_engine._stabilize()
        self.assertEqual(stack_count, len(ingest_engine.state.stack))

    def test_keyword_event_effect_multiplayer_projection_and_replay(self):
        lot = ManaProvenanceLot.create(
            {"R": 2},
            retention="end_of_combat",
        )
        self.assertEqual(lot, ManaProvenanceLot.from_dict(lot.to_dict()))
        legacy = ManaProvenanceLot.create(
            {"U": 1},
            restriction="artifact_spell_only",
        )
        self.assertEqual(
            legacy,
            ManaProvenanceLot.from_dict(legacy.to_dict()),
        )
        intent = CreateTokenIntent(
            actor="A",
            controller="A",
            name="Warrior",
            quantity=2,
            characteristics=FrozenMap(
                {
                    "type_line": "Creature — Warrior",
                    "power": "1",
                    "toughness": "1",
                    "colors": ["R"],
                }
            ),
            tapped=True,
            attacking_assignments=("B", "C"),
            sacrifice_at_end_step=True,
            reason="Mobilize identity round trip",
        )
        kind, identity = semantic_intent_identity(intent)
        self.assertEqual(
            identity,
            validate_semantic_intent_identity(kind, identity),
        )
        ordinary_kind, ordinary_identity = semantic_intent_identity(
            CreateTokenIntent(
                actor="A",
                controller="A",
                name="Treasure",
                quantity=1,
                reason="Legacy token identity",
            )
        )
        legacy_identity = {
            key: value
            for key, value in ordinary_identity.items()
            if key not in {"tapped", "attacking_assignments"}
        }
        self.assertEqual(
            ordinary_identity,
            validate_semantic_intent_identity(
                ordinary_kind,
                legacy_identity,
            ),
        )

        mobilize_session = self.session(250201, players=4)
        mobilize_engine = mobilize_session.engine
        mobilize = self.add_card(
            mobilize_engine,
            seat="A",
            name="Mobilize Event Fixture",
            ref="mobilize-source",
            zone="battlefield",
        )
        mobilize_program = self.register(mobilize_engine, mobilize)
        planeswalker_ref = mobilize_engine.create_token(
            "C",
            name="Mobilize planeswalker recipient",
            characteristics={
                "type_line": "Token Planeswalker — Test",
                "loyalty": "5",
            },
        )[0]
        battle_ref = mobilize_engine.create_token(
            "D",
            name="Mobilize battle recipient",
            battle_protector="B",
            characteristics={
                "type_line": "Token Battle — Siege",
                "defense": "5",
            },
        )[0]
        self.declare_attack(mobilize_session, mobilize, target="B")
        self.assertTrue(
            any(
                item.semantic_key == mobilize_program.key
                for item in mobilize_engine.state.stack
            )
        )
        self.resolve_top(mobilize_engine)
        self.assertIsNotNone(mobilize_engine.state.pending_decision)
        self.assertEqual(
            "semantic.choice",
            mobilize_engine.state.pending_decision.kind,
        )
        projected = StateProjector(
            self.db,
            mobilize_engine.state,
        )._decision("pilot:A")
        self.assertIsNotNone(projected)
        schema = projected["ctx"]["legal_actions"][0]["choice_schema"]
        self.assertEqual("object_map", schema["shape"])
        self.assertEqual(
            {"mobilize:0", "mobilize:1"},
            set(schema["legal_refs"]),
        )
        self.assertTrue(
            {"B", "C", planeswalker_ref, battle_ref}
            <= set(schema["legal_values"])
        )
        for seat in ("B", "C", "D"):
            self.assertIsNone(
                StateProjector(
                    self.db,
                    mobilize_engine.state,
                )._decision(f"pilot:{seat}")
            )
        mobilize_session.initial_checkpoint = checkpoint_envelope(
            mobilize_engine.state
        )
        mobilize_session.commands.clear()
        mobilize_session.decisions.clear()
        chosen = mobilize_session.act(
            "pilot:A",
            {
                "action_id": "choose",
                "attacking": {
                    "mobilize:0": planeswalker_ref,
                    "mobilize:1": battle_ref,
                },
            },
        )
        self.assertTrue(chosen.ok, chosen.summary)
        warriors = [
            card
            for card in mobilize_engine.state.cards.values()
            if card.zone == "battlefield"
            and card.controller == "A"
            and card.printed_name == "Warrior"
        ]
        self.assertEqual(2, len(warriors))
        self.assertTrue(all(card.tapped for card in warriors))
        self.assertEqual(
            {planeswalker_ref, battle_ref},
            {card.attacking for card in warriors},
        )
        expected_hash = authoritative_state_hash(mobilize_engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "fixed-keyword-mobilize"
            mobilize_session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

        departing = warriors[0]
        mobilize_engine.move_card(
            departing.object_id,
            "exile",
            reason="Mobilize stale delayed source witness",
            semantic_events=True,
        )
        mobilize_engine.permissions.invalidate_current()
        mobilize_engine.state.pending_decision = None
        mobilize_engine.state.priority_player = None
        mobilize_engine.state.priority_passes = []
        mobilize_engine.state.phase_index = TURN_STEPS.index(
            ("ending", "end_step")
        )
        mobilize_engine._enter_step()
        if (
            mobilize_engine.state.pending_decision is not None
            and mobilize_engine.state.pending_decision.kind == "trigger.order"
        ):
            refs = [
                row["id"]
                for row in mobilize_engine.state.pending_decision.payload_by_actor[
                    "A"
                ]["triggers"]
            ]
            ordered = mobilize_session.act(
                "pilot:A",
                {"action_id": "order", "triggers": refs},
            )
            self.assertTrue(ordered.ok, ordered.summary)
        while mobilize_engine.state.stack:
            self.resolve_top(mobilize_engine)
        self.assertEqual("outside", departing.zone)
        self.assertTrue(
            all(
                card.zone != "battlefield"
                for card in warriors
                if card is not departing
            )
        )

        soulshift_session = self.session(250202, players=4)
        soulshift_engine = soulshift_session.engine
        soulshift = self.add_card(
            soulshift_engine,
            seat="B",
            name="Soulshift Event Fixture",
            ref="soulshift-source",
            zone="battlefield",
        )
        soulshift_engine.change_control(
            soulshift.object_id,
            "A",
            reason="Soulshift previous-controller witness",
        )
        small = self.add_card(
            soulshift_engine,
            seat="A",
            name="Small Spirit Target Fixture",
            ref="small-spirit",
            zone="graveyard",
        )
        large = self.add_card(
            soulshift_engine,
            seat="A",
            name="Large Spirit Target Fixture",
            ref="large-spirit",
            zone="graveyard",
        )
        soulshift_program = self.register(soulshift_engine, soulshift)
        departing_identity = soulshift.logical_object_id
        soulshift_engine.move_card(
            soulshift.object_id,
            "graveyard",
            reason="Soulshift LKI witness",
            semantic_events=True,
        )
        soulshift_engine._stabilize()
        item = next(
            item
            for item in soulshift_engine.state.stack
            if item.semantic_key == soulshift_program.key
        )
        self.assertEqual("A", item.controller)
        self.assertEqual(
            departing_identity,
            item.context["source_logical_object_id"],
        )
        self.assertEqual("semantic.target", soulshift_engine.state.pending_decision.kind)
        target_projection = StateProjector(
            self.db,
            soulshift_engine.state,
        )._decision("pilot:A")
        target_schema = target_projection["ctx"]["legal_actions"][0][
            "target_schema"
        ]
        self.assertIn(small.ref, target_schema["legal_refs"])
        self.assertNotIn(large.ref, target_schema["legal_refs"])
        targeted = soulshift_session.act(
            "pilot:A",
            {"action_id": "choose", "targets": [small.ref]},
        )
        self.assertTrue(targeted.ok, targeted.summary)
        self.resolve_top(soulshift_engine)
        self.assertEqual("semantic.choice", soulshift_engine.state.pending_decision.kind)
        soulshift_session.initial_checkpoint = checkpoint_envelope(
            soulshift_engine.state
        )
        soulshift_session.commands.clear()
        soulshift_session.decisions.clear()
        optional = soulshift_session.act(
            "pilot:A",
            {"action_id": "choose", "choice": "apply"},
        )
        self.assertTrue(optional.ok, optional.summary)
        self.assertEqual("hand", small.zone)
        self.assertEqual("graveyard", large.zone)
        expected_soulshift_hash = authoritative_state_hash(
            soulshift_engine.state
        )
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "fixed-keyword-soulshift"
            soulshift_session.save(record_dir)
            soulshift_replay = replay_record(
                record_dir,
                self.db,
                verify=True,
            )
        self.assertTrue(soulshift_replay["ok"], soulshift_replay)
        self.assertEqual(
            expected_soulshift_hash,
            soulshift_replay["final_state_hash"],
        )


if __name__ == "__main__":
    unittest.main()
