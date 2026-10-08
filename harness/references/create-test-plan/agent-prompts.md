### Step 5: Agent Prompt Generation

**Specialized Agent Instructions:**
Create detailed prompts for each subagent that include:
- Specific context from the requirements analysis
- Detailed instructions for their specialized role
- Expected input/output formats
- Integration points with other agents

### Step 6: Test Plan File Generation

Create comprehensive test plan file:

```markdown
# Test Plan: ${functionalityMatch}

**Created**: $(date)
**Target**: ${functionalityMatch}
**Context**: [Summary of analyzed documentation]

## Requirements Analysis

### Source Documents
- [List of all documents analyzed]
- [Cross-references and dependencies identified]

### Acceptance Criteria
[All extracted ACs with full context]

### User Stories
[All user stories requiring validation]

### Integration Points
[System interfaces and dependencies]

### Success Metrics
[Performance thresholds and quality requirements]

### Risk Areas
[Edge cases and potential failure modes]

## Test Scenarios

### Automated Test Scenarios
[Detailed browser automation and API test scenarios]

### Interactive Test Scenarios
[Human-guided testing procedures and UX validation]

### Hybrid Test Scenarios
[Combined automated + manual approaches]

## Validation Criteria

### Success Thresholds
[Measurable pass/fail criteria for each scenario]

### Evidence Requirements
[What evidence proves success or failure]

### Quality Gates
[Performance, usability, and reliability standards]

## Agent Execution Prompts

### Requirements Analyzer Prompt
```
Context: ${functionalityMatch} testing based on comprehensive requirements analysis
Task: [Specific instructions based on discovered documentation]
Expected Output: [Structured requirements summary]
```

### Scenario Designer Prompt
```
Context: Transform ${functionalityMatch} requirements into executable test scenarios
Task: [Mode-specific scenario generation instructions]
Expected Output: [Test scenario definitions]
```

### Validation Planner Prompt
```
Context: Define success criteria for ${functionalityMatch} validation
Task: [Validation criteria and evidence requirements]
Expected Output: [Comprehensive validation plan]
```

### Browser Executor Prompt
```
Context: Execute automated tests for ${functionalityMatch}
Task: [Browser automation and performance testing]
Expected Output: [Execution results and evidence]
```

### Interactive Guide Prompt
```
Context: Guide human testing of ${functionalityMatch}
Task: [User experience and qualitative validation]
Expected Output: [Interactive session results]
```

### Evidence Collector Prompt
```
Context: Aggregate all ${functionalityMatch} testing evidence
Task: [Evidence compilation and organization]
Expected Output: [Comprehensive evidence package]
```

### BMAD Reporter Prompt
```
Context: Generate final report for ${functionalityMatch} testing
Task: [Analysis and actionable recommendations]
Expected Output: [BMAD-format final report]
```
