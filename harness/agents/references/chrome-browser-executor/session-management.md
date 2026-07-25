# Browser Session Management and Cleanup

## Session Initialization with Validation
```python
# Read session directory and validate
session_dir = extract_session_directory_from_prompt()
if not os.path.exists(session_dir):
    FAIL_IMMEDIATELY(f"Session directory {session_dir} does not exist")

# Create and validate evidence directory
evidence_dir = os.path.join(session_dir, "evidence")
os.makedirs(evidence_dir, exist_ok=True)

# MANDATORY: Check browser pages and validate
try:
    pages = mcp__chrome-devtools__list_pages()
    if not pages or len(pages) == 0:
        # Create new page if none exists
        mcp__chrome-devtools__new_page(url="about:blank")
    else:
        # Select the first available page
        mcp__chrome-devtools__select_page(pageIdx=0)

    test_screenshot = mcp__chrome-devtools__take_screenshot(fullPage=False)
    if test_screenshot.error:
        FAIL_IMMEDIATELY("Browser setup failed - cannot take screenshots")
except Exception as e:
    FAIL_IMMEDIATELY(f"Browser setup failed: {e}")
```

## Integration with Session Management

### Input Processing with Validation
```python
def process_session_inputs(session_dir):
    # Validate session directory exists
    if not os.path.exists(session_dir):
        raise Exception(f"Session directory {session_dir} does not exist")

    # Read and validate browser instructions
    browser_instructions_path = os.path.join(session_dir, "BROWSER_INSTRUCTIONS.md")
    if not os.path.exists(browser_instructions_path):
        raise Exception("BROWSER_INSTRUCTIONS.md not found in session directory")

    instructions = read_file(browser_instructions_path)
    if not instructions or len(instructions.strip()) == 0:
        raise Exception("BROWSER_INSTRUCTIONS.md is empty")

    # Create evidence directory
    evidence_dir = os.path.join(session_dir, "evidence")
    os.makedirs(evidence_dir, exist_ok=True)

    return instructions, evidence_dir
```

### Browser Session Cleanup - MANDATORY
```python
def cleanup_browser_session():
    """Close browser pages to release session for next test - CRITICAL"""
    cleanup_status = {
        "browser_cleanup": "attempted",
        "cleanup_timestamp": get_timestamp(),
        "next_test_ready": False
    }

    try:
        # STEP 1: Get list of pages
        pages = mcp__chrome-devtools__list_pages()

        if pages and len(pages) > 0:
            # Close all pages except the last one (Chrome requires at least one page)
            for i in range(len(pages) - 1):
                close_result = mcp__chrome-devtools__close_page(pageIdx=i)

                if close_result and close_result.error:
                    cleanup_status["error"] = close_result.error
                    print(f"Failed to close page {i}: {close_result.error}")

            cleanup_status["browser_cleanup"] = "completed"
            cleanup_status["next_test_ready"] = True
            print("Browser pages closed successfully")
        else:
            cleanup_status["browser_cleanup"] = "no_pages"
            cleanup_status["next_test_ready"] = True
            print("No browser pages to close")

    except Exception as e:
        cleanup_status["browser_cleanup"] = "failed"
        cleanup_status["error"] = str(e)
        print(f"Browser cleanup exception: {e}")

    finally:
        # STEP 2: Always provide manual cleanup guidance
        if not cleanup_status["next_test_ready"]:
            print("Manual cleanup may be required:")
            print("1. Close any Chrome windows opened by Chrome DevTools")
            print("2. Check mcp__chrome-devtools__list_pages() for active pages")

    return cleanup_status

def finalize_execution_results(session_dir, execution_results):
    # Validate all evidence files exist
    for result in execution_results:
        for evidence_file in result.get("evidence_files", []):
            if not validate_file_exists(evidence_file):
                raise Exception(f"Evidence file missing: {evidence_file}")

    # MANDATORY: Clean up browser session BEFORE finalizing results
    browser_cleanup_status = cleanup_browser_session()

    # Generate execution log with evidence links
    execution_log_path = os.path.join(session_dir, "EXECUTION_LOG.md")
    write_validated_execution_log(execution_log_path, execution_results, browser_cleanup_status)

    # Create evidence summary
    evidence_summary = {
        "total_files": count_evidence_files(session_dir),
        "total_size": calculate_evidence_size(session_dir),
        "validation_status": "all_validated",
        "quality_check": "passed",
        "browser_cleanup": browser_cleanup_status
    }

    evidence_summary_path = os.path.join(session_dir, "evidence", "evidence_summary.json")
    save_json(evidence_summary_path, evidence_summary)

    return execution_log_path
```

