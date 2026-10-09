#!/usr/bin/env python3
"""
Test Gates Auto-Discovery Engine

This module provides automatic discovery of test gates and stories
from project structure when no explicit configuration file exists.

Discovery sources:
1. docs/epics.md - Story structure and prerequisites
2. user-testing/scripts/TG-*.py - Test gate scripts
3. Git repository structure - Project metadata

Author: BMAD Framework
Date: 2025-10-10
"""

import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Any


class StoryDiscovery:
    """Discovers stories from project documentation."""

    STORY_PATTERN = re.compile(
        r"##\s+Story\s+(\S+)[:\s]+(.+?)$",
        re.MULTILINE | re.IGNORECASE
    )

    FILE_PATTERN = re.compile(
        r"^\s*-\s+([`]?)([^`\n]+?)\1\s*$",
        re.MULTILINE
    )

    PREREQUISITE_PATTERN = re.compile(
        r"\*\*Prerequisites?:\*\*\s*\n(.*?)(?=\n\n|\n\*\*|$)",
        re.DOTALL | re.IGNORECASE
    )

    def __init__(self, project_root: Path):
        """Initialize story discovery."""
        self.project_root = project_root
        self.epics_file = self._find_epics_file()

    def _find_epics_file(self) -> Optional[Path]:
        """Find epics.md or similar documentation."""
        candidates = [
            "docs/epics.md",
            "docs/EPICS.md",
            "epics.md",
            "EPICS.md",
            "docs/stories.md",
            "docs/STORIES.md",
        ]

        for candidate in candidates:
            path = self.project_root / candidate
            if path.exists():
                return path

        return None

    def discover_stories(self) -> Dict[str, Dict[str, Any]]:
        """
        Discover stories from epics file.

        Returns:
            Dict mapping story IDs to story metadata
        """
        if not self.epics_file:
            return {}

        try:
            content = self.epics_file.read_text(encoding='utf-8')
            return self._parse_epics_content(content)
        except Exception as e:
            print(f"Warning: Failed to parse {self.epics_file}: {e}", file=sys.stderr)
            return {}

    def _parse_epics_content(self, content: str) -> Dict[str, Dict[str, Any]]:
        """Parse epics.md content to extract stories."""
        stories = {}

        # Split into story sections
        sections = re.split(r"^##\s+Story\s+", content, flags=re.MULTILINE)

        for section in sections[1:]:  # Skip first split (pre-story content)
            story = self._parse_story_section(section)
            if story:
                stories[story['id']] = story

        return stories

    def _parse_story_section(self, section: str) -> Optional[Dict[str, Any]]:
        """Parse a single story section."""
        lines = section.split('\n', 1)
        if not lines:
            return None

        # Extract story ID and name from first line
        match = re.match(r"^(\S+?):?\s+(.+?)$", lines[0])
        if not match:
            return None

        story_id, name = match.groups()
        story_id = story_id.rstrip(':')  # Remove trailing colon if present

        # Extract description
        description = ""
        if len(lines) > 1:
            desc_match = re.search(
                r"\*\*So that\*\*\s+(.+?)(?=\n\n|\*\*)",
                lines[1],
                re.DOTALL | re.IGNORECASE
            )
            if desc_match:
                description = desc_match.group(1).strip()

        # Extract file references
        files = []
        if len(lines) > 1:
            files = self._extract_file_references(lines[1])

        # Extract prerequisites
        prerequisites = []
        if len(lines) > 1:
            prerequisites = self._extract_prerequisites(lines[1])

        return {
            'id': story_id,
            'name': name.strip(),
            'description': description,
            'files': files,
            'directories': [],
            'prerequisites': prerequisites,
            'is_test_gate': story_id.startswith('TG-')
        }

    def _extract_file_references(self, text: str) -> List[str]:
        """Extract file paths mentioned in story text."""
        files = []

        # Look for file paths in backticks or common patterns
        patterns = [
            r"`([a-zA-Z0-9_/.-]+\.(py|js|ts|jsx|tsx|java|go|rs|cpp|h|yaml|yml|json|md|j2|css|html))`",
        ]

        for pattern in patterns:
            matches = re.finditer(pattern, text)
            for match in matches:
                file_path = match.group(1)
                # Filter out invalid paths
                # Must start with valid path component (not command)
                # Must not contain spaces
                if (file_path.startswith(('src/', 'tests/', 'assets/', 'docs/', 'user-testing/'))
                    and ' ' not in file_path
                    and file_path not in files):
                    files.append(file_path)

        return files[:10]  # Limit to reasonable number

    def _extract_prerequisites(self, text: str) -> List[str]:
        """Extract story and test gate prerequisites."""
        prerequisites = []

        match = self.PREREQUISITE_PATTERN.search(text)
        if match:
            prereq_text = match.group(1)

            # Check for special cases
            if 'ALL previous stories' in prereq_text or 'ALL stories' in prereq_text:
                # Mark as requiring all stories - will be expanded later
                prerequisites.append('ALL_STORIES')

            # Look for test gate IDs like "TG-1.1", "TG-3.3"
            test_gate_ids = re.findall(
                r"(TG-\d+\.\d+)",
                prereq_text,
                re.IGNORECASE
            )
            prerequisites.extend(test_gate_ids)

            # Look for story IDs like "1.1", "Story 2.3", etc.
            story_ids = re.findall(
                r"(?:Story\s+)?(\d+\.\d+|\w+-\d+\.\d+|\w+-\d+)(?!\.\d)",
                prereq_text,
                re.IGNORECASE
            )
            # Filter out test gate IDs that were already captured
            story_ids = [sid for sid in story_ids if not sid.startswith('TG-')]
            prerequisites.extend(story_ids)

        return prerequisites


