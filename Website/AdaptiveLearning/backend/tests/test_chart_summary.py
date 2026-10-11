"""The chart-summary endpoint; only `_chart_summary_basis` reads, and is tested on which reads it makes."""
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

import pytest  # noqa: E402

import main  # noqa: E402


def _basis(**over):
    """A complete, ordinary basis. Every test changes one field of it."""
    base = {
        "days": 7, "weeks": 8, "face_included": True,
        "signals_retrieved": True, "trend_retrieved": True,
        "stats_retrieved": True, "topics_retrieved": True,
        "consent_retrieved": True,
        "channels": {
            "eeg":   {"enabled": True, "revoked_at": None, "samples": 400},
            "heart": {"enabled": True, "revoked_at": None, "samples": 120},
        },
        # Calm is `1 - stress`, as `_chart_summary_basis` builds it.
        "averages": {"focus": 0.63, "calm": 0.59, "heart_rate_bpm": 72.4,
                     "rmssd_ms": None, "body_arousal": None},
        "trend": {
            "focus": {"direction": "up", "first": 0.55, "last": 0.63,
                      "weeks_with_data": 4},
            "calm": {"direction": "steady", "first": 0.60, "last": 0.59,
                     "weeks_with_data": 4},
        },
        "academic": {"sessions": 12, "total_questions": 240,
                     "total_correct": 163, "accuracy": 68},
        "topics": {
            "weakest": {"topic_name": "angle_relationships", "accuracy": 42,
                        "attempted_questions": 20},
            "strongest": {"topic_name": "ordering", "accuracy": 91,
                          "attempted_questions": 30},
            "attempted_count": 5,
        },
    }
    base.update(over)
    return base


@pytest.fixture(autouse=True)
def _no_real_database(monkeypatch):
    """`_chart_summary_basis` reads the rollup for the usual; never reach a local stack."""
    from tests.test_access_control import _FakeSupabase
    monkeypatch.setattr(main, "supabase", _FakeSupabase({"signal_daily_rollup": []}))


# ── the flag ─────────────────────────────────────────────────────────────

def test_the_flag_is_declared_so_an_unreadable_table_cannot_change_behaviour():
    """The defaults map is also the whitelist."""
    assert main._FEATURE_FLAG_DEFAULTS["chart_summary_llm_enabled"] is True


# ── _trend_direction ─────────────────────────────────────────────────────

def _weeks(*values):
    return [{"week_start": f"2026-0{i + 1}-01", "focus": v}
            for i, v in enumerate(values)]


def test_one_week_of_readings_is_a_value_not_a_direction():
    assert main._trend_direction(_weeks(0.5), "focus")["direction"] is None
    assert main._trend_direction([], "focus")["direction"] is None


def test_no_weeks_and_one_week_are_told_apart():
    """A first open session has a focus average but no rollup week yet."""
    assert main._trend_direction([], "focus")["weeks_with_data"] == 0
    assert main._trend_direction(_weeks(0.5), "focus")["weeks_with_data"] == 1


def test_a_move_smaller_than_the_threshold_is_steady():
    """Below `_CHART_SUMMARY_TREND_MIN_DELTA` is within what strap fit moves."""
    move = main._trend_direction(_weeks(0.50, 0.52), "focus")
    assert move["direction"] == "steady"


def test_direction_is_reported_either_way_past_the_threshold():
    assert main._trend_direction(_weeks(0.50, 0.70), "focus")["direction"] == "up"
    assert main._trend_direction(_weeks(0.70, 0.50), "focus")["direction"] == "down"


def test_the_anchors_are_the_weeks_with_readings_not_the_ends_of_the_range():
    """A term can end in null weeks; anchor on the last bucket with a reading."""
    weeks = [{"week_start": "a", "focus": 0.40},
             {"week_start": "b", "focus": None},
             {"week_start": "c", "focus": 0.80},
             {"week_start": "d", "focus": None}]
    move = main._trend_direction(weeks, "focus")
    assert (move["first"], move["last"], move["weeks_with_data"]) == (0.4, 0.8, 2)
    assert move["direction"] == "up"


# ── the numeric check ────────────────────────────────────────────────────

def test_the_allowed_figures_are_read_out_of_the_sentences_we_send():
    """Not enumerated from the basis: sentences round figures and print dates it lacks."""
    lines = main._rule_based_chart_summary(_basis())
    figures = main._chart_summary_figures(lines)
    assert 72.0 in figures, "the rounded heart rate the sentence prints"
    assert 68.0 in figures and 240.0 in figures


def test_a_revocation_date_is_a_figure_the_reply_may_repeat():
    basis = _basis()
    basis["channels"]["heart"] = {"enabled": False, "samples": 0,
                                  "revoked_at": "2026-08-03T16:00:00+00:00"}
    lines = main._rule_based_chart_summary(basis)
    assert any("3 August" in line for line in lines)
    assert 3.0 in main._chart_summary_figures(lines)


def test_a_number_we_did_not_supply_rejects_the_whole_reply():
    """The one check this endpoint has that the strategies pass does not."""
    lines = main._rule_based_chart_summary(_basis())
    allowed = main._chart_summary_figures(lines)
    reply = "\n".join(f"{i + 1}. {line}" for i, line in enumerate(lines))
    assert main._validated_chart_summary(reply, allowed, len(lines)) is not None

    invented = reply.replace("68%", "77%")
    assert invented != reply
    assert main._validated_chart_summary(invented, allowed, len(lines)) is None


def test_a_thousands_separator_is_not_read_as_two_numbers():
    """"1,240" against a supplied 1240 is a correct reply formatted differently."""
    lines = ["A student has answered 1240 questions in total, which is a lot of practice."]
    allowed = main._chart_summary_figures(lines)
    reply = "1. A student has answered 1,240 questions in total, which is a lot of practice."
    assert main._validated_chart_summary(reply, allowed, 1) == [
        "A student has answered 1,240 questions in total, which is a lot of practice."]


def test_a_reply_that_drops_a_point_is_rejected():
    """Exactly the baseline's length: the likeliest drop is the channel-absence sentence."""
    lines = main._rule_based_chart_summary(_basis())
    allowed = main._chart_summary_figures(lines)
    short = "\n".join(f"{i + 1}. {line}" for i, line in enumerate(lines[:-1]))
    assert main._validated_chart_summary(short, allowed, len(lines)) is None


def test_a_clinical_term_anywhere_in_the_reply_rejects_it():
    lines = main._rule_based_chart_summary(_basis())
    allowed = main._chart_summary_figures(lines)
    reply = ("Here is what this suggests about their anxiety disorder:\n"
             + "\n".join(f"{i + 1}. {line}" for i, line in enumerate(lines)))
    assert main._validated_chart_summary(reply, allowed, len(lines)) is None


@pytest.mark.parametrize("cause", ["stopped working", "broke", "had a fault", "malfunctioned",
                                   "disconnected", "wasn't working", "wasn’t working", "didn't work",
                                   "is not working", "didn't work at all", "doesn't work",
                                   "hasn't been working", "sensor not working", "lost connection",
                                   "had technical problems", "stopped giving us data",
                                   "stopped sending readings", "no longer providing data"])
def test_a_reply_that_names_a_cause_for_a_turned_off_sensor_is_rejected(cause):
    """The run's wording: a withdrawal read as "before the sensor stopped working"."""
    basis = _basis()
    basis["channels"]["heart"] = {"enabled": False, "samples": 0,
                                  "revoked_at": "2026-08-03T16:00:00+00:00"}
    lines = main._rule_based_chart_summary(basis)
    allowed = main._chart_summary_figures(lines)
    faithful = "\n".join(f"{i + 1}. {line}" for i, line in enumerate(lines))
    assert main._validated_chart_summary(faithful, allowed, len(lines)) is not None
    assert "turned off on 3 August" in faithful
    caused = faithful.replace("turned off on 3 August", f"{cause} on 3 August")
    assert caused != faithful
    assert main._validated_chart_summary(caused, allowed, len(lines)) is None


