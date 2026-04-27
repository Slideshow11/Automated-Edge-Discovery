# Pre-Earnings HypothesisSpec Examples

These files are deterministic HypothesisSpec fixtures for the AED pre-earnings
options workflow. They document example hypotheses and generate known candidate
counts for smoke testing and CI.

**All examples are fixtures only. They are not trading recommendations.**

---

## Examples

### 1. `basic_preearn_dpe2_delta50.json`

Single DPE × single delta — 1 candidate.

| Constraint | Values | Count |
|---|---|---|
| `entry_dpe` | `[2]` | 1 |
| `delta_target` | `[0.5]` | 1 |
| `expiry_rank` | `[0]` | 1 |
| **Total** | | **1** |

```python
from pathlib import Path
import json
from engine.edge_discovery.hypotheses import HypothesisSpec
from engine.edge_discovery.hypotheses.generate import generate_candidates

path = Path("examples/preearn_hypotheses/basic_preearn_dpe2_delta50.json")
with path.open() as f:
    data = json.load(f)

spec = HypothesisSpec.from_dict(data)
candidates = generate_candidates(
    spec,
    options_db_path="/tmp/fake.db",   # not used for count
    preearn_repo_path="/tmp/fake",
)
assert len(candidates) == 1
```

### 2. `coarse_grid_preearn.json`

2 DPE × 2 delta × 1 expiry = 4 candidates.

| Constraint | Values | Count |
|---|---|---|
| `entry_dpe` | `[2, 3]` | 2 |
| `delta_target` | `[0.3, 0.5]` | 2 |
| `expiry_rank` | `[0]` | 1 |
| **Total** | | **4** |

```python
from pathlib import Path
import json
from engine.edge_discovery.hypotheses import HypothesisSpec
from engine.edge_discovery.hypotheses.generate import generate_candidates

path = Path("examples/preearn_hypotheses/coarse_grid_preearn.json")
with path.open() as f:
    data = json.load(f)

spec = HypothesisSpec.from_dict(data)
candidates = generate_candidates(
    spec,
    options_db_path="/tmp/fake.db",
    preearn_repo_path="/tmp/fake",
)
assert len(candidates) == 4
```

---

## Schema Notes

Both examples conform to `HypothesisSpec` with:

- `source_type`: `empirical_observation`
- `asset_class`: `equity_options`
- `strategy_family`: `preearn_options`
- `required_data`: `["options_db", "preearn_repo"]`
- `status`: `draft`
- `validation_plan.methods`: `["cpcv"]`

They use `ParameterConstraint` with explicit `values` tuples only (no range bounds).

---

## Running Candidates

Candidate generation does **not** execute real backtests. Supply fake paths:

```bash
python3 -c "
from pathlib import Path
import json
from engine.edge_discovery.hypotheses import HypothesisSpec
from engine.edge_discovery.hypotheses.generate import generate_candidates

path = Path('examples/preearn_hypotheses/basic_preearn_dpe2_delta50.json')
spec = HypothesisSpec.from_dict(json.load(open(path)))
cands = generate_candidates(spec, options_db_path='/tmp/fake', preearn_repo_path='/tmp/fake')
print(len(cands), 'candidates generated')
"
```

Output: `1 candidates generated`

---

## Not Trading Recommendations

These are deterministic CI/test fixtures. They do not:

- run real backtests
- call IVOL or any data API
- produce Sharpe ratios or PnL figures
- imply any trading signal

---

## Files

```
examples/preearn_hypotheses/
├── basic_preearn_dpe2_delta50.json   # 1 candidate
└── coarse_grid_preearn.json          # 4 candidates
```
