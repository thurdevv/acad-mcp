"""Keep the test runs out of the real operation log and the real 'received' folder."""

import os
import tempfile

_TMP = tempfile.mkdtemp(prefix="acadmcp_tests_")
os.environ["ACAD_MCP_LOG_DIR"] = os.path.join(_TMP, "logs")  # inherited by the subprocesses some tests start
os.environ.setdefault("ACAD_MCP_RECEIVED_DIR", os.path.join(_TMP, "received"))