def test_the_eeg_withdrawal_line_must_keep_turned_off_and_a_rewording_falls_back_to_the_rules():
    """The run's reply: "from before the sensor stopped giving us data on 9 October"."""
    basis = _basis()
    basis["channels"]["eeg"] = {"enabled": False, "samples": 12,
                                "revoked_at": "2026-10-09T16:00:00+00:00"}
    lines = main._rule_based_chart_summary(basis)
    assert any("from before the sensor was turned off on 9 October" in line for line in lines)
    allowed = main._chart_summary_figures(lines)
    faithful = "\n".join(f"{i + 1}. {line}" for i, line in enumerate(lines))
    assert main._validated_chart_summary(faithful, allowed, len(lines)) is not None
    reworded = faithful.replace("the sensor was turned off on 9 October",
                                "the sensor stopped giving us data on 9 October")
    assert reworded != faithful
    assert main._validated_chart_summary(reworded, allowed, len(lines)) is None


@pytest.mark.parametrize("text", [
    "The sensor no longer sends data.", "The headband no longer provides readings.",
    "It no longer gives us any signals.", "Heart rate stopped reporting data last week."])
def test_other_forms_of_stopped_sending_are_a_cause(text):
    assert main._names_a_cause(text)


@pytest.mark.parametrize("text", [
    "The sensor was turned off, so the app stopped collecting data.",
    "Recording is paused, so it no longer sends readings.",
    "The sensor was turned off on 3 Oct; it no longer sends readings.",
    "It was turned off on 3 Oct. and since then it no longer sends readings.",
    "The sensor was turned off on 3 October. It no longer sends readings."])
def test_stopped_sending_after_turned_off_or_paused_is_the_cause_already_stated(text):
    assert not main._names_a_cause(text)


@pytest.mark.parametrize("text", [
    "The headband stopped sending data, so it was switched off.",
    "It no longer sends readings. The sensor was turned off later."])
def test_a_stoppage_stated_before_any_turned_off_is_still_a_cause(text):
    assert main._names_a_cause(text)


@pytest.mark.parametrize("text,cause", [
    ("The headband was turned off on 3 October, and the heart sensor stopped sending data.", True),
    ("The headband was turned off on 3 October, so the camera no longer sends readings.", True),
    ("The headband was turned off on 3 October, so the headband no longer sends readings.", False),
    ("Readings are from before the sensor was turned off; it no longer sends readings.", False),
])
def test_turned_off_excuses_only_a_stoppage_about_the_same_sensor(text, cause):
    assert main._names_a_cause(text) is cause


def test_one_points_turned_off_does_not_excuse_another_points_stoppage():
    """Every withdrawn-headband student gets point 1, so it must not let a heart fault through."""
    basis = _basis()
    basis["channels"]["eeg"] = {"enabled": False, "samples": 12,
                                "revoked_at": "2026-10-03T16:00:00+00:00"}
    lines = main._rule_based_chart_summary(basis)
    assert any("turned off on 3 October" in line for line in lines)
    heart = next(i for i, line in enumerate(lines) if "heart rate" in line.lower())
    allowed = main._chart_summary_figures(lines)
    faulty = list(lines)
    faulty[heart] = faulty[heart].rstrip(".") + ", and the heart sensor stopped sending data this week."
    reply = "\n".join(f"{i + 1}. {line}" for i, line in enumerate(faulty))
    faithful = "\n".join(f"{i + 1}. {line}" for i, line in enumerate(lines))
    assert main._validated_chart_summary(faithful, allowed, len(lines)) is not None

    assert main._names_a_cause(f"1. Readings are from before the sensor was turned off on 3 October.\n"
                               f"2. The heart sensor stopped sending data this week.")
    assert main._validated_chart_summary(reply, allowed, len(lines)) is None


@pytest.mark.parametrize("text", ["Taylor stopped giving up on fractions after the first week.",
                                  "Focus was steady and the student stopped giving wrong answers."])
def test_stopped_giving_about_effort_is_not_a_cause(text):
    assert not main._names_a_cause(text)


def test_a_reply_saying_a_read_failed_is_still_accepted():
    """"Could not be read" rephrased as "failed to load" names no cause, so the filter leaves it."""
    lines = ["This student's practice totals could not be read, so the totals are not shown here today."]
    allowed = main._chart_summary_figures(lines)
    reply = "1. This student's practice totals failed to load, so the totals are not shown here today."
    assert main._validated_chart_summary(reply, allowed, 1) is not None


@pytest.mark.parametrize("line", [
    "Focus was lower on days spent not working through many questions at once.",
    "Focus was lower on days the student didn't work through many questions.",
    "Focus was higher on days the student did not work hard on fractions.",
    "Focus was higher on days the student didn't work on fractions.",
    # A month's or a date's first letters, not a date.
    "Focus was higher on days the student didn't work on decimals.",
    "Focus was higher on days the student didn't work on octagons.",
    "Focus was higher on days the student didn't work on the harder questions.",
    "Focus was higher on days the student didn't work on 3-digit numbers.",
    "Focus was higher on days the student didn't work on this topic.",
    # A number after "on" is a subject when a student is doing the work.
    "Focus was higher on days the student didn't work on 10 questions.",
    "Focus was higher on days the student didn't work on 2nd grade content.",
    # A sensor named earlier in the sentence is not the subject of the work.
    "Heart rate was higher on days the student didn't work on fractions.",
])
def test_a_reply_that_describes_the_students_work_is_still_accepted(line):
    """"Work" about the student's effort is not a cause for a sensor, so the filter leaves it."""
    allowed = main._chart_summary_figures([line])
    assert main._validated_chart_summary(f"1. {line}", allowed, 1) is not None


@pytest.mark.parametrize("text", ["The heart sensor wasn't working on Monday.",
                                  "The headband was not working on the 3rd.",
                                  "The headband was not working on Dec 3.",
                                  "The camera wasn't working on 12 October.",
                                  "The camera wasn't working on that day.",
                                  # Days no date list named: the sensor as subject is what counts.
                                  "The headband wasn't working on the first day.",
                                  "The camera wasn't working on the day of the test.",
                                  "The heart sensor wasn't working on that occasion.",
                                  "The headband wasn't working on Wed.",
                                  "The camera wasn't working on weekends.",
                                  "The headband wasn't working on some days.",
                                  "The webcam wasn't working on today.",
                                  "The headband was new. It wasn't working on the first day.",
                                  "The headband didn't work through the session.",
                                  # Subjects no list names: anything but a person is a cause.
                                  "The recording wasn't working.",
                                  "The readings weren't working.",
                                  "The connection wasn't working.",
                                  "The Muse wasn't working.",
                                  "The equipment wasn't working.",
                                  "The camera's feed wasn't working.",
                                  # First in its sentence, a capital is not taken for a name.
                                  "The headband was new. Bluetooth wasn't working.",
                                  # The sensor further back than the verb's own subject.
                                  "The headband the student wore wasn't working.",
                                  "The headband, which was new, wasn't working.",
                                  # "They" may be the devices; a capital mid-sentence is a device's name.
                                  "The headband and camera were new, but they weren't working.",
                                  "On Monday the Athena wasn't working.",
                                  "On Monday the Bluetooth wasn't working.",
                                  "1) Bluetooth wasn't working.",
                                  # Other failure verbs and phrases.
                                  "The headband never worked.",
                                  "The headband couldn't connect.",
                                  "The camera no longer works.",
                                  "The headband stopped responding.",
                                  "The camera had issues on Monday.",
                                  "The headband ran out of battery.",
                                  "The battery died halfway through.",
                                  "The headband failed to connect.",
                                  # Connecting is the device's whoever the subject, as "couldn't connect" is.
                                  "The student failed to connect the headband.",
                                  "The student couldn't connect the headband.",
                                  "The student had trouble connecting the headband.",
                                  "The student had problems pairing it.",
                                  "The headband couldn't connect on Monday.",
                                  "The camera failed to connect to the laptop.",
                                  # A device as the subject: any connect failure, whatever follows.
                                  "The headband couldn't connect to the app.",
                                  "The headband couldn't connect over Bluetooth.",
                                  "The headband couldn't connect that day.",
                                  "The headband wasn't connected yet.",
                                  "The headband hasn't been connected since Monday.",
                                  # A person as the subject: a device word past an adjective or a compound.
                                  "The student couldn't connect the new headband.",
                                  "The student couldn't connect the heart sensor.",
                                  "The student couldn't connect the headset.",
                                  # A person's connect failure with a time, and devices beyond the headband.
                                  "The student couldn't connect during the lesson.",
                                  "The student couldn't connect in the morning.",
                                  "The student couldn't connect today.",
                                  "The student couldn't connect during today's lesson.",
                                  "The student couldn't connect in today's session.",
                                  "The student couldn't connect on Monday.",
                                  "The student couldn't connect for most of the lesson.",
                                  "The student couldn't connect for the rest of the session.",
                                  "The student couldn't connect after the update.",
                                  "The student couldn't connect in class.",
                                  "The student couldn't connect on Monday morning and gave up.",
                                  # A time followed by another time or an adverb, and a device further on.
                                  "The student couldn't connect in class yesterday.",
                                  "The student couldn't connect in class either.",
                                  "The student couldn't connect in the morning before school.",
                                  "The student couldn't connect for most of the lesson today.",
                                  "The student couldn't connect in class with the headband.",
                                  "The student couldn't pair her earbuds.",
                                  "The student had trouble syncing her watch.",
                                  # "was not" is a negation like "wasn't", so any non-person subject counts.
                                  "On Monday the Bluetooth was not working.",
                                  "The Bluetooth also was not working."])
