"""Handled baseline-write failures must not leave owned partial output behind."""

import errno

import pytest

from recovery_assurance import core
from recovery_assurance.cli import main


@pytest.fixture
def baseline(tmp_path):
    source = tmp_path / "snapshot"
    source.mkdir()
    (source / "record.txt").write_bytes(b"synthetic")
    return core.capture(source)


def assert_retry(manifest, target):
    assert not target.exists()
    core.save_manifest(manifest, target)
    assert core.load_manifest(target) == manifest


@pytest.mark.parametrize(
    ("value", "error"),
    [(float("nan"), ValueError), (object(), TypeError)],
    ids=["nonfinite-annotation", "unserializable-annotation"],
)
def test_serialization_failure_allows_a_corrected_baseline_retry(baseline, tmp_path, value, error):
    target = tmp_path / "baseline.json"
    baseline["files"]["record.txt"]["note"] = value
    with pytest.raises(error):
        core.save_manifest(baseline, target)
    del baseline["files"]["record.txt"]["note"]
    assert_retry(baseline, target)


@pytest.mark.parametrize(
    "failure",
    [OSError(errno.ENOSPC, "synthetic disk-full fault"), KeyboardInterrupt()],
    ids=["write-error", "interruption"],
)
def test_partial_write_failure_is_reraised_and_owned_output_is_removed(
    baseline, tmp_path, monkeypatch, failure
):
    target = tmp_path / "baseline.json"

    def fail_dump(_manifest, stream, **_options):
        stream.write("{")
        stream.flush()
        raise failure

    with monkeypatch.context() as patch:
        patch.setattr(core.json, "dump", fail_dump)
        with pytest.raises(type(failure)) as caught:
            core.save_manifest(baseline, target)
    assert caught.value is failure
    assert_retry(baseline, target)


def test_close_failure_also_removes_owned_output(baseline, tmp_path, monkeypatch):
    target = tmp_path / "baseline.json"
    failure = OSError(errno.ENOSPC, "synthetic close fault")
    real_open = core.Path.open

    class FailOnClose:
        def __init__(self, stream):
            self.stream = stream

        def __getattr__(self, name):
            return getattr(self.stream, name)

        def __enter__(self):
            return self

        def __exit__(self, *_exception):
            self.stream.close()
            raise failure

    def open_with_close_fault(path, *arguments, **options):
        return FailOnClose(real_open(path, *arguments, **options))

    with monkeypatch.context() as patch:
        patch.setattr(core.Path, "open", open_with_close_fault)
        with pytest.raises(OSError) as caught:
            core.save_manifest(baseline, target)
    assert caught.value is failure
    assert_retry(baseline, target)


def test_exclusive_open_failure_preserves_existing_output(baseline, tmp_path):
    target = tmp_path / "baseline.json"
    target.write_bytes(b"preserve existing output")
    with pytest.raises(FileExistsError):
        core.save_manifest(baseline, target)
    assert target.read_bytes() == b"preserve existing output"


def test_failure_does_not_remove_a_replacement_file(baseline, tmp_path, monkeypatch):
    target = tmp_path / "baseline.json"
    replacement = tmp_path / "replacement.json"
    replacement.write_bytes(b"preserve replacement")
    failure = OSError(errno.ENOSPC, "synthetic write fault")

    def replace_then_fail(_manifest, stream, **_options):
        stream.write("{")
        stream.close()
        replacement.replace(target)
        raise failure

    with monkeypatch.context() as patch:
        patch.setattr(core.json, "dump", replace_then_fail)
        with pytest.raises(OSError) as caught:
            core.save_manifest(baseline, target)
    assert caught.value is failure
    assert target.read_bytes() == b"preserve replacement"


def test_capture_cli_returns_error_without_leaving_partial_baseline(
    baseline, tmp_path, monkeypatch, capsys
):
    target = tmp_path / "baseline.json"

    def fail_dump(_manifest, stream, **_options):
        stream.write("{")
        stream.flush()
        raise OSError(errno.ENOSPC, "synthetic disk-full fault")

    with monkeypatch.context() as patch:
        patch.setattr(core.json, "dump", fail_dump)
        code = main(["capture", str(tmp_path / "snapshot"), "--manifest", str(target)])
    captured = capsys.readouterr()
    assert code == 2
    assert captured.out == ""
    assert captured.err.startswith("Error:")
    assert "Traceback" not in captured.err
    assert_retry(baseline, target)
