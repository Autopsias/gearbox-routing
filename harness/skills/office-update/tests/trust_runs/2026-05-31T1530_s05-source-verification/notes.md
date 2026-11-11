# S05 worked run — source verification (replayed Exa + one restricted-masked claim)

Registry resolved external_search->mcp__67c72af1-30eb-4017-9b56-b43df03f817d__web_search_exa@unknown, external_fetch->mcp__67c72af1-30eb-4017-9b56-b43df03f817d__web_fetch_exa@unknown (LIVE-tool list).

## Verdicts (folded into the §2 provenance record)
- corroborates -> verified     The speed of light is 299792458 m/s | srcs=['S2']
- not-found -> needs-review Project Harbor will cost Acme €21B | srcs=[]
- contradicts -> conflicting  Operating margin was 6% last quarter | srcs=['S3']

## Egress records (raw-claim sha256 [8] + sanitised query)
- e3c51334 mode=allow-light   query='The speed of light is 299792458 m/s'
- 9815848c mode=mask-required query='Project <BANNED_1> will cost <ENTITY_1> tens-of-EUR-billions'
- aac925bc mode=allow-light   query='Operating margin was 6% last quarter'

Packet sources after fold: ['S1', 'S2', 'S3']