def test_a_sensor_not_working_is_a_cause_whatever_follows(text):
    assert main._names_a_cause(text)


@pytest.mark.parametrize("text", [
    "Focus came from the headband. She didn't work on fractions.",
    # A sensor ending the previous sentence is not the subject of a bare "not".
    "Readings came from the headband. Not working through every question kept focus steady.",
    "Focus was higher on days she did not work on word problems.",
    "The student who never worked ahead had steadier focus.",
    "Focus was lower on days the student did not record an answer quickly.",
    "It didn't work out as planned, so the session was shorter.",
    # The subject past an adverb, a relative clause, and a family noun.
    "The student sometimes didn't work through the harder questions.",
    "Students who struggled didn't work on the bonus questions.",
    "Your daughter didn't work on fractions this week.",
    "The student had trouble with fractions on Monday.",
    # An adverb before the auxiliary, and an auxiliary before the phrase's own verb.
    "The student also did not work ahead.",
    "The student has had trouble with fractions.",
    "The student also has had trouble with fractions.",
    # A group of people, and people named by where they are.
    "The class had trouble with fractions.",
    "The students in the class didn't work on fractions.",
    # Connecting ideas is maths, not a device.
    "The student failed to connect fractions to decimals.",
    "She couldn't connect the two ideas.",
    "The student had trouble connecting fractions to decimals.",
    "The student couldn't connect today's lesson to last week's.",
    # An idea as the subject, and "it" past the object.
    "The idea didn't connect with her.",
    "The lesson didn't connect.",
    "The student couldn't connect the ideas to it.",
    # An idea failing is teaching; a preposition that is not a time is not a when.
    "The method didn't work for her.",
    "The explanation didn't work at first.",
    "The student couldn't connect on a deeper level.",
    "The student couldn't connect the steps in her head.",
    "She couldn't connect in her head why it mattered.",
    "The student couldn't connect for long with the harder questions.",
    # A time word inside another word, or a count rather than a date.
    "The student couldn't connect ideas in the classroom discussion.",
    "The student couldn't connect in her daydreams.",
    "The student couldn't connect at the weekend's quiz.",
    "The student couldn't connect for 2 of the questions.",
    # A time word that does not end its clause is a place or a thing.
    "She couldn't connect in class discussions.",
    "She couldn't connect during break time with her classmates' ideas.",
    # A device only as where the material was shown, or inside a "what" clause.
    "The student couldn't connect fractions to the examples on the tablet.",
    "The student couldn't connect fractions to examples on tablets.",
    "The idea didn't connect with what she saw on the app.",
    "The student couldn't connect what the app showed to the lesson.",
])
def test_a_person_as_the_subject_is_effort_not_a_cause(text):
    assert not main._names_a_cause(text)


@pytest.mark.parametrize("text", ["Heart rate was not recorded because the sensor was turned off.",
                                  "Heart rate hasn't been recorded since 3 August.",
                                  "Expression readings weren't recorded this week.",
                                  # "Recording" in any form states the absence, not why.
                                  "The sensor wasn't recording heart rate because it was turned off on 3 October.",
                                  "The headband has not recorded anything since 3 October."])
def test_saying_nothing_was_recorded_is_an_absence_not_a_cause(text):
    assert not main._names_a_cause(text)


def test_the_prompt_forbids_naming_a_cause():
    prompt = main._chart_summary_prompt(_basis(), ["One point about focus that is long enough to pass."])
    assert "stopped working" in prompt and "why a sensor was off" in prompt


def test_a_degenerate_reply_of_fragments_is_rejected():
    """Well-formed and not a summary -- the floor, not just the ceiling."""
    assert main._validated_chart_summary("1. a\n2. b\n3. c", set(), 3) is None


# ── channel states ───────────────────────────────────────────────────────

def test_unreadable_consent_outranks_everything_else():
    """No claim about the family's decision has been earned (same order as `cellLabel`)."""
    basis = _basis(consent_retrieved=False)
    basis["channels"]["heart"] = {"enabled": False, "samples": 0,
                                  "revoked_at": "2026-08-03T16:00:00+00:00"}
    note = main._channel_absence("heart", basis)
    assert "could not be read" in note
    assert "3 August" not in note


def test_a_known_revocation_outranks_a_failed_signal_read():
    """The revocation comes from a different query, so it is still known."""
    basis = _basis(signals_retrieved=False)
    basis["channels"]["heart"] = {"enabled": False, "samples": 0,
                                  "revoked_at": "2026-08-03T16:00:00+00:00"}
    assert "turned off on 3 August" in main._channel_absence("heart", basis)


def test_a_failed_read_is_not_reported_as_nothing_recorded():
    basis = _basis(signals_retrieved=False)
    basis["channels"]["heart"] = {"enabled": True, "revoked_at": None, "samples": 0}
    assert "could not be read" in main._channel_absence("heart", basis)


def test_a_permitted_channel_with_no_samples_says_so_plainly():
    basis = _basis()
    basis["channels"]["heart"] = {"enabled": True, "revoked_at": None, "samples": 0}
    assert "nothing was recorded" in main._channel_absence("heart", basis)


def test_a_channel_with_a_reading_has_no_absence_note():
    assert main._channel_absence("heart", _basis()) is None


# ── the deterministic sentences ──────────────────────────────────────────

def test_a_failed_signal_read_never_reports_a_quiet_week():
    """`sessions` comes from the signal aggregate, so a failed read leaves it at 0."""
    # `sessions: 0` is load-bearing; with the happy path's 12 the test is vacuous.
    basis = _basis(signals_retrieved=False)
    basis["academic"] = {**basis["academic"], "sessions": 0}
    lines = main._rule_based_chart_summary(basis)
    assert not any("0 sessions" in line for line in lines)
    assert any("could not be read" in line for line in lines)


def test_a_failed_trend_read_is_not_reported_as_a_first_week():
    """An unread trend is empty, which would otherwise read as "only one week so far"."""
    lines = main._rule_based_chart_summary(
        _basis(trend_retrieved=False, trend={"focus": None, "calm": None}))
    assert not any("Only one week" in line for line in lines)
    assert any("term trend could not be read" in line for line in lines)


