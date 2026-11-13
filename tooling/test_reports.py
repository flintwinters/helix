"""Replayable evidence for supervised runtime operations and test contracts."""


def failure(root, case, stage, reason, command, result, *, source=None, expected=None, source_cwd=None,
            replay_args=None, observation=None):
    """Keep complete evidence on disk and a focused, navigable report in stderr."""
    import difflib
    from itertools import islice
    from pathlib import Path
    import shlex
    import tempfile

    if replay_args is not None and (source is not None or source_cwd is not None):
        raise ValueError("explicit replay arguments cannot be combined with source replay")
    relative = case.relative_to(root)
    parent = root / "runtime" / "test-failures" / relative.parent / case.stem / stage
    parent.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="run-", dir=parent))
    (directory / "actual.yaml").write_text(result.stdout)
    (directory / "stderr.txt").write_text(result.stderr)
    if observation is not None:
        import json

        (directory / "observation.json").write_text(json.dumps(observation, ensure_ascii=False, indent=2) + "\n")
    replay = relative
    if source is not None:
        replay = (directory / "input.yaml").relative_to(root)
        (root / replay).write_text(source)
    reproduce = f"./manage.py {command} {shlex.quote(str(replay))}"
    if replay_args is not None:
        reproduce = shlex.join(["./manage.py", *replay_args])
    if source is not None and source_cwd is not None:
        from os.path import relpath
        from manage import BIN

        # manage.py deliberately starts runtime commands at the checkout root.
        # Rebuild its stable binary, then replay stdin from the original import base.
        # Pin Cargo's configured target so a later shell environment cannot build
        # elsewhere while this recipe invokes an older executable.
        build = shlex.join(["env", f"CARGO_TARGET_DIR={BIN.parent.parent}", "./manage.py", "build"])
        invocation = shlex.join([relpath(BIN, source_cwd), *shlex.split(command), "-"])
        reproduce = (f"{build} && (cd {shlex.quote(relpath(source_cwd, root))} && "
                     f"{invocation} < {shlex.quote(relpath(root / replay, source_cwd))})")
    lines = [f"[{stage}] {relative}: {reason}",
             f"exit: {result.returncode}",
             f"reproduce: {reproduce}",
             f"evidence: {directory.relative_to(root)}"]
    if expected is not None:
        (directory / "expected.yaml").write_text(expected)
        diff_limit = 120
        diff = list(islice(difflib.unified_diff(expected.splitlines(keepends=True),
                                              result.stdout.splitlines(keepends=True),
                                              fromfile="expected", tofile="actual"), diff_limit + 1))
        lines.append("".join(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n"
                             for line in diff[:diff_limit]).rstrip())
        if len(diff) > diff_limit:
            lines.append("diff truncated; complete values are in evidence files")
    if result.stderr:
        lines.append("stderr:\n" + result.stderr[:2000])
    return "\n".join(lines)
