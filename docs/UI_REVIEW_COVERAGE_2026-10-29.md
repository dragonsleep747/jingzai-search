# 镜仔搜索全入口评审覆盖清单

日期：2026-10-29。配套 [评审报告](<D:/桌面/镜仔搜索/docs/UI_LAYOUT_REVIEW_2026-10-29.md>)。

记录 119 个检查场景、260 张截图，图像去重后 190 张。截图数量不等于独立窗口数量，打开不等于功能通过。

## 方法说明与未通过边界

- 正式程序实开窗口与当前代码的隔离样本窗口分别留档；绝大多数逐项检查来自隔离资料库，不操作用户原文件。
- native / native-supplement / native-deep 为真实 Qt 组件检查。部分系统选择器、通用输入和确认框为相同组件重建后取消；正式 EXE 原生打包选择器另有 frozen 记录。
- legacy 为逐工具入口检查；legacy-deep 补查子窗、标签页、菜单和条件窗口。父窗截图如受遮挡或混入其他窗口，已从目录排除，不能用作该窗排版依据。
- 智答模型设置、测速、历史子窗的隐藏/空白作为问题保留；辅助强制显示和历史定位替代场景仅帮助看内容，不算正常链路通过。
- 原位还原切换确认使用模拟校验结果并取消；没有写启动注册表。智答 Markdown 样本流只用于排版。
- 手机二维码实际生成并停止测试服务；没有真实手机配对后的端到端验收。外部意见反馈、官网结果和外部 Office/WPS 窗口未体验。
- 引用关系图实际生成 HTML 并由 Edge 渲染；节点、筛选等全部鼠标交互未逐一验收。
- 旧版库管理高级菜单已采集；退出采集辅助进程异常，已结束自有测试进程，不记为验收通过。

## 主页面与入口场景

