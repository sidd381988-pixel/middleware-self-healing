import os
import yaml
from dotenv import load_dotenv

load_dotenv()

_cfg = None


def load_config(path: str = None) -> dict:
    global _cfg
    if _cfg is not None:
        return _cfg

    if path is None:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(base, "config", "settings.yaml")

    with open(path, "r") as f:
        _cfg = yaml.safe_load(f)

    # Inject secrets from environment
    _cfg.setdefault("email", {})["smtp_password"] = os.environ.get("SMTP_PASSWORD", "")

    return _cfg
