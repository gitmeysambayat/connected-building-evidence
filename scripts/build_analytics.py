import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from twin.analytics import fit
if __name__ == "__main__":
    result = fit(force=True)
    print(json.dumps({"status": result["status"], "rooms": [{k: r[k] for k in ("room", "train_count", "validation_count", "test_count", "model", "baseline", "improvement_percent")} for r in result["rooms"]]}, indent=2))