def test_the_eeg_channel_being_off_is_said_once_not_twice():
    """Focus and calm go off together, so two sentences read as two faults."""
    basis = _basis()
    basis["channels"]["eeg"] = {"enabled": False, "samples": 0, "revoked_at": None}
    lines = main._rule_based_chart_summary(basis)
    off = [line for line in lines if "turned off" in line and "Focus and calm" in line]
    assert len(off) == 1


def test_no_sentence_names_engagement_beside_focus():
    """They are one number (signal_mapping.py); naming both reads as two agreeing."""
    lines = main._rule_based_chart_summary(_basis())
    prompt = main._chart_summary_prompt(_basis(), lines)
    assert "engagement" not in " ".join(lines).lower()
    assert "engagement" not in prompt.lower()


def test_the_lifetime_totals_and_the_weekly_sessions_are_separate_sentences():
    """Joined, a lifetime accuracy reads as one earned over the last week."""
    lines = main._rule_based_chart_summary(_basis())
    totals = next(line for line in lines if "240" in line)
    assert "12 sessions" not in totals


def test_the_prompt_carries_no_student_identifier():
    """The shape of the week, not a record that identifies a child."""
    basis = _basis()
    prompt = main._chart_summary_prompt(basis, main._rule_based_chart_summary(basis))
    assert "student-1" not in prompt


# ── the endpoint ─────────────────────────────────────────────────────────

@pytest.fixture
def endpoint(monkeypatch):
    """The endpoint with its reads stubbed, so only its own logic is under test."""
    monkeypatch.setattr(main, "get_user", lambda r: {"id": "parent-1"})
    monkeypatch.setattr(main, "_verify_can_view_student", lambda v, s: None)
    monkeypatch.setattr(main, "_chart_summary_basis",
                        lambda *a, **k: _basis())
    return lambda: main.student_chart_summary(
        "student-1", None, main.ChartSummaryRequest())


def test_the_access_check_runs_before_the_rate_limit(monkeypatch):
    """Or a caller with no relationship gets a 429 masking the 403."""
    monkeypatch.setattr(main, "get_user", lambda r: {"id": "stranger"})

    def refuse(viewer, student_id):
        raise main.HTTPException(403, "You do not have access to this student")
    monkeypatch.setattr(main, "_verify_can_view_student", refuse)

    for _ in range(main._CHART_SUMMARY_RATE_LIMIT + 2):
        with pytest.raises(main.HTTPException) as e:
            main.student_chart_summary("student-1", None, main.ChartSummaryRequest())
        assert e.value.status_code == 403


def test_the_per_caller_rate_limit_answers_429_with_a_retry_after(endpoint, set_flag):
    set_flag("chart_summary_llm_enabled", False)
    for _ in range(main._CHART_SUMMARY_RATE_LIMIT):
        endpoint()
    with pytest.raises(main.HTTPException) as e:
        endpoint()
    assert e.value.status_code == 429
    assert int(e.value.headers["Retry-After"]) >= 1


def test_with_the_flag_off_no_socket_is_opened(endpoint, set_flag, monkeypatch):
    """Pinned off explicitly: the autouse fixture follows `_FEATURE_FLAG_DEFAULTS`."""
    called = []
    monkeypatch.setattr(main, "_llm_chart_summary_bounded",
                        lambda *a: called.append(a))
    set_flag("chart_summary_llm_enabled", False)
    out = endpoint()
    assert called == []
    assert out["source"] == "rule-based"
    assert len(out["summary"]) >= 3


def test_a_rejected_model_reply_is_distinguishable_from_never_asking(
        endpoint, set_flag, monkeypatch):
    set_flag("chart_summary_llm_enabled", True)
    monkeypatch.setattr(main, "_llm_chart_summary_bounded", lambda *a: None)
    assert endpoint()["source"] == "rule-based (model output rejected)"


def test_an_accepted_model_reply_replaces_the_sentences_and_says_so(
        endpoint, set_flag, monkeypatch):
    set_flag("chart_summary_llm_enabled", True)
    monkeypatch.setattr(main, "_llm_chart_summary_bounded",
                        lambda *a: ["one", "two", "three"])
    out = endpoint()
    assert out["source"] == "model-phrased"
    assert out["summary"] == ["one", "two", "three"]


def test_the_response_names_its_sensor_sentences(endpoint, set_flag):
    """The frontend hides these under "Hide sensor data"; an empty list would hide nothing."""
    set_flag("chart_summary_llm_enabled", False)
    out = endpoint()
    sensor = out["basis"]["sensor_lines"]
    assert [out["summary"][i].split()[1] for i in sensor] == ["focus", "calm", "heart"]


def test_the_model_path_rejects_a_reply_that_calls_calm_stress(monkeypatch):
    """The check is only as good as the call that passes the baseline into it."""
    lines = main._rule_based_chart_summary(_basis())
    reply = "\n".join(f"{i + 1}. {l.replace('Average calm', 'Average stress')}"
                      for i, l in enumerate(lines))
    monkeypatch.setattr(main.llm_client, "generate_text", lambda *a, **k: reply)
    assert main._llm_chart_summary("prompt", lines) is None


def test_the_three_reads_behind_one_response_report_separately(endpoint, set_flag):
    """One flag would make a partial summary read as entirely fine or entirely broken."""
    set_flag("chart_summary_llm_enabled", False)
    basis = endpoint()["basis"]
    assert {"signals_retrieved", "trend_retrieved",
            "stats_retrieved", "topics_retrieved"} <= set(basis)


# ── the four bounds ──────────────────────────────────────────────────────

def test_consent_is_read_once_and_passed_into_both_reads(monkeypatch):
    """Assert on what was asked for; two consent reads could disagree within one response."""
    reads = []
    channels = main.ReportChannels(heart=False, emotion=False, consent_retrieved=True)
    monkeypatch.setattr(main, "_reportable_channels",
                        lambda sid, inc=True: reads.append(sid) or channels)
    summary_args, trend_args = {}, {}
    monkeypatch.setattr(main, "_signal_summary",
                        lambda sid, days, **kw: summary_args.update(kw) or main._EMPTY_SUMMARY)
    monkeypatch.setattr(main, "_signal_trend",
                        lambda sid, weeks, **kw: trend_args.update(kw) or {"weeks": [], "retrieved": True})
    monkeypatch.setattr(main, "_stats_including_open_session",
                        lambda sid: {"total_questions": 0, "total_correct": 0, "retrieved": True})
    monkeypatch.setattr(main, "_topic_breakdown_with_state", lambda sid: ([], True))

    main._chart_summary_basis("student-1", 7, 8, True)
    assert reads == ["student-1"], "one consent read, shared by both queries"
    assert summary_args["include_heart"] is False
    assert trend_args["include_heart"] is False


def test_past_the_waiter_cap_the_model_pass_is_skipped(monkeypatch):
    """A sync caller blocked on the model holds a shared anyio threadpool slot."""
    original = main._chart_summary_waiters
    main._chart_summary_waiters = threading.BoundedSemaphore(1)
    held = threading.Event()
    release = threading.Event()

    def _slow(*_a, **_k):
        held.set()
        release.wait(timeout=10)
        return ["a" * 40, "b" * 40, "c" * 40]

    monkeypatch.setattr(main, "_llm_chart_summary_admitted", _slow)
    try:
        blocker = threading.Thread(
            target=lambda: main._llm_chart_summary_bounded("p", ["x"]))
        blocker.start()
        assert held.wait(timeout=10)
        assert main._llm_chart_summary_bounded("p", ["x"]) is None, \
            "the second caller queued instead of being turned away"
        release.set()
        blocker.join(timeout=10)
    finally:
        release.set()
        main._chart_summary_waiters = original


