"""Test configuration.

Disable the import-time environment check so tests do not spawn the
bundled Windows executable.  This must happen before ``ckks_lite`` is
imported by the test modules.
"""

import os

os.environ["CKKS_LITE_ENV_CHECK"] = "skip"