| 分组 | 检查场景 | 记录状态 | 关联截图 |
|---|---|---|---|
| native | 主页面 / search | 有截图记录 | [#1](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/001-window.png>)、[#2](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/002-window.png>)、[#3](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/003-dropdown.png>) |
| native | 主页面 / library | 有截图记录 | [#4](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/004-window.png>)、[#5](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/005-window.png>) |
| native | 主页面 / bookmarks | 有截图记录 | [#6](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/006-window.png>)、[#7](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/007-window.png>) |
| native | 主页面 / tasks | 有截图记录 | [#8](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/008-window.png>)、[#9](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/009-window.png>) |
| native | 主页面 / tools | 有截图记录 | [#10](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/010-window.png>)、[#11](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/011-window.png>)、[#12](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/012-dropdown.png>) |
| native | 应用菜单 | 有截图记录 | [#13](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/013-menu.png>)、[#14](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/014-menu.png>)、[#15](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/015-menu.png>)、[#16](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/016-menu.png>) |
| native | 托盘菜单 | 有截图记录 | [#17](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/017-menu.png>) |
| native | 范围菜单 | 有截图记录 | [#18](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/018-menu.png>) |
| native | 搜索历史 | 有截图记录 | [#19](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/019-menu.png>)、[#20](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/020-menu.png>) |
| native | 筛选全部选项 | 有截图记录 | [#21](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/021-window.png>)、[#22](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/022-window.png>)、[#23](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/023-dropdown.png>)、[#24](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/024-dropdown.png>)；另 8 张见下方 |
| native | 结果菜单 / 0 | 有截图记录 | [#33](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/033-menu.png>)、[#34](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/034-menu.png>) |
| native | 结果菜单 / 1 | 有截图记录 | [#35](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/035-menu.png>)、[#36](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/036-menu.png>) |
| native | 结果菜单 / 2 | 有截图记录 | [#37](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/037-menu.png>)、[#38](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/038-menu.png>)、[#39](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/039-menu.png>) |
| native | 阅读 / 文本菜单 | 有截图记录 | [#40](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/040-menu.png>)、[#41](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/041-menu.png>) |
| native | 阅读 / PDF菜单与缩略图 | 有截图记录 | [#42](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/042-window.png>)、[#43](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/043-window.png>)、[#44](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/044-dropdown.png>)、[#45](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/045-menu.png>)；另 1 张见下方 |
| native | 添加资料菜单 | 有截图记录 | [#47](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/047-menu.png>) |
| native | 资料库更多操作 | 有截图记录 | [#48](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/048-menu.png>) |
| native | 范围管理 | 有截图记录 | [#49](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/049-window.png>) |
| native | 常规设置 | 有截图记录 | [#50](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/050-window.png>)、[#51](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/051-window.png>) |
| native | 搜索语法 | 有截图记录 | [#52](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/052-window.png>) |
| native | 完整说明 | 有截图记录 | [#53](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/053-window.png>) |
| native | 功能位置 | 有截图记录 | [#54](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/054-window.png>) |
| native | 管理常用搜索 | 有截图记录 | [#55](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/055-window.png>) |
| native | 保存搜索 | 有截图记录 | [#56](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/056-window.png>) |
| native | 添加文件选择器 | 有截图记录 | [#57](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/057-window.png>)、[#58](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/058-dropdown.png>)、[#59](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/059-dropdown.png>) |
| native | 扫描目录选择器 | 有截图记录 | [#60](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/060-window.png>)、[#61](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/061-dropdown.png>)、[#62](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/062-dropdown.png>) |
| native | 导出结果选择器 | 有截图记录 | [#63](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/063-window.png>)、[#64](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/064-dropdown.png>)、[#65](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/065-dropdown.png>) |
| native | 备份选择器 | 有截图记录 | [#66](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/066-window.png>)、[#67](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/067-dropdown.png>)、[#68](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/068-dropdown.png>) |
| native | 独立还原选择器 | 有截图记录 | [#69](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/069-window.png>)、[#70](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/070-dropdown.png>)、[#71](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/071-dropdown.png>) |
| native | 原位还原选择器 | 有截图记录 | [#72](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/072-window.png>)、[#73](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/073-dropdown.png>)、[#74](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/074-dropdown.png>) |
| native | 手机配对窗口 | 有截图记录 | [#75](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/075-window.png>) |
| native | 文本反查 | 有截图记录 | [#76](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/076-window.png>) |
| native | 问题文件 | 有截图记录 | [#77](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/077-window.png>) |
| native | 分类自查 | 有截图记录 | [#78](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/078-window.png>) |
| native | 重复文件清理 | 有截图记录 | [#79](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/079-window.png>) |
| native | 解析错误 | 有截图记录 | [#80](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/080-window.png>) |
| native | 单文件信息 | 有截图记录 | [#81](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/081-window.png>)、[#82](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/082-dropdown.png>) |
| native | 批量信息 | 有截图记录 | [#83](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/083-window.png>)、[#84](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/084-dropdown.png>)、[#85](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/085-dropdown.png>) |
| native | 重命名 | 有截图记录 | [#86](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/086-window.png>) |
| native | 移除确认 | 有截图记录 | [#87](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/087-window.png>) |
| native | 导出台账 | 有截图记录 | [#88](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/088-window.png>)、[#89](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/089-dropdown.png>)、[#90](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/090-dropdown.png>) |
| native | 自动版本匹配 | 没有新增窗口，不作为通过证明 | 本场景无可用截图；见状态及补查 |
| native | 双文档对比 | 有截图记录 | [#91](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/091-window.png>) |
| native | 文档详情 | 有截图记录 | [#92](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/092-window.png>) |
| native | 跳转行号 | 有截图记录 | [#93](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/093-window.png>) |
| native | 跳转当前位置 | 有截图记录 | [#94](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/094-window.png>) |
| native | 阅读正文右键 | 有截图记录 | [#95](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/095-menu.png>) |
| native | 引用右键 | 有截图记录 | [#96](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/096-menu.png>) |
| native | 正文结果右键 | 有截图记录 | [#97](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/097-menu.png>) |
| native | 电脑目录预览 | 有截图记录 | [#98](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/098-window.png>)、[#99](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/099-window.png>)、[#100](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/100-window.png>)、[#101](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/101-dropdown.png>)；另 2 张见下方 |
| native | 未入库文本预览 | 有截图记录 | [#104](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/104-window.png>)、[#105](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/105-window.png>)、[#106](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/106-window.png>)、[#107](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/107-dropdown.png>)；另 2 张见下方 |
| native | 智答与设置 | 有截图记录 | [#110](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/110-window.png>)、[#111](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/111-window.png>)、[#112](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/112-window.png>)、[#113](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/113-dropdown.png>)；另 2 张见下方 |
| native | 普通聊天 | 有截图记录 | [#116](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/116-window.png>)、[#117](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/117-window.png>)、[#118](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/118-window.png>)、[#119](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/119-dropdown.png>)；另 1 张见下方 |
| native | 收藏备注 | 没有新增窗口，不作为通过证明 | 本场景无可用截图；见状态及补查 |
| native | 收藏导出 | 有截图记录 | [#121](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/121-window.png>)、[#122](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/122-dropdown.png>)、[#123](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/123-dropdown.png>) |
| native | 入库预检与隐藏处理项 | 有截图记录 | [#124](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/124-window.png>)、[#125](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/125-window.png>) |
| native | 任务详情 | 有截图记录 | [#126](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/126-window.png>) |
| native-supplement | 收藏备注多行窗口 | 有截图记录 | [#127](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-supplement/001-window.png>) |
| native-supplement | 导入完成、冲突处理、失败与导出明细 | 有截图记录 | [#128](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-supplement/002-window.png>)、[#129](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-supplement/003-window.png>)、[#130](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-supplement/004-dropdown.png>)、[#131](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-supplement/005-window.png>)；另 4 张见下方 |
| native-supplement | 关闭窗口选择 | 有截图记录 | [#136](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-supplement/010-window.png>) |
| native-supplement | 后台任务退出确认 | 有截图记录 | [#137](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-supplement/011-window.png>) |
| native-supplement | 仅移除索引确认 | 有截图记录 | [#138](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-supplement/012-window.png>) |
| native-supplement | 移入回收站确认 | 有截图记录 | [#139](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-supplement/013-window.png>) |
| native-supplement | 任务失败详情 | 有截图记录 | [#140](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-supplement/014-window.png>) |
| native-supplement | 反查结果窗口 | 有截图记录 | [#141](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-supplement/015-window.png>)、[#142](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-supplement/016-window.png>) |
| native-supplement | 开机启动确认（取消） | 有截图记录 | [#143](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-supplement/017-window.png>) |
| native-supplement | 电脑图片预览 | 有截图记录 | [#144](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-supplement/018-window.png>)、[#145](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-supplement/019-window.png>)、[#146](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-supplement/020-dropdown.png>)、[#147](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-supplement/021-menu.png>)；另 1 张见下方 |
| native-supplement | 未入库PDF预览 | 有截图记录 | [#149](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-supplement/023-window.png>)、[#150](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-supplement/024-window.png>)、[#151](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-supplement/025-dropdown.png>)、[#152](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-supplement/026-menu.png>)；另 1 张见下方 |
| native-supplement | 搜索无结果状态 | 有截图记录 | [#154](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-supplement/028-window.png>)、[#155](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-supplement/029-window.png>)、[#156](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-supplement/030-dropdown.png>) |
| native-supplement | 智答错误与回答排版状态 | 有截图记录 | [#157](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-supplement/031-window.png>)、[#158](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-supplement/032-window.png>)、[#159](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-supplement/033-dropdown.png>)、[#160](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-supplement/034-dropdown.png>)；另 8 张见下方 |
| native-deep | 工具中心分组 1 | 有截图记录 | [#169](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-deep/001-window.png>)、[#170](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-deep/002-window.png>)、[#171](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-deep/003-dropdown.png>) |
| native-deep | 工具中心分组 2 | 有截图记录 | [#172](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-deep/004-window.png>)、[#173](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-deep/005-window.png>)、[#174](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-deep/006-dropdown.png>) |
| native-deep | 工具中心分组 3 | 有截图记录 | [#175](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-deep/007-window.png>)、[#176](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-deep/008-window.png>)、[#177](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-deep/009-dropdown.png>) |
| native-deep | 工具中心分组 4 | 有截图记录 | [#178](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-deep/010-window.png>)、[#179](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-deep/011-window.png>)、[#180](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-deep/012-dropdown.png>) |
| native-deep | 章节、专注、阅读模式与PDF跳页 | 有截图记录 | [#181](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-deep/013-window.png>)、[#182](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-deep/014-window.png>)、[#183](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-deep/015-dropdown.png>)、[#184](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-deep/016-window.png>)；另 4 张见下方 |
| native-deep | 文档引用关系表 | 有截图记录 | [#189](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-deep/021-window.png>) |
| native-deep | 结果内筛选与命中分布 | 有截图记录 | [#190](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-deep/022-window.png>)、[#191](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-deep/023-window.png>)、[#192](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-deep/024-dropdown.png>) |
| native-deep | 手机配对启停与二维码 | 有截图记录 | [#193](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-deep/025-window.png>)、[#194](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-deep/026-window.png>) |
| native-deep | 独立新文件入库完成与明细 | 有截图记录 | [#195](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-deep/027-window.png>)、[#196](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-deep/028-window.png>) |
| native-deep | 原位还原切换确认（模拟校验结果并取消） | 有截图记录 | [#197](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-deep/029-window.png>) |
| legacy | bookmarks / main | 有截图记录 | [#198](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy/bookmarks-main/01.png>)、[#199](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy/bookmarks-main/02.png>) |
| legacy | category / main | 有截图记录 | [#200](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy/category-main/01.png>)、[#201](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy/category-main/02.png>) |
| legacy | category_rules / add | 有截图记录 | [#202](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy/category_rules-add/01.png>) |
| legacy | category_rules / main | 有截图记录 | [#203](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy/category_rules-main/01.png>) |
| legacy | dedupe / main | 有截图记录 | [#204](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy/dedupe-main/01.png>)、[#205](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy/dedupe-main/02.png>) |
| legacy | download_review / main | 有截图记录 | [#206](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy/download_review-main/01.png>)、[#207](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy/download_review-main/02.png>) |
| legacy | download_watch / main | 有截图记录 | [#208](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy/download_watch-main/01.png>) |
| legacy | export_dir / main | 有截图记录 | [#209](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy/export_dir-main/01.png>) |
| legacy | gap / main | 有截图记录 | [#210](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy/gap-main/01.png>)、[#211](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy/gap-main/02.png>) |
| legacy | help / main | 有截图记录 | [#212](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy/help-main/01.png>)、[#213](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy/help-main/02.png>) |
| legacy | import / main | 有截图记录 | [#214](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy/import-main/01.png>)、[#215](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy/import-main/02.png>) |
| legacy | inbox / main | 有截图记录 | [#216](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy/inbox-main/01.png>)、[#217](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy/inbox-main/02.png>) |
| legacy | index_health / main | 有截图记录 | [#218](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy/index_health-main/01.png>)、[#219](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy/index_health-main/02.png>) |
| legacy | library / main | 有截图记录 | [#220](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy/library-main/01.png>) |
| legacy | library / menu | 有截图记录 | [#221](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy/library-menu/01.png>) |
| legacy | ocr / main | 只有窗口回执；补查结果另列 | 本场景无可用截图；见状态及补查 |
| legacy | problem_files / main | 有截图记录 | [#222](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy/problem_files-main/01.png>) |
| legacy | qa / history | 有截图记录 | [#223](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy/qa-history/01.png>)、[#224](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy/qa-history/02.png>) |
| legacy | qa / main | 有截图记录 | [#225](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy/qa-main/01.png>)、[#226](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy/qa-main/02.png>)、[#227](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy/qa-main/03.png>) |
| legacy | qa / models | 有截图记录 | [#228](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy/qa-models/01.png>)、[#229](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy/qa-models/02.png>)、[#230](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy/qa-models/03.png>) |
| legacy | qa / setup | 有截图记录 | [#231](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy/qa-setup/01.png>)、[#232](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy/qa-setup/02.png>)、[#233](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy/qa-setup/03.png>) |
| legacy | quick / main | 有截图记录 | [#234](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy/quick-main/01.png>) |
| legacy | ref_warnings / main | 有截图记录 | [#235](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy/ref_warnings-main/01.png>) |
| legacy | standards / main | 有截图记录 | [#236](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy/standards-main/01.png>)、[#237](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy/standards-main/02.png>) |
| legacy | synonyms / main | 有截图记录 | [#238](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy/synonyms-main/01.png>)、[#239](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy/synonyms-main/02.png>) |
| legacy | versions / main | 只有窗口回执；补查结果另列 | 本场景无可用截图；见状态及补查 |
| legacy | web / main | 有截图记录 | [#240](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy/web-main/01.png>) |
| legacy-deep | gap / results | 有截图记录 | [#241](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy-deep/gap-results/01.png>)、[#242](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy-deep/gap-results/02.png>)、[#243](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy-deep/gap-results/03.png>) |
| legacy-deep | ocr / main | 有截图记录 | [#244](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy-deep/ocr-main/01.png>) |
| legacy-deep | qa / bench | 有截图记录 | [#245](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy-deep/qa-bench/02.png>) |
| legacy-deep | qa / history-full | 有截图记录 | [#246](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy-deep/qa-history-full/02.png>) |
| legacy-deep | qa / history-layout | 有截图记录 | [#247](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy-deep/qa-history-layout/03.png>) |
| legacy-deep | qa / models | 有截图记录 | [#248](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy-deep/qa-models/02.png>) |
| legacy-deep | ref_warnings / main | 有截图记录 | [#249](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy-deep/ref_warnings-main/01.png>)、[#250](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy-deep/ref_warnings-main/02.png>)、[#251](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy-deep/ref_warnings-main/03.png>) |
| legacy-deep | synonyms / builtin | 有截图记录 | [#252](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy-deep/synonyms-builtin/02.png>) |
| legacy-deep | versions / main | 有截图记录 | [#253](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy-deep/versions-main/01.png>) |
| legacy-deep | versions / two-files | 有截图记录 | [#254](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy-deep/versions-two-files/01.png>)、[#255](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy-deep/versions-two-files/02.png>) |
| legacy-deep | library / advanced | 菜单已采集；辅助进程退出异常 | [#256](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy-deep/library-advanced/02.png>)、[#257](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy-deep/library-advanced/03.png>) |
| graph | 本地引用关系图网页 | 实际生成并渲染；交互未穷举 | [#260](<D:/桌面/镜仔搜索/.tmp/ui-audit/graph/graph.png>) |

## 完整工具路由核对

当前工具映射共 28 项。转交新版的入口按实际新版目标计，不虚构第二个旧版窗口。

| 路由 | 工具名称 | 检查说明 |
|---|---|---|
| qa | 完整镜仔智答 | 工作台及设置展开已查；模型、测速、历史子窗存在隐藏/空白异常；历史全文辅助展开 |
| web | 完整联网检索 | 工具窗口已打开；具体条件与补查见场景表 |
| import | 入库预检与冲突处理 | 工具窗口已打开；具体条件与补查见场景表 |
| inbox | 待入库文件审核 | 工具窗口已打开；具体条件与补查见场景表 |
| library | 旧版完整库管理 | 库管理窗口与高级菜单已查；菜单采集辅助进程退出异常 |
| bookmarks | 完整收藏管理 | 工具窗口已打开；具体条件与补查见场景表 |
| dedupe | 完整查重清理 | 工具窗口已打开；具体条件与补查见场景表 |
| category | 完整分类自查 | 工具窗口已打开；具体条件与补查见场景表 |
| category_rules | 分类规则管理 | 工具窗口已打开；具体条件与补查见场景表 |
| problem_files | 问题文件管理 | 工具窗口已打开；具体条件与补查见场景表 |
| ref_warnings | 全库引用预警 | 工具窗口已打开；具体条件与补查见场景表 |
| gap | 合规 Gap 扫描 | 工具窗口已打开；具体条件与补查见场景表 |
| versions | 完整版本对比 | 不足两份文件提示、两个文件选择、完整对比结果均补查 |
| index_health | 索引体检与备份设置 | 工具窗口已打开；具体条件与补查见场景表 |
| standards | 标准更新检查 | 工具窗口已打开；具体条件与补查见场景表 |
| mobile | 手机访问与管理 | 新版手机配对；未启动、实际二维码、停止状态已查，真实手机流程未验 |
| pair | 手机免口令扫码配对 | 转交新版手机配对，同上 |
| synonyms | 同义词库与内置词组 | 工具窗口已打开；具体条件与补查见场景表 |
| ocr | OCR 组件诊断 | 初轮仅回执，后续补查实际 OCR 状态消息框 |
| package | 打包资料库 | 转交新版备份选择器 |
| package_all | 打包软件与资料库 | 转交新版软件打包；正式 EXE Windows 原生保存框另行打开 |
| restore | 旧版原位还原资料库 | 转交新版原位还原；选择器及最终确认已查，切换确认用模拟校验结果并取消 |
| download_watch | 下载文件夹监控 | 工具窗口已打开；具体条件与补查见场景表 |
| download_review | 下载文件待入库审核 | 工具窗口已打开；具体条件与补查见场景表 |
| export_dir | 默认存档目录 | 工具窗口已打开；具体条件与补查见场景表 |
| feedback | 意见反馈 | 外部反馈网页未打开、未提交信息，仅核对入口 |
| help | 旧版完整使用说明 | 工具窗口已打开；具体条件与补查见场景表 |
| quick | 迷你速查与粘贴引用 | 工具窗口已打开；具体条件与补查见场景表 |

## 截图索引与隐藏项内容

下列菜单条目、下拉选项由实际组件读取。选项存在不等于每个动作已执行；删除、覆盖、还原切换等停在检查边界。

### #1 search

native · 主页面 / search · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/001-window.png>)

下拉/标签：探索 ／ 智答

### #2 search / 下半部

native · 主页面 / search · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/002-window.png>)

下拉/标签：探索 ／ 智答

### #3 search / 下拉：探索

native · 主页面 / search · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/003-dropdown.png>)

### #4 library

native · 主页面 / library · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/004-window.png>)

### #5 library / 下半部

native · 主页面 / library · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/005-window.png>)

### #6 bookmarks

native · 主页面 / bookmarks · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/006-window.png>)

### #7 bookmarks / 下半部

native · 主页面 / bookmarks · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/007-window.png>)

### #8 tasks

native · 主页面 / tasks · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/008-window.png>)

### #9 tasks / 下半部

native · 主页面 / tasks · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/009-window.png>)

### #10 tools

native · 主页面 / tools · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/010-window.png>)

下拉/标签：资料安全 ／ 检查与配置 ／ 辅助检索 ／ 旧版备用 ／ 全部

### #11 tools / 下半部

native · 主页面 / tools · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/011-window.png>)

下拉/标签：资料安全 ／ 检查与配置 ／ 辅助检索 ／ 旧版备用 ／ 全部

### #12 tools / 下拉：资料安全

native · 主页面 / tools · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/012-dropdown.png>)

### #13 菜单

native · 应用菜单 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/013-menu.png>)

菜单：任务与进度；工具中心；设置；帮助；退出软件

### #14 菜单 → 设置

native · 应用菜单 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/014-menu.png>)

菜单：常规与热键；启动与唤起

### #15 菜单 → 设置 → 启动与唤起

native · 应用菜单 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/015-menu.png>)

菜单：开机启动；注册 jingzai:// 唤起新版

### #16 菜单 → 帮助

native · 应用菜单 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/016-menu.png>)

菜单：完整使用说明（F1）；功能位置说明；意见反馈

### #17 托盘

native · 托盘菜单 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/017-menu.png>)

菜单：显示窗口；迷你速查；退出

### #18 范围

native · 范围菜单 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/018-menu.png>)

菜单：电脑文件；资料库（已选）；两者；管理搜索范围…

### #19 搜索历史

native · 搜索历史 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/019-menu.png>)

菜单：安全；名称:管理办法；保存当前搜索条件…；常用搜索；管理常用搜索…；恢复上次阅读

### #20 搜索历史 → 常用搜索

native · 搜索历史 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/020-menu.png>)

菜单：常用管理办法

### #21 筛选展开

native · 筛选全部选项 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/021-window.png>)

下拉/标签：探索 ／ 智答；电脑文件 ／ 资料库 ／ 两者；文件名称 ／ 完整路径 ／ 名称和路径；精确匹配 ／ 模糊匹配（错字容错）；全部 ／ 文本 ／ Office ／ PDF ／ 代码； ／ 09-公司制度（1）； ／ 现行 ／ 废止 ／ 替代；紧凑 ／ 舒适

### #22 筛选展开 / 下半部

native · 筛选全部选项 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/022-window.png>)

下拉/标签：探索 ／ 智答；电脑文件 ／ 资料库 ／ 两者；文件名称 ／ 完整路径 ／ 名称和路径；精确匹配 ／ 模糊匹配（错字容错）；全部 ／ 文本 ／ Office ／ PDF ／ 代码； ／ 09-公司制度（1）； ／ 现行 ／ 废止 ／ 替代；紧凑 ／ 舒适

### #23 筛选展开 / 下拉：探索

native · 筛选全部选项 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/023-dropdown.png>)

### #24 筛选展开 / 下拉：两者

native · 筛选全部选项 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/024-dropdown.png>)

### #25 筛选展开 / 下拉：文件名称

native · 筛选全部选项 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/025-dropdown.png>)

### #26 筛选展开 / 下拉：精确匹配

native · 筛选全部选项 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/026-dropdown.png>)

### #27 筛选展开 / 下拉：全部

native · 筛选全部选项 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/027-dropdown.png>)

### #28 筛选展开 / 下拉：

native · 筛选全部选项 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/028-dropdown.png>)

### #29 筛选展开 / 下拉：

native · 筛选全部选项 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/029-dropdown.png>)

### #30 筛选展开 / 下拉：

native · 筛选全部选项 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/030-dropdown.png>)

### #31 筛选展开 / 下拉：紧凑

native · 筛选全部选项 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/031-dropdown.png>)

### #32 筛选底部

native · 筛选全部选项 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/032-window.png>)

下拉/标签：探索 ／ 智答；电脑文件 ／ 资料库 ／ 两者；文件名称 ／ 完整路径 ／ 名称和路径；精确匹配 ／ 模糊匹配（错字容错）；全部 ／ 文本 ／ Office ／ PDF ／ 代码； ／ 09-公司制度（1）； ／ 现行 ／ 废止 ／ 替代；紧凑 ／ 舒适

### #33 结果操作

native · 结果菜单 / 0 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/033-menu.png>)

菜单：结果内筛选；显示命中分布；排序；导出搜索结果；文档引用关系；引用关系图；AI 分析当前搜索结果

### #34 结果操作 → 排序

native · 结果菜单 / 0 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/034-menu.png>)

菜单：文件名称（已选）；完整路径；最近修改；文件大小

### #35 结果操作

native · 结果菜单 / 1 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/035-menu.png>)

菜单：结果内筛选；显示命中分布；排序；导出搜索结果；文档引用关系；引用关系图；AI 分析当前搜索结果

### #36 结果操作 → 排序

native · 结果菜单 / 1 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/036-menu.png>)

菜单：相关程度（已选）；命中次数；施行日期↓；最近更新↓；文件名称

### #37 结果操作

native · 结果菜单 / 2 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/037-menu.png>)

菜单：结果内筛选；显示命中分布；正文结果排序；文件目录排序；导出搜索结果；文档引用关系；引用关系图；AI 分析当前搜索结果

### #38 结果操作 → 正文结果排序

native · 结果菜单 / 2 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/038-menu.png>)

菜单：相关程度（已选）；命中次数；施行日期↓；最近更新↓；文件名称

### #39 结果操作 → 文件目录排序

native · 结果菜单 / 2 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/039-menu.png>)

菜单：文件名称（已选）；完整路径；最近修改；文件大小

### #40 文本阅读

native · 阅读 / 文本菜单 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/040-menu.png>)

菜单：返回上一阅读位置（Alt+Left）；显示 / 隐藏章节目录；跳转到行号…；复制正文 / 所选文字（Ctrl+C）；复制条款引用；已收藏；打开原文件并定位；正文放大（Ctrl+滚轮）；正文缩小（Ctrl+滚轮）；；PDF 页码 / 缩放 / 查找；文档详细信息

下拉/标签：提取文字 ／ PDF 原始页面；整段 ／ 上下文 ／ 全文

### #41 文本阅读 → PDF 页码 / 缩放 / 查找

native · 阅读 / 文本菜单 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/041-menu.png>)

菜单：

下拉/标签：适合宽度 ／ 整页 ／ 75% ／ 100% ／ 125% ／ 150% ／ 200%

### #42 PDF阅读

native · 阅读 / PDF菜单与缩略图 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/042-window.png>)

下拉/标签：探索 ／ 智答

### #43 PDF阅读 / 下半部

native · 阅读 / PDF菜单与缩略图 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/043-window.png>)

下拉/标签：探索 ／ 智答

### #44 PDF阅读 / 下拉：探索

native · 阅读 / PDF菜单与缩略图 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/044-dropdown.png>)

### #45 PDF阅读菜单

native · 阅读 / PDF菜单与缩略图 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/045-menu.png>)

菜单：返回上一阅读位置（Alt+Left）；显示 / 隐藏章节目录；跳转到行号…；复制正文 / 所选文字（Ctrl+C）；复制条款引用；收藏；打开原文件并定位；正文放大（Ctrl+滚轮）；正文缩小（Ctrl+滚轮）；；PDF 页码 / 缩放 / 查找；文档详细信息

下拉/标签：提取文字 ／ PDF 原始页面；整段 ／ 上下文 ／ 全文

### #46 PDF阅读菜单 → PDF 页码 / 缩放 / 查找

native · 阅读 / PDF菜单与缩略图 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/046-menu.png>)

菜单：

下拉/标签：适合宽度 ／ 整页 ／ 75% ／ 100% ／ 125% ／ 150% ／ 200%

### #47 添加资料

native · 添加资料菜单 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/047-menu.png>)

菜单：添加文件…；扫描文件夹…；待入库文件审核…；快存剪贴板文字

### #48 资料库更多操作

native · 资料库更多操作 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/048-menu.png>)

菜单：打开原文件；显示所在文件夹；重命名文件…；重新索引所选文件；从索引中移除…；批量编辑信息…；导出全库台账…；查重清理；问题文件管理；分类自查与修正；分类规则管理；选择两个文件对比…；自动配对版本对比…；标准版本检查

### #49 范围管理 / 搜索范围

native · 范围管理 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/049-window.png>)

### #50 常规设置 / 设置

native · 常规设置 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/050-window.png>)

### #51 常规设置 / 设置 / 下半部

native · 常规设置 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/051-window.png>)

### #52 搜索语法 / 搜索速查 · 点击示例直接搜索

native · 搜索语法 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/052-window.png>)

### #53 完整说明 / 镜仔搜索 · 完整使用说明

native · 完整说明 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/053-window.png>)

### #54 功能位置 / 功能位置说明

native · 功能位置 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/054-window.png>)

### #55 管理常用搜索 / 常用搜索 · 双击应用

native · 管理常用搜索 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/055-window.png>)

### #56 保存搜索

native · 保存搜索 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/056-window.png>)

### #57 添加资料

native · 添加文件选择器 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/057-window.png>)

下拉/标签：.\.tmp\ui-audit\native\data\sample；支持的文件 (*.pdf *.docx *.doc *.xlsx *.xls *.pptx *.ppt *.txt *.md *.csv *.json *.zip *.7z *.rar *.png *.jpg *.jpeg) ／ 所有文件 (*)

### #58 添加资料 / 下拉：.\.tmp\ui-audit\native\data\sample

native · 添加文件选择器 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/058-dropdown.png>)

### #59 添加资料 / 下拉：支持的文件 (*.pdf *.docx *.doc *.xlsx *.xls *.pptx *.ppt *.txt *.md *.csv *.json *.zip *.7z *.rar *.png *.jpg *.jpeg)

native · 添加文件选择器 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/059-dropdown.png>)

### #60 选择要扫描的资料文件夹

native · 扫描目录选择器 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/060-window.png>)

下拉/标签：.\.tmp\ui-audit\native\data\sample；Directories

### #61 选择要扫描的资料文件夹 / 下拉：.\.tmp\ui-audit\native\data\sample

native · 扫描目录选择器 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/061-dropdown.png>)

### #62 选择要扫描的资料文件夹 / 下拉：Directories

native · 扫描目录选择器 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/062-dropdown.png>)

### #63 导出已加载的搜索结果（2 条）

native · 导出结果选择器 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/063-window.png>)

下拉/标签：.\.tmp\ui-audit\native\data\sample；CSV 表格 (*.csv) ／ Word 汇编 (*.docx)

### #64 导出已加载的搜索结果（2 条） / 下拉：.\.tmp\ui-audit\native\data\sample

native · 导出结果选择器 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/064-dropdown.png>)

### #65 导出已加载的搜索结果（2 条） / 下拉：CSV 表格 (*.csv)

native · 导出结果选择器 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/065-dropdown.png>)

### #66 备份资料库

native · 备份选择器 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/066-window.png>)

下拉/标签：.\.tmp\ui-audit\native\data\sample；完整资料包 (*.zip) ／ 索引备份 (*.db)

### #67 备份资料库 / 下拉：.\.tmp\ui-audit\native\data\sample

native · 备份选择器 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/067-dropdown.png>)

### #68 备份资料库 / 下拉：完整资料包 (*.zip)

native · 备份选择器 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/068-dropdown.png>)

### #69 选择完整资料包

native · 独立还原选择器 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/069-window.png>)

下拉/标签：.\.tmp\ui-audit\native\data\sample；资料包 (*.zip)

### #70 选择完整资料包 / 下拉：.\.tmp\ui-audit\native\data\sample

native · 独立还原选择器 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/070-dropdown.png>)

### #71 选择完整资料包 / 下拉：资料包 (*.zip)

native · 独立还原选择器 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/071-dropdown.png>)

### #72 选择原位还原的完整资料包

native · 原位还原选择器 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/072-window.png>)

下拉/标签：.\.tmp\ui-audit\native\data\sample；资料包 (*.zip)

### #73 选择原位还原的完整资料包 / 下拉：.\.tmp\ui-audit\native\data\sample

native · 原位还原选择器 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/073-dropdown.png>)

### #74 选择原位还原的完整资料包 / 下拉：资料包 (*.zip)

native · 原位还原选择器 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/074-dropdown.png>)

### #75 手机配对窗口 / 手机扫码配对 · 同一 Wi-Fi 下免口令访问

native · 手机配对窗口 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/075-window.png>)

### #76 文本反查 / 反查出处 · 粘贴原文片段

native · 文本反查 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/076-window.png>)

### #77 问题文件 / 问题文件管理

native · 问题文件 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/077-window.png>)

### #78 分类自查 / 分类自查 · 修正、忽略与撤销

native · 分类自查 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/078-window.png>)

### #79 重复文件清理 / 查重清理 · 保留建议文件，其余可选中清理

native · 重复文件清理 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/079-window.png>)

### #80 解析错误 / 解析错误

native · 解析错误 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/080-window.png>)

### #81 单文件信息 / 编辑资料信息

native · 单文件信息 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/081-window.png>)

下拉/标签：有效 ／ 已废止 ／ 已替代

### #82 单文件信息 / 编辑资料信息 / 下拉：有效

native · 单文件信息 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/082-dropdown.png>)

### #83 批量信息 / 批量编辑 1 个文件

native · 批量信息 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/083-window.png>)

下拉/标签：保持原状态 ／ 有效 ／ 废止 ／ 已替代；01-国家法律 ／ 02-行政法规 ／ 03-部门规章 ／ 04-规范性文件 ／ 05-国家标准GB ／ 06-行业标准JGJ ／ 07-河北省法规规章 ／ 08-河北省地方标准 ／ 09-公司制度 ／ 10-其他资料

### #84 批量信息 / 批量编辑 1 个文件 / 下拉：保持原状态

native · 批量信息 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/084-dropdown.png>)

### #85 批量信息 / 批量编辑 1 个文件 / 下拉：01-国家法律

native · 批量信息 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/085-dropdown.png>)

### #86 重命名文件

native · 重命名 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/086-window.png>)

### #87 从索引中移除

native · 移除确认 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/087-window.png>)

### #88 导出全库台账

native · 导出台账 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/088-window.png>)

下拉/标签：.\.tmp\ui-audit\native\data\sample；CSV 文件 (*.csv)

### #89 导出全库台账 / 下拉：.\.tmp\ui-audit\native\data\sample

native · 导出台账 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/089-dropdown.png>)

### #90 导出全库台账 / 下拉：CSV 文件 (*.csv)

native · 导出台账 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/090-dropdown.png>)

### #91 双文档对比 / 版本对比

native · 双文档对比 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/091-window.png>)

### #92 文档详情 / 文档详细信息

native · 文档详情 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/092-window.png>)

### #93 跳转到正文

native · 跳转行号 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/093-window.png>)

### #94 跳转到正文

native · 跳转当前位置 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/094-window.png>)

### #95 阅读正文右键

native · 阅读正文右键 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/095-menu.png>)

菜单：&Copy	Ctrl+C（禁用）；Copy &Link Location（禁用）；Select All	Ctrl+A

### #96 引用右键

native · 引用右键 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/096-menu.png>)

菜单：在软件内查看引用资料；打开源文件并定位；打开源文件所在位置；复制源文件路径

### #97 正文结果右键

native · 正文结果右键 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/097-menu.png>)

菜单：打开原文件并定位；复制条款引用；自动匹配其他版本并对比

### #98 目录预览 dir

native · 电脑目录预览 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/098-window.png>)

下拉/标签：探索 ／ 智答

### #99 目录预览 dir / 下半部

native · 电脑目录预览 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/099-window.png>)

下拉/标签：探索 ／ 智答

### #100 目录预览 dir / 下半部

native · 电脑目录预览 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/100-window.png>)

下拉/标签：探索 ／ 智答

### #101 目录预览 dir / 下拉：探索

native · 电脑目录预览 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/101-dropdown.png>)

### #102 文件操作

native · 电脑目录预览 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/102-menu.png>)

菜单：打开原文件；所在位置；复制路径；加入资料库；专注阅读；PDF 显示（禁用）

### #103 文件操作 → PDF 显示

native · 电脑目录预览 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/103-menu.png>)

菜单：适合宽度；整页；75%；100%；125%；150%；200%

### #104 目录预览 file

native · 未入库文本预览 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/104-window.png>)

下拉/标签：探索 ／ 智答

### #105 目录预览 file / 下半部

native · 未入库文本预览 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/105-window.png>)

下拉/标签：探索 ／ 智答

### #106 目录预览 file / 下半部

native · 未入库文本预览 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/106-window.png>)

下拉/标签：探索 ／ 智答

### #107 目录预览 file / 下拉：探索

native · 未入库文本预览 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/107-dropdown.png>)

### #108 文件操作

native · 未入库文本预览 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/108-menu.png>)

菜单：打开原文件；所在位置；复制路径；加入资料库；专注阅读；PDF 显示（禁用）

### #109 文件操作 → PDF 显示

native · 未入库文本预览 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/109-menu.png>)

菜单：适合宽度；整页；75%；100%；125%；150%；200%

### #110 智答

native · 智答与设置 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/110-window.png>)

下拉/标签：探索 ／ 智答；基于资料 ／ 普通聊天

### #111 智答 / 下半部

native · 智答与设置 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/111-window.png>)

下拉/标签：探索 ／ 智答；基于资料 ／ 普通聊天

### #112 智答 / 下半部

native · 智答与设置 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/112-window.png>)

下拉/标签：探索 ／ 智答；基于资料 ／ 普通聊天

### #113 智答 / 下拉：智答

native · 智答与设置 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/113-dropdown.png>)

### #114 智答 / 下拉：基于资料

native · 智答与设置 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/114-dropdown.png>)

### #115 智答设置

native · 智答与设置 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/115-menu.png>)