def test_time_spent_queueing_comes_out_of_the_budget_it_was_promised():
    """The wait and the work share one deadline. Asserts `<=`, never an exact elapsed time."""
    charged = []
    blocking, release = threading.Event(), threading.Event()

    def _work(prompt, _baseline, timeout=None):
        if prompt == "occupying":
            blocking.set()
            release.wait(timeout=10)
            return None
        charged.append(timeout)
        return None

    pool = ThreadPoolExecutor(max_workers=1)
    original_pool, original_llm = main._CHART_SUMMARY_LLM_POOL, main._llm_chart_summary
    original_timeout = main.CHART_SUMMARY_LLM_TIMEOUT
    main._CHART_SUMMARY_LLM_POOL = pool
    main._llm_chart_summary = _work
    main.CHART_SUMMARY_LLM_TIMEOUT = 5.0
    try:
        pool.submit(_work, "occupying", [])
        assert blocking.wait(timeout=10)
        # Let the queued call spend some of its budget waiting.
        waiter = threading.Thread(
            target=lambda: main._llm_chart_summary_admitted("queued", ["x"]))
        waiter.start()
        time.sleep(0.4)
        release.set()
        waiter.join(timeout=10)
    finally:
        release.set()
        pool.shutdown(wait=True)
        main._CHART_SUMMARY_LLM_POOL = original_pool
        main._llm_chart_summary = original_llm
        main.CHART_SUMMARY_LLM_TIMEOUT = original_timeout

    assert charged and charged[0] <= 5.0 - 0.3, \
        "the queued call was given a fresh budget instead of the remainder"


def test_an_abandoned_call_is_cancelled_rather_than_left_queued():
    """Asserted on the future's state: the shared deadline already stops the call, not the queueing."""
    submitted = []
    release = threading.Event()

    def _work(*_a, **_k):
        release.wait(timeout=10)
        return None

    class _Recording:
        """The real pool, with every future it hands out kept."""
        def __init__(self, pool):
            self._pool = pool

        def submit(self, fn, *a, **kw):
            future = self._pool.submit(fn, *a, **kw)
            submitted.append(future)
            return future

    pool = ThreadPoolExecutor(max_workers=1)
    original_pool, original_llm = main._CHART_SUMMARY_LLM_POOL, main._llm_chart_summary
    original_timeout = main.CHART_SUMMARY_LLM_TIMEOUT
    main._CHART_SUMMARY_LLM_POOL = _Recording(pool)
    main._llm_chart_summary = _work
    main.CHART_SUMMARY_LLM_TIMEOUT = 0.05
    try:
        blocker = pool.submit(_work)
        assert main._llm_chart_summary_bounded("queued", ["x"]) is None
        assert submitted, "nothing was submitted, so nothing was under test"
        assert submitted[0].cancelled(), \
            "the abandoned work is still sitting in the pool's queue"
        release.set()
        blocker.result(timeout=10)
    finally:
        release.set()
        pool.shutdown(wait=True)
        main._CHART_SUMMARY_LLM_POOL = original_pool
        main._llm_chart_summary = original_llm
        main.CHART_SUMMARY_LLM_TIMEOUT = original_timeout


def test_the_pool_is_shut_down_on_the_way_out():
    """The global must reset so a reload builds a fresh pool."""
    original = main._CHART_SUMMARY_LLM_POOL
    try:
        pool = main._chart_summary_pool()
        assert pool is main._chart_summary_pool(), "built twice"
        main._shutdown_chart_summary_pool()
        assert main._CHART_SUMMARY_LLM_POOL is None
        assert main._chart_summary_pool() is not pool
    finally:
        main._shutdown_chart_summary_pool()
        main._CHART_SUMMARY_LLM_POOL = original


def test_a_named_topic_carries_three_fields_and_not_the_whole_row(monkeypatch):
    """`_topic_breakdown` rows carry `stress`, which this response never gated on consent."""
    row = {"topic_id": "t1", "topic_name": "ordering", "accuracy": 91,
           "attempted_questions": 30, "correct_questions": 27,
           "stress": 0.8, "updated_at": "2026-09-01"}
    monkeypatch.setattr(main, "_reportable_channels",
                        lambda sid, inc=True: main.ReportChannels(
                            heart=False, emotion=False, consent_retrieved=True))
    monkeypatch.setattr(main, "_signal_summary", lambda *a, **k: main._EMPTY_SUMMARY)
    monkeypatch.setattr(main, "_signal_trend",
                        lambda *a, **k: {"weeks": [], "retrieved": True})
    monkeypatch.setattr(main, "_stats_including_open_session",
                        lambda sid: {"total_questions": 0, "total_correct": 0, "retrieved": True})
    monkeypatch.setattr(main, "_topic_breakdown_with_state", lambda sid: ([row], True))

    topics = main._chart_summary_basis("student-1", 7, 8, True)["topics"]
    for named in (topics["weakest"], topics["strongest"]):
        assert set(named) == {"topic_name", "accuracy", "attempted_questions"}


def test_a_failed_topics_read_is_not_reported_as_an_untouched_subject():
    """`_topic_breakdown` swallows its exception and answers `[]`."""
    basis = _basis(topics_retrieved=False)
    basis["topics"] = {"weakest": None, "strongest": None, "attempted_count": 0}
    lines = main._rule_based_chart_summary(basis)
    assert not any("No topic has been attempted yet" in line for line in lines)
    assert any("topic figures could not be read" in line for line in lines)


def test_the_topics_flag_outranks_having_no_attempted_topic():
    """A failed read and an untouched subject are both an empty list."""
    basis = _basis(topics_retrieved=False)
    lines = main._rule_based_chart_summary(basis)
    assert any("topic figures could not be read" in line for line in lines)
    assert not any("strongest attempted" in line for line in lines)


def test_the_read_state_is_reported_by_the_two_value_form(monkeypatch):
    """Split, not parameterised, so a caller cannot silently drop the flag."""
    class _Boom:
        def table(self, _name):
            raise RuntimeError("database unreachable")

    monkeypatch.setattr(main, "supabase", _Boom())
    rows, retrieved = main._topic_breakdown_with_state("student-1")
    assert rows == [] and retrieved is False
    assert main._topic_breakdown("student-1") == []


def test_zero_weeks_is_not_reported_as_one_week():
    """The rollup row is written at session close, so zero weeks is ordinary."""
    basis = _basis(trend={"focus": main._trend_direction([], "focus"),
                          "calm": main._trend_direction([], "calm")})
    lines = main._rule_based_chart_summary(basis)
    assert not any("Only one week" in line for line in lines)
    assert any("No week has a reading for it yet" in line for line in lines)


def test_the_zero_week_sentence_names_no_cause():
    """Several causes reach zero weeks and none is distinguishable here."""
    basis = _basis(trend={"focus": main._trend_direction([], "focus"),
                          "calm": main._trend_direction([], "calm")})
    assert basis["academic"]["sessions"] > 1, "the fixture has to contradict it"
    lines = main._rule_based_chart_summary(basis)

    joined = " ".join(lines)
    assert "this session's own readings" not in joined
    for claim in ("first session", "hasn't finished", "has not finished",
                  "not been written", "just started"):
        assert claim not in joined, f"the sentence explains itself with {claim!r}"


def test_one_week_still_says_one_week():
    one = main._trend_direction(_weeks(0.5), "focus")
    lines = main._rule_based_chart_summary(_basis(trend={"focus": one, "calm": one}))
    assert any("Only one week has readings" in line for line in lines)


def test_a_single_session_is_not_described_in_the_plural():
    basis = _basis()
    basis["academic"] = {**basis["academic"], "sessions": 1}
    lines = main._rule_based_chart_summary(basis)
    assert any("1 session was recorded" in line for line in lines)
    assert not any("1 sessions" in line for line in lines)


