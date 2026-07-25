#!/bin/bash
# Global Audio Feedback Hooks for Claude Code
# This script provides intelligent audio feedback across all your projects
# 
# Installation: Add this to your Claude Code hooks configuration
# Usage: Called automatically by Claude Code events

set -euo pipefail

# Configuration paths
readonly AUDIO_BASE="$HOME/.claude/audio-feedback/generated"
readonly PREFS_FILE="$HOME/.claude/audio-feedback/config/user_preferences.json"
readonly STATE_FILE="$HOME/.claude/audio-feedback/config/runtime_state.json"
readonly LOG_FILE="$HOME/.claude/audio-feedback/logs/audio-feedback.log"

# Ensure log directory exists
mkdir -p "$(dirname "$LOG_FILE")"

# Logging function
log_event() {
    local level="$1"
    local message="$2"
    local timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    echo "[$timestamp] [$level] $message" >> "$LOG_FILE"
}

# Check if audio feedback is enabled
is_enabled() {
    if [[ ! -f "$PREFS_FILE" ]]; then
        log_event "WARN" "Preferences file not found, audio feedback disabled"
        return 1
    fi
    
    local enabled=$(jq -r '.enabled // false' "$PREFS_FILE" 2>/dev/null)
    [[ "$enabled" == "true" ]]
}

# Get current time-based context
get_time_context() {
    local hour=$(date '+%H')
    local day_of_week=$(date '+%u')  # 1=Monday, 7=Sunday
    
    # Weekend detection
    if [[ $day_of_week -ge 6 ]]; then
        echo "weekend"
        return
    fi
    
    # Work hours detection (default 9-18, customizable in prefs)
    local work_start=$(jq -r '.scheduling.time_based_switching.work_hours.start // "09:00"' "$PREFS_FILE" | cut -d: -f1)
    local work_end=$(jq -r '.scheduling.time_based_switching.work_hours.end // "18:00"' "$PREFS_FILE" | cut -d: -f1)
    
    if [[ $hour -ge $work_start && $hour -lt $work_end ]]; then
        echo "work_hours"
    else
        echo "after_hours"
    fi
}

# Get project context from current directory
get_project_context() {
    local current_dir=$(pwd)
    local project_name=$(basename "$current_dir")
    
    # Check for project-specific overrides
    local overrides=$(jq -c '.context_awareness.project_overrides.rules[]? // empty' "$PREFS_FILE" 2>/dev/null)
    
    while IFS= read -r rule; do
        local pattern=$(echo "$rule" | jq -r '.project_pattern')
        if [[ "$project_name" == $pattern || "$current_dir" == $pattern ]]; then
            echo "$rule" | jq -r '.preferred_styles[0] // "default"'
            return
        fi
    done <<< "$overrides"
    
    echo "default"
}

# Check if in focus mode
is_focus_mode() {
    # Check for focus mode triggers in recent git messages or project names
    local focus_triggers=$(jq -r '.focus_mode.auto_detect.triggers[]?' "$PREFS_FILE" 2>/dev/null)
    local current_branch=$(git branch --show-current 2>/dev/null || echo "")
    local project_name=$(basename "$(pwd)")
    
    while IFS= read -r trigger; do
        if [[ "$current_branch" == *"$trigger"* || "$project_name" == *"$trigger"* ]]; then
            return 0
        fi
    done <<< "$focus_triggers"
    
    # Check manual focus mode (could be set by external tool)
    if [[ -f "$STATE_FILE" ]]; then
        local focus_enabled=$(jq -r '.focus_mode.manual_enabled // false' "$STATE_FILE" 2>/dev/null)
        [[ "$focus_enabled" == "true" ]]
    else
        return 1
    fi
}

# Get preferred styles for current context
get_preferred_styles() {
    local notification_type="$1"  # input_needed or task_complete
    local time_context=$(get_time_context)
    local project_context=$(get_project_context)
    
    # Focus mode override
    if is_focus_mode; then
        echo '["minimal"]'
        return
    fi
    
    # Time-based preferences
    local time_key="scheduling.time_based_switching.${time_context}.preferred_styles"
    local time_styles=$(jq -c ".$time_key // []" "$PREFS_FILE" 2>/dev/null)
    
    # Notification type preferences  
    local type_key="audio_feedback.${notification_type}.preferred_styles"
    local type_styles=$(jq -c ".$type_key // []" "$PREFS_FILE" 2>/dev/null)
    
    # Merge preferences (time-based takes precedence)
    if [[ "$time_styles" != "[]" ]]; then
        echo "$time_styles"
    else
        echo "$type_styles"
    fi
}

