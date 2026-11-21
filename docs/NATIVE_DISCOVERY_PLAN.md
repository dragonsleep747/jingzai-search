# 镜仔原生全盘发现模块：接入方案

状态：待实施。2026-10-28。用户已确定下一版模式名称为“探索／智答”，并要求不绑定 Everything。本文不表示已完成全盘发现或多模态检索；当前正式 EXE 不变。

## 目标与结构

新增随镜仔程序分发的 `search_app/discovery` 模块，通过 Windows 系统接口建立文件目录，独立于 Everything、Everything SDK/ES 及 Windows Search 索引服务。首期使用 Python 标准库 sqlite3、ctypes 与现有监听依赖，性能瓶颈通过测量后再决定是否引入原生编译助手。不要求用户逐个导入，也不复制、移动或改写原文件。

```text
磁盘与目录 → 内置发现器 → catalog.db（文件目录）
                             ↓ 可取消的分析队列
               正文／OCR／画面／视频等内容索引
                             ↓
                    探索结果 → 智答引用
```

目录可搜、正文可搜、画面可搜分别记录，不能以目录已列全冒充智能索引已完成。

## 现有工程的复用边界

- `search_app/qt_frontend/backend.py: Store.scan` 目前先 os.walk 收集文件列表，再提交正文索引。不能把传入目录改为 C 盘就当作完成快速全盘检索；它也不适合保存所有文件类型的纯目录记录。
- `search_app/watcher.py: FileWatcher` 单根目录监听后直接调用 `index_single_file`，适合现有内容库；新增目录发现需要独立事件层，不能让每次文件变更都立即运行 OCR 或模型。
- `search_app/indexer.py`、`parsers.py`、`searcher.py` 的已有内容能力保留，后续用适配层消费分析任务。
- `search_app/qt_frontend/jobs.py` 继续向界面报告任务状态。全盘目录与大模型分析不在 GUI 线程执行。
- 原有 `index.db`、收藏、分类、旧版 EXE 和配置先保持兼容。新增的文件目录独立存储为 `catalog.db`，不把所有磁盘文件伪装成已入库文档。
- 原 `IGNORE_DIRS` 为正文扫描策略，会排除 dist、build、虚拟环境等。目录层和深度分析层必须分开配置排除规则，不能直接复用它而悄悄漏掉大量文件名。

## 两条发现通道

### 原生 NTFS 通道

调用文档化的 Win32 API：卷探测、FSCTL_QUERY_USN_JOURNAL、FSCTL_ENUM_USN_DATA、FSCTL_READ_USN_JOURNAL。利用文件 ID、父目录 ID 和文件名重建目录，读取变更记录维护索引。首期优先支持 NTFS，其他文件系统通过能力探测选择回退通道。

低层卷读取可能需要额外权限。主界面保持普通用户运行；正式接入时将可选的提权发现助手与 UI 分离，限定卷范围和调用者。前期只做能力探测及已具备权限下的只读实验，不自动提权、安装服务或驱动，也不创建／删除 USN 日志。权限不足时回退目录枚举，并报告当前通道。

### 通用目录通道

对非 NTFS、指定目录、网络目录及原生通道不可用的情况，采用流式 scandir 枚举、分批事务写入、目录通知及补扫。读取目录元数据时不读取每个文件的正文、全文件哈希或模型特征。

目录通道的首次扫描速度与原生通道分别报告，不宣称两者已经达到 Everything 的速度。目录通知溢出、未记录完整变更或离线期间有变化时必须补扫。

## 目录数据库与接口契约

建议保留四类记录：sources（卷／目录范围和状态）、objects（文件对象身份）、entries（各个文件路径及父子关系）、checkpoints（同步游标与扫描代次）。迁移版本独立于旧内容库。

最小对外接口：

```python
list_sources() -> list[SourceInfo]
probe_source(source_id) -> SourceCapabilities
scan_source(request, progress, cancel) -> ScanReport
sync_source(source_id, cursor, progress, cancel) -> SyncReport
search_catalog(request) -> SearchPage
get_coverage() -> CoverageReport
```

