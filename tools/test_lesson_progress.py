"""Run with .venv-local/Scripts/python.exe -m unittest discover -s tools -p test_lesson_progress.py."""

import itertools
import logging
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lesson_progress import (
    LessonProgressStore, STEP_PREFIXES, completion_steps,
    course_progress, lesson_key_unit, unlocked_units,
)
from streamlit.testing.v1 import AppTest

logging.getLogger("streamlit.runtime.scriptrunner_utils.script_run_context").setLevel(logging.ERROR)


class LessonStorageTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "test.db"
        self.store = LessonProgressStore(self.path)

    def test_all_completion_combinations_and_prior_history(self):
        for unit in range(1, 11):
            for flags in itertools.product((False, True), repeat=5):
                state = {f"{prefix}_{unit}": flag for prefix, flag in zip(STEP_PREFIXES, flags)}
                expected = tuple(all(flags[:index + 1]) for index in range(5))
                self.assertEqual(completion_steps(state, {}, unit), expected)
                self.assertEqual(completion_steps(state, {unit: 5}, unit), (True,) * 5)

    def test_resume_types_and_no_completion_downgrade(self):
        values = {"unit5_checked": ("마트", 2), "unit3_results": {0: True, 1: False},
                  "unit1_answer": None, "vocab_read_cards_1": [0, 2], "selected_unit_number": 5}
        self.store.save({unit: unit % 6 for unit in range(1, 11)}, values)
        history, restored = LessonProgressStore(self.path).load()
        self.assertEqual(restored, values)
        self.assertIsInstance(restored["unit5_checked"], tuple)
        self.store.save({unit: 0 for unit in range(1, 11)}, {})
        self.assertEqual(self.store.load()[0], history)
        self.assertEqual(self.store.load()[1], {})  # Explicitly cleared answers stay cleared.

    def test_existing_totals_are_preserved(self):
        with sqlite3.connect(self.path) as connection:
            connection.execute("CREATE TABLE learner_progress (id INTEGER, total_xp INTEGER)")
            connection.execute("INSERT INTO learner_progress VALUES (1, 123)")
        LessonProgressStore(self.path).save({1: 2}, {"selected_unit_number": 1})
        with sqlite3.connect(self.path) as connection:
            self.assertEqual(connection.execute("SELECT total_xp FROM learner_progress").fetchone()[0], 123)

    def test_course_progress_uses_all_fifty_steps(self):
        history = {1: 5, 2: 3, 3: 0}
        completed, total, ratio = course_progress({}, history)
        self.assertEqual((completed, total), (8, 50))
        self.assertEqual(ratio, 0.16)

    def test_units_unlock_only_after_the_previous_unit(self):
        self.assertEqual(unlocked_units({}, {}), (1,))
        self.assertEqual(unlocked_units({}, {1: 5}), (1, 2))
        self.assertEqual(unlocked_units({}, {1: 5, 2: 4}), (1, 2))
        self.assertEqual(unlocked_units({}, {1: 5, 2: 5}), (1, 2, 3))
        self.assertEqual(unlocked_units({}, {}, review_mode=True), tuple(range(1, 11)))

    def test_lesson_keys_are_mapped_to_their_owning_unit(self):
        examples = {
            "unit1_intro_sequence_step": 1,
            "unit10_a2_place": 10,
            "unit_completed_4": 4,
            "vocab_read_cards_3": 3,
            "grammar_quiz_choice_5_2": 5,
            "grammar1_checked_choice_9_0": 9,
            "activity2_submission_notice_6": 6,
            "review_vocab_read_2_회사원": 2,
            "selected_unit_number": None,
            "total_xp": None,
        }
        for key, unit in examples.items():
            self.assertEqual(lesson_key_unit(key), unit, key)

    def test_app_imports_from_outside_project_directory(self):
        script = (
            "from pathlib import Path\n"
            f"app = Path({str(ROOT / 'app.py')!r})\n"
            "source = app.read_text(encoding='utf-8').split('@st.cache_data', 1)[0]\n"
            "exec(compile(source, str(app), 'exec'), {'__file__': str(app)})\n"
        )
        result = subprocess.run(
            [sys.executable, "-I", "-c", script], cwd=self.directory.name,
            capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


class LessonAppTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "app.db"
        source = (ROOT / "app.py").read_text(encoding="utf-8")
        # Use the real assets and a separate DB. Never run tests against learner data.
        source = source.replace('__file__', repr(str(ROOT / "app.py")))
        source = source.replace(
            'DB_PATH = Path(' + repr(str(ROOT / "app.py")) + ').with_name("gravity_korean.db")',
            'DB_PATH = ' + repr(str(self.path)),
        )
        self.assertIn('DB_PATH = ' + repr(str(self.path)), source)
        self.locked_source = source
        self.source = source
        # Existing end-to-end resume tests intentionally inspect arbitrary units.
        self.source = self.source.replace(
            'REVIEW_MODE = os.getenv("KOREAN_APP_REVIEW_MODE", "0") == "1"',
            'REVIEW_MODE = True',
        )

    def app(self, unit=None):
        app = AppTest.from_string(self.source, default_timeout=90)
        if unit is not None:
            app.session_state["selected_unit_number"] = unit
        return app.run()

    def locked_app(self):
        return AppTest.from_string(self.locked_source, default_timeout=90).run()

    def assert_no_errors(self, app):
        self.assertEqual([error.message for error in app.exception], [])

    def select_unit(self, app, unit):
        menu = app.selectbox(key="selected_unit_label")
        menu.select(menu.options[unit - 1]).run()
        self.assert_no_errors(app)

    def test_unit1_completes_only_after_visible_prerequisites_and_resumes(self):
        app = self.app(1)
        self.assertTrue(app.button(key="unit1_intro_reading_continue_locked").disabled)
        self.assertFalse(app.button(key="unit1_intro_sequence_0").disabled)
        self.assertTrue(app.button(key="unit1_intro_sequence_locked_1").disabled)
        for index in range(4):
            app.button(key=f"unit1_intro_sequence_{index}").click().run()
            self.assert_no_errors(app)
        self.assertEqual(app.session_state["unit1_intro_sequence_step"], 4)
        self.assertIsNotNone(app.button(key="unit1_intro_sequence_reset"))
        self.assertEqual(app.session_state["_lesson_history"].get(1, 0), 0)
        app.session_state["unit1_picture_dialogue_done"] = True
        app.session_state["unit1_picture_card_index"] = 3
        app.session_state["unit1_read_round"] = 3
        app.run()
        self.assertFalse(app.button(key="unit1_intro_reading_restart").disabled)
        self.assertGreaterEqual(
            sum("reading-line done" in str(markdown.value) for markdown in app.markdown),
            4,
        )
        complete_button = app.button(key="unit1_vocab_complete")
        self.assertTrue(complete_button.disabled)
        self.assertNotIn("unit1_picture_continue_grammar1", [button.key for button in app.button])
        disabled_guides = [
            str(markdown.value) for markdown in app.markdown
            if "disabled-button-guide" in str(markdown.value)
        ]
        self.assertTrue(any("18" in guide for guide in disabled_guides))
        app.session_state["vocab_read_cards_1"] = list(range(18))
        app.run()
        self.assert_no_errors(app)
        self.assertFalse(app.button(key="unit1_vocab_complete").disabled)
        self.assertEqual(app.session_state["_lesson_history"].get(1, 0), 0)
        app.button(key="unit1_vocab_complete").click().run()
        self.assert_no_errors(app)
        self.assertEqual(app.session_state["_lesson_history"][1], 1)
        self.assertNotIn("unit1_vocab_complete", [button.key for button in app.button])
        self.assertFalse(app.button(key="unit1_picture_continue_grammar1").disabled)
        self.assertFalse(app.button(key="unit1_vocab_replay").disabled)
        app.button(key="unit1_picture_continue_grammar1").click().run()
        self.assert_no_errors(app)
        self.assertNotIn("requested_lesson_tab", app.session_state.filtered_state)
        self.assertGreaterEqual(len(app.get("iframe")), 1)
        self.assertTrue(any(
            'id="lesson-five-stage-tabs"' in str(markdown.value)
            for markdown in app.markdown
        ))
        grammar_progress = [progress.text for progress in app.get("progress")]
        self.assertIn("그림 대화 완료 0/4", grammar_progress)
        self.assertIn("마지막 문법 확인 0/1", grammar_progress)
        self.assertTrue(app.button(key="unit1_grammar1_complete").disabled)
        self.assertIsNotNone(app.button(key="unit1_grammar1_restart_before_complete"))
        self.assertIsNone(app.radio(key="unit1_grammar1_picture_0").value)
        grammar1_progress_texts = [progress.text for progress in app.get("progress")]
        self.assertIn("그림 대화 완료 0/4", grammar1_progress_texts)
        self.assertIn("마지막 문법 확인 0/1", grammar1_progress_texts)
        self.assertNotIn("grammar1_done_1", [checkbox.key for checkbox in app.checkbox])
        self.assertTrue(app.session_state["unit1_intro_collapsed"])
        self.assertIsNotNone(app.button(key="unit1_intro_expand"))
        self.assertNotIn("unit1_intro_reading_restart", [button.key for button in app.button])
        app.button(key="unit1_intro_expand").click().run()
        self.assert_no_errors(app)
        self.assertFalse(app.session_state["unit1_intro_collapsed"])
        self.assertIsNotNone(app.button(key="unit1_intro_reading_restart"))
        self.assertEqual(app.session_state["_lesson_history"][1], 1)
        self.assertNotIn("unit1_intro_done", app.session_state.filtered_state)
        app.run()  # Reflect the completion checkpoint in the rendered tab label.
        self.assertIn("✓", app.tabs[0].label)
        self.select_unit(app, 2)
        restored = self.app()
        self.assert_no_errors(restored)
        self.assertEqual(restored.session_state["selected_unit_number"], 2)
        self.select_unit(restored, 1)
        self.assertEqual(restored.session_state["_lesson_history"][1], 1)
        self.assertEqual(restored.session_state["vocab_read_cards_1"], list(range(18)))
        self.assertIn("✓", restored.tabs[0].label)

    def test_unit1_stage_completion_buttons_and_scoped_restarts(self):
        LessonProgressStore(self.path).save({1: 2}, {
            "vocab_rewarded_1": True,
            "unit1_grammar1_friend_name": "민",
            "unit2_saved_answer": "keep me",
        })
        app = self.app(1)
        self.assertNotIn("grammar2_done_1", [widget.key for widget in app.checkbox])
        self.assertTrue(app.button(key="unit1_grammar2_complete").disabled)
        nonce = app.session_state.filtered_state.get("unit1_grammar2_reset_nonce", 0)
        for index, option in enumerate([0, 1, 0, 1]):
            radio = app.radio(key=f"unit1_grammar2_choice_{index}_{nonce}")
            radio.set_value(radio.options[option])
        app.text_input(key="unit1_grammar2_friend_name").set_value("민")
        app.selectbox(key="unit1_grammar2_friend_job").select("선생님")
        app.run()
        app.button(key="unit1_grammar2_check").click().run()
        self.assertFalse(app.button(key="unit1_grammar2_complete").disabled)
        self.assertEqual(app.session_state["_lesson_history"][1], 2)
        app.button(key="unit1_grammar2_complete").click().run()
        self.assert_no_errors(app)
        self.assertEqual(app.session_state["_lesson_history"][1], 3)
        self.assertNotIn("unit1_grammar2_complete", [button.key for button in app.button])
        app.button(key="unit1_grammar2_continue_activity1").click().run()
        self.assertIn("활동 1", app.session_state["_unit1_lesson_tabs"])
        xp = app.session_state["total_xp"]
        app.button(key="unit1_grammar2_replay").click().run()
        self.assert_no_errors(app)
        self.assertIn("문법 2", app.session_state["_unit1_lesson_tabs"])
        self.assertTrue(app.button(key="unit1_grammar2_complete").disabled)
        self.assertEqual(app.text_input(key="unit1_grammar2_friend_name").value, "")
        reset_nonce = app.session_state["unit1_grammar2_reset_nonce"]
        self.assertGreater(reset_nonce, nonce)
        self.assertIsNone(app.radio(key=f"unit1_grammar2_choice_0_{reset_nonce}").value)
        self.assertEqual(app.session_state["unit1_grammar1_friend_name"], "민")
        app.button(key="unit1_vocab_replay").click().run()
        self.assert_no_errors(app)
        self.assertTrue(app.button(key="unit1_vocab_complete").disabled)
        self.assertEqual(app.session_state["vocab_read_cards_1"], [])
        self.assertEqual(app.session_state["unit1_read_round"], 0)
        self.assertEqual(app.session_state["_lesson_history"][1], 3)
        self.assertEqual(app.session_state["total_xp"], xp)
        self.assertTrue(app.session_state["vocab_rewarded_1"])
        self.assertEqual(app.session_state["unit2_saved_answer"], "keep me")

    def test_unit1_activity1_completion_and_review_preserve_history(self):
        LessonProgressStore(self.path).save({1: 3}, {
            "unit1_grammar2_friend_name": "민",
            "vocab_rewarded_1": True,
        })
        app = self.app(1)
        self.assertNotIn("unit1_activity1_dialogue_read", [item.key for item in app.checkbox])
        self.assertTrue(app.button(key="unit1_activity1_complete").disabled)
        app.selectbox(key="unit1_activity1_anna_job").select("회사원")
        app.selectbox(key="unit1_activity1_juno_job").select("학생")
        app.run()
        app.button(key="unit1_activity1_greeting_check").click().run()
        self.assertTrue(app.button(key="unit1_activity1_complete").disabled)
        app.selectbox(key="unit1_activity1_friend_name").select("마리")
        app.selectbox(key="unit1_activity1_friend_job").select("회사원").run()
        self.assertFalse(app.button(key="unit1_activity1_complete").disabled)
        self.assertEqual(app.session_state["_lesson_history"][1], 3)
        app.selectbox(key="unit1_activity1_anna_job").select("학생").run()
        self.assertTrue(app.button(key="unit1_activity1_complete").disabled)
        app.selectbox(key="unit1_activity1_anna_job").select("회사원").run()
        app.button(key="unit1_activity1_complete").click().run()
        self.assert_no_errors(app)
        self.assertEqual(app.session_state["_lesson_history"][1], 4)
        self.assertNotIn("unit1_activity1_complete", [button.key for button in app.button])
        app.button(key="unit1_activity1_continue_activity2").click().run()
        self.assertIn("활동 2", app.session_state["_unit1_lesson_tabs"])
        xp = app.session_state["total_xp"]
        app.button(key="unit1_activity1_replay").click().run()
        self.assert_no_errors(app)
        self.assertIn("활동 1", app.session_state["_unit1_lesson_tabs"])
        self.assertTrue(app.button(key="unit1_activity1_complete").disabled)
        self.assertNotIn("unit1_activity1_greeting_checked", app.session_state.filtered_state)
        self.assertEqual(app.session_state["_lesson_history"][1], 4)
        self.assertEqual(app.session_state["total_xp"], xp)
        self.assertEqual(app.session_state["unit1_grammar2_friend_name"], "민")

    def test_answers_survive_unit_switch_restart_and_explicit_clear(self):
        app = self.app(5)
        self.assert_no_errors(app)
        keys = [f"unit5_activity1_reading_choice_{i}" for i in range(2)]
        answers = [app.selectbox(key=key).options[0] for key in keys]
        for key, answer in zip(keys, answers):
            app.selectbox(key=key).select(answer)
        app.run()
        app.button(key="unit5_activity1_reading_check").click().run()
        app.button(key="unit5_g2_speaking_done").click().run()
        self.assert_no_errors(app)
        self.select_unit(app, 6)
        self.select_unit(app, 5)
        self.assertEqual([app.selectbox(key=key).value for key in keys], answers)
        restored = self.app()
        self.assert_no_errors(restored)
        self.assertEqual([restored.selectbox(key=key).value for key in keys], answers)
        self.assertEqual(restored.session_state["unit5_activity1_reading_result"], [True, True])
        self.assertIsInstance(restored.session_state["unit5_g2_checked_items"], tuple)
        _, snapshot = LessonProgressStore(self.path).load()
        self.assertNotIn("unit5_g2_speaking_done", snapshot)  # A button is not an answer.
        # Hiding feedback must not be undone by restoring an old snapshot.
        restored.button(key="unit5_activity1_reading_check").click().run()
        self.assertFalse(restored.session_state["unit5_activity1_reading_feedback_visible"])

    def test_earned_completion_survives_review_with_incomplete_answers(self):
        LessonProgressStore(self.path).save({5: 5}, {"selected_unit_number": 5})
        app = self.app()
        self.assert_no_errors(app)
        self.assertTrue(all("✓" in tab.label for tab in app.tabs))
        self.assertTrue(app.button(key="activity2_submit_5").disabled)
        self.select_unit(app, 6)
        self.select_unit(app, 5)
        self.assertTrue(all("✓" in tab.label for tab in app.tabs))
        self.assertEqual(LessonProgressStore(self.path).load()[0][5], 5)

    def test_all_ten_units_display_restored_completion(self):
        LessonProgressStore(self.path).save({unit: 5 for unit in range(1, 11)}, {})
        app = self.app()
        for unit in range(1, 11):
            with self.subTest(unit=unit):
                self.select_unit(app, unit)
                self.assertEqual(len(app.tabs), 5)
                self.assertTrue(all("✓" in tab.label for tab in app.tabs))

    def test_dependent_answer_options_remain_valid(self):
        app = self.app(10)
        place = app.selectbox(key="unit10_a2_place")
        place.select(place.options[2]).run()
        self.assert_no_errors(app)
        purpose = app.selectbox(key="unit10_a2_purpose")
        self.assertIn(purpose.value, purpose.options)
        restored = self.app()
        self.assert_no_errors(restored)
        purpose = restored.selectbox(key="unit10_a2_purpose")
        self.assertIn(purpose.value, purpose.options)

    def test_learning_mode_hides_units_until_the_previous_unit_is_complete(self):
        app = self.locked_app()
        self.assert_no_errors(app)
        unit_menu = app.selectbox(key="selected_unit_label")
        self.assertEqual(len(unit_menu.options), 10)
        self.assertNotIn("🔒", unit_menu.options[0])
        self.assertIn("🔒", unit_menu.options[1])
        disabled_buttons = [button for button in app.button if button.disabled]
        disabled_guides = [
            markdown for markdown in app.markdown
            if "disabled-button-guide" in str(markdown.value)
        ]
        self.assertGreaterEqual(len(disabled_guides), len(disabled_buttons))
        self.assertNotIn("task_vocab", {button.key for button in app.button})
        unit_menu.select(unit_menu.options[1]).run()
        self.assert_no_errors(app)
        self.assertEqual(app.session_state["selected_unit_number"], 1)
        self.assertEqual(app.selectbox(key="selected_unit_label").value, unit_menu.options[0])
        LessonProgressStore(self.path).save({1: 5}, {"selected_unit_number": 1})
        app = self.locked_app()
        self.assert_no_errors(app)
        unit_menu = app.selectbox(key="selected_unit_label")
        self.assertIn("✓", unit_menu.options[0])
        self.assertNotIn("🔒", unit_menu.options[1])
        self.assertIn("🔒", unit_menu.options[2])
        self.assertIn("task_vocab", {button.key for button in app.button})

    def test_learning_home_restarts_only_transient_current_unit_state(self):
        LessonProgressStore(self.path).save(
            {1: 2},
            {
                "selected_unit_number": 1,
                "unit1_intro_sequence_step": 2,
                "vocab_read_cards_1": [0, 1],
                "unit2_saved_answer": "keep me",
            },
        )
        app = self.locked_app()
        self.assert_no_errors(app)
        home = app.button(key="go_home")
        self.assertFalse(home.help)
        self.assertTrue(any(
            'class="home-button-guide"' in str(markdown.value)
            and "XP" in str(markdown.value)
            for markdown in app.markdown
        ))
        home.click().run()
        self.assert_no_errors(app)
        self.assertEqual(app.session_state["_lesson_history"][1], 2)
        self.assertNotIn("unit1_intro_sequence_step", app.session_state.filtered_state)
        self.assertEqual(app.session_state["unit1_read_round"], 0)
        self.assertEqual(app.session_state["unit1_read_line"], 0)
        self.assertEqual(app.session_state["unit1_picture_card_index"], 0)
        self.assertTrue(any(progress.text == "대화 완료 0/4" for progress in app.get("progress")))
        self.assertEqual(app.session_state["vocab_read_cards_1"], [])
        self.assertEqual(app.session_state["unit2_saved_answer"], "keep me")


if __name__ == "__main__":
    unittest.main()