菜单：模型与完整智答工作台；更新资料语义索引；兼容模式（使用 CPU）

### #116 普通聊天

native · 普通聊天 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/116-window.png>)

下拉/标签：探索 ／ 智答；基于资料 ／ 普通聊天

### #117 普通聊天 / 下半部

native · 普通聊天 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/117-window.png>)

下拉/标签：探索 ／ 智答；基于资料 ／ 普通聊天

### #118 普通聊天 / 下半部

native · 普通聊天 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/118-window.png>)

下拉/标签：探索 ／ 智答；基于资料 ／ 普通聊天

### #119 普通聊天 / 下拉：智答

native · 普通聊天 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/119-dropdown.png>)

### #120 普通聊天 / 下拉：普通聊天

native · 普通聊天 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/120-dropdown.png>)

### #121 导出收藏汇编

native · 收藏导出 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/121-window.png>)

下拉/标签：.\.tmp\ui-audit\native\data\sample；Word 文件 (*.docx)

### #122 导出收藏汇编 / 下拉：.\.tmp\ui-audit\native\data\sample

native · 收藏导出 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/122-dropdown.png>)

### #123 导出收藏汇编 / 下拉：Word 文件 (*.docx)

native · 收藏导出 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/123-dropdown.png>)

### #124 入库预检

