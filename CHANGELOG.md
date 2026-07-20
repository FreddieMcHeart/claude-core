# CHANGELOG

<!-- version list -->

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
