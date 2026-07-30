"""Skills handed to the agent, and read back so the team sheet can show them.

The harness loads these itself from SKILLS_DIR. Parsing them again here is not
duplication for its own sake: the point of the stage is that the manager can
see what Antigravity was told, so the same file has to be readable by the UI.
"""

from pathlib import Path

SKILLS_DIR = Path(__file__).parent / "skills"


def _split_front_matter(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        return {}, text
    _, _, rest = text.partition("---\n")
    front, sep, body = rest.partition("\n---")
    if not sep:
        return {}, text
    meta = {}
    for line in front.splitlines():
        key, colon, value = line.partition(":")
        if colon:
            meta[key.strip()] = value.strip()
    return meta, body.lstrip("\n").lstrip("-").lstrip("\n")


def load_skills() -> list[dict]:
    """Every skill on disk, newest name order, with its front matter parsed."""
    skills = []
    for path in sorted(SKILLS_DIR.glob("*/SKILL.md")):
        meta, body = _split_front_matter(path.read_text())
        skills.append({
            "name": meta.get("name", path.parent.name),
            "description": meta.get("description", ""),
            "body": body,
        })
    return skills