native · 入库预检与隐藏处理项 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/124-window.png>)

### #125 导入处理方式编辑

native · 入库预检与隐藏处理项 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/125-window.png>)

### #126 任务详情 / 样本任务

native · 任务详情 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native/126-window.png>)

### #127 收藏备注多行窗口 / 收藏备注

native-supplement · 收藏备注多行窗口 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-supplement/001-window.png>)

### #128 导入完成：成功状态

native-supplement · 导入完成、冲突处理、失败与导出明细 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-supplement/002-window.png>)

### #129 同名冲突与重复预检

native-supplement · 导入完成、冲突处理、失败与导出明细 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-supplement/003-window.png>)

### #130 保留两份／替换／跳过

native-supplement · 导入完成、冲突处理、失败与导出明细 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-supplement/004-dropdown.png>)

### #131 导入失败状态

native-supplement · 导入完成、冲突处理、失败与导出明细 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-supplement/005-window.png>)

### #132 导出入库明细

native-supplement · 导入完成、冲突处理、失败与导出明细 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-supplement/006-window.png>)

下拉/标签：.\.tmp\ui-audit\native-supplement\data\sample；CSV 文件 (*.csv)

### #133 导出入库明细 / 下拉：.\.tmp\ui-audit\native-supplement\data\sample

