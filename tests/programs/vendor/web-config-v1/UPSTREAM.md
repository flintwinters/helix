# Web configuration v1 fixture

Original Helix test fixture, maintained in this repository under its license.
Entry file: `main.yaml`. Source and revision pin: the consuming repository commit.
No external upstream or local patches. The committed source bytes are the pin.

The transitive dependency is `../service-policy-v1/main.yaml`; preserve both
versioned directories when relocating this configuration. `build` exposes typed
input and output and returns a composition graph, not a resource provider.
