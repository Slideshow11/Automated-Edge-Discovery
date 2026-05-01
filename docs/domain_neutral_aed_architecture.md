# Domain-Neutral AED Architecture

## 1. Purpose

AED (Automated Edge Discovery) is a **domain-neutral research governance operating system**. It enforces provenance, trial accounting, and falsification discipline across any asset class, strategy type, or research domain.

Pre-earnings options research is one valid research domain that AED supports. It is not the identity of the system.

## 2. AED Core Purpose

AED core provides:

- **Governance framework** — stop rules, status lifecycle, mutation constraints
- **Provenance tracking** — hypothesis creation through review and advancement
- **Trial accounting** — SearchSpaceManifest, TrialLedger, ModelAssessmentSpec
- **Experiment specification** — ExperimentSpec, OutcomeSpec, InstrumentUniverseSpec
- **Model assessment** — confirmatory evidence gates, leakage checks, PBO checks
- **Review packets** — Human-approved status changes with documented rationale

AED core does not assume any specific data source, instrument type, event taxonomy, or research workflow.

## 3. Domain-Neutral Core Concepts

The following schemas and concepts are **core AED** — they apply to any research domain:

| Concept | Description | Status |
|---------|-------------|--------|
| Hypothesis | A falsifiable claim with evidence stage, source lane, and theory timing | v1 implemented |
| DataManifest | Describes the data used to form a hypothesis, including cutoffs and feature timestamps | Deferred |
| SearchSpaceManifest | Pre-declared search boundaries, budget, and constraints | v1 implemented |
| TrialLedger | Append-only trial record with source lane and promotion rules | v1 implemented |
| ModelAssessmentSpec | Confirmatory assessment with required checks and evidence gates | v1 implemented |
| EdgeHypothesisRegistry | Machine-readable hypothesis record with lifecycle events and artifact links | v1 implemented |
| ExperimentSpec | Full experiment declaration: hypothesis, data scope, entry/exit rules, outcome windows | Deferred |
| OutcomeSpec | Declares primary metric, null result definition, and success criteria before testing | Deferred |
| InstrumentUniverseSpec | Declares the instrument universe and inclusion/exclusion rules for a trial | Deferred |
| ReviewPacket | Human-authored review record documenting status change rationale | Deferred |

Core AED schemas must not require domain-specific fields.

## 4. Domain Modules and Profiles

Domain modules are **optional research profiles** that build on top of AED core. Each profile specifies how generic AED concepts map to domain-specific fields, instruments, and event types.

| Profile | Domain | Examples of Domain-Specific Fields |
|---------|--------|-----------------------------------|
| PreEarningsProfile | Options — earnings events | `earnings_date`, `event_session` (BMO/AMC), `iv_crush`, `gap_exposure` |
| SeasonalityProfile | Equities — calendar effects | `seasonality_pattern`, `roll_dates`, `expiry_calendar` |
| MacroRegimeProfile | Macro — regime detection | `regime_indicator`, `volatility_state`, `correlation_regime` |
| CrossSectionalEquityProfile | Equities — cross-sectional | `factor_loadings`, `universe_cut`, `rebalance_frequency` |
| OptionsEventRiskProfile | Options — event risk | `delta_target`, `iv_term_structure`, `risk_reversal`, `put_call_ratio` |
| CryptoRegimeProfile | Crypto — regime and structure | `exchange_source`, `settlement_architecture`, `collateral_currency` |
| CommodityTermStructureProfile | Commodities — curve | `contract_series`, `roll_strategy`, `basis_spread` |
| LiteratureReplicationProfile | Academic — replication | `paper_doi`, `replication_universe`, `original_period` |

A new domain profile does not require modifying AED core. It requires a new schema that references AED core concepts.

## 5. Boundary Rule

**Core AED schemas must not require pre-earnings-specific fields.**

Fields that must not appear in core AED schemas:

- `earnings_date` — belongs in PreEarningsProfile
- `event_session` with values `BMO`/`AMC`/`INTRA` — belongs in PreEarningsProfile
- `entry_dpe` — belongs in PreEarningsProfile or OptionsEventRiskProfile
- `exit_dpe` — belongs in PreEarningsProfile or OptionsEventRiskProfile
- `expiry_rank` — belongs in PreEarningsProfile
- `delta_target` — belongs in OptionsEventRiskProfile
- `iv_crush` — belongs in PreEarningsProfile
- `gap_exposure` — belongs in PreEarningsProfile or OptionsEventRiskProfile
- `amc_bmo_indicator` — belongs in PreEarningsProfile

