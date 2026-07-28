"""Session save/restore (.pbsession) — feature 9, 1.2.0."""

from __future__ import annotations

import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from app.analysis import analyze_folder
from app.classification import build_analysis_result
from app.enums import DestinationCategory, DetectedType
from app.errors import SessionError
from app.manual_overrides import assign_category
from app.metadata import MetadataReader
from app.models import AppSettings, PhotoMetadata
from app.review_queue import DISMISS_NOT_BRACKET_MARKER, SPLIT_KEEP_SINGLE_MARKER, dismiss_candidate, split_series
from app.series_manager import SeriesManager
from app.session import SavedSeries, SessionData, build_session, load_session, reconcile_session_state, restore_session, save_session

BASE_TIME = datetime(2026, 7, 1, 12, 0, 0)
JPEG_BYTES = bytes.fromhex("FFD8FFE000104A46494600010101006000600000FFD9")


def make_metadata(name: str, seconds: float, exposure: float | None = None, detected_type: DetectedType = DetectedType.DNG) -> PhotoMetadata:
    return PhotoMetadata(
        source_path=Path("C:/photos") / name,
        relative_path=Path(name),
        file_name=name,
        extension=Path(name).suffix.lower(),
        detected_type=detected_type,
        file_size=1000,
        mtime_ns=1,
        make="Canon",
        model="EOS R5",
        capture_time=BASE_TIME + timedelta(seconds=seconds),
        exposure_compensation=exposure,
    )


def build_test_analysis(metadata_items):
    return build_analysis_result(Path("C:/photos"), metadata_items, AppSettings(), "fp", BASE_TIME)


def bracket_metadata(prefix: str, offset: float = 0.0) -> list[PhotoMetadata]:
    """Three frames with real exposure variance: a genuine automatic bracket."""
    return [
        make_metadata(f"{prefix}_1.dng", offset + 0.0, -1.0),
        make_metadata(f"{prefix}_2.dng", offset + 0.3, 0.0),
        make_metadata(f"{prefix}_3.dng", offset + 0.6, 1.0),
    ]


class SessionDataRoundTripTests(unittest.TestCase):
    def _sample(self) -> SessionData:
        return SessionData(
            schema_version=1,
            saved_at="2026-07-01T12:00:00",
            source_root="C:/photos",
            output_parent="C:/output",
            settings_snapshot=AppSettings().snapshot(),
            fingerprint="abc123",
            analysis_created_at="2026-07-01T12:00:00",
            metadata_cache=[],
            series=[SavedSeries(raw_paths=["a.dng", "b.dng", "c.dng"], series_size=3, automatic=True, manually_confirmed=False, custom_name="Sunset")],
            file_overrides={"x.dng": "RAW"},
            deselected_paths=["y.dng"],
            dismissed_not_bracket_paths=["z.dng"],
            split_keep_single_paths=["w.dng"],
        )

    def test_to_dict_from_dict_round_trip(self):
        import json

        original = self._sample()
        restored = SessionData.from_dict(json.loads(json.dumps(original.to_dict())))
        self.assertEqual(restored.source_root, original.source_root)
        self.assertEqual(restored.output_parent, original.output_parent)
        self.assertEqual(restored.fingerprint, original.fingerprint)
        self.assertEqual(restored.file_overrides, original.file_overrides)
        self.assertEqual(restored.deselected_paths, original.deselected_paths)
        self.assertEqual(restored.dismissed_not_bracket_paths, original.dismissed_not_bracket_paths)
        self.assertEqual(restored.split_keep_single_paths, original.split_keep_single_paths)
        self.assertEqual(len(restored.series), 1)
        self.assertEqual(restored.series[0].raw_paths, ["a.dng", "b.dng", "c.dng"])
        self.assertEqual(restored.series[0].custom_name, "Sunset")

    def test_from_dict_rejects_a_newer_schema_version(self):
        data = self._sample().to_dict()
        data["schema_version"] = 999
        with self.assertRaises(SessionError):
            SessionData.from_dict(data)

    def test_from_dict_requires_a_source_root(self):
        with self.assertRaises(SessionError):
            SessionData.from_dict({})

    def test_from_dict_defaults_missing_optional_fields_instead_of_crashing(self):
        # Must not raise even though almost everything but source_root is absent.
        session = SessionData.from_dict({"source_root": "C:/photos"})
        self.assertEqual(session.series, [])
        self.assertEqual(session.file_overrides, {})
        self.assertEqual(session.deselected_paths, [])
        self.assertEqual(session.metadata_cache, [])

    def test_from_dict_tolerates_garbage_in_list_and_dict_fields(self):
        session = SessionData.from_dict(
            {
                "source_root": "C:/photos",
                "series": "not-a-list",
                "file_overrides": ["not", "a", "dict"],
                "deselected_paths": {"not": "a-list"},
            }
        )
        self.assertEqual(session.series, [])
        self.assertEqual(session.file_overrides, {})
        self.assertEqual(session.deselected_paths, [])


class SaveLoadFileTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "test.pbsession"

    def test_save_then_load_round_trip(self):
        session = SessionData(
            schema_version=1,
            saved_at="2026-07-01T12:00:00",
            source_root="C:/photos",
            output_parent=None,
            settings_snapshot=AppSettings().snapshot(),
            fingerprint="fp",
            analysis_created_at="2026-07-01T12:00:00",
            metadata_cache=[],
            series=[],
            file_overrides={},
            deselected_paths=[],
            dismissed_not_bracket_paths=[],
            split_keep_single_paths=[],
        )
        save_session(session, self.path)
        loaded = load_session(self.path)
        self.assertEqual(loaded.source_root, "C:/photos")
        self.assertEqual(loaded.fingerprint, "fp")

    def test_loading_a_missing_file_raises_session_error_not_a_bare_oserror(self):
        with self.assertRaises(SessionError):
            load_session(self.path)

    def test_loading_corrupted_json_raises_session_error_not_crash(self):
        self.path.write_text("{not valid json at all", encoding="utf-8")
        with self.assertRaises(SessionError):
            load_session(self.path)

    def test_loading_a_json_array_instead_of_an_object_raises_session_error(self):
        self.path.write_text("[1, 2, 3]", encoding="utf-8")
        with self.assertRaises(SessionError):
            load_session(self.path)

    def test_a_corrupted_session_file_never_touches_any_other_file(self):
        sentinel = Path(self._tmp.name) / "untouched.txt"
        sentinel.write_text("original", encoding="utf-8")
        self.path.write_text("garbage", encoding="utf-8")
        with self.assertRaises(SessionError):
            load_session(self.path)
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "original")


class BuildSessionTests(unittest.TestCase):
    def test_captures_the_automatic_series_signature(self):
        analysis = build_test_analysis(bracket_metadata("A"))
        manager = SeriesManager.from_analysis(analysis)
        session = build_session(analysis, manager, AppSettings(), Path("C:/output"))
        self.assertEqual(len(session.series), 1)
        self.assertEqual(session.series[0].raw_paths, ["A_1.dng", "A_2.dng", "A_3.dng"])
        self.assertEqual(session.series[0].series_size, 3)
        self.assertTrue(session.series[0].automatic)

    def test_captures_manual_override_and_deselection(self):
        analysis = build_test_analysis([make_metadata("single.dng", 0.0)])
        manager = SeriesManager.from_analysis(analysis)
        file = analysis.files[0]
        assign_category(file, DestinationCategory.SKIP)
        file.selected = False
        session = build_session(analysis, manager, AppSettings(), None)
        self.assertEqual(session.file_overrides, {"single.dng": "SKIP"})
        self.assertEqual(session.deselected_paths, ["single.dng"])

    def test_captures_dismissed_near_miss_candidates(self):
        analysis = build_test_analysis(bracket_metadata("A") + [make_metadata("solo.dng", 5.0)])
        for candidate in analysis.bracket_candidates:
            dismiss_candidate(candidate)
        session = build_session(analysis, SeriesManager.from_analysis(analysis), AppSettings(), None)
        self.assertTrue(
            any(DISMISS_NOT_BRACKET_MARKER and path for path in session.dismissed_not_bracket_paths),
        )

    def test_captures_split_keep_single(self):
        analysis = build_test_analysis(bracket_metadata("A"))
        manager = SeriesManager.from_analysis(analysis)
        split_series(manager, manager.series[0])
        session = build_session(analysis, manager, AppSettings(), None)
        self.assertEqual(set(session.split_keep_single_paths), {"A_1.dng", "A_2.dng", "A_3.dng"})

    def test_output_parent_is_none_when_not_provided(self):
        analysis = build_test_analysis([make_metadata("single.dng", 0.0)])
        session = build_session(analysis, SeriesManager.from_analysis(analysis), AppSettings(), None)
        self.assertIsNone(session.output_parent)


def _saved_series_from(manager: SeriesManager, index: int = 0, **overrides) -> SavedSeries:
    series = manager.series[index]
    fields = dict(
        raw_paths=sorted(file.metadata.relative_path.as_posix() for file in series.raw_files),
        series_size=series.series_size,
        automatic=series.automatic,
        manually_confirmed=series.manually_confirmed,
        custom_name=series.custom_name,
    )
    fields.update(overrides)
    return SavedSeries(**fields)