class TestGateDiscovery:
    """Discovers test gate scripts from project structure."""

    SCRIPT_PATTERN = re.compile(r"TG-([^_]+)_(.+)\.py$")

    REQUIRES_PATTERN = re.compile(
        r"(?:requires?|prerequisites?):\s*\[([^\]]+)\]",
        re.IGNORECASE
    )

    def __init__(self, project_root: Path):
        """Initialize test gate discovery."""
        self.project_root = project_root
        self.script_dirs = [
            "user-testing/scripts",
            "tests/gates",
            "testing/gates",
            "test/gates",
        ]

    def discover_test_gates(self) -> Dict[str, Dict[str, Any]]:
        """
        Discover test gate scripts.

        Returns:
            Dict mapping gate IDs to gate metadata
        """
        gates = {}

        for script_dir in self.script_dirs:
            dir_path = self.project_root / script_dir
            if not dir_path.exists():
                continue

            for script in dir_path.glob("TG-*.py"):
                gate = self._parse_test_gate_script(script)
                if gate:
                    gates[gate['id']] = gate

        return gates

    def _parse_test_gate_script(self, script_path: Path) -> Optional[Dict[str, Any]]:
        """Parse test gate script to extract metadata."""
        match = self.SCRIPT_PATTERN.search(script_path.name)
        if not match:
            return None

        gate_id = f"TG-{match.group(1)}"
        name_slug = match.group(2).replace('_', ' ').title()

        # Try to extract metadata from script docstring
        try:
            content = script_path.read_text(encoding='utf-8')
            metadata = self._extract_script_metadata(content)
        except Exception as e:
            print(f"Warning: Failed to read {script_path}: {e}", file=sys.stderr)
            metadata = {}

        return {
            'id': gate_id,
            'name': metadata.get('name', name_slug),
            'description': metadata.get('description', ''),
            'requires': metadata.get('requires', []),
            'script': str(script_path.relative_to(self.project_root)),
            'estimated_time': metadata.get('estimated_time', ''),
            'critical': metadata.get('critical', False),
        }

    def _extract_script_metadata(self, content: str) -> Dict[str, Any]:
        """Extract metadata from script docstring and comments."""
        metadata = {}

        # Extract docstring
        docstring_match = re.search(
            r'"""(.*?)"""',
            content,
            re.DOTALL
        )
        if docstring_match:
            docstring = docstring_match.group(1)

            # Extract name
            name_match = re.search(
                r"(?:TG-[\d.]+):\s*(.+?)(?:\n|Test Gate)",
                docstring,
                re.IGNORECASE
            )
            if name_match:
                metadata['name'] = name_match.group(1).strip()

            # Extract description
            desc_match = re.search(
                r"(?:This test|Test Approach|Description):\s*(.+?)(?:\n\n|Test |Usage:|$)",
                docstring,
                re.DOTALL | re.IGNORECASE
            )
            if desc_match:
                metadata['description'] = desc_match.group(1).strip()

            # Extract success threshold
            threshold_match = re.search(
                r"Success Threshold:\s*(.+?)(?:\n|$)",
                docstring,
                re.IGNORECASE
            )
            if threshold_match:
                metadata['threshold'] = threshold_match.group(1).strip()

        # Extract prerequisites from code
        requires_match = self.REQUIRES_PATTERN.search(content)
        if requires_match:
            requires_str = requires_match.group(1)
            # Parse story IDs from list
            story_ids = re.findall(r"['\"]([^'\"]+)['\"]", requires_str)
            metadata['requires'] = story_ids

        # Check if critical
        if 'critical' in content.lower() or 'lynchpin' in content.lower():
            metadata['critical'] = True

        return metadata


