from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT, keep_all, make_session
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.compiler.program_generation import register_generated_programs
from quorune.compiler.ability_keyword_fragment_model import (
    AbilityKeywordFragmentLowering,
)
from quorune.deck import DeckDefinition, DeckEntry, DeckLoader
from quorune.model import CardInstance
from quorune.oracle_ir import compile_oracle_card, generated_programs
from quorune.projection import StateProjector
from quorune.record import authoritative_state_hash, checkpoint_envelope, replay_record
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from scripts.build_test_database import build_fixture_database


REGISTRY_PATH = ROOT / "quorune" / "rules" / "capability-registry.json"


def focused_card_database(directory: str) -> CardDatabase:
    database = Path(directory) / "cascade.sqlite3"
    build_fixture_database(
        [
            ROOT / "tests" / "fixtures" / "scryfall-exact-lists.json",
            ROOT / "tests" / "fixtures" / "cascade-cards.json",
        ],
        database,
    )
    return CardDatabase(database)


class CascadeCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.db = focused_card_database(cls.temporary.name)
        cls.record = cls.db.lookup("Maelstrom Colossus")
        cls.capabilities = load_default_capability_registry()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def test_cascade_keyword_lowers_each_instance_with_precise_spans(self):
        record = self.db.lookup("Apex Devastator")
        ir = compile_oracle_card(
            record,
            capability_registry=self.capabilities,
            capability_profile="commander_review",
        )
        nodes = [
            node
            for node in ir.faces[0].nodes
            if node.template_id == "cascade-stack-cast-trigger-v1"
        ]

        self.assertEqual("exact", ir.status)
        self.assertEqual(4, len(nodes))
        self.assertEqual(4, len({node.node_id for node in nodes}))
        self.assertEqual(4, len({(node.span.start, node.span.end) for node in nodes}))
        for node in nodes:
            self.assertEqual("triggered_ability", node.kind)
            self.assertEqual("stack", node.active_zone)
            self.assertEqual("spell.cast", node.event)
            self.assertEqual(("typed_cascade_resolution",), node.runtime_coverage)
            self.assertEqual(("trigger.keyword.cascade",), node.capability_dependencies)
            self.assertEqual(
                "cascade",
                record.oracle_text[node.span.start : node.span.end].casefold(),
            )
            self.assertEqual("ability.trigger.cascade.v1", node.handlers[0]["handler_id"])

        programs = [
            program
            for program in generated_programs(
                self.db,
                record,
                trust_level="trusted",
                capability_registry=self.capabilities,
                capability_profile="commander_review",
            )
            if program.provenance.get("template_id")
            == "cascade-stack-cast-trigger-v1"
        ]
        self.assertEqual(4, len(programs))
        self.assertEqual(4, len({program.key for program in programs}))
        self.assertTrue(all(program.capability_closure["trusted"] for program in programs))

    def test_unsupported_cascade_wording_remains_material_residual(self):
        for text in ("Cascade 2", "Cascade — if you cast this from your hand"):
            with self.subTest(text=text):
                ir = compile_oracle_card(
                    replace(
                        self.record,
                        oracle_text=text,
                        keywords=("Cascade",),
                    ),
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)
                self.assertTrue(
                    any(
                        "cascade-unsupported-wording" in blocker
                        for residual in ir.material_residuals
                        for blocker in residual.blockers
                    )
                )

    def test_cascade_dependency_and_compiler_mutations_fail_closed(self):
        for dependency_id in (
            "casting.permission.one_shot_exile_without_mana",
            "trigger.event.normalized_spell_cast",
            "trigger.placement.apnap",
        ):
            with self.subTest(dependency=dependency_id):
                value = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
                dependency = next(
                    row for row in value["capabilities"] if row["id"] == dependency_id
                )
                dependency["status"] = "blocked"
                dependency["blockers"] = ["test mutation"]
                ir = compile_oracle_card(
                    self.record,
                    capability_registry=CapabilityRegistry(value),
                    capability_profile="commander_review",
                )
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(
                    any(
                        dependency_id in blocker
                        for residual in ir.material_residuals
                        for blocker in residual.blockers
                    )
                )

        with patch(
            "quorune.compiler.cascade_nodes.lower_ability_keyword_fragments",
            return_value=AbilityKeywordFragmentLowering(),
        ):
            ir = compile_oracle_card(
                self.record,
                capability_registry=self.capabilities,
                capability_profile="commander_review",
            )
        self.assertNotEqual("exact", ir.status)
        cascade_nodes = [
            node
            for node in ir.faces[0].nodes
            if node.template_id == "cascade-stack-cast-trigger-v1"
        ]
        self.assertEqual(1, len(cascade_nodes))
        self.assertFalse(cascade_nodes[0].exact)
        self.assertFalse(cascade_nodes[0].handlers)
        self.assertTrue(
            any(
                "ability.trigger.cascade.v1" in blocker
                for residual in ir.material_residuals
                for blocker in residual.blockers
            )
        )


class CascadeRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.db = focused_card_database(cls.temporary.name)
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

    def session(self, seed: int, *, players: int = 4):
        session = make_session(
            self.db,
            self.mishra,
            self.zimone,
            players=players,
            seed=seed,
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

    def add_card(self, engine, *, seat: str, name: str, ref: str, zone: str):
        record = self.db.lookup(name)
        card = CardInstance(
            object_id=f"fixture:{ref}",
            ref=ref,
            oracle_id=record.oracle_id,
            printed_name=record.name,
            owner=seat,
            controller=seat,
            zone=zone,
            zone_timestamp=engine.state.event_sequence + 1,
            known_to=list(engine.seats) if zone in {"battlefield", "exile"} else [seat],
            revealed_to=list(engine.seats) if zone in {"battlefield", "exile"} else [],
        )
        engine.state.cards[card.object_id] = card
        engine.state.players[seat].zones[zone].append(card.object_id)
        return card

    def register(self, engine, *names: str) -> None:
        register_generated_programs(
            self.db,
            engine.semantics,
            tuple(self.db.lookup(name) for name in names),
            trust_level="provisional",
            capability_registry=self.capabilities,
            capability_profile=engine.state.config.review_profile,
            promote_exact_runtime_handlers=True,
            promote_exact_capability_declarations=True,
            promote_exact_effect_programs=True,
        )

    @staticmethod
    def deck_card(engine, seat: str, name: str) -> CardInstance:
        return next(
            card
            for card in engine.state.cards.values()
            if card.owner == seat and card.printed_name == name
        )

    def arrange_library(self, engine, seat: str, names: tuple[str, ...]):
        cards = tuple(self.deck_card(engine, seat, name) for name in names)
        for card in reversed(cards):
            engine.move_card(card.object_id, "library", position="top", log=False)
        return cards

    def cast_cascade_source(self, engine, name: str, *, ref: str):
        self.register(engine, name)
        source = self.add_card(engine, seat="A", name=name, ref=ref, zone="hand")
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.priority_player = "A"
        engine.state.players["A"].mana_pool["C"] = 20
        engine.state.players["A"].mana_pool["G"] = 20
        engine._cast("A", {"card": source.ref, "pay": "auto"})
        return source

    @staticmethod
    def begin_top_resolution(engine):
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine._prepare_stack_resolution()

    def prepare_cast_choice(self, session, *, candidate_name: str = "Sol Ring"):
        engine = session.engine
        candidate = self.deck_card(engine, "A", candidate_name)
        portal = self.deck_card(engine, "A", "Portal to Phyrexia")
        island = self.deck_card(engine, "A", "Island")
        self.arrange_library(
            engine,
            "A",
            (island.printed_name, portal.printed_name, candidate.printed_name),
        )
        source = self.cast_cascade_source(
            engine,
            "Maelstrom Colossus",
            ref=f"cascade-source-{engine.state.config.seed}",
        )
        trigger = engine.state.stack[-1]
        self.assertEqual("builtin:cascade", trigger.semantic_key)
        self.begin_top_resolution(engine)
        self.assertEqual("selection.exile_cast", engine.state.pending_decision.kind)
        return source, trigger, candidate, portal, island

    def test_cascade_exiles_casts_and_random_bottoms(self):
        session = self.session(7028501)
        engine = session.engine
        source, trigger, candidate, portal, island = self.prepare_cast_choice(session)
        mana_before = dict(engine.state.players["A"].mana_pool)

        result = session.act("pilot:A", {"action_id": "cast"})

        self.assertTrue(result.ok, result.summary)
        self.assertEqual("stack", source.zone)
        self.assertEqual("stack", candidate.zone)
        self.assertFalse(any(item.ref == trigger.ref for item in engine.state.stack))
        cast_item = next(
            item for item in engine.state.stack if item.card_object_id == candidate.object_id
        )
        self.assertEqual("without_mana_cost", cast_item.context["cost_option"])
        self.assertEqual(mana_before, dict(engine.state.players["A"].mana_pool))
        library = engine.state.players["A"].zones["library"]
        self.assertEqual({portal.object_id, island.object_id}, set(library[:2]))
        self.assertEqual("library", portal.zone)
        self.assertEqual("library", island.zone)
        resolved = next(event for event in reversed(engine.state.events) if event.code == "cascade.resolve")
        self.assertEqual("cast", resolved.details["outcome"])
        self.assertEqual(2, resolved.details["bottom_count"])

    def test_modal_front_remains_eligible_when_back_face_is_a_land(self):
        session = self.session(7028508)
        engine = session.engine
        name = "Cascade Creature // Cascade Refuge"
        self.register(engine, name)
        candidate = self.add_card(
            engine,
            seat="A",
            name=name,
            ref="cascade-modal-candidate",
            zone="library",
        )
        engine.move_card(
            candidate.object_id,
            "library",
            position="top",
            log=False,
        )
        self.cast_cascade_source(
            engine,
            "Maelstrom Colossus",
            ref="modal-cascade-source",
        )

        self.begin_top_resolution(engine)

        self.assertEqual(
            "selection.exile_cast",
            engine.state.pending_decision.kind,
        )
        options = engine.state.pending_decision.payload_by_actor["A"][
            "cast_options"
        ]
        self.assertEqual(
            {"Cascade Creature"},
            {option.get("face") for option in options},
        )
        result = session.act("pilot:A", {"action_id": "decline"})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("library", candidate.zone)

    def test_multiple_cascade_instances_trigger_separately(self):
        session = self.session(7028502)
        engine = session.engine
        self.cast_cascade_source(engine, "Apex Devastator", ref="apex-cascade-source")

        self.assertEqual("trigger.order", engine.state.pending_decision.kind)
        batch = engine.state.pending_trigger_batches[0]
        self.assertEqual(4, len(batch.items))
        self.assertEqual(
            {"builtin:cascade"},
            {item.source_ability_id for item in batch.items},
        )
        refs = [item.ref for item in batch.items]
        result = session.act("pilot:A", {"action_id": "order", "triggers": refs})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(4, sum(item.semantic_key == "builtin:cascade" for item in engine.state.stack))

    def test_empty_or_ineligible_library_bottoms_without_choice(self):
        session = self.session(7028503)
        engine = session.engine
        portal, island = self.arrange_library(
            engine,
            "A",
            ("Portal to Phyrexia", "Island"),
        )
        keep = {portal.object_id, island.object_id}
        for object_id in list(engine.state.players["A"].zones["library"]):
            if object_id not in keep:
                engine.move_card(object_id, "outside", log=False)
        self.cast_cascade_source(engine, "Maelstrom Colossus", ref="empty-cascade-source")

        self.begin_top_resolution(engine)

        self.assertIsNone(engine.state.pending_decision)
        self.assertFalse(any(item.semantic_key == "builtin:cascade" for item in engine.state.stack))
        self.assertEqual({portal.object_id, island.object_id}, set(engine.state.players["A"].zones["library"][:2]))
        resolved = next(event for event in reversed(engine.state.events) if event.code == "cascade.resolve")
        self.assertEqual("no_candidate", resolved.details["outcome"])

    def test_cascade_cast_revalidates_additional_cost_and_targets(self):
        session = self.session(7028504)
        engine = session.engine
        self.register(engine, "Cascade Trial")
        candidate = self.add_card(
            engine,
            seat="A",
            name="Cascade Trial",
            ref="cascade-trial",
            zone="library",
        )
        engine.move_card(candidate.object_id, "library", position="top", log=False)
        artifact_ref = engine.create_token(
            "A",
            name="Cascade Cost Artifact",
            characteristics={"type_line": "Token Artifact"},
        )[0]
        target_ref = engine.create_token(
            "B",
            name="Cascade Target Creature",
            characteristics={
                "type_line": "Token Creature — Test",
                "power": "2",
                "toughness": "2",
            },
        )[0]
        artifact = next(card for card in engine.state.cards.values() if card.ref == artifact_ref)
        target = next(card for card in engine.state.cards.values() if card.ref == target_ref)
        self.cast_cascade_source(engine, "Maelstrom Colossus", ref="cost-cascade-source")
        self.begin_top_resolution(engine)
        option = engine.state.pending_decision.payload_by_actor["A"]["cast_options"][0]
        self.assertIn("sacrifice_cards", option["choice_schema"])
        self.assertIn(target.ref, option["target_schema"]["legal_refs"])

        result = session.act(
            "pilot:A",
            {
                "action_id": "cast",
                "targets": [target.ref],
                "sacrifice_cards": [artifact.ref],
            },
        )

        self.assertTrue(result.ok, result.summary)
        self.assertNotEqual("battlefield", artifact.zone)
        cast_item = next(item for item in engine.state.stack if item.card_object_id == candidate.object_id)
        self.assertEqual([target.ref], cast_item.targets)
        self.begin_top_resolution(engine)
        self.assertNotEqual("battlefield", target.zone)

    def test_stale_or_malformed_cascade_choice_rolls_back(self):
        session = self.session(7028505)
        engine = session.engine
        _source, _trigger, candidate, _portal, _island = self.prepare_cast_choice(session)
        engine.move_card(candidate.object_id, "graveyard", reason="stale Cascade fixture")
        before = authoritative_state_hash(engine.state)

        result = session.act("pilot:A", {"action_id": "cast"})

        self.assertFalse(result.ok)
        self.assertEqual(before, authoritative_state_hash(engine.state))

        session = self.session(7028506)
        engine = session.engine
        self.prepare_cast_choice(session)
        malformed = deepcopy(engine.state.pending_decision.continuation)
        malformed["selection"]["payload"]["options_fingerprint"] = "0" * 64
        engine.state.pending_decision.continuation = malformed
        before = authoritative_state_hash(engine.state)
        result = session.act("pilot:A", {"action_id": "cast"})
        self.assertFalse(result.ok)
        self.assertEqual(before, authoritative_state_hash(engine.state))

    def test_four_player_cascade_choice_is_public_and_replays_exactly(self):
        session = self.session(7028507, players=4)
        engine = session.engine
        self.prepare_cast_choice(session)
        projected = StateProjector(self.db, engine.state)._decision("pilot:A")
        self.assertIsNotNone(projected)
        serialized = json.dumps(projected, sort_keys=True)
        self.assertNotIn("object_id", serialized)
        self.assertNotIn("logical_object_id", serialized)
        for seat in ("B", "C", "D"):
            self.assertIsNone(StateProjector(self.db, engine.state)._decision(f"pilot:{seat}"))
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        result = session.act("pilot:A", {"action_id": "decline"})

        self.assertTrue(result.ok, result.summary)
        expected_hash = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "cascade-record"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])


class BattlefieldCascadeGrantTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "battlefield-cascade-grants.sqlite3"
        fixtures = ROOT / "tests" / "fixtures"
        build_fixture_database([
            fixtures / "bound-effect-program-cards.json",
            fixtures / "battlefield-cascade-grant-cards.json",
            fixtures / "activation-restriction-material-cards.json",
            fixtures / "characteristic-query-union-cards.json",
            fixtures / "conditional-exile-cards.json",
            fixtures / "enchanted-player-static-cards.json",
            fixtures / "paid-sacrifice-creature-stat-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Battlefield Cascade", [DeckEntry("Generic Bound Commander", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30)], ["Generic Bound Commander"])

    def cascade_occurrences(self, session, card):
        self.until(session, lambda game: game.state.pending_decision is not None
            and game.state.pending_decision.kind in {"priority", "trigger.order"})
        while session.state.pending_decision.kind == "trigger.order":
            refs = [item.ref for batch in session.state.pending_trigger_batches for item in batch.items]
            result = session.act(session.pending_principals()[0], {"action_id": "order", "triggers": refs})
            self.assertTrue(result.ok, result.summary)
        return [item for item in session.state.stack if item.semantic_key == "builtin:cascade"
                and item.source_object_id == card.object_id]

    def test_first_sliver_grants_only_from_battlefield_to_its_caster(self):
        for giver_zone, caster in (("hand", "A"), ("battlefield", "A"), ("battlefield", "B")):
            with self.subTest(giver_zone=giver_zone, caster=caster):
                session = self.session(70285060 + (giver_zone == "battlefield") + (caster == "B"))
                giver = self.add(session.engine, "The First Sliver", zone=giver_zone)
                candidate = self.add(session.engine, "Universal Automaton", seat=caster,
                    zone="library" if giver_zone == "hand" else "hand", ref="cascade-sliver-candidate")
                if giver_zone == "hand":
                    session.engine.move_card(candidate.object_id, "library", position="top", log=False)
                self.assert_closed(session, giver)
                self.prepare(session, actor=caster, active=caster, mana={"C": 1, "W": 1, "U": 1, "B": 1, "R": 1, "G": 1})
                self.checkpoint(session)
                if giver_zone == "hand":
                    self.cast(session, caster, giver)
                    self.assertEqual(1, len(self.cascade_occurrences(session, giver)))
                    self.until(session, lambda game: game.state.pending_decision is not None
                        and game.state.pending_decision.kind == "selection.exile_cast")
                    result = session.act("pilot:A", {"action_id": "cast"})
                    self.assertTrue(result.ok, result.summary)
                    self.assertEqual("stack", session.state.cards[candidate.object_id].zone)
                    self.assertFalse(self.cascade_occurrences(session, candidate))
                else:
                    self.cast(session, caster, candidate)
                    self.assertEqual(int(caster == "A"), len(self.cascade_occurrences(session, candidate)))
                self.replay(session, load=True)

    def test_zhulodok_adds_two_distinct_instances_only_for_the_matching_origin_and_value(self):
        from quorune.impulse_access import grant_temporary_cast_permission
        from quorune.impulse_access_model import ImpulseAccessDuration, TemporaryCastPermissionGrant

        for from_exile in (False, True):
            with self.subTest(from_exile=from_exile):
                session = self.session(70285064 + int(from_exile))
                source = self.add(session.engine, "Zhulodok, Void Gorger")
                spell = self.add(session.engine, "Maelstrom Colossus", zone="exile" if from_exile else "hand")
                cheap = self.add(session.engine, "Universal Automaton", zone="hand")
                self.assert_closed(session, source)
                self.assert_closed(session, spell)
                if from_exile:
                    grant_temporary_cast_permission(session.engine, card=spell,
                        grant=TemporaryCastPermissionGrant("A", ImpulseAccessDuration.END_OF_TURN, None, False, source.ref),
                        reason="Prepared public cast permission")
                self.prepare(session, mana={"C": 10})
                self.checkpoint(session)
                self.cast(session, "A", cheap)
                self.assertFalse(self.cascade_occurrences(session, cheap))
                self.assertIsNone(self.resolve(session))
                self.cast(session, "A", spell)
                occurrences = self.cascade_occurrences(session, spell)
                self.assertEqual(1 if from_exile else 3, len(occurrences))
                self.assertEqual(set(range(1, len(occurrences) + 1)),
                    {item.context["cascade_instance"] for item in occurrences})
                self.assertTrue(all(item.context["source_spell_mana_value"] == 8 for item in occurrences))
                self.replay(session, load=True)

    def test_cast_grant_survives_source_removal_and_preserves_private_library(self):
        session = self.session(70285066)
        engine = session.engine
        source = self.add(engine, "Wildsear, Scouring Maw")
        spell = self.add(engine, "Banishing Light", zone="hand")
        removal = self.add(engine, "Murder", seat="B", zone="hand")
        candidate = self.add(engine, "Universal Automaton", zone="library", ref="granted-cascade-library-top")
        hidden = self.add(engine, "Generic Bound Growth", zone="library", ref="granted-cascade-hidden-tail")
        for card in (hidden, candidate):
            engine.move_card(card.object_id, "library", position="top", log=False)
            card.known_to = []
            card.revealed_to = []
        self.assert_closed(session, source)
        self.prepare(session, mana={"C": 2, "W": 1})
        session.state.players["B"].mana_pool.update({"C": 1, "B": 2})
        self.checkpoint(session)
        self.cast(session, "A", spell)
        self.assertEqual(1, len(self.cascade_occurrences(session, spell)))
        self.assertIn("cascade", {key.casefold() for key in engine._effective_card_data(spell)["keywords"]})
        for seat in "ABCD":
            self.assertNotIn(candidate.ref, json.dumps(session.packet("pilot:" + seat, full=True)))
        self.cast(session, "B", removal, [source.ref])
        self.resolve_top(session)
        self.assertEqual("graveyard", session.state.cards[source.object_id].zone)
        self.assertNotIn("cascade", {key.casefold() for key in engine._effective_card_data(spell)["keywords"]})
        self.until(session, lambda game: game.state.pending_decision is not None
            and game.state.pending_decision.kind == "selection.exile_cast")
        self.assertEqual("exile", session.state.cards[candidate.object_id].zone)
        for seat in "ABCD":
            self.assertNotIn(hidden.ref, json.dumps(session.packet("pilot:" + seat, full=True)))
        result = session.act("pilot:A", {"action_id": "decline"})
        self.assertTrue(result.ok, result.summary)
        self.replay(session, load=True)

    def test_grant_source_sacrificed_to_pay_for_cast_does_not_qualify(self):
        for sacrifice_source in (False, True):
            with self.subTest(sacrifice_source=sacrifice_source):
                session = self.session(70285067 + int(sacrifice_source))
                source = self.add(session.engine, "Quandrix, the Proof")
                other = self.add(session.engine, "Generic Bound Body")
                spell = self.add(session.engine, "Fling", zone="hand")
                price = source if sacrifice_source else other
                self.assert_closed(session, source)
                self.assert_closed(session, spell)
                self.prepare(session, mana={"C": 1, "R": 1})
                self.checkpoint(session)
                offered = self.action(session, "A", spell)
                result = session.act("pilot:A", {"action_id": offered["id"], "targets": ["B"],
                    "sacrifice_cards": [price.ref], "pay": "auto"})
                self.assertTrue(result.ok, result.summary)
                self.assertEqual("graveyard", session.state.cards[price.object_id].zone)
                self.assertEqual(int(not sacrifice_source), len(self.cascade_occurrences(session, spell)))
                self.replay(session, load=True)

    def test_flamekin_reads_the_commander_designation_instead_of_printed_identity(self):
        session = self.session(70285070)
        source = self.add(session.engine, "Flamekin Herald")
        commander = next(card for card in session.state.cards.values()
                         if card.owner == "A" and card.is_commander)
        ordinary = self.add(session.engine, "Generic Bound Commander", zone="hand", ref="nondesignated-commander-copy")
        self.assertEqual(commander.oracle_id, ordinary.oracle_id)
        self.assert_closed(session, source)
        self.prepare(session, mana={"W": 2})
        self.checkpoint(session)
        self.cast(session, "A", ordinary)
        self.assertFalse(self.cascade_occurrences(session, ordinary))
        self.assertIsNone(self.resolve(session))
        self.cast(session, "A", commander)
        self.assertEqual(1, len(self.cascade_occurrences(session, commander)))
        self.replay(session, load=True)

    def test_zhulodok_reads_announced_x_instead_of_printed_mana_value(self):
        for x in (3, 4):
            with self.subTest(x=x):
                session = self.session(70285071 + x)
                source = self.add(session.engine, "Zhulodok, Void Gorger")
                spell = self.add(session.engine, "Walking Ballista", zone="hand")
                self.assert_closed(session, source)
                self.prepare(session, mana={"C": 8})
                self.checkpoint(session)
                offered = self.action(session, "A", spell)
                result = session.act("pilot:A", {"action_id": offered["id"], "x": x, "pay": "auto"})
                self.assertTrue(result.ok, result.summary)
                occurrences = self.cascade_occurrences(session, spell)
                self.assertEqual(2 if x == 4 else 0, len(occurrences))
                self.assertTrue(all(item.context["source_spell_mana_value"] == 2 * x for item in occurrences))
                self.replay(session, load=True)

    def test_battlefield_cascade_dependency_and_compiler_omission_fail_closed(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.semantic_runtime.battlefield_cascade_grants import CASCADE_GRANT_CAPABILITY, CASCADE_GRANT_EVENT

        session = self.session(70285069)
        card = self.add(session.engine, "Flamekin Herald")
        self.assert_closed(session, card)
        program = next(program for program in session.engine.semantics.programs_for_oracle(card.oracle_id)
                       if program.event == CASCADE_GRANT_EVENT)
        deps = tuple(dep for dep in program.capability_dependencies if dep != CASCADE_GRANT_CAPABILITY)
        unbound = replace(program, capability_dependencies=deps,
            capability_closure=self.registry.closure(deps, profile="commander_review").to_dict())
        binding = bind_semantic_program_runtime(unbound, capability_registry=self.registry, profile="commander_review")
        self.assertIn("capability:undeclared_runtime_dependency:" + CASCADE_GRANT_CAPABILITY, binding["blockers"])
        options = dict(capability_registry=self.registry, capability_profile="commander_review")
        original = self.db.lookup("Flamekin Herald")
        variant = replace(original, oracle_text=original.oracle_text.replace("spells you cast", "spells an opponent casts"))
        self.assertTrue(compile_oracle_card(variant, **options).material_residuals)
        with patch("quorune.compiler.battlefield_cascade_grants.battlefield_cascade_grant_handler", return_value=None):
            self.assertTrue(compile_oracle_card(original, **options).material_residuals)


if __name__ == "__main__":
    unittest.main()
