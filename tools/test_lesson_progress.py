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

    def assert_stage_pending(self, app, unit, stage):
        self.assert_no_errors(app)
        self.assertNotIn(f"unit{unit}_{stage}_complete", [b.key for b in app.button])
        if stage == "activity2":
            return
        key = f"unit2_{stage}_next" if unit == 2 else {
            "vocab": "unit1_picture_continue_grammar1",
            "grammar1": "unit1_grammar1_continue_grammar2",
            "grammar2": "unit1_grammar2_continue_activity1",
            "activity1": "unit1_activity1_continue_activity2",
        }[stage]
        self.assertTrue(app.button(key=key).disabled)

    def test_unit2_number_reading_counts_unique_clicks_and_resumes(self):
        app = self.app(2)

        def assert_count(app, count):
            self.assert_no_errors(app)
            progress = app.tabs[0].get("progress")[0]
            self.assertEqual(progress.proto.text, f"1. 숫자를 소리 내어 읽어 보세요. ({count}/30)")
            self.assertEqual(progress.value, int(count / 30 * 100))

        assert_count(app, 0)
        app.button(key="unit2_sino_number_0").click().run()
        assert_count(app, 1)
        app.button(key="unit2_sino_number_0").click().run()
        assert_count(app, 1)
        app.button(key="unit2_sino_number_10").click().run()
        assert_count(app, 2)
        resumed = self.app(2)
        assert_count(resumed, 2)
        numbers = [b.key.removeprefix("unit2_sino_number_") for b in resumed.button
                   if b.key and b.key.startswith("unit2_sino_number_")]
        resumed.session_state["unit2_sino_read_numbers"] = [n for n in numbers if n != "1000"]
        resumed.run()
        resumed.button(key="unit2_sino_number_1000").click().run()
        assert_count(resumed, 30)
        resumed.button(key="unit2_vocab_replay").click().run()
        assert_count(resumed, 0)
        assert_count(self.app(2), 0)

    def test_unit2_number_examples_count_separately_and_resume(self):
        app = self.app(2)

        def assert_count(app, count):
            self.assert_no_errors(app)
            progress = app.tabs[0].get("progress")[1]
            self.assertEqual(progress.proto.text, f"2. 숫자가 들어간 정보를 읽어 보세요. ({count}/6)")
            self.assertEqual(progress.value, int(count / 6 * 100))

        assert_count(app, 0)
        app.button(key="unit2_number_example_4층").click().run()
        assert_count(app, 1)
        app.button(key="unit2_number_example_4층").click().run()
        assert_count(app, 1)
        app.button(key="unit2_sino_number_0").click().run()
        assert_count(app, 1)
        resumed = self.app(2)
        assert_count(resumed, 1)
        for count, expression in enumerate(("5월 3일", "35쪽", "320번", "405호", "800원"), 2):
            resumed.button(key=f"unit2_number_example_{expression}").click().run()
            assert_count(resumed, count)
        self.assertEqual(resumed.session_state["unit2_sino_read_numbers"], ["0"])
        resumed.button(key="unit2_vocab_replay").click().run()
        assert_count(resumed, 0)
        assert_count(self.app(2), 0)

    def test_unit2_automatic_completion_navigation_replay_and_wrap_up(self):
        app = self.app(2)
        self.assert_no_errors(app)
        stages = ("vocab", "grammar1", "grammar2", "activity1", "activity2")
        for stage in stages:
            self.assert_stage_pending(app, 2, stage)
            self.assertIsNotNone(app.button(key=f"unit2_{stage}_replay"))
        self.assertNotIn("grammar1_done_2", [item.key for item in app.checkbox])
        self.assertNotIn("grammar2_done_2", [item.key for item in app.checkbox])

        vocab_buttons = [b for b in app.button if b.key and b.key.startswith("vocab_select_2_")]
        app.session_state["vocab_read_cards_2"] = list(range(len(vocab_buttons)))
        for i, value in enumerate(("140번", "5월", "800원", "405호")):
            app.selectbox(key=f"unit2_visual_number_{i}").select(value)
        app.run()
        self.assertEqual(app.session_state["_lesson_history"][2], 1)
        self.assertIn("어휘와 표현", app.session_state["_unit2_lesson_tabs"])
        app.button(key="unit2_vocab_next").click().run()
        self.assertIn("문법 1", app.session_state["_unit2_lesson_tabs"])

        for i in range(4):
            radio = app.radio(key=f"unit2_g1_picture_answer_{i}")
            radio.set_value(radio.options[(1, 0, 2, 1)[i]])
        app.run()
        app.button(key="unit2_g1_picture_check").click().run()
        for i, answer in enumerate(("가", "가", "가", "이")):
            app.radio(key=f"grammar_quiz_choice_2_{i}").set_value(answer).run()
            app.button(key=f"grammar_check_2_{i}").click().run()
            if i < 3:
                self.assert_stage_pending(app, 2, "grammar1")
                app.button(key=f"grammar_next_2_{i}").click().run()
        self.assertEqual(app.session_state["_lesson_history"][2], 2)
        self.assertIn("문법 1", app.session_state["_unit2_lesson_tabs"])
        app.button(key="unit2_grammar1_next").click().run()
        self.assertIn("문법 2", app.session_state["_unit2_lesson_tabs"])

        for i in range(4):
            radio = app.radio(key=f"unit2_negative_{i}")
            radio.set_value(radio.options[0])
        for i in range(3):
            radio = app.radio(key=f"unit2_negative_dialogue_{i}")
            radio.set_value(radio.options[0])
        app.run()
        app.button(key="unit2_negative_check").click().run()
        self.assertEqual(app.session_state["_lesson_history"][2], 3)
        app.button(key="unit2_grammar2_next").click().run()
        self.assertIn("활동 1", app.session_state["_unit2_lesson_tabs"])

        app.selectbox(key="unit2_activity1_topic").select("전화번호")
        app.selectbox(key="unit2_activity1_phone").select("010-1359-6783").run()
        app.button(key="unit2_activity1_check").click().run()
        radio = app.radio(key="unit2_activity1_confirmation")
        radio.set_value(radio.options[0]).run()
        app.button(key="unit2_activity1_confirmation_check").click().run()
        self.assertEqual(app.session_state["_lesson_history"][2], 4)
        app.button(key="unit2_activity1_next").click().run()
        self.assertIn("활동 2", app.session_state["_unit2_lesson_tabs"])

        for i in range(2):
            select = app.selectbox(key=f"unit2_activity2_place_phone_{i}")
            select.select(select.options[i + 1])
        app.run()
        app.button(key="unit2_activity2_place_check").click().run()
        app.text_input(key="unit2_contact_name_0").set_value("민")
        app.text_input(key="unit2_contact_phone_0").set_value("010-1234-5678")
        app.text_input(key="unit2_contact_email_name_0").set_value("min").run()
        self.assert_no_errors(app)
        self.assertEqual(app.session_state["_lesson_history"][2], 5)
        self.assertTrue(any('href="#unit-summary-heading"' in m.value for m in app.markdown))
        self.assertNotIn("unit2_wrap_up_finish", [b.key for b in app.button])
        app.checkbox(key="unit2_summary_confirmed").check()
        for step in ("vocab", "grammar", "sentence"):
            app.session_state[f"review_{step}_done_2"] = True
        app.run()
        app.button(key="unit2_wrap_up_finish").click().run()
        self.assertTrue(app.session_state["unit2_wrap_up_completed"])
        xp = app.session_state["total_xp"]
        for stage in stages:
            app.button(key=f"unit2_{stage}_replay").click().run()
            self.assert_no_errors(app)
            self.assert_stage_pending(app, 2, stage)
            self.assertEqual(app.session_state["_lesson_history"][2], 5)
            self.assertEqual(app.session_state["total_xp"], xp)
        self.assertEqual(app.text_input(key="unit2_contact_name_0").value, "")
        resumed = self.app(2)
        self.assert_no_errors(resumed)
        self.assertEqual(resumed.session_state["_lesson_history"][2], 5)
        self.assert_stage_pending(resumed, 2, "activity2")
        self.assertTrue(resumed.session_state["unit2_wrap_up_completed"])

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
        self.assert_stage_pending(app, 1, "vocab")
        self.assertTrue(app.button(key="unit1_picture_continue_grammar1").disabled)
        disabled_guides = [
            str(markdown.value) for markdown in app.markdown
            if "disabled-button-guide" in str(markdown.value)
        ]
        self.assertTrue(any("18" in guide for guide in disabled_guides))
        app.session_state["vocab_read_cards_1"] = list(range(18))
        app.run()
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
        self.assert_stage_pending(app, 1, "grammar1")
        self.assertIsNotNone(app.button(key="unit1_grammar1_restart"))
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

    def test_unit1_stage_automatic_completion_and_scoped_restarts(self):
        LessonProgressStore(self.path).save({1: 2}, {
            "vocab_rewarded_1": True,
            "unit1_grammar1_friend_name": "민",
            "unit2_saved_answer": "keep me",
        })
        app = self.app(1)
        self.assertNotIn("grammar2_done_1", [widget.key for widget in app.checkbox])
        self.assert_stage_pending(app, 1, "grammar2")
        nonce = app.session_state.filtered_state.get("unit1_grammar2_reset_nonce", 0)
        for index, option in enumerate([0, 1, 0, 1]):
            radio = app.radio(key=f"unit1_grammar2_choice_{index}_{nonce}")
            radio.set_value(radio.options[option])
        app.text_input(key="unit1_grammar2_friend_name").set_value("민")
        app.selectbox(key="unit1_grammar2_friend_job").select("선생님")
        app.run()
        app.button(key="unit1_grammar2_check").click().run()
        self.assert_no_errors(app)
        self.assertEqual(app.session_state["_lesson_history"][1], 3)
        self.assertNotIn("unit1_grammar2_complete", [button.key for button in app.button])
        app.button(key="unit1_grammar2_continue_activity1").click().run()
        self.assertIn("활동 1", app.session_state["_unit1_lesson_tabs"])
        xp = app.session_state["total_xp"]
        app.button(key="unit1_grammar2_replay").click().run()
        self.assert_no_errors(app)
        self.assertIn("문법 2", app.session_state["_unit1_lesson_tabs"])
        self.assert_stage_pending(app, 1, "grammar2")
        self.assertEqual(app.text_input(key="unit1_grammar2_friend_name").value, "")
        reset_nonce = app.session_state["unit1_grammar2_reset_nonce"]
        self.assertGreater(reset_nonce, nonce)
        self.assertIsNone(app.radio(key=f"unit1_grammar2_choice_0_{reset_nonce}").value)
        self.assertEqual(app.session_state["unit1_grammar1_friend_name"], "민")
        app.button(key="unit1_vocab_replay").click().run()
        self.assert_no_errors(app)
        self.assert_stage_pending(app, 1, "vocab")
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
        self.assert_stage_pending(app, 1, "activity1")
        app.selectbox(key="unit1_activity1_anna_job").select("회사원")
        app.selectbox(key="unit1_activity1_juno_job").select("학생")
        app.run()
        app.button(key="unit1_activity1_greeting_check").click().run()
        self.assert_stage_pending(app, 1, "activity1")
        app.selectbox(key="unit1_activity1_friend_name").select("마리")
        app.selectbox(key="unit1_activity1_friend_job").select("회사원").run()
        self.assertEqual(app.session_state["_lesson_history"][1], 4)
        app.selectbox(key="unit1_activity1_anna_job").select("학생").run()
        self.assertEqual(app.session_state["_lesson_history"][1], 4)
        self.assertFalse(app.button(key="unit1_activity1_continue_activity2").disabled)
        app.selectbox(key="unit1_activity1_anna_job").select("회사원").run()
        self.assert_no_errors(app)
        self.assertEqual(app.session_state["_lesson_history"][1], 4)
        self.assertNotIn("unit1_activity1_complete", [button.key for button in app.button])
        app.button(key="unit1_activity1_continue_activity2").click().run()
        self.assertIn("활동 2", app.session_state["_unit1_lesson_tabs"])
        xp = app.session_state["total_xp"]
        app.button(key="unit1_activity1_replay").click().run()
        self.assert_no_errors(app)
        self.assertIn("활동 1", app.session_state["_unit1_lesson_tabs"])
        self.assert_stage_pending(app, 1, "activity1")
        self.assertNotIn("unit1_activity1_greeting_checked", app.session_state.filtered_state)
        self.assertEqual(app.session_state["_lesson_history"][1], 4)
        self.assertEqual(app.session_state["total_xp"], xp)
        self.assertEqual(app.session_state["unit1_grammar2_friend_name"], "민")

    def test_unit1_grammar1_automatically_completes_and_replays(self):
        LessonProgressStore(self.path).save({1: 1}, {"vocab_rewarded_1": True})
        app = self.app(1)
        self.assert_stage_pending(app, 1, "grammar1")
        for index, option in enumerate((0, 1, 0, 1)):
            radio = app.radio(key=f"unit1_grammar1_picture_{index}")
            radio.set_value(radio.options[option])
        app.run()
        app.button(key="unit1_grammar1_picture_check").click().run()
        app.radio(key="unit1_grammar1_final_choice").set_value("이에요").run()
        app.button(key="unit1_grammar1_final_check").click().run()
        self.assert_stage_pending(app, 1, "grammar1")
        self.assertEqual(app.session_state["_lesson_history"][1], 1)
        app.text_input(key="unit1_grammar1_friend_name").set_value("민")
        app.selectbox(key="unit1_grammar1_friend_country").select("한국").run()
        self.assert_no_errors(app)
        self.assertEqual(app.session_state["_lesson_history"][1], 2)
        self.assertIn("문법 1", app.session_state["_unit1_lesson_tabs"])
        self.assertFalse(app.button(key="unit1_grammar1_continue_grammar2").disabled)
        self.assertTrue(any("문법 1 학습을 완료했어요" in m.value for m in app.success))
        xp = app.session_state["total_xp"]
        app.run()
        self.assertEqual(app.session_state["total_xp"], xp)
        app.button(key="unit1_grammar1_restart").click().run()
        self.assert_stage_pending(app, 1, "grammar1")
        self.assertEqual(app.session_state["_lesson_history"][1], 2)
        self.assertEqual(app.session_state["total_xp"], xp)
        self.assertIsNone(app.radio(key="unit1_grammar1_picture_0").value)
        resumed = self.app(1)
        self.assert_stage_pending(resumed, 1, "grammar1")
        self.assertEqual(resumed.session_state["_lesson_history"][1], 2)

    def test_unit1_activity2_automatically_completes_without_duplicate_xp(self):
        LessonProgressStore(self.path).save({1: 4}, {"vocab_rewarded_1": True})
        app = self.app(1)
        xp = app.session_state["total_xp"]

        def finish_activity(app):
            nonce = app.session_state.filtered_state.get("unit1_activity2_reset_nonce", 0)
            app.text_input(key=f"unit1_activity2_wei_name_{nonce}").set_value("웨이")
            app.selectbox(key=f"unit1_activity2_wei_job_{nonce}").select("요리사")
            app.text_input(key=f"unit1_activity2_yuna_name_{nonce}").set_value("유나")
            app.selectbox(key=f"unit1_activity2_yuna_job_{nonce}").select("가수")
            app.run()
            app.button(key="unit1_activity2_profile_check").click().run()
            app.text_input(key=f"unit1_final_name_{nonce}").set_value("민")
            app.selectbox(key=f"unit1_final_country_{nonce}").select("한국").run()
            self.assertFalse(any("활동 2 학습을 완료했어요" in m.value for m in app.success))
            app.selectbox(key=f"unit1_final_job_{nonce}").select("선생님").run()
            self.assert_no_errors(app)
            self.assertEqual(app.session_state["_lesson_history"][1], 5)
            self.assertIn("활동 2", app.session_state["_unit1_lesson_tabs"])
            self.assertTrue(any("활동 2 학습을 완료했어요" in m.value for m in app.success))
            self.assertTrue(any("다음: 단원 정리·복습" in m.value for m in app.markdown))
            self.assertNotIn("unit1_activity2_complete", [b.key for b in app.button])

        finish_activity(app)
        self.assertEqual(app.session_state["total_xp"], xp + 20)
        app.button(key="unit1_activity2_replay").click().run()
        self.assertEqual(app.session_state["_lesson_history"][1], 5)
        finish_activity(app)
        self.assertEqual(app.session_state["total_xp"], xp + 20)
        resumed = self.app(1)
        self.assert_no_errors(resumed)
        self.assertEqual(resumed.session_state["total_xp"], xp + 20)
        self.assertTrue(any("활동 2 학습을 완료했어요" in m.value for m in resumed.success))

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

    def test_later_units_have_reading_and_sequence_introductions(self):
        app = self.app(6)
        for unit in range(6, 11):
            with self.subTest(unit=unit):
                if unit != 6:
                    self.select_unit(app, unit)
                self.assert_no_errors(app)
                self.assertFalse(app.button(key=f"unit{unit}_read_advance").disabled)
                self.assertTrue(app.button(key=f"unit{unit}_intro_sequence_0").disabled)
                self.assertTrue(app.button(key=f"unit{unit}_intro_continue_vocabulary").disabled)
                for stage in ("vocab", "grammar1", "grammar2", "activity1", "activity2"):
                    self.assertIsNotNone(app.button(key=f"unit{unit}_{stage}_replay"))
                    self.assertIsNotNone(app.button(key=f"unit{unit}_{stage}_next"))

    def test_unit6_introduction_advances_to_vocabulary(self):
        app = self.app(6)
        for _ in range(12):
            app.button(key="unit6_read_advance").click().run()
            self.assert_no_errors(app)
        self.assertEqual(app.session_state["unit6_read_round"], 3)
        app.button(key="unit6_intro_reading_continue").click().run()
        self.assert_no_errors(app)
        for index in range(4):
            app.button(key=f"unit6_intro_sequence_{index}").click().run()
            self.assert_no_errors(app)
        self.assertFalse(app.button(key="unit6_intro_continue_vocabulary").disabled)
        app.button(key="unit6_intro_continue_vocabulary").click().run()
        self.assert_no_errors(app)
        self.assertTrue(app.session_state["_unit6_lesson_tabs"].startswith("● 어휘와 표현"))

    def test_later_unit_replay_keeps_earned_completion_and_locks_next(self):
        LessonProgressStore(self.path).save(
            {unit: 5 for unit in range(1, 7)},
            {"selected_unit_number": 6, "vocab_read_cards_6": [0, 1]},
        )
        app = self.app(6)
        self.assertFalse(app.button(key="unit6_vocab_next").disabled)
        app.button(key="unit6_vocab_next").click().run()
        self.assert_no_errors(app)
        self.assertTrue(app.session_state["_unit6_lesson_tabs"].startswith("● 문법 1"))
        app.button(key="unit6_vocab_replay").click().run()
        self.assert_no_errors(app)
        self.assertTrue(app.button(key="unit6_vocab_next").disabled)
        self.assertEqual(app.session_state["vocab_read_cards_6"], [])
        self.assertEqual(LessonProgressStore(self.path).load()[0][6], 5)

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
