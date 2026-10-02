from __future__ import annotations

import copy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT, keep_all, make_session
from quorune.carddb import CardDatabase, CardRecord
from quorune.compiler.hand_inspection_templates import (
    FIXED_HAND_INSPECTION_CAPABILITY,
    FIXED_HAND_INSPECTION_MECHANIC,
    fixed_hand_inspection_effect_template,
)
from quorune.deck import DeckLoader
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
    capability_dependencies_for_node,
)
from quorune.rules.hand_inspection_capability_shapes import (
    fixed_hand_inspection_node_capabilities,
)
from scripts.build_test_database import build_fixture_database


REGISTRY_PATH = ROOT / "quorune" / "rules" / "capability-registry.json"
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "fixed-hand-inspection-cards.json"


def trusted_registry(value: dict | None = None) -> CapabilityRegistry:
    registry = CapabilityRegistry(
        value or json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    )
    registry.mark_evidence_verified("0" * 64)
    return registry


def base_record() -> CardRecord:
    return CardRecord(
        oracle_id="fixture:fixed-hand-inspection",
        name="Generic Hand Inspection Fixture",
        mana_cost="{B}",
        mana_value=1.0,
        type_line="Sorcery",
        oracle_text=(
            "Target opponent reveals their hand. You choose a nonland card "
            "from it. That player discards that card."
        ),
        power=None,
        toughness=None,
        loyalty=None,
        defense=None,
        colors=("B",),
        color_identity=("B",),
        keywords=(),
        produced_mana=(),
        layout="normal",
        released_at="2026-01-01",
        legalities={"commander": "legal"},
        faces=(),
        raw={},
    )


class FixedHandInspectionCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.registry = trusted_registry()
        cls.base = base_record()

    def compile(self, text: str, *, type_line: str = "Sorcery", registry=None):
        return compile_oracle_card(
            replace(
                self.base,
                oracle_id=(
                    "fixture:hand-inspection:"
                    + hashlib.sha256(
                        f"{type_line}\0{text}".encode("utf-8")
                    ).hexdigest()[:24]
                ),
                oracle_text=text,
                type_line=type_line,
            ),
            capability_registry=registry or self.registry,
            capability_profile="commander_review",
        )

    def test_hand_inspection_compiles_across_contexts_predicates_and_fixed_tails(
        self,
    ):
        bodies = (
            "Look at target player's hand.",
            "Target opponent reveals their hand. You choose a nonland card "
            "from it. That player discards that card.",
            "Target player reveals their hand. You choose a nonland card from "
            "it with mana value 3 or less. That player discards that card.",
            "Target opponent reveals their hand. You choose a green or white "
            "creature card from it and exile that card.",
            "Look at target player's hand and choose a card from it. That "
            "player discards that card.",
            "Target player reveals their hand and discards all Trap cards.",
            "Target opponent reveals their hand. You choose a creature card "
            "from it. That player discards that card. Scry 1.",
            "Target player reveals their hand. You choose a nonland card from "
            "it. That player discards that card. You lose 2 life.",
        )
        fixtures = [
            (body, "Sorcery") for body in bodies
        ] + [
            (
                "When this creature enters, " + bodies[1],
                "Creature — Test",
            ),
            ("{2}, {T}: " + bodies[3], "Artifact Creature — Test"),
            (
                "Choose one —\n• " + bodies[1] + "\n• Draw a card.",
                "Sorcery",
            ),
        ]
        for text, type_line in fixtures:
            with self.subTest(text=text):
                ir = self.compile(text, type_line=type_line)
                self.assertEqual("exact", ir.status, ir.material_residuals)
                nodes = [node for face in ir.faces for node in face.nodes]
                self.assertTrue(
                    any(
                        FIXED_HAND_INSPECTION_MECHANIC in node.mechanics
                        and FIXED_HAND_INSPECTION_CAPABILITY
                        in node.capability_dependencies
                        for node in nodes
                    )
                )
                for node in nodes:
                    self.assertEqual(
                        node.text,
                        text[node.span.start : node.span.end],
                    )

    def test_unsupported_hand_inspection_grammar_and_shape_mutations_fail_closed(
        self,
    ):
        unsupported = (
            "Target opponent reveals their hand. You may choose a nonland card "
            "from it. If you do, that player discards that card.",
            "Target opponent reveals their hand. You choose a card from their "
            "hand or graveyard and exile it.",
            "Target opponent reveals their hand. You choose two cards from it. "
            "That player discards those cards.",
            "Target opponent discards a card at random.",
            "Choose a nonland card name. Target player reveals their hand and "
            "discards all cards with that name.",
            "Target player reveals their hand. You choose a nonland card from "
            "it [with mana value 2 or less]. That player discards that card.",
            "Target opponent reveals their hand. You choose a nonland card "
            "from it. Exile that card until this creature leaves the battlefield.",
        )
        for text in unsupported:
            with self.subTest(text=text):
                self.assertIsNone(fixed_hand_inspection_effect_template(text))
                ir = self.compile(text)
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)

        template = fixed_hand_inspection_effect_template(self.base.oracle_text)
        self.assertIsNotNone(template)
        assert template is not None
        _template_id, effects, schema, mechanics = template.compiled()
        self.assertIn(
            FIXED_HAND_INSPECTION_CAPABILITY,
            fixed_hand_inspection_node_capabilities(
                effects=effects,
                target_schema=schema,
                mechanic_ids=mechanics,
            ),
        )
        mutations = []
        for field, value in (
            ("inspection", "private_reveal"),
            ("action", "destroy"),
            ("player", "$target.0"),
            ("target", "$controller"),
        ):
            effect = copy.deepcopy(effects[0])
            effect[field] = value
            mutations.append(((effect,), schema, mechanics))
        effect = copy.deepcopy(effects[0])
        effect["predicate"]["query"]["zones"] = ["graveyard"]
        mutations.append(((effect,), schema, mechanics))
        mutations.append((effects, {**schema, "count": 2}, mechanics))
        for mutated_effects, mutated_schema, mutated_mechanics in mutations:
            with self.subTest(effects=mutated_effects):
                self.assertEqual(
                    (),
                    fixed_hand_inspection_node_capabilities(
                        effects=mutated_effects,
                        target_schema=mutated_schema,
                        mechanic_ids=mutated_mechanics,
                    ),
                )

        with patch(
            "quorune.oracle_ir.fixed_hand_inspection_effect_template",
            return_value=None,
        ):
            self.assertNotEqual("exact", self.compile(self.base.oracle_text).status)

    def test_hand_inspection_dependencies_fail_closed(self):
        for dependency_id in (
            "target.revalidate_resolution",
            "zone.change.destination_replacement",
        ):
            value = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
            dependency = next(
                row
                for row in value["capabilities"]
                if row["id"] == dependency_id
            )
            dependency["status"] = "blocked"
            dependency["blockers"] = ["focused hand-inspection dependency"]
            registry = trusted_registry(value)
            ir = self.compile(self.base.oracle_text, registry=registry)
            self.assertNotEqual("exact", ir.status)
            self.assertTrue(ir.material_residuals)


class FixedHandInspectionRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory()
        database = Path(cls.temporary.name) / "fixed-hand-inspection.sqlite3"
        build_fixture_database(
            [
                ROOT / "tests" / "fixtures" / "scryfall-exact-lists.json",
                ROOT / "tests" / "fixtures" / "damage-replacement-cards.json",
                FIXTURE_PATH,
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

    @classmethod
    def tearDownClass(cls) -> None:
        cls.db.close()
        cls.temporary.cleanup()

    def session(self, seed: int, card_name: str):
        mishra = copy.deepcopy(self.mishra)
        next(
            entry for entry in mishra.entries if entry.board == "mainboard"
        ).name = card_name
        session = make_session(
            self.db,
            mishra,
            copy.deepcopy(self.zimone),
            players=4,
            seed=seed,
            auto_pass_empty=False,
        )
        keep_all(session)
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine.state.priority_passes = []
        session.commands.clear()
        session.decisions.clear()
        return session

    @staticmethod
    def named_card(engine, name: str):
        return next(
            card
            for card in engine.state.cards.values()
            if card.owner == "A"
            and card.printed_name == name
            and card.zone != "command"
        )

    def promote(self, engine, *names: str) -> None:
        records = tuple(self.db.lookup(name) for name in names)
        for record in records:
            for program in engine.semantics.programs_for_oracle(record.oracle_id):
                engine.semantics.remove(program.key)
        register_generated_programs(
            self.db,
            engine.semantics,
            records,
            trust_level="trusted",
            capability_registry=trusted_registry(),
            capability_profile=engine.state.config.review_profile,
            promote_exact_runtime_handlers=True,
            promote_exact_trigger_programs=True,
            promote_exact_effect_programs=True,
            promote_exact_capability_declarations=True,
        )

    def ready_spell(self, session, name: str, mana: dict[str, int]):
        engine = session.engine
        source = self.named_card(engine, name)
        engine.move_card(source.object_id, "hand", log=False)
        engine.state.players["A"].mana_pool.update(mana)
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.priority_player = "A"
        hints = engine._priority_action_hints("A")
        action = next(
            row for row in hints["actions"] if row.get("card") == source.ref
        )
        engine._issue_priority("A", hints)
        return source, action

    def hand_pair(self, engine, seat: str):
        owned = [
            card
            for card in engine.state.cards.values()
            if card.owner == seat and card.zone not in {"command", "stack"}
        ]
        land = next(
            card
            for card in owned
            if "land"
            in engine._type_parts(
                str(engine._effective_card_data(card).get("type_line") or "")
            )[0]
        )
        nonland = next(
            card
            for card in owned
            if card is not land
            and "land"
            not in engine._type_parts(
                str(engine._effective_card_data(card).get("type_line") or "")
            )[0]
        )
        for card in (land, nonland):
            if card.zone != "hand":
                engine.move_card(card.object_id, "hand", log=False)
        return land, nonland

    def resolve_until_nonpriority(self, session):
        for _ in range(64):
            decision = session.state.pending_decision
            if decision is not None and decision.kind != "priority":
                return decision
            if not session.state.stack:
                return None
            principal = session.pending_principals()[0]
            result = session.act(principal, {"action_id": "pass"})
            self.assertTrue(result.ok, result.summary)
        self.fail("Hand-inspection resolution did not stabilize")

    def resolve_all(self, session, *, apply_replacement: bool = True):
        for _ in range(96):
            decision = session.state.pending_decision
            if decision is not None and decision.kind == "replacement.order":
                principal = session.pending_principals()[0]
                projected = StateProjector(self.db, session.state)._decision(
                    principal
                )
                self.assertIsNotNone(projected)
                assert projected is not None
                selected = next(
                    option
                    for option in projected["ctx"]["options"]
                    if bool(option.get("decline")) is (not apply_replacement)
                )
                accepted = session.act(
                    principal,
                    {
                        "action_id": "choose",
                        "replacement": selected["id"],
                    },
                )
                self.assertTrue(accepted.ok, accepted.summary)
                continue
            if decision is not None and decision.kind != "priority":
                return decision
            if not session.state.stack:
                return None
            principal = session.pending_principals()[0]
            accepted = session.act(principal, {"action_id": "pass"})
            self.assertTrue(accepted.ok, accepted.summary)
        self.fail("Hand-inspection resolution did not complete")

    def assert_replays(self, session, label: str) -> None:
        expected = authoritative_state_hash(session.state)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / label
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected, replay["final_state_hash"])

    def test_trusted_cast_reveals_selects_and_discards_through_public_action(self):
        session = self.session(402301, "Generic Hand Discard Fixture")
        engine = session.engine
        self.promote(engine, "Generic Hand Discard Fixture")
        land, selected = self.hand_pair(engine, "B")
        source, action = self.ready_spell(
            session, "Generic Hand Discard Fixture", {"B": 1}
        )
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        cast = session.act(
            "pilot:A",
            {
                "action_id": action["id"],
                "targets": ["B"],
                "pay": "manual",
                "payment": {"B": 1},
            },
        )
        self.assertTrue(cast.ok, cast.summary)
        decision = self.resolve_until_nonpriority(session)
        self.assertIsNotNone(decision)
        assert decision is not None
        self.assertEqual("semantic.choice", decision.kind)
        payload = decision.payload_by_actor["A"]
        schema = payload["legal_actions"][0]["choice_schema"]
        self.assertIn(selected.ref, schema["legal_refs"])
        self.assertNotIn(land.ref, schema["legal_refs"])
        for seat in "ABCD":
            rendered = json.dumps(
                StateProjector(self.db, engine.state)._snapshot(f"pilot:{seat}"),
                sort_keys=True,
            )
            self.assertIn(selected.ref, rendered)
            self.assertIn(land.ref, rendered)
        chosen = session.act(
            "pilot:A",
            {"action_id": "choose", "card": selected.ref},
        )
        self.assertTrue(chosen.ok, chosen.summary)
        self.resolve_all(session)
        self.assertEqual("graveyard", selected.zone)
        self.assertEqual("hand", land.zone)
        self.assertEqual("graveyard", source.zone)
        self.assert_replays(session, "trusted-hand-discard")

    def test_public_reveal_private_look_projection_and_exact_replay(self):
        session = self.session(402302, "Generic Hand Look Fixture")
        engine = session.engine
        self.promote(engine, "Generic Hand Look Fixture")
        land, nonland = self.hand_pair(engine, "B")
        source, action = self.ready_spell(
            session, "Generic Hand Look Fixture", {"U": 1}
        )
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        cast = session.act(
            "pilot:A",
            {
                "action_id": action["id"],
                "targets": ["B"],
                "pay": "manual",
                "payment": {"U": 1},
            },
        )
        self.assertTrue(cast.ok, cast.summary)
        self.assertIsNone(self.resolve_all(session))
        self.assertEqual("graveyard", source.zone)
        authorized = json.dumps(
            StateProjector(self.db, engine.state)._snapshot("pilot:A"),
            sort_keys=True,
        )
        unauthorized = json.dumps(
            StateProjector(self.db, engine.state)._snapshot("pilot:C"),
            sort_keys=True,
        )
        for card in (land, nonland):
            self.assertIn(card.ref, authorized)
            self.assertNotIn(card.ref, unauthorized)
        self.assert_replays(session, "private-target-hand-look")

    def test_illegal_target_and_stale_selection_reject_without_hidden_leak_or_mutation(
        self,
    ):
        illegal = self.session(402303, "Generic Hand Discard Fixture")
        illegal_engine = illegal.engine
        self.promote(illegal_engine, "Generic Hand Discard Fixture")
        _land, hidden = self.hand_pair(illegal_engine, "B")
        source, action = self.ready_spell(
            illegal, "Generic Hand Discard Fixture", {"B": 1}
        )
        cast = illegal.act(
            "pilot:A",
            {
                "action_id": action["id"],
                "targets": ["B"],
                "pay": "manual",
                "payment": {"B": 1},
            },
        )
        self.assertTrue(cast.ok, cast.summary)
        illegal_engine.state.players["B"].stats[
            "hexproof_from_colors_until_end"
        ] = ["B"]
        self.assertIsNone(self.resolve_all(illegal))
        self.assertEqual("graveyard", source.zone)
        for seat in "ACD":
            rendered = json.dumps(
                StateProjector(self.db, illegal_engine.state)._snapshot(
                    f"pilot:{seat}"
                ),
                sort_keys=True,
            )
            self.assertNotIn(hidden.ref, rendered)

        stale = self.session(402304, "Generic Hand Discard Fixture")
        stale_engine = stale.engine
        self.promote(stale_engine, "Generic Hand Discard Fixture")
        _land, candidate = self.hand_pair(stale_engine, "B")
        _source, action = self.ready_spell(
            stale, "Generic Hand Discard Fixture", {"B": 1}
        )
        cast = stale.act(
            "pilot:A",
            {
                "action_id": action["id"],
                "targets": ["B"],
                "pay": "manual",
                "payment": {"B": 1},
            },
        )
        self.assertTrue(cast.ok, cast.summary)
        self.resolve_until_nonpriority(stale)
        stale_engine.move_card(candidate.object_id, "graveyard", log=False)
        before = authoritative_state_hash(stale_engine.state)
        rejected = stale.act(
            "pilot:A",
            {"action_id": "choose", "card": candidate.ref},
        )
        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(stale_engine.state))

        malformed = self.session(4023041, "Generic Hand Discard Fixture")
        malformed_engine = malformed.engine
        self.promote(malformed_engine, "Generic Hand Discard Fixture")
        _land, candidate = self.hand_pair(malformed_engine, "B")
        _source, action = self.ready_spell(
            malformed, "Generic Hand Discard Fixture", {"B": 1}
        )
        cast = malformed.act(
            "pilot:A",
            {
                "action_id": action["id"],
                "targets": ["B"],
                "pay": "manual",
                "payment": {"B": 1},
            },
        )
        self.assertTrue(cast.ok, cast.summary)
        decision = self.resolve_until_nonpriority(malformed)
        self.assertIsNotNone(decision)
        assert decision is not None
        decision.continuation["effect"]["inspection"] = "private_reveal"
        before = authoritative_state_hash(malformed_engine.state)
        rejected = malformed.act(
            "pilot:A",
            {"action_id": "choose", "card": candidate.ref},
        )
        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(malformed_engine.state))

    def test_discard_and_exile_use_current_characteristics_replacement_and_resume_once(
        self,
    ):
        session = self.session(402305, "Generic Hand Discard Fixture")
        engine = session.engine
        self.promote(
            engine,
            "Generic Hand Discard Fixture",
            "Dauthi Voidwalker",
        )
        _land, selected = self.hand_pair(engine, "B")
        dauthi_record = self.db.lookup("Dauthi Voidwalker")
        dauthi = CardInstance(
            object_id="fixture:hand-inspection-dauthi",
            ref="hand-inspection-dauthi",
            oracle_id=dauthi_record.oracle_id,
            printed_name=dauthi_record.name,
            owner="A",
            controller="A",
            zone="battlefield",
            known_to=list(engine.seats),
            revealed_to=list(engine.seats),
        )
        engine.state.cards[dauthi.object_id] = dauthi
        engine.state.players["A"].zones["battlefield"].append(dauthi.object_id)
        _source, action = self.ready_spell(
            session, "Generic Hand Discard Fixture", {"B": 1}
        )
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        cast = session.act(
            "pilot:A",
            {
                "action_id": action["id"],
                "targets": ["B"],
                "pay": "manual",
                "payment": {"B": 1},
            },
        )
        self.assertTrue(cast.ok, cast.summary)
        self.resolve_until_nonpriority(session)
        chosen = session.act(
            "pilot:A",
            {"action_id": "choose", "card": selected.ref},
        )
        self.assertTrue(chosen.ok, chosen.summary)
        self.assertIsNone(self.resolve_all(session, apply_replacement=True))
        self.assertEqual("exile", selected.zone)
        self.assertEqual(1, selected.counters.get("void"))
        self.assert_replays(session, "hand-discard-replacement")

        current = self.session(402306, "Generic Hand Exile Fixture")
        current_engine = current.engine
        self.promote(current_engine, "Generic Hand Exile Fixture")
        _land, candidate = self.hand_pair(current_engine, "B")
        candidate.annotations["copy_overrides"] = {
            "type_line": "Land",
            "colors": [],
        }
        _source, action = self.ready_spell(
            current, "Generic Hand Exile Fixture", {"B": 1, "C": 1}
        )
        cast = current.act(
            "pilot:A",
            {
                "action_id": action["id"],
                "targets": ["B"],
                "pay": "manual",
                "payment": {"B": 1, "C": 1},
            },
        )
        self.assertTrue(cast.ok, cast.summary)
        decision = self.resolve_until_nonpriority(current)
        self.assertIsNotNone(decision)
        assert decision is not None
        legal = decision.payload_by_actor["A"]["legal_actions"][0][
            "choice_schema"
        ]["legal_refs"]
        self.assertNotIn(candidate.ref, legal)

    def test_bulk_discard_preserves_one_committed_batch(self):
        session = self.session(402307, "Generic Hand Bulk Fixture")
        engine = session.engine
        self.promote(engine, "Generic Hand Bulk Fixture")
        land, nonland = self.hand_pair(engine, "B")
        _source, action = self.ready_spell(
            session, "Generic Hand Bulk Fixture", {"B": 1, "C": 2}
        )
        cast = session.act(
            "pilot:A",
            {
                "action_id": action["id"],
                "targets": ["B"],
                "pay": "manual",
                "payment": {"B": 1, "C": 2},
            },
        )
        self.assertTrue(cast.ok, cast.summary)
        self.assertIsNone(self.resolve_all(session))
        self.assertEqual("hand", land.zone)
        self.assertEqual("graveyard", nonland.zone)
        self.assertFalse(
            any(
                card.zone == "hand"
                and "land"
                not in engine._type_parts(
                    str(engine._effective_card_data(card).get("type_line") or "")
                )[0]
                for card in engine.state.cards.values()
                if card.owner == "B"
            )
        )


class NonlandPermanentHandActionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from quorune.deck import DeckDefinition, DeckEntry
        cls.temporary=tempfile.TemporaryDirectory()
        path=Path(cls.temporary.name)/'nonland-hand.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/nonland-permanent-hand-cards.json'],path)
        cls.db=CardDatabase(path)
        cls.deck=DeckDefinition('Generic hand review deck',[DeckEntry('Generic Hand Review Commander',1,'commander'),DeckEntry('Generic Hand Review Swamp',15)],['Generic Hand Review Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close();cls.temporary.cleanup()

    def session(self,seed):
        from quorune.model import GameConfig
        from quorune.session import CommanderSession
        from quorune.rules.capabilities import load_default_capability_registry
        session=CommanderSession.create(self.db,{seat:copy.deepcopy(self.deck)for seat in 'ABCD'},first_player='A',seed=seed,config=GameConfig(seed=seed,auto_pass_empty_priority=False))
        keep_all(session);engine=session.engine
        engine.permissions.invalidate_current();engine.state.pending_decision=None;engine.state.priority_player=None;engine.state.priority_passes=[]
        register_generated_programs(self.db,engine.semantics,tuple(self.db.iter_cards()),trust_level='trusted',capability_registry=load_default_capability_registry(),capability_profile='commander_review',promote_exact_runtime_handlers=True,promote_exact_trigger_programs=True,promote_exact_effect_programs=True,promote_exact_capability_declarations=True)
        return session

    def add(self,engine,name,seat,ref):
        row=self.db.lookup(name)
        card=CardInstance(object_id='nonland-hand:'+ref,ref=ref,oracle_id=row.oracle_id,printed_name=row.name,owner=seat,controller=seat,zone='hand',zone_timestamp=engine._next_zone_timestamp(),known_to=[seat])
        engine.state.cards[card.object_id]=card;engine.state.players[seat].zones['hand'].append(card.object_id)
        return card

    def cast(self,session,spell):
        engine=session.engine
        programs=engine.semantics.programs_for_oracle(spell.oracle_id)
        self.assertTrue(programs);self.assertTrue(all(engine.semantic_program_is_current_trusted(p)for p in programs))
        engine.state.active_player='A';engine.state.started=True;engine.state.phase='precombat_main';engine.state.step='main';engine.state.players['A'].mana_pool['B']=1
        engine._grant_priority('A');engine.pump()
        action=next(row for row in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']if row['id']==f'cast:{spell.ref}')
        session.initial_checkpoint=checkpoint_envelope(engine.state);session.commands.clear();session.decisions.clear()
        result=session.act('pilot:A',{'action_id':action['id'],'targets':['B'],'pay':'auto'})
        self.assertTrue(result.ok,result.summary)
        for _ in range(8):
            if engine.state.pending_decision and engine.state.pending_decision.kind=='semantic.choice':return
            if not engine.state.stack:return
            result=session.act(session.pending_principals()[0],{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
        self.fail('Hand inspection did not resolve')

    def replay(self,session):
        from quorune.session import CommanderSession
        expected=authoritative_state_hash(session.state)
        with tempfile.TemporaryDirectory()as directory:
            path=Path(directory)/'nonland-hand-record';session.save(path)
            self.assertEqual(expected,authoritative_state_hash(CommanderSession.load(self.db,path).state))
            result=replay_record(path,self.db,verify=True)
        self.assertTrue(result['ok'],result);self.assertEqual(expected,result['final_state_hash'])

    def test_trusted_nonland_permanent_selection_excludes_all_land_combinations(self):
        session=self.session(240442001);engine=session.engine
        spell=self.add(engine,'Generic Nonland Hand Review Spell','A','HAND-REVIEW-SPELL')
        artifact=self.add(engine,'Generic Hand Review Artifact','B','NONLAND-ARTIFACT')
        artifact_land=self.add(engine,'Generic Hand Review Artifact Land','B','ARTIFACT-LAND')
        enchantment_land=self.add(engine,'Generic Hand Review Enchantment Land','B','ENCHANTMENT-LAND')
        land_creature=self.add(engine,'Generic Hand Review Land Creature','B','LAND-CREATURE')
        instant=self.add(engine,'Generic Hand Review Instant','B','INSTANT')
        self.cast(session,spell)
        refs=session.packet('pilot:A',full=True)['decision']['legal_actions'][0]['choice_schema']['legal_refs']
        self.assertIn(artifact.ref,refs);self.assertNotIn(instant.ref,refs)
        self.assertNotIn(artifact_land.ref,refs)
        self.assertNotIn(enchantment_land.ref,refs)
        self.assertNotIn(land_creature.ref,refs)
        before=authoritative_state_hash(engine.state)
        invalid=session.act('pilot:A',{'action_id':'choose','card':artifact_land.ref})
        self.assertFalse(invalid.ok);self.assertEqual(before,authoritative_state_hash(engine.state))
        result=session.act('pilot:A',{'action_id':'choose','card':artifact.ref})
        self.assertTrue(result.ok,result.summary)
        self.assertEqual('graveyard',engine.state.cards[artifact.object_id].zone)
        self.assertEqual('hand',engine.state.cards[artifact_land.object_id].zone)
        self.replay(session)

    def test_hand_predicate_compiler_preserves_positive_and_excluded_types(self):
        from quorune.rules.capabilities import load_default_capability_registry
        registry=load_default_capability_registry()
        for name in ('Generic Nonland Hand Review Spell','Generic Nonland Hand Look Spell','Generic Nonland Hand Exile Spell'):
            with self.subTest(name=name):
                ir=compile_oracle_card(self.db.lookup(name),capability_registry=registry,capability_profile='commander_review')
                self.assertEqual('exact',ir.status,ir.to_dict())
                query=ir.faces[0].nodes[0].effects[0]['predicate']['query']
                self.assertEqual(['artifact','battle','creature','enchantment','planeswalker'],query['types_any'])
                self.assertEqual(['land'],query['excluded_types'])
        control=compile_oracle_card(self.db.lookup('Generic Artifact Hand Selection Spell'),capability_registry=registry,capability_profile='commander_review')
        query=control.faces[0].nodes[0].effects[0]['predicate']['query']
        self.assertEqual(['artifact'],query['types_all']);self.assertEqual([],query['excluded_types'])

    def test_nonland_hand_exclusion_mutant_is_killed(self):
        from quorune.compiler import hand_inspection_templates as owner
        original=owner._quality_predicate
        def omit_exclusion(text):
            spec=original(text)
            if text.casefold()=='a nonland permanent card':
                return replace(spec,query=replace(spec.query,excluded_types=()))
            return spec
        with patch.object(owner,'_quality_predicate',omit_exclusion):
            with self.assertRaises(AssertionError):
                template=owner.fixed_hand_inspection_effect_template(self.db.lookup('Generic Nonland Hand Review Spell').oracle_text)
                self.assertEqual(('land',),template.predicate.query.excluded_types)

    def test_actual_v237_hand_record_rejects_silent_current_reinterpretation(self):
        path=ROOT/'tests/fixtures/records/nonland-hand-v237-2756dd2b'
        provenance=json.loads((path/'provenance.json').read_text(encoding='utf-8'))
        self.assertEqual('oracle-ir-v237',provenance['compiler_version'])
        self.assertEqual('explicit_runtime_trust_incompatibility',provenance['current_runtime_disposition'])
        programs=json.loads((path/'semantics.json').read_text(encoding='utf-8'))['programs'].values()
        program=next(row for row in programs if row['oracle_id']=='00000000-0000-4000-8000-000000000903')
        self.assertEqual([],program['effects'][0]['predicate']['query']['excluded_types'])
        self.assertEqual(6,len((path/'commands.jsonl').read_text(encoding='utf-8').splitlines()))
        with self.assertRaisesRegex(ValueError,'Runtime trust provenance mismatch in record manifest'):
            replay_record(path,self.db,verify=True)

    def test_private_look_public_exile_and_artifact_control_retain_real_action_semantics(self):
        cases=(('Generic Nonland Hand Look Spell','graveyard',False,False),('Generic Nonland Hand Exile Spell','exile',True,False),('Generic Artifact Hand Selection Spell','graveyard',True,True))
        for index,(name,destination,public,artifact_control)in enumerate(cases):
            with self.subTest(name=name):
                session=self.session(240442010+index);engine=session.engine
                spell=self.add(engine,name,'A','INSPECT-SPELL')
                artifact=self.add(engine,'Generic Hand Review Artifact','B','SELECTED-ARTIFACT')
                artifact_land=self.add(engine,'Generic Hand Review Artifact Land','B','CONTROL-ARTIFACT-LAND')
                self.cast(session,spell)
                projected=session.packet('pilot:A',full=True)['decision']
                refs=projected['legal_actions'][0]['choice_schema']['legal_refs']
                self.assertIn(artifact.ref,refs)
                self.assertEqual(artifact_control,artifact_land.ref in refs)
                outsider=json.dumps(StateProjector(self.db,engine.state)._snapshot('pilot:C'),sort_keys=True)
                self.assertEqual(public,artifact.ref in outsider)
                selected=artifact_land if artifact_control else artifact
                result=session.act('pilot:A',{'action_id':'choose','card':selected.ref})
                self.assertTrue(result.ok,result.summary)
                self.assertEqual(destination,engine.state.cards[selected.object_id].zone)
                self.replay(session)

    def test_nonland_permanent_empty_domain_reveals_without_discarding_land(self):
        session=self.session(240442020);engine=session.engine
        spell=self.add(engine,'Generic Nonland Hand Review Spell','A','EMPTY-DOMAIN-SPELL')
        artifact_land=self.add(engine,'Generic Hand Review Artifact Land','B','EMPTY-ARTIFACT-LAND')
        self.cast(session,spell)
        self.assertNotEqual('semantic.choice',engine.state.pending_decision.kind if engine.state.pending_decision else None)
        self.assertEqual('hand',engine.state.cards[artifact_land.object_id].zone)
        self.assertEqual('graveyard',engine.state.cards[spell.object_id].zone)
        self.replay(session)


if __name__ == "__main__":
    unittest.main()
