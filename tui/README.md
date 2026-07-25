# TUI MVP

`helix_step.py` is a minimal command-line debugger for the current Helix runtime.

It does not talk to the VM in-process. Instead, it matches the runtime that already exists in `build/helix`:

- load the target YAML file in Python
- wrap that VM under a temporary parent VM
- mirror Helix root-level `include` expansion before wrapping
- set the temporary parent `main` to `[step, __debug_target__]`
- run the compiled `helix` binary on the wrapper
- parse the emitted YAML back in Python
- extract the stepped child VM
- write that stepped VM back to the original file

That means one invocation advances the target file by exactly one Helix child-VM step.
In the current runtime, one `step` call can still move through a larger chunk of work than one sequence element because `advance_vm()` may arm and then immediately resume a vector-backed frame in the same call.

## Usage

```bash
python3 tui/helix_step.py path/to/program.yaml
```

Optional binary override:

```bash
python3 tui/helix_step.py path/to/program.yaml --binary /path/to/helix
```

## Notes

- The target YAML must be a top-level mapping.
- The wrapper file is created in the same directory as the target file so Helix `include` paths keep working relative to the original source location.
- This is an MVP for the current runtime shape, where `step` operates on a named child VM.
