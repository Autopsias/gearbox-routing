#!/usr/bin/env npx tsx
/**
 * Skill Evaluation Hook for Claude Code
 *
 * Based on Alexander Opalic's research showing skill activation jumps from ~20% to ~84%
 * when Claude is forced to explicitly evaluate skills before proceeding.
 *
 * This hook injects a reminder into every user prompt to:
 * 1. Evaluate available skills for relevance
 * 2. Activate relevant skills using the Skill() tool
 * 3. Only then proceed with implementation
 *
 * Reference: https://alexop.dev/posts/custom-tdd-workflow-claude-code-vue/
 */

import { readFileSync } from 'node:fs';
import { stdout, stdin } from 'node:process';

interface HookInput {
  session_id: string;
  user_prompt: string;
  working_directory: string;
}

function main(): void {
  // Read and consume stdin (required for hook protocol)
  let inputData = '';
  try {
    inputData = readFileSync(stdin.fd, 'utf-8');
  } catch {
    // stdin may be empty, that's OK
  }

  // Parse input if available (for context, though we don't use it currently)
  let hookInput: HookInput | null = null;
  if (inputData.trim()) {
    try {
      hookInput = JSON.parse(inputData);
    } catch {
      // Ignore parse errors
    }
  }

  // Keywords that suggest TDD/testing workflows where skill activation matters
  const tddKeywords = [
    'implement', 'develop', 'build', 'create', 'add feature',
    'test', 'tdd', 'atdd', 'acceptance', 'coverage',
    'epic-dev', 'story', 'phase'
  ];

  // Check if the prompt likely involves TDD workflows
  const prompt = hookInput?.user_prompt?.toLowerCase() || '';
  const isTddRelated = tddKeywords.some(keyword => prompt.includes(keyword));

  // Only inject the skill evaluation reminder for TDD-related prompts
  // This avoids overhead for simple queries
  if (isTddRelated) {
    const instruction = `
SKILL ACTIVATION REMINDER:

Before implementing, check if any of these workflow skills apply:
- /epic-dev, /epic-dev-full: For story implementation cycles
- /bmad:bmm:workflows:dev-story: For implementing stories
- /bmad:bmm:workflows:testarch-atdd: For generating acceptance tests
- /bmad:bmm:workflows:code-review: For adversarial code review

If a skill matches this task:
1. Invoke it using the Skill tool FIRST
2. Then proceed with the workflow it defines

If no skills apply, proceed normally.
`.trim();

    stdout.write(instruction);
  }
  // If not TDD-related, output nothing (hook is transparent)
}

main();