- `SourceInfo`：source_id、根路径、卷标识、文件系统、在线状态。
- `SourceCapabilities`：可用 provider、卷／日志只读能力、不可用原因、回退方式。
- `SearchRequest`：查询、名称／路径范围、扩展名、根目录、排序、分页及取消。默认按字面检索，妥善处理 `% _`、引号、空格与中文；暂不实现复杂语义解析。
- `SearchPage`：有界 results、has_more、请求版本、耗时。count 未完成时可以为空，不能通过同步全量计数阻塞返回。
- `FileHit`：entry_id、object_id（可空）、source_id、真实路径、名称、类型、size（可空）、mtime_ns（可空）、在线／失联状态。
- `ChangeEvent`：source_id、事件 ID／游标、对象 ID、旧／新父目录与名称、变更类型、内容版本；区分仅更名与内容变化。
- `CoverageReport`：每个来源的 provider、已发现数量、扫描是否完成、拒绝访问／失败项、待补扫状态、更新时间。正文和画面进度未来由分析队列补充，不在本期虚构。

路径和对象身份分开：同一对象可能有硬链接，不能因文件 ID 相同直接吞掉其他路径；更名也不应变成两份文件。重解析点默认不递归，避免循环。路径保留原始大小写和中文；身份、匹配键和显示路径分别处理。

## 同步正确性

1. 枚举与变更捕获需要交接：记录起始游标，枚举后回放期间变更，避免扫描期间新增／删除被遗漏。
2. 批次数据与游标在同一提交边界持久化，重放幂等。崩溃后允许重放，不能提前推进游标。
3. USN Journal ID 变化、日志范围失效、游标过期、通知溢出时，标记待补扫并恢复一致性。
4. 无权限、盘离线、枚举中断不等于文件已删除。未完整扫描时不能把未遇到的旧记录全部清掉。
5. 目录更名、子树移动、盘符变化、长路径、硬链接、未知记录版本需要明确处理或报告能力缺口。
6. 原生记录按长度／版本／UTF-16 边界解析，拒绝截断或损坏记录；不假定所有文件 ID 都是同一种长度。
7. 内容分析按真实变更失效。更名不必重算画面向量；内容变化必须更新，不能只用路径作为缓存键。

## 用户体验与后续接入

“搜索范围”里选择全电脑或指定位置，展示覆盖状态；文件留在原处。先让名称检索可用，再后台处理支持格式的内容。原有复制入库仍用于集中管理，探索不要求复制入库。

沿用已保留的界面提案：侧栏三个文字导航；搜索框切换探索／智答；筛选默认折叠；通高预览；菜单按用途分组。目录发现提供范围，探索的语义引擎负责匹配画面或内容，智答负责带来源的解释和分析。

## 工作分工与关口

**dsh 前期**：工程盘点、独立目录数据库、可运行目录枚举／查询原型、Windows 能力探测与 USN 记录解析／原生只读实验、事件回放与恢复测试、性能报告、接口及交接清单。只在新增模块和隔离测试目录工作。

**后续接入**：核对原型和真实 Windows 行为，补齐或优化原生通道，接入后台进程、范围设置和搜索结果，再接正文／多模态队列。最后运行原有功能回归、全盘覆盖与增量验证、窗口与打包验收，再更新正式版。

前期过关不等于多模态搜索完成。尚未取得权限、没有实际跑过的通道、未验证的文件系统必须明确标为未验证，不能用模拟测试代替实测成功。

## 参考接口

- https://learn.microsoft.com/en-us/windows/win32/api/winioctl/ni-winioctl-fsctl_enum_usn_data
- https://learn.microsoft.com/en-us/windows/win32/api/winioctl/ni-winioctl-fsctl_read_usn_journal
- https://learn.microsoft.com/en-us/windows/win32/fileio/walking-a-buffer-of-change-journal-records
- https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-readdirectorychangesw

配套可直接转交的任务书：`DSH_DISCOVERY_PREWORK.md`。