def _empty_session(**overrides) -> SessionData:
    fields = dict(
        schema_version=1,
        saved_at="2026-07-01T12:00:00",
        source_root="C:/photos",
        output_parent=None,
        settings_snapshot=AppSettings().snapshot(),
        fingerprint="fp",
        analysis_created_at="2026-07-01T12:00:00",
        metadata_cache=[],
        series=[],
        file_overrides={},
        deselected_paths=[],
        dismissed_not_bracket_paths=[],
        split_keep_single_paths=[],
    )
    fields.update(overrides)
    return SessionData(**fields)


class ReconcileSessionStateTests(unittest.TestCase):
    def test_matching_automatic_series_reapplies_custom_name_and_manually_confirmed(self):
        analysis = build_test_analysis(bracket_metadata("A"))
        manager = SeriesManager.from_analysis(analysis)
        saved = _saved_series_from(manager, custom_name="Sunset", manually_confirmed=True)
        session = _empty_session(series=[saved])

        warnings = reconcile_session_state(analysis, manager, session)

        self.assertEqual(warnings, [])
        self.assertEqual(len(manager.series), 1)
        target = manager.series[0]
        self.assertEqual(target.custom_name, "Sunset")
        self.assertTrue(target.manually_confirmed)
        self.assertIn("Sunset", str(target.destination_root))

    def test_manual_grouping_the_detector_would_not_find_gets_recreated(self):
        # Flat exposure across all three: the automatic detector will not group
        # these on its own, so this exercises the "recreate a manual series" path.
        metadata = [make_metadata(f"M_{i}.dng", i * 0.3, 0.0) for i in range(3)]
        analysis = build_test_analysis(metadata)
        manager = SeriesManager.from_analysis(analysis)
        self.assertEqual(manager.series, [])  # sanity: nothing automatic formed

        saved = SavedSeries(
            raw_paths=sorted(file.metadata.relative_path.as_posix() for file in analysis.files),
            series_size=3,
            automatic=False,
            manually_confirmed=True,
            custom_name=None,
        )
        session = _empty_session(series=[saved])

        warnings = reconcile_session_state(analysis, manager, session)

        self.assertEqual(warnings, [])
        self.assertEqual(len(manager.series), 1)
        self.assertFalse(manager.series[0].automatic)
        self.assertEqual(len(manager.series[0].raw_files), 3)

    def test_missing_file_in_a_saved_series_produces_a_warning_not_a_crash(self):
        analysis = build_test_analysis([make_metadata("only.dng", 0.0, 0.0)])
        manager = SeriesManager.from_analysis(analysis)
        saved = SavedSeries(raw_paths=["only.dng", "gone_1.dng", "gone_2.dng"], series_size=3, automatic=False, manually_confirmed=True)
        session = _empty_session(series=[saved])

        warnings = reconcile_session_state(analysis, manager, session)

        self.assertTrue(warnings)
        self.assertEqual(manager.series, [])

    def test_split_keep_single_paths_undoes_a_freshly_redetected_automatic_series(self):
        analysis = build_test_analysis(bracket_metadata("A"))
        manager = SeriesManager.from_analysis(analysis)
        self.assertEqual(len(manager.series), 1)  # sanity: it would auto-form again
        paths = [file.metadata.relative_path.as_posix() for file in manager.series[0].raw_files]
        session = _empty_session(split_keep_single_paths=paths)

        warnings = reconcile_session_state(analysis, manager, session)

        self.assertEqual(warnings, [])
        self.assertEqual(manager.series, [])
        for file in analysis.files:
            self.assertIsNone(file.series_id)
            self.assertIn(SPLIT_KEEP_SINGLE_MARKER, file.decision_reasons)

    def test_file_override_pulls_the_file_out_of_its_series_first(self):
        analysis = build_test_analysis(bracket_metadata("A"))
        manager = SeriesManager.from_analysis(analysis)
        excluded_path = "A_1.dng"
        session = _empty_session(file_overrides={excluded_path: "SKIP"})

        warnings = reconcile_session_state(analysis, manager, session)

        self.assertEqual(warnings, [])
        excluded_file = next(f for f in analysis.files if f.metadata.relative_path.as_posix() == excluded_path)
        self.assertIsNone(excluded_file.series_id)
        self.assertIs(excluded_file.destination_category, DestinationCategory.SKIP)

    def test_unknown_category_in_file_overrides_produces_a_warning_not_a_crash(self):
        analysis = build_test_analysis([make_metadata("only.dng", 0.0)])
        manager = SeriesManager.from_analysis(analysis)
        session = _empty_session(file_overrides={"only.dng": "NOT_A_REAL_CATEGORY"})

        warnings = reconcile_session_state(analysis, manager, session)

        self.assertTrue(warnings)

    def test_deselected_paths_are_reapplied(self):
        analysis = build_test_analysis([make_metadata("only.dng", 0.0)])
        manager = SeriesManager.from_analysis(analysis)
        session = _empty_session(deselected_paths=["only.dng"])

        reconcile_session_state(analysis, manager, session)

        self.assertFalse(analysis.files[0].selected)

    def test_dismissed_not_bracket_paths_are_reapplied(self):
        analysis = build_test_analysis([make_metadata("only.dng", 0.0)])
        manager = SeriesManager.from_analysis(analysis)
        session = _empty_session(dismissed_not_bracket_paths=["only.dng"])

        reconcile_session_state(analysis, manager, session)

        self.assertIn(DISMISS_NOT_BRACKET_MARKER, analysis.files[0].decision_reasons)

    def test_unrelated_new_automatic_series_is_left_untouched(self):
        # Nothing in the saved session claims these files at all; a session
        # restore must not suppress brand-new automatic detection.
        analysis = build_test_analysis(bracket_metadata("A"))
        manager = SeriesManager.from_analysis(analysis)
        session = _empty_session()  # no saved series, no claims

        warnings = reconcile_session_state(analysis, manager, session)

        self.assertEqual(warnings, [])
        self.assertEqual(len(manager.series), 1)
        self.assertTrue(manager.series[0].automatic)


