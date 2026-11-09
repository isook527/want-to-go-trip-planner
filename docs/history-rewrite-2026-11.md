# 2026 年 11 月 Git 历史重写 · Git history rewrite

2026 年 11 月 9 日，仓库改写提交历史以移除作者元数据中的个人邮箱。改写前后逐提交核对，所有文件树完全相同，现有 Release ZIP 的 SHA-256 一致。

On 9 November 2026, the repository history was rewritten to remove a personal email address from author metadata. Every commit tree matched its pre-rewrite counterpart, and the published release ZIPs retained their original SHA-256 checksums.

[同步公告 · Sync announcement](https://github.com/isook527/want-to-go-trip-planner/issues/5)

### 同步已有克隆

如果工作区干净，而且本地 `main` 没有自己的提交，请同步标签并重置 `main`。

```bash
git status --short
git fetch origin --prune --tags --force
git checkout main
git reset --hard origin/main
```

如果有自己的提交或未提交改动，先在原工作分支保留一份备份。下面用独立分支重放提交，保留 `backup-local`。如果已经 fetch，把 `old_base` 设为改写前的分叉提交，见哈希对照表。只有执行过 stash 才运行最后一行。

```bash
old_base=$(git merge-base origin/main HEAD)
git branch backup-local
git stash push --include-untracked -m "Before November 2026 history rewrite"
git fetch origin --prune --tags --force
git checkout main
git reset --hard origin/main
git switch -c replay-local backup-local
git rebase --onto origin/main "$old_base" replay-local
git stash pop
```

请保留旧分支作为本地备份，避免把旧历史合并或推回仓库。没有独有修改的 fork 可以删除后重新 fork。有修改时先在本地重放，再更新自己的 fork。已下载 Release ZIP 的读者无需操作。

### Sync an existing clone

If your working tree is clean and your local `main` has no commits of its own, run the first command block above. It refreshes the tags and resets your local `main`.

If you have local commits or uncommitted edits, run the second block from your original work branch. It saves a backup branch and stashes working changes before the reset, then replays your commits on a separate branch. If you already fetched, set `old_base` to the pre-rewrite fork point using the hash table. Run `git stash pop` only if the stash step saved changes. Keep the old branch as a local backup and avoid merging or pushing the old history back to this repository.

If your fork has no changes of its own, you can delete and recreate it. If it has changes, replay them locally first, then force-push to your own fork. If you only downloaded a Release ZIP, no action is needed.

### 标签和分支对照 · Tag and branch mapping

| 标签或分支 | 旧提交 | 新提交 |
| --- | --- | --- |
| `v2.1.2` | `3debd20bc73320cf584ae91831c6dacb34c8074f` | `3debd20bc73320cf584ae91831c6dacb34c8074f` |
| `v2.2.0` | `b2cda69774ba146fa19d50d27fe40d65c77b9a34` | `925210900c55690de8d1df1f41e1240ff345c650` |
| `v2.3.0` | `11c73ffd5508f9e2143642cab2e207e2b8a54a22` | `52bae4f1143cce5718269c7d3e53ede8d37cba33` |
| `v2.4.1` | `fa0cd51aa99c7feeaa5352ca53ebc2169e2e2fef` | `e74d2c8e429e9cc71a1d37b177e0a47bd2c678f3` |
| `v2.4.2` | `0c1b5fb1882140660d5017b26090df789ed4ccbc` | `e11a1e2a4ff71842b0e10dcd49a808dcc13d3b6d` |
| `v3.0.0` | `64477087b2ba14fcb895cd1268c92627ce01511e` | `25fec3ea04b5ec8ee5b4ed2da8929e6261f44711` |
| `v3.0.1` | `b9d58f1722b33cfe4650eaee2fd954cb304ba0d0` | `5ee4d1e3a6fba2579b7f0d5f97115a6478762fdc` |
| `v3.0.2` | `1fec664c64200d9a7d8ae5277856d9e0b7c8390e` | `ff4b06f6efb2cd9130f52ee62cb8d5a1728c9187` |
| `v3.2.0` | `e811362c0af103c6c67277c0377a4eee5ecb47b1` | `2c5a789544fc89c05492b13e499b59f8ff2f2990` |
| `v3.2.1` | `8cdcabc15419f6c2ea84263f918d0d9f52d03ffe` | `4e35c7b1f402dd456c940ade9730b3ddc41d9e0b` |
| `v3.2.2` | `5fd10894acd00cb4f41c90e3c973664f7980b812` | `6d16ed100546bdb1f42f673bb251dd1f30d52f82` |
| `codex/want-to-go-v2` | `3debd20bc73320cf584ae91831c6dacb34c8074f` | `3debd20bc73320cf584ae91831c6dacb34c8074f` |
| `codex/want-to-go-v2-2` | `e3ce5e55d6f3d2e3d44ac2ace55156e82e7260a9` | `e3ce5e55d6f3d2e3d44ac2ace55156e82e7260a9` |
| `codex/want-to-go-v2-3-visual` | `821b7c8d6d3b65b5b167f083d7b7d6b13d63b75d` | `914a288c7662f458bff9751c58293045d1d4a190` |
| `codex/want-to-go-v2-4-audit` | `865243f935c3fe05eba7762456fa4fa7119716b1` | `cf25808cd2bcfda51931c793b5656ee2534d2a1f` |
| `main` | `5fd10894acd00cb4f41c90e3c973664f7980b812` | `6d16ed100546bdb1f42f673bb251dd1f30d52f82` |
