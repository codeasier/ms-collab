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
