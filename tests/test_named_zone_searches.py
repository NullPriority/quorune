from __future__ import annotations

"""CR 701.23/701.24: choose searched zones before opening a hidden library."""

from dataclasses import replace
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.card_programs.binding import bind_card_program_runtime
from quorune.oracle_ir import compile_oracle_card, register_generated_programs
from quorune.deck import DeckDefinition, DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantics import SemanticRegistry
from scripts.build_test_database import build_fixture_database
import test_bound_effect_programs as witnesses
import test_attachment_followup_bindings as attachment_controls


EXCLUDED = ("Ajani's Aid", "Angrath's Fury", "Chandra's Flame Wave", "Dovin's Dismissal",
            "Elspeth, Undaunted Hero", "Grand Master of Flowers", "The First Doctor")
FIXTURE = ROOT / "tests/fixtures/named-zone-search-cards.json"


class NamedZoneSearchModelTests(unittest.TestCase):
    def test_names_and_optionality_have_an_explicit_immutable_codec(self):
        from quorune.named_search_model import NamedZoneSearchSpec, decode_named_zone_search
        from quorune.replacement.immutable import FrozenMap
        for optional in (False, True):
            value = NamedZoneSearchSpec("Chandra, Flame's Catalyst", optional)
            effect = value.instruction()
            self.assertEqual(value, decode_named_zone_search(FrozenMap(effect)))
            self.assertEqual(value, NamedZoneSearchSpec.from_dict(FrozenMap(value.to_dict())))
            with self.assertRaises(ValueError):decode_named_zone_search({**effect, "unexpected": True})
            with self.assertRaises(ValueError):decode_named_zone_search({**effect, "player": "B"})
        for name in ("", "bad\nname", 4):
            with self.subTest(name=name), self.assertRaises(ValueError):NamedZoneSearchSpec(name, True)
        with self.assertRaises(ValueError):NamedZoneSearchSpec("A card", 1)

    def test_zone_decision_keeps_public_search_obligations_and_conditional_shuffle(self):
        from quorune.named_search_model import NamedZoneSearchSpec
        value = NamedZoneSearchSpec("Command Tower", False)
        for choice, zones in (("library", ["library"]), ("graveyard", ["graveyard"]),
                              ("both", ["library", "graveyard"])):
            effect = value.search_instruction(choice)
            self.assertEqual(zones, effect["zone"])
            self.assertEqual("library" in zones, effect["shuffle_after"])
            self.assertEqual({"minimum": 1, "maximum": 1}, effect["count"])
            self.assertFalse(effect["optional"])
            self.assertTrue(effect["reveal"])
            self.assertEqual({"names": ["Command Tower"]}, effect["selector"])
        for choice in ("all", "exile", "hand", "decline", [], None):
            with self.subTest(choice=choice), self.assertRaises(ValueError):value.search_instruction(choice)

    def test_public_minimum_and_version6_codec_do_not_change_historical_searches(self):
        from copy import deepcopy
        from types import SimpleNamespace
        from quorune.named_search_model import NamedZoneSearchSpec, decode_named_zone_selection
        from quorune.rules.named_searches import named_zone_search_minimum
        from quorune.selection.searching import HiddenSearchOwnerMixin
        spec = NamedZoneSearchSpec("Command Tower", False)
        host = SimpleNamespace(state=SimpleNamespace(cards={"g": SimpleNamespace(ref="grave-match")},
            players={"A": SimpleNamespace(zones={"graveyard": ["g"]})}))
        for choice in ("library", "graveyard", "both"):
            effect = spec.search_instruction(choice)
            self.assertEqual(spec, decode_named_zone_selection(effect)[0])
            options = [{"id": "grave-match"}] if choice != "library" else [{"id": "library-match"}]
            self.assertEqual(int(choice != "library"), named_zone_search_minimum(host, "A", effect, options))
            self.assertEqual(int(choice != "library"), named_zone_search_minimum(host, "A", effect,
                {row["id"] for row in options}))
            self.assertEqual(0, named_zone_search_minimum(host, "A", effect, []))
            HiddenSearchOwnerMixin._validate_counted_search_effect(effect)
        effect = spec.search_instruction("both")
        for key, value in (("zone", ["hand"]), ("optional", True), ("shuffle_after", False),
                           ("destination", "battlefield"), ("schema_version", True)):
            bad = deepcopy(effect);bad[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):decode_named_zone_selection(bad)
        legacy = {key:value for key,value in effect.items() if key != "schema_version"}
        with patch("quorune.named_search_model.decode_named_zone_selection", side_effect=AssertionError("legacy changed")):
            HiddenSearchOwnerMixin._validate_counted_search_effect(legacy)

    def test_complete_oracle_grammar_is_closed_before_existing_search_owners(self):
        from quorune.compiler.named_search_templates import named_zone_search_template
        for first, optional in (("Search", False), ("You may search", True)):
            for connector, tense in (("and ", "search"), ("then ", "searched")):
                body = f"{first} your library and/or graveyard for a card named Huatli, Dinosaur Knight, reveal it, {connector}put it into your hand. If you {tense} your library this way, shuffle."
                compiled = named_zone_search_template(body)
                self.assertIsNotNone(compiled)
                effect, = compiled.compiled()[1]
                self.assertEqual("Huatli, Dinosaur Knight", effect["search"]["name"])
                self.assertIs(optional, effect["search"]["optional"])
        for text in (
            "Search your library and/or graveyard for a card named Sunlit Hoplite and put it onto the battlefield. If you search your library this way, shuffle.",
            "Search your library for a card named Command Tower, reveal it, and put it into your hand, then shuffle.",
            "Search target player's library and/or graveyard for a card named Command Tower, reveal it, and put it into your hand. If you search your library this way, shuffle.",
            "Search your library and/or graveyard for a creature card, reveal it, and put it into your hand. If you search your library this way, shuffle.",
        ):
            with self.subTest(text=text):self.assertIsNone(named_zone_search_template(text))


class NamedZoneSearchCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.registry = load_default_capability_registry()

    def compile(self, text):
        return compile_oracle_card(replace(witnesses.record(text), name="Named Search Witness"),
            capability_registry=self.registry, capability_profile="commander_review")

    def test_zero_target_bounded_prefix_and_entire_search_tail_keep_the_original_target_schema(self):
        for prefix in ("Put a +1/+1 counter on each of up to two target creatures.",
                       "Return up to two target creatures to their owner's hand.",
                       "Destroy target creature."):
            body = prefix + " You may search your library and/or graveyard for a card named Jace, Arcane Strategist, reveal it, and put it into your hand. If you search your library this way, shuffle."
            with self.subTest(prefix=prefix):
                ir = self.compile(body)
                self.assertEqual("exact", ir.status, ir.material_residuals)
                node, = ir.faces[0].nodes
                self.assertEqual("search_named_library_or_graveyard", node.effects[-1]["op"])
                self.assertEqual(2 if "two" in prefix else 1,
                    node.target_schema.get("up_to", node.target_schema.get("count")))
                self.assertEqual(body[node.span.start:node.span.end], node.text)

    def test_compiler_owner_omissions_and_unknown_suffixes_remain_material(self):
        text = "You may search your library and/or graveyard for a card named Jace, Arcane Strategist, reveal it, and put it into your hand. If you search your library this way, shuffle."
        self.assertEqual("exact", self.compile(text).status)
        with patch("quorune.compiler.named_search_templates.named_zone_search_template", return_value=None):
            self.assertNotEqual("exact", self.compile(text).status)
        text = "Destroy target creature. " + text
        self.assertEqual("exact", self.compile(text).status)
        with patch("quorune.rules.named_searches.named_zone_search_node_capabilities", return_value=()):
            self.assertNotEqual("exact", self.compile(text).status)
        self.assertNotEqual("exact", self.compile(text + " Then create an unrepresented object.").status)

    def test_dependency_withdrawals_and_wrong_named_zone_descriptors_fail_closed(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.named_searches import named_zone_search_node_capabilities
        text = "Search your library and/or graveyard for a card named Command Tower, reveal it, and put it into your hand. If you search your library this way, shuffle."
        self.assertEqual("exact", self.compile(text).status)
        for missing in ("library.search.named_library_graveyard", "library.search.restriction.static",
                        "zone.change.destination_replacement"):
            raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_text(encoding="utf-8"))
            next(row for row in raw["capabilities"] if row["id"] == missing).update(status="blocked", blockers=["named search owner withdrawn"])
            registry = CapabilityRegistry(raw);registry.mark_evidence_verified(self.registry.evidence_fingerprint)
            ir = compile_oracle_card(witnesses.record(text), capability_registry=registry, capability_profile="commander_review")
            with self.subTest(missing=missing):self.assertNotEqual("exact", ir.status)
        node, = self.compile(text).faces[0].nodes
        self.assertFalse(named_zone_search_node_capabilities(effects=node.effects,
            target_schema={"zones": ["player"], "count": 1}, mechanic_ids=node.mechanics))


