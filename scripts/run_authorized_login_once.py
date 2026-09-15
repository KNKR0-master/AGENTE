"""Operator-invoked single experiment. Never imported by server startup."""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.publisher.worker import PublisherWorker

if __name__ == '__main__':
    worker = PublisherWorker(ROOT, ROOT / 'data/agent.db')
    run_id = worker.authorize_controlled_login(1, explicitly_authorized=True)
    print(json.dumps({'run_id': run_id}), flush=True)
    worker.request_controlled_login(1, run_id=run_id, explicitly_authorized=True)
    reported = False
    previous = None
    while True:
        trace = worker.login_traces.get(1)
        marker = ROOT / 'data/publisher/evidence' / f'{run_id}.phone-confirmed'
        if trace and not trace.stopped and marker.exists() and not reported:
            worker.report_phone_confirmation(1)
            reported = True
        if trace:
            state = (trace.last_state, trace.result, trace.stopped)
            if state != previous:
                print(json.dumps({'run_id': run_id, 'semantic': trace.last_state,
                    'result': trace.result, 'stopped': trace.stopped, 'evidence': str(trace.path)}), flush=True)
                previous = state
        # Retain the owner process after a failed experiment; no restart or browser action.
        time.sleep(1)
