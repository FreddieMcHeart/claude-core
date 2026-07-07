# CHANGELOG

<!-- version list -->

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
