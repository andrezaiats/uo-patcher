# Changelog

All notable changes to this project will be documented in this file.
Format follows [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/).

## 2026-06-02

### Documentation

- replace banner with terminal demo gif ([3bc6a2b](https://github.com/andrezaiats/uo-patcher/commit/3bc6a2be6e5119ac44c2346a888f19ea5596405a))
- update changelog [skip ci] ([9332bd1](https://github.com/andrezaiats/uo-patcher/commit/9332bd1611a0199cf9474f9afbd569ca4315c6f9))

### CI

- add changelog generation and commit lint workflows ([063c592](https://github.com/andrezaiats/uo-patcher/commit/063c592727f7985892d22733c85b5edb2486431e))

## 2026-05-26

### Documentation

- add banner image to README ([bf693be](https://github.com/andrezaiats/uo-patcher/commit/bf693be238c36129257b522a9fb603172c836c66))
- update README with verification, self-healing, and new CLI flags ([54945b2](https://github.com/andrezaiats/uo-patcher/commit/54945b21e3c70f156a34a94afbb13e7a834c7c8b))

### Miscellaneous

- add temp/ to .gitignore ([284798e](https://github.com/andrezaiats/uo-patcher/commit/284798e37637914314a357e71f4dfaccfb34aac4))

### Bug Fixes

- use 137-byte metadata headers in MYP archive builder ([ebb9308](https://github.com/andrezaiats/uo-patcher/commit/ebb930867b19f32238527bb6b89e03b0a107cac7))
- preserve EA-format .uop files, add self-healing for corrupted archives ([0f15a3f](https://github.com/andrezaiats/uo-patcher/commit/0f15a3f8740ca91116809e1195d9b34b6e9473ea))
- zero-pad hash keys in build_pack_index to match manifest format ([1baf877](https://github.com/andrezaiats/uo-patcher/commit/1baf877312405355740213bbe05d87c73bfec149))

## 2026-05-25

### Features

- parallelize pack downloads, auto-verify, retry failures ([1ec9c39](https://github.com/andrezaiats/uo-patcher/commit/1ec9c398198471f6979a99932b5327386fb99bc4))

### Bug Fixes

- skip missing chunks, add verification, build patcher packs ([5817125](https://github.com/andrezaiats/uo-patcher/commit/58171258c0d56c49d11918ed0d0e03a3c6aa386a))
- rewrite build_myp_archive to match MYP v5 spec ([36e7e86](https://github.com/andrezaiats/uo-patcher/commit/36e7e86b8d6ecd54d8748832b81f9d02ae240cc2))

