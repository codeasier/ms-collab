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
- [Authorized artifact HTTP API](../packages/artifacts_api/README.md)：同一 SQLite 快照中的身份/权限、季度目录和命名视图查询；无真实 provider、下载或 UI。
- [GitCode community snapshot adapter](../packages/community_sync/README.md)：显式凭据、只读 provider 与离线 SQLite 快照；事务授权 HTTP 另见下方社区 API。

- [Authorized pipeline snapshot API](../packages/pipeline_api/README.md)：同事务授权的流水线快照列表、详情与耗时趋势；只读合成数据边界，不含真实 CI 提供方或写入。

- [Authorized community API](../packages/community_api/README.md)：同库事务授权、只读刷新与精确内容审批；token可见不自动共享，公开空间不绕过成员权限。
- [Scoped pipeline provider acquisition](../packages/pipeline_sync/README.md)：读取适配服务的有界 GET 协议、逐次当前授权与原子快照发布；真实服务兼容未验，不含上游写操作。
