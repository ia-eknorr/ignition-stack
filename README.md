# ignition-stack

CLI that generates ready-to-run Docker Compose stacks for Ignition 8.3 SCADA demos and SE engagements. Picks an architecture profile, asks a few questions, writes a self-contained project with a hand-readable compose file, env, file-config seed resources, and a `POST-SETUP.md` listing only what could not be pre-seeded.

Status: under construction. Phase 1 of build is the Ignition 8.3 seedability investigation: see [`docs/ignition-seeding-matrix.md`](docs/ignition-seeding-matrix.md) for which connection types can be provisioned from the filesystem/env vs require manual UI entry on a live 8.3.6 gateway.
