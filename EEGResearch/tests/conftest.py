"""Sets the required API_TOKEN/ADMIN_TOKEN before `src.app.main` is imported at collection."""
import os

os.environ.setdefault("API_TOKEN", "test-learner-token")
os.environ.setdefault("ADMIN_TOKEN", "test-admin-token")

# Drop any settings cached before the tokens above were set.
from src.app.config import get_settings  # noqa: E402

get_settings.cache_clear()
