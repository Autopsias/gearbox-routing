## Special Cases

**All gates passed:**
```
================================================================================
🎉 ALL TEST GATES PASSED!
================================================================================

  ✅ TG-1.1 - Agent Framework Validation
  ✅ TG-1.2 - Word Parser Validation
  ...
  ✅ TG-4.6 - End-to-End MVP Validation

MVP is complete! 🎉
```

**No gates found:**
```
❌ No test gates configured. Check /tmp/testgates_config.json
```

---

## Execution Notes

- Use bash commands with proper error handling
- Check gate completion ONLY via report files (not implementation files)
- Get all gate info dynamically from `/tmp/testgates_config.json`
- Keep output clean and focused
- **Always show progress** (passed gates list)
- **Always show next step** (what gate is next)
- **Make it actionable** (clear instructions)
- **Let test gate scripts validate story completion** - don't check files here!
