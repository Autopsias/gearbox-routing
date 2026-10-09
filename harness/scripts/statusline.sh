#!/usr/bin/env bash
# Claude Code statusline, two rows (each printed line is its own row):
#   5h:N% (2h26m) 7d:N% (6d7h) | Ctx:N% | Cache:N% warm
#   [session] Model (effort) | dir | miss:<cause>
# Limits come first so a narrow terminal never cuts them; one line was 172 chars.
# Cache = prompt_cache.hit_ratio of the main conversation (Claude Code >= 2.1.251);
# "cold" means the cached prefix is past its TTL; "miss:<cause>" names the likely
# cause of the last miss (>= 2.1.260), shortened (system_prompt_changed -> system).
# Everything comes from the stdin JSON; rate_limits.*.resets_at is Unix epoch seconds
# (Pro/Max only, present after the first API response). No network, no cache.
input=$(cat)
now=$(date +%s)

IFS=$'\001' read -r session model effort cur_dir ctx h5 h5_reset d7 d7_reset hit warm misses cause <<<"$(
  printf '%s' "$input" | jq -r '
    [ (.session_name // ""), (.model.display_name // ""), (.effort.level // ""),
      (.workspace.current_dir // ""),
      ((.context_window.used_percentage // -1) | tostring),
      ((.rate_limits.five_hour.used_percentage // -1) | tostring),
      ((.rate_limits.five_hour.resets_at // 0) | tostring),
      ((.rate_limits.seven_day.used_percentage // -1) | tostring),
      ((.rate_limits.seven_day.resets_at // 0) | tostring),
      (((.prompt_cache.hit_ratio // -1) * 100) | tostring),
      ((.prompt_cache.warm // false) | tostring),
      ((.prompt_cache.misses // 0) | tostring),
      ((.prompt_cache.last_miss_cause.causes // []) | join("+")
        | gsub("_prompt_changed|_changed|_rewritten"; "") | gsub("ttl_expired_"; "ttl")
        | gsub("likely_server_side"; "server"))
    ] | join("")' 2>/dev/null
)"

# epoch sec -> "2h26m" / "6d7h" / "12m"
remain() {
  local r=$(( ${1%.*} - now )); [ "$r" -lt 0 ] && r=0
  local d=$(( r / 86400 )) h=$(( (r % 86400) / 3600 )) m=$(( (r % 3600) / 60 ))
  if   [ "$d" -gt 0 ]; then printf '%dd%dh' "$d" "$h"
  elif [ "$h" -gt 0 ]; then printf '%dh%dm' "$h" "$m"
  else printf '%dm' "$m"; fi
}
# seg LABEL PCT RESET -> "5h:N% (2h26m)"
seg() {
  local s; s=$(printf '%s:%.0f%%' "$1" "$2")
  [ "${3%.*}" -gt 0 ] 2>/dev/null && s="$s ($(remain "$3"))"
  printf '%s' "$s"
}

dir_name=$(basename "${cur_dir/#$HOME/\~}")
model=${model% (1M context)}

# row 1: limits, context, cache
top=""
[ "$h5" != "-1" ] && top=$(seg 5h "$h5" "$h5_reset")
[ "$d7" != "-1" ] && top="${top:+$top }$(seg 7d "$d7" "$d7_reset")"
[ "$ctx" != "-1" ] && top=$(printf '%s%sCtx:%.0f%%' "$top" "${top:+ | }" "$ctx")
[ "$hit" != "-100" ] && {
  state=cold; [ "$warm" = "true" ] && state=warm
  top=$(printf '%s%sCache:%.0f%% %s' "$top" "${top:+ | }" "$hit" "$state")
}

# row 2: who and where
bot=""
[ -n "$session" ] && bot="[$session] "
if [ -n "$effort" ]; then bot="${bot}${model} (${effort})"; else bot="${bot}${model}"; fi
bot="${bot} | ${dir_name}"
[ "$hit" != "-100" ] && [ "$misses" -gt 0 ] 2>/dev/null && [ -n "$cause" ] && bot="${bot} | miss:${cause}"

[ -n "$top" ] && printf '%s\n' "$top"
printf '%s' "$bot"
