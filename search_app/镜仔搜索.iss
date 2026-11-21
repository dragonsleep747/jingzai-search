; 镜仔搜索 · Inno Setup 安装包脚本
; 编译：& "ISCC.exe" "search_app\镜仔搜索.iss"
; 产物：dist\镜仔搜索-安装包-1.0.0.exe
;
; 设计要点：
;  1. 装到 %LOCALAPPDATA%\镜仔搜索（用户目录），普通用户可写 —— 程序会把
;     config.json 写在 exe 同目录、索引库也建在数据根目录，装 Program Files
;     会因无写权限而保存设置失败、建库失败。
;  2. 只打包程序本体，不含 index.db / library / ocr_cache / config.json：
;     这些是使用者自己的文档索引与个人设置，首次运行自动创建。
;  3. 卸载只删程序，保留使用者数据（不加 [UninstallDelete] 删数据目录）。

#define AppName "镜仔搜索"
#define AppVer "1.0.0"
#define AppExe "镜仔搜索.exe"

[Setup]
AppId={{8F3A1C62-5B47-4D91-9E20-6C4A7B2D8E31}
AppName={#AppName}
AppVersion={#AppVer}
AppVerName={#AppName} {#AppVer}
AppPublisher={#AppName}
DefaultDirName={localappdata}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
OutputDir=..\dist
OutputBaseFilename={#AppName}-安装包-{#AppVer}
SetupIconFile=app_icon.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "en"; MessagesFile: "compiler:Default.isl"

[Messages]
; 官方中文语言包未随 Inno 附带，这里内联覆盖向导主要文案
ButtonBack=< 上一步(&B)
ButtonNext=下一步(&N) >
ButtonInstall=安装(&I)
ButtonCancel=取消
ButtonFinish=完成(&F)
ButtonBrowse=浏览(&B)...
ClickNext=点「下一步」继续，或点「取消」退出安装。
WelcomeLabel1=欢迎使用 镜仔搜索 安装向导
WelcomeLabel2=即将在本机安装 镜仔搜索 1.0.0。%n%n建议先关闭其他正在运行的程序再继续。
SelectDirLabel3=安装程序将把 镜仔搜索 装入以下文件夹。
SelectDirBrowseLabel=点「下一步」继续；如需更换位置，点「浏览」。
SelectTasksLabel2=请选择安装 镜仔搜索 时要一并完成的附加任务，然后点「下一步」。
SelectStartMenuFolderLabel3=安装程序将在以下开始菜单文件夹中创建快捷方式。
ReadyLabel1=安装程序已准备好，即将开始安装 镜仔搜索。
ReadyLabel2a=点「安装」开始安装；若需检查或更改设置，点「上一步」。
InstallingLabel=正在安装 镜仔搜索，请稍候...
FinishedHeadingLabel=镜仔搜索 安装完成
FinishedLabel=镜仔搜索 已安装完成。可从桌面或开始菜单的快捷方式启动。

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务："

[Files]
Source: "..\dist\{#AppExe}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "立即运行 {#AppName}"; Flags: nowait postinstall skipifsilent