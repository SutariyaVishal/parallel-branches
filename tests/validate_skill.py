"""Validate every skill under skills/ against the Agent Skills rules, plus the plugin manifests.

Checks: frontmatter name/description limits, name matches the directory, body length,
links to bundled files exist, references stay one level deep, manifests are valid JSON
and agree on the plugin name. Python 3.9+, standard library only.

Usage: python3 tests/validate_skill.py [repo-root]
"""

import json
import os
import re
import sys

NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
RESERVED = ("claude", "anthropic")
LINK_RE = re.compile(r"`((?:scripts|references|assets)/[A-Za-z0-9_./-]+)`")


def parse_frontmatter(text):
    """Return (dict of single-line keys, body). Raises ValueError if there is no frontmatter."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError("SKILL.md must start with '---'")
    end = next((i for i, line in enumerate(lines[1:], 1) if line.strip() == "---"), None)
    if end is None:
        raise ValueError("frontmatter is not closed with '---'")
    fields = {}
    for line in lines[1:end]:
        match = re.match(r"^([A-Za-z][A-Za-z0-9_-]*):\s*(.*)$", line)
        if match:
            fields[match.group(1)] = match.group(2).strip()
    return fields, "\n".join(lines[end + 1:])


def check_skill(skill_dir):
    problems = []
    path = os.path.join(skill_dir, "SKILL.md")
    if not os.path.isfile(path):
        return ["%s: missing SKILL.md" % skill_dir]
    with open(path) as fh:
        text = fh.read()
    try:
        fields, body = parse_frontmatter(text)
    except ValueError as exc:
        return ["%s: %s" % (path, exc)]
    name, desc = fields.get("name", ""), fields.get("description", "")
    if not name:
        problems.append("name is required")
    else:
        if len(name) > 64 or not NAME_RE.match(name):
            problems.append("name %r must be 1-64 chars of lowercase letters, digits and single hyphens" % name)
        if name != os.path.basename(os.path.normpath(skill_dir)):
            problems.append("name %r must match the directory name" % name)
        if any(word in name for word in RESERVED):
            problems.append("name %r contains a reserved word" % name)
    if not desc:
        problems.append("description is required")
    elif len(desc) > 1024:
        problems.append("description is %d chars, limit is 1024" % len(desc))
    if len(fields.get("compatibility", "")) > 500:
        problems.append("compatibility is over 500 chars")
    if len(text.splitlines()) > 500:
        problems.append("SKILL.md is over 500 lines")
    for rel in sorted(set(LINK_RE.findall(body))):
        if not os.path.exists(os.path.join(skill_dir, rel)):
            problems.append("SKILL.md mentions %s, which does not exist" % rel)
    refs = os.path.join(skill_dir, "references")
    if os.path.isdir(refs):
        for entry in os.listdir(refs):
            if os.path.isdir(os.path.join(refs, entry)):
                problems.append("references/%s is a directory; keep references one level deep" % entry)
    return ["%s: %s" % (path, p) for p in problems]


def check_manifests(root, skill_names):
    problems = []
    loaded = {}
    for rel in (".claude-plugin/plugin.json", ".claude-plugin/marketplace.json"):
        path = os.path.join(root, rel)
        try:
            with open(path) as fh:
                loaded[rel] = json.load(fh)
        except (OSError, ValueError) as exc:
            problems.append("%s: %s" % (rel, exc))
    plugin = loaded.get(".claude-plugin/plugin.json")
    market = loaded.get(".claude-plugin/marketplace.json")
    if plugin:
        for key in ("name", "version", "description", "license"):
            if not plugin.get(key):
                problems.append("plugin.json is missing %r" % key)
    if plugin and market:
        listed = [p.get("name") for p in market.get("plugins", [])]
        if plugin.get("name") not in listed:
            problems.append("marketplace.json does not list plugin %r" % plugin.get("name"))
        if any("version" in p for p in market.get("plugins", [])) and plugin.get("version"):
            problems.append("set version in plugin.json or marketplace.json, not both")
        for key in ("name", "owner", "description"):
            if not market.get(key):
                problems.append("marketplace.json is missing %r" % key)
    if plugin and skill_names and plugin.get("name") not in skill_names:
        problems.append("plugin name %r matches none of the skills %s" % (plugin.get("name"), skill_names))
    return problems


def main(argv):
    root = os.path.abspath(argv[1]) if len(argv) > 1 else os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    skills_dir = os.path.join(root, "skills")
    names = sorted(d for d in os.listdir(skills_dir) if os.path.isdir(os.path.join(skills_dir, d))) \
        if os.path.isdir(skills_dir) else []
    problems = []
    if not names:
        problems.append("no skills found under %s" % skills_dir)
    for name in names:
        problems.extend(check_skill(os.path.join(skills_dir, name)))
    problems.extend(check_manifests(root, names))
    for problem in problems:
        print("FAIL", problem)
    print("%d skill(s) checked, %d problem(s)" % (len(names), len(problems)))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
