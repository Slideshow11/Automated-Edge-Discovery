# InstrumentUniverseSpec v1 Design

**Design date:** 2026-05-01
**PR:** #104
**Governing documents:**
- [`docs/domain_neutral_aed_architecture.md`](./domain_neutral_aed_architecture.md) — AED core domain-neutral principles, boundary rule, generalized abstractions, agent tooling, and stop rules
- [`docs/domain_neutral_modularity_audit.md`](./domain_neutral_modularity_audit.md) — modularity audit confirming governance layer is domain-neutral; engine/ is expected pre-earnings coupling
- [`docs/literature_requirements_for_aed.md`](./literature_requirements_for_aed.md) — §10b: InstrumentUniverseSpec priority fields including `universe_id`, `instrument_list`, `eligibility_criteria`, `data_source_refs`, `liquidity_filter`, `market_impact_model`

---

## 1. Purpose

InstrumentUniverseSpec v1 defines the instrument eligibility universe for an experiment. It answers:
- What instruments may be considered?
- What instruments are excluded?
- What data manifests define the source universe?
- What liquidity, survivorship, tradability, and availability filters apply?
- When is universe membership determined?

InstrumentUniverseSpec is a **design-time declaration** of instrument eligibility constraints. It is committed to the repository before any trial data is generated. It does not contain runtime instrument selections, rankings, or signals.

InstrumentUniverseSpec is **domain-neutral**. It declares eligibility rules that apply across any asset class or research domain — equities, ETFs, options, futures, FX, crypto, commodities, rates, indices, or custom instruments.

---

## 2. Relationship to AED Artifacts

### 2a. ExperimentSpec

ExperimentSpec declares the experiment plan and references an InstrumentUniverseSpec as its instrument universe scope.

```
ExperimentSpec.instrument_universe_id → InstrumentUniverseSpec.instrument_universe_id
```

ExperimentSpec does not compute instrument eligibility — it references the universe declaration that ExperimentSpec will use.

### 2b. OutcomeSpec

OutcomeSpec defines what metric is measured over what window. InstrumentUniverseSpec defines which instruments provide the data for that measurement. They are independent sibling declarations:

```
ExperimentSpec.instrument_universe_id → InstrumentUniverseSpec.instrument_universe_id
ExperimentSpec.outcome_spec_id → OutcomeSpec.outcome_spec_id
```

OutcomeSpec does not reference InstrumentUniverseSpec directly; their relationship is mediated by ExperimentSpec.

### 2c. SearchSpaceManifest

SearchSpaceManifest declares trial generation budget and parameter constraints. InstrumentUniverseSpec declares instrument eligibility. Both constrain ExperimentSpec independently:

```
SearchSpaceManifest.search_space_id → ExperimentSpec.search_space_id
InstrumentUniverseSpec.instrument_universe_id → ExperimentSpec.instrument_universe_id
```

Neither SearchSpaceManifest nor InstrumentUniverseSpec depends on the other.

### 2d. TrialLedger

TrialLedger records individual trial results. The instrument universe membership determines which instruments appear in trial records, but TrialLedger does not own universe membership declarations:

```
TrialLedger.instrument_universe_id → InstrumentUniverseSpec.instrument_universe_id
```

TrialLedger records trial execution. InstrumentUniverseSpec declares the eligibility scope within which those trials were generated.

### 2e. DataManifest

DataManifest declares the data scope available for the experiment. InstrumentUniverseSpec references DataManifests to define the source universe for instrument selection:

```
InstrumentUniverseSpec.data_manifest_refs → DataManifest.data_manifest_id
```

InstrumentUniverseSpec instrument eligibility rules are evaluated against data described by DataManifest. DataManifest does not own instrument eligibility logic.

### 2f. ModelAssessmentSpec

ModelAssessmentSpec computes statistical assessment outputs from trial results. InstrumentUniverseSpec provides the instrument eligibility context, but assessment outputs are owned by ModelAssessmentSpec:

```
ModelAssessmentSpec.instrument_universe_id → InstrumentUniverseSpec.instrument_universe_id (informational)
```

InstrumentUniverseSpec does not own PBO estimates, DSR estimates, Sharpe haircuts, false discovery rates, strategy complexity scores, or any computed assessment output. These belong to ModelAssessmentSpec.

### 2g. EdgeHypothesisRegistry