def test_the_basis_carries_the_read_state_it_was_given(monkeypatch):
    """The wiring, not just the field's presence; other tests build the basis by hand."""
    monkeypatch.setattr(main, "_reportable_channels",
                        lambda sid, inc=True: main.ReportChannels(
                            heart=False, emotion=False, consent_retrieved=True))
    monkeypatch.setattr(main, "_signal_summary", lambda *a, **k: main._EMPTY_SUMMARY)
    monkeypatch.setattr(main, "_signal_trend",
                        lambda *a, **k: {"weeks": [], "retrieved": True})
    monkeypatch.setattr(main, "_stats_including_open_session",
                        lambda sid: {"total_questions": 0, "total_correct": 0, "retrieved": True})
    # An empty permitted channel asks whether rows arrived; answered here, not by a database.
    monkeypatch.setattr(main, "_any_rows_since", lambda *a: False)

    monkeypatch.setattr(main, "_topic_breakdown_with_state", lambda sid: ([], False))
    assert main._chart_summary_basis("s", 7, 8, True)["topics_retrieved"] is False

    monkeypatch.setattr(main, "_topic_breakdown_with_state", lambda sid: ([], True))
    assert main._chart_summary_basis("s", 7, 8, True)["topics_retrieved"] is True


# ── a scale change, a withdrawn channel with data, unusable rows, a tie ──

def test_a_trend_across_a_score_scale_change_has_no_direction():
    """Scale 1 early and scale 2 late were reported as a rise from one to the other."""
    weeks = [{"focus": 0.3, "score_scale": {"min": 1, "max": 1}},
             {"focus": 0.7, "score_scale": {"min": 2, "max": 2}}]
    move = main._trend_direction(weeks, "focus")
    assert move["direction"] is None and move["mixed_scale"] is True
    basis = _basis(trend={"focus": move, "calm": move})
    lines = main._rule_based_chart_summary(basis)
    assert any("scored on different scales" in line for line in lines)
    assert not any("risen" in line for line in lines)


def test_one_scale_throughout_still_has_a_direction():
    weeks = [{"focus": 0.3, "score_scale": {"min": 2, "max": 2}},
             {"focus": 0.7, "score_scale": {"min": 2, "max": 2}}, {"focus": 0.8}]
    assert main._trend_direction(weeks, "focus")["direction"] == "up"


def test_a_stress_trend_across_two_calm_sources_has_no_direction_and_focus_keeps_one():
    """The calm source moves stress's unit and not focus's."""
    weeks = [{"focus": 0.3, "stress": 0.3,
              "score_scale": {"min": 2, "max": 2, "calm_sources": ["sdk"]}},
             {"focus": 0.7, "stress": 0.7,
              "score_scale": {"min": 2, "max": 2, "calm_sources": ["local"]}}]
    stress = main._trend_direction(weeks, "stress")
    assert stress["direction"] is None and stress["mixed_scale"] is True
    assert main._trend_direction(weeks, "focus")["direction"] == "up"


def test_a_calm_trend_across_two_calm_sources_has_no_direction_either():
    """Calm is the same number as stress, so the calm source moves its unit too."""
    weeks = [{"calm": 0.3, "score_scale": {"min": 2, "max": 2, "calm_sources": ["sdk"]}},
             {"calm": 0.7, "score_scale": {"min": 2, "max": 2, "calm_sources": ["local"]}}]
    calm = main._trend_direction(weeks, "calm")
    assert calm["direction"] is None and calm["mixed_scale"] is True


# ── calm, body arousal and the usual ─────────────────────────────────────

def _usual(arousal=None, **measures):
    """`_personal_baseline`'s shape; unnamed measures are `no_current`."""
    out = {m: {"status": "no_current"} for m in main._USUAL_MEASURES}
    out.update(measures)
    body = main._body_arousal((0, 0, 0, 0), frozenset(), False)
    return {"retrieved": True, "body_arousal": {**body, **(arousal or {})}, "measures": out}


def _measured(share=0.22, **arousal):
    basis = _basis(usual=_usual(arousal={"state": "measured", **arousal}))
    basis["averages"]["body_arousal"] = share
    return basis


def test_the_summary_says_calm_and_never_average_stress():
    lines = main._rule_based_chart_summary(_basis())
    assert any(line.startswith("Average calm is 59%") for line in lines)
    assert not any("stress" in line.lower() for line in lines)


def test_the_calm_trend_is_read_from_calm():
    lines = main._rule_based_chart_summary(_basis())
    assert any("held steady from 60% to 59%" in line for line in lines)


def _arousal_line(basis):
    return next(l for l in main._rule_based_chart_summary(basis) if l.startswith("Body arousal"))


def test_body_arousal_is_stated_with_its_caveat():
    line = _arousal_line(_measured())
    assert "22%" in line
    assert "excitement, effort and movement as well as with stress" in line


def test_body_arousal_is_defined_as_what_heart_stress_measures():
    """Usable readings against the lesson's own opening rate, not "lesson time" or "resting"."""
    line = _arousal_line(_measured())
    assert ("the share of the headband's usable heart readings that were at least 10 beats a "
            "minute above the rate it measured at the start of each lesson") in line
    assert "lesson time" not in line and "resting" not in line


def test_a_thin_body_arousal_says_it_is_rough():
    assert "only a few readings" in _arousal_line(_measured(few_readings=True))
    assert "only a few readings" not in _arousal_line(_measured(few_readings=False))


def test_body_arousal_beside_an_open_lesson_says_that_lesson_is_not_counted():
    assert "still in progress are not counted" in _arousal_line(_measured(pending=True))
    assert "still in progress" not in _arousal_line(_measured(pending=False))


@pytest.mark.parametrize("state,expected", [
    ("calibrating", "still measuring this student's starting heart rate"),
    ("pending", "a lesson is still in progress, and it is worked out when the lesson finishes"),
    ("unusable", "heart readings were not steady enough to use"),
    ("camera_only", "only by the headband's heart sensor, not the camera"),
    ("none", "the headband's heart sensor recorded nothing"),
])
def test_no_body_arousal_says_why_and_never_zero(state, expected):
    line = _arousal_line(_basis(usual=_usual(arousal={"state": state})))
    assert expected in line
    assert "0%" not in line


def test_unusable_heart_readings_do_not_claim_a_lesson_is_open():
    line = _arousal_line(_basis(usual=_usual(arousal={"state": "unusable"})))
    assert "finishes" not in line


def test_a_declined_heart_channel_says_nothing_about_body_arousal():
    basis = _basis(usual=_usual(arousal={"state": "not_requested"}))
    assert not any(l.startswith("Body arousal") for l in main._rule_based_chart_summary(basis))


@pytest.mark.parametrize("state,expected", [
    ("not_retrieved", "could not be read this time"),
    ("unknown", "whether a lesson is still in progress could not be checked"),
])
def test_an_unread_body_arousal_says_so_rather_than_vanishing(state, expected):
    line = _arousal_line(_basis(usual=_usual(arousal={"state": state})))
    assert expected in line
    assert "%" not in line


def test_a_measured_share_whose_open_lessons_went_unchecked_says_so():
    assert "could not be checked" in _arousal_line(_measured(pending=None))


def test_a_pending_lesson_names_no_sensor():
    """An open lesson's rows may be camera rows, which never name a sensor."""
    line = _arousal_line(_basis(usual=_usual(arousal={"state": "pending"})))
    assert "headband" not in line and "camera" not in line