### Output Generation with Evidence Validation

This agent GUARANTEES that every claim is backed by evidence and prevents the generation of fictional success reports that have plagued the testing framework. It will fail gracefully with evidence rather than hallucinate success.

## Execution Log Generation - EVIDENCE REQUIRED
```markdown
# EXECUTION_LOG.md - EVIDENCE VALIDATED RESULTS

## Session Information
- **Session ID**: {session_id}
- **Agent**: chrome-browser-executor
- **Execution Date**: {timestamp}
- **Evidence Directory**: evidence/
- **Browser Status**: Validated | Failed

## Execution Summary
- **Total Test Attempts**: {total_count}
- **Successfully Executed**: {success_count}
- **Failed**: {fail_count}
- **Blocked**: {blocked_count}
- **Evidence Files Created**: {evidence_count}

## Detailed Test Results

### Test 1: ChatGPT Interface Navigation
**Status**: PASSED
**Evidence Files**:
- `evidence/chatgpt_initial_20250830_185500.png` - Initial page load (47KB)
- `evidence/dom_analysis_20250830_185501.json` - Page structure analysis (12KB)
- `evidence/real_elements_20250830_185502.json` - Discovered element UIDs (3KB)

**Validation Results**:
- Navigation successful: Confirmed by screenshot
- Page fully loaded: Confirmed by DOM analysis
- Elements discoverable: Real UIDs extracted from snapshot

### Test 2: Form Input Attempt
**Status**: FAILED
**Evidence Files**:
- `evidence/authentication_required_20250830_185600.png` - Login page (52KB)
- `evidence/chatgpt_page_analysis_20250830_185600.json` - Page analysis (8KB)
- `evidence/error_state_20250830_185601.png` - Final error state (51KB)

**Failure Analysis**:
- **Root Cause**: Authentication barrier detected
- **Evidence**: Screenshots show login page, not chat interface
- **Impact**: Cannot proceed with form input testing
- **Console Errors**: Authentication required for GPT access

**Recovery Actions**:
- Captured comprehensive error evidence
- Documented authentication requirements
- Preserved session state for manual intervention

## Critical Findings

### Authentication Barrier
The testing revealed that the application requires active user authentication before accessing the interface. This blocks automated testing without pre-authentication.

**Evidence Supporting Finding**:
- Screenshot shows login page instead of chat interface
- DOM analysis confirms authentication elements present
- No chat input elements discoverable in unauthenticated state

### Technical Constraints
Browser automation works correctly, but application-level authentication prevents test execution.

## Evidence Validation Summary
- **Total Evidence Files**: {evidence_count}
- **Total Evidence Size**: {total_size_kb}KB
- **All Files Validated**: Yes | No
- **Screenshot Quality**: All valid | Some issues | Multiple failures
- **Data Integrity**: All parseable | Some corrupt | Multiple failures

## Browser Session Management
- **Active Pages**: {page_count}
- **Session Status**: Ready for next test | Manual intervention needed
- **Page Cleanup**: Completed | Failed | Manual cleanup required

## Recommendations for Next Testing Session
1. **Pre-authenticate** ChatGPT session manually before running automation
2. **Implement authentication bypass** in test environment
3. **Create mock interface** for authentication-free testing
4. **Focus on post-authentication workflows** in next iteration

## Framework Validation
- **Evidence Collection**: All claims backed by evidence files
- **Error Documentation**: Failures properly captured and analyzed
- **No False Positives**: No success claims without evidence
- **Quality Assurance**: All evidence files validated for integrity

---
*This execution log contains ONLY validated results with evidence proof for every claim*
```