class ConfigGenerator:
    """Generates configuration from discovered metadata."""

    def __init__(self, project_root: Path):
        """Initialize config generator."""
        self.project_root = project_root
        self.story_discovery = StoryDiscovery(project_root)
        self.gate_discovery = TestGateDiscovery(project_root)

    def generate_config(self, format: str = 'yaml') -> str:
        """
        Generate configuration from discovered metadata.

        Args:
            format: Output format ('yaml' or 'json')

        Returns:
            Configuration as string
        """
        all_stories = self.story_discovery.discover_stories()
        gates_from_scripts = self.gate_discovery.discover_test_gates()

        config = {
            'project': {
                'name': self.project_root.name,
                'root': '.',
            },
            'stories': {},
            'test_gates': {},
        }

        # Separate regular stories from test gate stories
        test_gate_stories = {}
        regular_stories = {}

        for story_id, story in all_stories.items():
            if story.get('is_test_gate', False):
                test_gate_stories[story_id] = story
            else:
                regular_stories[story_id] = story

        # Add regular stories to config
        for story_id, story in regular_stories.items():
            config['stories'][story_id] = {
                'name': story['name'],
                'description': story['description'],
                'files': story['files'],
            }
            if story['directories']:
                config['stories'][story_id]['directories'] = story['directories']

        # Merge test gate data from epics.md and scripts
        for gate_id, gate_script_meta in gates_from_scripts.items():
            # Start with script metadata
            gate_config = {
                'name': gate_script_meta['name'],
                'requires': gate_script_meta.get('requires', []),
                'script': gate_script_meta['script'],
            }

            # Merge with epics.md metadata if available
            if gate_id in test_gate_stories:
                tg_story = test_gate_stories[gate_id]

                # Update name if epics.md has better name
                if tg_story.get('name'):
                    gate_config['name'] = tg_story['name']

                # Use prerequisites from epics.md (more reliable)
                if tg_story.get('prerequisites'):
                    prereqs = tg_story['prerequisites']

                    # Expand ALL_STORIES to actual story list
                    if 'ALL_STORIES' in prereqs:
                        prereqs = list(regular_stories.keys())

                    gate_config['requires'] = prereqs

                # Add description from epics.md
                if tg_story.get('description'):
                    gate_config['description'] = tg_story['description']

            # Add script metadata
            if gate_script_meta.get('description') and 'description' not in gate_config:
                gate_config['description'] = gate_script_meta['description']
            if gate_script_meta.get('estimated_time'):
                gate_config['estimated_time'] = gate_script_meta['estimated_time']
            if gate_script_meta.get('critical'):
                gate_config['critical'] = True

            config['test_gates'][gate_id] = gate_config

        if format == 'json':
            return json.dumps(config, indent=2)
        else:
            return self._to_yaml(config)

    def _to_yaml(self, obj: Any, indent: int = 0) -> str:
        """Simple YAML generator (not using PyYAML to avoid dependency)."""
        lines = []
        prefix = "  " * indent

        if isinstance(obj, dict):
            for key, value in obj.items():
                if isinstance(value, (dict, list)):
                    lines.append(f"{prefix}{key}:")
                    lines.append(self._to_yaml(value, indent + 1))
                elif isinstance(value, str):
                    # Quote strings with special chars
                    if ':' in value or '#' in value or value.startswith(' '):
                        lines.append(f'{prefix}{key}: "{value}"')
                    else:
                        lines.append(f"{prefix}{key}: {value}")
                elif isinstance(value, bool):
                    lines.append(f"{prefix}{key}: {str(value).lower()}")
                else:
                    lines.append(f"{prefix}{key}: {value}")
        elif isinstance(obj, list):
            for item in obj:
                if isinstance(item, (dict, list)):
                    lines.append(f"{prefix}-")
                    lines.append(self._to_yaml(item, indent + 1))
                elif isinstance(item, str):
                    lines.append(f"{prefix}- {item}")
                else:
                    lines.append(f"{prefix}- {item}")

        return '\n'.join(lines)


def discover_project_config(project_root: Optional[Path] = None) -> Dict[str, Any]:
    """
    Main entry point for project discovery.

    Args:
        project_root: Project root path (defaults to current directory)

    Returns:
        Discovered configuration dictionary
    """
    if project_root is None:
        project_root = Path.cwd()
    elif isinstance(project_root, str):
        project_root = Path(project_root)

    generator = ConfigGenerator(project_root)
    stories = generator.story_discovery.discover_stories()
    gates = generator.gate_discovery.discover_test_gates()

    return {
        'project': {
            'name': project_root.name,
            'root': str(project_root),
        },
        'stories': stories,
        'test_gates': gates,
    }


def main():
    """CLI entry point for discovery engine."""
    import argparse

    parser = argparse.ArgumentParser(
        description='Test Gates Auto-Discovery and Configuration Generator'
    )
    parser.add_argument(
        'project_root',
        nargs='?',
        default='.',
        help='Project root directory (default: current directory)'
    )
    parser.add_argument(
        '--format',
        choices=['json', 'yaml'],
        default='json',
        help='Output format (default: json)'
    )
    parser.add_argument(
        '--output',
        help='Output file path (default: stdout)'
    )
    parser.add_argument(
        '--check-config',
        action='store_true',
        help='Check if config file exists, exit with 0 if found, 1 if not'
    )

    args = parser.parse_args()

    project_root = Path(args.project_root).resolve()

    # Check config mode
    if args.check_config:
        config_file = project_root / '.claude' / 'testgates.config.yaml'
        if config_file.exists():
            print(str(config_file))
            sys.exit(0)
        else:
            sys.exit(1)

    # Generate config
    generator = ConfigGenerator(project_root)
    config_output = generator.generate_config(args.format)

    # Output
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(config_output)
        print(f"✅ Configuration written to: {output_path}", file=sys.stderr)
    else:
        print(config_output)


if __name__ == '__main__':
    main()