def test_poor_contact_rows_in_an_open_lesson_are_pending_not_nothing(monkeypatch):
    """`any_rows` sees rows the usable count does not; they are a lesson, not silence."""
    from tests.test_access_control import _FakeSupabase
    monkeypatch.setattr(main, "supabase", _FakeSupabase({
        "signal_daily_rollup": [],
        "sessions": [{"id": "o", "user_id": "s", "started_at": main._utc_now().isoformat(),
                      "ended_at": None}],
        "heart_signals": [{"user_id": "s", "ts": main._utc_now().isoformat(), "trusted": False}]}))
    monkeypatch.setattr(main, "_reportable_channels",
                        lambda sid, inc=True: main.ReportChannels(
                            heart=True, emotion=False, consent_retrieved=True))
    monkeypatch.setattr(main, "_signal_summary", lambda *a, **k: {
        **main._EMPTY_SUMMARY, "cognitive_samples": 400, "heart_samples": 0})
    monkeypatch.setattr(main, "_stats_including_open_session",
                        lambda sid: {"total_questions": 0, "total_correct": 0, "retrieved": True})
    monkeypatch.setattr(main, "_topic_breakdown_with_state", lambda sid: ([], True))
    monkeypatch.setattr(main, "_any_rows_since", lambda table, *a: table == "heart_signals")

    basis = main._chart_summary_basis("s", 7, 8, True)
    lines = main._rule_based_chart_summary(basis)

    assert basis["usual"]["body_arousal"]["state"] == "pending"
    assert not any("recorded nothing" in l for l in lines)


def test_an_unchecked_heart_table_leaves_body_arousal_unknown(monkeypatch):
    monkeypatch.setattr(main, "_reportable_channels",
                        lambda sid, inc=True: main.ReportChannels(
                            heart=True, emotion=False, consent_retrieved=True))
    monkeypatch.setattr(main, "_signal_summary", lambda *a, **k: {
        **main._EMPTY_SUMMARY, "cognitive_samples": 400, "heart_samples": 0})
    monkeypatch.setattr(main, "_stats_including_open_session",
                        lambda sid: {"total_questions": 0, "total_correct": 0, "retrieved": True})
    monkeypatch.setattr(main, "_topic_breakdown_with_state", lambda sid: ([], True))
    monkeypatch.setattr(main, "_any_rows_since",
                        lambda table, *a: None if table == "heart_signals" else True)

    assert main._chart_summary_basis("s", 7, 8, True)["usual"]["body_arousal"]["state"] == \
        "unknown"


def test_heart_rate_variability_is_stated_beside_heart_rate():
    basis = _basis()
    basis["averages"]["rmssd_ms"] = 41.6
    assert "Average heart rate is 72 bpm, and heart-rate variability is 42 ms." in \
        main._rule_based_chart_summary(basis)


def test_the_comparison_with_the_usual_is_in_words_only():
    basis = _basis(usual=_usual(
        focus={"status": "compared", "verdict": "about_usual"},
        calm={"status": "compared", "verdict": "lower"},
        heart_rate_bpm={"status": "not_enough_history"},
        rmssd_ms={"status": "not_comparable", "reason": "sensor_changed"}))
    lines = main._rule_based_chart_summary(basis)
    assert ("Compared with this student's own earlier days, focus was about usual and calm "
            "was lower than usual.") in lines
    assert ("Heart rate does not have enough earlier days yet to say what is usual for "
            "this student.") in lines
    assert ("Heart-rate variability cannot be compared with earlier days, because this "
            "period's heart readings came from more than one sensor.") in lines


@pytest.mark.parametrize("reason,why", [
    ("mixed_scale", "the headband's scoring changed during this period"),
    ("scale_unknown", "this period's scoring was not recorded"),
])
def test_each_not_comparable_reason_gives_its_own_cause(reason, why):
    basis = _basis(usual=_usual(calm={"status": "not_comparable", "reason": reason}))
    assert f"Calm cannot be compared with earlier days, because {why}." in \
        main._rule_based_chart_summary(basis)


def test_a_period_still_being_summarised_is_not_called_a_change():
    """An open lesson leaves the scale unknown for now; "changed" would be a false claim."""
    basis = _basis(usual=_usual(focus={"status": "pending"}, calm={"status": "pending"}))
    lines = main._rule_based_chart_summary(basis)
    assert ("Focus and calm will be compared with earlier days once this period's lessons "
            "have finished.") in lines
    assert not any("changed" in l or "not have enough" in l for l in lines)


def test_an_unread_usual_says_so_rather_than_saying_nothing():
    basis = _basis(usual={**_usual(), "retrieved": False})
    assert ("How these figures compare with this student's earlier days could not be read."
            in main._rule_based_chart_summary(basis))


def test_sensor_lines_are_exactly_the_sentences_about_readings():
    basis = _measured(0.1)
    basis["usual"]["measures"]["focus"] = {"status": "compared", "verdict": "higher"}
    lines, sensor = main._chart_summary_lines(basis)
    shown = [l for i, l in enumerate(lines) if i not in sensor]
    assert [lines[i].split()[0] for i in sensor] == \
        ["Average", "Average", "Average", "Body", "Compared"]
    assert all(not any(w in l for w in ("focus", "calm", "heart", "arousal")) for l in shown)


def _reply(lines):
    return "\n".join(f"{i + 1}. {l}" for i, l in enumerate(lines))


def _validate(reply_lines, lines):
    return main._validated_chart_summary(_reply(reply_lines), main._chart_summary_figures(lines),
                                         len(lines), lines)


def _full_basis():
    basis = _measured(0.22)
    basis["usual"]["measures"]["focus"] = {"status": "compared", "verdict": "higher"}
    basis["averages"]["rmssd_ms"] = 41.6
    return basis


def test_every_number_in_the_new_sentences_is_an_allowed_figure():
    lines = main._rule_based_chart_summary(_full_basis())
    assert _validate(lines, lines) == lines


def test_a_reply_that_reorders_sensor_and_other_points_is_rejected():
    """`sensor_lines` indexes the baseline; a reordered reply would hide the wrong sentences."""
    lines, sensor = main._chart_summary_lines(_full_basis())
    other = next(i for i in range(len(lines)) if i not in sensor)
    swapped = list(lines)
    swapped[other], swapped[sensor[0]] = lines[sensor[0]], lines[other]
    assert _validate(swapped, lines) is None


def test_a_reply_that_drops_what_a_sensor_point_is_about_is_rejected():
    """The comparison point has no number, so only its wording keeps it a sensor line."""
    lines = main._rule_based_chart_summary(_full_basis())
    usual = next(i for i, l in enumerate(lines) if l.startswith("Compared with"))
    vague = list(lines)
    vague[usual] = "Compared with earlier days, things looked much as usual."
    assert _validate(vague, lines) is None


def test_a_reply_that_moves_a_figure_to_another_point_is_rejected():
    """Allowed numbers, wrong place: the swap the global check let through."""
    lines = main._rule_based_chart_summary(_full_basis())
    focus = next(i for i, l in enumerate(lines) if l.startswith("Average focus"))
    calm = next(i for i, l in enumerate(lines) if l.startswith("Average calm"))
    moved = list(lines)
    moved[focus] = lines[focus].replace("63%", "59%", 1)
    moved[calm] = lines[calm].replace("59%", "63%", 1)
    assert moved != lines
    assert _validate(moved, lines) is None


def test_a_reply_that_calls_calm_stress_is_rejected():
    lines = main._rule_based_chart_summary(_basis())
    swapped = [l.replace("Average calm", "Average stress") for l in lines]
    assert swapped != lines
    reply = "\n".join(f"{i + 1}. {l}" for i, l in enumerate(swapped))
    assert main._validated_chart_summary(reply, main._chart_summary_figures(lines),
                                         len(lines), lines) is None