class EndToEndRestoreTests(unittest.TestCase):
    """One real-folder round trip through the actual analyze_folder pipeline,
    proving the wiring connects — not just that the pure reconciliation logic
    is correct in isolation."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.source = Path(self._tmp.name) / "source"
        self.source.mkdir()
        self.session_path = Path(self._tmp.name) / "test.pbsession"
        (self.source / "a.jpg").write_bytes(JPEG_BYTES)
        (self.source / "b.jpg").write_bytes(JPEG_BYTES)

    def test_save_and_restore_round_trip_over_real_files(self):
        analysis = analyze_folder(self.source, AppSettings(), threading.Event())
        manager = SeriesManager.from_analysis(analysis)
        excluded = analysis.files[0]
        assign_category(excluded, DestinationCategory.SKIP)

        session = build_session(analysis, manager, AppSettings(), None)
        save_session(session, self.session_path)
        loaded = load_session(self.session_path)

        restored_analysis, _manager, _settings, warnings = restore_session(loaded, threading.Event())

        self.assertEqual(warnings, [])
        self.assertEqual(len(restored_analysis.files), 2)
        restored_excluded = next(f for f in restored_analysis.files if f.metadata.relative_path == excluded.metadata.relative_path)
        self.assertIs(restored_excluded.destination_category, DestinationCategory.SKIP)

    def test_unchanged_files_skip_exiftool_metadata_reader_entirely(self):
        analysis = analyze_folder(self.source, AppSettings(), threading.Event())
        session = build_session(analysis, SeriesManager.from_analysis(analysis), AppSettings(), None)

        reader = MetadataReader()
        spy = mock.MagicMock(wraps=reader.read_many)
        reader.read_many = spy

        restore_session(session, threading.Event(), metadata_reader=reader)

        spy.assert_not_called()

    def test_a_modified_file_is_read_fresh_while_the_other_stays_cached(self):
        analysis = analyze_folder(self.source, AppSettings(), threading.Event())
        session = build_session(analysis, SeriesManager.from_analysis(analysis), AppSettings(), None)

        time.sleep(0.05)
        (self.source / "a.jpg").write_bytes(JPEG_BYTES + b"\x00\x00")  # changes size and mtime

        reader = MetadataReader()
        spy = mock.MagicMock(wraps=reader.read_many)
        reader.read_many = spy

        restored_analysis, _manager, _settings, warnings = restore_session(session, threading.Event(), metadata_reader=reader)

        self.assertEqual(warnings, [])
        spy.assert_called_once()
        fresh_paths = spy.call_args.args[0]
        self.assertEqual(len(fresh_paths), 1)
        self.assertEqual(fresh_paths[0].name, "a.jpg")
        self.assertEqual(len(restored_analysis.files), 2)


if __name__ == "__main__":
    unittest.main()