native-supplement · 导入完成、冲突处理、失败与导出明细 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-supplement/007-dropdown.png>)

### #134 导出入库明细 / 下拉：CSV 文件 (*.csv)

native-supplement · 导入完成、冲突处理、失败与导出明细 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-supplement/008-dropdown.png>)

### #135 导入完成、冲突处理、失败与导出明细 / 入库处理明细

native-supplement · 导入完成、冲突处理、失败与导出明细 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-supplement/009-window.png>)

### #136 关闭窗口选择 / 关闭窗口

native-supplement · 关闭窗口选择 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-supplement/010-window.png>)

### #137 后台任务仍在运行

native-supplement · 后台任务退出确认 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-supplement/011-window.png>)

### #138 确认清理

native-supplement · 仅移除索引确认 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-supplement/012-window.png>)

### #139 确认清理

native-supplement · 移入回收站确认 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-supplement/013-window.png>)

### #140 任务失败详情 / 审阅失败任务

native-supplement · 任务失败详情 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-supplement/014-window.png>)

### #141 反查结果窗口 / 反查出处 · 粘贴原文片段

native-supplement · 反查结果窗口 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-supplement/015-window.png>)

### #142 反查结果窗口 / 反查出处 · 1 处匹配

native-supplement · 反查结果窗口 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-supplement/016-window.png>)

