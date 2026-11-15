# 文档导航

- [渐进迁移计划](migration-plan.md)：公开边界、迁移顺序和验收门槛。
- [工作空间设计目标](workspaces.md)：用户视角与待明确的隔离契约。
- [Public CI](public-ci.md)：公开贡献的隔离边界、检查命令与未验证设置。
- [返回项目首页](../README.md)。

- [可运行本地演示](runnable-demo.md)：运行方式、API、已覆盖检查及未完成边界。

- [Trusted identity boundary](../packages/identity/README.md)：验证器与本地主体绑定接口；不是已接入的登录功能。

- [Pipeline snapshot analytics](../packages/pipelines/README.md)：作用域快照的成功作业耗时趋势；不含真实上游、执行或日志读取。
- [Artifact catalog core](../packages/artifacts/README.md)：显式作用域、季度目录与命名筛选；尚未接入真实存储或下载。

- [Transactional workspace API](../packages/workspace_api/README.md)：可信验证器与 SQLite 同事务授权的配置式 WSGI 适配器；没有真实登录提供方或生产启动入口。

- [Workspace administration UI](../packages/workspace_ui/README.md)：显式配置事务 API 的成员管理页面；不含默认身份或真实登录流程。
- [Workspace lifecycle and modules](../packages/workspace_admin_api/README.md)：平台操作员创建/归档、空间管理员的目录约束模块安装/启停，以及同事务持久幂等；无默认身份或外部执行权限。
- [Authorized artifact HTTP API](../packages/artifacts_api/README.md)：同一 SQLite 快照中的身份/权限、季度目录和命名视图查询；无真实 provider 或下载。
- [GitCode community snapshot adapter](../packages/community_sync/README.md)：显式凭据、只读 provider 与离线 SQLite 快照；事务授权 HTTP 另见下方社区 API。

- [Authorized pipeline snapshot API](../packages/pipeline_api/README.md)：同事务授权的流水线快照列表、详情与耗时趋势；只读合成数据边界，不含真实 CI 提供方或写入。

- [Authorized community API](../packages/community_api/README.md)：同库事务授权、只读刷新与精确内容审批；token可见不自动共享，公开空间不绕过成员权限。
- [Scoped pipeline provider acquisition](../packages/pipeline_sync/README.md)：读取适配服务的有界 GET 协议、逐次当前授权与原子快照发布；真实服务兼容未验，不含上游写操作。

- [Authorized artifact browser](../packages/artifacts_ui/README.md)：目录、季度、分页与命名视图的受权只读界面；同步入口另见下方受权索引，页面自身无同步/下载操作。

- [Identity-addressed workspace invitations](../packages/workspace_invitations/README.md)：可信目录指定身份的邀请/加入、24 小时过期、单次/撤销与事务审计；无公开秘密链接或默认身份。

- [Scoped artifact acquisition and index](../packages/artifact_sync/README.md)：OBS 有界只读分页、同库权限/元数据索引及精确内容审批；合成验证，真实账号兼容和受权下载未完成。
- [Authorized pipeline observations UI](../packages/pipeline_ui/README.md)：同源受权运行列表、详情及耗时展示；版本一致性与取消竞态防护，不含日志、执行或上游刷新。

- [Scoped research-asset catalog](../packages/research_assets/README.md)：类型/实体、稳定业务身份、全来源原子发布与同事务受权查询；不含真实来源凭据、镜像读取或执行。