# Get preferred voices for current context
get_preferred_voices() {
    local time_context=$(get_time_context)
    
    # Focus mode override
    if is_focus_mode; then
        echo '["casey_minimal"]'
        return
    fi
    
    # Time-based voice preferences
    local time_key="scheduling.time_based_switching.${time_context}.preferred_voices"
    local time_voices=$(jq -c ".$time_key // []" "$PREFS_FILE" 2>/dev/null)
    
    # Default voice preferences
    local default_voices=$(jq -c '.voice_preferences.preferred_voices // []' "$PREFS_FILE" 2>/dev/null)
    
    if [[ "$time_voices" != "[]" ]]; then
        echo "$time_voices"
    else
        echo "$default_voices"
    fi
}

# Load recent audio selections to avoid repetition
get_recent_selections() {
    local notification_type="$1"
    
    if [[ -f "$STATE_FILE" ]]; then
        jq -c ".recent_selections.${notification_type} // []" "$STATE_FILE" 2>/dev/null || echo "[]"
    else
        echo "[]"
    fi
}

# Update recent selections
update_recent_selections() {
    local notification_type="$1"
    local filename="$2"
    local max_recent=5
    
    # Create state file if it doesn't exist
    if [[ ! -f "$STATE_FILE" ]]; then
        echo '{"recent_selections":{"input_needed":[],"task_complete":[]}}' > "$STATE_FILE"
    fi
    
    # Update recent selections
    local updated=$(jq --arg type "$notification_type" --arg file "$filename" --argjson max "$max_recent" '
        .recent_selections[$type] = ([.recent_selections[$type][], $file] | unique | .[-$max:])
    ' "$STATE_FILE")
    
    echo "$updated" > "$STATE_FILE"
}

# Smart audio file selection
select_audio_file() {
    local notification_type="$1"  # input_needed or task_complete
    local audio_dir="$AUDIO_BASE/${notification_type//_/-}"
    
    if [[ ! -d "$audio_dir" ]]; then
        log_event "ERROR" "Audio directory not found: $audio_dir"
        return 1
    fi
    
    local preferred_styles=($(get_preferred_styles "$notification_type" | jq -r '.[]' 2>/dev/null || echo "friendly"))
    local preferred_voices=($(get_preferred_voices | jq -r '.[]' 2>/dev/null || echo "sam_friendly"))
    local recent_files=($(get_recent_selections "$notification_type" | jq -r '.[]' 2>/dev/null || echo ""))
    
    # Build candidate list based on preferences
    local candidates=()
    
    # Try preferred combinations first
    for style in "${preferred_styles[@]}"; do
        for voice in "${preferred_voices[@]}"; do
            local voice_name=$(echo "$voice" | cut -d_ -f2-)
            local pattern="${style}-${voice_name}-*.*"
            
            while IFS= read -r -d '' file; do
                local basename=$(basename "$file")
                # Skip if recently used
                local skip=false
                for recent in "${recent_files[@]:-}"; do
                    if [[ "$basename" == "$recent" ]]; then
                        skip=true
                        break
                    fi
                done
                
                if [[ "$skip" == false ]]; then
                    candidates+=("$file")
                fi
            done < <(find "$audio_dir" -name "$pattern" -print0 2>/dev/null)
        done
    done
    
    # Fallback to any available files if no preferred matches
    if [[ ${#candidates[@]} -eq 0 ]]; then
        while IFS= read -r -d '' file; do
            local basename=$(basename "$file")
            local skip=false
            for recent in "${recent_files[@]:-}"; do
                if [[ "$basename" == "$recent" ]]; then
                    skip=true
                    break
                fi
            done
            
            if [[ "$skip" == false ]]; then
                candidates+=("$file")
            fi
        done < <(find "$audio_dir" -name "*.mp3" -o -name "*.aiff" -print0 2>/dev/null)
    fi
    
    # Select random candidate
    if [[ ${#candidates[@]} -gt 0 ]]; then
        local selected_index=$((RANDOM % ${#candidates[@]}))
        local selected_file="${candidates[$selected_index]}"
        local selected_basename=$(basename "$selected_file")
        
        # Update recent selections
        update_recent_selections "$notification_type" "$selected_basename"
        
        log_event "INFO" "Selected audio: $selected_basename for $notification_type"
        echo "$selected_file"
    else
        log_event "WARN" "No suitable audio files found for $notification_type"
        return 1
    fi
}

# Get volume for current context
get_volume() {
    local notification_type="$1"
    local time_context=$(get_time_context)
    
    # Focus mode volume reduction
    if is_focus_mode; then
        local focus_volume=$(jq -r '.focus_mode.focus_behavior.volume_reduction // 0.3' "$PREFS_FILE" 2>/dev/null)
        echo "$focus_volume"
        return
    fi
    
    # Time-based volume
    local time_key="scheduling.time_based_switching.${time_context}.volume"
    local time_volume=$(jq -r ".$time_key // null" "$PREFS_FILE" 2>/dev/null)
    
    # Notification type volume
    local type_key="audio_feedback.${notification_type}.volume"
    local type_volume=$(jq -r ".$type_key // 0.7" "$PREFS_FILE" 2>/dev/null)
    
    if [[ "$time_volume" != "null" ]]; then
        echo "$time_volume"
    else
        echo "$type_volume"
    fi
}

# Play audio file with appropriate volume
play_audio() {
    local audio_file="$1"
    local notification_type="$2"
    
    if [[ ! -f "$audio_file" ]]; then
        log_event "ERROR" "Audio file not found: $audio_file"
        return 1
    fi
    
    local volume=$(get_volume "$notification_type")
    
    # Platform-specific audio playback
    if command -v afplay >/dev/null 2>&1; then
        # macOS
        afplay "$audio_file" --volume "$volume" 2>/dev/null &
    elif command -v paplay >/dev/null 2>&1; then
        # Linux with PulseAudio
        paplay "$audio_file" --volume=$(($(echo "$volume * 65536" | bc -l | cut -d. -f1))) 2>/dev/null &
    elif command -v aplay >/dev/null 2>&1; then
        # Linux with ALSA
        aplay "$audio_file" -q 2>/dev/null &
    else
        log_event "ERROR" "No audio playback command found (afplay, paplay, aplay)"
        return 1
    fi
}

# Main hook functions
on_input_needed() {
    if ! is_enabled; then
        return 0
    fi
    
    local enabled=$(jq -r '.audio_feedback.input_needed.enabled // true' "$PREFS_FILE" 2>/dev/null)
    if [[ "$enabled" != "true" ]]; then
        return 0
    fi
    
    log_event "INFO" "Input needed event triggered"
    
    local audio_file=$(select_audio_file "input_needed")
    if [[ $? -eq 0 && -n "$audio_file" ]]; then
        play_audio "$audio_file" "input_needed"
    fi
}

on_task_complete() {
    if ! is_enabled; then
        return 0
    fi
    
    local enabled=$(jq -r '.audio_feedback.task_complete.enabled // true' "$PREFS_FILE" 2>/dev/null)
    if [[ "$enabled" != "true" ]]; then
        return 0
    fi
    
    log_event "INFO" "Task complete event triggered"
    
    local audio_file=$(select_audio_file "task_complete")
    if [[ $? -eq 0 && -n "$audio_file" ]]; then
        play_audio "$audio_file" "task_complete"
    fi
}

# Utility functions for manual testing
test_input_sound() {
    echo "🔊 Testing input needed sound..."
    on_input_needed
}

test_complete_sound() {
    echo "🔊 Testing task complete sound..."
    on_task_complete
}

toggle_focus_mode() {
    if [[ ! -f "$STATE_FILE" ]]; then
        echo '{"focus_mode":{"manual_enabled":true}}' > "$STATE_FILE"
    else
        local current=$(jq -r '.focus_mode.manual_enabled // false' "$STATE_FILE")
        local new_state=$([[ "$current" == "true" ]] && echo "false" || echo "true")
        local updated=$(jq --argjson enabled "$new_state" '.focus_mode.manual_enabled = $enabled' "$STATE_FILE")
        echo "$updated" > "$STATE_FILE"
    fi
    
    local status=$(jq -r '.focus_mode.manual_enabled' "$STATE_FILE")
    echo "🎯 Focus mode: $status"
}

# Command-line interface for testing
case "${1:-}" in
    "test-input")
        test_input_sound
        ;;
    "test-complete")
        test_complete_sound
        ;;
    "on_input_needed")
        on_input_needed
        ;;
    "on_task_complete")
        on_task_complete
        ;;
    "toggle-focus")
        toggle_focus_mode
        ;;
    "status")
        echo "📊 Audio Feedback Status:"
        echo "  Enabled: $(is_enabled && echo "✅ Yes" || echo "❌ No")"
        echo "  Focus Mode: $(is_focus_mode && echo "🎯 Active" || echo "🔓 Inactive")"
        echo "  Time Context: $(get_time_context)"
        echo "  Project Context: $(get_project_context)"
        echo "  Generated Files: $(find "$AUDIO_BASE" \( -name "*.mp3" -o -name "*.aiff" \) 2>/dev/null | wc -l)"
        ;;
    "")
        # No arguments - this is normal for hook usage
        ;;
    *)
        echo "Usage: $0 [test-input|test-complete|toggle-focus|status|on_input_needed|on_task_complete]"
        ;;
esac