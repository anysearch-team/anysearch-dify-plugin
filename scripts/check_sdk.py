"""通过真实 SDK stdio 验证注册、凭据、成功及异常，不执行真实搜索。"""
import json
import argparse
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time


def check(project: Path | None = None) -> dict:
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ, INSTALL_METHOD="local", PYTHONIOENCODING="utf-8")
    env.pop("REMOTE_INSTALL_KEY", None)
    process = subprocess.Popen([sys.executable, str(root / "tests/sdk_runtime.py")], cwd=project or root, env=env,
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, encoding="utf-8")
    messages = queue.Queue()

    def consume(stream):
        for line in stream:
            try:
                messages.put(json.loads(line))
            except ValueError:
                continue

    threads = [threading.Thread(target=consume, args=(stream,), daemon=True) for stream in (process.stdout, process.stderr)]
    for thread in threads:
        thread.start()
    try:
        results = {}
        cases = [("empty_credentials", "validate_tool_credentials", None),
                 ("zero_results", "invoke_tool", {"query": "synthetic-zero"}),
                 ("invalid_input", "invoke_tool", {"query": "private-input-sentinel", "unknown": True}),
                 ("business_error", "invoke_tool", {"query": "synthetic-business-failure"}),
                 ("discover", "invoke_tool", {"refresh": True}),
                 ("extract", "invoke_tool", {"url": "https://example.com/"}),
                 ("extract_invalid", "invoke_tool", {"url": "http://127.0.0.1/"}),
                 ("vertical_search", "invoke_tool", {"query":"React", "tag":"code.doc", "params_json":'{"library":"react"}'}),
                 ("vertical_optional_query", "invoke_tool", {"tag":"code.doc", "params_json":'{"library":"react"}'}),
                 ("vertical_invalid", "invoke_tool", {"query":"React", "tag":"code.doc", "params_json":'{"library":""}'})]
        for name, action, parameters in cases:
            data = {"type": "tool", "action": action, "provider": "anysearch", "credentials": {}, "user_id": "sdk-probe"}
            if parameters is not None:
                data.update(tool="extract" if name.startswith("extract") else "vertical_search" if name.startswith("vertical") else "discover" if name == "discover" else "search", tool_parameters=parameters)
            process.stdin.write(json.dumps({"session_id": name, "event": "request", "data": data}) + "\n")
            process.stdin.flush()
            frames = []
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                frame = messages.get(timeout=max(0.01, deadline-time.monotonic()))
                if frame.get("session_id") != name:
                    continue
                frames.append(frame)
                if frame.get("event") == "end" or frame.get("data", {}).get("type") == "end":
                    break
            else:
                raise RuntimeError("SDK did not finish")
            raw = json.dumps(frames)
            assert not any(v in raw for v in ("private-input-sentinel", "private-body-sentinel", "returned-secret-sentinel"))
            results[name] = {"frames": len(frames), "traceback_metadata": "traceback" in raw,
                             "success_variable": '"variable_name"' in raw,
                             "result_true": '"result": true' in raw,
                             "error_code": "INVALID_INPUT" if "INVALID_INPUT" in raw else "UPSTREAM_BUSINESS_ERROR" if "UPSTREAM_BUSINESS_ERROR" in raw else None,
                             "zero_results_text": "No results found" in raw, "sentinel_leak": False}
        assert results["empty_credentials"]["result_true"]
        assert results["zero_results"]["success_variable"] and results["zero_results"]["zero_results_text"]
        assert results["discover"]["success_variable"] and results["discover"]["error_code"] is None
        assert results['extract']['success_variable'] and results['extract']['error_code'] is None
        assert results["vertical_search"]["success_variable"] and results["vertical_search"]["zero_results_text"]
        assert results["vertical_optional_query"]["success_variable"]
        assert results["vertical_optional_query"]["zero_results_text"]
        assert results["vertical_optional_query"]["error_code"] is None
        for name in ("invalid_input", "business_error", "vertical_invalid", "extract_invalid"):
            assert results[name]["error_code"] and results[name]["traceback_metadata"]
            assert not results[name]["success_variable"]
        return {"synthetic_http": True, "sdk_process": True, "cases": results}
    finally:
        process.terminate()
        process.wait(timeout=5)
        for thread in threads:
            thread.join(timeout=1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--project', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    report = check(args.project)
    out = args.output or Path(".codex/sdk-unpacked.json" if args.project else ".codex/sdk-protocol.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
