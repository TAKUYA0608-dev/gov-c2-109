# GOV-C2-109 — Government AI Use-Case Outcome Evidence Extractor

> **Category**: Cat 2 (domain workflow (a job to be done))
> **Industry**: Government

## Overview

Given a reporting period, the seven-field outcome schema (target process, purpose, evidence source, measured outcome, constraints, human oversight, evidence gap) and a set of programme reports as page text, the agent builds an outcome evidence register: it anchors each schema field to a document and page by label matching, extracts the value with its page span, records missing fields as missing data, checks that every value is cited and entailed by its source span, and flags uncited, low-confidence or unentailed fields — and the register as a whole — for an authorised officer's sign-off. Extraction and citation checking are deterministic label anchoring; there is no LLM. Resident and staff personal data and My Number values are minimised before extraction and re-redacted on output, prompt-like or schema-external text inside a report is contained and never followed, a request with no reports gets an out-of-scope answer, and the register is a candidate the agent never approves. The schema-label lexicon shipped here is a small seeded sample — replace it with your own reporting schema.

This is an agent template built with the **AGENTIC STAR** development platform and the
**AgentCore Framework**. It is intended to be taken as a starting point: fork it, adapt it to
your own data and policies, and run it inside your own AGENTIC STAR deployment.

## Requirements

**This template does not run standalone.** It requires:

| Requirement | Notes |
|---|---|
| **AGENTIC STAR platform** | The agent connects to the platform at start-up. Without it, start-up fails immediately (see *Behaviour without the platform* below). Deployment guides and API documentation: [AGENTIC STAR Developers](https://developers.fd.agenticstar.tm.softbank.jp/) |
| **AgentCore Framework** (`agenticstar-agentcore`) | Installed from PyPI as a dependency. |
| Python | 3.11 or later (`requires-python = ">=3.11"`) |

```bash
pip install -e .
```

### Behaviour without the platform

The framework is designed to run **only** on AGENTIC STAR. There is no fallback or degraded
mode. If the platform is unreachable or the SDK version does not match, the agent raises
`PlatformRequired` during graph compile / start-up preflight rather than starting in a partially
working state. This is intentional — a half-running agent is worse than one that refuses to start.

## Quick Start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python -m pytest tests/ -v
```

Tests run without a platform connection. Running the agent itself does not.

## Project Structure

```
src/          agent implementation (nodes, services, schemas)
tests/        unit, integration and boundary tests
config/       agent configuration
docs/         design and operational documentation
```

See `docs/02_design.md` for the design and `docs/03_test_spec.md` for the test specification.

## Customising

1. Adjust `config/` for your own environment and policies.
2. Replace the knowledge sources and sample data with your own.
3. Review the node implementations under `src/nodes/` for domain-specific logic.
4. Re-run the test suite.

## License

MIT — see [LICENSE](LICENSE).

## Status of this repository

This template is published **as is**, by its individual author, under the MIT license. It carries
**no warranty and no support commitment**, and no organisation stands behind its behaviour or
fitness for any purpose. Issues and pull requests may or may not receive a response; that is at
the sole discretion of the repository owner.
