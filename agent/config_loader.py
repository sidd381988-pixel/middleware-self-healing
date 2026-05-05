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

    # Inject AWS credentials from environment into bedrock section.
    # boto3 also reads these automatically, but storing them here lets
    # other modules reference them if needed.
    bedrock = _cfg.setdefault("bedrock", {})
    if os.environ.get("AWS_ACCESS_KEY_ID"):
        bedrock["aws_access_key_id"] = os.environ["AWS_ACCESS_KEY_ID"]
    if os.environ.get("AWS_SECRET_ACCESS_KEY"):
        bedrock["aws_secret_access_key"] = os.environ["AWS_SECRET_ACCESS_KEY"]
    if os.environ.get("AWS_REGION"):
        bedrock["region"] = os.environ["AWS_REGION"]

    # Inject SMTP password
    _cfg.setdefault("email", {})["smtp_password"] = os.environ.get("SMTP_PASSWORD", "")

    return _cfg