EdgeHypothesisRegistry holds the hypothesis being tested. InstrumentUniverseSpec constrains which instruments the hypothesis applies to, but does not advance hypothesis status:

```
InstrumentUniverseSpec.hypothesis_id → EdgeHypothesisRegistry.hypothesis_id (informational)
```

InstrumentUniverseSpec may record which hypothesis the universe was constructed for, but it does not change hypothesis status.

### 2h. Runner Outputs

Runner outputs (equity curves, performance series, null-model comparisons) are runtime artifacts computed against the instruments declared by InstrumentUniverseSpec. Runner outputs do not own universe membership rules:

```
RunnerOutput.instrument_universe_id → InstrumentUniverseSpec.instrument_universe_id
```

Runner outputs reference InstrumentUniverseSpec for provenance; InstrumentUniverseSpec does not reference runner outputs.

### 2i. ReviewPacket

ReviewPacket renders a human judgment on hypothesis advancement. InstrumentUniverseSpec provides the instrument universe context for that judgment, but does not own the decision:

```
ReviewPacket.instrument_universe_id → InstrumentUniverseSpec.instrument_universe_id (informational)
```

### 2j. Domain Profiles

Domain profiles (PreEarningsProfile, SeasonalityProfile, MacroRegimeProfile, etc.) provide domain-specific URI resolutions for abstract references. InstrumentUniverseSpec supports `domain_profile_refs` to allow domain-specific instrument filters without hard-coding them into the core schema:

```
InstrumentUniverseSpec.domain_profile_refs → DomainProfile.domain_profile_id
```

Domain profiles do not modify InstrumentUniverseSpec. They provide domain-specific instrument eligibility enrichments that sit above the core InstrumentUniverseSpec boundary.

---

## 3. Proposed Required Fields

These fields are required for InstrumentUniverseSpec v1. They define the core instrument eligibility declaration.

| Field | Type | Description |
|-------|------|-------------|
| `instrument_universe_id` | string | Canonical ID, format IUS-YYYY-NNNN |
| `universe_version` | integer | Semantic version integer, ≥ 1 |
| `universe_family` | string | Universe category, e.g. "us_equity", "crypto_spot", "options_event_risk" |
| `asset_classes` | array[enum] | Asset classes included in this universe. See §5a. |
| `data_manifest_refs` | array[string] | References to DataManifest entries that define the source data for this universe |
| `universe_construction_policy` | enum | How the universe is constructed. See §5b. |
| `membership_timing_policy` | enum | When universe membership is determined. See §5c. |
| `inclusion_rules` | array[object] | Rules that must be satisfied for inclusion. See §6. |
| `exclusion_rules` | array[object] | Rules that disqualify instruments regardless of inclusion_rules. See §6. |
| `liquidity_policy` | object | Minimum liquidity requirements. See §7. |
| `survivorship_policy` | enum | Survivorship bias handling. See §5d. |
| `tradability_policy` | enum | Tradability requirements at different stages. See §5e. |
| `corporate_action_policy` | enum | How corporate actions are handled. See §5f. |
| `created_at` | string | ISO 8601 timestamp of universe creation |
| `reviewer` | object | Reviewer identity with `reviewer_id` (string) and optional `reviewer_name` (string) |

---

## 4. Proposed Optional Fields and Hooks

These fields are optional. They provide flexibility for domain-specific instruments, reference tracking, and future extensions without modifying the core required set.

