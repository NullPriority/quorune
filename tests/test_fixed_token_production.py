from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from common import ROOT, keep_all, make_session
from quorune.carddb import CardDatabase, CardRecord
from quorune.characteristic_evaluation import type_parts
from quorune.compiled_activated_abilities import compiled_activated_abilities
from quorune.compiler.token_templates import (
    fixed_token_creation_effect_template,
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
from quorune.deck import DeckLoader
from quorune.engine import TURN_STEPS
from quorune.creature_subtypes import CREATURE_SUBTYPES
from quorune.fixed_token_production import (
    AFTERLIFE_CAPABILITY_ID,
    FIXED_DELAYED_TOKEN_CAPABILITY_ID,
    FIXED_TOKEN_COPY_CAPABILITY_ID,
    INVESTIGATE_CAPABILITY_ID,
)
from quorune.model import CardInstance, StackItem
from quorune.oracle_ir import (
    compile_oracle_card,
    register_generated_programs,
)
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
from quorune.rules.token_creation_capability_shapes import (
    fixed_token_creation_node_capabilities,
)
from quorune.session import CommanderSession
from scripts.build_test_database import build_fixture_database


FIXTURE = ROOT / "tests" / "fixtures" / "fixed-token-production-cards.json"
REGISTRY = ROOT / "quorune" / "rules" / "capability-registry.json"


def focused_database(directory: str) -> CardDatabase:
    path = Path(directory) / "fixed-token-production.sqlite3"
    build_fixture_database(
        [
            ROOT / "tests" / "fixtures" / "scryfall-exact-lists.json",
            FIXTURE,
        ],
        path,
    )
    return CardDatabase(path)


def compiler_record(
    name: str,
    text: str,
    suffix: int,
    *,
    type_line: str = "Sorcery",
    keywords: tuple[str, ...] = (),
) -> CardRecord:
    return CardRecord(
        oracle_id=f"00000000-0000-4000-8000-{suffix:012d}",
        name=name,
        mana_cost="{2}",
        mana_value=2.0,
        type_line=type_line,
        oracle_text=text,
        power="2" if "Creature" in type_line else None,
        toughness="2" if "Creature" in type_line else None,
        loyalty=None,
        defense=None,
        colors=(),
        color_identity=(),
        keywords=keywords,
        produced_mana=(),
        layout="normal",
        released_at="2026-01-01",
        legalities={"commander": "legal"},
        faces=(),
        raw={},
    )


class FixedTokenProductionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.capabilities = load_default_capability_registry()
        cls.registry_value = json.loads(REGISTRY.read_text(encoding="utf-8"))
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

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def session(self, seed: int, *, players: int = 4) -> CommanderSession:
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
        engine.state.stack.clear()
        engine.state.pending_trigger_batches.clear()
        session.commands.clear()
        session.decisions.clear()
        records = tuple(
            self.db.lookup(name)
            for name in (
                "Inquiry Engine",
                "Afterlife Envoy",
                "Afterlife Pair",
                "Copy Formula",
                "Delayed Chrysalis",
                "Token Cache",
                "Restricted Prism",
                "Artifact Prism",
            )
        )
        register_generated_programs(
            self.db,
            engine.semantics,
            records,
            trust_level="trusted",
            capability_registry=self.capabilities,
            capability_profile=engine.state.config.review_profile,
            promote_exact_trigger_programs=True,
            promote_exact_runtime_handlers=True,
            promote_exact_effect_programs=True,
        )
        return session

    @staticmethod
    def add_card(
        session: CommanderSession,
        *,
        name: str,
        ref: str,
        owner: str,
        controller: str | None = None,
        zone: str = "battlefield",
    ) -> CardInstance:
        engine = session.engine
        record = engine.card_db.lookup(name)
        controller = controller or owner
        card = CardInstance(
            object_id=f"fixture:{ref}",
            ref=ref,
            oracle_id=record.oracle_id,
            printed_name=record.name,
            owner=owner,
            controller=controller,
            zone=zone,
            zone_timestamp=engine._next_zone_timestamp(),
            acquired_control_turn_count=-1,
            known_to=list(engine.seats),
            revealed_to=list(engine.seats),
        )
        engine.state.cards[card.object_id] = card
        zone_owner = controller if zone == "battlefield" else owner
        engine.state.players[zone_owner].zones[zone].append(card.object_id)
        return card

    @staticmethod
    def prepare_priority(session: CommanderSession, seat: str = "A") -> None:
        engine = session.engine
        engine.state.active_player = seat
        engine.state.started = True
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.priority_player = None
        engine.state.priority_passes = []
        engine._grant_priority(seat)
        engine.pump()

    @staticmethod
    def pass_until_empty(session: CommanderSession, *, limit: int = 32) -> None:
        for _ in range(limit):
            if not session.state.stack and not session.state.pending_trigger_batches:
                return
            principals = session.pending_principals()
            if not principals:
                if session.state.priority_player is None:
                    session.engine._grant_priority(
                        session.state.active_player
                    )
                session.engine.pump()
                principals = session.pending_principals()
            if not principals:
                raise AssertionError(
                    "Token production resolution has no actor: "
                    f"stack={[(item.ref, item.semantic_key) for item in session.state.stack]!r}, "
                    f"batches={len(session.state.pending_trigger_batches)}, "
                    f"priority={session.state.priority_player!r}, "
                    f"decision={getattr(session.state.pending_decision, 'kind', None)!r}"
                )
            result = session.act(principals[0], {"action_id": "pass"})
            if not result.ok:
                raise AssertionError(result.summary)
        raise AssertionError("Token production resolution did not converge")

    def compile(self, record: CardRecord):
        return compile_oracle_card(
            record,
            capability_registry=self.capabilities,
            capability_profile="commander_review",
        )

    def test_compiler_lowers_investigate_afterlife_and_extended_token_definitions(self):
        fixtures = (
            compiler_record("Investigate Spell", "Investigate.", 1116001, keywords=("Investigate",)),
            compiler_record("Investigate Batch", "Investigate three times.", 1116002, keywords=("Investigate",)),
            compiler_record("Investigate Trigger", "When this creature enters, investigate.", 1116003, type_line="Creature — Scout", keywords=("Investigate",)),
            compiler_record("Optional Investigate", "You may investigate.", 1116004, keywords=("Investigate",)),
            compiler_record("Afterlife Pair", "Afterlife 1, afterlife 1", 2135001, type_line="Creature — Spirit", keywords=("Afterlife",)),
            compiler_record("Named Token", "Create a 1/1 colorless Sliver artifact creature token named Metallic Sliver.", 1110001),
            compiler_record("Legendary Token", "Create Voja, a legendary 2/2 green and white Wolf creature token.", 1110002),
            compiler_record("Postposed Colors", "Create an 8/8 Beast creature token that's red, green, and white.", 1110003),
            compiler_record("Thirteen Tokens", "Create thirteen tapped 2/2 black Zombie creature tokens.", 1110004),
            compiler_record("Infect Token", "Create two 1/1 green Phyrexian Insect creature tokens with infect.", 1110005),
            compiler_record("Landwalk Token", "Create three 1/1 red Goblin Scout creature tokens with mountainwalk.", 1110006),
            compiler_record("Changeling Token", "Create a 2/2 colorless Shapeshifter creature token with changeling.", 1110007),
            compiler_record("Restriction Token", "Create a 1/1 black Rat creature token with \"This token can't block.\"", 1110008),
            compiler_record("Powerstone Token", "Create a tapped Powerstone token.", 1110009),
            compiler_record("Junk Token", "Create a Junk token.", 1110010),
            compiler_record("Vibranium Token", "Create a tapped Vibranium token.", 1110011),
            compiler_record("Copy Token", "Create a token that's a copy of target creature.", 7070001),
            compiler_record("Delayed Token", "Create a 1/1 white Spirit creature token with flying at the beginning of the next end step.", 6037001),
            compiler_record("Restricted Powerstone Mana", "{T}: Add {C}. This mana can't be spent to cast a nonartifact spell.", 1066001, type_line="Artifact"),
            compiler_record("Restricted Artifact Mana", "{T}: Add one mana of any color. Spend this mana only to cast an artifact spell.", 1066002, type_line="Artifact"),
            compiler_record("Restricted Creature Mana", "{T}: Add one mana of any color. Spend this mana only to cast a creature spell.", 1066003, type_line="Artifact"),
        )
        compiled_by_name = {}
        for record in fixtures:
            with self.subTest(record=record.name):
                compiled = self.compile(record)
                compiled_by_name[record.name] = compiled
                self.assertEqual("exact", compiled.status, compiled.material_residuals)
                self.assertTrue(all(node.exact for face in compiled.faces for node in face.nodes))

        afterlife = compiled_by_name["Afterlife Pair"]
        nodes = [node for node in afterlife.faces[0].nodes if node.template_id == "afterlife-fixed-token-trigger-v1"]
        self.assertEqual(2, len(nodes))
        self.assertEqual(2, len({(node.span.start, node.span.end) for node in nodes}))
        self.assertTrue(all(node.event == "permanent.graveyard.self" for node in nodes))

        copy_node = compiled_by_name["Copy Token"].faces[0].nodes[0]
        self.assertEqual("$target.0", copy_node.effects[0]["copy_of"])
        self.assertEqual(["creature"], copy_node.target_schema["types_any"])
        self.assertIn(FIXED_TOKEN_COPY_CAPABILITY_ID, copy_node.capability_dependencies)

        for name, restriction in (
            ("Restricted Powerstone Mana", "nonartifact_spell_prohibited"),
            ("Restricted Artifact Mana", "artifact_spell_only"),
            ("Restricted Creature Mana", "creature_spell_only"),
        ):
            with self.subTest(restriction=restriction):
                node = compiled_by_name[name].faces[0].nodes[0]
                self.assertEqual(
                    "activated-mana-restricted-fixed-output-v1",
                    node.template_id,
                )
                self.assertEqual(
                    ("mana.activated.restricted_fixed_output",),
                    node.capability_dependencies,
                )
                self.assertEqual(
                    restriction,
                    node.handlers[0]["ability"]["spend_restriction"],
                )

    def test_token_production_grammar_and_capability_mutants_fail_closed(self):
        unsupported = (
            "Investigate X times.",
            "Afterlife 0",
            "Afterlife X",
            "Create a token that's a copy of target creature you control.",
            "Create a token that's a copy of this creature.",
            "Create a token that's a copy of target creature, except it has haste.",
            "Create a 1/1 red Warrior creature token tapped and attacking.",
            "Create a Wicked Role token attached to target creature.",
            "Create an Incubator token.",
            "Create a 1/1 green Boar creature token with \"When this token dies, create a Food token.\"",
            "Create a 1/1 blue Fish creature token with \"This token has hexproof.\"",
            "Create a 1/1 white Spirit creature token at the beginning of your next end step.",
        )
        base = compiler_record("Unsupported Production", unsupported[0], 1110900)
        for index, text in enumerate(unsupported):
            with self.subTest(text=text):
                compiled = self.compile(
                    replace(base, oracle_id=f"00000000-0000-4000-8000-{1110900 + index:012d}", oracle_text=text)
                )
                self.assertNotEqual("exact", compiled.status)
                self.assertTrue(compiled.material_residuals)

        mutations = (
            (INVESTIGATE_CAPABILITY_ID, compiler_record("Blocked Investigate", "Investigate.", 1110950, keywords=("Investigate",))),
            (AFTERLIFE_CAPABILITY_ID, compiler_record("Blocked Afterlife", "Afterlife 1", 1110951, type_line="Creature — Spirit", keywords=("Afterlife",))),
            (FIXED_TOKEN_COPY_CAPABILITY_ID, compiler_record("Blocked Copy", "Create a token that's a copy of target creature.", 1110952)),
            (FIXED_DELAYED_TOKEN_CAPABILITY_ID, compiler_record("Blocked Delay", "Create a 1/1 white Spirit creature token at the beginning of the next end step.", 1110953)),
            ("mana.activated.restricted_fixed_output", compiler_record("Blocked Mana", "{T}: Add one mana of any color. Spend this mana only to cast a creature spell.", 1110954, type_line="Artifact")),
        )
        for capability_id, record in mutations:
            with self.subTest(capability=capability_id):
                value = deepcopy(self.registry_value)
                row = next(item for item in value["capabilities"] if item["id"] == capability_id)
                row["status"] = "blocked"
                row["blockers"] = ["focused token-production mutation"]
                registry = CapabilityRegistry(value)
                registry.mark_evidence_verified("0" * 64)
                compiled = compile_oracle_card(record, capability_registry=registry, capability_profile="commander_review")
                self.assertNotEqual("exact", compiled.status)

        records_by_capability = dict(mutations)
        dependency_mutations = (
            (INVESTIGATE_CAPABILITY_ID, "token.creation.fixed_definition"),
            (INVESTIGATE_CAPABILITY_ID, "zone.draw.library_to_hand"),
            (AFTERLIFE_CAPABILITY_ID, "combat.block.flying"),
            (AFTERLIFE_CAPABILITY_ID, "token.creation.fixed_definition"),
            (
                AFTERLIFE_CAPABILITY_ID,
                "trigger.event.normalized_zone_change",
            ),
            (AFTERLIFE_CAPABILITY_ID, "trigger.placement.apnap"),
            (
                FIXED_TOKEN_COPY_CAPABILITY_ID,
                "target.revalidate_resolution",
            ),
            (
                FIXED_DELAYED_TOKEN_CAPABILITY_ID,
                "token.creation.fixed_definition",
            ),
            (
                FIXED_DELAYED_TOKEN_CAPABILITY_ID,
                "trigger.placement.apnap",
            ),
            (
                "mana.activated.restricted_fixed_output",
                "activation.tap_untap_cost.haste",
            ),
        )
        for capability_id, dependency_id in dependency_mutations:
            with self.subTest(
                capability=capability_id,
                dependency=dependency_id,
            ):
                value = deepcopy(self.registry_value)
                dependency = next(
                    item
                    for item in value["capabilities"]
                    if item["id"] == dependency_id
                )
                dependency["status"] = "blocked"
                dependency["blockers"] = [
                    "focused token-production dependency mutation"
                ]
                registry = CapabilityRegistry(value)
                registry.mark_evidence_verified("0" * 64)
                compiled = compile_oracle_card(
                    records_by_capability[capability_id],
                    capability_registry=registry,
                    capability_profile="commander_review",
                )
                self.assertNotEqual("exact", compiled.status)
                self.assertTrue(
                    any(
                        dependency_id in blocker
                        for residual in compiled.material_residuals
                        for blocker in residual.blockers
                    )
                )

        with mock.patch("quorune.oracle_ir.fixed_token_creation_effect_template", return_value=None):
            self.assertNotEqual("exact", self.compile(compiler_record("Missing Investigate", "Investigate.", 1110990, keywords=("Investigate",))).status)
        with mock.patch("quorune.compiler.keyword_nodes.fixed_afterlife_keyword_node", return_value=None):
            self.assertNotEqual("exact", self.compile(compiler_record("Missing Afterlife", "Afterlife 1", 1110991, type_line="Creature — Spirit", keywords=("Afterlife",))).status)

        malformed_copy = {
            "op": "create_token",
            "controller": "$controller",
            "quantity": 1,
            "copy_of": "$source",
        }
        copy_template = fixed_token_creation_effect_template(
            "Create a token that's a copy of target creature."
        )
        self.assertIsNotNone(copy_template)
        self.assertEqual(
            (FIXED_TOKEN_COPY_CAPABILITY_ID,),
            fixed_token_creation_node_capabilities(
                effects=(copy_template.effect,),
                target_schema=copy_template.target_schema,
                mechanic_ids=set(copy_template.mechanics),
            ),
        )
        self.assertEqual(
            (),
            fixed_token_creation_node_capabilities(
                effects=(malformed_copy,),
                target_schema=copy_template.target_schema,
                mechanic_ids=set(copy_template.mechanics),
            ),
        )
        self.assertEqual(
            (),
            fixed_token_creation_node_capabilities(
                effects=(copy_template.effect,),
                target_schema=copy_template.target_schema,
                mechanic_ids={"cr-111-tokens"},
            ),
        )

        delayed_template = fixed_token_creation_effect_template(
            "Create a 1/1 white Spirit creature token at the beginning of "
            "the next end step."
        )
        self.assertIsNotNone(delayed_template)
        self.assertIn(
            FIXED_DELAYED_TOKEN_CAPABILITY_ID,
            fixed_token_creation_node_capabilities(
                effects=(delayed_template.effect,),
                target_schema=None,
                mechanic_ids=set(delayed_template.mechanics),
            ),
        )
        self.assertEqual(
            (),
            fixed_token_creation_node_capabilities(
                effects=(
                    {**dict(delayed_template.effect), "event": "step.end"},
                ),
                target_schema=None,
                mechanic_ids=set(delayed_template.mechanics),
            ),
        )

    def test_investigate_and_predefined_tokens_use_canonical_abilities(self):
        session = self.session(1116101, players=2)
        engine = session.engine
        inquiry = self.add_card(session, name="Inquiry Engine", ref="inquiry", owner="A")
        engine.state.players["A"].mana_pool["C"] = 4
        self.prepare_priority(session)
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        result = session.act("pilot:A", {"action_id": f"activate:{inquiry.ref}:ab1"})
        self.assertTrue(result.ok, result.summary)
        self.pass_until_empty(session)
        clue = next(card for card in engine.state.cards.values() if card.is_token and card.printed_name == "Clue")
        [clue_ability] = compiled_activated_abilities(engine, clue)
        self.assertEqual("builtin:draw:1", clue_ability.builtin_semantic_key)

        expected_hash = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "investigate-record"
            session.save(directory)
            replay = replay_record(directory, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

        cache = self.add_card(session, name="Token Cache", ref="cache", owner="A")
        programs = [program for program in engine.semantics.programs_for_oracle(cache.oracle_id) if program.effects]
        for program in programs:
            item = StackItem(stack_id=f"stack:{program.ability_id}", ref=f"S-{program.ability_id}", kind="triggered_ability", controller="A", label=program.label, source_object_id=cache.object_id, semantic_key=program.key, visibility=list(engine.seats), context={"source_logical_object_id": cache.logical_object_id})
            engine.state.stack.append(item)
            engine._continue_resolution(stack_ref=item.ref, effects=[dict(effect) for effect in program.effects], destination=None, note=program.notes)
        created = {card.printed_name: card for card in engine.state.cards.values() if card.is_token}
        self.assertTrue({"Clue", "Powerstone", "Junk", "Vibranium"}.issubset(created))
        self.assertEqual(["Indestructible"], engine._effective_card_data(created["Vibranium"])["keywords"])

    def test_token_characteristic_fragments_use_shared_rules_owners(self):
        session = self.session(1116108, players=2)
        engine = session.engine
        ordinary_attacker = self.add_card(
            session,
            name="Creature Payment",
            ref="ordinary-attacker",
            owner="A",
        )
        ordinary_blocker = self.add_card(
            session,
            name="Creature Payment",
            ref="ordinary-blocker",
            owner="B",
        )
        definitions = (
            (
                "Create a 1/1 black Rat creature token with \"This token "
                "can't block.\"",
                "B",
            ),
            (
                "Create a 1/1 blue Fish creature token with \"This token "
                "can't be blocked.\"",
                "A",
            ),
            (
                "Create a 2/2 colorless Shapeshifter creature token with "
                "changeling.",
                "A",
            ),
        )
        created = {}
        for text, controller in definitions:
            template = fixed_token_creation_effect_template(text)
            self.assertIsNotNone(template)
            effect = {**dict(template.effect), "controller": controller}
            before = set(engine.state.cards)
            engine.apply_effect(effect, actor=controller)
            [token] = [
                card
                for object_id, card in engine.state.cards.items()
                if object_id not in before and card.is_token
            ]
            created[token.printed_name] = token

        self.assertFalse(
            engine._can_block(ordinary_attacker, created["Rat"])[0]
        )
        self.assertFalse(
            engine._can_block(created["Fish"], ordinary_blocker)[0]
        )
        current = engine._effective_card_data(created["Shapeshifter"])
        card_types, subtypes, _supertypes = type_parts(current["type_line"])
        self.assertIn("creature", card_types)
        self.assertEqual(CREATURE_SUBTYPES, frozenset(subtypes))

    def test_predefined_token_profiles_enforce_mana_and_impulse_rules(self):
        session = self.session(1116102, players=2)
        engine = session.engine
        engine.state.players["A"].mana_pool.update({"C": 1, "G": 1, "U": 1})
        engine._add_restricted_mana("A", "nonartifact_spell_prohibited", {"C": 1})
        engine._add_restricted_mana("A", "creature_spell_only", {"G": 1})
        engine._add_restricted_mana("A", "artifact_spell_only", {"U": 1})
        artifact = engine._spell_mana_spend_context("Artifact")
        creature = engine._spell_mana_spend_context("Creature — Test")
        nonartifact = engine._spell_mana_spend_context("Sorcery")
        self.assertEqual("artifact_spell", artifact)
        self.assertEqual("creature_spell", creature)
        self.assertEqual(0, engine._spendable_mana_pool("A", nonartifact)["C"])
        self.assertEqual(1, engine._spendable_mana_pool("A", artifact)["C"])
        self.assertEqual(1, engine._spendable_mana_pool("A", creature)["G"])
        self.assertEqual(0, engine._spendable_mana_pool("A", nonartifact)["G"])
        self.assertEqual(1, engine._spendable_mana_pool("A", artifact)["U"])
        self.assertEqual(0, engine._spendable_mana_pool("A", creature)["U"])
        engine._apply_mana_spend("A", {"C": 1}, artifact)
        self.assertEqual(0, engine.state.players["A"].mana_pool["C"])

        replay_session = self.session(1116106, players=2)
        replay_engine = replay_session.engine
        for text in ("Create a Powerstone token.", "Create a Junk token."):
            template = fixed_token_creation_effect_template(text)
            self.assertIsNotNone(template)
            effect = {**dict(template.effect), "controller": "A"}
            replay_engine.apply_effect(effect, actor="A")
        powerstone = next(
            card
            for card in replay_engine.state.cards.values()
            if card.is_token and card.printed_name == "Powerstone"
        )
        junk = next(
            card
            for card in replay_engine.state.cards.values()
            if card.is_token and card.printed_name == "Junk"
        )
        self.prepare_priority(replay_session)
        top = replay_engine.state.players["A"].zones["library"][-1]
        replay_session.initial_checkpoint = checkpoint_envelope(
            replay_engine.state
        )
        replay_session.commands.clear()
        replay_session.decisions.clear()

        result = replay_session.act(
            "pilot:A",
            {"action_id": f"activate:{powerstone.ref}:ab1"},
        )
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(1, replay_engine.state.players["A"].mana_pool["C"])
        self.assertEqual(
            1,
            replay_engine.state.players["A"].stats["restricted_mana"]
            ["nonartifact_spell_prohibited"]["C"],
        )
        result = replay_session.act(
            "pilot:A",
            {"action_id": f"activate:{junk.ref}:ab1"},
        )
        self.assertTrue(result.ok, result.summary)
        self.pass_until_empty(replay_session)
        self.assertEqual("outside", junk.zone)
        self.assertEqual("exile", replay_engine.state.cards[top].zone)
        self.assertIn(
            "temporary_play_permission",
            replay_engine.state.cards[top].annotations,
        )
        expected_hash = authoritative_state_hash(replay_engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "predefined-token-record"
            replay_session.save(directory)
            replay = replay_record(directory, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_compiled_restricted_mana_is_controller_scoped_and_replays(self):
        session = self.session(1116110)
        engine = session.engine
        creature_prism = self.add_card(
            session,
            name="Restricted Prism",
            ref="creature-prism",
            owner="A",
        )
        artifact_prism = self.add_card(
            session,
            name="Artifact Prism",
            ref="artifact-prism",
            owner="A",
        )
        self.prepare_priority(session)
        own_sources = {
            action.get("source")
            for action in engine._priority_action_hints("A")["actions"]
        }
        opposing_sources = {
            action.get("source")
            for action in engine._priority_action_hints("B")["actions"]
        }
        self.assertTrue(
            {creature_prism.ref, artifact_prism.ref}.issubset(own_sources)
        )
        self.assertFalse(
            {creature_prism.ref, artifact_prism.ref}
            .intersection(opposing_sources)
        )

        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        result = session.act(
            "pilot:A",
            {
                "action_id": f"activate:{creature_prism.ref}:ab1",
                "mana_output": {"G": 1},
            },
        )
        self.assertTrue(result.ok, result.summary)
        result = session.act(
            "pilot:A",
            {
                "action_id": f"activate:{artifact_prism.ref}:ab1",
                "mana_output": {"U": 1},
            },
        )
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(1, engine.state.players["A"].mana_pool["G"])
        self.assertEqual(1, engine.state.players["A"].mana_pool["U"])
        restrictions = engine.state.players["A"].stats["restricted_mana"]
        self.assertEqual(1, restrictions["creature_spell_only"]["G"])
        self.assertEqual(1, restrictions["artifact_spell_only"]["U"])
        self.assertEqual(
            0,
            engine._spendable_mana_pool(
                "A",
                engine._spell_mana_spend_context("Sorcery"),
            )["G"],
        )

        expected_hash = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "restricted-mana-record"
            session.save(directory)
            replay = replay_record(directory, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_afterlife_uses_previous_controller_and_permanent_graveyard_lki(self):
        session = self.session(1116103)
        engine = session.engine
        source = self.add_card(session, name="Afterlife Envoy", ref="afterlife", owner="A")
        engine.change_control(source.object_id, "B", reason="afterlife controller witness")
        commit_continuous_effect(
            engine.state,
            ContinuousEffect(
                effect_id="fixture:afterlife-noncreature",
                source_id="fixture:afterlife-noncreature",
                layer=Layer.TYPE,
                sublayer="4",
                timestamp=engine._next_zone_timestamp(),
                operations=(ContinuousOperation("set_types", ["Artifact"], field="card_types"),),
                origin=ContinuousEffectOrigin.RESOLUTION,
                duration=ContinuousEffectDuration.UNTIL_END_OF_TURN,
                locked_objects=(ContinuousObjectIdentity(object_id=source.object_id, logical_object_id=source.logical_object_id),),
            ),
        )
        self.assertNotIn("creature", engine._type_parts(engine._effective_card_data(source)["type_line"])[0])
        engine.state.active_player = "A"
        engine.move_card(source.object_id, "graveyard", semantic_events=True, reason="noncreature Afterlife witness")
        engine._stabilize()
        self.assertEqual(1, len(engine.state.stack))
        item = engine.state.stack[-1]
        self.assertEqual("B", item.controller)
        self.assertEqual("permanent.graveyard", item.context["event"])
        engine._grant_priority("A")
        engine.pump()
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        self.pass_until_empty(session)
        spirits = [card for card in engine.state.cards.values() if card.is_token and card.controller == "B" and card.printed_name == "Spirit"]
        self.assertEqual(2, len(spirits))
        self.assertTrue(all(engine._effective_card_data(card)["colors"] == ["W", "B"] for card in spirits))
        self.assertTrue(
            all(
                "Flying" in engine._effective_card_data(card)["keywords"]
                for card in spirits
            )
        )
        expected_hash = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "afterlife-record"
            session.save(directory)
            replay = replay_record(directory, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_multiple_afterlife_instances_share_apnap_batch_and_replay(self):
        session = self.session(1116109)
        engine = session.engine
        single = self.add_card(
            session,
            name="Afterlife Envoy",
            ref="afterlife-single",
            owner="B",
        )
        pair = self.add_card(
            session,
            name="Afterlife Pair",
            ref="afterlife-pair",
            owner="C",
        )
        engine.state.active_player = "A"
        engine._move_cards_simultaneously(
            (
                (pair.object_id, "graveyard"),
                (single.object_id, "graveyard"),
            ),
            reason="simultaneous Afterlife fixture",
        )
        engine._grant_priority("A")

        decision = engine.state.pending_decision
        self.assertIsNotNone(
            decision,
            (
                f"stack={[(item.controller, item.label) for item in engine.state.stack]!r}, "
                f"batches={[batch.to_dict() for batch in engine.state.pending_trigger_batches]!r}"
            ),
        )
        self.assertEqual("trigger.order", decision.kind)
        self.assertEqual(["C"], decision.actors)
        self.assertEqual(["pilot:C"], session.pending_principals())
        projector = StateProjector(self.db, engine.state)
        self.assertIsNotNone(projector._decision("pilot:C"))
        for seat in ("A", "B", "D"):
            self.assertIsNone(projector._decision(f"pilot:{seat}"))
        refs = [
            row["id"]
            for row in decision.payload_by_actor["C"]["triggers"]
        ]
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        result = session.act(
            "pilot:C",
            {"action_id": "order", "triggers": list(reversed(refs))},
        )
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(
            ["B", "C", "C"],
            [item.controller for item in engine.state.stack],
        )
        self.pass_until_empty(session)
        spirits = [
            card
            for card in engine.state.cards.values()
            if card.is_token and card.printed_name == "Spirit"
        ]
        self.assertEqual(2, sum(card.controller == "B" for card in spirits))
        self.assertEqual(2, sum(card.controller == "C" for card in spirits))

        expected_hash = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "afterlife-apnap-record"
            session.save(directory)
            replay = replay_record(directory, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_copy_and_delayed_tokens_revalidate_and_replay(self):
        stale_session = self.session(1116104, players=2)
        stale_engine = stale_session.engine
        stale = self.add_card(
            stale_session,
            name="Creature Payment",
            ref="stale-copy-target",
            owner="A",
        )
        stale_spell = self.add_card(
            stale_session,
            name="Copy Formula",
            ref="stale-copy-spell",
            owner="A",
            zone="hand",
        )
        stale_engine.state.players["A"].mana_pool.update({"C": 2, "U": 1})
        self.prepare_priority(stale_session)
        result = stale_session.act(
            "pilot:A",
            {
                "action_id": f"cast:{stale_spell.ref}",
                "targets": [stale.ref],
                "pay": "manual",
                "payment": {"C": 2, "U": 1},
            },
        )
        self.assertTrue(result.ok, result.summary)
        stale_engine.move_card(stale.object_id, "graveyard", semantic_events=True)
        stale_engine.move_card(
            stale.object_id,
            "battlefield",
            controller="A",
            semantic_events=True,
        )
        self.pass_until_empty(stale_session)
        self.assertFalse(
            any(card.is_token for card in stale_engine.state.cards.values())
        )

        session = self.session(1116107, players=2)
        engine = session.engine
        target = self.add_card(
            session,
            name="Creature Payment",
            ref="copy-target",
            owner="A",
        )
        spell = self.add_card(
            session,
            name="Copy Formula",
            ref="copy-spell",
            owner="A",
            zone="hand",
        )
        delayed = self.add_card(
            session,
            name="Delayed Chrysalis",
            ref="delayed",
            owner="A",
        )
        engine.state.players["A"].mana_pool.update({"C": 2, "U": 1, "G": 1})
        self.prepare_priority(session)
        engine.state.phase = "postcombat_main"
        engine.state.step = "main"
        engine.state.phase_index = TURN_STEPS.index(
            ("postcombat_main", "main")
        )
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        result = session.act(
            "pilot:A",
            {
                "action_id": f"cast:{spell.ref}",
                "targets": [target.ref],
                "pay": "manual",
                "payment": {"C": 2, "U": 1},
            },
        )
        self.assertTrue(result.ok, result.summary)
        self.pass_until_empty(session)
        copied = [
            card
            for card in engine.state.cards.values()
            if card.is_token and card.printed_name == "Creature Payment"
        ]
        self.assertEqual(1, len(copied))
        self.assertEqual("1", engine._effective_card_data(copied[0])["power"])

        engine.state.players["A"].mana_pool["G"] = 1
        result = session.act("pilot:A", {"action_id": f"activate:{delayed.ref}:ab1"})
        self.assertTrue(result.ok, result.summary)
        self.pass_until_empty(session)
        self.assertEqual("graveyard", delayed.zone)
        self.assertTrue(any(trigger.active for trigger in engine.state.delayed_triggers))
        for _ in range(16):
            if any(
                card.is_token and card.printed_name == "Butterfly"
                for card in engine.state.cards.values()
            ):
                break
            principals = session.pending_principals()
            self.assertTrue(principals)
            result = session.act(principals[0], {"action_id": "pass"})
            self.assertTrue(result.ok, result.summary)
        self.assertTrue(any(card.is_token and card.printed_name == "Butterfly" for card in engine.state.cards.values()))

        expected_hash = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "fixed-token-production-record"
            session.save(directory)
            replay = replay_record(directory, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_token_replacement_order_rollback_and_privacy(self):
        session = self.session(1116105)
        engine = session.engine
        for name in ("Stridehangar Automaton", "Worldwalker Helm"):
            source = next(card for card in engine.state.cards.values() if card.owner == "A" and card.printed_name == name)
            engine.move_card(source.object_id, "battlefield", controller="A")
        program = next(program for program in engine.semantics.programs_for_oracle(self.db.lookup("Inquiry Engine").oracle_id) if program.effects)
        effect = deepcopy(program.effects[0])
        effect["quantity"] = 3
        item = StackItem(stack_id="investigate-replacement", ref="S-investigate-replacement", kind="triggered_ability", controller="A", label=program.label, semantic_key=program.key, visibility=list(engine.seats))
        engine.state.stack.append(item)
        engine._continue_resolution(stack_ref=item.ref, effects=[effect], destination=None, note=program.notes)
        decision = engine.state.pending_decision
        self.assertEqual("replacement.order", decision.kind)
        self.assertEqual(["A"], decision.actors)
        projector = StateProjector(self.db, engine.state)
        projected = projector._decision("pilot:A")
        for seat in ("B", "C", "D"):
            self.assertIsNone(projector._decision(f"pilot:{seat}"))
        before = authoritative_state_hash(engine.state)
        rejected = engine.try_submit(token=engine.permissions.capability_for("pilot:A").token, principal="pilot:A", action="choose", payload={"replacement": "stale-choice"})
        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(engine.state))
        result = engine.submit(token=engine.permissions.capability_for("pilot:A").token, principal="pilot:A", action="choose", payload={"replacement": projected["ctx"]["options"][0]["id"]})
        self.assertTrue(result.ok, result.summary)
        self.assertGreaterEqual(len([card for card in engine.state.cards.values() if card.is_token]), 5)


if __name__ == "__main__":
    unittest.main()
