import json
from pathlib import Path
import sys

import pytest

from src.core.ai_runtime import PersistentModelRunner


def test_persistent_runner_reuses_child_and_reports_job_errors(tmp_path: Path):
    script = tmp_path / "fake_runner.py"
    script.write_text(
        "\n".join(
            (
                "import json, sys",
                "print(json.dumps({'ready': True}), flush=True)",
                "count = 0",
                "for line in sys.stdin:",
                "    request = json.loads(line)",
                "    count += 1",
                "    if request['source'] == 'bad':",
                "        print(json.dumps({'ok': False, 'error': 'expected test failure'}), flush=True)",
                "    else:",
                "        print(json.dumps({'ok': True, 'message': f'job {count}'}), flush=True)",
            )
        ),
        encoding="utf-8",
    )
    runner = PersistentModelRunner([sys.executable, "-u", str(script)])
    try:
        assert runner.run("one", "output-one") == "job 1"
        with pytest.raises(RuntimeError, match="expected test failure"):
            runner.run("bad", "output-bad")
        assert runner.run("two", "output-two") == "job 3"
        assert runner.process is not None and runner.process.poll() is None
    finally:
        process = runner.process
        runner.close()
    assert process is not None and process.poll() is not None