| Field | Type | Description |
|-------|------|-------------|
| `instrument_id_namespace` | string | Canonical namespace for instrument IDs in this universe (e.g., "CUSIP", "ISIN", "OCC", "BUID", "CCY") |
| `symbol_mapping_policy` | object | Maps external symbols to internal instrument IDs. Contains `mapping_source` (string), `mapping_version` (string), `mapping_timestamp` (string) |
| `exchange_filter` | array[string] | List of permitted exchange codes or venue identifiers |
| `country_filter` | array[string] | List of permitted country codes (ISO 3166-1 alpha-2) |
| `currency_filter` | array[string] | List of permitted currencies (ISO 4217) |
| `sector_industry_filter` | object | Sector and industry inclusion constraints. Contains `sectors` (array[string]), `industries` (array[string]), `exclude_sectors` (array[string]) |
| `market_cap_filter` | object | Market capitalization constraints. Contains `min_market_cap` (number), `max_market_cap` (number), `market_cap_currency` (string) |
| `price_filter` | object | Price level constraints. Contains `min_price` (number), `max_price` (number), `price_currency` (string) |
| `volume_filter` | object | Trading volume constraints. Contains `min_average_volume` (number), `volume_window_days` (integer), `volume_currency` (string) |
| `open_interest_filter` | object | Open interest constraints for derivatives. Contains `min_open_interest` (number), `open_interest_currency` (string) |
| `option_contract_filter` | object | Options-specific constraints. Contains `allowed_option_types` (array[enum: call,put,custom]), `strike_filter` (object with `min_strike`, `max_strike`, `strike_currency`), `expiry_filter` (object with `min_dte`, `max_dte`), `option_style` (enum: american,european,custom) |
| `futures_contract_filter` | object | Futures-specific constraints. Contains `allowed_contracts` (array[string]), `contract_size_filter` (object), `delivery_filter` (object) |
| `crypto_exchange_filter` | array[string] | Permitted crypto exchanges or venues |
| `data_availability_policy` | object | Data completeness requirements. See §8. |
| `universe_snapshot_refs` | array[string] | References to point-in-time universe snapshot artifacts |
| `runner_output_refs` | array[string] | References to runner outputs that used this universe |
| `domain_profile_refs` | array[string] | References to domain profiles providing domain-specific instrument enrichments |
| `extension_hooks` | object | Optional extension object. Contains `model_assessment_extension_refs` (array[string]), `runner_output_extension_refs` (array[string]), `review_packet_extension_refs` (array[string]), `domain_profile_refs` (array[string]). All arrays optional. |
| `notes` | string | Human-readable notes about universe construction rationale |

---

## 5. Proposed Enums

### 5a. asset_classes

Instruments may span multiple asset classes. At least one is required.

| Value | Description |
|-------|-------------|
| `equity` | Common stock, preferred stock |
| `etf` | Exchange-traded fund |
| `option` | Equity or index options |
| `future` | Futures contract |
| `fx` | Foreign exchange |
| `crypto` | Digital asset spot or derivatives |
| `commodity` | Physical commodity or commodity derivative |
| `rate` | Fixed income, interest rate derivative |
| `index` | Index (not directly tradeable) |
| `custom` | Non-standard instrument requiring domain-specific handling |

### 5b. universe_construction_policy

| Value | Description |
|-------|-------------|
| `static_list` | Universe is a fixed list of instrument IDs at a point in time |
| `point_in_time_membership` | Universe membership determined retrospectively using historical constituent data |
| `rolling_membership` | Universe membership rolls forward over time (e.g., rolling N-day liquid universe) |
| `rule_based_filter` | Universe constructed by applying inclusion/exclusion rules to a parent universe |
| `external_index_membership` | Universe defined by membership in an external index or benchmark |
| `custom` | Domain-specific construction requiring custom logic |

### 5c. membership_timing_policy

| Value | Description |
|-------|-------------|
| `decision_time` | Membership evaluated at the experiment decision timestamp |
| `entry_time` | Membership evaluated at instrument entry into portfolio/strategy |
| `rebalance_time` | Membership evaluated at each rebalance date |
| `event_time` | Membership evaluated at event timestamp (for event-study universes) |
| `fixed_snapshot` | Membership fixed to a specific date/time snapshot |
| `custom` | Domain-specific timing |

### 5d. survivorship_policy

| Value | Description |
|-------|-------------|
| `point_in_time` | Only instruments that existed at each point in time are included (no look-ahead bias) |
| `current_constituents_only` | Only currently existing instruments included |
| `survivor_bias_allowed_for_smoke_test` | Survivor bias permitted only for preliminary smoke-test runs; full backtests require `point_in_time` |
| `custom` | Domain-specific survivorship handling |

### 5e. tradability_policy

| Value | Description |
|-------|-------------|
| `tradable_at_decision_time` | Instruments must be tradable at the decision timestamp |
| `tradable_at_entry_time` | Instruments must be tradable at portfolio entry timestamp |
| `tradable_through_window` | Instruments must remain tradable throughout the outcome measurement window |
| `custom` | Domain-specific tradability requirement |

### 5f. corporate_action_policy

| Value | Description |
|-------|-------------|
| `adjusted` | All corporate actions fully adjusted (standard for most equity backtests) |
| `raw` | Unadjusted prices; corporate actions appear as price jumps |
| `split_adjusted` | Prices adjusted for splits only |
| `dividend_adjusted` | Prices adjusted for dividends only |
| `total_return_adjusted` | Prices adjusted for total returns (splits + dividends) |
| `custom` | Domain-specific adjustment policy |

