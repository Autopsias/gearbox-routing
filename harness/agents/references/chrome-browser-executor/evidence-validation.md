# Evidence Validation Patterns and Anti-Hallucination Controls

## ANTI-HALLUCINATION CONTROLS

### MANDATORY EVIDENCE REQUIREMENTS
1. **Every action must have screenshot proof**
2. **Every claim must have verifiable evidence file**
3. **No success reports without actual test execution**
4. **All evidence files must be saved to session directory**
5. **Screenshots must show actual page content, not empty pages**

### PROHIBITED BEHAVIORS
- **NEVER claim success without evidence**
- **NEVER generate fictional element UIDs**
- **NEVER report test completion without screenshots**
- **NEVER write execution logs for tests you didn't run**
- **NEVER assume tests worked if browser fails**

### EXECUTION VALIDATION PROTOCOL
- **EVERY claim must be backed by evidence file**
- **EVERY screenshot must be saved and verified non-empty**
- **EVERY error must be documented with evidence**
- **EVERY success must have before/after proof**

## Real DOM Discovery (No Fictional Elements)
```python
def discover_real_dom_elements():
    # MANDATORY: Get actual DOM structure
    snapshot = mcp__chrome-devtools__take_snapshot()

    if not snapshot or snapshot.error:
        save_error_evidence("dom_discovery_failed")
        FAIL_IMMEDIATELY("Cannot discover DOM - browser not responsive")

    # Save DOM analysis as evidence
    dom_evidence_file = f"{evidence_dir}/dom_analysis_{timestamp()}.json"
    save_dom_analysis(dom_evidence_file, snapshot)

    # Extract REAL elements with UIDs from actual snapshot
    real_elements = {
        "text_inputs": extract_text_inputs_from_snapshot(snapshot),
        "buttons": extract_buttons_from_snapshot(snapshot),
        "clickable_elements": extract_clickable_elements_from_snapshot(snapshot)
    }

    # Save real elements as evidence
    elements_file = f"{evidence_dir}/real_elements_{timestamp()}.json"
    save_real_elements(elements_file, real_elements)

    return real_elements
```

## Evidence-Validated Test Execution
```python
def execute_test_with_evidence(test_scenario):
    # MANDATORY: Screenshot before action
    before_screenshot = f"{evidence_dir}/{test_scenario.id}_before_{timestamp()}.png"
    result = mcp__chrome-devtools__take_screenshot(fullPage=False)

    if result.error:
        FAIL_WITH_EVIDENCE(f"Cannot capture before screenshot for {test_scenario.id}")
        return

    # Save screenshot to file
    Write(file_path=before_screenshot, content=result.data)

    # Execute the actual action
    action_result = None
    if test_scenario.action_type == "navigate":
        action_result = mcp__chrome-devtools__navigate_page(url=test_scenario.url)
    elif test_scenario.action_type == "click":
        # Use UID from snapshot
        action_result = mcp__chrome-devtools__click(uid=test_scenario.element_uid)
    elif test_scenario.action_type == "type":
        # Use UID from snapshot for text input
        action_result = mcp__chrome-devtools__fill(
            uid=test_scenario.element_uid,
            value=test_scenario.input_text
        )

    # MANDATORY: Screenshot after action
    after_screenshot = f"{evidence_dir}/{test_scenario.id}_after_{timestamp()}.png"
    result = mcp__chrome-devtools__take_screenshot(fullPage=False)

    if result.error:
        FAIL_WITH_EVIDENCE(f"Cannot capture after screenshot for {test_scenario.id}")
        return

    # Save screenshot to file
    Write(file_path=after_screenshot, content=result.data)

    # MANDATORY: Validate action actually worked
    if action_result and action_result.error:
        error_screenshot = f"{evidence_dir}/{test_scenario.id}_error_{timestamp()}.png"
        error_result = mcp__chrome-devtools__take_screenshot(fullPage=False)
        if not error_result.error:
            Write(file_path=error_screenshot, content=error_result.data)

        FAIL_WITH_EVIDENCE(f"Action failed: {action_result.error}")
        return

    SUCCESS_WITH_EVIDENCE(f"Test {test_scenario.id} completed successfully",
                         [before_screenshot, after_screenshot])
```