def test_the_basis_turns_stress_into_calm_for_the_average_and_the_trend(monkeypatch):
    monkeypatch.setattr(main, "_reportable_channels",
                        lambda sid, inc=True: main.ReportChannels(
                            heart=False, emotion=False, consent_retrieved=True))
    monkeypatch.setattr(main, "_signal_summary", lambda *a, **k: {
        **main._EMPTY_SUMMARY, "focus": 0.5, "stress": 0.25, "cognitive_samples": 400})
    monkeypatch.setattr(main, "_signal_trend", lambda *a, **k: {"retrieved": True, "weeks": [
        {"stress": 0.6, "score_scale": None}, {"stress": 0.2, "score_scale": None}]})
    monkeypatch.setattr(main, "_stats_including_open_session",
                        lambda sid: {"total_questions": 0, "total_correct": 0, "retrieved": True})
    monkeypatch.setattr(main, "_topic_breakdown_with_state", lambda sid: ([], True))

    basis = main._chart_summary_basis("s", 7, 8, True)

    assert basis["averages"]["calm"] == 0.75
    assert "stress" not in basis["averages"] and "stress" not in basis["trend"]
    assert basis["trend"]["calm"]["direction"] == "up"
    assert basis["usual"]["measures"]["calm"]["current"] == 0.75


def test_the_trend_and_the_usual_share_one_rollup_read(monkeypatch):
    from tests.test_access_control import _FakeSupabase
    fake = _FakeSupabase({"signal_daily_rollup": [
        {"user_id": "s", "day": main._school_today().isoformat(), "channel": "cognitive",
         "avg_focus": 0.5, "avg_stress": 0.5, "trusted_sample_count": 100,
         "score_scale_min": 3, "score_scale_max": 3, "calm_sources": ["sdk"]}]})
    monkeypatch.setattr(main, "supabase", fake)
    monkeypatch.setattr(main, "_reportable_channels",
                        lambda sid, inc=True: main.ReportChannels(
                            heart=False, emotion=False, consent_retrieved=True))
    monkeypatch.setattr(main, "_signal_summary", lambda *a, **k: {
        **main._EMPTY_SUMMARY, "focus": 0.5, "stress": 0.5, "cognitive_samples": 400})
    monkeypatch.setattr(main, "_stats_including_open_session",
                        lambda sid: {"total_questions": 0, "total_correct": 0, "retrieved": True})
    monkeypatch.setattr(main, "_topic_breakdown_with_state", lambda sid: ([], True))

    basis = main._chart_summary_basis("s", 7, 8, True)

    assert fake.table_calls.count("signal_daily_rollup") == 1
    assert basis["trend"]["focus"]["weeks_with_data"] == 1, "the trend saw the shared rows"
    assert basis["usual"]["measures"]["focus"]["status"] == "not_enough_history"


def test_lists_join_with_the_conjunction_asked_for():
    assert main._list_words(["a"]) == "a"
    assert main._list_words(["a", "b", "c"]) == "a, b and c"
    assert main._list_words(["a", "b"], "or") == "a or b"


def test_the_weekly_summary_states_calm_not_stress(monkeypatch):
    from tests.test_access_control import _FakeSupabase
    monkeypatch.setattr(main, "supabase", _FakeSupabase({
        "cognitive_signals": [{"user_id": "s", "ts": main._utc_now().isoformat(),
                               "focus": 0.5, "stress": 0.4}],
        "face_signals": [], "heart_signals": [], "sessions": [], "signal_daily_rollup": []}))
    summary = main._weekly_signal_report("s", include_heart=False, include_emotion=False)["summary"]
    assert "average calm was 60%" in summary
    assert "stress" not in summary


def test_a_withdrawn_eeg_channel_with_readings_states_them():
    """EEG is read regardless of withdrawal; its figures were dropped as 'not recorded'."""
    basis = _basis(channels={
        "eeg": {"enabled": False, "revoked_at": "2026-09-19T12:00:00+00:00", "samples": 400},
        "heart": {"enabled": True, "revoked_at": None, "samples": 120}})
    lines = main._rule_based_chart_summary(basis)
    assert any(line.startswith("Average focus is 63%") for line in lines)
    assert any("from before the sensor was turned off on 19 September" in line for line in lines)
    assert not any("was not recorded because" in line for line in lines)


def test_a_withdrawn_channel_with_no_readings_still_says_it_was_off():
    basis = _basis(channels={
        "eeg": {"enabled": False, "revoked_at": None, "samples": 0},
        "heart": {"enabled": True, "revoked_at": None, "samples": 120}},
        averages={"focus": None, "calm": None, "heart_rate_bpm": 72.4})
    lines = main._rule_based_chart_summary(basis)
    assert "Focus and calm was not recorded because the sensor was turned off." in lines


@pytest.mark.parametrize("any_rows,expected", [
    (True, "the readings were rejected rather than missing"),
    (False, "was permitted but nothing was recorded"),
    (None, "whether anything was recorded could not be read"),
])
def test_unusable_rows_are_told_from_none_and_from_unknown(any_rows, expected):
    """The RPC counts usable samples, so zero could not tell 'unusable' from 'nothing'."""
    basis = _basis(channels={
        "eeg": {"enabled": True, "revoked_at": None, "samples": 0, "any_rows": any_rows},
        "heart": {"enabled": True, "revoked_at": None, "samples": 120}},
        averages={"focus": None, "calm": None, "heart_rate_bpm": 72.4})
    assert any(expected in line for line in main._rule_based_chart_summary(basis))


def test_the_basis_asks_for_rows_only_when_a_permitted_channel_has_no_samples(monkeypatch):
    asked = []
    monkeypatch.setattr(main, "_reportable_channels",
                        lambda sid, inc=True: main.ReportChannels(
                            heart=True, emotion=False, consent_retrieved=True))
    monkeypatch.setattr(main, "_signal_summary",
                        lambda *a, **k: {**main._EMPTY_SUMMARY, "cognitive_samples": 40})
    monkeypatch.setattr(main, "_signal_trend", lambda *a, **k: {"weeks": [], "retrieved": True})
    monkeypatch.setattr(main, "_stats_including_open_session",
                        lambda sid: {"total_questions": 0, "total_correct": 0, "retrieved": True})
    monkeypatch.setattr(main, "_topic_breakdown_with_state", lambda sid: ([], True))
    monkeypatch.setattr(main, "_any_rows_since", lambda table, *a: asked.append(table) or True)
    basis = main._chart_summary_basis("s", 7, 8, True)
    assert asked == ["heart_signals"]
    assert basis["channels"]["heart"]["any_rows"] is True
    assert "any_rows" not in basis["channels"]["eeg"]


def test_a_tie_between_every_topic_is_not_read_as_one_topic():
    """Both at 100%: min() and max() returned the first, and the summary said 'Only ordering'."""
    tied = {"topic_name": "ordering", "accuracy": 100, "attempted_questions": 4}
    basis = _basis(topics={"weakest": tied, "strongest": tied, "attempted_count": 2,
                           "scored_count": 2})
    lines = main._rule_based_chart_summary(basis)
    assert "All 2 attempted topics are at 100%, so none stands out as strongest or weakest." in lines
    assert not any(line.startswith("Only ") for line in lines)


def test_the_chart_summary_reads_open_sessions_once(monkeypatch):
    """The trend and the usual must answer from one read, or a lesson closing between splits them."""
    from tests.test_access_control import _FakeSupabase
    fake = _FakeSupabase({"signal_daily_rollup": [], "heart_signals": [], "sessions": [
        {"id": "o", "user_id": "s", "started_at": main._utc_now().isoformat(), "ended_at": None}]})
    monkeypatch.setattr(main, "supabase", fake)
    monkeypatch.setattr(main, "_reportable_channels",
                        lambda sid, inc=True: main.ReportChannels(
                            heart=True, emotion=False, consent_retrieved=True))
    monkeypatch.setattr(main, "_signal_summary", lambda *a, **k: {
        **main._EMPTY_SUMMARY, "cognitive_samples": 400, "heart_samples": 5})
    monkeypatch.setattr(main, "_stats_including_open_session",
                        lambda sid: {"total_questions": 0, "total_correct": 0, "retrieved": True})
    monkeypatch.setattr(main, "_topic_breakdown_with_state", lambda sid: ([], True))

    main._chart_summary_basis("s", 7, 8, True)

    assert fake.table_calls.count("sessions") == 1
