from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.semantics import SemanticRegistry
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.loot_transform_graveyard import LOOT_TRANSFORM_CAPABILITY
from quorune.rules.loyalty_graveyard_cast import LOYALTY_GRAVEYARD_CAST_CAPABILITY, current_loyalty_graveyard_cast_permission
from scripts.build_test_database import build_fixture_database


CARD = "Jace, Vryn's Prodigy // Jace, Telepath Unbound"
BACK = "Jace, Telepath Unbound"


class LootTransformGraveyardRiderTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        db = Path(cls.temporary.name) / "loot-transform-graveyard-rider.sqlite3"
        fixtures = (
            "bound-effect-program-cards", "loot-transform-graveyard-rider-cards",
            "attached-cast-restriction-cards", "activation-restriction-material-cards",
        )
        build_fixture_database([ROOT / f"tests/fixtures/{name}.json" for name in fixtures], db)
        cls.db = CardDatabase(db)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Loot transformation and graveyard rider", [
            DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def scene(self, seed, count, owner="A"):
        game = self.session(seed)
        source = self.add(game.engine, CARD, seat=owner, ref="jace-source")
        source.active_face = "Jace, Vryn\x27s Prodigy"
        if owner != "A":
            game.engine.change_control(source.object_id, "A", reason="pre-action borrowed source")
        source.acquired_control_turn_count = -1
        discarded = self.add(game.engine, "Island", zone="hand", ref="jace-private-discard")
        spell = self.add(game.engine, "Shock", zone="graveyard")
        for n in range(count - 1):
            self.add(game.engine, "Plains", zone="graveyard", ref=f"jace-yard-{n}")
        self.assert_closed(game, source)
        return game, source, discarded, spell

    def activation(self, game, source, predicate, targets=()):
        self.priority(game, "A")
        original = game.state.cards[source.object_id]
        ability, = [ability for ability in game.engine._activated_abilities(original) if predicate(ability)]
        action = f"activate:{original.ref}:{ability.ability_id}"
        candidate = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        self.assertIn(action, {row["id"] for row in candidate})
        result = game.act("pilot:A", {"action_id":"activate:"+original.ref+":"+ability.ability_id,
                                     "targets":list(targets),"pay":"auto"})
        self.assertTrue(result.ok, result.summary)

    def finish_loot(self, game, discard):
        result = game.act("pilot:A", {"action_id":"choose","cards":[discard.ref]})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))

    def test_actual_post_discard_threshold_returns_transformed_and_grants_one_paid_cast_with_exile_rider(self):
        game, source, discard, spell = self.scene(61412811, 4)
        self.add(game.engine, "Mountain")
        self.prepare(game)
        self.checkpoint(game)
        initial_identity = game.state.cards[source.object_id].logical_object_id
        self.activation(game, source, lambda ability: ability.tap_source)
        choice = self.resolve(game)
        self.assertIsNotNone(choice)
        self.assertEqual(["A"], choice.actors)
        for principal in ("pilot:B", "pilot:C", "pilot:D", "spectator"):
            self.assertNotIn(discard.ref, json.dumps(game.packet(principal, full=True)))
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", {"action_id":"choose","cards":[discard.ref]}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        game = self.replay(game, load=True)
        self.finish_loot(game, discard)
        walker = game.state.cards[source.object_id]
        self.assertEqual(("battlefield", BACK, "A", 5), (walker.zone, walker.active_face, walker.controller, walker.counters["loyalty"]))
        self.assertNotEqual(initial_identity, walker.logical_object_id)
        self.activation(game, source, lambda ability: ability.loyalty_delta == -3, [spell.ref])
        self.assertIsNone(self.resolve(game))
        current = game.state.cards[spell.object_id]
        self.assertIsNotNone(current_loyalty_graveyard_cast_permission(game.state, "A", current))
        self.assertIsNone(current_loyalty_graveyard_cast_permission(game.state, "B", current))

        self.priority(game, "A")
        offer = self.action(game, "A", spell)
        before = authoritative_state_hash(game.state)
        reply = game.act("pilot:A", {"action_id":offer["id"],"pay":"manual","payment":{},"targets":["B"]})
        self.assertFalse(reply.ok, reply.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.cast(game, "A", spell, ["B"])
        item, = [item for item in game.state.stack if item.card_object_id == spell.object_id]
        self.assertEqual(("R",), item.mana_colors_spent)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(38, game.state.players["B"].life)
        self.assertEqual("exile", game.state.cards[spell.object_id].zone)
        self.assertIsNone(current_loyalty_graveyard_cast_permission(game.state, "A", game.state.cards[spell.object_id]))
        self.replay(game, load=True)

    def test_actual_borrowed_source_returns_to_owner_and_missing_source_or_count_leaves_no_return(self):
        for seed, count, owner, bounced in ((61412812, 4, "B", False),(61412813, 3, "A", False),(61412814, 4, "A", True)):
            with self.subTest(owner=owner, count=count, bounced=bounced):
                game, source, discard, _ = self.scene(seed, count, owner)
                reply = None
                if bounced:
                    reply = self.add(game.engine, "Unsummon", seat="B", zone="hand")
                    self.add(game.engine, "Island", seat="B", ref="jace-reply-mana")
                self.prepare(game)
                self.checkpoint(game)
                self.activation(game, source, lambda ability: ability.tap_source)
                if reply is not None:
                    self.cast(game, "B", reply, [source.ref])
                    self.resolve_top(game)
                pending = self.resolve(game)
                self.assertIsNotNone(pending)
                game = self.replay(game, load=True)
                self.finish_loot(game, discard)
                current = game.state.cards[source.object_id]
                if owner == "B":
                    self.assertEqual(("battlefield",BACK,"B"), (current.zone,current.active_face,current.controller))
                elif bounced:
                    self.assertEqual("hand", current.zone)
                else:
                    self.assertNotEqual(BACK, current.active_face)
                    self.assertEqual("battlefield", current.zone)
                self.replay(game, load=True)

    def test_complete_faces_require_the_exact_loot_owner_and_paid_graveyard_cast(self):
        original = self.db.lookup(CARD, fuzzy=False)
        compiled = compile_oracle_card(original, capability_registry=self.registry, capability_profile="commander_review")
        self.assertEqual("exact", compiled.status, compiled.material_residuals)
        self.assertEqual(2, len(compiled.faces))
        nodes = [node for face in compiled.faces for node in face.nodes]
        self.assertTrue(any(LOOT_TRANSFORM_CAPABILITY in node.capability_dependencies for node in nodes))
        self.assertTrue(any(LOYALTY_GRAVEYARD_CAST_CAPABILITY in node.capability_dependencies for node in nodes))
        self.assertTrue(any(node.cost and node.cost.get("loyalty_delta") == 1 for node in nodes))
        self.assertTrue(any(node.cost and node.cost.get("loyalty_delta") == -9 for node in nodes))
        program = compile_best_available_card_program(
            self.db, original, semantic_registry=SemanticRegistry(),
            capability_registry=self.registry, capability_profile="commander_review",
        )
        self.assertTrue(program.trust_closure["strict_capability_ready"])
        self.assertTrue({LOOT_TRANSFORM_CAPABILITY, LOYALTY_GRAVEYARD_CAST_CAPABILITY,
                         "activation.loyalty.positive_counter_cost", "object.emblem.creation"}
                        <= set(program.trust_closure["capability_dependencies"]))

    def test_omitting_the_activation_binding_is_killed(self):
        with patch("quorune.compiler.activation_paid_sacrifice_results.loot_transform_or_graveyard_cast",return_value=None):
            with self.assertRaises(AssertionError):
                self.test_complete_faces_require_the_exact_loot_owner_and_paid_graveyard_cast()

class EmryArtifactGraveyardCastTests(InheritedNativeFixture, unittest.TestCase):
    name = "Emry, Lurker of the Loch"
    profile = "commander_review"

    @classmethod
    def use_pinned_rows(cls, path):
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Artifact graveyard casts", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "emry-graveyard-cast.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/emry-graveyard-cast-cards.json"], path)
        cls.use_pinned_rows(path)

    def offered(self, game, kind, card, *, seat="A"):
        packet = game.packet(f"pilot:{seat}", full=True)
        rows = (packet.get("decision") or {}).get("ctx", {}).get("legal", {}).get("actions", ())
        prefix = f"{kind}:{card.ref}"
        return next((row for row in rows
                     if (row.get("kind"), row.get("card")) == (kind, card.ref)
                     or row["id"] == prefix or row["id"].startswith(prefix + ":")), None)

    def activate_card(self, game, source, target):
        activation = self.offered(game, "activate", source)
        self.assertIsNotNone(activation)
        result = game.act("pilot:A", {"action_id": activation["id"], "targets": [target.ref], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertTrue(game.state.cards[source.object_id].tapped)

    def recovered_source(self, game):
        source = self.add(game.engine, self.name)
        source.acquired_control_turn_count = -1
        self.assert_closed(game, source)
        return source

    def test_emry_strict_admission_reuses_price_target_and_legacy_slot(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.replacement.immutable import thaw_value
        from quorune.rules.artifact_graveyard_cast_activation import ARTIFACT_GRAVEYARD_CAST_CAPABILITY
        from test_controller_creature_keyword_absence import register_card
        from test_qualified_zone_event_queries import query_record

        record = self.db.lookup(self.name, fuzzy=False)
        self.assertEqual("da3e7d3d-2ca0-40c3-9602-fca37c92f507", record.oracle_id)
        options = {"capability_registry": self.registry, "capability_profile": self.profile}
        result = compile_oracle_card(record, **options)
        self.assertEqual("exact", result.status, result.material_residuals)
        affinity, mill, activated = result.faces[0].nodes
        self.assertIn("casting.payment.affinity", affinity.capability_dependencies)
        self.assertEqual(({"op": "mill", "player": "$controller", "count": 4},), mill.effects)
        self.assertEqual((125, 263, 3), (activated.span.start, activated.span.end, activated.span.line))
        self.assertEqual("owned-artifact-graveyard-cast-v1", activated.template_id)
        self.assertTrue(activated.cost["tap_source"])
        self.assertEqual(0, activated.cost["life_payment"])
        self.assertEqual(
            {"zones": ["graveyard"], "categories": ["card"], "owner": "you", "artifact": True, "count": 1},
            activated.target_schema,
        )
        ready, semantics = register_card(self.db, self.registry, self.name)
        self.assertTrue(ready["strict_capability_ready"], ready)
        original = semantics.programs_for_oracle(record.oracle_id)
        ability, = (program for program in original if program.event == "activate")
        sibling, = (program for program in original if program.event == "permanent.enter.self")
        self.assertEqual("ability:ab3", ability.ability_id)
        self.assertEqual("mill-fixed-controller-v1", sibling.provenance["template_id"])
        self.assertTrue(ability.provenance["authored_by"].startswith("oracle-ir-v"))
        effect, = thaw_value(ability.effects)
        for modified in (
            replace(ability, effects=({**effect, "without_mana_cost": True},)),
            replace(ability, effects=({**effect, "artifact_graveyard_cast_version": True},)),
            replace(ability, effects=({**effect, "card": "$source"},)),
            replace(ability, effects=({**effect, "owned_only": False},)),
            replace(ability, event="resolve", handlers=()),
            replace(ability, active_zone="graveyard"),
            replace(ability, target_schema={**ability.target_schema, "owner": "opponent"}),
            replace(ability, target_schema={**ability.target_schema, "count": True}),
            replace(ability, cost_schema={**ability.cost_schema, "tap_source": False}),
        ):
            with self.subTest(modified=modified):
                binding = bind_semantic_program_runtime(modified, capability_registry=self.registry, profile=self.profile)
                self.assertFalse(binding["strict"], binding)
        deps = tuple(cap for cap in ability.capability_dependencies if cap != ARTIFACT_GRAVEYARD_CAST_CAPABILITY)
        without_authority = replace(ability, capability_dependencies=deps,
                                    capability_closure=self.registry.closure(deps, profile=self.profile).to_dict())
        binding = bind_semantic_program_runtime(without_authority, capability_registry=self.registry, profile=self.profile)
        self.assertIn("capability:undeclared_runtime_dependency:" + ARTIFACT_GRAVEYARD_CAST_CAPABILITY, binding["blockers"])

        text = "{T}: Choose target artifact card in your graveyard. You may cast that card this turn."
        alternate = replace(query_record(text), type_line="Artifact", power=None, toughness=None)
        self.assertEqual("exact", compile_oracle_card(alternate, **options).status)
        for wording in (
            text.replace("your graveyard", "a graveyard"),
            text.replace("artifact card", "creature card"),
            text.replace("cast that card", "play that card"),
            text.replace("this turn", "during your next turn"),
            text.replace("{T}", "{1}, {T}"),
        ):
            mutated = compile_oracle_card(replace(alternate, oracle_text=wording), **options)
            self.assertFalse(any(ARTIFACT_GRAVEYARD_CAST_CAPABILITY in node.capability_dependencies
                                 for node in mutated.faces[0].nodes))
        owner = "quorune.compiler.loot_transform_graveyard_templates.artifact_graveyard_cast_dependencies"
        with patch(owner, return_value=()):
            self.assertNotEqual("exact", compile_oracle_card(alternate, **options).status)

    def test_actual_affinity_price_and_entry_mill_use_independent_owners(self):
        session = self.session(73240711)
        emry = self.add(session.engine, self.name, zone="hand")
        self.add(session.engine, "Tormod's Crypt")
        self.add(session.engine, "Vault Skirge")
        self.add(session.engine, "Sol Ring", seat="B")
        self.assert_closed(session, emry)
        self.prepare(session, mana={"U": 1})
        self.checkpoint(session)
        before = len(session.state.players["A"].zones["library"])
        offer = self.offered(session, "cast", emry)
        self.assertIsNotNone(offer)
        self.cast(session, "A", emry)
        self.assertFalse(any(session.state.players["A"].mana_pool.values()))
        self.resolve_top(session)
        self.assertEqual("battlefield", session.state.cards[emry.object_id].zone)
        self.assertEqual(1, self.trigger_count(session, emry))
        self.resolve_top(session)
        self.assertEqual(before - 4, len(session.state.players["A"].zones["library"]))
        self.replay(session, load=True)

    def test_paid_cast_window_survives_source_departure_and_never_revives(self):
        session = self.session(73240712)
        source = self.recovered_source(session)
        ring = self.add(session.engine, "Sol Ring", zone="graveyard")
        crypt = self.add(session.engine, "Tormod's Crypt", zone="graveyard")
        enemy = self.add(session.engine, "Sol Ring", seat="B", zone="graveyard", ref="emry-foreign-ring")
        nonartifact = self.add(session.engine, "Grizzly Bears", zone="graveyard", ref="emry-nonartifact")
        forest = self.add(session.engine, "Forest", zone="hand")
        murder = self.add(session.engine, "Murder", seat="B", zone="hand")
        for index in range(3):
            self.add(session.engine, "Swamp", seat="B", ref=f"emry-response-swamp-{index}")
        self.prepare(session)
        self.checkpoint(session)
        offered = self.offered(session, "activate", source)
        before = authoritative_state_hash(session.state)
        for invalid in (enemy, nonartifact):
            attempted = session.act("pilot:A", {"action_id": offered["id"], "targets": [invalid.ref], "pay": "auto"})
            self.assertFalse(attempted.ok)
            self.assertEqual(before, authoritative_state_hash(session.state))
        self.activate_card(session, source, ring)
        self.cast(session, "B", murder, [source.ref])
        session = self.replay(session, load=True)
        self.resolve_top(session)
        self.assertEqual("graveyard", session.state.cards[source.object_id].zone)
        self.resolve_top(session)
        ring_permission = session.state.cards[ring.object_id].annotations["temporary_play_permission"]
        self.assertEqual(("A", "graveyard", False, False, True),
                         tuple(ring_permission[key] for key in ("player", "zone", "without_mana_cost", "allow_land", "allow_spell")))
        self.assertNotIn("temporary_play_permission", session.state.cards[crypt.object_id].annotations)
        grant, = (event for event in session.state.events if event.code == "permission.cast"
                  and event.details.get("object") == ring.ref)
        self.assertEqual({"A", "analyst"}, set(grant.visibility))
        self.priority(session, "A")
        self.assertIsNone(self.offered(session, "cast", ring))
        rejected = session.act("pilot:A", {"action_id": f"cast:{ring.ref}", "pay": "auto"})
        self.assertFalse(rejected.ok)
        land_offer = self.offered(session, "play_land", forest)
        self.assertIsNotNone(land_offer)
        played = session.act("pilot:A", {"action_id": land_offer["id"]})
        self.assertTrue(played.ok, played.summary)
        self.cast(session, "A", ring)
        self.assertTrue(session.state.cards[forest.object_id].tapped)
        self.resolve_top(session)
        self.assertEqual("battlefield", session.state.cards[ring.object_id].zone)
        self.replay(session, load=True)

        replayable = self.session(73240713)
        source = self.recovered_source(replayable)
        crypt = self.add(replayable.engine, "Tormod's Crypt", zone="graveyard")
        self.prepare(replayable)
        self.checkpoint(replayable)
        self.activate_card(replayable, source, crypt)
        self.resolve_top(replayable)
        self.cast(replayable, "A", crypt)
        self.resolve_top(replayable)
        current = replayable.state.cards[crypt.object_id]
        self.assertEqual("battlefield", current.zone)
        self.activate_card_for_player(replayable, current, "B")
        self.resolve_top(replayable)
        current = replayable.state.cards[crypt.object_id]
        self.assertEqual("graveyard", current.zone)
        self.assertNotIn("temporary_play_permission", current.annotations)
        self.assertIsNone(self.offered(replayable, "cast", current))
        self.replay(replayable, load=True)

    def activate_card_for_player(self, session, source, seat):
        self.priority(session, "A")
        offer = self.offered(session, "activate", source)
        self.assertIsNotNone(offer)
        result = session.act("pilot:A", {"action_id": offer["id"], "targets": [seat], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)

    def test_actual_returned_incarnation_and_artifact_land_have_distinct_legality(self):
        session = self.session(73240714)
        source = self.recovered_source(session)
        artifact = self.add(session.engine, "Sol Ring", zone="graveyard")
        revival = self.add(session.engine, "Noxious Revival", zone="hand")
        scour = self.add(session.engine, "Thought Scour", zone="hand")
        original_identity = artifact.logical_object_id
        self.prepare(session, mana={"G": 1, "U": 1})
        self.checkpoint(session)
        self.activate_card(session, source, artifact)
        self.priority(session, "A")
        revive_offer = self.offered(session, "cast", revival)
        mana_option = next(option for option in revive_offer["cost_options"] if option["requirements"]["G"] == 1)
        revived = session.act("pilot:A", {
            "action_id": revive_offer["id"], "cost_option": mana_option["id"],
            "targets": [artifact.ref], "pay": "auto",
        })
        self.assertTrue(revived.ok, revived.summary)
        self.resolve_top(session)
        self.assertEqual("library", session.state.cards[artifact.object_id].zone)
        self.cast(session, "A", scour, ["A"])
        self.resolve_top(session)
        returned = session.state.cards[artifact.object_id]
        self.assertEqual("graveyard", returned.zone)
        self.assertNotEqual(original_identity, returned.logical_object_id)
        session = self.replay(session, load=True)
        self.resolve_top(session)
        self.assertNotIn("temporary_play_permission", session.state.cards[artifact.object_id].annotations)
        self.replay(session, load=True)

        session = self.session(73240715)
        source = self.recovered_source(session)
        land = self.add(session.engine, "Seat of the Synod", zone="graveyard")
        self.prepare(session)
        self.checkpoint(session)
        self.activate_card(session, source, land)
        self.resolve_top(session)
        self.assertIn("temporary_play_permission", session.state.cards[land.object_id].annotations)
        self.assertIsNone(self.offered(session, "play_land", land))
        self.assertIsNone(self.offered(session, "cast", land))
        self.replay(session, load=True)


    def test_artifact_graveyard_grant_obeys_off_turn_timing_and_expires(self):
        session = self.session(73240716)
        source = self.recovered_source(session)
        self.add(session.engine, "Island")
        ring = self.add(session.engine, "Sol Ring", zone="graveyard")
        self.prepare(session, active="B", actor="A")
        self.checkpoint(session)
        turn = session.state.turn_sequence
        self.activate_card(session, source, ring)
        self.resolve_top(session)
        permission = session.state.cards[ring.object_id].annotations["temporary_play_permission"]
        self.assertEqual(turn, permission["turn_sequence"])
        self.priority(session, "A")
        self.assertIsNone(self.offered(session, "cast", ring))
        self.until(session, lambda current: current.state.turn_sequence > turn)
        self.assertNotIn("temporary_play_permission", session.state.cards[ring.object_id].annotations)
        self.replay(session, load=True)