### #143 开机启动

native-supplement · 开机启动确认（取消） · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-supplement/017-window.png>)

### #144 图片预览

native-supplement · 电脑图片预览 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-supplement/018-window.png>)

下拉/标签：探索 ／ 智答

### #145 图片预览 / 下半部

native-supplement · 电脑图片预览 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-supplement/019-window.png>)

下拉/标签：探索 ／ 智答

### #146 图片预览 / 下拉：探索

native-supplement · 电脑图片预览 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-supplement/020-dropdown.png>)

### #147 按来源变化的阅读菜单

native-supplement · 电脑图片预览 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-supplement/021-menu.png>)

菜单：打开原文件；所在位置；复制路径；加入资料库；专注阅读；PDF 显示（禁用）

### #148 按来源变化的阅读菜单 → PDF 显示

native-supplement · 电脑图片预览 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-supplement/022-menu.png>)

菜单：适合宽度；整页；75%；100%；125%；150%；200%

### #149 未入库PDF预览

native-supplement · 未入库PDF预览 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-supplement/023-window.png>)

下拉/标签：探索 ／ 智答

### #150 未入库PDF预览 / 下半部

native-supplement · 未入库PDF预览 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-supplement/024-window.png>)

下拉/标签：探索 ／ 智答

### #151 未入库PDF预览 / 下拉：探索