If a governance or provenance field applies to more than one domain, it belongs in AED core. If it is specific to one domain profile, it belongs in that profile's schema.

## 6. Generalized Abstractions

Instead of domain-specific fields, AED core uses generalized abstractions that any domain can instantiate:

| Core Abstraction | PreEarningsProfile instantiation | SeasonalityProfile instantiation |
|-----------------|--------------------------------|----------------------------------|
| `event_timestamp` | earnings announcement datetime | fiscal year start/end |
| `decision_timestamp` | pre-earnings decision time | signal generation time |
| `entry_rule` | pre-event entry (DTE, delta, spread) | calendar-triggered entry |
| `exit_rule` | post-event exit (DPE, IV collapse) | pattern completion exit |
| `feature_cutoff` | data cutoff before hypothesis | universe snapshot date |
| `outcome_window` | 0-DTE to +30-DPE | N-session window |
| `instrument_universe` | equity + options on specific ticker | index constituents |
| `risk_profile` | gap risk, IV crush, delta exposure | drawdown, volatility, correlation |
| `data_scope` | options observations, event cohort | bar data, factor data |
| `trial_family` | pre-earnings IV ramp family | calendar anomaly family |

## 7. Future Spec Roadmap

Schemas and validators planned for AED core:

| Spec | Description | Priority |
|------|-------------|----------|
| ExperimentSpec v1 | Full experiment declaration with entry/exit rules, data scope, outcome windows | High |
| OutcomeSpec v1 | Primary metric, null result, success criteria declared before testing | High |
| InstrumentUniverseSpec v1 | Universe declaration with inclusion/exclusion rules | High |
| EventStudySpec v1 | Event study design: timing, windows, normal-performance model, inference | Medium |
| OptionsEventRiskSpec v1 | Options event risk profile: delta targets, risk reversals, term structure | Medium |
| PreEarningsProfile v1 | Pre-earnings domain module: event_session, gap_exposure, IV crush | Medium |

ExperimentSpec, OutcomeSpec, and InstrumentUniverseSpec are prerequisites for any automated trial advancement. They must be domain-neutral by design.

## 8. Agent and Tooling Layer

AED may eventually leverage Hermes and OpenClaw for semi-autonomous research assistance. These agents operate as suggestion engines and validation tools — they do not govern AED.

Permitted agent activities:
- Suggest hypotheses based on exploratory anomalies
- Draft ExperimentSpec or OutcomeSpec from a hypothesis
- Propose falsification tests and required checks
- Summarize trial failures and identify leakage risks
- Prepare draft ReviewPacket entries
- Validate specs and fixtures against schemas
- Flag ex-post hypothesis formation

Agent constraints:
- Agents **can suggest**. Validators **can block**. **Humans approve** status changes.
- Agents must not bypass governance validation
- Agents must not mutate the EdgeHypothesisRegistry without manual review
- Agents must not approve their own proposed status changes
- Agent activity is logged; all mutations are audit-trailed

AED's stop rules apply to all agents operating within the system.

## 9. Stop Rules

The following are permanently locked in AED core unless explicitly unlocked by a future governance amendment:

- **No autonomous search** — no automated exploration of the strategy space without pre-declared SearchSpaceManifest and human review
- **No Bayesian optimization** — no automated hyperparameter optimization
- **No genetic programming** — no evolutionary algorithm-driven strategy generation
- **No automated promotion** — no automatic advancement of hypotheses to next stage
- **No automated registry mutation** — no automatic changes to EdgeHypothesisRegistry records
- **No live trading** — no real-money execution under any circumstances
- **No production execution** — no simulated production with real market impact
- **No GCRU integration** — no connection to live market data feeds, execution systems, or risk management without a separate governance design

These rules apply regardless of whether they are invoked by humans, scripts, or AI agents.

## 10. Manual Review Rule

> **Agents can suggest. Validators can block. Humans approve.**

Every status change in the EdgeHypothesisRegistry requires:
1. A ReviewPacket with documented rationale
2. A human signature of approval
3. An append-only lifecycle event

Validators enforce the structural requirements of a ReviewPacket. They do not approve status changes. Approval is always manual.

This rule is not a feature. It is a governance constraint that cannot be overridden by automation.