---

## 6. Inclusion and Exclusion Rule Structure

Each rule in `inclusion_rules` and `exclusion_rules` is an object with the following fields:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `rule_id` | string | Yes | Unique rule identifier within the universe, format IRL-YYYY-NNNN |
| `field` | string | Yes | Instrument field or attribute being evaluated (e.g., "country_code", "exchange", "market_cap", "liquidity_score") |
| `operator` | enum | Yes | Comparison operator: `eq`, `ne`, `gt`, `gte`, `lt`, `lte`, `in`, `not_in`, `contains`, `regex` |
| `value` | varies | Yes | Comparison value; type depends on `operator` and `field` |
| `timing` | string | No | When the rule applies: `decision_time`, `entry_time`, `rebalance_time`. Defaults to `decision_time` if omitted. |
| `data_manifest_ref` | string | No | Reference to the DataManifest that provides the data for evaluating this rule |
| `reason` | string | No | Human-readable rationale for this rule |

Example exclusion rule (exclude OTC instruments):

```json
{
  "rule_id": "IRL-2026-0002",
  "field": "exchange",
  "operator": "in",
  "value": ["NYSE", "NASDAQ", "CBOE", "BATS"],
  "timing": "decision_time",
  "reason": "Limit to listed exchanges; OTC Pink Sheets excluded"
}
```

Example inclusion rule (minimum market cap):

```json
{
  "rule_id": "IRL-2026-0001",
  "field": "market_cap_usd",
  "operator": "gte",
  "value": 1000000000,
  "timing": "decision_time",
  "reason": "Exclude micro-cap instruments with insufficient liquidity"
}
```

---

## 7. Liquidity Policy

The `liquidity_policy` object defines minimum liquidity requirements for universe membership. All numeric fields are required unless marked optional.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `min_price` | number | No | Minimum instrument price in `price_currency`. Instruments below this are excluded. |
| `max_price` | number | No | Maximum instrument price. Instruments above this are excluded. |
| `min_dollar_volume` | number | No | Minimum average daily dollar volume over `liquidity_lookback_days` |
| `min_average_volume` | number | No | Minimum average daily share/copy volume over `liquidity_lookback_days` |
| `min_open_interest` | number | No | Minimum open interest (for derivatives: options, futures) |
| `max_bid_ask_spread` | number | No | Maximum bid-ask spread as a fraction (e.g., 0.01 = 1% spread) |
| `min_days_listed` | integer | No | Minimum number of trading days instrument must have been listed before inclusion |
| `liquidity_lookback_days` | integer | No | Number of days over which volume/spread metrics are computed. Default: 20. |
| `liquidity_measure_timing` | enum | No | When liquidity is measured: `decision_time`, `entry_time`, `fixed_snapshot`. Default: `decision_time`. |
| `liquidity_not_applicable_reason` | string | No | Free-text reason why liquidity policy does not apply (e.g., for illiquid alternative assets). Required if all other liquidity fields are absent. |

**Base bound rationale for `max_bid_ask_spread`:** The base constraint is [0, 1] as a fraction. A spread of 1.0 would mean the bid-ask spread equals the instrument price, which is trivially true for any illiquid instrument. Tighter caps (e.g., max 0.05 for liquid equity universes) belong in domain profile validators, not the core schema. The core schema enforces only the [0, 1] bound.

---

## 8. Data Availability Policy

The `data_availability_policy` object defines data completeness requirements for universe membership. All fields are required unless marked optional.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `required_history_days` | integer | No | Minimum number of trading days of history required before instrument can enter universe |
| `required_feature_coverage` | number | No | Fraction of feature data points that must be non-null [0, 1] |
| `required_outcome_coverage` | number | No | Fraction of outcome data points that must be non-null [0, 1] |
| `missing_data_policy` | enum | No | How missing data is handled: `exclude`, `impute`, `allow_partial`. Default: `exclude`. |
| `stale_data_policy` | enum | No | How stale data is handled: `exclude`, `forward_fill`, `allow_stale`. Default: `exclude`. |
| `point_in_time_required` | boolean | No | Whether point-in-time data is required (vs. back-adjusted). Default: `true`. |
| `feature_cutoff_alignment_required` | boolean | No | Whether feature timestamps must align with outcome window cutoffs. Default: `true`. |

---

