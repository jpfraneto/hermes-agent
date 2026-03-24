"""Skill slash commands — scan installed skills and build invocation messages.

Shared between CLI (cli.py) and gateway (gateway/run.py) so both surfaces
can invoke skills via /skill-name commands.
"""

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_skill_commands: Dict[str, Dict[str, Any]] = {}


def _coerce_string_list(value: Any) -> List[str]:
    """Coerce a frontmatter value into a list of non-empty strings."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _normalize_trigger_text(text: str) -> str:
    """Lowercase and collapse whitespace for phrase matching."""
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def scan_skill_commands() -> Dict[str, Dict[str, Any]]:
    """Scan ~/.hermes/skills/ and return a mapping of /command -> skill info.

    Returns:
        Dict mapping "/skill-name" to {name, description, skill_md_path, skill_dir}.
    """
    global _skill_commands
    _skill_commands = {}
    try:
        from tools.skills_tool import SKILLS_DIR, _parse_frontmatter
        if not SKILLS_DIR.exists():
            return _skill_commands
        for skill_md in SKILLS_DIR.rglob("SKILL.md"):
            path_str = str(skill_md)
            if '/.git/' in path_str or '/.github/' in path_str or '/.hub/' in path_str:
                continue
            try:
                content = skill_md.read_text(encoding='utf-8')
                frontmatter, body = _parse_frontmatter(content)
                name = frontmatter.get('name', skill_md.parent.name)
                description = frontmatter.get('description', '')
                metadata = frontmatter.get('metadata') if isinstance(frontmatter.get('metadata'), dict) else {}
                hermes_meta = metadata.get('hermes') if isinstance(metadata.get('hermes'), dict) else {}
                if not description:
                    for line in body.strip().split('\n'):
                        line = line.strip()
                        if line and not line.startswith('#'):
                            description = line[:80]
                            break
                auto_trigger_phrases = _coerce_string_list(
                    hermes_meta.get("auto_trigger_phrases")
                    or metadata.get("auto_trigger_phrases")
                    or frontmatter.get("auto_trigger_phrases")
                )
                cmd_name = name.lower().replace(' ', '-').replace('_', '-')
                _skill_commands[f"/{cmd_name}"] = {
                    "name": name,
                    "description": description or f"Invoke the {name} skill",
                    "skill_md_path": str(skill_md),
                    "skill_dir": str(skill_md.parent),
                    "auto_trigger_phrases": auto_trigger_phrases,
                }
            except Exception:
                continue
    except Exception:
        pass
    return _skill_commands


def get_skill_commands() -> Dict[str, Dict[str, Any]]:
    """Return the current skill commands mapping (scan first if empty)."""
    if not _skill_commands:
        scan_skill_commands()
    return _skill_commands


def match_skill_command_from_text(user_text: str) -> Optional[str]:
    """
    Return the best matching skill command key for plain-language user text.

    Matching is deterministic and phrase-based: skills can opt in via
    metadata.hermes.auto_trigger_phrases in frontmatter.
    """
    normalized_text = _normalize_trigger_text(user_text)
    if not normalized_text:
        return None

    best_match: Optional[str] = None
    best_score = -1

    for cmd_key, skill_info in get_skill_commands().items():
        for phrase in skill_info.get("auto_trigger_phrases", []) or []:
            normalized_phrase = _normalize_trigger_text(phrase)
            if normalized_phrase and normalized_phrase in normalized_text:
                score = len(normalized_phrase)
                if score > best_score:
                    best_match = cmd_key
                    best_score = score

    return best_match


def build_auto_skill_invocation_message(user_text: str) -> Optional[Dict[str, str]]:
    """
    Build a slash-command-equivalent invocation message from plain user text.

    Returns:
        {"cmd_key", "skill_name", "message"} or None if no skill matches.
    """
    cmd_key = match_skill_command_from_text(user_text)
    if not cmd_key:
        return None

    commands = get_skill_commands()
    skill_info = commands.get(cmd_key)
    if not skill_info:
        return None

    message = build_skill_invocation_message(cmd_key, user_text)
    if not message:
        return None

    return {
        "cmd_key": cmd_key,
        "skill_name": skill_info["name"],
        "message": message,
    }


def build_skill_invocation_message(cmd_key: str, user_instruction: str = "") -> Optional[str]:
    """Build the user message content for a skill slash command invocation.

    Args:
        cmd_key: The command key including leading slash (e.g., "/gif-search").
        user_instruction: Optional text the user typed after the command.

    Returns:
        The formatted message string, or None if the skill wasn't found.
    """
    commands = get_skill_commands()
    skill_info = commands.get(cmd_key)
    if not skill_info:
        return None

    skill_md_path = Path(skill_info["skill_md_path"])
    skill_dir = Path(skill_info["skill_dir"])
    skill_name = skill_info["name"]

    try:
        content = skill_md_path.read_text(encoding='utf-8')
    except Exception:
        return f"[Failed to load skill: {skill_name}]"

    parts = [
        f'[SYSTEM: The user has invoked the "{skill_name}" skill, indicating they want you to follow its instructions. The full skill content is loaded below.]',
        "",
        content.strip(),
    ]

    supporting = []
    for subdir in ("references", "templates", "scripts", "assets"):
        subdir_path = skill_dir / subdir
        if subdir_path.exists():
            for f in sorted(subdir_path.rglob("*")):
                if f.is_file():
                    rel = str(f.relative_to(skill_dir))
                    supporting.append(rel)

    if supporting:
        parts.append("")
        parts.append("[This skill has supporting files you can load with the skill_view tool:]")
        for sf in supporting:
            parts.append(f"- {sf}")
        parts.append(f'\nTo view any of these, use: skill_view(name="{skill_name}", file_path="<path>")')

    if user_instruction:
        parts.append("")
        parts.append(f"The user has provided the following instruction alongside the skill invocation: {user_instruction}")

    return "\n".join(parts)
