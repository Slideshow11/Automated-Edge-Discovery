from pathlib import Path
import json

REPO = Path(__file__).resolve().parents[1]
INPUT = REPO / "examples" / "metaorders.csv"
OUT = REPO / "examples" / "calibrate_ac_v2_demo.json"
DIAG_DIR = REPO / "examples" / "diagnostics"

from engine.edge_discovery.calibrate_ac_v2 import calibrate_ac_v2
from engine.edge_discovery import diagnostics as diag
import pandas as pd

print("Running v2 calibrator demo on:", INPUT)
res = calibrate_ac_v2(str(INPUT), output_path=str(OUT), model="normalized", cluster_by="symbol", bootstrap="cluster", n_bootstrap=200, strict=True)
print("Wrote demo output to", OUT)

# load df for diagnostics
try:
    df = pd.read_csv(INPUT)
    df_feats = None
    from engine.edge_discovery import features as ft
    dfv = df.copy()
    df_feats = ft.build_features(dfv, strict=True)
    diag.diagnostic_report(df_feats, res, DIAG_DIR)
    print("Wrote diagnostics to", DIAG_DIR)
except Exception as e:
    print("Diagnostics failed:", e)
