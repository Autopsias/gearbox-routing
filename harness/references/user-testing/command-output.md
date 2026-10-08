### Command Output

#### Success Output
```
BMAD Testing Session Completed Successfully

Session ID: epic-3.3_hybrid_{YYYYMMDD}_{HHMMSS}_{hash}
Target: Epic 3.3 - Test Execution & BMAD Reporting Engine
Mode: Hybrid (Automated + Manual)
Duration: 4.2 minutes

Results Summary:
- Acceptance Criteria Coverage: 85.7% (6/7 ACs)
- Test Scenarios Executed: 12/15
- Evidence Files Generated: 41
- Issues Found: 2 Major, 3 Minor
- Recommendations: 8 actionable items

Reports Generated:
- BMAD Brief: workspace/testing/sessions/{session_id}/phase_3/bmad_brief.md
- Recommendations: workspace/testing/sessions/{session_id}/phase_3/recommendations.json
- Evidence Package: workspace/testing/sessions/{session_id}/phase_2/evidence/package.json

Next Steps:
1. Review BMAD brief for critical findings
2. Implement high-priority recommendations
3. Address browser automation reliability issues

Session archived to: workspace/testing/archive/{YYYY-MM-DD}/
```

#### Error Output
```
BMAD Testing Session Failed

Session ID: epic-3.3_hybrid_{YYYYMMDD}_{HHMMSS}_{hash}
Target: Epic 3.3 - Test Execution & BMAD Reporting Engine
Duration: 2.1 minutes (failed in Phase 2)

Failure Analysis:
- Phase 1: Completed successfully
- Phase 2: Browser automation timeout, manual testing incomplete
- Phase 3: Not reached

Recovery Options:
1. Retry with interactive-only mode: /user_testing epic-3.3 --mode interactive
2. Resume from Phase 2: /user_testing --resume epic-3.3_hybrid_{YYYYMMDD}_{HHMMSS}_{hash}
3. Review detailed logs: workspace/testing/sessions/{session_id}/phase_2/execution_log.json

### Browser Session Troubleshooting
If tests fail with "Browser is already in use" error:
1. **Close Chrome windows**: Look for Chrome DevTools-opened Chrome windows and close them
2. **Check page status**: Use Chrome DevTools list_pages to see active sessions
3. **Retry test**: Browser session will be available for next test

Session preserved for debugging. Use --cleanup to remove when resolved.
```