## 9. Boundary: What InstrumentUniverseSpec Does Not Own

InstrumentUniverseSpec declares instrument eligibility constraints. It does **not** own any of the following:

### 9a. Signals, Rankings, and Scores

InstrumentUniverseSpec does not own:
- Signals of any kind (entry signals, exit signals, factor scores, alpha signals)
- Instrument rankings (top-N by score, percentile ranks, factor loadings)
- Composite scores or multi-factor model outputs

**Rationale:** Signals and rankings are runtime outputs produced by runners executing ExperimentSpecs. InstrumentUniverseSpec defines the universe within which those runners operate.

### 9b. Trial Accounting and Selection

InstrumentUniverseSpec does not own:
- `selected_variant_id` — which trial variant was selected
- `n_tried` — number of trial variants attempted
- `trial_family_id` — which trial family a trial belongs to

**Rationale:** Trial accounting belongs to TrialLedger and ModelAssessmentSpec. InstrumentUniverseSpec defines which instruments were eligible; which instruments were selected and how many trials were run is a separate concern.

### 9c. PnL and Realized Returns

InstrumentUniverseSpec does not own:
- PnL of any kind (realized, unrealized, gross, net)
- Realized returns
- Return attribution

**Rationale:** Returns are computed by runners and recorded in TrialLedger and ModelAssessmentSpec. InstrumentUniverseSpec defines what could be traded, not what was traded or what it returned.

### 9d. Model Assessment Outputs

InstrumentUniverseSpec does not own:
- `pbo_estimate` — probability of backtest overfitting
- `dsr_estimate` — degree of statistical significance
- `strategy_complexity_score` — complexity-adjusted overfit estimate
- Any overfit correction or bootstrap estimate

**Rationale:** These belong to ModelAssessmentSpec. InstrumentUniverseSpec provides instrument eligibility context; ModelAssessmentSpec computes statistical assessment outputs.

### 9e. ReviewPacket Decisions

InstrumentUniverseSpec does not own:
- ReviewPacket or any hypothesis advancement decision
- Approval or rejection rationale
- Status changes in EdgeHypothesisRegistry

**Rationale:** These belong to ReviewPacket and EdgeHypothesisRegistry. InstrumentUniverseSpec is a design-time declaration that may inform a ReviewPacket, but it does not make or own advancement decisions.

---

## 10. Conceptual Examples

These examples illustrate how InstrumentUniverseSpec universe declarations work across domains. All examples use domain-neutral language; domain-specific details sit in optional filters or `domain_profile_refs`.

### 10a. U.S. Liquid Equity Universe

```
instrument_universe_id: IUS-2026-0001
universe_family: us_liquid_equity
asset_classes: [equity]
universe_construction_policy: rule_based_filter
membership_timing_policy: fixed_snapshot
survivorship_policy: point_in_time
tradability_policy: tradable_through_window
corporate_action_policy: total_return_adjusted
inclusion_rules:
  - rule_id: IRL-2026-0001
    field: exchange
    operator: in
    value: [NYSE, NASDAQ]
    reason: Listed U.S. equity exchanges
  - rule_id: IRL-2026-0002
    field: market_cap_usd
    operator: gte
    value: 1000000000
    reason: NYSE/NASDAQ market cap >= $1B
liquidity_policy:
  min_dollar_volume: 5000000
  liquidity_lookback_days: 20
  liquidity_measure_timing: decision_time
optional filters:
  country_filter: [US]
  price_filter: { min_price: 5 }
domain_profile_refs: [DSP-2026-0001]
```

### 10b. ETF Seasonality Universe

```
instrument_universe_id: IUS-2026-0002
universe_family: etf_seasonality
asset_classes: [etf]
universe_construction_policy: external_index_membership
membership_timing_policy: fixed_snapshot
survivorship_policy: point_in_time
tradability_policy: tradable_at_decision_time
corporate_action_policy: adjusted
data_manifest_refs: [DM-2026-0011]
domain_profile_refs: [DSP-2026-0003]
```

### 10c. Options Event-Risk Universe

```
instrument_universe_id: IUS-2026-0003
universe_family: options_event_risk
asset_classes: [option, equity]
universe_construction_policy: rule_based_filter
membership_timing_policy: event_time
survivorship_policy: point_in_time
tradability_policy: tradable_at_entry_time
corporate_action_policy: adjusted
option_contract_filter:
  allowed_option_types: [call, put]
  expiry_filter: { min_dte: 5, max_dte: 60 }
  option_style: american
domain_profile_refs: [DSP-2026-0002]
```

