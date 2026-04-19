Example usage of run_wfa_cpcv

Python invocation:

from engine.edge_discovery.runner import run_wfa_cpcv

# Run two-split CPCV on two strategies and write outputs to ./wfa_out
res = run_wfa_cpcv(["stratA", "stratB"], n_splits=2, purge=0.01, cost_model=None, out_dir='./wfa_out')
print(res)

CLI (if you implement a small CLI wrapper):

# Not implemented in this change, but a simple Python one-liner can run it
python -c "from engine.edge_discovery.runner import run_wfa_cpcv; print(run_wfa_cpcv(['stratA'], n_splits=2, out_dir='./wfa_out'))"
