"""Replayable diagnostics for golden-state and robustness contracts."""
import difflib
import shlex


def failure(root, case, stage, reason, command, result, *, source=None, expected=None):
    """Keep complete evidence on disk and a focused, navigable report in stderr."""
    relative = case.relative_to(root)
    directory = root / "runtime" / "test-failures" / relative.parent / case.stem / stage
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "actual.yaml").write_text(result.stdout)
    (directory / "stderr.txt").write_text(result.stderr)
    replay = relative
    if source is not None:
        replay = (directory / "input.yaml").relative_to(root)
        (root / replay).write_text(source)
    lines = [f"[{stage}] {relative}: {reason}",
             f"exit: {result.returncode}",
             f"reproduce: ./manage.py {command} {shlex.quote(str(replay))}",
             f"evidence: {directory.relative_to(root)}"]
    if expected is not None:
        (directory / "expected.yaml").write_text(expected)
        diff = list(difflib.unified_diff(expected.splitlines(keepends=True),
                                       result.stdout.splitlines(keepends=True),
                                       fromfile="expected", tofile="actual"))
        lines.append("".join(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n"
                             for line in diff[:120]).rstrip())
        if len(diff) > 120:
            lines.append("diff truncated; complete values are in evidence files")
    if result.stderr:
        lines.append("stderr:\n" + result.stderr[:2000])
    return "\n".join(lines)