### 10d. Crypto Spot Universe

```
instrument_universe_id: IUS-2026-0004
universe_family: crypto_spot_liquid
asset_classes: [crypto]
universe_construction_policy: rule_based_filter
membership_timing_policy: rebalance_time
survivorship_policy: current_constituents_only
tradability_policy: tradable_through_window
corporate_action_policy: raw
crypto_exchange_filter: [coinbase, kraken, binance_usd, gemini]
volume_filter:
  min_average_volume: 1000000
  volume_window_days: 30
domain_profile_refs: [DSP-2026-0005]
```

### 10e. Futures Term-Structure Universe

```
instrument_universe_id: IUS-2026-0005
universe_family: futures_term_structure
asset_classes: [future]
universe_construction_policy: rule_based_filter
membership_timing_policy: rebalance_time
survivorship_policy: point_in_time
tradability_policy: tradable_through_window
corporate_action_policy: raw
futures_contract_filter:
  allowed_contracts: [CL, GC, ES, ZN, NG, SI]
open_interest_filter:
  min_open_interest: 50000
domain_profile_refs: [DSP-2026-0004]
```

---

## 11. Agent/Tooling Layer

InstrumentUniverseSpec is a governance artifact. Hermes and OpenClaw may draft InstrumentUniverseSpecs and suggest missing eligibility filters, but they operate under the following constraints:

Hermes and OpenClaw **may**:
- Draft InstrumentUniverseSpecs following domain-neutral construction patterns
- Suggest inclusion/exclusion rules based on domain knowledge
- Validate InstrumentUniverseSpecs against the schema once implemented
- Reference existing DataManifests and domain profiles

Hermes and OpenClaw **may not**:
- Approve or advance a hypothesis
- Bypass or disable any validator
- Run unlocked autonomous search, Bayesian optimization, or genetic programming
- Advance hypothesis status in EdgeHypothesisRegistry
- Render a ReviewPacket decision
- Access live trading systems or production execution
- Select instruments into a universe based on runtime signals or scores

---

## 12. Validation Roadmap

InstrumentUniverseSpec v1 is the most recent completed milestone. All implementation items are complete:

1. **Design doc** (PR #104) — describes the schema and field semantics (this document)
2. **Schema** (PR #105) — JSON schema for InstrumentUniverseSpec v1
3. **Fixtures** (PR #106) — valid and invalid JSON fixtures covering all required fields, enums, and boundary conditions
4. **Schema hardening** (PR #107) — schema boundary and reviewer field hardening
5. **Validator** (PR #108) — `scripts/local/validate_instrument_universe_spec.py` implementing the schema rules
6. **Tests** (PR #109) — pytest coverage of all validator paths
7. **CI wiring** (PR #110) — added to `scripts/ci/validate_governance_manifests.sh`
8. **Docs status update** (this PR) — updated `docs/current_project_status.md` and `docs/README.md`

This roadmap follows the same pattern used for TrialLedger, SearchSpaceManifest, ModelAssessmentSpec, EdgeHypothesisRegistry, ExperimentSpec, and OutcomeSpec.

---

## 13. Stop Rules

InstrumentUniverseSpec v1 design respects the AED stop rules:

InstrumentUniverseSpec does **not** enable, unlock, or activate any of the following without an explicit, separately designed governance extension:

- **Autonomous search** — InstrumentUniverseSpec does not trigger or authorize autonomous instrument selection or universe construction. `autonomous_search` remains prohibited. Manual hypothesis generation and review is required.
- **Bayesian optimization** — No Bayesian optimization of universe construction parameters. `bayesian_optimization` remains prohibited.
- **Genetic programming** — No genetic programming of universe rule generation. `genetic_programming` remains prohibited.
- **Automated promotion** — No automated advancement of hypotheses. Human-authored ReviewPacket required.
- **Automated registry mutation** — No automated changes to EdgeHypothesisRegistry status.
- **Live trading** — InstrumentUniverseSpec is a design-time declaration only. It does not authorize live trading or production execution.
- **Production execution** — No production system execution.
- **GCRU integration** — GCRU integration requires a separately designed governance extension. InstrumentUniverseSpec does not include GCRU-specific fields or live feed connections.

These rules apply regardless of whether they are invoked by humans, scripts, or AI agents.
