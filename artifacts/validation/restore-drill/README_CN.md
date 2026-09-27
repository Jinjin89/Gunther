# Gunther 受控恢复证据

日期：2026-08-30  
当前权威基线：schema v10  
范围：自动化隔离目录；不覆盖、移动或修改当前 Gunther 数据目录

## 当前结论

正式 `restore` CLI 已完成自动化恢复闭环。它先执行完整备份验证，只允许恢复到**不存在的新目录**；目标已经存在、是 symlink、位于备份内部或复制结果不一致时均 fail closed。恢复完成后会再次验证 SQLite、Asset、Recording 与 Artifact，任何一步失败都不会发布一个看似成功的目标目录。

推荐命令：

```bash
npm run verify:backup -- /备份目录/gunther-backup-...
npm run restore:data -- /备份目录/gunther-backup-... --data-dir /新的不存在目录
```

## 当前 schema v10 自动化证据

测试在独立临时数据树中创建并持久化：

- 非空 SQLite schema v10 数据库及完整 migration history；
- 一个真实 Source 与非空 Asset 原件；
- 一个包含两个真实 chunk、状态为 `completed` 的 Recording；
- RecordingSession / RecordingChunk ledger、完整音频文件、大小与 SHA-256；
- 一个不可变 Artifact，以及其 content、manifest、provenance、unit bindings 与 revision snapshots。

闭环如下：

```text
backup + verify
→ restore CLI 验证输入
→ 原子恢复到不存在的新 data dir
→ SQLite quick_check / foreign keys / schema / migrations
→ Asset 下载字节、大小与 SHA-256
→ completed Recording、双 chunk ledger、文件大小与 SHA-256
→ Artifact content / manifest / binding / revision 完整性
→ 数据库引用文件与受控目录文件双向归属检查
→ 从恢复目录再次 backup + verify
```

上述自动化证明当前 v10 的数据库—原件—录音—Artifact 集合能够被一致备份、校验、恢复和再次备份，也证明 restore 不会覆盖现有目录。它是当前交付所依赖的正式恢复证据。

## 历史兼容性证据：v6→v9

项目早期曾将一份通过校验的 schema v6 停机备份复制到隔离目录，并由当时的后端迁移至 schema v9。该次演练保持 3 个 Knowledge Bases、3 个 Sources、8 个 Notebook notes 和 0 个 Recordings，`quick_check=ok`、外键违规为 0；随后再次完成 v9 backup + verify。

这段记录仅证明当时 v6→v9 迁移链的历史兼容性。其输入本身没有 Asset 或 Recording 文件，也早于 v10 Artifact，因此：

- 不把它描述为当前 v10 完整恢复；
- 不把当时的隔离临时路径写成运行或交付依赖；
- 不用它替代上面的非空 v10 正式 restore CLI 自动化证据。

当前 migration 10 是 `immutable_artifact_history`；应用仍按顺序保留并验证 v1–v10 migration history。

## 剩余边界

- 尚未对真实用户的非空 v10 数据目录执行人工停机备份→新目录恢复→完整 UI 检查。
- 尚无应用内恢复向导、加密异地副本、定期恢复抽检或证书/设备重新配对向导。
- 普通业务备份不包含 `.env`、日志、sidecar ready token 或 mobile gateway 私钥；恢复后需要按运行手册重新配置秘密，并可能重新建立 gateway 身份与配对。
- 自动化恢复证据不能替代运维人员对备份介质、保留周期、访问权限和异地灾备的管理。