class NamedZoneSearchRegistrationTests(unittest.TestCase):
    def test_complete_original_sources_preserve_all_unsupported_siblings(self):
        registry = load_default_capability_registry();semantics = SemanticRegistry()
        source_names = [row["name"] for row in json.loads(FIXTURE.read_text(encoding="utf-8"))["cards"]
                        if "search your library and/or graveyard for a card named" in row.get("oracle_text", "").casefold()]
        self.assertEqual(38, len(source_names))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "full-named-sources.sqlite3"
            build_fixture_database([FIXTURE], path)
            with CardDatabase(path) as db:
                for name in source_names:
                    with self.subTest(name=name):
                        card = db.lookup(name, fuzzy=False)
                        register_generated_programs(db, semantics, (card,), trust_level="provisional",
                            capability_registry=registry, capability_profile="commander_review",
                            promote_exact_runtime_handlers=True, promote_exact_trigger_programs=True,
                            promote_exact_effect_programs=True, promote_exact_capability_declarations=True)
                        program = compile_best_available_card_program(db, card, semantic_registry=semantics,
                            capability_registry=registry, capability_profile="commander_review")
                        bound = bind_card_program_runtime(program, capability_registry=registry, profile="commander_review")
                        self.assertEqual(name not in EXCLUDED, bool(bound["strict_capability_ready"]
                            and bound["trust_basis"] == "capability_closed"), bound["blockers"])


class NamedZoneSearchRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = attachment_controls.AttachmentFollowupRuntimeTests.session
    add = attachment_controls.AttachmentFollowupRuntimeTests.add
    prepare = attachment_controls.AttachmentFollowupRuntimeTests.prepare
    closed = attachment_controls.AttachmentFollowupRuntimeTests.closed
    actions = attachment_controls.AttachmentFollowupRuntimeTests.actions
    cast_offered = attachment_controls.AttachmentFollowupRuntimeTests.cast_offered
    checkpoint = attachment_controls.AttachmentFollowupRuntimeTests.checkpoint
    resolve = attachment_controls.AttachmentFollowupRuntimeTests.resolve
    replay = attachment_controls.AttachmentFollowupRuntimeTests.replay
    until = attachment_controls.AttachmentFollowupRuntimeTests.until

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "named-zone-search.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/bound-effect-program-cards.json", FIXTURE], path)
        cls.db = CardDatabase(path);cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Named search witnesses", [DeckEntry("Generic Bound Commander", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30)], ["Generic Bound Commander"])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()

    def world(self, seed, source="Ashiok's Forerunner", named="Ashiok, Sculptor of Fears", *,
              library=True, graveyard=True, controls=(), response=None):
        session = self.session(seed)
        spell = self.add(session.engine, source, zone="hand")
        found_library = self.add(session.engine, named, zone="library", ref="named-library-result") if library else None
        found_graveyard = self.add(session.engine, named, zone="graveyard", ref="named-graveyard-result") if graveyard else None
        extra = [self.add(session.engine, name, seat=seat, ref=f"named-control-{i}")
                 for i, (name, seat) in enumerate(controls)]
        reply = self.add(session.engine, response, zone="hand") if response else None
        self.prepare(session, spell, {color: 20 for color in ("C", "W", "U", "B", "R", "G")})
        return session, spell, found_library, found_graveyard, extra, reply

    def packet(self, session):return session.packet("pilot:A", full=True)["decision"]["ctx"]

    def zones(self, session, spell, choice, *, targets=()):
        self.cast_offered(session, spell, targets=targets)
        self.assertEqual("semantic.choice", self.resolve(session).kind)
        self.choose_zone(session, choice)

    def choose_zone(self, session, choice):
        result = session.act("pilot:A", {"action_id": "choose", "choice": choice})
        self.assertTrue(result.ok, result.summary)
        return result

    def take(self, session, refs):
        self.assertEqual("semantic.search", session.state.pending_decision.kind)
        result = session.act("pilot:A", {"action_id": "choose", "search_cards": list(refs)})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(session))

    def shuffles(self, session):return session.state.players["A"].stats.get("shuffle_count", 0)

    def reject(self, session, principal, response):
        before = authoritative_state_hash(session.state)
        result = session.act(principal, response)
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(session.state))

    def test_graveyard_only_and_decline_never_open_or_shuffle_the_library(self):
        for index, choice in enumerate(("graveyard", "decline")):
            with self.subTest(choice=choice):
                session, spell, library, graveyard, _, _ = self.world(295100 + index)
                self.checkpoint(session);order = tuple(session.state.players["A"].zones["library"])
                before = self.shuffles(session)
                self.cast_offered(session, spell)
                self.assertEqual("semantic.choice", self.resolve(session).kind)
                for seat in "ABCD":
                    self.assertNotIn(library.ref, str(session.packet("pilot:" + seat, full=True)["decision"]))
                self.reject(session, "pilot:B", {"action_id": "choose", "choice": choice})
                session = self.replay(session, load=True)
                self.choose_zone(session, choice)
                if choice == "graveyard":
                    packet = self.packet(session)
                    self.assertEqual(["graveyard"], packet["search_spec"]["zone"])
                    self.assertNotIn(library.ref, str(packet))
                    self.reject(session, "pilot:A", {"action_id": "choose", "search_cards": [library.ref]})
                    self.reject(session, "pilot:A", {"action_id": "choose", "search_cards": []})
                    self.take(session, [graveyard.ref])
                    self.assertEqual("hand", session.state.cards[graveyard.object_id].zone)
                    event = next(e for e in reversed(session.state.events) if e.code == "library.search")
                    self.assertEqual(["graveyard"], event.details["searched_zones"])
                else:
                    self.assertIsNone(self.resolve(session))
                    self.assertEqual("graveyard", session.state.cards[graveyard.object_id].zone)
                self.assertEqual(before, self.shuffles(session))
                self.assertEqual(order, tuple(session.state.players["A"].zones["library"]))
                self.replay(session, load=True)

    def test_library_quality_may_fail_even_with_a_match_and_still_shuffles(self):
        session, spell, library, graveyard, _, _ = self.world(295110)
        self.checkpoint(session);before = self.shuffles(session)
        self.zones(session, spell, "library")
        packet = self.packet(session);schema = packet["legal_actions"][0]["choice_schema"]
        self.assertEqual(0, schema["minimum"]);self.assertIn(library.ref, schema["legal_refs"])
        self.assertNotIn(graveyard.ref, schema["legal_refs"])
        for seat in "BCD":self.assertNotIn(library.ref, str(session.packet("pilot:" + seat, full=True)))
        session = self.replay(session, load=True)
        self.take(session, [])
        self.assertEqual(before + 1, self.shuffles(session))
        self.assertEqual("library", session.state.cards[library.object_id].zone)
        self.replay(session, load=True)

    def test_searching_both_requires_a_public_match_but_allows_either_found_card(self):
        for index, chosen_zone in enumerate(("library", "graveyard")):
            with self.subTest(chosen_zone=chosen_zone):
                session, spell, library, graveyard, _, _ = self.world(295120 + index)
                self.checkpoint(session);before = self.shuffles(session)
                self.zones(session, spell, "both")
                schema = self.packet(session)["legal_actions"][0]["choice_schema"]
                self.assertEqual(1, schema["minimum"])
                self.assertEqual({library.ref, graveyard.ref}, set(schema["legal_refs"]))
                self.reject(session, "pilot:A", {"action_id": "choose", "search_cards": []})
                self.reject(session, "pilot:A", {"action_id": "choose", "search_cards": [library.ref, graveyard.ref]})
                selected = library if chosen_zone == "library" else graveyard
                self.take(session, [selected.ref])
                self.assertEqual("hand", session.state.cards[selected.object_id].zone)
                self.assertEqual(before + 1, self.shuffles(session))
                event = next(e for e in reversed(session.state.events) if e.code == "library.search")
                self.assertEqual(["library", "graveyard"], event.details["searched_zones"])
                self.replay(session, load=True)

    def test_real_library_prohibition_leaves_a_mandatory_public_search_and_no_shuffle(self):
        session, spell, library, graveyard, controls, _ = self.world(295130, "Tower Winder", "Command Tower",
            controls=(("Mindlock Orb", "B"),))
        self.closed(session, controls[0]);self.checkpoint(session);before = self.shuffles(session)
        self.cast_offered(session, spell)
        self.assertEqual("semantic.choice", self.resolve(session).kind)
        self.reject(session, "pilot:A", {"action_id": "choose", "choice": "library"})
        self.reject(session, "pilot:A", {"action_id": "choose", "choice": "both"})
        self.reject(session, "pilot:A", {"action_id": "choose", "choice": "decline"})
        self.choose_zone(session, "graveyard")
        self.assertNotIn(library.ref, str(self.packet(session)))
        self.take(session, [graveyard.ref]);self.assertEqual(before, self.shuffles(session))
        self.replay(session, load=True)

    def test_real_aven_window_does_not_restrict_public_graveyard_and_shuffles_entire_library(self):
        session, spell, library, graveyard, controls, _ = self.world(295140, "Tower Winder", "Command Tower",
            controls=(("Aven Mindcensor", "B"),))
        for index in range(4):self.add(session.engine, "Generic Bound Plains", zone="library", ref=f"named-window-{index}")
        self.closed(session, controls[0]);self.checkpoint(session);before = self.shuffles(session)
        self.zones(session, spell, "both")
        packet = self.packet(session);schema = packet["legal_actions"][0]["choice_schema"]
        self.assertEqual([graveyard.ref], schema["legal_refs"])
        self.assertNotIn(library.ref, str(packet))
        self.reject(session, "pilot:A", {"action_id": "choose", "search_cards": [library.ref]})
        self.take(session, [graveyard.ref]);self.assertEqual(before + 1, self.shuffles(session))
        self.assertEqual("library", session.state.cards[library.object_id].zone)
        self.replay(session, load=True)

    def test_original_mount_equipment_artifact_and_enchantment_enter_searches(self):
        cases = (("Sun-Blessed Mount", "Huatli, Dinosaur Knight"),
                 ("Raven Clan War-Axe", "Eivor, Battle-Ready"),
                 ("Visage of Bolas", "Nicol Bolas, the Deceiver"),
                 ("Gideon's Resolve", "Gideon, Martial Paragon"))
        for index, (source, named) in enumerate(cases):
            with self.subTest(source=source):
                session, spell, _, graveyard, _, _ = self.world(295150 + index, source, named, library=False)
                self.checkpoint(session);self.zones(session, spell, "graveyard")
                self.take(session, [graveyard.ref]);self.assertEqual("battlefield", session.state.cards[spell.object_id].zone)
                self.assertEqual("hand", session.state.cards[graveyard.object_id].zone)
                self.replay(session, load=True)

    def test_zero_targets_and_partially_illegal_target_sets_preserve_search(self):
        session, spell, _, graveyard, _, _ = self.world(295160, "Jace's Ruse", "Jace, Arcane Strategist", library=False)
        self.checkpoint(session);self.zones(session, spell, "graveyard", targets=())
        self.take(session, [graveyard.ref]);self.replay(session, load=True)
        session, spell, _, graveyard, _, blink = self.world(295161, "Basri's Aegis", "Basri, Devoted Paladin",
            library=False, response="Cloudshift")
        first = self.add(session.engine, "Generic Bound Body", ref="named-first-target")
        second = self.add(session.engine, "Generic Bound Body", ref="named-second-target")
        self.prepare(session, spell, {color: 20 for color in ("C", "W", "U", "B", "R", "G")})
        self.checkpoint(session)
        self.cast_offered(session, spell, targets=(first.ref, second.ref))
        self.cast_offered(session, blink, targets=(first.ref,))
        self.assertEqual("semantic.choice", self.resolve(session).kind)
        self.assertEqual(0, session.state.cards[first.object_id].counters.get("+1/+1", 0))
        self.assertEqual(1, session.state.cards[second.object_id].counters.get("+1/+1", 0))
        self.choose_zone(session, "graveyard");self.take(session, [graveyard.ref]);self.replay(session, load=True)

    def test_all_targets_illegal_stops_search_but_failed_destruction_does_not(self):
        session, spell, _, graveyard, _, blink = self.world(295170, "Liliana's Scorn", "Liliana, Death Mage",
            library=False, response="Cloudshift")
        target = self.add(session.engine, "Generic Bound Body")
        self.prepare(session, spell, {color: 20 for color in ("C", "W", "U", "B", "R", "G")})
        self.checkpoint(session);before = self.shuffles(session)
        self.cast_offered(session, spell, targets=(target.ref,));self.cast_offered(session, blink, targets=(target.ref,))
        self.assertIsNone(self.resolve(session));self.assertEqual("graveyard", session.state.cards[graveyard.object_id].zone)
        self.assertEqual(before, self.shuffles(session));self.replay(session, load=True)
        session, spell, _, graveyard, controls, _ = self.world(295171, "Liliana's Scorn", "Liliana, Death Mage",
            library=False, controls=(("Darksteel Myr", "A"),))
        self.closed(session, controls[0]);self.checkpoint(session)
        self.zones(session, spell, "graveyard", targets=(controls[0].ref,))
        self.assertEqual("battlefield", session.state.cards[controls[0].object_id].zone)
        self.take(session, [graveyard.ref]);self.replay(session, load=True)

    def test_destroyed_target_toughness_life_result_precedes_the_named_search(self):
        session, spell, _, graveyard, _, _ = self.world(295180, "Vraska's Stoneglare", "Vraska, Regal Gorgon", library=False)
        target = self.add(session.engine, "Generic Bound Body", seat="B")
        toughness = int(self.db.lookup("Generic Bound Body", fuzzy=False).toughness)
        self.prepare(session, spell, {color: 20 for color in ("C", "W", "U", "B", "R", "G")})
        self.checkpoint(session);before = session.state.players["A"].life
        self.zones(session, spell, "graveyard", targets=(target.ref,))
        self.assertEqual("graveyard", session.state.cards[target.object_id].zone)
        self.assertEqual(before + toughness, session.state.players["A"].life)
        self.take(session, [graveyard.ref]);self.replay(session, load=True)

    def test_commander_search_result_waits_for_replacement_and_replays_while_pending(self):
        session, spell, found, _, _, _ = self.world(295190, "Raven Clan War-Axe", "Eivor, Battle-Ready", graveyard=False)
        for card in session.state.cards.values():
            if card.owner == "A" and card.is_commander:card.is_commander = False;card.commander_designation_id = None
        found.is_commander = True;found.commander_designation_id = "commander:" + found.object_id
        session.state.commander_oracle_ids["A"] = [found.oracle_id]
        self.checkpoint(session);self.zones(session, spell, "library")
        result = session.act("pilot:A", {"action_id": "choose", "search_cards": [found.ref]})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("replacement.order", session.state.pending_decision.kind)
        self.assertEqual("library", session.state.cards[found.object_id].zone)
        options = self.packet(session)["options"]
        apply = next(option["id"] for option in options if not option["id"].startswith("decline:"))
        self.reject(session, "pilot:B", {"action_id": "choose", "choices": {"replacement": apply}})
        session = self.replay(session, load=True)
        result = session.act("pilot:A", {"action_id": "choose", "choices": {"replacement": apply}})
        self.assertTrue(result.ok, result.summary);self.assertIsNone(self.resolve(session))
        self.assertEqual("command", session.state.cards[found.object_id].zone)
        self.replay(session, load=True)

    def test_payload_minima_are_required_even_without_coverage(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        session, spell, _, _, _, _ = self.world(295200)
        program = next(p for p in session.engine.semantics.programs_for_oracle(spell.oracle_id)
                       if any(e.get("op") == "search_named_library_or_graveyard" for e in p.effects))
        missing = "library.search.named_library_graveyard"
        deps = tuple(c for c in program.capability_dependencies if c != missing)
        changed = replace(program, coverage=(), capability_dependencies=deps,
            capability_closure=self.registry.closure(deps, profile="commander_review").to_dict())
        bound = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile="commander_review")
        self.assertFalse(bound["strict"]);self.assertIn("capability:undeclared_runtime_dependency:" + missing, bound["blockers"])

    def test_source_control_change_does_not_redirect_the_search_or_private_choice(self):
        session, spell, _, graveyard, _, _ = self.world(295205, library=False)
        control = self.add(session.engine, "Act of Aggression", seat="B", zone="hand")
        opposing = self.add(session.engine, "Ashiok, Sculptor of Fears", seat="B", zone="graveyard", ref="other-players-match")
        session.state.players["B"].mana_pool.update({"R": 5, "C": 5})
        self.closed(session, control);self.checkpoint(session)
        self.cast_offered(session, spell)
        self.until(session, lambda: session.state.cards[spell.object_id].zone == "battlefield"
            and bool(session.state.stack))
        for _ in range(4):
            if session.pending_principals() == ["pilot:B"]:break
            principal = session.pending_principals()[0]
            result = session.act(principal, {"action_id": "pass"});self.assertTrue(result.ok, result.summary)
        action = next(row for row in self.actions(session, "B") if row["id"] == "cast:" + control.ref)
        command = {"action_id": action["id"], "targets": [spell.ref], "pay": "auto"}
        if action.get("cost_options"):command["cost_option"] = action["cost_options"][0]["id"]
        result = session.act("pilot:B", command)
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("semantic.choice", self.resolve(session).kind)
        self.assertEqual("B", session.state.cards[spell.object_id].controller)
        self.assertEqual(["pilot:A"], session.pending_principals())
        self.reject(session, "pilot:B", {"action_id": "choose", "choice": "graveyard"})
        self.choose_zone(session, "graveyard")
        self.assertNotIn(opposing.ref, str(self.packet(session)))
        self.take(session, [graveyard.ref])
        self.assertEqual("hand", session.state.cards[graveyard.object_id].zone)
        self.assertEqual("graveyard", session.state.cards[opposing.object_id].zone)
        self.replay(session, load=True)

    def test_public_graveyard_minimum_omission_is_killed_in_a_fresh_real_search(self):
        session, spell, _, _, _, _ = self.world(295210)
        self.checkpoint(session)
        with patch("quorune.rules.named_searches.named_zone_search_minimum", return_value=0):
            self.zones(session, spell, "both")
            result = session.act("pilot:A", {"action_id": "choose", "search_cards": []})
            with self.assertRaises(AssertionError):self.assertFalse(result.ok)



