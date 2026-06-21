# Helix

Helix is an experimental C++ runtime for structured, versioned program state.

## Current Features Overview

IMPLEMENTED NOW:
- Running programs can be stored, inspected, and edited as ordinary human readable YAML.
- Helix Micro Virtual Machines can run Helix code repeatably and disposably.
- MicroVM running *state* and source code is fully human readable YAML.
- Version a running program/VM - branch, checkout, and merge program state.
- Breakpoints can trigger commits.
- Programmatically start, step, and debug nested child VMs.

## In progress

__HCC: Compile C99 into readable Helix YAML__
- Use Helix's debuggability in C.
- Rigorously test Microcontroller code before flashing
- Use MicroVMs to emulate distributed systems field tests

## Next

- Run on RP2040
- Run on FPGA RV32 softcore (icesugar pro, vexriscv, litex)
- Some kind of Lean4 library to operate on Helix YAML source as a hyperlinter

## Demo (turn up volume)

[![Helix demo video](https://img.youtube.com/vi/WamNcUSqt1w/hqdefault.jpg)](https://youtu.be/WamNcUSqt1w)