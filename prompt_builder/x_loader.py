from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "prompt_profiles"


def load_text(name):
    p = DATA_DIR / name
    if p.exists():
        return p.read_text(encoding="utf-8-sig").strip()
    return ""


def load_root_text(name):
    p = ROOT / name
    if p.exists():
        return p.read_text(encoding="utf-8-sig").strip()
    return ""


def part(title, body):
    body = str(body or "").strip()
    if not body:
        return ""
    return "\n[" + title + "]\n" + body + "\n"


def ltx_text():
    base = load_text("ltx23_prompt_profile_v1.md") or load_root_text("ltx23_prompt_profile_v1.md")
    extra = part("contract", load_text("ltx23_director_contract.md"))
    return (base + extra).strip()


def beat_text(base, policy=""):
    text = load_text("beat_writer_profile.md") or str(base or "")
    return (text.strip() + "\n" + str(policy or "").strip()).strip()


def story_text(base):
    return load_text("story_writer_profile.md") or str(base or "").strip()