native-supplement · 未入库PDF预览 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-supplement/025-dropdown.png>)

### #152 按来源变化的阅读菜单

native-supplement · 未入库PDF预览 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-supplement/026-menu.png>)

菜单：打开原文件；所在位置；复制路径；加入资料库；专注阅读；PDF 显示

### #153 按来源变化的阅读菜单 → PDF 显示

native-supplement · 未入库PDF预览 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-supplement/027-menu.png>)

菜单：适合宽度；整页；75%；100%；125%；150%；200%

### #154 没有匹配结果

native-supplement · 搜索无结果状态 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-supplement/028-window.png>)

下拉/标签：探索 ／ 智答

### #155 没有匹配结果 / 下半部

native-supplement · 搜索无结果状态 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-supplement/029-window.png>)

下拉/标签：探索 ／ 智答

### #156 没有匹配结果 / 下拉：探索

native-supplement · 搜索无结果状态 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-supplement/030-dropdown.png>)

### #157 缺少正文依据

native-supplement · 智答错误与回答排版状态 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-supplement/031-window.png>)

下拉/标签：探索 ／ 智答；基于资料 ／ 普通聊天

### #158 缺少正文依据 / 下半部

native-supplement · 智答错误与回答排版状态 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-supplement/032-window.png>)

下拉/标签：探索 ／ 智答；基于资料 ／ 普通聊天

### #159 缺少正文依据 / 下拉：智答

native-supplement · 智答错误与回答排版状态 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-supplement/033-dropdown.png>)

### #160 缺少正文依据 / 下拉：基于资料

native-supplement · 智答错误与回答排版状态 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-supplement/034-dropdown.png>)

### #161 模型服务不可用

native-supplement · 智答错误与回答排版状态 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-supplement/035-window.png>)

下拉/标签：探索 ／ 智答；基于资料 ／ 普通聊天

### #162 模型服务不可用 / 下半部

native-supplement · 智答错误与回答排版状态 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-supplement/036-window.png>)

下拉/标签：探索 ／ 智答；基于资料 ／ 普通聊天

### #163 模型服务不可用 / 下拉：智答

native-supplement · 智答错误与回答排版状态 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-supplement/037-dropdown.png>)

### #164 模型服务不可用 / 下拉：普通聊天

native-supplement · 智答错误与回答排版状态 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-supplement/038-dropdown.png>)

### #165 样本回答排版（替代模型流）

native-supplement · 智答错误与回答排版状态 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-supplement/039-window.png>)

下拉/标签：探索 ／ 智答；基于资料 ／ 普通聊天

### #166 样本回答排版（替代模型流） / 下半部

native-supplement · 智答错误与回答排版状态 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-supplement/040-window.png>)

下拉/标签：探索 ／ 智答；基于资料 ／ 普通聊天

### #167 样本回答排版（替代模型流） / 下拉：智答

native-supplement · 智答错误与回答排版状态 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-supplement/041-dropdown.png>)

### #168 样本回答排版（替代模型流） / 下拉：普通聊天

native-supplement · 智答错误与回答排版状态 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-supplement/042-dropdown.png>)

### #169 工具中心 / 检查与配置

native-deep · 工具中心分组 1 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-deep/001-window.png>)

下拉/标签：资料安全 ／ 检查与配置 ／ 辅助检索 ／ 旧版备用 ／ 全部

### #170 工具中心 / 检查与配置 / 下半部

native-deep · 工具中心分组 1 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-deep/002-window.png>)

下拉/标签：资料安全 ／ 检查与配置 ／ 辅助检索 ／ 旧版备用 ／ 全部

### #171 工具中心 / 检查与配置 / 下拉：检查与配置

native-deep · 工具中心分组 1 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-deep/003-dropdown.png>)

### #172 工具中心 / 辅助检索

native-deep · 工具中心分组 2 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-deep/004-window.png>)

下拉/标签：资料安全 ／ 检查与配置 ／ 辅助检索 ／ 旧版备用 ／ 全部

### #173 工具中心 / 辅助检索 / 下半部

native-deep · 工具中心分组 2 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-deep/005-window.png>)

下拉/标签：资料安全 ／ 检查与配置 ／ 辅助检索 ／ 旧版备用 ／ 全部

### #174 工具中心 / 辅助检索 / 下拉：辅助检索

native-deep · 工具中心分组 2 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-deep/006-dropdown.png>)

### #175 工具中心 / 旧版备用

native-deep · 工具中心分组 3 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-deep/007-window.png>)

下拉/标签：资料安全 ／ 检查与配置 ／ 辅助检索 ／ 旧版备用 ／ 全部

### #176 工具中心 / 旧版备用 / 下半部

native-deep · 工具中心分组 3 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-deep/008-window.png>)

下拉/标签：资料安全 ／ 检查与配置 ／ 辅助检索 ／ 旧版备用 ／ 全部

### #177 工具中心 / 旧版备用 / 下拉：旧版备用

native-deep · 工具中心分组 3 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-deep/009-dropdown.png>)

### #178 工具中心 / 全部

native-deep · 工具中心分组 4 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-deep/010-window.png>)

下拉/标签：资料安全 ／ 检查与配置 ／ 辅助检索 ／ 旧版备用 ／ 全部

### #179 工具中心 / 全部 / 下半部

native-deep · 工具中心分组 4 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-deep/011-window.png>)

下拉/标签：资料安全 ／ 检查与配置 ／ 辅助检索 ／ 旧版备用 ／ 全部

### #180 工具中心 / 全部 / 下拉：全部

native-deep · 工具中心分组 4 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-deep/012-dropdown.png>)

### #181 章节目录

native-deep · 章节、专注、阅读模式与PDF跳页 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-deep/013-window.png>)

下拉/标签：探索 ／ 智答

### #182 章节目录 / 下半部

native-deep · 章节、专注、阅读模式与PDF跳页 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-deep/014-window.png>)

下拉/标签：探索 ／ 智答

### #183 章节目录 / 下拉：探索

native-deep · 章节、专注、阅读模式与PDF跳页 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-deep/015-dropdown.png>)

### #184 专注阅读

native-deep · 章节、专注、阅读模式与PDF跳页 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-deep/016-window.png>)

### #185 专注阅读 / 下半部

native-deep · 章节、专注、阅读模式与PDF跳页 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-deep/017-window.png>)

### #186 阅读 / 提取文字

native-deep · 章节、专注、阅读模式与PDF跳页 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-deep/018-dropdown.png>)

### #187 阅读 / 整段

native-deep · 章节、专注、阅读模式与PDF跳页 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-deep/019-dropdown.png>)

### #188 跳转到 PDF 页码

native-deep · 章节、专注、阅读模式与PDF跳页 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-deep/020-window.png>)

### #189 文档引用关系表 / 文档引用关系

native-deep · 文档引用关系表 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-deep/021-window.png>)

### #190 命中分布与结果内筛选

native-deep · 结果内筛选与命中分布 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-deep/022-window.png>)

下拉/标签：探索 ／ 智答

### #191 命中分布与结果内筛选 / 下半部

native-deep · 结果内筛选与命中分布 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-deep/023-window.png>)

下拉/标签：探索 ／ 智答

### #192 命中分布与结果内筛选 / 下拉：探索

native-deep · 结果内筛选与命中分布 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-deep/024-dropdown.png>)

### #193 隔离资料库 / 实际生成配对二维码

native-deep · 手机配对启停与二维码 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-deep/025-window.png>)

### #194 隔离资料库 / 手机服务已停止

native-deep · 手机配对启停与二维码 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-deep/026-window.png>)

### #195 入库完成成功结果

native-deep · 独立新文件入库完成与明细 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-deep/027-window.png>)