class CastXZoneSearchModelTests(unittest.TestCase):
    def test_cast_x_search_codecs_reject_crossed_and_mutated_payloads(self):
        from copy import deepcopy
        from quorune.cast_x_search_model import (
            CastXZoneSearchSelection, cast_x_zone_search_effect, decode_cast_x_zone_search,
        )
        from quorune.named_search_model import NamedZoneSearchSpec, decode_named_zone_selection
        from quorune.replacement.immutable import FrozenMap
        from quorune.selection.searching import HiddenSearchOwnerMixin

        source = cast_x_zone_search_effect()
        self.assertIsInstance(decode_cast_x_zone_search(FrozenMap(source)), FrozenMap)
        self.assertEqual(0, decode_cast_x_zone_search(cast_x_zone_search_effect(maximum=0, player="A"), actor="A"))
        with self.assertRaises(ValueError):
            decode_cast_x_zone_search(cast_x_zone_search_effect(maximum=True, player="A"), actor="A")
        selection = CastXZoneSearchSelection(("library", "graveyard"), 10)
        valid = selection.effect()
        self.assertEqual(selection, CastXZoneSearchSelection.from_effect(FrozenMap(valid)))
        HiddenSearchOwnerMixin._validate_counted_search_effect(valid)
        for field, wrong in (
            ("destination", "hand"), ("zone", ["graveyard", "library"]), ("shuffle_after", False),
            ("selector", {"types": ["creature"], "mana_value": {"maximum": True}}),
            ("count", {"minimum": True, "maximum": 1}), ("schema_version", 28.0),
        ):
            with self.subTest(field=field), self.assertRaises(ValueError):
                CastXZoneSearchSelection.from_effect({**deepcopy(valid), field: wrong})
        named = NamedZoneSearchSpec("Generic Bound Commander", False).search_instruction("both")
        with self.assertRaises(ValueError):
            CastXZoneSearchSelection.from_effect({**named, "schema_version": 28})
        with self.assertRaises(ValueError):
            decode_named_zone_selection({**valid, "schema_version": 6})

    def test_cast_x_search_requires_the_complete_announced_spell(self):
        from quorune.cast_x_search_model import CAST_X_ZONE_SEARCH_OPERATION
        from quorune.rules.capabilities import CapabilityRegistry

        cards = json.loads((ROOT / "tests/fixtures/scryfall-exact-lists.json").read_text(encoding="utf-8"))["cards"]
        original = next(row for row in cards if row["name"] == "Finale of Devastation")
        card = replace(witnesses.record(original["oracle_text"]), mana_cost=original["mana_cost"])
        registry = load_default_capability_registry()

        def compile_card(source, caps=registry):
            return compile_oracle_card(source, capability_registry=caps, capability_profile="commander_review")

        ir = compile_card(card)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        self.assertEqual(CAST_X_ZONE_SEARCH_OPERATION, node.effects[0]["op"])
        self.assertEqual(card.oracle_text[node.span.start:node.span.end], node.text)
        for candidate in (
            replace(card, mana_cost="{G}{G}"),
            replace(card, oracle_text=card.oracle_text.replace("your library and/or graveyard", "your library")),
            replace(card, oracle_text=card.oracle_text.replace("gain haste", "gain vigilance")),
            replace(card, oracle_text=card.oracle_text + " Then choose an unrepresented object."),
        ):
            self.assertNotEqual("exact", compile_card(candidate).status)
        with patch("quorune.rules.cast_x_zone_searches.cast_x_zone_search_node_capabilities", return_value=()):
            self.assertNotEqual("exact", compile_card(card).status)
        for missing in ("quantity_expression.declared_effect_amount", "library.search.restriction.static",
                        "zone.change.destination_replacement"):
            source = json.loads((ROOT / "quorune/rules/capability-registry.json").read_text(encoding="utf-8"))
            next(row for row in source["capabilities"] if row["id"] == missing).update(
                status="blocked", blockers=["search dependency withdrawn"])
            withdrawn = CapabilityRegistry(source)
            withdrawn.mark_evidence_verified(registry.evidence_fingerprint)
            with self.subTest(missing=missing):
                self.assertNotEqual("exact", compile_card(card, withdrawn).status)


class CastXZoneSearchRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = NamedZoneSearchRuntimeTests.session
    add = NamedZoneSearchRuntimeTests.add
    prepare = NamedZoneSearchRuntimeTests.prepare
    closed = NamedZoneSearchRuntimeTests.closed
    actions = NamedZoneSearchRuntimeTests.actions
    checkpoint = NamedZoneSearchRuntimeTests.checkpoint
    resolve = NamedZoneSearchRuntimeTests.resolve
    replay = NamedZoneSearchRuntimeTests.replay
    choose_zone = NamedZoneSearchRuntimeTests.choose_zone
    take = NamedZoneSearchRuntimeTests.take
    packet = NamedZoneSearchRuntimeTests.packet
    reject = NamedZoneSearchRuntimeTests.reject
    shuffles = NamedZoneSearchRuntimeTests.shuffles

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        database = Path(cls.temporary.name) / "cast-x-zone-search.sqlite3"
        named = json.loads(FIXTURE.read_text(encoding="utf-8"))
        named["cards"] = [row for row in named["cards"] if row["name"] == "Mindlock Orb"]
        mindlock_id = named["cards"][0]["oracle_id"]
        named["rulings"] = [row for row in named["rulings"] if row["oracle_id"] == mindlock_id]
        restricted_fixture = Path(cls.temporary.name) / "search-restriction.json"
        restricted_fixture.write_text(json.dumps(named), encoding="utf-8")
        build_fixture_database(
            [ROOT / "tests/fixtures/bound-effect-program-cards.json",
             ROOT / "tests/fixtures/scryfall-exact-lists.json", restricted_fixture], database,
        )
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Search witnesses", [
            DeckEntry("Generic Bound Commander", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def open_spell(self, seed, *, x, restricted=False):
        game = self.session(seed)
        game.state.config.semantic_policy = "trusted_only"
        spell = self.add(game.engine, "Finale of Devastation", zone="hand")
        yours = self.add(game.engine, "Generic Bound Body", ref="cast-x-existing-body")
        theirs = self.add(game.engine, "Generic Bound Body", seat="B", ref="cast-x-opposing-body")
        library = self.add(game.engine, "Generic Bound Body", zone="library", ref="cast-x-secret-result")
        grave = self.add(game.engine, "Bloodghast", zone="graveyard", ref="cast-x-public-result")
        extra = self.add(game.engine, "Sol Ring", zone="library", ref="cast-x-secret-noncreature")
        for hidden in (library, extra):
            hidden.known_to = []
            hidden.revealed_to = []
        if restricted:
            self.closed(game, self.add(game.engine, "Mindlock Orb", seat="B"))
        self.prepare(game, spell, {"C": x, "G": 2})
        self.checkpoint(game)
        action = "cast:" + spell.ref
        self.assertIn(action, {row["id"] for row in self.actions(game)})
        result = game.act("pilot:A", {"action_id": action, "x": x, "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(x, game.state.stack[-1].x_value)
        self.assertEqual(0, sum(game.state.players["A"].mana_pool.values()))
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        for seat in "ABCD":
            packet = str(game.packet("pilot:" + seat, full=True))
            self.assertNotIn(library.ref, packet)
            self.assertNotIn(extra.ref, packet)
        return game, spell, yours, theirs, library, grave, extra

    def stats(self, game, card):
        return tuple(game.engine._numeric_stat(card.object_id, name) for name in ("power", "toughness"))

    def test_cast_x_search_requires_public_match_and_pumps_post_search_creature_set(self):
        game, spell, yours, theirs, library, grave, extra = self.open_spell(125111, x=10)
        self.reject(game, "pilot:B", {"action_id": "choose", "choice": "both"})
        game = self.replay(game, load=True)
        self.choose_zone(game, "both")
        options = self.packet(game)["legal_actions"][0]["choice_schema"]
        self.assertEqual((1, 1), (options["minimum"], options["maximum"]))
        self.assertEqual({library.ref, grave.ref}, set(options["legal_refs"]))
        for seat in "BCD":
            packet = str(game.packet("pilot:" + seat, full=True))
            self.assertNotIn(library.ref, packet)
            self.assertNotIn(extra.ref, packet)
        for illegal_refs in ([], [extra.ref], [grave.ref, library.ref]):
            self.reject(game, "pilot:A", {"action_id": "choose", "search_cards": illegal_refs})
        bases = {card.object_id: self.stats(game, card) for card in (grave, library, yours, theirs)}
        before = self.shuffles(game)
        game = self.replay(game, load=True)
        self.take(game, [library.ref])
        self.assertEqual(before + 1, self.shuffles(game))
        self.assertEqual("graveyard", game.state.cards[spell.object_id].zone)
        self.assertEqual("battlefield", game.state.cards[library.object_id].zone)
        self.assertEqual("graveyard", game.state.cards[grave.object_id].zone)
        for card in (library, yours):
            self.assertEqual(tuple(value + 10 for value in bases[card.object_id]), self.stats(game, card))
            self.assertIn("haste", game.engine._combat_keywords(game.state.cards[card.object_id]))
        self.assertEqual(bases[theirs.object_id], self.stats(game, theirs))
        self.replay(game, load=True)
        game.engine.move_card(grave.object_id, "battlefield", controller="A")
        self.assertEqual(bases[grave.object_id], self.stats(game, grave))

    def test_cast_x_search_keeps_conditional_shuffle_and_applies_bonus_without_a_find(self):
        for i, (zone, x, restricted) in enumerate((("library", 10, False), ("graveyard", 9, True), ("graveyard", 0, False))):
            with self.subTest(zone=zone):
                game, _, yours, _, library, grave, _ = self.open_spell(125120 + i, x=x, restricted=restricted)
                stats = self.stats(game, yours)
                before = self.shuffles(game)
                if restricted:
                    for invalid in ("library", "both", "decline"):
                        self.reject(game, "pilot:A", {"action_id": "choose", "choice": invalid})
                self.choose_zone(game, zone)
                options = self.packet(game)["legal_actions"][0]["choice_schema"]
                if zone == "library":
                    self.assertEqual(0, options["minimum"])
                    self.assertEqual([library.ref], options["legal_refs"])
                    self.take(game, [])
                elif x == 0:
                    self.assertEqual(0, options["minimum"])
                    self.assertEqual([], options["legal_refs"])
                    self.take(game, [])
                else:
                    self.assertEqual(1, options["minimum"])
                    self.assertEqual([grave.ref], options["legal_refs"])
                    self.assertNotIn(library.ref, str(self.packet(game)))
                    self.take(game, [grave.ref])
                self.assertEqual(before + int(zone == "library"), self.shuffles(game))
                bonus = x if x >= 10 else 0
                self.assertEqual(tuple(stat + bonus for stat in stats), self.stats(game, yours))
                self.assertEqual(bool(bonus), "haste" in game.engine._combat_keywords(game.state.cards[yours.object_id]))
                self.replay(game, load=True)

if __name__ == "__main__":unittest.main()
