; XhsSpider Windows 安装包脚本（Inno Setup 6）
; 命令行构建：
;   ISCC.exe /DSourceDir=dist\XhsSpider /DOutputDir=dist desktop\xhsspider.iss
; 可用 /DMyAppVersion=1.0.0 覆盖版本号

#ifndef SourceDir
#define SourceDir "..\dist\XhsSpider"
#endif
#ifndef OutputDir
#define OutputDir "..\dist"
#endif
#ifndef MyAppVersion
#define MyAppVersion "1.0.0"
#endif

[Setup]
AppId={{8C1F0E9A-6B7C-4A8E-9D2F-XHSPIDER0001}
AppName=XhsSpider
AppVersion={#MyAppVersion}
AppPublisher=YC
DefaultDirName={autopf}\XhsSpider
DefaultGroupName=XhsSpider
UninstallDisplayName=XhsSpider
OutputDir={#OutputDir}
OutputBaseFilename=XhsSpider-Setup-x64
SetupIconFile=..\assets\app.ico
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64
DisableProgramGroupPage=yes

[Languages]
Name: "chinesesimp"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; RecurseSubdirs; Flags: ignoreversion

[Icons]
Name: "{group}\XhsSpider"; Filename: "{app}\XhsSpider.exe"
Name: "{group}\{cm:UninstallProgram,XhsSpider}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\XhsSpider"; Filename: "{app}\XhsSpider.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\XhsSpider.exe"; Description: "{cm:LaunchProgram,XhsSpider}"; Flags: nowait postinstall skipifsilent
