# CHANGELOG

<!-- version list -->

## v0.11.8 (2026-07-30)

### Bug Fixes

- **doctor,install**: The wiki mount check depended on the caller's cwd
  ([#43](https://github.com/FreddieMcHeart/claude-core/pull/43),
  [`dc8c0b0`](https://github.com/FreddieMcHeart/claude-core/commit/dc8c0b046f875ef4a8a5af97441a0e6482a6b7e1))

### Documentation

- **roadmap**: The plugin update command reported a success about the wrong source
  ([`b644fe8`](https://github.com/FreddieMcHeart/claude-core/commit/b644fe81701563733b4a4ec7d807d9a448b44470))


## v0.11.7 (2026-07-30)

### Bug Fixes

- Cost ledger accumulates in segments across resets, and counts dispatches by type
  ([`8986ef5`](https://github.com/FreddieMcHeart/claude-core/commit/8986ef55beb4aaf7ebbaefe7c46ce8fc0b01695c))

- **cost-ledger**: Both readers now handle the totals-nested schema, not just top-level
  ([`1368ee5`](https://github.com/FreddieMcHeart/claude-core/commit/1368ee521e81f6a0ad2a52b9d4040f7cf4565ba0))


## v0.11.6 (2026-07-30)

### Bug Fixes

- **doctor**: The wiki mirror check passed on a two-commit-behind mirror
  ([#41](https://github.com/FreddieMcHeart/claude-core/pull/41),
  [`0aa6d09`](https://github.com/FreddieMcHeart/claude-core/commit/0aa6d093217049b080e7b58d9ead95cadec47962))

### Documentation

- **roadmap**: An instrument that records it RAN does not record that its result ARRIVED
  ([`f72dda2`](https://github.com/FreddieMcHeart/claude-core/commit/f72dda2592583613627e38442b14de8277c6eb9a))

- **roadmap**: File the plugin-drift detector, and the block that fired on a merge
  ([`fc4adf1`](https://github.com/FreddieMcHeart/claude-core/commit/fc4adf1861999f24b325568cc3ef18171d70a9b7))

- **roadmap**: Settle the gauge design, and record why it lands after agent detection
  ([`fa879af`](https://github.com/FreddieMcHeart/claude-core/commit/fa879afa4501a01282184f4459c3cb0e17005ba3))

- **roadmap**: The aggregate tier makes the relay unreachable, and a new remedy shape
  ([`f9792f6`](https://github.com/FreddieMcHeart/claude-core/commit/f9792f699e5a6a597e62b87984b7e1c328cbeb6e))

- **roadmap**: The misclassified population is every CLI write, not merges
  ([`4d562d5`](https://github.com/FreddieMcHeart/claude-core/commit/4d562d582763a6d220a0406802a9e06ae4cf34f3))

- **roadmap**: The read-block fired a second time, on the next merge
  ([`6a7f884`](https://github.com/FreddieMcHeart/claude-core/commit/6a7f884b7048657d1b63a4818f84bd5c68556380))

- **roadmap**: The statusline HUD and the guard now measure different numbers
  ([`e7db8f7`](https://github.com/FreddieMcHeart/claude-core/commit/e7db8f708907925a8d94212dc25d56e004710388))


## v0.11.5 (2026-07-30)

### Bug Fixes

- **delegation-discipline**: Repoint the OTHER dead provenance pointer
  ([`7db2910`](https://github.com/FreddieMcHeart/claude-core/commit/7db291001696d1dbcb016ee572dc2b9ac009249c))


## v0.11.4 (2026-07-30)

### Bug Fixes

- Fire log now records the scoped count that actually fired, not the flat total
  ([`e7b536d`](https://github.com/FreddieMcHeart/claude-core/commit/e7b536dc9b9c9e1349a88ac11618c82f8d63ae06))

- Sub-agent detection keyed on payload, not background-job signal; per-agent state scoping
  ([`43d41e3`](https://github.com/FreddieMcHeart/claude-core/commit/43d41e31334fc3f0c2858f36f775710e80e58ec4))


## v0.11.3 (2026-07-30)

### Bug Fixes

- **models-router**: Repoint the provenance sentence at records that exist
  ([`c5a1386`](https://github.com/FreddieMcHeart/claude-core/commit/c5a138631e4b3522752cab353df2107f8c0aca81))

### Chores

- Untrack the committed .pyc, and record the Bash-counts-as-read defect
  ([`4d5f247`](https://github.com/FreddieMcHeart/claude-core/commit/4d5f247a09461feadc09913945b62294b7e4727c))

### Documentation

- Add the cost-discipline.py architecture diagram
  ([`bf6a080`](https://github.com/FreddieMcHeart/claude-core/commit/bf6a080452718cb574d7fd4028954ef189e562c2))

- **roadmap**: Shape Bash to return the answer; mark the Bash-streak proposal superseded
  ([`3ae7c09`](https://github.com/FreddieMcHeart/claude-core/commit/3ae7c0946033bd1e05f46660b5ffd75e52f130a8))


## v0.11.2 (2026-07-29)

### Bug Fixes

- Address review — correct comment, assert on emitted messages not parse errors
  ([`05103c2`](https://github.com/FreddieMcHeart/claude-core/commit/05103c2d2d4d1f8f7003b0f95dc3a887688e5d6a))

- **hooks**: Reset edit-loop counter on re-edit; correct model-cache docstrings
  ([`c2685aa`](https://github.com/FreddieMcHeart/claude-core/commit/c2685aa6724e31543a722f0a08d3911a601d53fb))

### Chores

- **hook**: Add a temporary payload-shape probe for sub-agent detection
  ([`7df0988`](https://github.com/FreddieMcHeart/claude-core/commit/7df0988fcca181830ee75284bbafb60a1e55b951))

### Documentation

- Correct the relay-child analysis from the transcript, and close the reader gap
  ([`8cdc506`](https://github.com/FreddieMcHeart/claude-core/commit/8cdc506b3898e299295abcd894acca8a4f2f5434))

- **models-router**: Narrow the effort-verification caveat to stage 2
  ([`6313772`](https://github.com/FreddieMcHeart/claude-core/commit/631377267525696a86cf93fb5cf930a950f42bfc))

- **models-router**: Stage 2 contradicts the screen — the 1.89x does not transfer
  ([`5e2ceba`](https://github.com/FreddieMcHeart/claude-core/commit/5e2ceba2bd9095e28378c6622db025276ee934ad))

- **roadmap**: Record the token-residency findings and retire the model-inheritance layer
  ([`2ad0fa8`](https://github.com/FreddieMcHeart/claude-core/commit/2ad0fa80f23b44e6d121d6b91af8e96c73bc9f86))

- **roadmap**: State the child-model mechanism instead of quoting a default that moved
  ([`d1e6610`](https://github.com/FreddieMcHeart/claude-core/commit/d1e6610ac7864feb226418c53782f783ab3c2f66))


## v0.11.1 (2026-07-27)

### Bug Fixes

- **hook**: Stop counting refused calls, and stop printing the bypass
  ([`f7072ce`](https://github.com/FreddieMcHeart/claude-core/commit/f7072ce62d0144cf5d094c1ec997376d3a2dcc18))


## v0.11.0 (2026-07-27)

### Features

- **hook**: Fire a re-validation trigger before a dated claim decays
  ([`b221a36`](https://github.com/FreddieMcHeart/claude-core/commit/b221a36efe870a277d39363ac4f5475ed919c396))


## v0.10.0 (2026-07-27)

### Features

- **hook**: Detect committed index rows pointing at uncommitted pages
  ([`d5d9046`](https://github.com/FreddieMcHeart/claude-core/commit/d5d904619235e660b46f88ce02b95f90299fe49c))


## v0.9.0 (2026-07-27)

### Bug Fixes

- Correct four defects found by self-review of this branch
  ([`0dcd355`](https://github.com/FreddieMcHeart/claude-core/commit/0dcd35592bf0264b813af10ec7916dd64bfb0b0f))

- Repair five defects found by review of this branch
  ([`3b2a0b2`](https://github.com/FreddieMcHeart/claude-core/commit/3b2a0b2281ed1f098e56123d133ae927bdfac81a))

- **cost-audit**: Price Opus 5 + Sonnet 5, normalise model IDs
  ([`3a991a0`](https://github.com/FreddieMcHeart/claude-core/commit/3a991a069c72bd914ddadee3933480990fde6c9e))

- **hook**: Name Opus 5 as the default main in the session reminder
  ([`df35201`](https://github.com/FreddieMcHeart/claude-core/commit/df352018aa905c183672a389aef5a352ce8c367b))

### Documentation

- Add ROADMAP.md — durable index of open follow-ups
  ([`59368c3`](https://github.com/FreddieMcHeart/claude-core/commit/59368c3b23d7249218c2829203313adf73bc8cc5))

### Features

- **delegation-discipline**: Add an over-delegation ceiling for an Opus 5 main
  ([`4299ce6`](https://github.com/FreddieMcHeart/claude-core/commit/4299ce6e4a86cc3d223987f00ba35951bd57d322))

- **hook**: Read effortLevel from settings so the reminder can't go stale
  ([`41d5ee1`](https://github.com/FreddieMcHeart/claude-core/commit/41d5ee1f517b465f7bd4cff98d73f2dda44e0c54))

- **models-router**: Make effort a first-class routing axis
  ([`0dcd074`](https://github.com/FreddieMcHeart/claude-core/commit/0dcd07499a2e62849919129aef9e1cf08fedaa30))


## v0.8.1 (2026-07-20)

### Bug Fixes

- **cost-ledger**: Correct aggregate_reads window label (not lifetime)
  ([`f25cf35`](https://github.com/FreddieMcHeart/claude-core/commit/f25cf35f560436d210e180bcbde6e410b71bc0e6))


## v0.8.0 (2026-07-20)

### Features

- **metrics**: Capture/compare cohort snapshots of session cost metrics
  ([`eb81d5a`](https://github.com/FreddieMcHeart/claude-core/commit/eb81d5a7a4f4b9248c745c087796c84ccaeabd46))

### Refactoring

- **reports**: Extract shared _report_table helper
  ([`cc82b1f`](https://github.com/FreddieMcHeart/claude-core/commit/cc82b1fa1e0a40e8eddf31904e8b2d7f47041e4e))


## v0.7.0 (2026-07-20)

### Features

- **fire-log**: Cost-discipline fire-log summary report
  ([`1d67c25`](https://github.com/FreddieMcHeart/claude-core/commit/1d67c25e11bf560599522ede9b0653c1b59b211f))


## v0.6.0 (2026-07-20)

### Features

- **cost-ledger**: Cross-session ledger summary report
  ([`e687cde`](https://github.com/FreddieMcHeart/claude-core/commit/e687cde9d0289912043b6311f59975ddf6795476))


## v0.5.1 (2026-07-20)

### Bug Fixes

- **cost-discipline**: Reset by_tool + metered_results on compaction
  ([`af67dd7`](https://github.com/FreddieMcHeart/claude-core/commit/af67dd7a0937aabcd2956f83d8e903aaaedad5f3))


## v0.5.0 (2026-07-18)

### Features

- **cost-discipline**: Meter all tool results + cross-session cost ledger
  ([#12](https://github.com/FreddieMcHeart/claude-core/pull/12),
  [`2a7c4e5`](https://github.com/FreddieMcHeart/claude-core/commit/2a7c4e5573b604fe7bbd89748cba368e16065b97))


## v0.4.0 (2026-07-15)

### Bug Fixes

- **hooks**: Drop the D-status shortcut that zeroed unmerged files
  ([#11](https://github.com/FreddieMcHeart/claude-core/pull/11),
  [`00c7b5e`](https://github.com/FreddieMcHeart/claude-core/commit/00c7b5ea3f12259133353f8f8de9ee6c3631e6a9))

- **hooks**: Parse git status with -z -uall so the hygiene scan cannot under-report
  ([#11](https://github.com/FreddieMcHeart/claude-core/pull/11),
  [`00c7b5e`](https://github.com/FreddieMcHeart/claude-core/commit/00c7b5ea3f12259133353f8f8de9ee6c3631e6a9))

### Features

- **hooks**: Nudge when the harness repo has stale uncommitted work
  ([#11](https://github.com/FreddieMcHeart/claude-core/pull/11),
  [`00c7b5e`](https://github.com/FreddieMcHeart/claude-core/commit/00c7b5ea3f12259133353f8f8de9ee6c3631e6a9))


## v0.3.1 (2026-07-13)

### Bug Fixes

- Update commit-workflow nudge text to the generic commit-commands plugin
  ([#10](https://github.com/FreddieMcHeart/claude-core/pull/10),
  [`d1356c4`](https://github.com/FreddieMcHeart/claude-core/commit/d1356c4fa173a57be05edfa52658adacbd29dc66))

### Documentation

- Add never-push-directly-to-main rule after admin-bypass docs push
  ([#8](https://github.com/FreddieMcHeart/claude-core/pull/8),
  [`4a8d980`](https://github.com/FreddieMcHeart/claude-core/commit/4a8d980e02bea38fbb98a650d9913e53e861f2a6))

- Document claude-core-wiki canonical-copy convention (edit standalone clone, not submodule)
  ([#9](https://github.com/FreddieMcHeart/claude-core/pull/9),
  [`3f38867`](https://github.com/FreddieMcHeart/claude-core/commit/3f388672ca630397dc9fee4709869311b16907f6))

- Document wiki canonical-copy convention
  ([#9](https://github.com/FreddieMcHeart/claude-core/pull/9),
  [`3f38867`](https://github.com/FreddieMcHeart/claude-core/commit/3f388672ca630397dc9fee4709869311b16907f6))

- Split wiki brain/ by project (claude-core vs downbeat), fix path references
  ([`a499622`](https://github.com/FreddieMcHeart/claude-core/commit/a4996224be812065ad911aa86cdc448ad25fc86f))

- Strengthen wiki-mirror sync into a required last-step, not a suggestion
  ([#9](https://github.com/FreddieMcHeart/claude-core/pull/9),
  [`3f38867`](https://github.com/FreddieMcHeart/claude-core/commit/3f388672ca630397dc9fee4709869311b16907f6))


## v0.3.0 (2026-07-10)

### Bug Fixes

- Address review nits on harvest.mjs ([#7](https://github.com/FreddieMcHeart/claude-core/pull/7),
  [`b2aba6c`](https://github.com/FreddieMcHeart/claude-core/commit/b2aba6cb29e519eb556f7026d8796a5b1c972304))

### Continuous Integration

- Wire release.yml to RELEASE_TOKEN now that main has a ruleset
  ([#6](https://github.com/FreddieMcHeart/claude-core/pull/6),
  [`da26388`](https://github.com/FreddieMcHeart/claude-core/commit/da2638822a483689b357f61d02b778f40187c99a))

### Documentation

- Add OSS community files (LICENSE, CONTRIBUTING, CODE_OF_CONDUCT, SECURITY)
  ([#5](https://github.com/FreddieMcHeart/claude-core/pull/5),
  [`46d1874`](https://github.com/FreddieMcHeart/claude-core/commit/46d18740d996d242edd48b2faf5d2e070b57ab04))

### Features

- **skills**: Add harvest — portable content-seed ideation skill+command
  ([#7](https://github.com/FreddieMcHeart/claude-core/pull/7),
  [`b2aba6c`](https://github.com/FreddieMcHeart/claude-core/commit/b2aba6cb29e519eb556f7026d8796a5b1c972304))


## v0.2.2 (2026-07-08)

### Bug Fixes

- Add missing UserPromptSubmit event to plugin hooks.json and migrate_to_plugin.py
  ([#3](https://github.com/FreddieMcHeart/claude-core/pull/3),
  [`c5de9a7`](https://github.com/FreddieMcHeart/claude-core/commit/c5de9a774261547be1856cab8dae41586de29285))


## v0.2.1 (2026-07-08)

### Bug Fixes

- Correct plugin install instructions to use marketplace add + install
  ([`fb5fde0`](https://github.com/FreddieMcHeart/claude-core/commit/fb5fde01217489dc54a1476c0a7ae535d579f894))


## v0.2.0 (2026-07-07)

### Bug Fixes

- Stage plugin.json in build_command — semantic-release doesn't auto-stage build_command output
  ([`8adbc24`](https://github.com/FreddieMcHeart/claude-core/commit/8adbc24f6c2dc6350ebc979503d1c516e614245d))

- Use current python-semantic-release config keys (conventional parser,
  default_templates.changelog_file)
  ([`204fc90`](https://github.com/FreddieMcHeart/claude-core/commit/204fc9078617b0dedfb8a10adb466866eb8a0f55))

### Continuous Integration

- Add python-semantic-release config (GitHub Releases only, no PyPI)
  ([`b6f605a`](https://github.com/FreddieMcHeart/claude-core/commit/b6f605afda57c0b134ca30887fde615d0207961e))

- Add release.yml — semantic-release GitHub Releases after portability.yml goes green
  ([`4936681`](https://github.com/FreddieMcHeart/claude-core/commit/4936681de28bd6383534d8780cd610312ecf2a05))

- Bump actions/checkout to v7, actions/setup-python to v6
  ([`11a1ee8`](https://github.com/FreddieMcHeart/claude-core/commit/11a1ee8d37b950359df56ee35545e17c920f7954))

### Documentation

- Implementation plan for release management + README polish
  ([`7523a34`](https://github.com/FreddieMcHeart/claude-core/commit/7523a3460ac323d773ce1eaeeb501685e14d00ad))

- Release-management + README design spec
  ([`d8e7685`](https://github.com/FreddieMcHeart/claude-core/commit/d8e76852873a2aacce2496c972e448c4197e347a))

- Rewrite README with real demo, plugin install path, release process
  ([`597311e`](https://github.com/FreddieMcHeart/claude-core/commit/597311e06c996498090406f4eceeb872addb5181))

### Features

- Sync .claude-plugin/plugin.json version via semantic-release build_command
  ([`19600c1`](https://github.com/FreddieMcHeart/claude-core/commit/19600c112d4e26cedae918167f79e45daa93b451))


## v0.1.0 (2026-07-06)

- Initial Release