### #196 独立新文件入库完成与明细 / 入库处理明细

native-deep · 独立新文件入库完成与明细 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-deep/028-window.png>)

### #197 资料准备完成

native-deep · 原位还原切换确认（模拟校验结果并取消） · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/native-deep/029-window.png>)

### #198 bookmarks / main

legacy · bookmarks / main · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy/bookmarks-main/01.png>)

### #199 结束状态

legacy · bookmarks / main · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy/bookmarks-main/02.png>)

### #200 category / main

legacy · category / main · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy/category-main/01.png>)

### #201 结束状态

legacy · category / main · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy/category-main/02.png>)

### #202 结束状态

legacy · category_rules / add · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy/category_rules-add/01.png>)

### #203 结束状态

legacy · category_rules / main · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy/category_rules-main/01.png>)

### #204 dedupe / main

legacy · dedupe / main · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy/dedupe-main/01.png>)

### #205 结束状态

legacy · dedupe / main · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy/dedupe-main/02.png>)

### #206 download_review / main

legacy · download_review / main · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy/download_review-main/01.png>)

### #207 结束状态

legacy · download_review / main · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy/download_review-main/02.png>)

### #208 系统选择或提示窗口

legacy · download_watch / main · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy/download_watch-main/01.png>)

### #209 系统选择或提示窗口

legacy · export_dir / main · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy/export_dir-main/01.png>)

### #210 gap / main

legacy · gap / main · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy/gap-main/01.png>)

### #211 结束状态

legacy · gap / main · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy/gap-main/02.png>)

### #212 help / main

legacy · help / main · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy/help-main/01.png>)

### #213 结束状态

legacy · help / main · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy/help-main/02.png>)

### #214 import / main

legacy · import / main · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy/import-main/01.png>)

### #215 结束状态

legacy · import / main · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy/import-main/02.png>)

### #216 inbox / main

legacy · inbox / main · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy/inbox-main/01.png>)

### #217 结束状态

legacy · inbox / main · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy/inbox-main/02.png>)

### #218 index_health / main

legacy · index_health / main · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy/index_health-main/01.png>)

### #219 结束状态

legacy · index_health / main · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy/index_health-main/02.png>)

### #220 结束状态

legacy · library / main · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy/library-main/01.png>)

### #221 结束状态

legacy · library / menu · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy/library-menu/01.png>)

### #222 结束状态

legacy · problem_files / main · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy/problem_files-main/01.png>)

### #223 qa / history

legacy · qa / history · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy/qa-history/01.png>)

### #224 结束状态

legacy · qa / history · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy/qa-history/02.png>)

### #225 qa / main

legacy · qa / main · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy/qa-main/01.png>)

### #226 qa / main

legacy · qa / main · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy/qa-main/02.png>)

### #227 结束状态

legacy · qa / main · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy/qa-main/03.png>)

### #228 qa / models

legacy · qa / models · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy/qa-models/01.png>)

### #229 qa / models

legacy · qa / models · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy/qa-models/02.png>)

### #230 结束状态

legacy · qa / models · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy/qa-models/03.png>)

### #231 qa / setup

legacy · qa / setup · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy/qa-setup/01.png>)

### #232 qa / setup

legacy · qa / setup · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy/qa-setup/02.png>)

### #233 结束状态

legacy · qa / setup · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy/qa-setup/03.png>)

### #234 quick / main

legacy · quick / main · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy/quick-main/01.png>)

### #235 结束状态

legacy · ref_warnings / main · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy/ref_warnings-main/01.png>)

### #236 standards / main

legacy · standards / main · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy/standards-main/01.png>)

### #237 结束状态

legacy · standards / main · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy/standards-main/02.png>)

### #238 synonyms / main

legacy · synonyms / main · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy/synonyms-main/01.png>)

### #239 结束状态

legacy · synonyms / main · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy/synonyms-main/02.png>)

### #240 结束状态

legacy · web / main · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy/web-main/01.png>)

### #241 gap / results

legacy-deep · gap / results · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy-deep/gap-results/01.png>)

### #242 标签页 /  缺失（需补入库）（0）

legacy-deep · gap / results · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy-deep/gap-results/02.png>)

### #243 标签页 /  已入库（0）

legacy-deep · gap / results · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy-deep/gap-results/03.png>)

### #244 系统选择或提示窗口

legacy-deep · ocr / main · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy-deep/ocr-main/01.png>)

### #245 qa / bench

legacy-deep · qa / bench · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy-deep/qa-bench/02.png>)

原始状态：withdrawn；辅助展开，不记正常打开通过。

### #246 qa / history-full

legacy-deep · qa / history-full · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy-deep/qa-history-full/02.png>)

原始状态：withdrawn；辅助展开，不记正常打开通过。

### #247 qa / history-layout

legacy-deep · qa / history-layout · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy-deep/qa-history-layout/03.png>)

### #248 qa / models

legacy-deep · qa / models · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy-deep/qa-models/02.png>)

原始状态：withdrawn；辅助展开，不记正常打开通过。

下拉/标签：qwen2.5:7b-instruct（通义千问2.5 7B，默认均衡）、qwen2.5:3b-instruct（通义千问2.5 3B，低配机器）、qwen2.5:14b-instruct（通义千问2.5 14B，高配机器）、qwen3:8b（通义千问3 8B）、deepseek-r1:8b（深度求索R1 8B 推理）、llama3.1:8b；bge-m3（智源BGE-M3 向量，默认）、nomic-embed-text

### #249 ref_warnings / main

legacy-deep · ref_warnings / main · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy-deep/ref_warnings-main/01.png>)

### #250 标签页 /  引用已废止/被替代文件（0）

legacy-deep · ref_warnings / main · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy-deep/ref_warnings-main/02.png>)

### #251 标签页 /  引用未入库文件（0）

legacy-deep · ref_warnings / main · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy-deep/ref_warnings-main/03.png>)

### #252 synonyms / builtin

legacy-deep · synonyms / builtin · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy-deep/synonyms-builtin/02.png>)

### #253 系统选择或提示窗口

legacy-deep · versions / main · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy-deep/versions-main/01.png>)

### #254 versions / two-files

legacy-deep · versions / two-files · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy-deep/versions-two-files/01.png>)

下拉/标签：新版管理办法.txt、审阅样本.txt

### #255 versions / two-files

legacy-deep · versions / two-files · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy-deep/versions-two-files/02.png>)

### #256 旧版库管理高级工具菜单

legacy-deep · library / advanced · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy-deep/library-advanced/02.png>)

### #257 旧版库管理高级工具菜单

legacy-deep · library / advanced · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/legacy-deep/library-advanced/03.png>)

### #258 打包软件和完整资料库

frozen · 实际EXE软件打包选择器 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/frozen-package/windows-0.png>)

### #259 镜仔搜索 · 新版

frozen · 实际EXE软件打包选择器 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/frozen-package/windows-1.png>)

### #260 引用关系图 / 实际生成HTML与Edge渲染

graph · 本地引用关系图网页 · [打开原图](<D:/桌面/镜仔搜索/.tmp/ui-audit/graph/graph.png>)

## 机器可读记录

[总目录 JSON](<D:/桌面/镜仔搜索/.tmp/ui-audit/catalog.json>)。其中保留组件文字、状态、字号、位置、下拉内容等元数据。

评审材料保存在当前工程内。正式 EXE SHA256：`C3E0D989774E4431D4853B70949EC5FC0CA8A0E1E5F2A235625A08909A72043E`，本轮结束时重新核对一致。