## ChatGPT Interface Testing (REAL PATTERNS)
```python
def test_chatgpt_real_implementation():
    # Step 1: Navigate with evidence
    navigate_result = mcp__chrome-devtools__navigate_page(url="https://chatgpt.com")
    initial_screenshot = save_evidence_screenshot("chatgpt_initial")

    if navigate_result.error:
        FAIL_WITH_EVIDENCE(f"Navigation to ChatGPT failed: {navigate_result.error}")
        return

    # Step 2: Discover REAL page structure
    snapshot = mcp__chrome-devtools__take_snapshot()
    if not snapshot or snapshot.error:
        FAIL_WITH_EVIDENCE("Cannot get ChatGPT page structure")
        return

    page_analysis_file = f"{evidence_dir}/chatgpt_page_analysis_{timestamp()}.json"
    save_page_analysis(page_analysis_file, snapshot)

    # Step 3: Check for authentication requirements
    if requires_authentication(snapshot):
        auth_screenshot = save_evidence_screenshot("authentication_required")

        write_execution_log_entry({
            "status": "BLOCKED",
            "reason": "Authentication required before testing can proceed",
            "evidence": [auth_screenshot, page_analysis_file],
            "recommendation": "Manual login required or implement authentication bypass"
        })
        return  # DO NOT continue with fake success

    # Step 4: Find REAL input elements with UIDs
    real_elements = discover_real_dom_elements()

    if not real_elements.get("text_inputs"):
        no_input_screenshot = save_evidence_screenshot("no_input_found")
        FAIL_WITH_EVIDENCE("No text input elements found in ChatGPT interface")
        return

    # Step 5: Attempt real interaction using UID
    text_input = real_elements["text_inputs"][0]  # Use first found input

    type_result = mcp__chrome-devtools__fill(
        uid=text_input.uid,
        value="Order total: $299.99 for 2 items"
    )

    interaction_screenshot = save_evidence_screenshot("text_input_attempt")

    if type_result.error:
        FAIL_WITH_EVIDENCE(f"Text input failed: {type_result.error}")
        return

    # Step 6: Look for submit button and attempt submission
    submit_buttons = real_elements.get("buttons", [])
    submit_button = find_submit_button(submit_buttons)

    if submit_button:
        submit_result = mcp__chrome-devtools__click(uid=submit_button.uid)

        if submit_result.error:
            submit_failed_screenshot = save_evidence_screenshot("submit_failed")
            FAIL_WITH_EVIDENCE(f"Submit button click failed: {submit_result.error}")
            return

        # Wait for response and validate
        mcp__chrome-devtools__wait_for(text="AI response")
        response_screenshot = save_evidence_screenshot("ai_response_check")

        # Check if response appeared
        response_snapshot = mcp__chrome-devtools__take_snapshot()
        if response_appeared_in_snapshot(response_snapshot):
            SUCCESS_WITH_EVIDENCE("Application input successful with response",
                                [initial_screenshot, interaction_screenshot, response_screenshot])
        else:
            FAIL_WITH_EVIDENCE("No AI response detected after submission")
    else:
        no_submit_screenshot = save_evidence_screenshot("no_submit_button")
        FAIL_WITH_EVIDENCE("No submit button found in interface")
```

## Evidence Validation Functions
```python
def save_evidence_screenshot(description):
    """Save screenshot with mandatory validation"""
    timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
    filename = f"{evidence_dir}/{description}_{timestamp_str}.png"

    result = mcp__chrome-devtools__take_screenshot(fullPage=False)

    if result.error:
        raise Exception(f"Screenshot failed: {result.error}")

    # MANDATORY: Save screenshot data to file
    Write(file_path=filename, content=result.data)

    # Validate file was created
    if not validate_file_exists(filename):
        raise Exception(f"Screenshot {filename} was not created")

    return filename

def validate_file_exists(filepath):
    """Validate file exists using Read tool"""
    try:
        content = Read(file_path=filepath)
        return len(content) > 0
    except:
        return False

def FAIL_WITH_EVIDENCE(message):
    """Fail test with evidence collection"""
    error_screenshot = save_evidence_screenshot("error_state")
    console_logs = mcp__chrome-devtools__list_console_messages()

    error_entry = {
        "status": "FAILED",
        "timestamp": datetime.now().isoformat(),
        "error_message": message,
        "evidence_files": [error_screenshot],
        "console_logs": console_logs,
        "browser_state": "error"
    }

    write_execution_log_entry(error_entry)

    # DO NOT continue execution after failure
    raise TestExecutionException(message)

def SUCCESS_WITH_EVIDENCE(message, evidence_files):
    """Report success ONLY with evidence"""
    success_entry = {
        "status": "PASSED",
        "timestamp": datetime.now().isoformat(),
        "success_message": message,
        "evidence_files": evidence_files,
        "validation": "evidence_verified"
    }

    write_execution_log_entry(success_entry)
```

## Batch Form Filling with Chrome DevTools
```python
def fill_form_batch(form_elements):
    """Fill multiple form fields at once using Chrome DevTools"""
    elements_to_fill = []

    for element in form_elements:
        elements_to_fill.append({
            "uid": element.uid,
            "value": element.value
        })

    # Use batch fill_form function
    result = mcp__chrome-devtools__fill_form(elements=elements_to_fill)

    if result.error:
        FAIL_WITH_EVIDENCE(f"Batch form fill failed: {result.error}")
        return False

    # Take screenshot after form fill
    form_filled_screenshot = save_evidence_screenshot("form_filled")

    SUCCESS_WITH_EVIDENCE("Form filled successfully", [form_filled_screenshot])
    return True
```

## Anti-Hallucination Task Verification

```python
# MANDATORY: Update task only after REAL evidence exists
def complete_task_with_evidence(task_id, evidence_files):
    # Verify ALL evidence files exist
    for file in evidence_files:
        if not validate_file_exists(file):
            TaskUpdate(
                taskId=task_id,
                metadata={"hallucination_warning": f"Evidence file missing: {file}"}
            )
            raise Exception("Cannot complete task - evidence missing")

    # Only mark complete with verified evidence
    TaskUpdate(
        taskId=task_id,
        status="completed",
        metadata={
            "evidence_verified": True,
            "files_validated": len(evidence_files)
        }
    )
```
