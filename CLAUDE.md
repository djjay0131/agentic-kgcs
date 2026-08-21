# CLAUDE.md

Instructions for AI agents working in this repository.

<!-- BEGIN agentic-governance: repository layout -->
## Repository layout: two planes

The source of truth for this rule is
`~/code/agentic-governance/llm/governance/project-operating-system.md`
§Repository Areas, and the decision behind it is
`~/code/agentic-governance/llm/governance/adr/0001-llm-control-plane-docs-data-plane.md`.
Where this file and §Repository Areas disagree, §Repository Areas
wins. The paths below are the ones this repo declares in
`llm/governance/governance-delta.md` §Repository Layout.

The split is by **role**, not by authorship. Who wrote a document
decides nothing; what the document *does* decides everything.

**Control plane — the `llm/` tree.** Artifacts that govern, plan,
record, review, or operate this repository: governance policy and
the governance delta, role charters, workflows, prompts and skills,
design specs acting as design authority, implementation plans,
backlog and feature specs, the memory bank, ADRs, roadmaps,
execution patterns, and review and retrospective records.
Control-plane documents are sources of truth, and nothing downstream
is authoritative over them.

**Data plane — the artifacts tree (`docs`).** Project and
domain deliverables, external material, and derived views of
control-plane content: product and API documentation, project/domain
technical specifications and reference material, vendor and
third-party specifications, external proposals, research sources,
PDFs, diagrams, datasets, and published sites and generated views.
Nothing here governs how this repository is operated.

**No artifact that governs repository operation lives in the
artifacts tree, and any view placed there must name the `llm/`
document it projects.**

### Before you create any document: Q1, then Q2

**Q1 — Does this artifact control how the repository is governed,
planned, remembered, reviewed, or operated?** YES → control plane
(`llm/`). This is governance policy and the governance delta, role
charters, workflows, prompts and skills, design specs acting as
design authority, implementation plans, backlog and feature specs,
the memory bank, ADRs, roadmaps, execution patterns, and review and
retrospective records.

**Q2 — Otherwise: is it a project or domain deliverable, technical
reference, external source, specification, or generated project
documentation?** YES → the artifacts tree (`docs`). This
is product and API documentation, project/domain technical
specifications and reference material, vendor and third-party
specifications, external proposals, research sources, PDFs,
diagrams, datasets, and published sites and generated views. A
derived view of a control-plane document belongs here too, and must
name the `llm/` document it projects.

**Otherwise — do not invent a location.** Use the existing structure
the artifact plainly belongs to (`src/`, `tests/`, `.github/`), or
escalate to the Repository Steward.

If the answer to Q1 is unclear, treat the artifact as control plane.
Misfiling a source of truth as an artifact is the failure this rule
exists to prevent; the reverse is cheap to correct.

### Canonical destinations

| Content | Destination |
|---|---|
| Governance policy, the delta, patterns | `llm/governance` |
| Architecture Decision Records | `llm/governance/adr` |
| Design specs, the design-authority document | `llm/specs` |
| Implementation plans | `llm/plans` |
| Feature specs and backlog | `llm/features` |
| Memory bank | `llm/memory_bank` |
| Product/domain docs, external material, published views | `docs` |

ADRs are control plane: an ADR *is* the decision, not a report of
one. A published ADR index may be generated into the artifacts tree
as a derived view.

This repo declares only the paths it uses. An absent slot is not a
violation; an undeclared path is. If a document needs a home that is
not listed above, do not invent a path: use the existing structure it
plainly belongs to, or escalate to the Repository Steward.

### Tool-contract paths

Some paths are fixed by a tool or a platform rather than chosen by
this project. They sit outside both planes and are exempt. The class
is closed:

- `.github/` — workflows, issue templates, PR templates.
- `.claude-plugin/` — the marketplace manifest.
- The plugin payload root — whatever directory a marketplace
  `source` field points at.
- Root-convention files: `README.md`, `CHANGELOG.md`, `VERSION`,
  `CONTRIBUTING.md`, `LICENSE`, `CLAUDE.md`, `AGENTS.md`.

The exemption covers **location only**. A tool default is never
design authority. Where a tool writes control-plane content into the
artifacts tree, override the tool here and relocate the output.

### Output-location preferences (these override tool defaults)

These are the repository owner's standing **user preferences for
spec and plan location**. They take precedence over any skill's,
plugin's, or tool's default output path.

**Design specs and brainstorming output.** Write every design spec
to `llm/specs/YYYY-MM-DD-<topic>-design.md`. **Never** write to
`docs/superpowers/specs/`, and never create a `docs/superpowers/`
directory.

**Implementation plans.** Write every implementation plan to
`llm/plans/YYYY-MM-DD-<feature-name>.md`. **Never** write to
`docs/superpowers/plans/`, and never create a `docs/superpowers/`
directory.

This applies to the `obra/superpowers` skills — `brainstorming`,
`writing-plans`, and anything downstream of them — and to any other
tool with a hardcoded documentation path. If a skill instructs you
to write a spec or a plan somewhere else, this preference wins:
create the document under `llm/` instead, and do not mirror or copy
it into the artifacts tree.
<!-- END agentic-governance: repository layout -->
