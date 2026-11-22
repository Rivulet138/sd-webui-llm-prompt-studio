"""Apply or restore the narrow Gradio 4.40 native input-collection hook."""
import argparse
import hashlib
import sys
from pathlib import Path


ORIGINAL = "B.inputs.map(x=>U(x))"
PATCHED = "(typeof window.llmPromptStudioGradioInputs===\"function\"?window.llmPromptStudioGradioInputs(B,U):B.inputs.map(x=>U(x)))"
BACKUP_SUFFIX = ".lps-native-inputs.bak"


def patch_asset(asset: Path, *, restore: bool = False) -> str:
    original = ORIGINAL.encode()
    patched = PATCHED.encode()
    data = asset.read_bytes()
    backup = asset.with_name(asset.name + BACKUP_SUFFIX)
    if restore:
        if not backup.is_file():
            raise ValueError("No native-inputs backup exists")
        saved = backup.read_bytes()
        if data != saved.replace(original, patched, 1):
            raise ValueError("Asset has changed since patching; refusing to overwrite it")
        asset.write_bytes(saved)
        backup.unlink()
        return "restored"
    if patched in data:
        return "already patched"
    if data.count(original) != 1:
        raise ValueError("Unsupported Gradio asset: expected exactly one native input collector")
    if backup.exists() and backup.read_bytes() != data:
        raise ValueError("Existing backup belongs to a different asset")
    backup.write_bytes(data)
    asset.write_bytes(data.replace(original, patched, 1))
    return "patched"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--asset", type=Path, help="Path to Blocks-B7cNA3Pj.js; defaults to this Python environment")
    parser.add_argument("--restore", action="store_true")
    args = parser.parse_args()
    if args.asset:
        asset = args.asset.resolve()
    else:
        import gradio
        asset = Path(gradio.__file__).parent / "templates/frontend/assets/Blocks-B7cNA3Pj.js"
    try:
        result = patch_asset(asset, restore=args.restore)
    except (OSError, ValueError) as error:
        parser.exit(1, f"{error}\n")
    sys.stdout.write(f"{result}: {asset}\n")
    sys.stdout.write("sha256=" + hashlib.sha256(asset.read_bytes()).hexdigest() + "\n")


if __name__ == "__main__":
    main()
