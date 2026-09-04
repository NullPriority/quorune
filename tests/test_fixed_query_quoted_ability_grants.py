from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from common import keep_all, make_session, pass_current
from quorune.carddb import CardDatabase, CardRecord
from quorune.continuous_effect_model import (
    ContinuousEffect,
    ContinuousEffectDuration,
    ContinuousEffectOrigin,
    ContinuousObjectIdentity,
    ContinuousOperation,
    Layer,
)
from quorune.continuous_effect_state import commit_continuous_effect
from quorune.deck import DeckLoader
from quorune.model import CardInstance, CombatState
from quorune.oracle_ir import (
    compile_oracle_card,
    generated_programs,
)
from quorune.record import (
    authoritative_state_hash,
    checkpoint_envelope,
    replay_record,
)
from quorune.rules.capabilities import (
    CapabilityRegistry,
    load_default_capability_registry,
)
from scripts.build_test_database import build_fixture_database


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "fixed-query-quoted-ability-grants.json"
HANDLER_ID = "continuous.ability.fixed-query-grant.v1"
TEMPLATE_ID = "continuous-fixed-query-granted-ability-v1"
REGISTRY = ROOT / "quorune" / "rules" / "capability-registry.json"


class _NoRulingsDatabase:
    @staticmethod
    def rulings(record):
        del record
        return ()


def fixture_record(text: str, *, suffix: int = 1) -> CardRecord:
    return CardRecord(
        oracle_id=f"00000000-0000-4000-8000-{119_070_000 + suffix:012d}",
        name="Query Grant Compiler Fixture",
        mana_cost="{2}",
        mana_value=2.0,
        type_line="Enchantment",
        oracle_text=text,
        power=None,
        toughness=None,
        loyalty=None,
        defense=None,
        colors=(),
        color_identity=(),
        keywords=(),
        produced_mana=(),
        layout="normal",
        released_at="2026-09-04",
        legalities={"commander": "legal"},
        faces=(),
        raw={},
    )


class FixedQueryQuotedAbilityGrantCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.capabilities = load_default_capability_registry()
        cls.fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def compile(self, record: CardRecord):
        return compile_oracle_card(
            record,
            capability_registry=self.capabilities,
            capability_profile="commander_review",
        )

    def test_group_quotes_compile_exact_typed_inner_programs_and_costs(self):
        for index, row in enumerate(self.fixture["cards"]):
            record = fixture_record(row["oracle_text"], suffix=index)
            with self.subTest(name=row["name"]):
                ir = self.compile(record)
                self.assertEqual("exact", ir.status, ir.to_dict())
                outer = next(
                    node
                    for node in ir.faces[0].nodes
                    if node.template_id == TEMPLATE_ID
                )
                inner = next(
                    node
                    for node in ir.faces[0].nodes
                    if node.kind.startswith("granted_")
                )
                descriptor = outer.handlers[0]
                fragment = descriptor["modifier"]["add_ability_fragments"][0]
                self.assertEqual(HANDLER_ID, descriptor["handler_id"])
                self.assertIn(
                    descriptor["condition"]["target_controller"],
                    {"source_controller", "any"},
                )
                self.assertTrue(inner.exact)
                self.assertEqual(
                    inner.node_id,
                    f"{outer.node_id}:granted",
                )
                self.assertEqual(
                    fragment["value"]["semantic_key"],
                    f"{record.oracle_id}:{fragment['value']['ability_id']}",
                )
                programs = generated_programs(
                    _NoRulingsDatabase(),
                    record,
                    trust_level="trusted",
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                granted = next(
                    program
                    for program in programs
                    if program.ability_id == fragment["value"]["ability_id"]
                )
                self.assertEqual(
                    fragment["value"]["semantic_key"],
                    granted.key,
                )
                self.assertTrue(granted.provenance["granted_only"])

        costs = {
            row["name"]: self.compile(fixture_record(row["oracle_text"], suffix=20 + index))
            for index, row in enumerate(self.fixture["cards"])
        }
        sacrifice = next(
            node
            for node in costs["Query Grant Sacrifice Fixture"].faces[0].nodes
            if node.template_id == TEMPLATE_ID
        ).handlers[0]["modifier"]["add_ability_fragments"][0]["value"]
        self.assertEqual(2, sacrifice["schema_version"])
        self.assertTrue(sacrifice["sacrifice_source"])
        discard = next(
            node
            for node in costs["Query Grant Discard Fixture"].faces[0].nodes
            if node.template_id == TEMPLATE_ID
        ).handlers[0]["modifier"]["add_ability_fragments"][0]["value"]
        self.assertEqual("discard", discard["choices"][0]["kind"])
        untap = next(
            node
            for node in costs["Query Grant Untap Fixture"].faces[0].nodes
            if node.template_id == TEMPLATE_ID
        ).handlers[0]["modifier"]["add_ability_fragments"][0]["value"]
        self.assertTrue(untap["untap_source"])
        life = next(
            node
            for node in costs["Query Grant Life Fixture"].faces[0].nodes
            if node.template_id == TEMPLATE_ID
        ).handlers[0]["modifier"]["add_ability_fragments"][0]["value"]
        self.assertEqual(2, life["life_payment"])
        restricted = next(
            node
            for node in costs["Query Grant Restricted Mana Fixture"].faces[0].nodes
            if node.template_id == TEMPLATE_ID
        ).handlers[0]["modifier"]["add_ability_fragments"][0]["value"]
        self.assertEqual(
            "nonartifact_spell_prohibited",
            restricted["mana_spend_restriction"],
        )

    def test_group_quote_grammar_and_inner_behavior_fail_closed(self):
        fixtures = (
            'Creatures you control have "This creature has flying."',
            'Creatures you control have "{T}: Draw a card." and "{T}: Add {G}."',
            'Creatures you control have "{T}: Tap this Aura."',
            'Creatures you control have "Exile this creature: Draw a card."',
            'Creatures you control have "Pay {E}: Draw a card."',
            'Creature cards in your graveyard have "{T}: Add {G}."',
            'Creatures you control have "{T}: Query Grant Compiler Fixture deals 1 damage to any target."',
        )
        for index, text in enumerate(fixtures):
            with self.subTest(text=text):
                ir = self.compile(fixture_record(text, suffix=100 + index))
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)
                self.assertFalse(
                    any(
                        node.kind.startswith("granted_")
                        for node in ir.faces[0].nodes
                    )
                )

    def test_group_quote_dependencies_fail_closed(self):
        record = fixture_record(
            'Creatures you control have "{T}: This creature deals 1 damage to any target."',
            suffix=200,
        )
        baseline = self.compile(record)
        outer = next(
            node for node in baseline.faces[0].nodes if node.template_id == TEMPLATE_ID
        )
        inner_dependency = next(
            dependency
            for dependency in outer.capability_dependencies
            if dependency != "continuous.ability.fixed_query_grant"
        )
        for capability_id in (
            "continuous.ability.fixed_query_grant",
            inner_dependency,
        ):
            with self.subTest(capability_id=capability_id):
                value = json.loads(REGISTRY.read_text(encoding="utf-8"))
                row = next(
                    item
                    for item in value["capabilities"]
                    if item["id"] == capability_id
                )
                row["status"] = "blocked"
                row["blockers"] = ["focused dependency mutation"]
                registry = CapabilityRegistry(value)
                registry.mark_evidence_verified("0" * 64)
                mutated = compile_oracle_card(
                    record,
                    capability_registry=registry,
                    capability_profile="commander_review",
                )
                candidate = next(
                    node
                    for node in mutated.faces[0].nodes
                    if node.node_id == outer.node_id
                )
                self.assertFalse(candidate.exact)
                self.assertTrue(candidate.residual_ids)


class FixedQueryQuotedAbilityGrantRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        database = Path(cls.temporary.name) / "query-grants.sqlite3"
        build_fixture_database(
            [
                ROOT / "tests" / "fixtures" / "scryfall-exact-lists.json",
                FIXTURE,
            ],
            database,
        )
        cls.db = CardDatabase(database)
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
    def tearDownClass(cls):
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
        for row in json.loads(FIXTURE.read_text(encoding="utf-8"))["cards"]:
            record = self.db.lookup(row["name"])
            for program in generated_programs(
                self.db,
                record,
                trust_level="trusted",
                capability_registry=self.capabilities,
                capability_profile="commander_review",
            ):
                engine.semantics.put(program)
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.pending_trigger_batches.clear()
        engine.state.stack.clear()
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.priority_player = "A"
        engine.state.priority_passes = []
        return session

    def add_source(self, engine, *, name: str, ref: str, seat: str = "A"):
        record = self.db.lookup(name)
        value = CardInstance(
            object_id=f"query-grant-source:{ref}",
            ref=ref,
            oracle_id=record.oracle_id,
            printed_name=record.name,
            owner=seat,
            controller=seat,
            zone="battlefield",
            zone_timestamp=engine._next_zone_timestamp(),
            known_to=list(engine.seats),
            revealed_to=list(engine.seats),
            entered_battlefield_turn_sequence=engine.state.turn_sequence - 1,
        )
        engine.state.cards[value.object_id] = value
        engine.state.players[seat].zones["battlefield"].append(value.object_id)
        return value

    @staticmethod
    def creature(engine, *, ref: str, seat: str = "A"):
        created = engine.create_token(
            seat,
            name=f"Query grant recipient {ref}",
            characteristics={
                "type_line": "Token Creature — Test",
                "power": "2",
                "toughness": "2",
                "keywords": [],
            },
        )[0]
        value = engine._resolve_object(seat, created, zones={"battlefield"})
        value.entered_battlefield_turn_sequence = engine.state.turn_sequence - 1
        value.acquired_control_turn_count = -1
        return value

    @staticmethod
    def grant_ability(engine, recipient, semantic_key: str):
        return next(
            ability
            for ability in engine._activated_abilities(recipient)
            if ability.builtin_semantic_key == semantic_key
        )

    @staticmethod
    def remove_all_abilities(engine, card, *, suffix: str):
        commit_continuous_effect(
            engine.state,
            ContinuousEffect(
                effect_id=f"test:query-grant-removal:{suffix}",
                source_id=f"test:query-grant-removal-source:{suffix}",
                layer=Layer.ABILITY,
                sublayer="6",
                timestamp=engine._next_zone_timestamp(),
                operations=(ContinuousOperation("remove_all_abilities"),),
                origin=ContinuousEffectOrigin.RESOLUTION,
                duration=ContinuousEffectDuration.UNTIL_END_OF_TURN,
                locked_objects=(
                    ContinuousObjectIdentity(
                        object_id=card.object_id,
                        logical_object_id=card.logical_object_id,
                    ),
                ),
            ),
        )

    @staticmethod
    def resolve_top(engine):
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine._prepare_stack_resolution()

    @staticmethod
    def activation_offer(session, recipient, ability):
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine._grant_priority(recipient.controller)
        engine.pump()
        packet = session.packet(f"pilot:{recipient.controller}", full=True)
        return next(
            action
            for action in packet["decision"]["ctx"]["legal"]["actions"]
            if action["id"]
            == f"activate:{recipient.ref}:{ability.ability_id}"
        )

    def test_live_query_recomputes_control_entry_source_and_layer_six(self):
        session = self.session(1190701, players=4)
        engine = session.engine
        source = self.add_source(
            engine,
            name="Query Grant Damage Fixture",
            ref="live-source",
        )
        controlled = self.creature(engine, ref="controlled")
        opponent = self.creature(engine, ref="opponent", seat="B")
        key = "fixture-query-grant-damage:ability:granted:front:n1"

        self.grant_ability(engine, controlled, key)
        self.assertFalse(
            any(
                ability.builtin_semantic_key == key
                for ability in engine._activated_abilities(opponent)
            )
        )
        entrant = self.creature(engine, ref="entrant")
        self.grant_ability(engine, entrant, key)

        engine.change_control(
            controlled.object_id,
            "B",
            reason="query grant control witness",
        )
        self.assertFalse(
            any(
                ability.builtin_semantic_key == key
                for ability in engine._activated_abilities(controlled)
            )
        )
        engine.change_control(
            controlled.object_id,
            "A",
            reason="query grant restored control witness",
        )
        self.grant_ability(engine, controlled, key)

        self.remove_all_abilities(engine, source, suffix="source")
        self.assertFalse(
            any(
                ability.builtin_semantic_key == key
                for ability in engine._activated_abilities(controlled)
            )
        )

        session = self.session(1190702)
        engine = session.engine
        source = self.add_source(
            engine,
            name="Query Grant Damage Fixture",
            ref="departure-source",
        )
        recipient = self.creature(engine, ref="departure-recipient")
        self.grant_ability(engine, recipient, key)
        engine.move_card(source.object_id, "graveyard", controller="A", log=False)
        self.assertFalse(
            any(
                ability.builtin_semantic_key == key
                for ability in engine._activated_abilities(recipient)
            )
        )
        engine.move_card(source.object_id, "battlefield", controller="A", log=False)
        self.grant_ability(engine, recipient, key)

    def test_granted_source_cost_is_atomic_revalidated_and_replays(self):
        session = self.session(1190703)
        engine = session.engine
        grant = self.add_source(
            engine,
            name="Query Grant Sacrifice Fixture",
            ref="sacrifice-grant",
        )
        recipient = self.creature(engine, ref="sacrifice-recipient")
        key = "fixture-query-grant-sacrifice:ability:granted:front:n1"
        ability = self.grant_ability(engine, recipient, key)
        self.assertTrue(ability.sacrifice_source)
        engine.state.players["A"].mana_pool["C"] = 1
        offer = self.activation_offer(session, recipient, ability)
        engine.state.players["A"].mana_pool["C"] = 0
        before = authoritative_state_hash(engine.state)
        rejected = session.act("pilot:A", {"action_id": offer["id"]})
        self.assertFalse(rejected.ok)
        self.assertIn("stale", rejected.summary.casefold())
        self.assertEqual(before, authoritative_state_hash(engine.state))
        self.assertEqual(
            "battlefield",
            engine.state.cards[recipient.object_id].zone,
        )

        engine.state.players["A"].mana_pool["C"] = 1
        life_before = engine.state.players["A"].life
        offer = self.activation_offer(session, recipient, ability)
        activated = session.act("pilot:A", {"action_id": offer["id"]})
        self.assertTrue(activated.ok, activated.summary)
        self.assertEqual(
            "outside",
            engine.state.cards[recipient.object_id].zone,
        )
        self.assertEqual(
            "battlefield",
            engine.state.cards[grant.object_id].zone,
        )
        self.assertEqual(key, engine.state.stack[-1].semantic_key)

        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        for _ in range(8):
            if not engine.state.stack:
                break
            pass_current(session)
        self.assertEqual(life_before + 2, engine.state.players["A"].life)
        expected_hash = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "query-grant-source-cost"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

        stale_session = self.session(1190704)
        stale_engine = stale_session.engine
        stale_grant = self.add_source(
            stale_engine,
            name="Query Grant Sacrifice Fixture",
            ref="stale-grant",
        )
        stale_recipient = self.creature(stale_engine, ref="stale-recipient")
        stale_ability = self.grant_ability(stale_engine, stale_recipient, key)
        stale_engine.state.players["A"].mana_pool["C"] = 1
        stale_offer = self.activation_offer(
            stale_session,
            stale_recipient,
            stale_ability,
        )
        stale_engine.move_card(
            stale_grant.object_id,
            "graveyard",
            controller="A",
            log=False,
        )
        before = authoritative_state_hash(stale_engine.state)
        rejected = stale_session.act(
            "pilot:A",
            {"action_id": stale_offer["id"]},
        )
        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(stale_engine.state))

    def test_extended_granted_costs_reach_canonical_activation_owners(self):
        discard_session = self.session(1190706)
        discard_engine = discard_session.engine
        self.add_source(
            discard_engine,
            name="Query Grant Discard Fixture",
            ref="discard-grant",
        )
        discard_recipient = self.creature(
            discard_engine,
            ref="discard-recipient",
        )
        discard_ability = self.grant_ability(
            discard_engine,
            discard_recipient,
            "fixture-query-grant-discard:ability:granted:front:n1",
        )
        self.assertEqual("discard", discard_ability.choices[0].kind)
        discarded = discard_engine.state.cards[
            discard_engine.state.players["A"].zones["hand"][0]
        ]
        hand_before = len(discard_engine.state.players["A"].zones["hand"])
        discard_engine._activate(
            "A",
            {
                "source": discard_recipient.ref,
                "ability": discard_ability.ability_id,
                "cost_cards": [discarded.ref],
            },
        )
        self.assertTrue(discard_recipient.tapped)
        self.assertEqual("graveyard", discarded.zone)
        self.resolve_top(discard_engine)
        self.assertEqual(
            hand_before,
            len(discard_engine.state.players["A"].zones["hand"]),
        )

        untap_session = self.session(1190707)
        untap_engine = untap_session.engine
        self.add_source(
            untap_engine,
            name="Query Grant Untap Fixture",
            ref="untap-grant",
        )
        untap_recipient = self.creature(untap_engine, ref="untap-recipient")
        untap_recipient.tapped = True
        untap_ability = self.grant_ability(
            untap_engine,
            untap_recipient,
            "fixture-query-grant-untap:ability:granted:front:n1",
        )
        self.assertTrue(untap_ability.untap_source)
        untap_engine.state.players["A"].mana_pool["C"] = 1
        untap_engine._activate(
            "A",
            {
                "source": untap_recipient.ref,
                "ability": untap_ability.ability_id,
                "pay": "manual",
                "payment": {"C": 1},
            },
        )
        self.assertFalse(untap_recipient.tapped)
        self.resolve_top(untap_engine)
        self.assertEqual(
            4,
            untap_engine._numeric_stat(untap_recipient.object_id, "power"),
        )

        life_session = self.session(1190708)
        life_engine = life_session.engine
        self.add_source(
            life_engine,
            name="Query Grant Life Fixture",
            ref="life-grant",
        )
        life_recipient = self.creature(
            life_engine,
            ref="life-recipient",
            seat="B",
        )
        life_ability = self.grant_ability(
            life_engine,
            life_recipient,
            "fixture-query-grant-life:ability:granted:front:n1",
        )
        self.assertEqual(2, life_ability.life_payment)
        life_engine.state.priority_player = "B"
        life_before = life_engine.state.players["B"].life
        life_engine._activate(
            "B",
            {
                "source": life_recipient.ref,
                "ability": life_ability.ability_id,
            },
        )
        self.assertEqual(life_before - 2, life_engine.state.players["B"].life)
        self.resolve_top(life_engine)
        self.assertEqual("outside", life_recipient.zone)

        mana_session = self.session(1190709)
        mana_engine = mana_session.engine
        self.add_source(
            mana_engine,
            name="Query Grant Restricted Mana Fixture",
            ref="mana-grant",
        )
        mana_recipient = self.creature(mana_engine, ref="mana-recipient")
        mana_ability = self.grant_ability(
            mana_engine,
            mana_recipient,
            "fixture-query-grant-restricted-mana:ability:granted:front:n1",
        )
        self.assertEqual(
            "nonartifact_spell_prohibited",
            mana_ability.mana_spend_restriction,
        )
        mana_engine._activate(
            "A",
            {
                "source": mana_recipient.ref,
                "ability": mana_ability.ability_id,
            },
        )
        self.assertEqual(1, mana_engine.state.players["A"].mana_pool["C"])
        self.assertEqual(
            1,
            mana_engine.state.players["A"].stats["restricted_mana"]
            ["nonartifact_spell_prohibited"]["C"],
        )

    def test_global_granted_triggers_preserve_multiplicity_apnap_and_privacy(self):
        session = self.session(1190705, players=4)
        engine = session.engine
        sources = tuple(
            self.add_source(
                engine,
                name="Query Grant Trigger Fixture",
                ref=f"global-trigger-{index}",
                seat="B" if index else "A",
            )
            for index in range(2)
        )
        recipient = self.creature(engine, ref="global-recipient", seat="A")
        engine.state.active_player = "A"
        engine.state.phase_index = 5
        engine.state.phase = "combat"
        engine.state.step = "declare_attackers"
        engine.state.combat = CombatState()
        engine.state.priority_player = "A"
        engine._issue_attackers()
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        attacked = session.act(
            "pilot:A",
            {"a": "attack", "atk": {recipient.ref: "B"}},
        )
        self.assertTrue(attacked.ok, attacked.summary)
        decision = engine.state.pending_decision
        self.assertEqual("trigger.order", decision.kind)
        self.assertEqual(["A"], decision.actors)
        pending = [
            item
            for batch in engine.state.pending_trigger_batches
            for item in batch.items
        ]
        self.assertEqual(2, len(pending))
        self.assertTrue(
            all(
                item.source_ability_id
                == "fixture-query-grant-trigger:trigger:front:n1:granted"
                for item in pending
            )
        )
        self.assertTrue(
            all(item.source_object_id == recipient.object_id for item in pending)
        )
        projected = session.packet("pilot:A", full=True)["decision"]
        self.assertEqual("trigger.order", projected["kind"])
        for seat in "BCD":
            self.assertIsNone(session.packet(f"pilot:{seat}", full=True)["decision"])
        serialized = json.dumps(projected, sort_keys=True)
        self.assertNotIn(recipient.object_id, serialized)
        self.assertTrue(
            all(source.object_id not in serialized for source in sources)
        )

        ordered = session.act(
            "pilot:A",
            {
                "action_id": "order",
                "triggers": [item.ref for item in reversed(pending)],
            },
        )
        self.assertTrue(ordered.ok, ordered.summary)
        life_before = engine.state.players["A"].life
        for _ in range(16):
            if not engine.state.stack:
                break
            pass_current(session)
        self.assertEqual(life_before + 4, engine.state.players["A"].life)
        expected_hash = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "query-grant-trigger-replay"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])


if __name__ == "__main__":
    unittest.main()
