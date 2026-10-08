### Step 2: Comprehensive Requirements Analysis

**FULL CONTEXT ANALYSIS** - This is where the high-context work happens:

**Document Discovery:**
Use Grep and Read tools to find ALL relevant documentation:
- Search `docs/prd/*${functionalityMatch}*.md`
- Search `docs/stories/*${functionalityMatch}*.md`
- Search `docs/features/*${functionalityMatch}*.md`
- Search project files for functionality references
- Analyze any custom specifications provided

**Requirements Extraction:**
For EACH discovered document, extract:
- **Acceptance Criteria**: All AC patterns (AC X.X.X, Given-When-Then, etc.)
- **User Stories**: "As a...I want...So that..." patterns
- **Integration Points**: System interfaces, APIs, dependencies
- **Success Metrics**: Performance thresholds, quality requirements
- **Risk Areas**: Edge cases, potential failure modes
- **Business Logic**: Domain-specific requirements (like Mike Israetel methodology)

**Context Integration:**
- Cross-reference requirements across multiple documents
- Identify dependencies between different acceptance criteria
- Map user workflows that span multiple components
- Understand system architecture context

### Step 3: Test Scenario Design

**Mode-Specific Scenario Planning:**
For each testing mode (automated/interactive/hybrid), design:

**Automated Scenarios:**
- Browser automation sequences using MCP tools
- API endpoint validation workflows
- Performance measurement checkpoints
- Error condition testing scenarios

**Interactive Scenarios:**
- Human-guided test procedures
- User experience validation flows
- Qualitative assessment activities
- Accessibility and usability evaluation

**Hybrid Scenarios:**
- Automated setup + manual validation
- Quantitative collection + qualitative interpretation
- Parallel automated/manual execution paths

### Step 4: Validation Criteria Definition

**Measurable Success Criteria:**
For each scenario, define:
- **Functional Validation**: Feature behavior correctness
- **Performance Validation**: Response times, resource usage
- **Quality Validation**: User experience, accessibility, reliability
- **Integration Validation**: Cross-system communication, data flow

**Evidence Requirements:**
- **Automated Evidence**: Screenshots, logs, metrics, API responses
- **Manual Evidence**: User feedback, qualitative observations
- **Hybrid Evidence**: Combined data + human interpretation
